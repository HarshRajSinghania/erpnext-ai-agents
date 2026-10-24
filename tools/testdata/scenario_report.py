"""Reads what the scenario load left in ERPNext and prints it the way the app sees it: per material, the consumption by month (from the stock ledger), the vendors
(orders, prices, observed lead time, on-time share, as get_vendor_offers reports them) and the stock position today.

    python -m tools.testdata.scenario_report            (needs erp.type: erpnext in the config)
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta

from erp_agents.core.config import load_config
from erp_agents.core.erp.erpnext import ERPNextClient

from . import scenarios


def collect(erp: ERPNextClient, today: date) -> dict:
    out: dict = {}
    sales_orders = {r["name"]: r["customer"] for r in erp._list("Sales Order", ["name", "customer"], [["po_no", "like", "TD/SO-S%"], ["docstatus", "=", 1]])}
    for sc in scenarios.SCENARIOS:
        sle = erp._list("Stock Ledger Entry", ["posting_date", "voucher_type", "actual_qty"], [["item_code", "=", sc.code], ["is_cancelled", "=", 0]], order_by="posting_date asc")
        by_month: dict[str, float] = defaultdict(float)
        for r in sle:
            if r["voucher_type"] == "Delivery Note":
                by_month[str(r["posting_date"])[:7]] += -float(r["actual_qty"])
        on_hand = sum(float(b["actual_qty"]) for b in erp._list("Bin", ["actual_qty"], [["item_code", "=", sc.code]]))
        parents = {r["parent"] for r in erp._list("Sales Order Item", ["parent"], [["item_code", "=", sc.code]], parent="Sales Order")}
        customers = {sales_orders[p] for p in parents if p in sales_orders}
        offers = erp.get_vendor_offers(sc.code, today - timedelta(days=400))
        out[sc.code] = {"scenario": sc, "by_month": dict(by_month), "on_hand": on_hand, "customers": len(customers), "offers": offers}
    return out


def render(data: dict) -> str:
    months = sorted({m for d in data.values() for m in d["by_month"]})
    lines = ["Consumption (units delivered to customers, by month)", "", f"{'':34}" + "".join(f"{m[2:]:>8}" for m in months)]
    for code, d in data.items():
        sc = d["scenario"]
        lines.append(f"{code} {sc.name[:27]:<28}" + "".join(f"{d['by_month'].get(m, 0):>8,.0f}" for m in months) + f"   [{sc.pattern}]")
    lines += ["", "Vendors as the app reads them (from the purchase orders and receipts in ERPNext)"]
    for code, d in data.items():
        sc = d["scenario"]
        lines += ["", f"{code} {sc.name}: {len(d['offers'])} vendors, {d['customers']} customers, {d['on_hand']:,.0f} {sc.uom} in stock now ({sc.note})"]
        for o in sorted(d["offers"], key=lambda o: (o.unit_price or 0)):
            lines.append(f"    {o.name[:38]:<38} {o.country or '':<18} {o.orders:>3} orders  last price {o.unit_price or 0:>10,.2f} (company currency)  lead {o.lead_time_days or 0:>5.1f} d  "
                         + (f"on time {o.on_time_rate * 100:>3.0f}%" if o.on_time_rate is not None else "on time  n/a"))
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="scenario_report")
    p.add_argument("--config", default="config/settings.erpnext.yaml")
    args = p.parse_args(argv)
    cfg = load_config(args.config)["erp"]
    if cfg.get("type") != "erpnext":
        raise SystemExit("needs erp.type: erpnext in the config")
    erp = ERPNextClient(cfg["url"], cfg["api_key"], cfg["api_secret"], company=cfg.get("company"))
    print(render(collect(erp, date.today())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
