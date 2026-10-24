"""The item catalogue: size, coverage, tax profiles and supplier assignment (no ERPNext needed)."""
from collections import Counter

from tools.testdata import catalog, world


def items():
    return catalog.build_items()


def test_two_hundred_items_192_goods_in_eight_groups_and_eight_services():
    its = items()
    assert len(its) == 200 and len({i.code for i in its}) == 200 and len({i.name for i in its}) == 200
    goods, services = [i for i in its if i.stock], [i for i in its if not i.stock]
    assert len(goods) == 192 and len(services) == 8 and {i.group for i in services} == {"Services"}
    assert Counter(i.group_code for i in goods) == {g: 24 for g in catalog.GROUPS}


def test_deterministic_so_every_run_makes_the_same_catalogue():
    a, b = items(), items()
    assert [(i.code, i.name, i.base_price, i.suppliers) for i in a] == [(i.code, i.name, i.base_price, i.suppliers) for i in b]


def test_every_good_has_a_customs_code_gst_slab_weight_and_sensible_price():
    for i in items():
        if not i.stock:
            continue
        assert i.hs and len(i.hs.replace(".", "")) == 8 and i.gst in (12, 18) and i.kg > 0 and i.base_price > 0 and i.uom and i.moq > 0
        assert 0 <= i.bcd_fta <= i.bcd_mfn <= 20 and 0 <= i.add_cn <= 12


def test_duties_differ_by_what_the_goods_are():
    its = [i for i in items() if i.stock]
    assert len({(i.bcd_mfn, i.bcd_fta, i.add_cn) for i in its}) >= 8
    assert {i.add_cn for i in its if i.group_code == "MET"} >= {0, 8, 10, 12}                  # steel/aluminium attract anti-dumping duty, copper does not
    assert all(i.add_cn == 0 for i in its if i.group_code in ("ELE", "FGI", "FGC"))
    profiles = {i.profile for i in its}
    assert 30 <= len(profiles) <= 50 and all(p.startswith("HS ") for p in profiles)           # one item tax template per profile


def test_every_good_has_one_or_two_real_suppliers_that_sell_its_group():
    vendors = {v.name: v for v in world.VENDORS}
    for i in items():
        if not i.stock:
            assert not i.suppliers
            continue
        assert 1 <= len(i.suppliers) <= 2
        for name, price, lead in i.suppliers:
            v = vendors[name]
            assert v.kind == world.GOODS and i.group_code in v.supplies and price > 0 and 2 <= lead <= 60
    assert Counter(len(i.suppliers) for i in items() if i.stock)[2] > 100                     # mostly a local and an imported source


def test_imports_look_cheaper_per_unit_than_local_before_freight_and_duty():
    fx, start = world.fx_series(), world.fx_dates()[0]
    cheaper = total = 0
    for i in items():
        local = [p for n, p, _ in i.suppliers if next(v for v in world.VENDORS if v.name == n).country == "India"]
        foreign = [(n, p) for n, p, _ in i.suppliers if next(v for v in world.VENDORS if v.name == n).country != "India"]
        if local and foreign:
            total += 1
            n, p = foreign[0]
            inr = p * world.fx_on(fx, world.IMPORT_COUNTRIES[next(v for v in world.VENDORS if v.name == n).country].currency, start)
            cheaper += inr < local[0]
    assert total > 80 and cheaper / total > 0.9


def test_every_group_can_be_sourced_locally_and_from_abroad():
    for gc in catalog.GROUPS:
        vs = [v for v in world.VENDORS if v.kind == world.GOODS and gc in v.supplies]
        assert any(v.country == "India" for v in vs) and any(v.country != "India" for v in vs), gc


def test_selling_prices_carry_the_margin_and_an_export_premium():
    fx, day = world.fx_series(), world.fx_dates()[0]
    it = next(i for i in items() if i.code == "MEC-0001")
    inr = catalog.selling_price(it, "INR", fx, day)
    assert round(inr, 2) == round(it.base_price * 1.26, 2)
    usd = catalog.selling_price(it, "USD", fx, day)
    assert abs(usd * 88.0 - inr * 1.06) < 0.6
