"""A tamper-evident seal over the decision history: every closed approval request and every settings change is sealed once, in order, in a hash chain.

Each row of `audit_chain` holds the digest of the record as it was when sealed (for a request: its content, the answer, who gave it and when) and a hash over the
previous row's hash and that digest. `verify()` walks the chain and recomputes both:

  * a row whose hash does not follow from the one before it        -> the chain itself was edited
  * a sealed record whose content no longer gives the same digest   -> the record was changed after it was sealed
  * a sealed record that is gone                                    -> it was deleted

This makes changes visible, it does not prevent them: someone with write access to the database file can rewrite the whole chain. Keep backups (`erp-agents backup`) and,
for stronger proof, copy the latest hash (`erp-agents history --verify` prints it) somewhere the database cannot reach, for example in an e-mail to yourself.
Records closed before the chain existed are sealed the first time it runs ("sealed" rather than "decided"), so the seal covers them from then on.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Optional

from .common import iso, utcnow
from .storage import Database

log = logging.getLogger(__name__)
GENESIS = "0" * 64


def digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def link(prev: str, source: str, ref_id: str, event: str, at: str, record_digest: str) -> str:
    return hashlib.sha256("|".join((prev, source, ref_id, event, at, record_digest)).encode()).hexdigest()


def approval_payload(row: Any) -> dict[str, Any]:
    """What is sealed for a request: everything that says what was asked, why, and how it was answered."""
    return {k: row[k] for k in ("id", "kind", "status", "request_json", "decision_json", "ref_type", "ref_id", "created_at", "decided_at", "status_note")}


def setting_payload(row: Any) -> dict[str, Any]:
    return {k: row[k] for k in ("id", "ts", "actor", "scope_type", "scope_key", "field", "old_json", "new_json")}


class AuditChain:
    def __init__(self, db: Database, clock: Callable[[], Any] = utcnow) -> None:
        self.db, self.clock = db, clock

    def _last(self) -> str:
        row = self.db.query_one("SELECT hash FROM audit_chain ORDER BY seq DESC LIMIT 1")
        return row["hash"] if row else GENESIS

    def _append(self, source: str, ref_id: str, event: str, payload: dict[str, Any]) -> None:
        at, prev = iso(self.clock()), self._last()
        d = digest(payload)
        self.db.execute("INSERT INTO audit_chain(at, source, ref_id, event, digest, prev, hash) VALUES (?,?,?,?,?,?,?)", (at, source, ref_id, event, d, prev, link(prev, source, ref_id, event, at, d)))

    def sweep(self, *_: Any) -> int:
        """Seal every closed request and every settings change that is not sealed yet. Safe to call any time, as often as wanted (it is called after each answer, each
        change of settings, at start-up and by the maintenance job). Returns how many were sealed."""
        sealed = 0
        with self.db.transaction():
            first = self.db.query_one("SELECT 1 FROM audit_chain LIMIT 1") is None
            rows = self.db.query("SELECT * FROM approvals WHERE status!='pending' AND id NOT IN (SELECT ref_id FROM audit_chain WHERE source='approval') ORDER BY COALESCE(decided_at, created_at), id")
            for r in rows:
                self._append("approval", r["id"], "sealed" if first else r["status"], approval_payload(r))
                sealed += 1
            done = self.db.query_one("SELECT MAX(CAST(ref_id AS INTEGER)) AS n FROM audit_chain WHERE source='setting'")["n"] or 0
            for r in self.db.query("SELECT * FROM settings_audit WHERE id > ? ORDER BY id", (done,)):
                self._append("setting", str(r["id"]), "sealed" if first else "changed", setting_payload(r))
                sealed += 1
        return sealed

    def verify(self) -> dict[str, Any]:
        """Walk the chain. ok = nothing changed, nothing deleted, the chain unbroken."""
        problems: list[str] = []
        prev, n, last_at = GENESIS, 0, None
        for row in self.db.query("SELECT * FROM audit_chain ORDER BY seq"):
            n += 1
            if row["prev"] != prev or row["hash"] != link(prev, row["source"], row["ref_id"], row["event"], row["at"], row["digest"]):
                problems.append(f"the seal itself was edited at entry {row['seq']} ({row['source']} {row['ref_id']})")
            current = self._current(row["source"], row["ref_id"])
            if current is None:
                problems.append(f"{'request' if row['source'] == 'approval' else 'settings change'} {row['ref_id']} was deleted after it was sealed on {row['at'][:10]}")
            elif digest(current) != row["digest"]:
                problems.append(f"{'request' if row['source'] == 'approval' else 'settings change'} {row['ref_id']} was changed after it was sealed on {row['at'][:10]}")
            prev, last_at = row["hash"], row["at"]
            if len(problems) >= 50:
                problems.append("... and possibly more (stopped after 50)")
                break
        unsealed = self.db.query_one("SELECT COUNT(*) AS n FROM approvals WHERE status!='pending' AND id NOT IN (SELECT ref_id FROM audit_chain WHERE source='approval')")["n"]
        return {"ok": not problems, "entries": n, "latest_hash": prev, "latest_at": last_at, "problems": problems, "not_sealed_yet": unsealed}

    def sealed_at(self, source: str, ref_id: str) -> Optional[str]:
        row = self.db.query_one("SELECT at FROM audit_chain WHERE source=? AND ref_id=? ORDER BY seq LIMIT 1", (source, ref_id))
        return row["at"] if row else None

    def _current(self, source: str, ref_id: str) -> Optional[dict[str, Any]]:
        if source == "approval":
            row = self.db.query_one("SELECT * FROM approvals WHERE id=?", (ref_id,))
            return approval_payload(row) if row else None
        row = self.db.query_one("SELECT * FROM settings_audit WHERE id=?", (int(ref_id),))
        return setting_payload(row) if row else None
