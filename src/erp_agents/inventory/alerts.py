"""Alert persistence + lifecycle for the inventory agent."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from ..core.common import Clock, iso, parse_dt, short_id, utcnow
from ..core.storage import Database, dumps, loads

ACTIVE = ("awaiting",)
OPEN_STATES = ("awaiting", "snoozed", "dismissed", "actioned")


@dataclass
class Alert:
    id: str
    item_code: str
    kind: str
    severity: int
    status: str
    created_at: datetime
    updated_at: datetime
    analysis: dict[str, Any]
    reasoning_text: str
    approval_id: Optional[str]
    snoozed_until: Optional[datetime]
    suppress_until: Optional[datetime]
    cooldown_until: Optional[datetime]
    status_note: str
    run_id: Optional[str]


class AlertRepo:
    def __init__(self, db: Database, clock: Clock = utcnow) -> None:
        self.db = db
        self.clock = clock

    def create(self, item_code: str, kind: str, severity: int, analysis: dict, reasoning_text: str, run_id: Optional[str]) -> Alert:
        now = iso(self.clock())
        alert_id = short_id(10)
        self.db.execute(
            "INSERT INTO alerts(id, item_code, kind, severity, status, created_at, updated_at, analysis_json, reasoning_text, run_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (alert_id, item_code, kind, severity, "awaiting", now, now, dumps(analysis), reasoning_text, run_id),
        )
        return self.get(alert_id)

    def get(self, alert_id: str) -> Optional[Alert]:
        r = self.db.query_one("SELECT * FROM alerts WHERE id=?", (alert_id,))
        return _to_alert(r) if r else None

    def latest_for_item(self, item_code: str) -> Optional[Alert]:
        r = self.db.query_one("SELECT * FROM alerts WHERE item_code=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (item_code,))
        return _to_alert(r) if r else None

    def open_for_item(self, item_code: str) -> list[Alert]:
        marks = ",".join("?" * len(OPEN_STATES))
        rows = self.db.query(f"SELECT * FROM alerts WHERE item_code=? AND status IN ({marks})", (item_code, *OPEN_STATES))
        return [_to_alert(r) for r in rows]

    def list(self, status: Optional[str] = None, item_code: Optional[str] = None, limit: int = 100) -> list[Alert]:
        sql, params, conds = "SELECT * FROM alerts", [], []
        if status:
            conds.append("status=?"); params.append(status)
        if item_code:
            conds.append("item_code=?"); params.append(item_code)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return [_to_alert(r) for r in self.db.query(sql, params)]

    def set_approval(self, alert_id: str, approval_id: str) -> None:
        self.db.execute("UPDATE alerts SET approval_id=?, updated_at=? WHERE id=?", (approval_id, iso(self.clock()), alert_id))

    def update_status(self, alert_id: str, status: str, note: str = "", *, snoozed_until: Optional[datetime] = None,
                      suppress_until: Optional[datetime] = None, cooldown_until: Optional[datetime] = None) -> None:
        self.db.execute(
            "UPDATE alerts SET status=?, status_note=?, updated_at=?, snoozed_until=COALESCE(?, snoozed_until), "
            "suppress_until=COALESCE(?, suppress_until), cooldown_until=COALESCE(?, cooldown_until) WHERE id=?",
            (status, note, iso(self.clock()), iso(snoozed_until), iso(suppress_until), iso(cooldown_until), alert_id),
        )

    def by_approval(self, approval_id: str) -> Optional[Alert]:
        r = self.db.query_one("SELECT * FROM alerts WHERE approval_id=?", (approval_id,))
        return _to_alert(r) if r else None


def _to_alert(r) -> Alert:
    return Alert(
        id=r["id"], item_code=r["item_code"], kind=r["kind"], severity=r["severity"], status=r["status"],
        created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
        analysis=loads(r["analysis_json"]), reasoning_text=r["reasoning_text"], approval_id=r["approval_id"],
        snoozed_until=parse_dt(r["snoozed_until"]), suppress_until=parse_dt(r["suppress_until"]),
        cooldown_until=parse_dt(r["cooldown_until"]), status_note=r["status_note"] or "", run_id=r["run_id"],
    )
