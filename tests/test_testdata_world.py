"""The test-data definitions: sizes, coverage and internal consistency (no ERPNext needed)."""
from collections import Counter
from datetime import date

from tools.testdata import world


def test_fifty_vendors_and_fifty_customers_around_the_world():
    assert len(world.VENDORS) == 50 and len(world.CUSTOMERS) == 50
    assert len({v.name for v in world.VENDORS}) == 50 and len({c.name for c in world.CUSTOMERS}) == 50
    vend = Counter(v.country for v in world.VENDORS)
    assert vend["India"] == 17 and len(vend) == 15 and sum(1 for v in world.VENDORS if v.kind == world.SERVICE) == 10
    cust = Counter(c.country for c in world.CUSTOMERS)
    assert cust["India"] == 20 and len(cust) == 13 and sum(cust.values()) - cust["India"] == 30
    assert sum(1 for c in world.CUSTOMERS if c.sez) == 2
    assert all(v.state == "" for v in world.VENDORS if v.country != "India")                 # a state only makes sense in India
    assert all(v.state for v in world.VENDORS if v.country == "India")
    assert all(v.supplies for v in world.VENDORS if v.kind == world.GOODS) and all(v.service for v in world.VENDORS if v.kind == world.SERVICE)


def test_every_party_country_is_a_known_trade_route_and_currency():
    for v in world.VENDORS:
        assert v.country == "India" or v.country in world.IMPORT_COUNTRIES
        assert world.vendor_currency(v) in ["INR", *world.FOREIGN_CURRENCIES]
    for c in world.CUSTOMERS:
        assert c.country == "India" or c.country in world.EXPORT_COUNTRIES
        assert world.customer_currency(c) in ["INR", *world.FOREIGN_CURRENCIES]


def test_parties_attract_different_duty_regimes_by_location():
    regimes = {world.IMPORT_COUNTRIES[v.country].import_regime for v in world.VENDORS if v.country != "India"}
    assert regimes == {"mfn", "mfn_ad", "fta_asean", "fta_cepa", "fta_uae"}
    cats = {world.vendor_tax_category(v) for v in world.VENDORS}
    assert world.DOMESTIC_PURCHASE_INTRA in cats and world.DOMESTIC_PURCHASE_INTER in cats and len(cats) >= 11
    assert len({world.customer_tax_category(c) for c in world.CUSTOMERS}) >= 8
    assert world.IMPORT_COUNTRIES["United States"].freight_pct > world.IMPORT_COUNTRIES["United Arab Emirates"].freight_pct     # farther = dearer
    assert world.IMPORT_COUNTRIES["United States"].transit_days > world.IMPORT_COUNTRIES["United Arab Emirates"].transit_days


def test_intra_vs_inter_state_follows_our_home_state():
    mh = next(v for v in world.VENDORS if v.state == "Maharashtra")
    gj = next(v for v in world.VENDORS if v.state == "Gujarat")
    assert world.vendor_tax_category(mh) == world.DOMESTIC_PURCHASE_INTRA and world.vendor_tax_category(gj) == world.DOMESTIC_PURCHASE_INTER
    sez = next(c for c in world.CUSTOMERS if c.sez)
    assert world.customer_tax_category(sez) == world.SEZ_SALE


def test_exchange_rates_are_deterministic_dated_and_never_in_the_future():
    a, b = world.fx_series(), world.fx_series()
    assert a == b
    dates = world.fx_dates()
    assert dates[0] == date(2025, 10, 1) and max(dates) <= world.PERIOD_END and dates == sorted(set(dates))
    assert set(a) == set(world.FOREIGN_CURRENCIES) and all(len(s) == len(dates) for s in a.values())
    usd = list(a["USD"].values())
    assert usd[0] == 88.0 and all(80 < r < 100 for r in usd) and len(set(usd)) > 20          # moves, but stays realistic
    assert world.fx_on(a, "USD", date(2025, 11, 20)) == a["USD"][date(2025, 11, 16)] and world.fx_on(a, "INR", date(2026, 1, 1)) == 1.0


def test_the_period_is_the_last_twelve_months_up_to_yesterday():
    assert world.PERIOD_START == date(2025, 11, 1) and world.PERIOD_END == date(2026, 10, 17)
    assert (world.PERIOD_END - world.PERIOD_START).days == 350                  # 1 Nov 2025 to 17 Oct 2026
