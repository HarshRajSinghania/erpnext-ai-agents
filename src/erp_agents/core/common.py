"""Small shared helpers: clock, ids, roles/actors, number formatting."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def short_id(n: int = 10) -> str:
    """Short, URL/callback-safe id (Telegram callback_data is limited to 64 bytes)."""
    return uuid.uuid4().hex[:n]


# Roles: admin > approver > viewer
ROLE_ORDER = {"viewer": 0, "approver": 1, "admin": 2}


@dataclass(frozen=True)
class Actor:
    """Who is performing an action (a person, via some channel, or the system)."""

    id: str
    roles: frozenset[str] = field(default_factory=frozenset)
    via: str = "system"
    # Set for people who signed in through ERPNext: what they may do is then exactly these capabilities
    # ("look", "decide", "settings"), with NO implied ladder (a System Manager who may not create purchase
    # orders in ERPNext can change settings but cannot approve one). None = the older role ladder below.
    caps: Optional[frozenset[str]] = None

    def has_role(self, minimum: str) -> bool:
        if self.caps is not None:
            return {"viewer": "look", "approver": "decide", "admin": "settings"}[minimum] in self.caps
        need = ROLE_ORDER[minimum]
        return any(ROLE_ORDER.get(r, -1) >= need for r in self.roles)


SYSTEM_ACTOR = Actor(id="system", roles=frozenset({"admin"}), via="system")


def fmt_num(value: float | None, digits: int = 1) -> str:
    """12.0 -> '12', 12.345 -> '12.3'; None -> 'n/a'."""
    if value is None:
        return "n/a"
    if abs(value - round(value)) < 10 ** (-digits - 1):
        return f"{int(round(value)):,}"
    return f"{value:,.{digits}f}"
