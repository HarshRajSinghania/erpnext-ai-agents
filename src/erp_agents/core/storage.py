"""SQLite storage (stdlib only, no ORM).

One small database holds: admin overrides + audit, alerts, approvals + deliveries,
work orders (the hand-off queue to future agents) and agent run logs.
Thread-safe (single connection guarded by an RLock) so the API, scheduler and
Telegram poller can share it. Transactions nest via a depth counter.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

log = logging.getLogger(__name__)

# The shape of the database this code understands. `schema_info.version` in the file says which shape it has:
#   - a file with a HIGHER number was made by a newer version of this application: it is refused, untouched (an older version must not guess at tables it does not know);
#   - a file with a LOWER number is copied aside (`<file>.before-schema-N-<time>`) and then upgraded by MIGRATIONS, one version at a time, each in its own transaction.
# To change the schema: add the migration as MIGRATIONS[N+1] (a function taking the connection), raise SCHEMA_VERSION to N+1, and add a test (tests/test_operations.py).
SCHEMA_VERSION = 1
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}
# Columns added to a table after it first shipped (before migrations existed): an existing database gets them when it is opened (CREATE TABLE IF NOT EXISTS does not change a
# table that is there). Safe to repeat, so it stays as it is.
ADDED_COLUMNS = (("agent_tasks", "provider", "TEXT"),)


class SchemaError(RuntimeError):
    """The database cannot be used by this version of the application (made by a newer one, or an upgrade failed). Nothing was changed."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS settings_overrides (
    scope_type  TEXT NOT NULL,           -- global | group | item | system
    scope_key   TEXT NOT NULL,
    data_json   TEXT NOT NULL,
    updated_by  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (scope_type, scope_key)
);

CREATE TABLE IF NOT EXISTS settings_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    scope_type  TEXT NOT NULL,
    scope_key   TEXT NOT NULL,
    field       TEXT NOT NULL,
    old_json    TEXT,
    new_json    TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id              TEXT PRIMARY KEY,
    item_code       TEXT NOT NULL,
    kind            TEXT NOT NULL,       -- BREACH | EARLY_WARNING
    severity        INTEGER NOT NULL,
    status          TEXT NOT NULL,       -- awaiting|snoozed|dismissed|actioned|resolved|superseded|expired|cancelled
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    analysis_json   TEXT NOT NULL,
    reasoning_text  TEXT NOT NULL,
    approval_id     TEXT,
    snoozed_until   TEXT,
    suppress_until  TEXT,
    cooldown_until  TEXT,
    status_note     TEXT,
    run_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_item ON alerts(item_code, created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);

CREATE TABLE IF NOT EXISTS approvals (
    id               TEXT PRIMARY KEY,
    kind             TEXT NOT NULL,
    status           TEXT NOT NULL,      -- pending|decided|expired|cancelled
    request_json     TEXT NOT NULL,
    decision_json    TEXT,
    ref_type         TEXT,
    ref_id           TEXT,
    created_at       TEXT NOT NULL,
    expires_at       TEXT,
    decided_at       TEXT,
    last_reminder_at TEXT,
    reminder_count   INTEGER NOT NULL DEFAULT 0,
    status_note      TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

CREATE TABLE IF NOT EXISTS deliveries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id  TEXT NOT NULL,
    recipient_id TEXT NOT NULL,
    channel      TEXT NOT NULL,
    address      TEXT,
    status       TEXT NOT NULL,          -- sent|failed|given_up
    external_id  TEXT,
    error        TEXT,
    attempts     INTEGER NOT NULL DEFAULT 1,
    sent_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_approval ON deliveries(approval_id);

CREATE TABLE IF NOT EXISTS work_orders (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,          -- e.g. REORDER_NEEDED
    status       TEXT NOT NULL,          -- pending|claimed|done|failed
    payload_json TEXT NOT NULL,
    source       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id             TEXT PRIMARY KEY,
    work_order_id  TEXT NOT NULL,
    item_code      TEXT NOT NULL,
    status         TEXT NOT NULL,        -- awaiting|ordered|po_failed|snoozed|rejected|manual|acknowledged|expired|cancelled
    approval_id    TEXT,
    supplier       TEXT,
    supplier_name  TEXT,
    qty            REAL,
    rate           REAL,
    total          REAL,
    po_name        TEXT,
    ranking_json   TEXT,
    status_note    TEXT,
    snoozed_until  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proposals_item ON proposals(item_code, created_at);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);

CREATE TABLE IF NOT EXISTS assistant_answers (
    cache_key    TEXT PRIMARY KEY,       -- hash of model + prompt version + facts + question: same input -> same answer
    approval_id  TEXT NOT NULL,
    question     TEXT NOT NULL,
    result_json  TEXT NOT NULL,          -- what the person was shown, including the check outcome
    model        TEXT NOT NULL,
    asked_by     TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_approval ON assistant_answers(approval_id, created_at);

CREATE TABLE IF NOT EXISTS stock_notices (
    id            TEXT PRIMARY KEY,
    item_code     TEXT NOT NULL,
    level         TEXT NOT NULL,         -- LOW | URGENT
    status        TEXT NOT NULL,         -- sent | acknowledged | snoozed | cleared | superseded
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    approval_id   TEXT,
    snoozed_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_notices_item ON stock_notices(item_code, created_at);

CREATE TABLE IF NOT EXISTS chat_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_by    TEXT NOT NULL,
    asked_at    TEXT NOT NULL,
    question    TEXT NOT NULL,
    intent      TEXT NOT NULL,
    status      TEXT NOT NULL,
    source      TEXT NOT NULL         -- rules | ai-routed
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id           TEXT PRIMARY KEY,
    agent        TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    dry_run      INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT
);

-- the AI agents: one row per task (what was asked, what came out, what was sent to the model) and one per tool call
CREATE TABLE IF NOT EXISTS agent_tasks (
    id            TEXT PRIMARY KEY,
    agent         TEXT NOT NULL,          -- inventory | purchasing
    task          TEXT NOT NULL,          -- brief | review | ask
    status        TEXT NOT NULL,          -- ok | rejected | fallback | failed
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    model         TEXT,
    provider      TEXT,                   -- ollama | anthropic | openai | gemini
    remote        INTEGER NOT NULL DEFAULT 0,
    steps         INTEGER NOT NULL DEFAULT 0,
    tool_calls    INTEGER NOT NULL DEFAULT 0,
    llm_seconds   REAL,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    approval_id   TEXT,
    asked_by      TEXT,
    request_text  TEXT,
    result_json   TEXT,
    note          TEXT,
    trace_json    TEXT                    -- the messages sent to the model and its replies (pruned after agents.keep_traces_days)
);
CREATE INDEX IF NOT EXISTS ix_agent_tasks_agent ON agent_tasks(agent, started_at);
CREATE INDEX IF NOT EXISTS ix_agent_tasks_approval ON agent_tasks(approval_id);

CREATE TABLE IF NOT EXISTS tool_calls (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    via       TEXT NOT NULL,              -- mcp-stdio | mcp-http | agent | api
    tool      TEXT NOT NULL,
    args_json TEXT,
    ok        INTEGER NOT NULL,
    ms        INTEGER,
    task_id   TEXT,
    error     TEXT
);
"""


class Database:
    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._depth = 0
        try:
            self._check_version()                    # before anything is written: a database from a newer version is left exactly as it is
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema()
        except BaseException:
            self._conn.close()
            raise

    def stored_version(self) -> Optional[int]:
        """The schema version written in the file; None for a database that has none yet (a new, empty file)."""
        has = self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_info'").fetchone()
        row = self._conn.execute("SELECT version FROM schema_info").fetchone() if has else None
        return int(row["version"]) if row else None

    def _check_version(self) -> None:
        found = self.stored_version()
        if found is not None and found > SCHEMA_VERSION:
            raise SchemaError(f"This database ({self.path}) has schema version {found}, made by a newer version of the application; this version understands up to {SCHEMA_VERSION}. "
                              "Install the newer version, or restore a backup that this version made. Nothing was changed.")

    def _init_schema(self) -> None:
        with self._lock:
            found = self.stored_version()
            if found is not None and found < SCHEMA_VERSION:
                self._upgrade(found)
            self._conn.executescript(SCHEMA)
            for table, column, declaration in ADDED_COLUMNS:
                if column not in {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            row = self._conn.execute("SELECT version FROM schema_info").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))

    def _upgrade(self, found: int) -> None:
        """Bring an older database up to SCHEMA_VERSION: a copy first (a file database), then one migration at a time, each all-or-nothing."""
        missing = [v for v in range(found + 1, SCHEMA_VERSION + 1) if v not in MIGRATIONS]
        if missing:
            raise SchemaError(f"The database has schema version {found} and this version needs {SCHEMA_VERSION}, but there is no migration for version(s) "
                              f"{', '.join(map(str, missing))}. Nothing was changed.")
        copy_to = None
        if self.path != ":memory:":
            copy_to = Path(f"{self.path}.before-schema-{found}-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
            target = sqlite3.connect(str(copy_to))
            try:
                self._conn.backup(target)
            finally:
                target.close()
            log.warning("upgrading the database from schema %d to %d; a copy of the old one is at %s", found, SCHEMA_VERSION, copy_to)
        for version in range(found + 1, SCHEMA_VERSION + 1):
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                MIGRATIONS[version](self._conn)
                self._conn.execute("UPDATE schema_info SET version=?", (version,))
                self._conn.execute("COMMIT")
            except BaseException as exc:
                self._conn.execute("ROLLBACK")
                raise SchemaError(f"Upgrading the database to schema version {version} failed ({type(exc).__name__}: {str(exc)[:160]}). Nothing was changed"
                                  + (f"; the copy made before the upgrade is at {copy_to}." if copy_to else ".")) from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Atomic block. Nested use joins the outer transaction."""
        with self._lock:
            outermost = self._depth == 0
            if outermost:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outermost:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outermost:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False, sort_keys=True)


def loads(text: Optional[str]) -> Any:
    return None if text is None else json.loads(text)
