"""The finance and tax definitions are internally consistent (no ERPNext needed)."""
from collections import Counter

from tools.testdata import catalog, finance, world

ACCOUNT_NAMES = {a[0] for a in finance.ACCOUNTS}
EXISTING_PARENTS = {"Tax Assets", "Stock Assets", "Loans and Advances (Assets)", "Bank Accounts", "Accounts Receivable", "Accounts Payable", "Duties and Taxes",
                    "Capital Account", "Direct Expenses", "Indirect Expenses", "Direct Income", "Indirect Income"}
EXISTING_ACCOUNTS = {"Cash", "Debtors", "Creditors", "Cost of Goods Sold", "Sales", "Stock In Hand"}


def test_accounts_are_unique_with_known_parents_and_consistent_types():
    names = [a[0] for a in finance.ACCOUNTS]
    assert len(names) == len(set(names)) and len(names) > 60
    created: set[str] = set()
    for name, parent, root, typ, ccy, group in finance.ACCOUNTS:
        assert parent in EXISTING_PARENTS | created, f"{name}: parent {parent} unknown or defined later"
        assert root in ("Asset", "Liability", "Equity", "Income", "Expense")
        if group:
            created.add(name)
    assert all(a[4] != "INR" for a in finance.ACCOUNTS)                                             # foreign accounts only carry a foreign currency
    assert {a[3] for a in finance.ACCOUNTS if "Creditors (" in a[0]} == {"Payable"} and {a[3] for a in finance.ACCOUNTS if "Debtors (" in a[0]} == {"Receivable"}


def test_every_party_currency_has_a_party_account():
    for v in world.VENDORS:
        ccy = world.vendor_currency(v)
        assert ccy == "INR" or f"Creditors ({ccy})" in ACCOUNT_NAMES, (v.name, ccy)
    for c in world.CUSTOMERS:
        ccy = world.customer_currency(c)
        assert ccy == "INR" or f"Debtors ({ccy})" in ACCOUNT_NAMES, (c.name, ccy)


def test_every_account_a_template_uses_is_defined():
    used = set()
    for cat in finance.IMPORT_CATEGORIES:
        used |= {r.account for r in finance.import_template(cat)[1]}
    for _n, rows in finance.domestic_purchase_templates() + finance.sales_templates():
        used |= {r.account for r in rows}
    for prof in finance.item_tax_profiles().values():
        used |= set(prof)
    for w in finance.WITHHOLDING:
        used.add(w.account)
    assert used <= ACCOUNT_NAMES | EXISTING_ACCOUNTS, used - ACCOUNT_NAMES


def test_tax_categories_cover_every_party_and_every_template():
    cats = set(finance.TAX_CATEGORIES)
    assert len(cats) == len(finance.TAX_CATEGORIES) == 23
    assert {world.vendor_tax_category(v) for v in world.VENDORS} <= cats and {world.customer_tax_category(c) for c in world.CUSTOMERS} <= cats
    templates = {n for n, _ in finance.domestic_purchase_templates()} | {finance.import_template(c)[0] for c in finance.IMPORT_CATEGORIES} | {n for n, _ in finance.sales_templates()}
    assert templates == cats                                                                        # one template per category: a Tax Rule can pick each


def test_import_templates_model_freight_insurance_duty_igst_in_the_right_order():
    for cat in finance.IMPORT_CATEGORIES:
        _, rows = finance.import_template(cat)
        kinds = [r.account for r in rows]
        assert kinds[:2] == ["Inbound Freight - Sea & Air", "Marine Insurance - Inbound"] and kinds[-1] == "Port Handling & CHA Charges"
        igst = next(i for i, r in enumerate(rows) if r.account == "Input IGST on Imports")
        assert rows[igst].category == "Total" and rows[igst].charge_type == "On Previous Row Total"
        assert all(r.category == "Valuation" for r in rows if r.account != "Input IGST on Imports")           # everything else is cost
        for n, r in enumerate(rows, start=1):                                                                  # references point at an earlier row
            assert r.row_id is None or 1 <= r.row_id < n, (cat, r)
        assert rows[igst].row_id == igst                                                                       # IGST is charged on everything before it
    china = finance.import_template("Import - China (MFN + anti-dumping)")[1]
    asean = finance.import_template("Import - ASEAN (FTA)")[1]
    assert any(r.account == "Anti-dumping Duty" for r in china) and not any(r.account == "Anti-dumping Duty" for r in asean)
    assert any(r.account == "Basic Customs Duty - FTA" for r in asean) and any(r.account == "Basic Customs Duty - MFN" for r in china)


def test_freight_and_insurance_rise_with_distance():
    rate = lambda cat, acct: next(r.rate for r in finance.import_template(cat)[1] if r.account == acct)
    assert rate("Import - USA (MFN)", "Inbound Freight - Sea & Air") > rate("Import - UAE (CEPA)", "Inbound Freight - Sea & Air")
    assert rate("Import - USA (MFN)", "Marine Insurance - Inbound") > rate("Import - China (MFN + anti-dumping)", "Marine Insurance - Inbound")
    assert len({finance.import_template(c)[1][0].rate for c in finance.IMPORT_CATEGORIES}) >= 4


def test_export_templates_bill_freight_and_insurance_and_ddp_adds_destination_duty():
    by = dict(finance.sales_templates())
    ddp = by["Export - Middle East (DDP)"]
    cif = by["Export - Europe (CIF)"]
    assert any(r.account == "Destination Duties Recovered (DDP)" for r in ddp) and not any(r.account == "Destination Duties Recovered (DDP)" for r in cif)
    assert all(any(r.account == "Freight Recovered" for r in rows) for n, rows in by.items() if n.startswith("Export"))
    assert [r.rate for r in by[world.SEZ_SALE]] == [0.0] and by[world.DOMESTIC_SALE_INTER][0].rate == 18.0


def test_item_tax_profiles_carry_every_rate_the_templates_override():
    profiles = finance.item_tax_profiles()
    assert 30 <= len(profiles) <= 50
    one = next(iter(profiles.values()))
    assert {"Input CGST", "Input SGST", "Input IGST", "Input IGST on Imports", "Output IGST", "Basic Customs Duty - MFN", "Basic Customs Duty - FTA", "Anti-dumping Duty"} <= set(one)
    assert one["Input CGST"] * 2 == one["Input IGST"]


def test_withholding_rates_by_group_and_every_supplier_group_maps_to_a_rate():
    cats = {w.name: w for w in finance.WITHHOLDING}
    assert cats["TDS 194C - Contractors & Transport"].rates == {"Company": 2.0, "Individual / HUF": 1.0, "Partnership / Firm": 2.0}
    assert cats["TDS 194Q - Purchase of Goods"].cumulative == 5_000_000 and cats["TDS 195 - Payments to Non-Residents"].rates == {"Non-Resident": 20.0}
    for v in world.VENDORS:
        if v.country == "India" and v.kind == world.SERVICE:
            cat = finance.SERVICE_WITHHOLDING[v.service]
            if cat:
                assert finance.LEGAL_GROUP[v.legal] in cats[cat].rates, (v.name, cat)
    assert set(finance.SERVICE_WITHHOLDING.values()) - {""} <= set(cats)


def test_payment_terms_templates_add_up_to_one_hundred_percent():
    terms = {t[0]: t for t in finance.PAYMENT_TERMS}
    for name, parts in finance.PAYMENT_TEMPLATES.items():
        assert sum(p for _, p in parts) == 100 and all(t in terms for t, _ in parts), name
    for v in world.VENDORS:
        assert finance.vendor_terms(v) in finance.PAYMENT_TEMPLATES
    for c in world.CUSTOMERS:
        assert finance.customer_terms(c) in finance.PAYMENT_TEMPLATES


def test_fifteen_warehouses_and_the_cost_centre_tree():
    assert len(finance.WAREHOUSES) == 15 and len({w[0] for w in finance.WAREHOUSES}) == 15
    assert {w[1] for w in finance.WAREHOUSES} <= set(finance.WAREHOUSE_TYPES) | {"Transit"}
    names = {c[0] for c in finance.COST_CENTRES}
    assert all(p == "" or p in names for _, p in finance.COST_CENTRES) and len(finance.PROFIT_CENTRES) == 6
    assert set(finance.GROUP_COST_CENTRE.values()) <= set(finance.PROFIT_CENTRES)
    assert {g for g in finance.GROUP_COST_CENTRE} == set(catalog.GROUPS) | {"SVC"}


def test_routes_cover_every_trading_partner_with_weight_bands_and_transit_times():
    rs = finance.routes()
    buying, selling = [r for r in rs if r.kind == "Buying"], [r for r in rs if r.kind == "Selling"]
    assert {c for r in buying for c in r.countries} == set(world.IMPORT_COUNTRIES)
    assert {c for r in selling for c in r.countries if c != "India"} == set(world.EXPORT_COUNTRIES)
    assert len({r.label for r in rs}) == len(rs) and len(rs) >= 35
    for r in rs:
        cond = finance.route_conditions(r)
        assert len(cond) == 5 and cond[0]["from_value"] == 0 and cond[-1]["to_value"] == 0
        assert [c["shipping_amount"] for c in cond] == sorted(c["shipping_amount"] for c in cond) and r.transit_days >= 1
    air = next(r for r in buying if r.mode == "Air" and r.countries == ("China",))
    sea = next(r for r in buying if r.mode == "Sea" and r.countries == ("China",))
    assert air.transit_days < sea.transit_days and air.per_kg > sea.per_kg * 10
    assert Counter(r.mode for r in rs)["Road"] == 6
