"""The test-data loader: idempotent writes, manifest, dry run, error messages, and the documents each step builds."""
import json
from collections import defaultdict
from urllib.parse import unquote

import pytest

from tools.testdata import catalog, finance, steps, world
from tools.testdata.writer import Writer, WriterError


class R:
    def __init__(self, data=None, status=200, text=""):
        self._d, self.status_code, self.text = data if data is not None else {}, status, text or json.dumps(data or {})

    def json(self):
        return self._d


class FakeERP:
    """An in-memory ERPNext: stores documents by (doctype, name); supports get, list with = / in filters, insert, update, insert_many, submit."""

    def __init__(self):
        self.docs: dict[tuple[str, str], dict] = {}
        self.fail_next: list[tuple[str, int, dict]] = []
        self.calls: list[tuple[str, str]] = []
        self.counter = defaultdict(int)

    def _name(self, doctype, doc):
        if doctype in ("Item Price", "Address"):                                       # automatic names, like ERPNext
            self.counter[doctype] += 1
            return f"{doctype}-{self.counter[doctype]:05d}"
        for k in ("name", "item_code", "supplier_name", "customer_name", "account_name", "company_name", "title", "warehouse_name", "cost_center_name", "label", "bank_name",
                  "year", "template_name", "group_name", "payment_term_name", "price_list_name", "uom_name", "territory_name", "supplier_group_name", "customer_group_name",
                  "item_group_name", "finance_book_name", "holiday_list_name", "tariff_number", "category_name"):
            if doc.get(k):
                n = doc[k]
                break
        else:
            self.counter[doctype] += 1
            n = f"{doctype}-{self.counter[doctype]:05d}"
        # ERPNext appends the company abbreviation to some names
        if doctype in ("Account", "Cost Center", "Warehouse", "Item Tax Template", "Purchase Taxes and Charges Template", "Sales Taxes and Charges Template") and doc.get("company"):
            abbr = "TTC"
            if not n.endswith(f" - {abbr}"):
                n = f"{n} - {abbr}"
        return n

    def _hit(self, method, url):
        self.calls.append((method, url))
        for i, (sub, status, body) in enumerate(self.fail_next):
            if sub in url:
                self.fail_next.pop(i)
                return R(body, status)
        return None

    def get(self, url, params=None, timeout=None):
        if (r := self._hit("get", url)):
            return r
        rest = url.split("/api/resource/")[1]
        if "/" in rest:
            dt, name = rest.split("/", 1)
            d = self.docs.get((unquote(dt), unquote(name)))
            return R({"data": d}) if d else R({"exception": "not found"}, 404)
        dt = unquote(rest)
        flt = json.loads(params.get("filters", "[]"))
        rows = [d for (t, _n), d in self.docs.items() if t == dt]
        for f, op, v in flt:
            rows = [d for d in rows if (d.get(f) in v if op == "in" else d.get(f) == v)]
        return R({"data": rows})

    def post(self, url, json=None, timeout=None):
        if (r := self._hit("post", url)):
            return r
        if "insert_many" in url:
            docs = __import__("json").loads(json["docs"])
            names = []
            for d in docs:
                n = self._name(d["doctype"], d)
                self.docs[(d["doctype"], n)] = {**d, "name": n}
                names.append(n)
            return R({"message": names})
        if "frappe.client.submit" in url:
            d = __import__("json").loads(json["doc"])
            self.docs[(d["doctype"], d["name"])]["docstatus"] = 1
            return R({"message": d})
        doctype = unquote(url.split("/api/resource/")[1])
        n = self._name(doctype, json)
        self.docs[(doctype, n)] = {**json, "name": n, "docstatus": 0}
        return R({"data": {"name": n}})

    def put(self, url, json=None, timeout=None):
        if (r := self._hit("put", url)):
            return r
        dt, name = url.split("/api/resource/")[1].split("/", 1)
        self.docs[(unquote(dt), unquote(name))].update(json)
        return R({"data": {}})


@pytest.fixture
def erp():
    return FakeERP()


@pytest.fixture
def ctx(erp, tmp_path):
    return steps.Context(Writer(erp, "http://erp.local", tmp_path / "manifest.jsonl", pause=0))


def created(erp, doctype):
    return [d for (t, _n), d in erp.docs.items() if t == doctype]


# ------------------------------------------------------------------ the writer
def test_ensure_creates_once_and_records_what_it_made(erp, ctx, tmp_path):
    assert ctx.w.ensure("Bank", "HDFC Bank", {"bank_name": "HDFC Bank"}) == "created"
    assert ctx.w.ensure("Bank", "HDFC Bank", {"bank_name": "HDFC Bank"}) == "exists"
    assert len(created(erp, "Bank")) == 1 and ctx.w.stats["Bank: created"] == 1 and ctx.w.stats["Bank: existed"] == 1
    lines = [json.loads(l) for l in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert [(l["doctype"], l["name"]) for l in lines] == [("Bank", "HDFC Bank")]


def test_dry_run_changes_nothing_and_still_counts(erp, tmp_path):
    w = Writer(erp, "http://erp.local", tmp_path / "m.jsonl", dry_run=True)
    w.ensure("Bank", "X", {"bank_name": "X"})
    w.create_many("Item Price", [{"item_code": "A"}, {"item_code": "B"}])
    assert not erp.docs and erp.calls == [] and not (tmp_path / "m.jsonl").exists()
    assert w.stats["Bank: would create"] == 1 and w.stats["Item Price: would create"] == 2


def test_errors_keep_erpnexts_own_message(erp, ctx):
    msg = json.dumps([json.dumps({"message": "Account Debtors is a group"}), json.dumps({"message": "Cannot post here"})])
    erp.fail_next.append(("/api/resource/Account", 417, {"_server_messages": msg}))
    with pytest.raises(WriterError, match="creating Account 'X'.*Account Debtors is a group \\| Cannot post here"):
        ctx.w.create("Account", {"account_name": "X"})


def test_temporary_server_trouble_is_retried(erp, ctx, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    erp.fail_next.append(("/api/resource/Bank", 503, {}))
    erp.fail_next.append(("/api/resource/Bank", 502, {}))
    assert ctx.w.create("Bank", {"bank_name": "B"}) == "B"
    erp.fail_next += [("/api/resource/Bank", 503, {})] * 5
    with pytest.raises(WriterError):
        ctx.w.create("Bank", {"bank_name": "C"})


def test_create_many_sends_batches_and_records_each(erp, ctx, tmp_path):
    docs = [{"tariff_number": f"7318.{i:02d}.00", "description": "x"} for i in range(120)]
    assert ctx.w.create_many("Customs Tariff Number", docs, batch=50) == 120
    assert len([c for c in erp.calls if "insert_many" in c[1]]) == 3 and len(created(erp, "Customs Tariff Number")) == 120
    assert len((tmp_path / "manifest.jsonl").read_text().splitlines()) == 120


def test_submit_posts_the_draft_once(erp, ctx):
    name = ctx.w.create("Journal Entry", {"title": "JE", "doctype": "Journal Entry"})
    ctx.w.submit("Journal Entry", name)
    ctx.w.submit("Journal Entry", name)                                                    # already submitted: no second call
    assert erp.docs[("Journal Entry", name)]["docstatus"] == 1 and len([c for c in erp.calls if "client.submit" in c[1]]) == 1


# ------------------------------------------------------------------ the steps
def test_company_and_fiscal_year(erp, ctx):
    steps.step_company(ctx)
    co = created(erp, "Company")[0]
    assert (co["company_name"], co["abbr"], co["country"], co["default_currency"], co["chart_of_accounts"]) == ("Test Trading Co", "TTC", "India", "INR", "India - Chart of Accounts")
    assert co["enable_perpetual_inventory"] == 1 and created(erp, "Fiscal Year")[0]["year_start_date"] == "2025-04-01"


def test_accounts_attach_to_the_right_parents_with_currency_and_type(erp, ctx):
    steps.step_accounts(ctx)
    accs = {d["account_name"]: d for d in created(erp, "Account")}
    assert len(accs) == len(finance.ACCOUNTS) == 67
    assert accs["Input IGST on Imports"]["parent_account"] == "Tax Assets - TTC" and accs["Input IGST on Imports"]["account_type"] == "Tax"
    assert accs["Creditors (CNY)"]["account_currency"] == "CNY" and accs["Creditors (CNY)"]["account_type"] == "Payable"
    assert accs["Basic Customs Duty - FTA"]["parent_account"] == "Import Duties & Landed Costs - TTC" and accs["Basic Customs Duty - FTA"]["account_type"] == "Chargeable"
    assert accs["Import Duties & Landed Costs"]["is_group"] == 1 and accs["SBI EEFC A/c (USD)"]["account_currency"] == "USD"
    assert all(d["company"] == "Test Trading Co" for d in accs.values())


def test_profit_centres_are_a_branch_of_the_cost_centre_tree(erp, ctx):
    steps.step_cost_centers(ctx)
    cc = {d["cost_center_name"]: d for d in created(erp, "Cost Center")}
    assert cc["Profit Centers"]["parent_cost_center"] == "Test Trading Co - TTC" and cc["Cost Centers"]["is_group"] == 1
    assert cc["Export Business"]["parent_cost_center"] == "Profit Centers - TTC"
    assert cc["Customs & Compliance"]["parent_cost_center"] == "Logistics & Warehousing - TTC" and cc["Logistics & Warehousing"]["parent_cost_center"] == "Cost Centers - TTC"
    assert len(cc) == 21


def test_tax_templates_carry_rows_references_actual_amounts_and_rules(erp, ctx):
    steps.step_tax_setup(ctx)
    pt = {d["title"]: d for d in created(erp, "Purchase Taxes and Charges Template")}
    china = pt["Import - China (MFN + anti-dumping)"]["taxes"]
    assert [r["account_head"] for r in china][:3] == ["Inbound Freight - Sea & Air - TTC", "Marine Insurance - Inbound - TTC", "Basic Customs Duty - MFN - TTC"]
    bcd = china[2]
    assert (bcd["charge_type"], bcd["row_id"], bcd["category"], bcd["rate"]) == ("On Previous Row Total", 2, "Valuation", 10.0)
    assert china[-1]["charge_type"] == "Actual" and china[-1]["tax_amount"] == 15500.0 and "row_id" not in china[-1]
    igst = next(r for r in china if r["account_head"] == "Input IGST on Imports - TTC")
    assert igst["category"] == "Total" and igst["row_id"] == 5
    assert pt[world.DOMESTIC_PURCHASE_INTRA]["is_default"] == 1 and pt["Import - USA (MFN)"]["is_default"] == 0
    rules = created(erp, "Tax Rule")
    assert len(rules) == 23 and {r["tax_type"] for r in rules} == {"Purchase", "Sales"}
    china_rule = next(r for r in rules if r["tax_category"] == "Import - China (MFN + anti-dumping)")
    assert china_rule["purchase_tax_template"] == "Import - China (MFN + anti-dumping) - TTC" and china_rule["company"] == "Test Trading Co"
    it = next(d for d in created(erp, "Item Tax Template") if d["title"] == "HS 73181500 / GST 18%")
    rates = {t["tax_type"]: t["tax_rate"] for t in it["taxes"]}
    assert rates["Input CGST - TTC"] == 9 and rates["Basic Customs Duty - MFN - TTC"] == 10 and rates["Anti-dumping Duty - TTC"] == 6


def test_vendors_carry_currency_tax_category_terms_withholding_and_party_account(erp, ctx):
    steps.step_suppliers(ctx)
    sup = {d["supplier_name"]: d for d in created(erp, "Supplier")}
    assert len(sup) == 50 and len(created(erp, "Address")) == 50
    cn = sup["Ningbo Hengda Fasteners Co Ltd"]
    assert (cn["default_currency"], cn["tax_category"], cn["supplier_group"], cn["default_price_list"]) == ("CNY", "Import - China (MFN + anti-dumping)", "Import Vendors - Asia Pacific", "Buying - CNY")
    assert cn["accounts"] == [{"company": "Test Trading Co", "account": "Creditors (CNY) - TTC"}] and "tax_withholding_category" not in cn
    mh = sup["Bharat Precision Fasteners Pvt Ltd"]
    assert mh["tax_category"] == world.DOMESTIC_PURCHASE_INTRA and mh["tax_withholding_category"] == "TDS 194Q - Purchase of Goods" and "accounts" not in mh
    tr = sup["Deccan Transporters"]
    assert tr["is_transporter"] == 1 and tr["tax_withholding_category"] == "TDS 194C - Contractors & Transport" and tr["tax_withholding_group"] == "Partnership / Firm"
    assert sup["Gujarat Steel & Alloys Ltd"]["tax_category"] == world.DOMESTIC_PURCHASE_INTER
    addr = next(a for a in created(erp, "Address") if a["address_title"] == "Ningbo Hengda Fasteners Co Ltd")
    assert addr["country"] == "China" and addr["links"] == [{"link_doctype": "Supplier", "link_name": "Ningbo Hengda Fasteners Co Ltd"}]


def test_customers_carry_territory_credit_limit_and_two_addresses(erp, ctx):
    steps.step_customers(ctx)
    cust = {d["customer_name"]: d for d in created(erp, "Customer")}
    assert len(cust) == 50 and len(created(erp, "Address")) == 100
    us = cust["Atlas Industrial Distributors Inc"]
    assert (us["default_currency"], us["territory"], us["tax_category"], us["default_price_list"]) == ("USD", "North America", "Export - North America (DDP)", "Selling - USD")
    assert us["credit_limits"] == [{"company": "Test Trading Co", "credit_limit": 60_000_000}] and us["accounts"][0]["account"] == "Debtors (USD) - TTC"
    sez = cust["Kandla SEZ Fabricators Pvt Ltd"]
    assert sez["tax_category"] == world.SEZ_SALE and sez["customer_group"] == "SEZ Units" and "accounts" not in sez
    assert {a["address_type"] for a in created(erp, "Address")} == {"Billing", "Shipping"}


def test_items_link_group_hs_code_tax_profile_suppliers_and_lead_time(erp, ctx):
    steps.step_items(ctx)
    items = {d["item_code"]: d for d in created(erp, "Item")}
    assert len(items) == 200 and sum(1 for d in items.values() if d["is_stock_item"]) == 192
    bolt = items["FAS-0001"]
    assert bolt["item_group"] == "Fasteners & Hardware" and bolt["customs_tariff_number"] == "7318.15.00" and bolt["weight_uom"] == "Kg"
    assert bolt["taxes"] == [{"item_tax_template": "HS 73181500 / GST 18% - TTC"}] and bolt["valuation_method"] == "Moving Average"
    cat = next(i for i in catalog.build_items() if i.code == "FAS-0001")
    assert bolt["lead_time_days"] == min(l for _n, _p, l in cat.suppliers) and {s["supplier"] for s in bolt["supplier_items"]} == {n for n, _p, _l in cat.suppliers}
    assert bolt["item_defaults"][0]["default_supplier"] == cat.suppliers[0][0] and items["FGI-0001"]["valuation_method"] == "FIFO"
    svc = items["SVC-0001"]
    assert svc["is_stock_item"] == 0 and svc["item_defaults"][0]["expense_account"] == "Inbound Freight - Sea & Air - TTC" and "taxes" not in svc


def test_prices_are_per_vendor_in_its_currency_and_per_customer_currency(erp, ctx):
    for pl in ("Standard Buying", "Standard Selling"):                                         # ship with ERPNext
        erp.docs[("Price List", pl)] = {"name": pl, "enabled": 1}
    steps.step_reference(ctx)                                                                  # creates the price lists the re-run check looks through
    steps.step_items(ctx)
    steps.step_prices(ctx)
    prices = created(erp, "Item Price")
    goods = 192
    selling = [p for p in prices if p["selling"]]
    lists = {"Standard Selling", *{f"Selling - {world.customer_currency(c)}" for c in world.CUSTOMERS if world.customer_currency(c) != "INR"}}
    assert len(selling) == goods * len(lists) and {p["price_list"] for p in selling} == lists and len(lists) > 3
    buying = [p for p in prices if p["buying"]]
    assert sum(len(i.suppliers) for i in catalog.build_items()) == len(buying) and all(p["supplier"] and p["lead_time_days"] for p in buying)
    cny = next(p for p in buying if p["currency"] == "CNY")
    assert cny["price_list"] == "Buying - CNY"
    again = len(prices)
    steps.step_prices(ctx)
    assert len(created(erp, "Item Price")) == again                                           # re-running adds nothing


def test_opening_capital_balances_and_is_posted_once(erp, ctx):
    for mode in steps.MODE_ACCOUNT:
        erp.docs[("Mode of Payment", mode)] = {"name": mode, "accounts": []}
    erp.docs[("Company", "Test Trading Co")] = {"name": "Test Trading Co"}
    steps.step_finish(ctx)
    je = created(erp, "Journal Entry")
    assert len(je) == 1 and je[0]["docstatus"] == 1 and je[0]["posting_date"] == "2025-11-01"
    assert sum(a["debit_in_account_currency"] for a in je[0]["accounts"]) == sum(a["credit_in_account_currency"] for a in je[0]["accounts"]) == 160_000_000
    assert erp.docs[("Company", "Test Trading Co")]["default_bank_account"] == "HDFC Bank Current A/c - TTC"
    steps.step_finish(ctx)
    assert len(created(erp, "Journal Entry")) == 1


def test_every_step_runs_in_dependency_order_and_is_listed():
    names = list(steps.STEPS)
    assert names.index("company") < names.index("accounts") < names.index("tax_setup") < names.index("suppliers") < names.index("items") < names.index("prices") < names.index("finish")
    assert names.index("reference") < names.index("accounts") and names.index("accounts") < names.index("banks") and names.index("suppliers") < names.index("items")
    assert names[-1] == "verify" and all(doc for _fn, doc in steps.STEPS.values())
