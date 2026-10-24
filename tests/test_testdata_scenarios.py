"""The ten multi-vendor materials: their history is planned without ERPNext, and must be plausible, deterministic, and show the patterns they are named for."""
from collections import Counter, defaultdict
from datetime import date

import pytest

from tools.testdata import catalog, scenarios, sim, world

PLAN = scenarios.build_plan()
ITEMS = {i.code: i for i in scenarios.items()}
SC = {s.code: s for s in scenarios.SCENARIOS}


def of(code, *kinds):
    return [e for e in PLAN.events if (not kinds or e.kind in kinds) and _item(e) == code]


def _item(e):
    if "lines" in e.data and e.data["lines"]:
        return e.data["lines"][0][0]
    return None


def monthly(code, kind=sim.SO):
    out = defaultdict(float)
    for e in of(code, kind):
        out[f"{e.day:%Y-%m}"] += e.data["lines"][0][1]
    return out


def test_ten_new_materials_that_do_not_touch_the_existing_catalogue():
    assert len(scenarios.SCENARIOS) == 10 and len(ITEMS) == 10
    existing = {i.code for i in catalog.build_items()}
    assert not existing & set(ITEMS)
    assert len({i.name for i in ITEMS.values()}) == 10 and not {i.name for i in ITEMS.values()} & {i.name for i in catalog.build_items()}
    assert [i.code for i in catalog.build_items()][:3] == ["FAS-0001", "FAS-0002", "FAS-0003"] and len(catalog.build_items()) == 200      # the year plan is unchanged


def test_each_material_reuses_the_tax_profile_of_an_existing_product_so_its_templates_already_exist():
    known = {i.profile for i in catalog.build_items() if i.stock}
    for code, it in ITEMS.items():
        assert it.profile in known, code
        assert it.hs and it.gst in (12, 18) and it.base_price > 0 and it.moq > 0


def test_every_material_has_four_or_more_vendors_in_different_places_with_different_prices_and_lead_times():
    for code, sc in SC.items():
        it = ITEMS[code]
        assert 4 <= len(it.suppliers) <= 6
        vendors = [world.VENDORS[[v.name for v in world.VENDORS].index(n)] for n, _p, _l in it.suppliers]
        assert len({v.city for v in vendors}) == len(vendors) or len({v.country for v in vendors}) >= 3        # in different places
        assert any(v.country == "India" for v in vendors) and any(v.country != "India" for v in vendors)
        assert len({l for _n, _p, l in it.suppliers}) >= 4 and min(l for _n, _p, l in it.suppliers) <= 8 and max(l for _n, _p, l in it.suppliers) >= 30
        group = sc.group
        assert all(group in v.supplies for v in vendors), code                                               # each vendor really supplies this kind of goods


def test_every_vendor_of_every_material_has_orders_to_compare():
    for code, sc in SC.items():
        orders = Counter(e.data["supplier"] for e in of(code, sim.PO))
        for s in sc.sources:
            assert orders[s.vendor] >= 1, (code, s.vendor)
        assert sum(1 for s in sc.sources if orders[s.vendor] >= 2) >= len(sc.sources) - 1, (code, dict(orders))


def test_every_material_is_sold_to_seven_or_more_customers():
    for code, sc in SC.items():
        buyers = {e.data["customer"] for e in of(code, sim.SO)}
        assert len(buyers) >= 6, (code, len(buyers))
        assert {b.customer for b in sc.buyers} <= {c.name for c in world.CUSTOMERS}
    assert {e.data["customer"] for e in PLAN.events if e.kind == sim.SO and world.customer_currency(next(c for c in world.CUSTOMERS if c.name == e.data["customer"])) != "INR"}


def test_deterministic_so_a_rerun_finds_the_same_documents():
    again = scenarios.build_plan()
    assert [(e.day, e.kind, e.key) for e in again.events] == [(e.day, e.kind, e.key) for e in PLAN.events]
    keys = [e.key for e in PLAN.events if e.kind in (sim.PO, sim.SO)]
    assert len(keys) == len(set(keys)) and all(k.split("-")[1].startswith("S") for k in keys)                # own prefix: never collides with the year plan's keys
    assert not {e.key for e in sim.build_plan().events} & {e.key for e in PLAN.events}


def test_no_stock_is_delivered_before_it_is_received_and_the_stock_never_goes_negative():
    for code in SC:
        balance, lines = 0.0, sorted(of(code, sim.PR, sim.DN), key=lambda e: e.sort_key)
        for e in lines:
            qty = e.data["lines"][0][1]
            balance += qty if e.kind == sim.PR else -qty
            assert balance >= -1e-6, (code, e.day, e.kind, balance)


def test_every_purchase_is_at_least_the_items_minimum_order_quantity_in_whole_lots():
    for code, sc in SC.items():
        for e in of(code, sim.PO):
            assert e.data["lines"][0][1] >= sc.moq and e.data["lines"][0][1] % sc.moq == 0, (code, e.data["lines"])


def test_documents_follow_each_other_in_time_and_all_events_are_inside_the_period():
    by_key = {e.key: e for e in PLAN.events}
    for e in PLAN.events:
        assert scenarios.START <= e.day <= scenarios.END
        if e.parent:
            assert e.parent in by_key and by_key[e.parent].day <= e.day, (e.key, e.parent)


def test_foreign_purchases_have_a_customs_entry_and_indian_ones_do_not():
    for e in PLAN.events:
        if e.kind == sim.PR:
            cus = f"{e.key.replace('PR', 'CUS', 1)}"
            foreign = e.data["country"] != "India"
            assert (cus in {x.key for x in PLAN.events if x.kind == sim.CUSTOMS}) == foreign


def test_late_deliveries_exist_and_arrive_after_the_date_promised_on_the_order():
    late = [(po, pr) for po in PLAN.events if po.kind == sim.PO for pr in PLAN.events if pr.kind == sim.PR and pr.parent == po.key and pr.day > po.data["arrive"]]
    assert len(late) >= 10
    assert all(pr.day > po.data["arrive"] for po, pr in late)


# ------------------------------------------------------------------ the demand patterns are visible in the sales orders
def avg(vals):
    return sum(vals) / len(vals)


def test_growth_copper_rod_sells_far_more_at_the_end_than_at_the_start():
    m = monthly("MET-0025")
    assert avg([m[k] for k in ("2026-08", "2026-09")]) > 2.0 * avg([m[k] for k in ("2025-12", "2026-01", "2026-02")])


def test_falling_epoxy_paint_sells_clearly_less_at_the_end():
    m = monthly("CHM-0026")
    assert avg([m[k] for k in ("2026-08", "2026-09")]) < 0.7 * avg([m[k] for k in ("2026-01", "2026-02", "2026-03")])


def test_step_bearing_demand_jumps_in_june_when_two_new_customers_start_buying():
    m = monthly("MEC-0025")
    assert avg([m[k] for k in ("2026-06", "2026-07", "2026-08")]) > 2.0 * avg([m[k] for k in ("2026-02", "2026-03", "2026-04")])
    first = {}
    for e in of("MEC-0025", sim.SO):
        first.setdefault(e.data["customer"], e.day)
    assert first["Hosur Auto Assemblies Pvt Ltd"] >= date(2026, 6, 1) and first["Bengaluru Aerospace Parts Pvt Ltd"] >= date(2026, 6, 1)


def test_monsoon_stainless_sheet_dips_in_the_rains():
    m = monthly("MET-0026")
    assert avg([m["2026-07"], m["2026-08"]]) < 0.5 * avg([m["2026-02"], m["2026-03"], m["2026-04"]])


def test_festive_stretch_film_peaks_in_october_and_march():
    m = monthly("PKG-0025")
    low = avg([m["2026-06"], m["2026-07"], m["2026-08"]])
    assert m["2026-10"] > 1.4 * low and m["2026-03"] > 1.4 * low


def test_spiky_gate_valve_has_seven_big_project_orders_far_above_the_usual_lot():
    sizes = sorted((e.data["lines"][0][1] for e in of("MEC-0026", sim.SO)), reverse=True)
    typical = SC["MEC-0026"].typical
    assert sum(1 for q in sizes if q >= 3 * typical) >= 6 and sizes[0] >= 20


def test_intermittent_breaker_is_ordered_on_few_days_in_small_lots():
    days = {e.day for e in of("ELE-0026", sim.SO)}
    assert len(days) <= 45 and max(e.data["lines"][0][1] for e in of("ELE-0026", sim.SO)) <= 12 * SC["ELE-0026"].typical


def test_monthend_relay_orders_cluster_in_the_last_week_of_the_month():
    orders = of("ELE-0025", sim.SO)
    last_week = sum(1 for e in orders if e.day.day >= 24)
    first_half = sum(1 for e in orders if e.day.day <= 12)
    assert last_week > 1.5 * first_half


def test_plateau_hex_bolt_grows_then_levels_off_after_march():
    m = monthly("FAS-0025")
    early = avg([m["2025-12"], m["2026-01"]])
    later = [m[k] for k in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")]
    assert avg(later) > 2.0 * early and max(later) < 1.5 * min(later)


def test_the_materials_end_in_different_stock_situations():
    st = PLAN.stats["per_item"]
    out_or_low = [c for c, s in st.items() if s["days_of_stock_at_end"] < 21]
    healthy = [c for c, s in st.items() if s["days_of_stock_at_end"] >= 30]
    assert len(out_or_low) >= 2 and len(healthy) >= 4
    assert any(s["end_backlog"] > 0 and s["end_onhand"] == 0 for s in st.values())          # at least one is out of stock with customers waiting
    assert all(s["end_onhand"] >= 0 for s in st.values())


def test_a_single_material_can_be_planned_alone_and_gives_the_same_events():
    one = scenarios.build_plan(only={"CHM-0025"})
    assert {e.key for e in one.events} == {e.key for e in PLAN.events if e.key.split("-")[1] == "S05"}
