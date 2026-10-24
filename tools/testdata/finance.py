"""Finance and tax setup for the test company: accounts, cost/profit centres, banks, tax categories and templates, withholding, terms.

ILLUSTRATIVE rates (test values, not tax advice). Account names are given WITHOUT the company suffix; the loader adds " - TTC".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from . import catalog, world

# ------------------------------------------------------------------ accounts: (name, parent, root_type, account_type, currency, is_group)
A = tuple
ACCOUNTS: list[A] = []


def _acc(name: str, parent: str, root: str, typ: str = "", ccy: str = "", group: bool = False) -> None:
    ACCOUNTS.append((name, parent, root, typ, ccy, group))


# Assets
for n in ("Input CGST", "Input SGST", "Input IGST", "Input IGST on Imports", "TDS Receivable"):
    _acc(n, "Tax Assets", "Asset", "Tax")
_acc("Goods In Transit (Stock)", "Stock Assets", "Asset", "Stock")
_acc("Advances to Suppliers", "Loans and Advances (Assets)", "Asset")
_acc("Staff Advances", "Loans and Advances (Assets)", "Asset")
BANK_ACCOUNTS = [  # (GL account, bank, account holder label, currency, branch/IFSC prefix)
    ("HDFC Bank Current A/c", "HDFC Bank", "INR", "HDFC0000001"), ("ICICI Bank Collection A/c", "ICICI Bank", "INR", "ICIC0000002"),
    ("Axis Bank Operations A/c", "Axis Bank", "INR", "UTIB0000003"), ("Kotak Bank Current A/c", "Kotak Mahindra Bank", "INR", "KKBK0000004"),
    ("SBI EEFC A/c (USD)", "State Bank of India", "USD", "SBIN0000005"), ("HSBC EEFC A/c (EUR)", "HSBC India", "EUR", "HSBC0000006"),
    ("Standard Chartered A/c (GBP)", "Standard Chartered Bank India", "GBP", "SCBL0000007"), ("Citibank Multi-currency A/c (USD)", "Citibank India", "USD", "CITI0000008"),
]
for gl, _bank, ccy, _ifsc in BANK_ACCOUNTS:
    _acc(gl, "Bank Accounts", "Asset", "Bank", "" if ccy == "INR" else ccy)
RECEIVABLE_CCY = ["USD", "EUR", "GBP", "AED", "AUD", "SGD", "CAD", "ZAR"]
PAYABLE_CCY = ["USD", "EUR", "CNY", "JPY", "GBP", "SGD", "AED"]
for c in RECEIVABLE_CCY:
    _acc(f"Debtors ({c})", "Accounts Receivable", "Asset", "Receivable", c)
for c in PAYABLE_CCY:
    _acc(f"Creditors ({c})", "Accounts Payable", "Liability", "Payable", c)
# Liabilities and equity
for n in ("Output CGST", "Output SGST", "Output IGST", "TDS Payable 194C", "TDS Payable 194J", "TDS Payable 194Q", "TDS Payable 194H", "TDS Payable 195", "TCS Payable"):
    _acc(n, "Duties and Taxes", "Liability", "Tax")
_acc("Share Capital", "Capital Account", "Equity", "Equity")
_acc("Securities Premium", "Capital Account", "Equity", "Equity")
# Import duties and landed costs (expenses that become part of what the goods cost)
_acc("Import Duties & Landed Costs", "Direct Expenses", "Expense", "", group=True)
for n in ("Basic Customs Duty - MFN", "Basic Customs Duty - FTA", "Social Welfare Surcharge", "Anti-dumping Duty", "Inbound Freight - Sea & Air",
          "Marine Insurance - Inbound", "Port Handling & CHA Charges", "Inland Transport - Inbound"):
    _acc(n, "Import Duties & Landed Costs", "Expense", "Chargeable")
for n in ("Outbound Freight", "Warehouse Rent", "Salaries & Wages", "Bank Charges", "Professional Fees", "Stock Insurance", "Packing Expenses", "Interest on Loans", "Customs Compliance Fees"):
    _acc(n, "Indirect Expenses", "Expense", "Chargeable" if n == "Outbound Freight" else "")
# Income
for n in ("Export Sales", "Domestic Sales", "Freight Recovered", "Insurance Recovered", "Destination Duties Recovered (DDP)"):
    _acc(n, "Direct Income", "Income", "Income Account")
for n in ("Interest Income", "Scrap Sales"):
    _acc(n, "Indirect Income", "Income", "Income Account")

# ------------------------------------------------------------------ cost centres, with profit centres as a branch of the same tree
PROFIT_CENTRES = ["Industrial Products", "Consumer Products", "Electronics Division", "Packaging Division", "Export Business", "Domestic Trading"]
COST_CENTRES: list[tuple[str, str]] = [  # (name, parent) - parent "" means the "Cost Centers" branch
    ("Corporate & Admin", ""), ("Finance & Accounts", ""), ("Procurement & Imports", ""), ("Logistics & Warehousing", ""), ("Quality & Compliance", ""),
    ("Sales & Marketing", ""), ("IT & Systems", ""), ("HR & Facilities", ""),
    ("Inbound Logistics", "Logistics & Warehousing"), ("Outbound Logistics", "Logistics & Warehousing"), ("Customs & Compliance", "Logistics & Warehousing"),
    ("Domestic Sales", "Sales & Marketing"), ("Export Sales", "Sales & Marketing"),
]
GROUP_COST_CENTRE = {"FAS": "Industrial Products", "MET": "Industrial Products", "ELE": "Electronics Division", "MEC": "Industrial Products", "CHM": "Domestic Trading",
                     "PKG": "Packaging Division", "FGI": "Industrial Products", "FGC": "Consumer Products", "SVC": "Domestic Trading"}

# ------------------------------------------------------------------ banks, finance books
BANKS = ["HDFC Bank", "ICICI Bank", "Axis Bank", "Kotak Mahindra Bank", "State Bank of India", "HSBC India", "Standard Chartered Bank India", "Citibank India"]
FINANCE_BOOKS = ["Indian GAAP (Statutory)", "IFRS Reporting", "Income Tax Books", "Management Accounts"]

# ------------------------------------------------------------------ tax categories
IMPORT_CATEGORIES = sorted({c.tax_category for c in world.IMPORT_COUNTRIES.values()})
EXPORT_CATEGORIES = sorted({c.tax_category for c in world.EXPORT_COUNTRIES.values()})
TAX_CATEGORIES = [world.DOMESTIC_PURCHASE_INTRA, world.DOMESTIC_PURCHASE_INTER, *IMPORT_CATEGORIES,
                  world.DOMESTIC_SALE_INTRA, world.DOMESTIC_SALE_INTER, world.SEZ_SALE, *EXPORT_CATEGORIES]


# ------------------------------------------------------------------ item tax templates (one per HS code + GST rate)
def item_tax_profiles() -> dict[str, dict[str, float]]:
    """{profile name: {account name: rate %}} for every distinct customs code + GST rate in the catalogue."""
    out: dict[str, dict[str, float]] = {}
    for it in catalog.build_items():
        if not it.stock or it.profile in out:
            continue
        g = it.gst
        out[it.profile] = {
            "Input CGST": g / 2, "Input SGST": g / 2, "Input IGST": g, "Input IGST on Imports": g,
            "Output CGST": g / 2, "Output SGST": g / 2, "Output IGST": g,
            "Basic Customs Duty - MFN": it.bcd_mfn, "Basic Customs Duty - FTA": it.bcd_fta, "Anti-dumping Duty": it.add_cn,
        }
    return out


# ------------------------------------------------------------------ purchase taxes templates
@dataclass(frozen=True)
class Row:
    description: str
    charge_type: str
    account: str
    rate: float = 0.0
    category: str = "Total"
    add_deduct: str = "Add"
    row_id: Optional[int] = None
    amount: float = 0.0


PORT_HANDLING = {"mfn": 14500.0, "mfn_ad": 15500.0, "fta_asean": 13000.0, "fta_cepa": 13500.0, "fta_uae": 9500.0}
DEFAULT_BCD = {"mfn": 10.0, "mfn_ad": 10.0, "fta_asean": 5.0, "fta_cepa": 5.0, "fta_uae": 5.0}


def import_template(category: str) -> tuple[str, list[Row]]:
    """The purchase taxes template for one import tax category: freight and insurance estimates, customs duties, IGST, port handling."""
    countries = [c for c in world.IMPORT_COUNTRIES.values() if c.tax_category == category]
    c0 = countries[0]
    fta = c0.import_regime.startswith("fta")
    freight = max(c.freight_pct for c in countries)
    insurance = max(c.insurance_pct for c in countries)
    bcd_acc = "Basic Customs Duty - FTA" if fta else "Basic Customs Duty - MFN"
    rows = [
        Row(f"Inbound freight, sea/air (est. {freight:g}% of goods)", "On Net Total", "Inbound Freight - Sea & Air", freight, "Valuation"),
        Row(f"Marine insurance (est. {insurance:g}% of goods)", "On Net Total", "Marine Insurance - Inbound", insurance, "Valuation"),
        Row("Basic Customs Duty (on goods + freight + insurance)", "On Previous Row Total", bcd_acc, DEFAULT_BCD[c0.import_regime], "Valuation", row_id=2),
        Row("Social Welfare Surcharge (10% of BCD)", "On Previous Row Amount", "Social Welfare Surcharge", 10.0, "Valuation", row_id=3),
    ]
    last_duty = 4
    if c0.import_regime == "mfn_ad":
        rows.append(Row("Anti-dumping duty (on goods + freight + insurance)", "On Previous Row Total", "Anti-dumping Duty", 8.0, "Valuation", row_id=2))
        last_duty = 5
    rows.append(Row("IGST on imports (creditable)", "On Previous Row Total", "Input IGST on Imports", 18.0, "Total", row_id=last_duty))
    rows.append(Row("Port handling & CHA charges (per consignment)", "Actual", "Port Handling & CHA Charges", 0.0, "Valuation", amount=PORT_HANDLING[c0.import_regime]))
    return f"{category}", rows


def domestic_purchase_templates() -> list[tuple[str, list[Row]]]:
    return [
        (world.DOMESTIC_PURCHASE_INTRA, [Row("Input CGST", "On Net Total", "Input CGST", 9.0), Row("Input SGST", "On Net Total", "Input SGST", 9.0)]),
        (world.DOMESTIC_PURCHASE_INTER, [Row("Input IGST", "On Net Total", "Input IGST", 18.0)]),
    ]


# ------------------------------------------------------------------ sales taxes templates
DESTINATION_DUTY = {"Export - North America (DDP)": 5.0, "Export - Middle East (DDP)": 5.0, "Export - Oceania (DDP)": 5.0}


def sales_templates() -> list[tuple[str, list[Row]]]:
    out: list[tuple[str, list[Row]]] = [
        (world.DOMESTIC_SALE_INTRA, [Row("Output CGST", "On Net Total", "Output CGST", 9.0), Row("Output SGST", "On Net Total", "Output SGST", 9.0)]),
        (world.DOMESTIC_SALE_INTER, [Row("Output IGST", "On Net Total", "Output IGST", 18.0)]),
        (world.SEZ_SALE, [Row("IGST (zero-rated supply to SEZ under LUT)", "On Net Total", "Output IGST", 0.0)]),
    ]
    for cat in EXPORT_CATEGORIES:
        cs = [c for c in world.EXPORT_COUNTRIES.values() if c.tax_category == cat]
        freight, ins = max(c.freight_pct for c in cs), max(c.insurance_pct for c in cs)
        rows = [Row(f"Freight recovered (est. {freight:g}% of goods)", "On Net Total", "Freight Recovered", freight),
                Row(f"Insurance recovered (est. {ins:g}% of goods)", "On Net Total", "Insurance Recovered", ins)]
        if cat in DESTINATION_DUTY:
            rows.append(Row("Destination duties & taxes prepaid (DDP)", "On Net Total", "Destination Duties Recovered (DDP)", DESTINATION_DUTY[cat]))
        rows.append(Row("IGST (zero-rated export under LUT)", "On Net Total", "Output IGST", 0.0))
        out.append((cat, rows))
    return out


# ------------------------------------------------------------------ tax withholding (TDS)
FY_RATE_WINDOWS = [("2025-04-01", "2026-03-31"), ("2026-04-01", "2027-03-31")]
WITHHOLDING_GROUPS = ["Company", "Individual / HUF", "Partnership / Firm", "Non-Resident"]


@dataclass(frozen=True)
class Withholding:
    name: str
    account: str
    rates: dict[str, float]          # group -> %
    single: float
    cumulative: float


WITHHOLDING = [
    Withholding("TDS 194C - Contractors & Transport", "TDS Payable 194C", {"Company": 2.0, "Individual / HUF": 1.0, "Partnership / Firm": 2.0}, 30_000, 100_000),
    Withholding("TDS 194J - Professional & Technical Fees", "TDS Payable 194J", {"Company": 10.0, "Individual / HUF": 10.0, "Partnership / Firm": 10.0}, 50_000, 0),
    Withholding("TDS 194Q - Purchase of Goods", "TDS Payable 194Q", {"Company": 0.1, "Individual / HUF": 0.1, "Partnership / Firm": 0.1}, 0, 5_000_000),
    Withholding("TDS 194H - Commission & Brokerage", "TDS Payable 194H", {"Company": 2.0, "Individual / HUF": 2.0, "Partnership / Firm": 2.0}, 20_000, 0),
    Withholding("TDS 195 - Payments to Non-Residents", "TDS Payable 195", {"Non-Resident": 20.0}, 0, 0),
]
SERVICE_WITHHOLDING = {"transport": "TDS 194C - Contractors & Transport", "cha": "TDS 194C - Contractors & Transport", "warehouse": "TDS 194C - Contractors & Transport",
                       "calibration": "TDS 194J - Professional & Technical Fees", "insurance": "", "forwarding": ""}
LEGAL_GROUP = {"Company": "Company", "Partnership": "Partnership / Firm", "Individual": "Individual / HUF"}

# ------------------------------------------------------------------ payment terms
PAYMENT_TERMS = [  # (term name, portion %, credit days, based on)
    ("Net 15", 100, 15), ("Net 30", 100, 30), ("Net 45", 100, 45), ("Net 60", 100, 60), ("Net 90", 100, 90),
    ("Advance 30%", 30, 0), ("Balance 70% on Delivery", 70, 7), ("Advance 50%", 50, 0), ("Balance 50% Net 30", 50, 30), ("LC 60 Days", 100, 60),
]
PAYMENT_TEMPLATES = {
    "Net 15": [("Net 15", 100)], "Net 30": [("Net 30", 100)], "Net 45": [("Net 45", 100)], "Net 60": [("Net 60", 100)], "Net 90": [("Net 90", 100)],
    "30% Advance + 70% on Delivery": [("Advance 30%", 30), ("Balance 70% on Delivery", 70)],
    "50% Advance + 50% Net 30": [("Advance 50%", 50), ("Balance 50% Net 30", 50)], "LC at 60 Days": [("LC 60 Days", 100)],
}


def vendor_terms(v: world.Vendor) -> str:
    if v.kind == world.SERVICE:
        return "Net 15" if v.country == "India" else "Net 30"
    if v.country == "India":
        return ("Net 30", "Net 45", "Net 60")[hash_idx(v.name, 3)]
    return ("LC at 60 Days", "30% Advance + 70% on Delivery", "Net 60", "50% Advance + 50% Net 30")[hash_idx(v.name, 4)]


def customer_terms(c: world.Customer) -> str:
    if c.country == "India":
        return ("Net 30", "Net 45", "Net 60", "Net 15")[hash_idx(c.name, 4)] if c.kind != "retail" else "Net 15"
    return ("Net 45", "Net 60", "LC at 60 Days", "50% Advance + 50% Net 30", "Net 90")[hash_idx(c.name, 5)]


def hash_idx(text: str, n: int) -> int:
    return sum(ord(ch) * (i + 1) for i, ch in enumerate(text)) % n


# ------------------------------------------------------------------ warehouses
WAREHOUSE_TYPES = ["Stores", "Bonded", "Finished Goods", "Quarantine", "Scrap", "Returns", "Dispatch", "Packing"]
# (name without suffix, warehouse type). The first four exist already; their types are set.
WAREHOUSES = [
    ("Stores", "Stores"), ("Finished Goods", "Finished Goods"), ("Goods In Transit", "Transit"), ("Work In Progress", "Packing"),
    ("Nhava Sheva Bonded Warehouse", "Bonded"), ("Chennai Port Bonded Warehouse", "Bonded"), ("Delhi NCR Distribution Center", "Stores"),
    ("Bengaluru Distribution Center", "Stores"), ("Kolkata Distribution Center", "Stores"), ("Ahmedabad Regional Store", "Stores"),
    ("Pune Packing & Kitting Unit", "Packing"), ("Quality Hold - Quarantine", "Quarantine"), ("Rejected & Scrap", "Scrap"),
    ("Customer Returns", "Returns"), ("Export Dispatch Dock (Nhava Sheva)", "Dispatch"),
]

# ------------------------------------------------------------------ shipping routes
INDIA_PORT = {"sea_west": "Nhava Sheva (JNPT)", "sea_south": "Chennai", "air_west": "Mumbai (BOM)"}
SEA_RATE_PER_KG = {"China": 14, "Vietnam": 12, "Thailand": 11, "Singapore": 10, "Korea, Republic of": 13, "Japan": 15, "Taiwan": 13, "United Arab Emirates": 6,
                   "Germany": 20, "Italy": 19, "Netherlands": 19, "United Kingdom": 21, "Türkiye": 17, "United States": 28, "Brazil": 30,
                   "Canada": 26, "Saudi Arabia": 8, "Australia": 17, "Kenya": 12, "Nigeria": 18, "South Africa": 14}
AIR_RATE_PER_KG = {"China": 260, "Germany": 340, "United States": 420, "Japan": 300}
WEIGHT_BANDS = [(0, 500), (500, 2000), (2000, 10000), (10000, 50000), (50000, 1_000_000)]
SOUTH_PORT_COUNTRIES = {"China", "Vietnam", "Thailand", "Singapore", "Korea, Republic of", "Japan", "Taiwan"}
DOMESTIC_LANES = [("Mumbai", "Delhi NCR", 3, 9.0), ("Mumbai", "Bengaluru", 3, 8.0), ("Mumbai", "Kolkata", 5, 11.0), ("Mumbai", "Ahmedabad", 1, 5.0),
                  ("Chennai", "Bengaluru", 1, 4.0), ("Nhava Sheva", "Mumbai DC", 1, 3.0)]


@dataclass(frozen=True)
class Route:
    label: str
    kind: str                         # Buying | Selling
    origin: str
    destination: str
    mode: str
    transit_days: int
    incoterm: str
    countries: tuple[str, ...]
    per_kg: float
    base_fee: float


def routes() -> list[Route]:
    out: list[Route] = []
    for name, c in world.IMPORT_COUNTRIES.items():
        dest = INDIA_PORT["sea_south"] if name in SOUTH_PORT_COUNTRIES else INDIA_PORT["sea_west"]
        out.append(Route(f"Sea: {c.port} to {dest}", "Buying", c.port, dest, "Sea", c.transit_days, "CIF" if name != "United Arab Emirates" else "CFR",
                         (name,), SEA_RATE_PER_KG[name], 6500))
    for name, rate in AIR_RATE_PER_KG.items():
        c = world.IMPORT_COUNTRIES[name]
        out.append(Route(f"Air: {c.port} to {INDIA_PORT['air_west']}", "Buying", c.port, INDIA_PORT["air_west"], "Air", 4, "CPT", (name,), rate, 4500))
    for name, c in world.EXPORT_COUNTRIES.items():
        incoterm = "DDP" if c.tax_category.endswith("(DDP)") else "CIF"
        out.append(Route(f"Sea: Nhava Sheva to {c.port}", "Selling", "Nhava Sheva (JNPT)", c.port, "Sea", c.transit_days, incoterm, (name,), SEA_RATE_PER_KG[name], 7500))
    for a, b, days, rate in DOMESTIC_LANES:
        out.append(Route(f"Road: {a} to {b}", "Selling", a, b, "Road", days, "EXW", ("India",), rate, 2500))
    return out


def route_conditions(r: Route) -> list[dict]:
    """Net-weight bands (kg) with a shipping amount (INR) for each: a base fee plus a per-kg rate on the band's heavier end."""
    return [{"from_value": lo, "to_value": hi if hi < 1_000_000 else 0, "shipping_amount": round(r.base_fee + r.per_kg * min(hi, 50_000), 0)} for lo, hi in WEIGHT_BANDS]


CUSTOM_FIELDS = [
    ("custom_route_origin", "Route Origin", "Data", None), ("custom_route_destination", "Route Destination", "Data", None),
    ("custom_transport_mode", "Transport Mode", "Select", "Sea\nAir\nRoad"), ("custom_transit_days", "Transit Days", "Int", None),
    ("custom_incoterm", "Incoterm", "Link", "Incoterm"),
]

HOLIDAYS_2025_26 = [("2025-08-15", "Independence Day"), ("2025-08-27", "Ganesh Chaturthi"), ("2025-10-02", "Gandhi Jayanti / Dussehra"), ("2025-10-20", "Diwali (Laxmi Puja)"),
                    ("2025-10-22", "Diwali (Balipratipada)"), ("2025-11-05", "Guru Nanak Jayanti"), ("2025-12-25", "Christmas"), ("2026-01-26", "Republic Day"),
                    ("2026-03-04", "Holi"), ("2026-03-20", "Eid-ul-Fitr")]
HOLIDAYS_2026_27 = [("2026-04-03", "Good Friday"), ("2026-05-01", "Maharashtra Day"), ("2026-08-15", "Independence Day"), ("2026-09-14", "Ganesh Chaturthi"),
                    ("2026-10-02", "Gandhi Jayanti"), ("2026-10-20", "Dussehra"), ("2026-11-08", "Diwali (Laxmi Puja)"), ("2026-12-25", "Christmas")]
