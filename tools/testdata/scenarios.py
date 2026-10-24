"""Ten materials with a rich past, for trying the vendor comparison and the consumption analysis.

Each material is bought from 4 or 5 vendors in different places (Indian cities and several countries) that differ in price, lead time, reliability and price
trend, and is sold to 7 or 8 customers (Indian and export) in a demand pattern of its own: growing, falling, seasonal, in project spikes, in single sudden steps,
on a month-end cycle, rare and lumpy, steady. The plan is built without touching ERPNext (like sim.py) and posted with the same Poster, so it is the same
purchase-to-payment and order-to-cash documents: Purchase Order, Receipt, customs entry, Purchase Invoice, payment; Sales Order, Delivery Note, Sales Invoice, payment.

FICTIONAL and ILLUSTRATIVE, like the rest of the test data. Deterministic: the same seed gives the same history.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from . import catalog, finance, sim, world

START = date(2025, 12, 1)
END = date(2026, 10, 20)
SEED = "scenarios-v1"
SALES_START = START + timedelta(days=14)           # the opening stock arrives before the first customer order
WORKDAYS_PER_MONTH = 21.7
COVER_DAYS = 21                                      # an order brings the stock position to this many days of demand beyond the vendor's lead time
SAFETY_DAYS = 14
MIN_ORDERS_PER_VENDOR = 2

SEASON_MONSOON = {12: 1.0, 1: 1.05, 2: 1.1, 3: 1.5, 4: 1.2, 5: 1.0, 6: 0.5, 7: 0.4, 8: 0.45, 9: 0.9, 10: 1.3, 11: 1.2}     # building and fabrication slow down in the rains
SEASON_FESTIVE = {12: 1.3, 1: 0.8, 2: 0.9, 3: 1.4, 4: 0.8, 5: 0.7, 6: 0.55, 7: 0.5, 8: 0.7, 9: 1.0, 10: 1.9, 11: 1.8}       # packing for the festival season and the March close


@dataclass(frozen=True)
class Source:
    """One vendor of one material."""
    vendor: str                       # exactly as in world.VENDORS
    rel_price: float                  # unit price in rupee terms, before freight and duty, as a fraction of the material's base price
    extra_days: int                   # Indian vendor: the whole lead time; foreign vendor: days on top of the sea or air transit
    share: float = 1.0                # how often the buyer prefers this vendor
    late_prob: float = 0.05           # chance that a delivery arrives after the date promised on the order
    late_days: tuple[int, int] = (2, 6)
    drift: float = 0.004              # price change per month
    from_day: Optional[date] = None   # vendor used only from / until this day
    until_day: Optional[date] = None

    def lead(self) -> int:
        v = _VENDORS[self.vendor]
        return self.extra_days if v.country == "India" else world.IMPORT_COUNTRIES[v.country].transit_days + self.extra_days

    def active(self, day: date) -> bool:
        return (self.from_day is None or day >= self.from_day) and (self.until_day is None or day <= self.until_day)


@dataclass(frozen=True)
class Buyer:
    customer: str
    weight: float
    mult: float = 1.0                 # export customers order in larger lots
    from_day: Optional[date] = None   # a customer that starts buying on this day


@dataclass(frozen=True)
class Scenario:
    code: str
    name: str
    group: str                        # FAS, MET, ...
    product: str                      # an existing product of the catalogue: gives the customs code and the duty rates
    price_factor: float               # base price as a multiple of that product's smallest variant
    uom: str
    kg: float
    pattern: str                      # growth | decline | plateau | step | monsoon | festive | monthend | spiky | intermittent | steady
    monthly: float                    # units sold in an average month
    typical: float                    # units in a typical order
    moq: float
    sources: tuple[Source, ...]
    buyers: tuple[Buyer, ...]
    stop_buying_after: Optional[date] = None     # leaves this material running low (or out of stock) today
    spike_days: tuple[date, ...] = ()            # "spiky": the days of the big project orders
    note: str = ""


S, B = Source, Buyer
JUN1 = date(2026, 6, 1)

SCENARIOS: tuple[Scenario, ...] = (
    Scenario("MET-0025", "Copper Rod 20mm", "MET", "Copper Rod", 1.02, "Kg", 1.0, "growth", 5000, 400, 100, (
        S("Mumbai Metal Traders", 1.03, 4, 3, 0.08), S("Gujarat Steel & Alloys Ltd", 0.97, 7, 3, 0.22, (3, 7)),
        S("Jebel Ali Metals FZE", 0.88, 9, 2, 0.10), S("Busan Steel Industries Co Ltd", 0.84, 11, 2, 0.15, (3, 8)), S("Houston Industrial Supply LLC", 0.82, 10, 1, 0.10)), (
        B("Sahyadri Engineering Works Pvt Ltd", 5), B("Nashik Auto Components Pvt Ltd", 4), B("Mumbai Infra Projects Ltd", 3), B("Gujarat Pumps & Engineering Pvt Ltd", 3),
        B("Rajkot Auto Parts Pvt Ltd", 2), B("NCR Power Systems Pvt Ltd", 2), B("Great Lakes Machinery Corp", 2, 2.0), B("Gulf Contracting FZE", 1, 2.0)),
        note="demand grows steadily, about +10% a month"),
    Scenario("MET-0026", "Stainless Steel Sheet 304 8mm", "MET", "Stainless Steel Sheet 304", 1.10, "Kg", 1.0, "monsoon", 3500, 350, 100, (
        S("Gujarat Steel & Alloys Ltd", 1.00, 6, 3, 0.15), S("Mumbai Metal Traders", 1.05, 3, 2, 0.05), S("Lion City Industrial Trading Pte Ltd", 0.90, 9, 2, 0.08),
        S("Busan Steel Industries Co Ltd", 0.85, 10, 2, 0.12), S("Istanbul Metal Sanayi A.S.", 0.88, 12, 1, 0.30, (4, 10))), (
        B("Hyderabad Pharma Equipment Pvt Ltd", 4), B("Howrah Foundry & Fabrication Ltd", 2), B("Surat Textile Machinery Pvt Ltd", 3), B("Kandla SEZ Fabricators Pvt Ltd", 3),
        B("Mahindra World City Components SEZ Unit", 2), B("Marina Engineering Pte Ltd", 2, 2.0), B("Southern Cross Machinery Pty Ltd", 1, 2.0), B("Thames Engineering Supplies Ltd", 2, 2.0)),
        stop_buying_after=date(2026, 8, 24), note="demand dips in the rains (Jun to Aug) and peaks in March and October; no new orders since late August, so stock runs low"),
    Scenario("MEC-0025", "Deep Groove Ball Bearing 6305", "MEC", "Deep Groove Ball Bearing", 1.9 * 1.1, "Nos", 0.15, "step", 900, 110, 50, (
        S("Rajkot Bearings & Gears Pvt Ltd", 1.00, 5, 3, 0.08), S("Bharat Precision Fasteners Pvt Ltd", 1.04, 3, 2, 0.04),
        S("Wuxi Precision Bearings Co Ltd", 0.82, 10, 2, 0.20, (4, 9), from_day=date(2026, 2, 1)), S("Osaka Seiko Bearings K.K.", 0.93, 9, 2, 0.05),
        S("Rheinland Präzision GmbH", 0.97, 12, 1, 0.10, until_day=date(2026, 5, 31))), (
        B("Nashik Auto Components Pvt Ltd", 4), B("Rajkot Auto Parts Pvt Ltd", 3), B("Coimbatore Motors & Pumps Ltd", 3), B("Aurangabad Industrial Supplies", 2),
        B("Mysuru Machine Tools Ltd", 2), B("Bayerische Maschinenhandel GmbH", 1, 2.0),
        B("Hosur Auto Assemblies Pvt Ltd", 5, from_day=JUN1), B("Bengaluru Aerospace Parts Pvt Ltd", 4, from_day=JUN1)),
        note="demand jumps by about 2.5 times on 1 June, when two new OEM customers start buying; one vendor was dropped in May and a cheaper one added in February"),
    Scenario("MEC-0026", "Gate Valve Cast Iron 8 inch", "MEC", "Gate Valve Cast Iron", 5.6, "Nos", 4.5, "spiky", 25, 5, 2, (
        S("Hyderabad Industrial Pumps Pvt Ltd", 1.00, 8, 3, 0.20, (3, 8)), S("Bharat Precision Fasteners Pvt Ltd", 1.03, 6, 2, 0.05),
        S("Ohio Valve & Control Inc", 0.83, 12, 1.5, 0.10), S("Torino Meccanica S.p.A.", 0.90, 11, 2, 0.10), S("Shanghai Meridian Pumps & Valves Co Ltd", 0.79, 10, 2, 0.25, (4, 10))), (
        B("Mumbai Infra Projects Ltd", 4), B("Capital Construction Co", 4), B("Gujarat Pumps & Engineering Pvt Ltd", 2), B("Kandla SEZ Fabricators Pvt Ltd", 1),
        B("Riyadh Heavy Equipment Co", 2, 2.0), B("Gulf Contracting FZE", 2, 2.0), B("Abu Dhabi Industrial Supplies LLC", 1, 2.0), B("Johannesburg Mining Equipment Pty Ltd", 1, 2.0)),
        spike_days=(date(2026, 1, 12), date(2026, 2, 23), date(2026, 4, 6), date(2026, 5, 18), date(2026, 7, 6), date(2026, 8, 17), date(2026, 9, 28)),
        note="a few units a month, then seven big project orders of 20 to 40 valves"),
    Scenario("CHM-0025", "Hydraulic Oil ISO 150", "CHM", "Hydraulic Oil", 1.15, "Litre", 0.88, "steady", 4000, 400, 200, (
        S("Kolkata Chemicals & Coatings Ltd", 1.00, 6, 3, 0.10), S("Hamburg Chemie Handels GmbH", 0.91, 10, 2, 0.08), S("Houston Industrial Supply LLC", 0.85, 10, 1, 0.10),
        S("Yokohama Fine Chemicals K.K.", 0.92, 9, 2, 0.05), S("Lion City Industrial Trading Pte Ltd", 0.88, 8, 2, 0.12)), (
        B("Sahyadri Engineering Works Pvt Ltd", 3), B("Aurangabad Industrial Supplies", 3), B("Konkan Retail Hardware", 2), B("Coimbatore Motors & Pumps Ltd", 3),
        B("Hosur Auto Assemblies Pvt Ltd", 2), B("Jaipur Hardware Distributors", 2), B("Kanpur Leather Machinery Pvt Ltd", 1), B("Al Noor Trading LLC", 1, 2.0)),
        note="flat demand with ordinary noise: the easy case"),
    Scenario("CHM-0026", "Industrial Epoxy Paint Yellow", "CHM", "Industrial Epoxy Paint", 1.04, "Litre", 1.3, "decline", 1200, 120, 100, (
        S("Kolkata Chemicals & Coatings Ltd", 1.00, 5, 3, 0.10), S("Hamburg Chemie Handels GmbH", 0.92, 10, 2, 0.08),
        S("Yokohama Fine Chemicals K.K.", 0.94, 9, 2, 0.05), S("Houston Industrial Supply LLC", 0.86, 12, 1, 0.20, (3, 9))), (
        B("Konkan Retail Hardware", 3), B("Jaipur Hardware Distributors", 3), B("Aurangabad Industrial Supplies", 2), B("Howrah Foundry & Fabrication Ltd", 2),
        B("Surat Textile Machinery Pvt Ltd", 2), B("Sharjah Hardware Trading LLC", 1, 2.0), B("Nairobi Agro Engineering Ltd", 1, 2.0)),
        stop_buying_after=date(2026, 6, 30), note="a colour being phased out: demand falls to a third; buying stopped in June, so by October stock is down to about ten days with a few orders waiting"),
    Scenario("ELE-0025", "Industrial Relay 24V 50A", "ELE", "Industrial Relay 24V", 2.4 * 1.3, "Nos", 0.12, "monthend", 450, 45, 25, (
        S("Chennai Electro Components Pvt Ltd", 1.00, 5, 3, 0.10), S("Shenzhen Brightway Electronics Co Ltd", 0.80, 9, 2, 0.20, (3, 9)),
        S("Hanoi Electro Parts JSC", 0.84, 9, 2, 0.08), S("Stuttgart Antriebstechnik GmbH", 0.95, 10, 2, 0.05), S("Silicon Valley Components Inc", 0.88, 12, 1, 0.10)), (
        B("Nashik Auto Components Pvt Ltd", 4), B("Hosur Auto Assemblies Pvt Ltd", 4), B("Bengaluru Aerospace Parts Pvt Ltd", 3), B("Mahindra World City Components SEZ Unit", 2),
        B("NCR Power Systems Pvt Ltd", 3), B("Coimbatore Motors & Pumps Ltd", 2), B("Atlas Industrial Distributors Inc", 1, 2.0), B("Jurong Industrial Supplies Pte Ltd", 1, 2.0)),
        note="customers order mostly in the last week of the month"),
    Scenario("ELE-0026", "Miniature Circuit Breaker 100A", "ELE", "Miniature Circuit Breaker", 2.4 * 1.4, "Nos", 0.15, "intermittent", 50, 15, 10, (
        S("Chennai Electro Components Pvt Ltd", 1.00, 5, 3, 0.10), S("Shenzhen Brightway Electronics Co Ltd", 0.81, 9, 2, 0.20, (3, 8)),
        S("Hanoi Electro Parts JSC", 0.85, 9, 2, 0.10), S("Incheon Display & Sensors Co Ltd", 0.90, 10, 1, 0.05)), (
        B("NCR Power Systems Pvt Ltd", 3), B("Capital Construction Co", 3), B("Mumbai Infra Projects Ltd", 3), B("Hyderabad Pharma Equipment Pvt Ltd", 2),
        B("Sunbelt Equipment Supply Inc", 1, 1.5), B("Abu Dhabi Industrial Supplies LLC", 1, 1.5), B("Mombasa Port Supplies Ltd", 1, 1.5)),
        note="rare, lumpy orders: two or three a month, none on most days"),
    Scenario("FAS-0025", "Hex Bolt SS304 M16x90", "FAS", "Hex Bolt SS304", 3.6 * 1.4, "Nos", 0.04, "plateau", 60000, 5000, 1000, (
        S("Bharat Precision Fasteners Pvt Ltd", 1.02, 3, 3, 0.05), S("Ludhiana Hardware Industries Pvt Ltd", 0.97, 8, 2, 0.15, (3, 7)),
        S("Mumbai Metal Traders", 1.06, 2, 2, 0.04), S("Ningbo Hengda Fasteners Co Ltd", 0.80, 9, 2, 0.18, (3, 9)),
        S("Saigon Fastener Industries Co Ltd", 0.83, 9, 2, 0.08), S("Istanbul Metal Sanayi A.S.", 0.86, 11, 1, 0.12)), (
        B("Konkan Retail Hardware", 2), B("Aurangabad Industrial Supplies", 3), B("Jaipur Hardware Distributors", 3), B("Sahyadri Engineering Works Pvt Ltd", 3),
        B("Nashik Auto Components Pvt Ltd", 2), B("Pacific Fasteners LLC", 2, 2.0), B("Sydney Industrial Fasteners Pty Ltd", 2, 2.0), B("Lagos Industrial Supplies Ltd", 1, 2.0)),
        stop_buying_after=date(2026, 8, 3), note="demand grows until March and then stays level; no new orders since early August, so it is at or near stock-out"),
    Scenario("PKG-0025", "Stretch Film 500mm 30 micron", "PKG", "Stretch Film 500mm", 1.55, "Roll", 2.2, "festive", 700, 70, 20, (
        S("Noida Packaging Solutions Pvt Ltd", 1.00, 6, 3, 0.12), S("Guangzhou Pearl Packaging Co Ltd", 0.81, 10, 2, 0.20, (3, 8)),
        S("Danang Packaging JSC", 0.86, 9, 2, 0.08), S("Bangkok Polymer Packaging Co Ltd", 0.88, 8, 2, 0.06), S("Gulf Packaging Industries LLC", 0.93, 9, 2, 0.10)), (
        B("Konkan Retail Hardware", 2), B("Aurangabad Industrial Supplies", 3), B("Jaipur Hardware Distributors", 3), B("Nashik Auto Components Pvt Ltd", 2),
        B("Hosur Auto Assemblies Pvt Ltd", 2), B("Surat Textile Machinery Pvt Ltd", 2), B("Jurong Industrial Supplies Pte Ltd", 1, 2.0), B("Rotterdam Maschinen Handel B.V.", 1, 2.0)),
        note="festival-season packing: demand peaks in October and November and in March, and is low in the rains"),
)

_VENDORS = {v.name: v for v in world.VENDORS}
_CUSTOMERS = {c.name: c for c in world.CUSTOMERS}


# ------------------------------------------------------------------ the materials as catalogue items (they are NOT part of catalog.build_items(): the year plan stays as it was)
def _product(group: str, name: str) -> catalog.Product:
    return next(p for p in catalog.PRODUCTS[group] if p.name == name)


def items() -> list[catalog.Item]:
    fx, base_day = world.fx_series(), world.fx_dates()[0]
    out: list[catalog.Item] = []
    for sc in SCENARIOS:
        prod = _product(sc.group, sc.product)
        gname, margin = catalog.GROUPS[sc.group]
        base = round(prod.price * sc.price_factor, 2 if prod.price < 100 else 0)
        sources: list[tuple[str, float, int]] = []
        for s in sc.sources:
            v = _VENDORS[s.vendor]
            inr = base * s.rel_price
            ccy = world.vendor_currency(v)
            price = round(inr, 2) if ccy == "INR" else round(inr / world.fx_on(fx, ccy, base_day), 3 if inr < 100 else 2)
            sources.append((s.vendor, price, s.lead()))
        out.append(catalog.Item(code=sc.code, name=sc.name, group_code=sc.group, group=gname, uom=sc.uom, kg=sc.kg, hs=prod.hs, hs_desc=prod.hs_desc, gst=prod.gst, bcd_mfn=prod.bcd_mfn,
                                bcd_fta=prod.bcd_fta, add_cn=prod.add_cn, base_price=base, margin=margin, suppliers=sources, moq=sc.moq,
                                country_of_origin=_VENDORS[sc.sources[0].vendor].country))
    return out


# ------------------------------------------------------------------ demand
def shape(sc: Scenario, day: date) -> float:
    """Demand on `day` relative to an average month of this material."""
    t = (day - START).days / 30.4
    p = sc.pattern
    if p == "growth":
        return 0.55 + 0.1 * t
    if p == "decline":
        return max(0.3, 1.7 - 0.115 * t)
    if p == "plateau":
        return min(1.4, 0.6 + 0.2 * t)
    if p == "step":
        return 0.6 if day < JUN1 else 1.7
    if p == "monsoon":
        return SEASON_MONSOON[day.month]
    if p == "festive":
        return SEASON_FESTIVE[day.month]
    if p == "monthend":
        return 2.6 if day.day >= 24 else 0.45 if day.day <= 12 else 0.9
    if p == "spiky":
        return 0.35
    return 1.0


def _buyer(rng: random.Random, sc: Scenario, day: date) -> Buyer:
    pool = [b for b in sc.buyers if b.from_day is None or day >= b.from_day]
    return rng.choices(pool, [b.weight for b in pool])[0]


def _poisson(rng: random.Random, lam: float) -> int:
    return sim._poisson(rng, lam)


# ------------------------------------------------------------------ the plan
def build_plan(seed: str = SEED, start: date = START, end: date = END, only: Optional[set[str]] = None) -> sim.Plan:
    fx = world.fx_series()
    cat = {i.code: i for i in items()}
    events: list[sim.Event] = []
    stats: dict[str, float] = defaultdict(float)
    per_item: dict[str, dict] = {}
    for idx, sc in enumerate(SCENARIOS, start=1):
        if only and sc.code not in only:
            continue
        evs, st = _plan_one(idx, sc, cat[sc.code], seed, fx, start, end)
        events += evs
        per_item[sc.code] = st
        for k, v in st.items():
            if isinstance(v, (int, float)):
                stats[k] += v
    events.sort(key=lambda e: e.sort_key)
    return sim.Plan(events, {**stats, "per_item": per_item})


def _plan_one(idx: int, sc: Scenario, it: catalog.Item, seed: str, fx: dict, start: date, end: date) -> tuple[list[sim.Event], dict]:
    rng = random.Random(f"{seed}-{sc.code}")
    events: list[sim.Event] = []
    seq: dict[str, int] = defaultdict(int)
    onhand = on_order = backlog = 0.0
    arrivals: dict[date, list[float]] = defaultdict(list)
    deliveries: dict[date, list[sim.Event]] = defaultdict(list)
    sold_by_day: dict[date, float] = defaultdict(float)             # units ordered by customers, for the forecast
    st: dict = defaultdict(float)
    st["orders_by_vendor"] = defaultdict(int)
    st["units_by_vendor"] = defaultdict(float)
    st["sold_by_month"] = defaultdict(float)
    vendors = _VENDORS

    def key(prefix: str, day: date, width: int = 3) -> str:
        seq[prefix] += 1
        return f"{prefix}-S{idx:02d}-{day:%Y%m}-{seq[prefix]:0{width}d}"

    def add(kind: str, day: date, k: str, data: dict, parent: Optional[str] = None) -> sim.Event:
        e = sim.Event(day, kind, k, data, parent)
        if day <= end:
            events.append(e)
        return e

    def price_on(s: Source, day: date) -> float:
        v = vendors[s.vendor]
        ccy = world.vendor_currency(v)
        base = next(p for n, p, _l in it.suppliers if n == s.vendor)
        months = (day - world.fx_dates()[0]).days / 30.4
        p = base * (1 + s.drift) ** months * rng.uniform(0.985, 1.015)
        return round(p, 3 if p < 100 else 2)

    def forecast(day: date) -> float:
        """Units per day: what customers ordered over the last 28 days, never below half of what the pattern says."""
        recent = sum(q for d, q in sold_by_day.items() if day - timedelta(days=28) <= d < day) / 28.0
        model = sc.monthly * shape(sc, day) / 30.4
        return max(recent, 0.5 * model, 1e-6)

    def place_order(day: date, s: Source, qty: float) -> None:
        nonlocal on_order
        v = vendors[s.vendor]
        ccy = world.vendor_currency(v)
        rate = price_on(s, day)
        qty = sim._cap_value(it, qty, rate, ccy, fx, day)
        qty = max(sc.moq, math.ceil(qty / sc.moq) * sc.moq)
        k = key("PO", day)
        promised = day + timedelta(days=s.lead() + (rng.randint(0, 4) if v.country != "India" else rng.randint(0, 2)))
        late = rng.randint(*s.late_days) if rng.random() < s.late_prob else 0
        arrive = promised + timedelta(days=late)
        lines = [(it.code, qty, rate)]
        add(sim.PO, day, k, {"supplier": s.vendor, "currency": ccy, "lines": lines, "arrive": promised, "country": v.country})
        on_order += qty
        st["orders"] += 1
        st["orders_by_vendor"][s.vendor] += 1
        st["units_by_vendor"][s.vendor] += qty
        if late:
            st["late_deliveries"] += 1
        if arrive > end:
            return
        pr = add(sim.PR, arrive, k.replace("PO", "PR", 1), {"supplier": s.vendor, "currency": ccy, "lines": lines, "country": v.country}, parent=k)
        arrivals[arrive].append(qty)
        if v.country != "India":
            add(sim.CUSTOMS, arrive, k.replace("PO", "CUS", 1), {"supplier": s.vendor, "country": v.country, "lines": lines}, parent=pr.key)
        inv_day = arrive + timedelta(days=rng.randint(0, 5))
        pi = add(sim.PI, inv_day, k.replace("PO", "PI", 1), {"supplier": s.vendor, "currency": ccy, "lines": lines}, parent=pr.key)
        pay_day = inv_day + timedelta(days=max(1, sim.payment_days(finance.vendor_terms(v)) + rng.randint(-5, 10)))
        add(sim.PAY_SUP, pay_day, k.replace("PO", "PS", 1), {"supplier": s.vendor, "currency": ccy}, parent=pi.key)

    def new_sales_order(day: date, buyer: Buyer, qty: float, project: bool = False) -> None:
        nonlocal backlog
        cust = _CUSTOMERS[buyer.customer]
        k = key("SO", day, 4)
        lag = rng.randint(2, 6) if cust.country == "India" else rng.randint(5, 14)
        lines = [(it.code, qty)]
        ev = add(sim.SO, day, k, {"customer": cust.name, "lines": lines, "cust_obj": cust, "open": list(lines), "attempt": 0, "dn_no": 1, "deliver_on": day + timedelta(days=lag)})
        backlog += qty
        if not project:                                            # a one-off project order says nothing about what to stock
            sold_by_day[day] += qty
        st["sales_orders"] += 1
        st["ordered_units"] += qty
        deliveries[day + timedelta(days=lag)].append(ev)

    def ship_due(day: date) -> None:
        nonlocal onhand, backlog
        for so_ev in deliveries.pop(day, []):
            lines = []
            for item, qty in so_ev.data["open"]:
                ship = min(qty, math.floor(onhand))
                if ship >= 1:
                    lines.append((item, ship))
                    onhand -= ship
                    backlog -= ship
            if not lines:
                if so_ev.data["attempt"] < 5:
                    so_ev.data["attempt"] += 1
                    deliveries[day + timedelta(days=7)].append(so_ev)
                else:
                    st["unfilled_orders"] += 1
                continue
            shipped = dict(lines)
            so_ev.data["open"] = [(i, q - shipped.get(i, 0)) for i, q in so_ev.data["open"] if q - shipped.get(i, 0) > 0]
            part = f"{so_ev.key}-D{so_ev.data['dn_no']}"
            so_ev.data["dn_no"] += 1
            dn = add(sim.DN, day, part, {"lines": lines, "customer": so_ev.data["customer"]}, parent=so_ev.key)
            st["shipped_units"] += lines[0][1]
            st["sold_by_month"][f"{day:%Y-%m}"] += lines[0][1]
            if dn.day > end:
                continue
            lag = rng.choices([0, 1, 2], [0.6, 0.3, 0.1])[0]
            si = add(sim.SI, day + timedelta(days=lag), part.replace("-D", "-I"), {"customer": so_ev.data["customer"], "lines": lines}, parent=part)
            cust = so_ev.data["cust_obj"]
            terms_days = sim.payment_days(finance.customer_terms(cust))
            delay = terms_days + rng.randint(-3, 14) + (rng.randint(25, 60) if rng.random() < 0.08 else 0)
            add(sim.PAY_CUST, si.day + timedelta(days=max(1, delay)), part.replace("-D", "-R"), {"customer": cust.name, "lines": lines}, parent=si.key)
            if so_ev.data["open"]:
                deliveries[day + timedelta(days=rng.randint(5, 12))].append(so_ev)

    def weighted_source(day: date, pool: list[Source]) -> Source:
        return rng.choices(pool, [s.share for s in pool])[0]

    def review(day: date) -> None:
        if sc.stop_buying_after and day > sc.stop_buying_after:
            return
        active = [s for s in sc.sources if s.active(day)]
        fc = forecast(day)
        position = onhand + on_order - backlog
        phys_cover = (onhand - backlog) / fc
        untried = [s for s in active if st["orders_by_vendor"][s.vendor] < MIN_ORDERS_PER_VENDOR]
        if untried and rng.random() < 0.3:                     # a trial order of about three weeks of demand: every vendor gets a few orders, so each has a history to compare
            place_order(day, rng.choice(untried), max(sc.moq, fc * 21))
            return
        pick = weighted_source(day, active)                    # the vendor the buyer prefers this time
        if position >= fc * (pick.lead() + SAFETY_DAYS):
            return
        fastest = min(active, key=lambda s: s.lead())
        qty = max(sc.moq, fc * (pick.lead() + COVER_DAYS) - position)
        gap = pick.lead() + 3 - phys_cover                     # days the stock will be gone before this vendor delivers
        if rng.random() < 0.25 and len(active) > 1:            # sometimes the buyer splits an order between two vendors
            other = weighted_source(day, [s for s in active if s is not pick])
            place_order(day, pick, qty * 0.6)
            place_order(day, other, qty * 0.4)
            gap = min(gap, other.lead() + 3 - phys_cover) if other.lead() > pick.lead() else gap
        else:
            place_order(day, pick, qty)
        if gap > 0 and pick is not fastest:                    # a small order from the quickest vendor bridges the gap
            place_order(day, fastest, fc * (gap + 7))

    # opening stock: two quick domestic orders on the first day, which arrive before the first customer order
    quick = sorted((s for s in sc.sources if s.active(start) and vendors[s.vendor].country == "India"), key=lambda s: s.lead())[:2]
    for s in quick:
        place_order(start, s, sc.monthly * shape(sc, start) * 0.8)

    spikes = {d: rng.uniform(3.5, 7.0) for d in sc.spike_days}
    carry = rng.random()
    day = start
    first_review = start + timedelta(days=(7 - start.weekday()) % 7 + 7)
    while day <= end:
        for q in arrivals.pop(day, []):
            onhand += q
            on_order -= q
        ship_due(day)
        if day.weekday() in sim.WORKDAYS and day >= SALES_START:
            lam = sc.monthly * shape(sc, day) / WORKDAYS_PER_MONTH / sc.typical
            carry += lam                                                               # steadier than a Poisson count: each month has about the number of orders its pattern says
            n_orders, carry = int(carry), carry - int(carry)
            for _ in range(n_orders):
                buyer = _buyer(rng, sc, day)
                sigma = 0.6 if sc.pattern == "intermittent" else 0.25
                qty = sim._round_qty(it, sc.typical * rng.lognormvariate(0, sigma) * buyer.mult)
                new_sales_order(day, buyer, qty)
            if day in spikes:                                       # a project order: several times the usual lot
                buyer = _buyer(rng, sc, day)
                new_sales_order(day, buyer, sim._round_qty(it, sc.typical * spikes[day] * buyer.mult), project=True)
        if day >= first_review and day.weekday() == 0:
            review(day)
        day += timedelta(days=1)

    st["end_onhand"] = onhand
    st["end_backlog"] = backlog
    st["end_on_order"] = on_order
    st["days_of_stock_at_end"] = (onhand - backlog) / max(sc.monthly * shape(sc, end) / 30.4, 1e-6)
    return events, st
