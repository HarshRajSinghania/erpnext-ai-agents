"""The loading steps, in dependency order. Each step is idempotent (re-running skips what exists).
Data definitions live in world.py, catalog.py and finance.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from . import catalog, finance, world
from .writer import Writer


@dataclass
class Context:
    w: Writer
    company: str = world.COMPANY["name"]
    abbr: str = world.COMPANY["abbr"]
    cache: dict[str, Any] = field(default_factory=dict)

    def acc(self, name: str) -> str:
        """Full account name as ERPNext stores it, e.g. 'Debtors - TTC'."""
        return f"{name} - {self.abbr}"

    def cc(self, name: str) -> str:
        return f"{name} - {self.abbr}"


def _group(ctx: Context, doctype: str, key: str, names: list[str], build: Callable[[str], dict]) -> None:
    for n in names:
        ctx.w.ensure(doctype, n, build(n))


# ====================================================================== 1. company
def step_company(ctx: Context) -> None:
    c = world.COMPANY
    # Fiscal years first: a company cannot post into a year that does not exist.
    for fy in world.FISCAL_YEARS:
        ctx.w.ensure("Fiscal Year", fy["year"], fy)
    ctx.w.ensure("Company", c["name"], {
        "company_name": c["name"], "abbr": c["abbr"], "country": c["country"], "default_currency": c["currency"],
        "chart_of_accounts": "India - Chart of Accounts", "create_chart_of_accounts_based_on": "Standard Template",
        "valuation_method": "Moving Average", "enable_perpetual_inventory": 1, "domain": "Distribution",
    })


# ====================================================================== 2. currencies and exchange rates
def step_currencies(ctx: Context) -> None:
    w = ctx.w
    for ccy in world.FOREIGN_CURRENCIES:
        if w.dry_run or not w.get("Currency", ccy).get("enabled"):
            w.update("Currency", ccy, {"enabled": 1})
    series = world.fx_series()
    have = set()
    if not w.dry_run:
        have = {(r["date"], r["from_currency"]) for r in w.find("Currency Exchange", [["to_currency", "=", "INR"]], ["date", "from_currency"], limit=100000)}
    docs = [{"date": d.isoformat(), "from_currency": ccy, "to_currency": "INR", "exchange_rate": rate, "for_buying": 1, "for_selling": 1}
            for ccy, s in series.items() for d, rate in s.items() if (d.isoformat(), ccy) not in have]
    w.create_many("Currency Exchange", docs, batch=100)


# ====================================================================== 3. reference data: units, groups, territories, terms, banks, books
def step_reference(ctx: Context) -> None:
    w = ctx.w
    for u in ("Carton", "Roll", "Pack", "Pallet"):
        w.ensure("UOM", u, {"uom_name": u, "enabled": 1, "must_be_whole_number": 1})
    for t in finance.WAREHOUSE_TYPES:
        w.ensure("Warehouse Type", t, {"name": t, "description": f"{t} warehouse"})
    supplier_groups = ["Domestic Goods Vendors", "Import Vendors - Asia Pacific", "Import Vendors - Europe", "Import Vendors - Americas", "Import Vendors - Middle East",
                       "Freight & Logistics", "Professional Services"]
    _group(ctx, "Supplier Group", "supplier_group_name", supplier_groups,
           lambda n: {"supplier_group_name": n, "parent_supplier_group": "All Supplier Groups", "is_group": 0})
    customer_groups = ["Domestic - Industrial", "Domestic - OEM", "Domestic - Retail & Distributors", "SEZ Units", "Export - Distributors", "Export - Industrial"]
    _group(ctx, "Customer Group", "customer_group_name", customer_groups,
           lambda n: {"customer_group_name": n, "parent_customer_group": "All Customer Groups", "is_group": 0})
    for zone in sorted(set(world.STATE_ZONE.values())):
        w.ensure("Territory", zone, {"territory_name": zone, "parent_territory": "India", "is_group": 0})
    for region in sorted({c.region for c in world.EXPORT_COUNTRIES.values()}):
        w.ensure("Territory", region, {"territory_name": region, "parent_territory": "Rest Of The World", "is_group": 0})
    _group(ctx, "Tax Category", "title", finance.TAX_CATEGORIES, lambda n: {"title": n})
    _group(ctx, "Tax Withholding Group", "group_name", finance.WITHHOLDING_GROUPS, lambda n: {"group_name": n})
    _group(ctx, "Finance Book", "finance_book_name", finance.FINANCE_BOOKS, lambda n: {"finance_book_name": n})
    _group(ctx, "Bank", "bank_name", finance.BANKS, lambda n: {"bank_name": n})
    for name, portion, days in finance.PAYMENT_TERMS:
        w.ensure("Payment Term", name, {"payment_term_name": name, "invoice_portion": portion, "credit_days": days,
                                        "due_date_based_on": "Day(s) after invoice date", "description": name})
    terms = {t[0]: t for t in finance.PAYMENT_TERMS}
    for tname, parts in finance.PAYMENT_TEMPLATES.items():
        w.ensure("Payment Terms Template", tname, {"template_name": tname, "terms": [
            {"payment_term": t, "invoice_portion": pct, "credit_days": terms[t][2], "due_date_based_on": "Day(s) after invoice date", "description": t} for t, pct in parts]})
    _holiday_list(ctx, "India 2025-26", date(2025, 4, 1), date(2026, 3, 31), finance.HOLIDAYS_2025_26)
    _holiday_list(ctx, "India 2026-27", date(2026, 4, 1), date(2027, 3, 31), finance.HOLIDAYS_2026_27)
    # price lists: one per currency we buy or sell in
    for ccy in sorted({world.vendor_currency(v) for v in world.VENDORS}):
        w.ensure("Price List", f"Buying - {ccy}", {"price_list_name": f"Buying - {ccy}", "currency": ccy, "enabled": 1, "buying": 1, "selling": 0}) if ccy != "INR" else None
    for ccy in sorted({world.customer_currency(c) for c in world.CUSTOMERS}):
        w.ensure("Price List", f"Selling - {ccy}", {"price_list_name": f"Selling - {ccy}", "currency": ccy, "enabled": 1, "buying": 0, "selling": 1}) if ccy != "INR" else None


def _holiday_list(ctx: Context, name: str, start: date, end: date, festivals: list[tuple[str, str]]) -> None:
    rows = [{"holiday_date": d, "description": desc} for d, desc in festivals]
    d, fest = start, {d for d, _ in festivals}
    while d <= end:
        if d.weekday() == 6 and d.isoformat() not in fest:
            rows.append({"holiday_date": d.isoformat(), "description": "Sunday", "weekly_off": 1})
        d += timedelta(days=1)
    ctx.w.ensure("Holiday List", name, {"holiday_list_name": name, "from_date": start.isoformat(), "to_date": end.isoformat(), "holidays": rows})


# ====================================================================== 4. chart of accounts, cost and profit centres
def step_accounts(ctx: Context) -> None:
    for name, parent, root, typ, ccy, group in finance.ACCOUNTS:
        full = ctx.acc(name)
        doc = {"account_name": name, "parent_account": ctx.acc(parent), "company": ctx.company, "root_type": root, "is_group": 1 if group else 0}
        if typ:
            doc["account_type"] = typ
        if ccy:
            doc["account_currency"] = ccy
        ctx.w.ensure("Account", full, doc)


def step_cost_centers(ctx: Context) -> None:
    root = ctx.cc(ctx.company)
    for grp in ("Profit Centers", "Cost Centers"):
        ctx.w.ensure("Cost Center", ctx.cc(grp), {"cost_center_name": grp, "parent_cost_center": root, "company": ctx.company, "is_group": 1})
    for pc in finance.PROFIT_CENTRES:
        ctx.w.ensure("Cost Center", ctx.cc(pc), {"cost_center_name": pc, "parent_cost_center": ctx.cc("Profit Centers"), "company": ctx.company, "is_group": 0})
    groups = {parent for _, parent in finance.COST_CENTRES if parent}
    for name, parent in finance.COST_CENTRES:
        if name in groups:
            ctx.w.ensure("Cost Center", ctx.cc(name), {"cost_center_name": name, "parent_cost_center": ctx.cc("Cost Centers"), "company": ctx.company, "is_group": 1})
    for name, parent in finance.COST_CENTRES:
        if name not in groups:
            ctx.w.ensure("Cost Center", ctx.cc(name), {"cost_center_name": name, "parent_cost_center": ctx.cc(parent or "Cost Centers"), "company": ctx.company, "is_group": 0})


# ====================================================================== 5. bank accounts
def step_banks(ctx: Context) -> None:
    for n, (gl, bank, ccy, ifsc) in enumerate(finance.BANK_ACCOUNTS, start=1):
        ctx.w.ensure_one("Bank Account", [["account_name", "=", gl], ["bank", "=", bank]], {
            "account_name": gl, "bank": bank, "account": ctx.acc(gl), "is_company_account": 1, "company": ctx.company,
            "bank_account_no": f"{50100000000000 + n * 7919317:014d}", "branch_code": ifsc, "is_default": 1 if n == 1 else 0,
        })


# ====================================================================== 6. tax withholding (TDS)
def step_withholding(ctx: Context) -> None:
    for wh in finance.WITHHOLDING:
        rates = [{"from_date": start, "to_date": end, "tax_withholding_group": group, "tax_withholding_rate": pct,
                  "single_threshold": wh.single, "cumulative_threshold": wh.cumulative}
                 for start, end in finance.FY_RATE_WINDOWS for group, pct in wh.rates.items()]
        ctx.w.ensure("Tax Withholding Category", wh.name, {
            "name": wh.name, "category_name": wh.name, "tax_deduction_basis": "Net Total", "round_off_tax_amount": 1,
            "rates": rates, "accounts": [{"company": ctx.company, "account": ctx.acc(wh.account)}],
        })


# ====================================================================== 7. tax templates and Tax Rules (what makes duties depend on location)
def _purchase_rows(ctx: Context, rows: list[finance.Row]) -> list[dict]:
    return [{"category": r.category, "add_deduct_tax": r.add_deduct, "charge_type": r.charge_type, "account_head": ctx.acc(r.account), "description": r.description,
             "rate": r.rate, **({"row_id": r.row_id} if r.row_id else {}), **({"tax_amount": r.amount} if r.charge_type == "Actual" else {})} for r in rows]


def _sales_rows(ctx: Context, rows: list[finance.Row]) -> list[dict]:
    return [{"charge_type": r.charge_type, "account_head": ctx.acc(r.account), "description": r.description, "rate": r.rate} for r in rows]


def template_name(ctx: Context, title: str) -> str:
    return f"{title} - {ctx.abbr}"


def step_tax_setup(ctx: Context) -> None:
    w = ctx.w
    for profile, rates in finance.item_tax_profiles().items():
        w.ensure_one("Item Tax Template", [["title", "=", profile], ["company", "=", ctx.company]], {
            "title": profile, "company": ctx.company, "taxes": [{"tax_type": ctx.acc(acct), "tax_rate": rate} for acct, rate in rates.items()]})
    purchase = finance.domestic_purchase_templates() + [finance.import_template(c) for c in finance.IMPORT_CATEGORIES]
    for title, rows in purchase:
        w.ensure_one("Purchase Taxes and Charges Template", [["title", "=", title], ["company", "=", ctx.company]], {
            "title": title, "company": ctx.company, "is_default": 1 if title == world.DOMESTIC_PURCHASE_INTRA else 0, "taxes": _purchase_rows(ctx, rows)})
    sales = finance.sales_templates()
    for title, rows in sales:
        w.ensure_one("Sales Taxes and Charges Template", [["title", "=", title], ["company", "=", ctx.company]], {
            "title": title, "company": ctx.company, "is_default": 1 if title == world.DOMESTIC_SALE_INTRA else 0, "taxes": _sales_rows(ctx, rows)})
    # Tax Rules: the supplier's / customer's tax category selects the template, the same way ERPNext does it for a new order
    for title, _rows in purchase:
        w.ensure_one("Tax Rule", [["tax_type", "=", "Purchase"], ["tax_category", "=", title], ["company", "=", ctx.company]], {
            "tax_type": "Purchase", "tax_category": title, "purchase_tax_template": template_name(ctx, title), "company": ctx.company, "priority": 1})
    for title, _rows in sales:
        w.ensure_one("Tax Rule", [["tax_type", "=", "Sales"], ["tax_category", "=", title], ["company", "=", ctx.company]], {
            "tax_type": "Sales", "tax_category": title, "sales_tax_template": template_name(ctx, title), "company": ctx.company, "priority": 1})


# ====================================================================== 8. shipping routes
def step_shipping(ctx: Context) -> None:
    w = ctx.w
    after = "calculate_based_on"
    for fieldname, label, ftype, options in finance.CUSTOM_FIELDS:
        doc = {"dt": "Shipping Rule", "fieldname": fieldname, "label": label, "fieldtype": ftype, "insert_after": after}
        if options:
            doc["options"] = options
        w.ensure("Custom Field", f"Shipping Rule-{fieldname}", doc)
        after = fieldname
    for r in finance.routes():
        buying = r.kind == "Buying"
        cost_center = "Procurement & Imports" if buying else ("Outbound Logistics" if r.mode == "Road" else "Export Sales")
        w.ensure("Shipping Rule", r.label, {
            "label": r.label, "shipping_rule_type": r.kind, "company": ctx.company, "calculate_based_on": "Net Weight",
            "account": ctx.acc("Inbound Freight - Sea & Air" if buying else "Freight Recovered"), "cost_center": ctx.cc(cost_center),
            "conditions": finance.route_conditions(r), "countries": [{"country": c} for c in r.countries],
            "custom_route_origin": r.origin, "custom_route_destination": r.destination, "custom_transport_mode": r.mode,
            "custom_transit_days": r.transit_days, "custom_incoterm": r.incoterm,
        })


# ====================================================================== 9. warehouses
def step_warehouses(ctx: Context) -> None:
    root = f"All Warehouses - {ctx.abbr}"
    for name, wtype in finance.WAREHOUSES:
        full = f"{name} - {ctx.abbr}"
        if not ctx.w.dry_run and ctx.w.exists("Warehouse", full):
            if ctx.w.get("Warehouse", full).get("warehouse_type") != wtype:
                ctx.w.update("Warehouse", full, {"warehouse_type": wtype})
            continue
        doc = {"warehouse_name": name, "company": ctx.company, "parent_warehouse": root, "warehouse_type": wtype, "is_group": 0}
        ctx.w.create("Warehouse", doc)
    ctx.w.update("Warehouse", f"Goods In Transit - {ctx.abbr}", {"account": ctx.acc("Goods In Transit (Stock)")})


# ====================================================================== 10. item groups and customs tariff numbers
def step_item_groups(ctx: Context) -> None:
    for code, (name, _margin) in catalog.GROUPS.items():
        pc = ctx.cc(finance.GROUP_COST_CENTRE[code])
        ctx.w.ensure("Item Group", name, {
            "item_group_name": name, "parent_item_group": "All Item Groups", "is_group": 0,
            "item_group_defaults": [{"company": ctx.company, "default_warehouse": f"Stores - {ctx.abbr}", "buying_cost_center": pc, "selling_cost_center": pc,
                                     "expense_account": ctx.acc("Cost of Goods Sold"), "income_account": ctx.acc("Sales")}]})


def step_customs(ctx: Context) -> None:
    seen: dict[str, str] = {}
    for it in catalog.build_items():
        if it.hs:
            seen.setdefault(it.hs, it.hs_desc)
    have = set() if ctx.w.dry_run else {r["name"] for r in ctx.w.find("Customs Tariff Number", [], ["name"], limit=10000)}
    ctx.w.create_many("Customs Tariff Number", [{"tariff_number": hs, "description": desc} for hs, desc in seen.items() if hs not in have])


# ====================================================================== 11. suppliers and customers, with addresses
PHONE_SEED = 9000


def _address_line(name: str, n: int) -> str:
    return f"{10 + sum(map(ord, name)) % 480}, {('Industrial Estate', 'Trade Centre', 'Business Park', 'Port Road', 'Commercial Complex')[n % 5]}"


def _pincode(country: str, name: str) -> str:
    base = sum(map(ord, name))
    return f"{400000 + base % 99999}" if country == "India" else f"{10000 + base % 89999}"


def _supplier_group(v: world.Vendor) -> str:
    if v.kind == world.SERVICE:
        return "Professional Services" if v.service == "calibration" else "Freight & Logistics"
    if v.country == "India":
        return "Domestic Goods Vendors"
    region = world.IMPORT_COUNTRIES[v.country].region
    return {"Asia Pacific": "Import Vendors - Asia Pacific", "Europe": "Import Vendors - Europe", "Middle East": "Import Vendors - Middle East"}.get(region, "Import Vendors - Americas")


def step_suppliers(ctx: Context) -> None:
    w = ctx.w
    for n, v in enumerate(world.VENDORS):
        ccy = world.vendor_currency(v)
        doc: dict[str, Any] = {
            "supplier_name": v.name, "supplier_type": v.legal, "supplier_group": _supplier_group(v), "country": v.country, "tax_category": world.vendor_tax_category(v),
            "default_currency": ccy, "payment_terms": finance.vendor_terms(v), "is_transporter": 1 if v.service == "transport" else 0,
            "default_price_list": "Standard Buying" if ccy == "INR" else f"Buying - {ccy}",
        }
        if ccy != "INR":
            doc["accounts"] = [{"company": ctx.company, "account": ctx.acc(f"Creditors ({ccy})")}]
        if v.country == "India":
            category = finance.SERVICE_WITHHOLDING.get(v.service, "") if v.kind == world.SERVICE else "TDS 194Q - Purchase of Goods"
            if category:
                doc["tax_withholding_category"] = category
                doc["tax_withholding_group"] = finance.LEGAL_GROUP[v.legal]
        if w.dry_run or not w.exists("Supplier", v.name):
            w.create("Supplier", doc)
        else:
            w.stats["Supplier: existed"] += 1
        w.ensure_one("Address", [["address_title", "=", v.name], ["address_type", "=", "Billing"]], {
            "address_title": v.name, "address_type": "Billing", "address_line1": _address_line(v.name, n), "city": v.city, "state": v.state or None,
            "country": v.country, "pincode": _pincode(v.country, v.name), "is_primary_address": 1, "links": [{"link_doctype": "Supplier", "link_name": v.name}]})


def _customer_group(c: world.Customer) -> str:
    if c.country != "India":
        return "Export - Industrial" if c.kind in ("industrial", "oem") else "Export - Distributors"
    return {"sez": "SEZ Units", "oem": "Domestic - OEM", "industrial": "Domestic - Industrial"}.get(c.kind, "Domestic - Retail & Distributors")


def _territory(c: world.Customer) -> str:
    return world.STATE_ZONE.get(c.state, "India") if c.country == "India" else world.EXPORT_COUNTRIES[c.country].region


def step_customers(ctx: Context) -> None:
    w = ctx.w
    for n, c in enumerate(world.CUSTOMERS):
        ccy = world.customer_currency(c)
        doc: dict[str, Any] = {
            "customer_name": c.name, "customer_type": "Company", "customer_group": _customer_group(c), "territory": _territory(c), "default_currency": ccy,
            "tax_category": world.customer_tax_category(c), "payment_terms": finance.customer_terms(c),
            "default_price_list": "Standard Selling" if ccy == "INR" else f"Selling - {ccy}",
            "credit_limits": [{"company": ctx.company, "credit_limit": c.credit_limit}],
        }
        if ccy != "INR":
            doc["accounts"] = [{"company": ctx.company, "account": ctx.acc(f"Debtors ({ccy})")}]
        if w.dry_run or not w.exists("Customer", c.name):
            w.create("Customer", doc)
        else:
            w.stats["Customer: existed"] += 1
        link = [{"link_doctype": "Customer", "link_name": c.name}]
        for kind, primary_billing, primary_shipping in (("Billing", 1, 0), ("Shipping", 0, 1)):
            w.ensure_one("Address", [["address_title", "=", c.name], ["address_type", "=", kind]], {
                "address_title": c.name, "address_type": kind, "address_line1": _address_line(c.name, n + (3 if kind == "Shipping" else 0)), "city": c.city,
                "state": c.state or None, "country": c.country, "pincode": _pincode(c.country, c.name), "is_primary_address": primary_billing,
                "is_shipping_address": primary_shipping, "links": link})


# ====================================================================== 12. items and prices
SERVICE_ACCOUNT = {"SVC-0001": "Inbound Freight - Sea & Air", "SVC-0002": "Inbound Freight - Sea & Air", "SVC-0003": "Inland Transport - Inbound",
                   "SVC-0004": "Customs Compliance Fees", "SVC-0005": "Marine Insurance - Inbound", "SVC-0006": "Warehouse Rent",
                   "SVC-0007": "Packing Expenses", "SVC-0008": "Professional Fees"}


def item_doc(ctx: Context, it: catalog.Item) -> dict[str, Any]:
    pc = ctx.cc(finance.GROUP_COST_CENTRE[it.group_code])
    doc: dict[str, Any] = {
        "item_code": it.code, "item_name": it.name, "item_group": it.group, "stock_uom": it.uom, "is_stock_item": 1 if it.stock else 0,
        "is_purchase_item": 1, "is_sales_item": 1 if it.stock else 0, "description": f"{it.name} ({it.hs_desc})" if it.hs else it.name,
    }
    default = {"company": ctx.company, "buying_cost_center": pc, "selling_cost_center": pc}
    if it.stock:
        main = it.suppliers[0]
        doc.update({
            "valuation_method": "FIFO" if it.group_code in ("FGI", "FGC") else "Moving Average", "weight_per_unit": it.kg, "weight_uom": "Kg",
            "customs_tariff_number": it.hs, "country_of_origin": it.country_of_origin, "lead_time_days": min(lead for _n, _p, lead in it.suppliers),
            "min_order_qty": it.moq, "taxes": [{"item_tax_template": template_name(ctx, it.profile)}],
            "supplier_items": [{"supplier": name, "supplier_part_no": f"{name.split()[0][:4].upper()}-{it.code}"} for name, _p, _l in it.suppliers],
        })
        default.update({"default_warehouse": f"Stores - {ctx.abbr}", "default_supplier": main[0],
                        "expense_account": ctx.acc("Cost of Goods Sold"), "income_account": ctx.acc("Sales")})
    else:
        default["expense_account"] = ctx.acc(SERVICE_ACCOUNT[it.code])
    doc["item_defaults"] = [default]
    return doc


def step_items(ctx: Context, items: list[catalog.Item] | None = None) -> None:
    for it in (items if items is not None else catalog.build_items()):
        ctx.w.ensure("Item", it.code, item_doc(ctx, it))


def step_prices(ctx: Context, items: list[catalog.Item] | None = None) -> None:
    w, fx, day = ctx.w, world.fx_series(), world.fx_dates()[0]
    only = items is not None
    items = [i for i in (items if only else catalog.build_items()) if i.stock]
    vendors = {v.name: v for v in world.VENDORS}
    have: set[tuple] = set()
    if not w.dry_run:
        lists = [r["name"] for r in w.find("Price List", [["enabled", "=", 1]], ["name"], limit=500)]
        flt = [["price_list", "in", lists]] + ([["item_code", "in", [i.code for i in items]]] if only else [])
        have = {(r["item_code"], r["price_list"], r.get("supplier") or "") for r in w.find("Item Price", flt, ["item_code", "price_list", "supplier"], limit=200000)}
    docs: list[dict] = []
    for it in items:
        for name, price, lead in it.suppliers:                                     # what each vendor charges, in its own currency
            ccy = world.vendor_currency(vendors[name])
            pl = "Standard Buying" if ccy == "INR" else f"Buying - {ccy}"
            if (it.code, pl, name) not in have:
                docs.append({"item_code": it.code, "uom": it.uom, "price_list": pl, "price_list_rate": price, "currency": ccy, "buying": 1, "selling": 0,
                             "supplier": name, "valid_from": "2025-10-01", "lead_time_days": lead})
        for ccy in ["INR", *sorted({world.customer_currency(c) for c in world.CUSTOMERS} - {"INR"})]:
            pl = "Standard Selling" if ccy == "INR" else f"Selling - {ccy}"
            if (it.code, pl, "") not in have:
                docs.append({"item_code": it.code, "uom": it.uom, "price_list": pl, "price_list_rate": catalog.selling_price(it, ccy, fx, day), "currency": ccy,
                             "buying": 0, "selling": 1, "valid_from": "2025-10-01"})
    w.create_many("Item Price", docs, batch=100)


# ====================================================================== 13. finishing: payment modes, company defaults, opening capital
MODE_ACCOUNT = {"Cash": "Cash", "Wire Transfer": "HDFC Bank Current A/c", "Cheque": "ICICI Bank Collection A/c", "Bank Draft": "HDFC Bank Current A/c",
                "Credit Card": "Axis Bank Operations A/c"}
OPENING_TITLE = "Opening capital - Test Trading Co"
OPENING_LINES = [  # (account, debit, credit)
    ("HDFC Bank Current A/c", 100_000_000, 0), ("ICICI Bank Collection A/c", 60_000_000, 0),
    ("Share Capital", 0, 120_000_000), ("Unsecured Loans", 0, 40_000_000),
]


def step_finish(ctx: Context) -> None:
    w = ctx.w
    for mode, account in MODE_ACCOUNT.items():
        if w.dry_run:
            w.stats["Mode of Payment: would update"] += 1
            continue
        doc = w.get("Mode of Payment", mode)
        if not any(a.get("company") == ctx.company for a in doc.get("accounts", [])):
            rows = [{"company": a["company"], "default_account": a["default_account"]} for a in doc.get("accounts", [])]
            rows.append({"company": ctx.company, "default_account": ctx.acc(account)})
            w.update("Mode of Payment", mode, {"accounts": rows})
    # The company's default bank account is a LEDGER account (a Bank Account record is a different thing in ERPNext).
    w.update("Company", ctx.company, {"default_holiday_list": "India 2025-26", "default_bank_account": ctx.acc("HDFC Bank Current A/c"),
                                      "expenses_included_in_valuation": ctx.acc("Expenses Included In Valuation")})
    doc = {
        "title": OPENING_TITLE, "voucher_type": "Journal Entry", "company": ctx.company, "posting_date": world.PERIOD_START.isoformat(),
        "naming_series": "ACC-JV-.YYYY.-", "user_remark": "Opening position: shares issued to the promoters and a promoter loan, received in the bank.",
        "accounts": [{"account": ctx.acc(a), "debit_in_account_currency": dr, "credit_in_account_currency": cr} for a, dr, cr in OPENING_LINES],
    }
    if w.dry_run or not w.find("Journal Entry", [["title", "=", OPENING_TITLE], ["company", "=", ctx.company]], limit=1):
        name = w.create("Journal Entry", doc)
        w.submit("Journal Entry", name)
    else:
        w.stats["Journal Entry: existed"] += 1


def _num(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return str(value)


# ====================================================================== 14. verification (read-only)
def step_verify(ctx: Context) -> None:
    w = ctx.w
    print("   What exists for the company:")
    for label, doctype, flt in [
        ("accounts", "Account", [["company", "=", ctx.company]]), ("cost / profit centres", "Cost Center", [["company", "=", ctx.company]]),
        ("warehouses", "Warehouse", [["company", "=", ctx.company]]), ("bank accounts", "Bank Account", [["company", "=", ctx.company]]),
        ("purchase tax templates", "Purchase Taxes and Charges Template", [["company", "=", ctx.company]]),
        ("sales tax templates", "Sales Taxes and Charges Template", [["company", "=", ctx.company]]), ("item tax templates", "Item Tax Template", [["company", "=", ctx.company]]),
        ("tax rules", "Tax Rule", [["company", "=", ctx.company]]), ("shipping rules", "Shipping Rule", [["company", "=", ctx.company]]),
        ("tax withholding categories", "Tax Withholding Category", []), ("tax categories", "Tax Category", []),
        ("finance books", "Finance Book", []), ("banks", "Bank", []), ("currency exchange rates", "Currency Exchange", [["to_currency", "=", "INR"]]),
    ]:
        print(f"     {label:<28}{len(w.find(doctype, flt, limit=100000))}")
    print("   Masters (whole site, includes your earlier demo data):")
    for label, doctype in [("items", "Item"), ("suppliers", "Supplier"), ("customers", "Customer"), ("item prices", "Item Price"), ("addresses", "Address")]:
        print(f"     {label:<28}{len(w.find(doctype, [], limit=100000))}")
    gl = w.find("GL Entry", [["company", "=", ctx.company], ["is_cancelled", "=", 0]], ["debit", "credit"], limit=100000)
    print(f"   Ledger entries for the company: {len(gl)}; total debit {sum(g['debit'] for g in gl):,.2f} = total credit {sum(g['credit'] for g in gl):,.2f}")
    rep = w.report("Balance Sheet", {"company": ctx.company, "filter_based_on": "Fiscal Year", "from_fiscal_year": "2025-2026", "to_fiscal_year": "2026-2027",
                                     "periodicity": "Yearly", "accumulated_values": 1, "include_default_book_entries": 1})
    print("   Balance sheet (ERPNext's own report), amounts in INR:")
    cols = [c["fieldname"] for c in rep.get("columns", []) if c["fieldname"] not in ("account", "account_name", "currency")]
    for row in rep.get("result", []):
        if not row or not row.get("account"):
            continue
        if row.get("indent", 9) <= 1 or "Total" in str(row.get("account_name", "")) or "Provisional" in str(row.get("account_name", "")):
            vals = ", ".join(_num(row.get(c)) for c in cols)
            print(f"     {'  ' * int(row.get('indent', 0))}{row.get('account_name') or row.get('account')}: {vals}")


STEPS: dict[str, tuple[Callable[[Context], None], str]] = {
    "company": (step_company, "fiscal years and the company (India chart of accounts, perpetual inventory)"),
    "currencies": (step_currencies, "enable currencies and load twice-monthly exchange rates to INR"),
    "reference": (step_reference, "units, warehouse types, supplier/customer groups, territories, tax categories, finance books, banks, payment terms, holidays, price lists"),
    "accounts": (step_accounts, "extend the chart of accounts (GST, TDS, duties, freight, foreign-currency parties, banks, income, expenses)"),
    "cost_centers": (step_cost_centers, "cost centres, with profit centres as a branch of the same tree"),
    "banks": (step_banks, "company bank accounts linked to their ledger accounts (INR and foreign currency)"),
    "withholding": (step_withholding, "TDS categories (194C, 194J, 194Q, 194H, 195) with rates by group and financial year"),
    "tax_setup": (step_tax_setup, "item tax templates, purchase/sales tax templates (duties, freight, insurance, GST) and Tax Rules by category"),
    "shipping": (step_shipping, "shipping routes as Shipping Rules (weight bands, transit days, mode, Incoterm) plus custom route fields"),
    "warehouses": (step_warehouses, "15 warehouses by type (stores, bonded, transit, dispatch, quarantine, scrap, returns ...)"),
    "item_groups": (step_item_groups, "8 item groups with default warehouse, cost centre and accounts"),
    "customs": (step_customs, "customs tariff (HS) numbers used by the items"),
    "suppliers": (step_suppliers, "50 vendors worldwide with currency, tax category, terms, withholding and addresses"),
    "customers": (step_customers, "50 customers (20 India incl. 2 SEZ, 30 export) with credit limits and addresses"),
    "items": (step_items, "200 items (192 goods, 8 services) with customs codes, tax profiles, weights, lead times and supplier links"),
    "prices": (step_prices, "buying prices per vendor in its currency and selling prices in every customer currency"),
    "finish": (step_finish, "payment modes to bank accounts, company defaults, and the opening capital journal entry on 1 Nov 2025"),
    "verify": (step_verify, "read-only: counts, ledger balance and ERPNext's own balance sheet"),
}
