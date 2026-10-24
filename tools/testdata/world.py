"""Reference data for the test company: who we are, when, where our vendors and customers are, and how goods travel.

Everything here is FICTIONAL and ILLUSTRATIVE: company names are invented, and duty / freight / insurance rates are test
values chosen to look realistic, not legal or tariff advice. Generation is deterministic (fixed seeds), so every run
produces the same data.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

COMPANY = {"name": "Test Trading Co", "abbr": "TTC", "country": "India", "currency": "INR", "state": "Maharashtra", "city": "Mumbai"}

# The last 12 months, ending yesterday so that nothing is dated in the future.
PERIOD_START = date(2025, 11, 1)
PERIOD_END = date(2026, 10, 17)

FISCAL_YEARS = [
    {"year": "2025-2026", "year_start_date": "2025-04-01", "year_end_date": "2026-03-31"},
]

# ------------------------------------------------------------------ currencies
FOREIGN_CURRENCIES = ["USD", "EUR", "CNY", "JPY", "GBP", "SGD", "AED", "AUD", "CAD", "ZAR"]
# Illustrative INR per 1 unit on 1 Oct 2025.
FX_BASE = {"USD": 88.0, "EUR": 102.5, "CNY": 12.35, "JPY": 0.585, "GBP": 118.0, "SGD": 68.5, "AED": 23.95, "AUD": 58.0, "CAD": 63.5, "ZAR": 4.95}
FX_DRIFT = {"USD": 0.0030, "EUR": 0.0015, "CNY": 0.0025, "JPY": -0.0010, "GBP": 0.0015, "SGD": 0.0025, "AED": 0.0030, "AUD": 0.0010, "CAD": 0.0020, "ZAR": 0.0010}
FX_VOL = {"USD": 0.004, "EUR": 0.007, "CNY": 0.004, "JPY": 0.008, "GBP": 0.008, "SGD": 0.005, "AED": 0.004, "AUD": 0.009, "CAD": 0.007, "ZAR": 0.012}


def fx_dates() -> list[date]:
    """The 1st and 16th of every month from Oct 2025 until the end of the period."""
    out, d = [], date(2025, 10, 1)
    while d <= PERIOD_END:
        for day in (1, 16):
            x = date(d.year, d.month, day)
            if x <= PERIOD_END:
                out.append(x)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def fx_series() -> dict[str, dict[date, float]]:
    """{currency: {date: INR per unit}}: a seeded random walk with a mild trend, so rates move like real ones."""
    out: dict[str, dict[date, float]] = {}
    for ccy in FOREIGN_CURRENCIES:
        rng = random.Random(f"fx-{ccy}")
        rate, series = FX_BASE[ccy], {}
        for d in fx_dates():
            series[d] = round(rate, 4 if rate < 1 else 3)
            rate *= 1 + FX_DRIFT[ccy] / 2 + rng.gauss(0, FX_VOL[ccy])
        out[ccy] = series
    return out


def fx_on(series: dict[str, dict[date, float]], ccy: str, day: date) -> float:
    """The latest rate on or before `day` (what ERPNext uses)."""
    if ccy == "INR":
        return 1.0
    s = series[ccy]
    return s[max(d for d in s if d <= day)] if any(d <= day for d in s) else s[min(s)]


# ------------------------------------------------------------------ geography: how goods move between us and each country
@dataclass(frozen=True)
class Country:
    name: str                      # exactly as ERPNext names the country
    code: str
    currency: str
    region: str                    # for territories and freight/insurance levels
    port: str                      # the port or airport goods leave from / arrive at
    mode: str                      # Sea | Air | Road
    transit_days: int
    tax_category: str              # purchase side (import) or sales side (export), see finance.py
    import_regime: str = ""        # mfn | mfn_ad | fta_asean | fta_cepa | fta_uae
    freight_pct: float = 0.0       # inbound sea/air freight as % of goods value (illustrative)
    insurance_pct: float = 0.0     # marine insurance as % of goods value (illustrative)


# Inbound (we import FROM these)
IMPORT_COUNTRIES = {
    "China": Country("China", "CN", "CNY", "Asia Pacific", "Shanghai", "Sea", 32, "Import - China (MFN + anti-dumping)", "mfn_ad", 3.0, 0.25),
    "Vietnam": Country("Vietnam", "VN", "USD", "Asia Pacific", "Ho Chi Minh City", "Sea", 24, "Import - ASEAN (FTA)", "fta_asean", 3.0, 0.25),
    "Thailand": Country("Thailand", "TH", "USD", "Asia Pacific", "Laem Chabang", "Sea", 22, "Import - ASEAN (FTA)", "fta_asean", 3.0, 0.25),
    "Singapore": Country("Singapore", "SG", "SGD", "Asia Pacific", "Singapore", "Sea", 14, "Import - ASEAN (FTA)", "fta_asean", 2.5, 0.20),
    "Korea, Republic of": Country("Korea, Republic of", "KR", "USD", "Asia Pacific", "Busan", "Sea", 28, "Import - Korea & Japan (CEPA)", "fta_cepa", 3.0, 0.25),
    "Japan": Country("Japan", "JP", "JPY", "Asia Pacific", "Yokohama", "Sea", 30, "Import - Korea & Japan (CEPA)", "fta_cepa", 3.5, 0.25),
    "Taiwan": Country("Taiwan", "TW", "USD", "Asia Pacific", "Kaohsiung", "Sea", 26, "Import - Taiwan (MFN)", "mfn", 3.0, 0.25),
    "United Arab Emirates": Country("United Arab Emirates", "AE", "AED", "Middle East", "Jebel Ali", "Sea", 7, "Import - UAE (CEPA)", "fta_uae", 2.0, 0.20),
    "Germany": Country("Germany", "DE", "EUR", "Europe", "Hamburg", "Sea", 28, "Import - EU (MFN)", "mfn", 4.5, 0.30),
    "Italy": Country("Italy", "IT", "EUR", "Europe", "Genoa", "Sea", 24, "Import - EU (MFN)", "mfn", 4.5, 0.30),
    "Netherlands": Country("Netherlands", "NL", "EUR", "Europe", "Rotterdam", "Sea", 27, "Import - EU (MFN)", "mfn", 4.5, 0.30),
    "United Kingdom": Country("United Kingdom", "GB", "GBP", "Europe", "Felixstowe", "Sea", 30, "Import - UK (MFN)", "mfn", 4.5, 0.30),
    "Türkiye": Country("Türkiye", "TR", "USD", "Europe", "Mersin", "Sea", 20, "Import - Türkiye (MFN)", "mfn", 4.0, 0.30),
    "United States": Country("United States", "US", "USD", "North America", "Houston", "Sea", 42, "Import - USA (MFN)", "mfn", 6.0, 0.40),
    "Brazil": Country("Brazil", "BR", "USD", "South America", "Santos", "Sea", 40, "Import - Brazil (MFN)", "mfn", 6.0, 0.40),
}

# Outbound (we export TO these). Export sales are zero-rated; freight/insurance are billed according to the Incoterm.
EXPORT_COUNTRIES = {
    "United States": Country("United States", "US", "USD", "North America", "Houston", "Sea", 42, "Export - North America (DDP)", "", 6.0, 0.40),
    "Canada": Country("Canada", "CA", "CAD", "North America", "Vancouver", "Sea", 38, "Export - North America (CIF)", "", 6.0, 0.40),
    "United Kingdom": Country("United Kingdom", "GB", "GBP", "Europe", "Felixstowe", "Sea", 30, "Export - Europe (CIF)", "", 4.5, 0.30),
    "Germany": Country("Germany", "DE", "EUR", "Europe", "Hamburg", "Sea", 28, "Export - Europe (CIF)", "", 4.5, 0.30),
    "Netherlands": Country("Netherlands", "NL", "EUR", "Europe", "Rotterdam", "Sea", 27, "Export - Europe (CIF)", "", 4.5, 0.30),
    "United Arab Emirates": Country("United Arab Emirates", "AE", "AED", "Middle East", "Jebel Ali", "Sea", 7, "Export - Middle East (DDP)", "", 2.0, 0.20),
    "Saudi Arabia": Country("Saudi Arabia", "SA", "USD", "Middle East", "Dammam", "Sea", 12, "Export - Middle East (CIF)", "", 2.5, 0.25),
    "Singapore": Country("Singapore", "SG", "SGD", "Asia Pacific", "Singapore", "Sea", 14, "Export - Asia Pacific (CIF)", "", 2.5, 0.20),
    "Australia": Country("Australia", "AU", "AUD", "Oceania", "Sydney", "Sea", 24, "Export - Oceania (DDP)", "", 4.0, 0.30),
    "Kenya": Country("Kenya", "KE", "USD", "Africa", "Mombasa", "Sea", 16, "Export - Africa (CIF)", "", 5.0, 0.50),
    "Nigeria": Country("Nigeria", "NG", "USD", "Africa", "Lagos", "Sea", 34, "Export - Africa (CIF)", "", 6.0, 0.60),
    "South Africa": Country("South Africa", "ZA", "ZAR", "Africa", "Durban", "Sea", 22, "Export - Africa (CIF)", "", 5.0, 0.50),
}

# Indian states we deal with: tax category depends on whether the state is our own (intra-state GST) or not (inter-state IGST).
HOME_STATE = COMPANY["state"]
STATE_ZONE = {"Maharashtra": "West India", "Gujarat": "West India", "Rajasthan": "North India", "Delhi": "North India", "Haryana": "North India", "Punjab": "North India",
              "Uttar Pradesh": "North India", "Karnataka": "South India", "Tamil Nadu": "South India", "Telangana": "South India", "Kerala": "South India",
              "West Bengal": "East India"}
DOMESTIC_PURCHASE_INTRA, DOMESTIC_PURCHASE_INTER = "Domestic Purchase - Intra-state", "Domestic Purchase - Inter-state"
DOMESTIC_SALE_INTRA, DOMESTIC_SALE_INTER, SEZ_SALE = "Domestic Sale - Intra-state", "Domestic Sale - Inter-state", "SEZ Supply (zero rated)"


# ------------------------------------------------------------------ vendors (50)
GOODS, SERVICE = "goods", "service"


@dataclass(frozen=True)
class Vendor:
    name: str
    country: str
    city: str
    state: str = ""                # Indian state, else ""
    kind: str = GOODS
    supplies: tuple[str, ...] = ()  # item-group codes (goods) or service type
    service: str = ""               # transport | cha | warehouse | insurance | calibration | forwarding
    legal: str = "Company"          # Company | Partnership | Individual (decides TDS rate)


V = Vendor
VENDORS: list[Vendor] = [
    # ---- India, goods (10)
    V("Bharat Precision Fasteners Pvt Ltd", "India", "Pune", "Maharashtra", supplies=("FAS", "MEC")),
    V("Gujarat Steel & Alloys Ltd", "India", "Ahmedabad", "Gujarat", supplies=("MET",)),
    V("Chennai Electro Components Pvt Ltd", "India", "Chennai", "Tamil Nadu", supplies=("ELE",)),
    V("Rajkot Bearings & Gears Pvt Ltd", "India", "Rajkot", "Gujarat", supplies=("MEC",)),
    V("Noida Packaging Solutions Pvt Ltd", "India", "Noida", "Uttar Pradesh", supplies=("PKG",)),
    V("Kolkata Chemicals & Coatings Ltd", "India", "Kolkata", "West Bengal", supplies=("CHM",)),
    V("Hyderabad Industrial Pumps Pvt Ltd", "India", "Hyderabad", "Telangana", supplies=("MEC", "FGI")),
    V("Bengaluru Tooling Works Pvt Ltd", "India", "Bengaluru", "Karnataka", supplies=("FGC", "FAS")),
    V("Mumbai Metal Traders", "India", "Mumbai", "Maharashtra", supplies=("MET", "FAS"), legal="Partnership"),
    V("Ludhiana Hardware Industries Pvt Ltd", "India", "Ludhiana", "Punjab", supplies=("FAS", "FGC")),
    # ---- India, services (7)
    V("Konkan Roadways & Logistics Pvt Ltd", "India", "Mumbai", "Maharashtra", SERVICE, service="transport"),
    V("Deccan Transporters", "India", "Hyderabad", "Telangana", SERVICE, service="transport", legal="Partnership"),
    V("Western Clearing & Forwarding Pvt Ltd", "India", "Mumbai", "Maharashtra", SERVICE, service="cha"),
    V("Coastal Customs House Agents", "India", "Chennai", "Tamil Nadu", SERVICE, service="cha", legal="Partnership"),
    V("SafeStore Warehousing Pvt Ltd", "India", "Bhiwandi", "Maharashtra", SERVICE, service="warehouse"),
    V("Marine Shield Insurance Brokers Pvt Ltd", "India", "Mumbai", "Maharashtra", SERVICE, service="insurance"),
    V("Precision Calibration Labs", "India", "Pune", "Maharashtra", SERVICE, service="calibration", legal="Individual"),
    # ---- China (7)
    V("Shenzhen Brightway Electronics Co Ltd", "China", "Shenzhen", supplies=("ELE",)),
    V("Ningbo Hengda Fasteners Co Ltd", "China", "Ningbo", supplies=("FAS",)),
    V("Tianjin Ironbridge Steel Co Ltd", "China", "Tianjin", supplies=("MET",)),
    V("Guangzhou Pearl Packaging Co Ltd", "China", "Guangzhou", supplies=("PKG",)),
    V("Wuxi Precision Bearings Co Ltd", "China", "Wuxi", supplies=("MEC",)),
    V("Dongguan Everlight Appliances Co Ltd", "China", "Dongguan", supplies=("FGC",)),
    V("Shanghai Meridian Pumps & Valves Co Ltd", "China", "Shanghai", supplies=("FGI", "MEC")),
    # ---- Vietnam (3), Thailand (1)
    V("Saigon Fastener Industries Co Ltd", "Vietnam", "Ho Chi Minh City", supplies=("FAS",)),
    V("Hanoi Electro Parts JSC", "Vietnam", "Hanoi", supplies=("ELE",)),
    V("Danang Packaging JSC", "Vietnam", "Da Nang", supplies=("PKG",)),
    V("Bangkok Polymer Packaging Co Ltd", "Thailand", "Bangkok", supplies=("PKG",)),
    # ---- Germany (3), Italy (1)
    V("Rheinland Präzision GmbH", "Germany", "Düsseldorf", supplies=("MEC",)),
    V("Stuttgart Antriebstechnik GmbH", "Germany", "Stuttgart", supplies=("ELE", "FGI")),
    V("Hamburg Chemie Handels GmbH", "Germany", "Hamburg", supplies=("CHM",)),
    V("Torino Meccanica S.p.A.", "Italy", "Turin", supplies=("MEC", "FGI")),
    # ---- USA (4)
    V("Houston Industrial Supply LLC", "United States", "Houston", supplies=("MET", "CHM")),
    V("Ohio Valve & Control Inc", "United States", "Cleveland", supplies=("MEC",)),
    V("Silicon Valley Components Inc", "United States", "San Jose", supplies=("ELE",)),
    V("Midwest Tooling Corp", "United States", "Chicago", supplies=("FGC", "FGI")),
    # ---- Japan (3), Korea (2)
    V("Osaka Seiko Bearings K.K.", "Japan", "Osaka", supplies=("MEC",)),
    V("Nagoya Electric Motors K.K.", "Japan", "Nagoya", supplies=("ELE",)),
    V("Yokohama Fine Chemicals K.K.", "Japan", "Yokohama", supplies=("CHM",)),
    V("Busan Steel Industries Co Ltd", "Korea, Republic of", "Busan", supplies=("MET",)),
    V("Incheon Display & Sensors Co Ltd", "Korea, Republic of", "Incheon", supplies=("ELE",)),
    # ---- Singapore, UAE, UK, Türkiye, Brazil, Taiwan
    V("Lion City Industrial Trading Pte Ltd", "Singapore", "Singapore", supplies=("CHM", "MET")),
    V("Jebel Ali Metals FZE", "United Arab Emirates", "Dubai", supplies=("MET",)),
    V("Gulf Packaging Industries LLC", "United Arab Emirates", "Sharjah", supplies=("PKG",)),
    V("Midlands Engineering Supplies Ltd", "United Kingdom", "Birmingham", supplies=("MEC", "FGI")),
    V("Istanbul Metal Sanayi A.S.", "Türkiye", "Istanbul", supplies=("MET", "FAS")),
    V("São Paulo Metalúrgica Ltda", "Brazil", "São Paulo", supplies=("MET",)),
    # ---- foreign freight forwarders (3)
    V("Straits Freight Forwarding Pte Ltd", "Singapore", "Singapore", kind=SERVICE, service="forwarding"),
    V("Emirates Freight & Logistics LLC", "United Arab Emirates", "Dubai", kind=SERVICE, service="forwarding"),
    V("Rotterdam Global Forwarding B.V.", "Netherlands", "Rotterdam", kind=SERVICE, service="forwarding"),
]


# ------------------------------------------------------------------ customers (50)
@dataclass(frozen=True)
class Customer:
    name: str
    country: str
    city: str
    state: str = ""
    kind: str = "retail"           # industrial | retail | distributor | sez | oem
    sez: bool = False
    credit_limit: float = 0.0      # in INR


C = Customer
CUSTOMERS: list[Customer] = [
    # ---- India (20)
    C("Sahyadri Engineering Works Pvt Ltd", "India", "Pune", "Maharashtra", "industrial", credit_limit=30_000_000),
    C("Mumbai Infra Projects Ltd", "India", "Mumbai", "Maharashtra", "industrial", credit_limit=50_000_000),
    C("Nashik Auto Components Pvt Ltd", "India", "Nashik", "Maharashtra", "oem", credit_limit=25_000_000),
    C("Konkan Retail Hardware", "India", "Ratnagiri", "Maharashtra", "retail", credit_limit=3_000_000),
    C("Aurangabad Industrial Supplies", "India", "Aurangabad", "Maharashtra", "distributor", credit_limit=8_000_000),
    C("Gujarat Pumps & Engineering Pvt Ltd", "India", "Vadodara", "Gujarat", "industrial", credit_limit=20_000_000),
    C("Surat Textile Machinery Pvt Ltd", "India", "Surat", "Gujarat", "industrial", credit_limit=15_000_000),
    C("Rajkot Auto Parts Pvt Ltd", "India", "Rajkot", "Gujarat", "oem", credit_limit=12_000_000),
    C("Capital Construction Co", "India", "New Delhi", "Delhi", "industrial", credit_limit=40_000_000),
    C("NCR Power Systems Pvt Ltd", "India", "Gurugram", "Haryana", "industrial", credit_limit=22_000_000),
    C("Bengaluru Aerospace Parts Pvt Ltd", "India", "Bengaluru", "Karnataka", "oem", credit_limit=35_000_000),
    C("Mysuru Machine Tools Ltd", "India", "Mysuru", "Karnataka", "industrial", credit_limit=10_000_000),
    C("Coimbatore Motors & Pumps Ltd", "India", "Coimbatore", "Tamil Nadu", "industrial", credit_limit=18_000_000),
    C("Hosur Auto Assemblies Pvt Ltd", "India", "Hosur", "Tamil Nadu", "oem", credit_limit=16_000_000),
    C("Hyderabad Pharma Equipment Pvt Ltd", "India", "Hyderabad", "Telangana", "industrial", credit_limit=14_000_000),
    C("Howrah Foundry & Fabrication Ltd", "India", "Howrah", "West Bengal", "industrial", credit_limit=9_000_000),
    C("Jaipur Hardware Distributors", "India", "Jaipur", "Rajasthan", "distributor", credit_limit=6_000_000),
    C("Kanpur Leather Machinery Pvt Ltd", "India", "Kanpur", "Uttar Pradesh", "industrial", credit_limit=7_000_000),
    C("Kandla SEZ Fabricators Pvt Ltd", "India", "Gandhidham", "Gujarat", "sez", sez=True, credit_limit=20_000_000),
    C("Mahindra World City Components SEZ Unit", "India", "Chengalpattu", "Tamil Nadu", "sez", sez=True, credit_limit=18_000_000),
    # ---- exports (30)
    C("Atlas Industrial Distributors Inc", "United States", "Houston", "", "distributor", credit_limit=60_000_000),
    C("Great Lakes Machinery Corp", "United States", "Detroit", "", "industrial", credit_limit=45_000_000),
    C("Pacific Fasteners LLC", "United States", "Los Angeles", "", "distributor", credit_limit=30_000_000),
    C("Sunbelt Equipment Supply Inc", "United States", "Phoenix", "", "retail", credit_limit=15_000_000),
    C("Liberty Tool & Die Inc", "United States", "Pittsburgh", "", "industrial", credit_limit=25_000_000),
    C("Thames Engineering Supplies Ltd", "United Kingdom", "London", "", "distributor", credit_limit=35_000_000),
    C("Northern Fabrication Ltd", "United Kingdom", "Leeds", "", "industrial", credit_limit=20_000_000),
    C("Caledonian Industrial Ltd", "United Kingdom", "Glasgow", "", "industrial", credit_limit=18_000_000),
    C("Bayerische Maschinenhandel GmbH", "Germany", "Munich", "", "distributor", credit_limit=40_000_000),
    C("Ruhr Industriebedarf GmbH", "Germany", "Essen", "", "industrial", credit_limit=30_000_000),
    C("Hansa Werkzeug GmbH", "Germany", "Bremen", "", "retail", credit_limit=12_000_000),
    C("Al Noor Trading LLC", "United Arab Emirates", "Dubai", "", "distributor", credit_limit=28_000_000),
    C("Gulf Contracting FZE", "United Arab Emirates", "Sharjah", "", "industrial", credit_limit=22_000_000),
    C("Sharjah Hardware Trading LLC", "United Arab Emirates", "Sharjah", "", "retail", credit_limit=8_000_000),
    C("Abu Dhabi Industrial Supplies LLC", "United Arab Emirates", "Abu Dhabi", "", "industrial", credit_limit=26_000_000),
    C("Riyadh Heavy Equipment Co", "Saudi Arabia", "Riyadh", "", "industrial", credit_limit=32_000_000),
    C("Jeddah Marine Supplies Co", "Saudi Arabia", "Jeddah", "", "distributor", credit_limit=14_000_000),
    C("Marina Engineering Pte Ltd", "Singapore", "Singapore", "", "industrial", credit_limit=20_000_000),
    C("Jurong Industrial Supplies Pte Ltd", "Singapore", "Singapore", "", "distributor", credit_limit=16_000_000),
    C("Southern Cross Machinery Pty Ltd", "Australia", "Melbourne", "", "industrial", credit_limit=24_000_000),
    C("Outback Mining Supplies Pty Ltd", "Australia", "Perth", "", "industrial", credit_limit=30_000_000),
    C("Sydney Industrial Fasteners Pty Ltd", "Australia", "Sydney", "", "distributor", credit_limit=15_000_000),
    C("Nairobi Agro Engineering Ltd", "Kenya", "Nairobi", "", "industrial", credit_limit=9_000_000),
    C("Mombasa Port Supplies Ltd", "Kenya", "Mombasa", "", "distributor", credit_limit=7_000_000),
    C("Lagos Industrial Supplies Ltd", "Nigeria", "Lagos", "", "distributor", credit_limit=10_000_000),
    C("Johannesburg Mining Equipment Pty Ltd", "South Africa", "Johannesburg", "", "industrial", credit_limit=20_000_000),
    C("Cape Town Marine Engineering Pty Ltd", "South Africa", "Cape Town", "", "industrial", credit_limit=12_000_000),
    C("Rotterdam Maschinen Handel B.V.", "Netherlands", "Rotterdam", "", "distributor", credit_limit=22_000_000),
    C("Maple Industrial Supply Ltd", "Canada", "Toronto", "", "distributor", credit_limit=18_000_000),
    C("Calgary Oilfield Services Inc", "Canada", "Calgary", "", "industrial", credit_limit=25_000_000),
]


def vendor_currency(v: Vendor) -> str:
    return "INR" if v.country == "India" else IMPORT_COUNTRIES[v.country].currency


def customer_currency(c: Customer) -> str:
    return "INR" if c.country == "India" else EXPORT_COUNTRIES[c.country].currency


def vendor_tax_category(v: Vendor) -> str:
    if v.country == "India":
        return DOMESTIC_PURCHASE_INTRA if v.state == HOME_STATE else DOMESTIC_PURCHASE_INTER
    return IMPORT_COUNTRIES[v.country].tax_category


def customer_tax_category(c: Customer) -> str:
    if c.country == "India":
        if c.sez:
            return SEZ_SALE
        return DOMESTIC_SALE_INTRA if c.state == HOME_STATE else DOMESTIC_SALE_INTER
    return EXPORT_COUNTRIES[c.country].tax_category
