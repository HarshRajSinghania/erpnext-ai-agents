"""Work-order queue: how agents hand tasks to each other.

The inventory agent emits REORDER_NEEDED after the owner approves; the procurement agent (next
module) claims it, picks a vendor and asks for the PO approval. Plain table + typed payloads, no
broker needed at this scale. `subscribe()` lets in-process agents react immediately.
"""
from __future__ import annotations

import logging

from typing import Any, Callable, Optional

from .common import Clock, iso, short_id, utcnow
from .storage import Database, dumps, loads

log = logging.getLogger(__name__)

REORDER_NEEDED = "REORDER_NEEDED"


class WorkOrderQueue:
    def __init__(self, db: Database, clock: Clock = utcnow) -> None:
        self.db = db
        self.clock = clock
        self._subs: list[Callable[[dict[str, Any]], None]] = []

    def subscribe(self, fn: Callable[[dict[str, Any]], None]) -> None:
        self._subs.append(fn)

    def emit(self, type_: str, payload: dict[str, Any], source: str = "") -> dict[str, Any]:
        now = iso(self.clock())
        wo_id = short_id(10)
        self.db.execute(
            "INSERT INTO work_orders(id, type, status, payload_json, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (wo_id, type_, "pending", dumps(payload), source, now, now),
        )
        wo = self.get(wo_id)
        for fn in self._subs:
            try:
                fn(wo)
            except Exception:  # noqa: BLE001 - a bad subscriber must never undo an approved decision
                log.exception("a work-order subscriber failed")
        return wo

    def get(self, wo_id: str) -> Optional[dict[str, Any]]:
        r = self.db.query_one("SELECT * FROM work_orders WHERE id=?", (wo_id,))
        return _row(r) if r else None

    def list(self, status: Optional[str] = None, type_: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        sql, params, conds = "SELECT * FROM work_orders", [], []
        if status:
            conds.append("status=?"); params.append(status)
        if type_:
            conds.append("type=?"); params.append(type_)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [_row(r) for r in self.db.query(sql, params)]

    def set_status(self, wo_id: str, status: str) -> None:
        self.db.execute("UPDATE work_orders SET status=?, updated_at=? WHERE id=?", (status, iso(self.clock()), wo_id))


def _row(r) -> dict[str, Any]:
    return {"id": r["id"], "type": r["type"], "status": r["status"], "payload": loads(r["payload_json"]),
            "source": r["source"], "created_at": r["created_at"], "updated_at": r["updated_at"]}
