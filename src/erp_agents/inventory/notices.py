"""Stock alerts: information-only notices ("low stock", "needed urgently") sent BEFORE any request to reorder.

An alert asks nothing of the owner except to acknowledge it. It is remembered per item so the same problem is not announced over and over:
one alert per episode, a worse level (low -> urgent) is announced at once, a still-low item is repeated only after a few days, and
everything is cleared when the item recovers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..core.common import Clock, iso, parse_dt, short_id, utcnow
from ..core.storage import Database

LEVELS = {"LOW": 0, "URGENT": 1}
ACTIVE = ("sent", "acknowledged", "snoozed")


@dataclass
class Notice:
    id: str
    item_code: str
    level: str
    status: str
    created_at: datetime
    approval_id: Optional[str]
    snoozed_until: Optional[datetime]


class NoticeRepo:
    def __init__(self, db: Database, clock: Clock = utcnow) -> None:
        self.db, self.clock = db, clock

    def create(self, item_code: str, level: str, approval_id: Optional[str] = None) -> Notice:
        now, nid = iso(self.clock()), short_id(10)
        self.db.execute("INSERT INTO stock_notices(id, item_code, level, status, created_at, updated_at, approval_id) VALUES (?,?,?,?,?,?,?)",
                        (nid, item_code, level, "sent", now, now, approval_id))
        return self.get(nid)                                                       # type: ignore[return-value]

    def get(self, nid: str) -> Optional[Notice]:
        r = self.db.query_one("SELECT * FROM stock_notices WHERE id=?", (nid,))
        return _row(r) if r else None

    def by_approval(self, approval_id: str) -> Optional[Notice]:
        r = self.db.query_one("SELECT * FROM stock_notices WHERE approval_id=?", (approval_id,))
        return _row(r) if r else None

    def latest_active(self, item_code: str) -> Optional[Notice]:
        marks = ",".join("?" * len(ACTIVE))
        r = self.db.query_one(f"SELECT * FROM stock_notices WHERE item_code=? AND status IN ({marks}) ORDER BY created_at DESC, rowid DESC LIMIT 1", (item_code, *ACTIVE))
        return _row(r) if r else None

    def active_for_item(self, item_code: str) -> list[Notice]:
        marks = ",".join("?" * len(ACTIVE))
        return [_row(r) for r in self.db.query(f"SELECT * FROM stock_notices WHERE item_code=? AND status IN ({marks})", (item_code, *ACTIVE))]

    def set_approval(self, nid: str, approval_id: str) -> None:
        self.db.execute("UPDATE stock_notices SET approval_id=?, updated_at=? WHERE id=?", (approval_id, iso(self.clock()), nid))

    def set_status(self, nid: str, status: str, snoozed_until: Optional[datetime] = None) -> None:
        self.db.execute("UPDATE stock_notices SET status=?, updated_at=?, snoozed_until=COALESCE(?, snoozed_until) WHERE id=?",
                        (status, iso(self.clock()), iso(snoozed_until) if snoozed_until else None, nid))

    def list(self, limit: int = 100) -> list[Notice]:
        return [_row(r) for r in self.db.query("SELECT * FROM stock_notices ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))]


def _row(r) -> Notice:
    return Notice(id=r["id"], item_code=r["item_code"], level=r["level"], status=r["status"], created_at=parse_dt(r["created_at"]),
                  approval_id=r["approval_id"], snoozed_until=parse_dt(r["snoozed_until"]))
