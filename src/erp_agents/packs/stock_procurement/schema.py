"""The tables of the stock pack: alerts and stock notices, purchase proposals, the "Ask why" answer cache, the chat log and the run log of the two agents.

The core creates its own tables; this pack registers these with `Database.register_schema` (the pack's version is kept apart in `pack_schema`). Every repository and service of
the pack calls `ensure_schema(db)` when it is built, so a database made before packs existed, which already has these tables, just gets its version row.
To change a table: raise VERSION, add MIGRATIONS[VERSION] (a function taking the connection) and a test (tests/test_operations.py shows the mechanism).
"""
from __future__ import annotations

import sqlite3
from typing import Callable

NAME = "stock_procurement"
VERSION = 1
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}

SCHEMA = """
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
"""


def ensure_schema(db) -> None:
    db.register_schema(NAME, SCHEMA, VERSION, MIGRATIONS)
