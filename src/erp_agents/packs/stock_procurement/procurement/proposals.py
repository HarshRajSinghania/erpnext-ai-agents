"""Stored purchase-order proposals: what the vendor selector recommended, what the owner decided, and the PO it led to."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from erp_agents.core.common import Clock, iso, parse_dt, short_id, utcnow
from erp_agents.core.storage import Database, dumps, loads
from ..schema import ensure_schema

OPEN_STATUSES = ("awaiting", "manual")      # a request is out and unanswered: do not ask again for the same item


@dataclass
class Proposal:
    id: str
    work_order_id: str
    item_code: str
    status: str
    approval_id: Optional[str]
    supplier: Optional[str]
    supplier_name: Optional[str]
    qty: Optional[float]
    rate: Optional[float]
    total: Optional[float]
    po_name: Optional[str]
    ranking: Optional[dict[str, Any]]
    status_note: str
    snoozed_until: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "work_order_id": self.work_order_id, "item_code": self.item_code, "status": self.status,
            "approval_id": self.approval_id, "supplier": self.supplier, "supplier_name": self.supplier_name,
            "qty": self.qty, "rate": self.rate, "total": self.total, "po_name": self.po_name,
            "status_note": self.status_note, "created_at": iso(self.created_at), "updated_at": iso(self.updated_at),
            "snoozed_until": iso(self.snoozed_until) if self.snoozed_until else None, "ranking": self.ranking,
        }


class ProposalRepo:
    def __init__(self, db: Database, clock: Clock = utcnow) -> None:
        ensure_schema(db)
        self.db, self.clock = db, clock

    def create(self, work_order_id: str, item_code: str, status: str, *, supplier: Optional[str] = None,
               supplier_name: Optional[str] = None, qty: Optional[float] = None, rate: Optional[float] = None,
               total: Optional[float] = None, ranking: Optional[dict[str, Any]] = None, note: str = "") -> Proposal:
        now, pid = iso(self.clock()), short_id(10)
        self.db.execute(
            "INSERT INTO proposals(id, work_order_id, item_code, status, supplier, supplier_name, qty, rate, total, ranking_json, status_note, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, work_order_id, item_code, status, supplier, supplier_name, qty, rate, total, dumps(ranking) if ranking else None, note, now, now),
        )
        return self.get(pid)  # type: ignore[return-value]

    def get(self, pid: str) -> Optional[Proposal]:
        r = self.db.query_one("SELECT * FROM proposals WHERE id=?", (pid,))
        return _row(r) if r else None

    def by_approval(self, approval_id: str) -> Optional[Proposal]:
        r = self.db.query_one("SELECT * FROM proposals WHERE approval_id=?", (approval_id,))
        return _row(r) if r else None

    def open_for_item(self, item_code: str) -> list[Proposal]:
        marks = ",".join("?" for _ in OPEN_STATUSES)
        return [_row(r) for r in self.db.query(f"SELECT * FROM proposals WHERE item_code=? AND status IN ({marks})", (item_code, *OPEN_STATUSES))]  # only "?" placeholders are formatted in  # nosec B608

    def snoozed_due(self) -> list[Proposal]:
        now = iso(self.clock())
        return [_row(r) for r in self.db.query("SELECT * FROM proposals WHERE status='snoozed' AND snoozed_until IS NOT NULL AND snoozed_until <= ?", (now,))]

    def list(self, status: Optional[str] = None, item_code: Optional[str] = None, limit: int = 100) -> list[Proposal]:
        sql, params, conds = "SELECT * FROM proposals", [], []
        if status:
            conds.append("status=?"); params.append(status)
        if item_code:
            conds.append("item_code=?"); params.append(item_code)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [_row(r) for r in self.db.query(sql, params)]

    def set_ranking(self, pid: str, ranking: dict[str, Any]) -> None:
        self.db.execute("UPDATE proposals SET ranking_json=?, updated_at=? WHERE id=?", (dumps(ranking), iso(self.clock()), pid))

    def set_approval(self, pid: str, approval_id: str) -> None:
        self.db.execute("UPDATE proposals SET approval_id=?, updated_at=? WHERE id=?", (approval_id, iso(self.clock()), pid))

    def revise(self, pid: str, *, supplier: Optional[str], supplier_name: Optional[str], qty: Optional[float], rate: Optional[float], total: Optional[float], note: str) -> None:
        """The owner changed the quantity or the vendor of an open proposal: it stays the same proposal, awaiting an answer."""
        self.db.execute("UPDATE proposals SET supplier=?, supplier_name=?, qty=?, rate=?, total=?, status_note=?, updated_at=? WHERE id=?",
                        (supplier, supplier_name, qty, rate, total, note, iso(self.clock()), pid))

    def update_status(self, pid: str, status: str, note: str = "", **extra: Any) -> None:
        sets, params = ["status=?", "status_note=?", "updated_at=?"], [status, note, iso(self.clock())]
        for col in ("po_name", "snoozed_until"):
            if col in extra:
                sets.append(f"{col}=?")
                v = extra[col]
                params.append(iso(v) if isinstance(v, datetime) else v)
        params.append(pid)
        self.db.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id=?", params)  # the column names come from the fixed tuple above and every value is bound  # nosec B608


def _row(r) -> Proposal:
    return Proposal(
        id=r["id"], work_order_id=r["work_order_id"], item_code=r["item_code"], status=r["status"], approval_id=r["approval_id"],
        supplier=r["supplier"], supplier_name=r["supplier_name"], qty=r["qty"], rate=r["rate"], total=r["total"], po_name=r["po_name"],
        ranking=loads(r["ranking_json"]), status_note=r["status_note"] or "", snoozed_until=parse_dt(r["snoozed_until"]),
        created_at=parse_dt(r["created_at"]), updated_at=parse_dt(r["updated_at"]),
    )
