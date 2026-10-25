"""The follow-up services as the application sees them: `app.followup` (purchasing) and `app.stock_insight` (stock intelligence)."""
from __future__ import annotations

from typing import Any

from ..inventory.counts import CycleCounts
from ..inventory.slow_stock import SlowStock
from ..inventory.substitutes import Substitutes
from .deliveries import DeliveryChaser
from .quotes import QuoteRounds
from .receipts import ReceiptChecker
from .vendors import VendorMessenger


class FollowUp:
    """Chasing late deliveries, checking goods receipts, quote rounds, and the messenger that reaches vendors."""

    def __init__(self, app: Any) -> None:
        self.messenger = VendorMessenger(app)
        self.chaser = DeliveryChaser(app, self.messenger)
        self.receipts = ReceiptChecker(app)
        self.quotes = QuoteRounds(app, self.messenger)

    def daily(self, dry_run: bool = False) -> dict[str, Any]:
        return {"deliveries": self.chaser.run(dry_run), "receipts": self.receipts.run(dry_run)}


class StockInsight:
    """Approved substitutes, slow and dead stock, cycle counts."""

    def __init__(self, app: Any) -> None:
        self.substitutes = Substitutes(app)
        self.slow = SlowStock(app)
        self.counts = CycleCounts(app)
