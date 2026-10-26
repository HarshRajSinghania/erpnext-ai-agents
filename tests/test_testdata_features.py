"""The feature scenarios (tools/testdata/features.py) as a plan: no ERPNext is read or written. These tests keep the plan honest, so that what it says about each material
(its stock today, what it should show) stays true however the numbers are changed."""
from datetime import date, timedelta

import pytest

from tools.testdata import features, finance
from tools.testdata.steps import Context
from tools.testdata.writer import Writer

TODAY = date(2026, 10, 26)
SLOW_ON_PURPOSE = {"TST-DEAD-1", "TST-DEAD-2", "TST-SLOW-1", "TST-SLOW-2"}


@pytest.fixture(scope="module")
def plan():
    return features.Features(Context(Writer(None, "http://erp.invalid", None, dry_run=True)), TODAY)


def rows(plan):
    return {r["code"]: r for r in plan.summary()}


def test_every_material_can_be_built_and_stock_never_goes_negative(plan):
    r = rows(plan)                                                           # raises PlanError for a material whose stock would go negative at any point
    assert len(r) == len(features.mats()) == 28
    assert all(x["lowest"] >= 0 and x["opening"] >= 0 for x in r.values())


def test_a_material_that_names_its_stock_ends_with_exactly_that(plan):
    r = rows(plan)
    for m in features.mats():
        if m.stock is not None:
            assert r[m.code]["stock_now"] == pytest.approx(m.stock), m.code


def test_only_the_slow_and_dead_materials_hold_more_than_the_slow_limit(plan):
    """Nothing else may end up on the slow-stock list by accident: 180 days of cover is the app's limit."""
    r = rows(plan)
    for m in features.mats():
        if m.use and m.code not in SLOW_ON_PURPOSE:
            assert r[m.code]["stock_now"] / m.use < 180, f"{m.code} would look like slow stock"
    assert r["TST-SLOW-1"]["stock_now"] / features.mats()[[m.code for m in features.mats()].index("TST-SLOW-1")].use >= 180


def test_keys_are_unique_and_every_document_is_dated_in_the_past_or_today(plan):
    keys = [e.label for evs in plan.evs.values() for e in evs]
    assert keys                                                              # labels are per material; the real keys add the material, checked next
    seen = set()
    for code, evs in plan.evs.items():
        for e in evs:
            assert e.day <= TODAY, (code, e.label)
        for e in evs:
            if e.kind in ("Delivery Note",):
                continue
            k = (code, e.label.split(":")[0])
            assert k not in seen, k
            seen.add(k)


def test_the_late_orders_are_late_and_the_two_that_are_not_are_not(plan):
    pos = {code: [e for e in plan.evs[code] if e.kind == "Purchase Order"] for code in ("TST-LATE-1", "TST-LATE-2", "TST-LATE-3")}
    due = lambda e: date.fromisoformat(e.label.rsplit("due ", 1)[1])        # noqa: E731
    assert (TODAY - due(pos["TST-LATE-1"][0])).days == 6
    assert (TODAY - due(pos["TST-LATE-3"][0])).days == 3
    by = {e.label.split(":")[0]: (TODAY - due(e)).days for e in pos["TST-LATE-2"]}
    assert by["PO1"] == 4 and by["PO2"] == 1 and by["PO3"] < 0                 # late, inside the one grace day, not yet due


def test_the_goods_receipt_scenarios_fall_inside_the_check_window(plan):
    for code in ("TST-GRN-1", "TST-GRN-2", "TST-GRN-3", "TST-GRN-4", "TST-GRN-5", "TST-GRN-6"):
        for e in plan.evs[code]:
            if e.kind in ("Purchase Receipt", "Purchase Invoice"):
                assert 0 <= (TODAY - e.day).days <= 14, (code, e.label, e.day)    # followup.receipt_lookback_days


def test_the_seasonal_item_has_over_a_year_of_history_and_last_years_jump_in_the_right_weeks(plan):
    days = [e for e in plan.evs["TST-SEAS-1"] if e.kind == "Delivery Note"]
    first = min(e.day for e in days)
    assert (TODAY - first).days >= 455 - 5                                    # the seasonal comparison needs 365 + the 90-day base window
    jump = [e for e in days if TODAY - timedelta(days=365) <= e.day < TODAY - timedelta(days=353)]
    normal = [e for e in days if TODAY - timedelta(days=330) <= e.day < TODAY - timedelta(days=200)]
    rate = lambda es, span: sum(-e.delta for e in es) / span                   # noqa: E731
    assert rate(jump, 12) / rate(normal, 130) == pytest.approx(1.8, rel=0.05)


def test_the_vendors_can_be_reached_the_way_each_scenario_needs(plan):
    c = features.CONTACTS
    assert c["gamma"] is None
    assert c["alpha"][1] and c["alpha"][2] and c["beta"][1] and not c["beta"][2] and not c["delta"][1] and c["delta"][2]
    for contact in c.values():
        if contact:
            assert not contact[1] or contact[1].endswith("@example.com")        # an address that can never deliver
            assert not contact[2] or contact[2].startswith("+91 00000")         # a number that can never be reached


def test_every_material_has_the_customs_and_tax_data_the_existing_loader_needs():
    profiles = finance.item_tax_profiles()
    for m in features.mats():
        it = features.make_item(m)
        assert it.profile in profiles, m.code
        assert it.suppliers and all(name in {v.name for v in features.VENDORS.values()} for name, _p, _l in it.suppliers)


def test_the_substitute_scenario_has_what_it_names(plan):
    codes = {m.code: m for m in features.mats()}
    assert codes["TST-SUB-A1"].alt and codes["TST-SUB-C1"].alt and not codes["TST-SUB-B1"].alt        # B1 has no alternative listed
    r = rows(plan)
    assert r["TST-SUB-A1"]["stock_now"] / 20 < 7 and r["TST-SUB-A2"]["stock_now"] > 0 and r["TST-SUB-A3"]["stock_now"] == 0
    assert r["TST-SUB-C1"]["stock_now"] / 10 > 30                              # healthy: nothing to suggest


def test_the_count_scenarios_say_what_the_system_held(plan):
    r = rows(plan)
    assert r["TST-CNT-2"]["stock_now"] == 20 and r["TST-CNT-3"]["stock_now"] == 70
    assert sum(e.delta for e in plan.evs["TST-CNT-2"] if e.kind == "Stock Reconciliation") == -220     # 240 -> 20: twelve times fewer
    assert sum(e.delta for e in plan.evs["TST-CNT-3"] if e.kind == "Stock Reconciliation") == -20
    drafts = [e for e in plan.evs["TST-CNT-1"] if e.draft]
    assert len(drafts) == 1 and drafts[0].delta == 0                           # a receipt that is not posted moves no stock


def test_every_sale_and_sales_order_of_the_scenarios_goes_to_a_test_customer(plan):
    """An earlier run spread the delivery notes over existing customers, pushed one over its credit limit and used up part of everyone's: only the test customers may be used."""
    mine = {c.name for c in features.TEST_CUSTOMERS}
    assert len(mine) >= 3 and all(c.credit_limit >= 100_000_000 for c in features.TEST_CUSTOMERS)
    assert {plan.customer(india=True, nth=n).name for n in range(40)} == mine
    from tools.testdata import world
    assert not mine & {c.name for c in world.CUSTOMERS}                          # and none of them is one of the existing customers
