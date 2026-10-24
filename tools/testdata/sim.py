"""Plans the year of business, day by day, without touching ERPNext.

The plan is a chronological list of events (purchase orders, receipts, invoices, payments, deliveries, journals ...).
Planning simulates stock so that nothing is delivered that has not been received, and reorders the way the agent would
(reorder point from demand and lead time, cheapest landed-cost vendor). Deterministic: the same seed gives the same year.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from . import catalog, finance, world

# event kinds, in the order they happen within one day
PO, PR, CUSTOMS, PI, PAY_SUP, SO, DN, SI, PAY_CUST, JV, STOCK_XFER = "PO", "PR", "CUSTOMS", "PI", "PAY_SUP", "SO", "DN", "SI", "PAY_CUST", "JV", "STOCK_XFER"
ORDER = {PO: 0, PR: 1, CUSTOMS: 2, PI: 3, STOCK_XFER: 4, SO: 5, DN: 6, SI: 7, PAY_SUP: 8, PAY_CUST: 9, JV: 10}
TIME = {PO: "09:00:00", PR: "10:00:00", CUSTOMS: "10:30:00", PI: "11:00:00", STOCK_XFER: "12:00:00", SO: "09:30:00", DN: "14:00:00", SI: "15:00:00",
        PAY_SUP: "16:00:00", PAY_CUST: "16:30:00", JV: "17:00:00"}

SEASON = {1: 0.95, 2: 1.0, 3: 1.35, 4: 0.9, 5: 0.9, 6: 0.85, 7: 0.9, 8: 1.0, 9: 1.1, 10: 1.25, 11: 1.3, 12: 1.15}   # year-end push, festival season, fiscal year end in March
ORDERS_PER_WEEKDAY = 1.7
WORKDAYS = (0, 1, 2, 3, 4)


@dataclass
class Event:
    day: date
    kind: str
    key: str                       # unique, stable id: used to find the document again if the load is re-run
    data: dict[str, Any] = field(default_factory=dict)
    parent: Optional[str] = None   # key of the event this one follows

    @property
    def sort_key(self) -> tuple:
        return (self.day, ORDER[self.kind], self.key)


@dataclass
class Plan:
    events: list[Event]
    stats: dict[str, Any]


# ------------------------------------------------------------------ landed cost the way the purchase templates compute it
def landed_uplift(item: catalog.Item, country: str) -> float:
    """Extra cost as a fraction of the goods value for importing `item` from `country` (freight, insurance, customs duty, surcharge, anti-dumping)."""
    if country == "India":
        return 0.0
    c = world.IMPORT_COUNTRIES[country]
    fr, ins = c.freight_pct / 100, c.insurance_pct / 100
    cif = 1 + fr + ins
    fta = c.import_regime.startswith("fta")
    bcd = cif * (item.bcd_fta if fta else item.bcd_mfn) / 100
    ad = cif * item.add_cn / 100 if c.import_regime == "mfn_ad" else 0.0
    return fr + ins + bcd + bcd * 0.10 + ad


def payment_days(template: str) -> int:
    terms = {t[0]: t[2] for t in finance.PAYMENT_TERMS}
    return max(terms[t] for t, _p in finance.PAYMENT_TEMPLATES[template])


# ------------------------------------------------------------------ the planner
CUSTOMER_GROUP_MIX = {   # what each kind of customer tends to buy
    "industrial": {"MET": 3, "MEC": 4, "ELE": 3, "FGI": 4, "FAS": 2, "CHM": 2, "PKG": 1},
    "oem": {"MEC": 4, "ELE": 4, "FAS": 4, "MET": 2, "PKG": 2, "CHM": 1},
    "retail": {"FGC": 5, "FAS": 3, "PKG": 1, "CHM": 1},
    "distributor": {"FGC": 4, "FAS": 3, "FGI": 2, "ELE": 2, "MEC": 2, "PKG": 2, "CHM": 1},
    "sez": {"MET": 3, "MEC": 3, "ELE": 3, "FAS": 3, "FGI": 2},
}


def build_plan(seed: str = "year-v1", start: date = world.PERIOD_START, end: date = world.PERIOD_END, orders_per_day: float = ORDERS_PER_WEEKDAY) -> Plan:
    rng = random.Random(seed)
    fx = world.fx_series()
    goods = [i for i in catalog.build_items() if i.stock]
    by_code = {i.code: i for i in goods}
    vendors = {v.name: v for v in world.VENDORS}
    customers = world.CUSTOMERS

    weight = {i.code: rng.lognormvariate(0, 0.9) for i in goods}
    total_w = sum(weight.values())
    sell_inr = {i.code: i.base_price * (1 + i.margin) for i in goods}
    typical_qty = {i.code: _round_qty(i, 60_000 / sell_inr[i.code]) for i in goods}
    lines_per_day = orders_per_day * 2.5
    daily_demand = {i.code: lines_per_day * weight[i.code] / total_w * typical_qty[i.code] * 1.6 for i in goods}      # 1.6: export orders are larger

    onhand: dict[str, float] = defaultdict(float)
    on_order: dict[str, float] = defaultdict(float)
    backlog: dict[str, float] = defaultdict(float)             # ordered by customers, not yet delivered
    unit_cost: dict[str, float] = defaultdict(float)           # moving average, INR (used for the transfer sizes only)
    events: list[Event] = []
    arrivals: dict[date, list[Event]] = defaultdict(list)
    deliveries: dict[date, list[Event]] = defaultdict(list)
    seq = defaultdict(int)
    gst = {"Output CGST": 0.0, "Output SGST": 0.0, "Output IGST": 0.0, "Input CGST": 0.0, "Input SGST": 0.0, "Input IGST": 0.0, "Input IGST on Imports": 0.0}
    stats: dict[str, Any] = defaultdict(float)

    def key(prefix: str, day: date) -> str:
        seq[prefix] += 1
        return f"{prefix}-{day:%Y%m}-{seq[prefix]:04d}"

    def add(kind: str, day: date, k: str, data: dict, parent: Optional[str] = None) -> Event:
        e = Event(day, kind, k, data, parent)
        if day <= end:
            events.append(e)
        return e

    # item -> suppliers that can be bought from
    def pick_supplier(it: catalog.Item, day: date) -> tuple[str, float, int]:
        scored = []
        for name, price, lead in it.suppliers:
            v = vendors[name]
            ccy = world.vendor_currency(v)
            inr = price * world.fx_on(fx, ccy, day) * (1 + landed_uplift(it, v.country))
            scored.append((inr, name, price, lead))
        scored.sort()
        pick = scored[0] if len(scored) == 1 or rng.random() > 0.2 else scored[1]      # 1 order in 5 goes to the second source
        return pick[1], pick[2], pick[3]

    day = start
    first_monday = start + timedelta(days=(7 - start.weekday()) % 7)
    month_done: set[tuple[int, int]] = set()
    while day <= end:
        # ---------------- receipts due today
        for pr in arrivals.pop(day, []):
            for item, qty, _rate in pr.data["lines"]:
                onhand[item] += qty
                on_order[item] -= qty
        # ---------------- deliveries due today (stock is checked at this moment)
        for so_ev in deliveries.pop(day, []):
            lines = []
            for item, qty in so_ev.data["open"]:
                ship = min(qty, math.floor(onhand[item]))
                if ship >= 1:
                    lines.append((item, ship))
                    onhand[item] -= ship
                    backlog[item] -= ship
            if not lines:
                # nothing in stock: try again in a week, give up after five weeks (the order stays open in ERPNext)
                if so_ev.data["attempt"] < 5:
                    so_ev.data["attempt"] += 1
                    deliveries[day + timedelta(days=7)].append(so_ev)
                continue
            shipped = dict(lines)
            so_ev.data["open"] = [(i, q - shipped.get(i, 0)) for i, q in so_ev.data["open"] if q - shipped.get(i, 0) > 0]
            part = f"{so_ev.key}-D{so_ev.data['dn_no']}"
            so_ev.data["dn_no"] += 1
            dn = add(DN, day, part, {"lines": lines, "customer": so_ev.data["customer"]}, parent=so_ev.key)
            if dn.day > end:
                continue
            lag = rng.choices([0, 1, 2], [0.6, 0.3, 0.1])[0]
            si = add(SI, day + timedelta(days=lag), part.replace("-D", "-I"), {"customer": so_ev.data["customer"], "lines": lines}, parent=part)
            cust = so_ev.data["cust_obj"]
            terms_days = payment_days(finance.customer_terms(cust))
            delay = terms_days + rng.randint(-3, 14) + (rng.randint(25, 60) if rng.random() < 0.08 else 0)
            add(PAY_CUST, si.day + timedelta(days=max(1, delay)), part.replace("-D", "-R"), {"customer": cust.name, "lines": lines}, parent=si.key)
            stats["deliveries"] += 1
            if so_ev.data["open"]:
                deliveries[day + timedelta(days=rng.randint(5, 12))].append(so_ev)
        # ---------------- new customer orders
        if day.weekday() in WORKDAYS and day >= start + timedelta(days=2):
            n = _poisson(rng, orders_per_day * SEASON[day.month])
            for _ in range(n):
                cust = _pick_customer(rng, customers)
                mix = CUSTOMER_GROUP_MIX[cust.kind]
                n_lines = rng.choices([1, 2, 3, 4], [0.25, 0.35, 0.28, 0.12])[0]
                chosen: dict[str, float] = {}
                for _l in range(n_lines):
                    grp = rng.choices(list(mix), list(mix.values()))[0]
                    pool = [i for i in goods if i.group_code == grp]
                    it = rng.choices(pool, [weight[i.code] for i in pool])[0]
                    mult = rng.choice([0.5, 1, 1, 1.5, 2]) * (2.0 if cust.country != "India" else 1.0)
                    chosen[it.code] = chosen.get(it.code, 0) + _round_qty(it, typical_qty[it.code] * mult)
                lines = [(c, q) for c, q in chosen.items()]
                k = key("SO", day)
                lag = rng.randint(2, 6) if cust.country == "India" else rng.randint(5, 14)
                ev = add(SO, day, k, {"customer": cust.name, "lines": lines, "cust_obj": cust, "open": list(lines), "attempt": 0, "dn_no": 1, "deliver_on": day + timedelta(days=lag)})
                for item, q in lines:
                    backlog[item] += q
                deliveries[day + timedelta(days=lag)].append(ev)
                stats["orders"] += 1
        # ---------------- weekly purchasing review (the agent's job in real life)
        if day >= first_monday and day.weekday() == 0:
            need: dict[str, list[tuple[catalog.Item, float, float, int]]] = defaultdict(list)
            for it in goods:
                lead_guess = max(l for _n, _p, l in it.suppliers)
                position = onhand[it.code] + on_order[it.code] - backlog[it.code]
                reorder_point = daily_demand[it.code] * (lead_guess + 21)
                if position < reorder_point:
                    vendor, price, lead = pick_supplier(it, day)
                    target = daily_demand[it.code] * (lead + 60)
                    qty = max(it.moq, math.ceil((target - position) / it.moq) * it.moq)
                    qty = _cap_value(it, qty, price, world.vendor_currency(vendors[vendor]), fx, day)
                    need[vendor].append((it, qty, price, lead))
            for vendor, rows in sorted(need.items()):
                v = vendors[vendor]
                ccy = world.vendor_currency(v)
                for chunk in _chunks(rows, 25):
                    k = key("PO", day)
                    lead = max(r[3] for r in chunk)
                    arrive = day + timedelta(days=lead + (rng.randint(0, 4) if v.country != "India" else rng.randint(0, 2)))
                    drift = 1 + 0.004 * (day - start).days / 30
                    lines = [(it.code, qty, round(price * (drift if v.country == "India" else 1.0) * rng.uniform(0.985, 1.015), 3 if price < 100 else 2)) for it, qty, price, _l in chunk]
                    po = add(PO, day, k, {"supplier": vendor, "currency": ccy, "lines": lines, "arrive": arrive, "country": v.country})
                    for item, qty, _r in lines:
                        on_order[item] += qty
                    stats["purchase_orders"] += 1
                    if arrive > end:
                        continue
                    pr = add(PR, arrive, k.replace("PO", "PR"), {"supplier": vendor, "currency": ccy, "lines": lines, "country": v.country}, parent=k)
                    arrivals[arrive].append(pr)
                    if v.country != "India":
                        add(CUSTOMS, arrive, k.replace("PO", "CUS"), {"supplier": vendor, "country": v.country, "lines": lines}, parent=pr.key)
                    inv_day = arrive + timedelta(days=rng.randint(0, 5))
                    pi = add(PI, inv_day, k.replace("PO", "PI"), {"supplier": vendor, "currency": ccy, "lines": lines}, parent=pr.key)
                    pay_day = inv_day + timedelta(days=max(1, payment_days(finance.vendor_terms(v)) + rng.randint(-5, 10)))
                    add(PAY_SUP, pay_day, k.replace("PO", "PS"), {"supplier": vendor, "currency": ccy}, parent=pi.key)
        # ---------------- monthly journals and stock moves
        ym = (day.year, day.month)
        if ym not in month_done and (day.day >= 25 or day == end) and day.weekday() in WORKDAYS:
            month_done.add(ym)
            for ev in _month_end_journals(day, rng, key):
                add(JV, day, ev["key"], ev)
        if day.day == 20 and day.weekday() in WORKDAYS or (day.day in (21, 22) and day.weekday() == 0):
            if (day.year, day.month, "gst") not in month_done:
                month_done.add((day.year, day.month, "gst"))
                add(JV, day, f"JV-{day:%Y%m}-GST", {"key": f"JV-{day:%Y%m}-GST", "type": "gst", "title": f"GST settlement {day:%b %Y}"})
        day += timedelta(days=1)

    capex = date(2025, 11, 12)
    events.append(Event(capex, JV, "JV-CAPEX", {"key": "JV-CAPEX", "type": "lines", "title": "Purchase of plant, equipment and furniture", "lines": [
        ("Plants and Machineries", 6_200_000, 0), ("Office Equipment", 1_400_000, 0), ("Electronic Equipment", 1_100_000, 0), ("Furniture and Fixtures", 800_000, 0)]}))
    stats["events"] = len(events)
    events.sort(key=lambda e: e.sort_key)
    return Plan(events, dict(stats))


def _round_qty(it: catalog.Item, q: float) -> float:
    q = max(1.0, q)
    if q >= 1000:
        return float(round(q / 100) * 100)
    if q >= 100:
        return float(round(q / 10) * 10)
    if q >= 20:
        return float(round(q / 5) * 5)
    return float(round(q))


def _cap_value(it: catalog.Item, qty: float, price: float, ccy: str, fx: dict, day: date) -> float:
    """Keep any single purchase line under ~4 million INR so the cash plan stays realistic."""
    inr = price * world.fx_on(fx, ccy, day)
    if inr * qty > 4_000_000:
        qty = max(it.moq, math.floor(4_000_000 / inr / it.moq) * it.moq)
    return qty


def _chunks(rows: list, n: int):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _pick_customer(rng: random.Random, customers: list[world.Customer]) -> world.Customer:
    return rng.choices(customers, [c.credit_limit ** 0.6 for c in customers])[0]


def _month_end_journals(day: date, rng: random.Random, key) -> list[dict]:
    """Overheads and financing entries for the month: (account, debit, credit) lines against the main bank."""
    ym = f"{day:%Y%m}"
    return [
        {"key": f"JV-{ym}-PAY", "type": "lines", "title": f"Salaries {day:%b %Y}", "lines": [("Salaries & Wages", 1_850_000 + rng.randint(-40, 80) * 1000, 0)]},
        {"key": f"JV-{ym}-RENT", "type": "lines", "title": f"Rent and utilities {day:%b %Y}", "lines": [("Warehouse Rent", 640_000, 0), ("Office Rent", 220_000, 0), ("Utility Expenses", 95_000 + rng.randint(0, 40) * 1000, 0)]},
        {"key": f"JV-{ym}-OPEX", "type": "lines", "title": f"Operating expenses {day:%b %Y}", "lines": [
            ("Marketing Expenses", 140_000 + rng.randint(0, 120) * 1000, 0), ("Professional Fees", 85_000, 0), ("Bank Charges", 22_000 + rng.randint(0, 15) * 1000, 0),
            ("Telephone Expenses", 18_000, 0), ("Travel Expenses", 60_000 + rng.randint(0, 90) * 1000, 0), ("Customs Compliance Fees", 30_000, 0)]},
        {"key": f"JV-{ym}-INT", "type": "lines", "title": f"Interest {day:%b %Y}", "lines": [("Interest on Loans", 300_000, 0), ("Interest Income", 0, 95_000 + rng.randint(0, 25) * 1000)]},
        {"key": f"JV-{ym}-DEP", "type": "dep", "title": f"Depreciation {day:%b %Y}", "lines": [("Depreciation", 118_000, 0), ("Accumulated Depreciations", 0, 118_000)]},
    ]
