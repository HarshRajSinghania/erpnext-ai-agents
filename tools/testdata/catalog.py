"""The item catalogue: 192 traded goods in 8 groups plus 8 service items, each with a customs tariff code and tax rates.

FICTIONAL and ILLUSTRATIVE: tariff codes are real-looking, but GST / duty rates are test values, not tariff advice.
Deterministic: the same items, prices and suppliers on every run.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from . import world

GROUPS = {
    "FAS": ("Fasteners & Hardware", 0.28), "MET": ("Metals - Raw Material", 0.12), "ELE": ("Electrical & Electronics", 0.24),
    "MEC": ("Mechanical Components", 0.26), "CHM": ("Chemicals & Consumables", 0.22), "PKG": ("Packaging Materials", 0.30),
    "FGI": ("Finished Goods - Industrial", 0.20), "FGC": ("Finished Goods - Consumer", 0.32),
}
SERVICE_GROUP = "Services"          # exists in ERPNext already


@dataclass(frozen=True)
class Product:
    name: str
    hs: str                        # customs tariff number, 8 digits with dots
    hs_desc: str
    gst: float                     # GST rate %
    bcd_mfn: float                 # basic customs duty % (most-favoured-nation)
    bcd_fta: float                 # basic customs duty % under a free trade / partnership agreement
    add_cn: float                  # anti-dumping duty % (applies to China only)
    uom: str
    kg: float                      # weight per unit
    price: float                   # base INR purchase price of the smallest variant
    variants: tuple[str, ...]
    grow: tuple[float, ...] = (1.0, 1.6, 2.4, 3.6)     # price multiplier per variant


P = Product
PRODUCTS: dict[str, list[Product]] = {
    "FAS": [
        P("Hex Bolt SS304", "7318.15.00", "Threaded screws and bolts", 18, 10, 5, 6, "Nos", 0.04, 6.0, ("M6x25", "M8x40", "M10x50", "M12x70")),
        P("Hex Nut SS304", "7318.16.00", "Nuts", 18, 10, 5, 6, "Nos", 0.02, 3.0, ("M6", "M8", "M10", "M12")),
        P("Self-tapping Screw", "7318.14.00", "Self-tapping screws", 18, 10, 5, 6, "Nos", 0.005, 1.2, ("4.2x16", "4.8x25", "5.5x38", "6.3x50")),
        P("Flat Washer", "7318.22.00", "Washers", 18, 10, 5, 6, "Nos", 0.01, 0.8, ("M6", "M8", "M10", "M12")),
        P("Blind Rivet Aluminium", "7318.23.00", "Rivets", 18, 10, 5, 6, "Nos", 0.006, 1.5, ("3.2x10", "4.0x12", "4.8x16", "6.4x20")),
        P("Anchor Bolt Zinc Plated", "7318.19.00", "Other threaded articles", 18, 10, 5, 6, "Nos", 0.12, 14.0, ("M8x75", "M10x90", "M12x110", "M16x150")),
    ],
    "MET": [
        P("Hot Rolled Steel Coil", "7208.37.00", "Flat-rolled iron, hot-rolled", 18, 7.5, 0, 12, "Kg", 1.0, 58.0, ("2mm", "3mm", "4mm", "6mm"), (1.0, 1.01, 1.02, 1.04)),
        P("Cold Rolled Steel Sheet", "7209.18.00", "Flat-rolled iron, cold-rolled", 18, 7.5, 0, 12, "Kg", 1.0, 68.0, ("0.5mm", "0.8mm", "1.0mm", "1.5mm"), (1.0, 1.01, 1.02, 1.04)),
        P("Stainless Steel Sheet 304", "7219.32.00", "Flat-rolled stainless steel", 18, 10, 5, 8, "Kg", 1.0, 240.0, ("1mm", "2mm", "3mm", "5mm"), (1.0, 1.02, 1.04, 1.07)),
        P("Aluminium Sheet 5052", "7606.12.00", "Aluminium plates and sheets", 18, 7.5, 0, 10, "Kg", 1.0, 310.0, ("1mm", "2mm", "3mm", "5mm"), (1.0, 1.02, 1.03, 1.05)),
        P("Copper Rod", "7407.10.00", "Copper bars and rods", 18, 5, 0, 0, "Kg", 1.0, 880.0, ("8mm", "10mm", "12mm", "16mm"), (1.0, 1.0, 1.01, 1.02)),
        P("Brass Bar", "7407.21.00", "Brass bars and rods", 18, 5, 0, 0, "Kg", 1.0, 620.0, ("10mm", "16mm", "25mm", "32mm"), (1.0, 1.01, 1.02, 1.04)),
    ],
    "ELE": [
        P("Power Cable 3-core Copper", "8544.49.00", "Insulated electric conductors", 18, 10, 5, 0, "Meter", 0.18, 95.0, ("1.5 sqmm", "2.5 sqmm", "4 sqmm", "6 sqmm"), (1.0, 1.5, 2.3, 3.4)),
        P("Industrial Relay 24V", "8536.41.00", "Relays for voltage up to 60V", 18, 10, 5, 0, "Nos", 0.12, 420.0, ("5A", "10A", "16A", "30A"), (1.0, 1.3, 1.7, 2.4)),
        P("Miniature Circuit Breaker", "8536.20.00", "Automatic circuit breakers", 18, 10, 5, 0, "Nos", 0.15, 360.0, ("6A", "16A", "32A", "63A"), (1.0, 1.2, 1.6, 2.4)),
        P("Three-phase Induction Motor", "8501.52.00", "AC motors, multi-phase, 750W-75kW", 18, 10, 5, 0, "Nos", 18.0, 16500.0, ("1HP", "2HP", "5HP", "7.5HP"), (1.0, 1.6, 3.1, 4.4)),
        P("Proximity Sensor", "9031.80.00", "Measuring and checking instruments", 18, 7.5, 0, 0, "Nos", 0.1, 1450.0, ("M12 NPN", "M18 NPN", "M12 PNP", "M18 PNP"), (1.0, 1.25, 1.1, 1.35)),
        P("Control Panel PCB", "8534.00.00", "Printed circuits", 18, 15, 7.5, 0, "Nos", 0.09, 780.0, ("2-layer", "4-layer", "6-layer", "8-layer"), (1.0, 1.7, 2.6, 3.8)),
    ],
    "MEC": [
        P("Deep Groove Ball Bearing", "8482.10.20", "Ball bearings", 18, 7.5, 0, 0, "Nos", 0.15, 180.0, ("6204", "6205", "6206", "6208"), (1.0, 1.15, 1.4, 1.9)),
        P("Gate Valve Cast Iron", "8481.80.00", "Taps, cocks and valves", 18, 7.5, 0, 0, "Nos", 4.5, 3800.0, ("2 inch", "3 inch", "4 inch", "6 inch"), (1.0, 1.5, 2.2, 3.8)),
        P("Industrial Rubber Hose", "4009.42.00", "Reinforced vulcanised rubber tubes", 18, 10, 5, 0, "Meter", 0.9, 260.0, ("25mm", "32mm", "50mm", "75mm"), (1.0, 1.4, 2.2, 3.5)),
        P("Spur Gear Steel", "8483.40.00", "Gears and gearing", 18, 7.5, 0, 0, "Nos", 0.8, 920.0, ("20T", "30T", "40T", "60T"), (1.0, 1.4, 1.9, 3.0)),
        P("Spiral Wound Gasket", "8484.10.00", "Gaskets of metal sheeting", 18, 10, 5, 0, "Nos", 0.12, 240.0, ("2 inch", "3 inch", "4 inch", "6 inch"), (1.0, 1.4, 1.9, 2.8)),
        P("Pneumatic Cylinder", "8412.31.00", "Linear acting pneumatic engines", 18, 7.5, 0, 0, "Nos", 1.8, 4200.0, ("32mm", "40mm", "50mm", "63mm"), (1.0, 1.3, 1.7, 2.3)),
    ],
    "CHM": [
        P("Industrial Epoxy Paint", "3208.90.00", "Paints and varnishes", 18, 10, 5, 0, "Litre", 1.3, 420.0, ("Grey", "Red", "Blue", "Black"), (1.0, 1.04, 1.04, 1.02)),
        P("Industrial Adhesive", "3506.91.00", "Adhesives based on polymers", 18, 10, 5, 0, "Kg", 1.0, 560.0, ("Epoxy 2-part", "Polyurethane", "Cyanoacrylate", "Silicone"), (1.0, 1.3, 2.4, 1.2)),
        P("Hydraulic Oil", "2710.19.00", "Lubricating oils", 18, 5, 0, 0, "Litre", 0.88, 210.0, ("ISO 32", "ISO 46", "ISO 68", "ISO 100"), (1.0, 1.02, 1.05, 1.1)),
        P("Cutting Fluid Soluble", "3403.19.00", "Lubricating preparations", 18, 7.5, 0, 0, "Litre", 1.0, 180.0, ("Standard", "Heavy duty", "Synthetic", "Semi-synthetic"), (1.0, 1.2, 1.5, 1.3)),
        P("Welding Electrode E6013", "8311.10.00", "Coated electrodes for arc welding", 18, 10, 5, 0, "Kg", 1.0, 165.0, ("2.5mm", "3.15mm", "4mm", "5mm"), (1.0, 0.98, 0.97, 0.97)),
        P("Industrial Solvent Thinner", "3814.00.00", "Organic composite solvents", 18, 10, 5, 0, "Litre", 0.85, 130.0, ("Grade A", "Grade B", "Grade C", "Grade D"), (1.0, 1.1, 1.2, 1.4)),
    ],
    "PKG": [
        P("Corrugated Carton 5-ply", "4819.10.00", "Cartons of corrugated paper", 12, 10, 5, 0, "Nos", 0.45, 38.0, ("Small", "Medium", "Large", "XL"), (1.0, 1.5, 2.3, 3.4)),
        P("Wooden Pallet 1200x1000", "4415.20.00", "Pallets and load boards of wood", 12, 10, 5, 0, "Nos", 18.0, 520.0, ("Standard", "Heavy duty", "Export treated", "Reversible"), (1.0, 1.4, 1.7, 1.5)),
        P("Stretch Film 500mm", "3920.10.00", "Plastic film of polyethylene", 18, 10, 5, 0, "Roll", 2.2, 520.0, ("17 micron", "20 micron", "23 micron", "25 micron"), (1.0, 1.15, 1.3, 1.4)),
        P("BOPP Packing Tape 48mm", "3919.10.00", "Self-adhesive plastic tape", 18, 10, 5, 0, "Roll", 0.25, 62.0, ("Clear", "Brown", "Printed", "Fragile"), (1.0, 1.0, 1.6, 1.8)),
        P("Self-adhesive Labels", "4821.10.00", "Printed paper labels", 12, 10, 5, 0, "Roll", 0.15, 210.0, ("50x25mm", "75x50mm", "100x75mm", "100x150mm"), (1.0, 1.3, 1.8, 2.6)),
        P("Bubble Wrap Roll", "3921.19.00", "Cellular plastic sheets", 18, 10, 5, 0, "Roll", 3.0, 880.0, ("0.5m", "1.0m", "1.2m", "1.5m"), (1.0, 1.9, 2.3, 2.8)),
    ],
    "FGI": [
        P("Centrifugal Pump Set", "8413.70.00", "Centrifugal pumps", 18, 7.5, 0, 0, "Nos", 22.0, 14500.0, ("0.5HP", "1HP", "2HP", "5HP"), (1.0, 1.4, 2.2, 4.1)),
        P("Control Panel Assembly", "8537.10.00", "Boards and panels, up to 1000V", 18, 10, 5, 0, "Nos", 35.0, 28500.0, ("7.5kW", "15kW", "22kW", "37kW"), (1.0, 1.6, 2.1, 3.2)),
        P("Axial Industrial Fan", "8414.59.00", "Fans", 18, 10, 5, 0, "Nos", 9.0, 5200.0, ("300mm", "450mm", "600mm", "900mm"), (1.0, 1.5, 2.2, 3.8)),
        P("Air Compressor", "8414.80.00", "Air pumps and compressors", 18, 7.5, 0, 0, "Nos", 55.0, 32500.0, ("2HP", "3HP", "5HP", "7.5HP"), (1.0, 1.4, 2.1, 3.0)),
        P("Hydraulic Bottle Jack", "8425.42.00", "Hydraulic jacks", 18, 7.5, 0, 0, "Nos", 18.0, 8800.0, ("5T", "10T", "20T", "50T"), (1.0, 1.5, 2.6, 5.4)),
        P("Inverter Welding Machine", "8515.31.00", "Arc welding machines", 18, 10, 5, 0, "Nos", 12.0, 18500.0, ("200A", "300A", "400A", "500A"), (1.0, 1.5, 2.1, 2.8)),
    ],
    "FGC": [
        P("Cordless Drill 18V", "8467.21.00", "Electromechanical hand drills", 18, 10, 5, 0, "Nos", 1.6, 4800.0, ("Basic", "Pro", "Brushless", "Kit"), (1.0, 1.4, 1.9, 2.4)),
        P("Hand Tool Set 120pc", "8206.00.00", "Tool sets", 18, 10, 5, 0, "Set", 6.5, 3200.0, ("Chrome", "Premium", "Mechanic", "Industrial"), (1.0, 1.5, 1.9, 2.4)),
        P("LED Floodlight", "9405.42.00", "Electric LED lamps and fittings", 12, 20, 10, 0, "Nos", 1.1, 1450.0, ("30W", "50W", "100W", "200W"), (1.0, 1.6, 2.9, 5.0)),
        P("Heat Gun", "8516.79.00", "Electro-thermic appliances", 18, 20, 10, 0, "Nos", 0.9, 1650.0, ("1600W", "1800W", "2000W", "2200W"), (1.0, 1.1, 1.25, 1.4)),
        P("Safety Helmet", "6506.10.00", "Safety headgear", 18, 10, 5, 0, "Nos", 0.4, 260.0, ("White", "Yellow", "Blue", "Red"), (1.0, 1.0, 1.0, 1.0)),
        P("Digital Multimeter", "9030.31.00", "Multimeters", 18, 7.5, 0, 0, "Nos", 0.3, 1850.0, ("Basic", "Auto-range", "True-RMS", "Industrial"), (1.0, 1.4, 2.3, 3.2)),
    ],
}

SERVICES = [
    ("SVC-0001", "Ocean Freight Service (Inbound)", "Nos"), ("SVC-0002", "Air Freight Service (Inbound)", "Nos"), ("SVC-0003", "Inland Road Transport", "Nos"),
    ("SVC-0004", "Customs Clearance & Documentation", "Nos"), ("SVC-0005", "Marine Cargo Insurance", "Nos"), ("SVC-0006", "Warehousing Service", "Nos"),
    ("SVC-0007", "Packing & Palletising Labour", "Hour"), ("SVC-0008", "Calibration & Inspection", "Nos"),
]


@dataclass
class Item:
    code: str
    name: str
    group_code: str                # FAS, MET, ... or "SVC"
    group: str
    uom: str
    kg: float
    hs: Optional[str]
    hs_desc: str
    gst: float
    bcd_mfn: float
    bcd_fta: float
    add_cn: float
    base_price: float              # INR purchase price, the starting point for every supplier price
    margin: float
    stock: bool = True
    suppliers: list[tuple[str, float, int]] = field(default_factory=list)   # (vendor name, price in the vendor's currency, lead time days)
    moq: float = 1
    country_of_origin: str = "India"

    @property
    def profile(self) -> str:
        """Items with the same HS code and GST rate share one item tax template."""
        return f"HS {self.hs.replace('.', '')} / GST {self.gst:g}%" if self.hs else ""


def _goods_vendors(group_code: str) -> list[world.Vendor]:
    return [v for v in world.VENDORS if v.kind == world.GOODS and group_code in v.supplies]


def build_items() -> list[Item]:
    rng = random.Random("catalog-v1")
    fx = world.fx_series()
    start = world.fx_dates()[0]
    items: list[Item] = []
    for gc, (gname, margin) in GROUPS.items():
        seq = 0
        domestic = [v for v in _goods_vendors(gc) if v.country == "India"]
        foreign = [v for v in _goods_vendors(gc) if v.country != "India"]
        for prod in PRODUCTS[gc]:
            for vi, variant in enumerate(prod.variants):
                seq += 1
                base = round(prod.price * prod.grow[vi] * rng.uniform(0.94, 1.06), 2 if prod.price < 100 else 0)
                it = Item(code=f"{gc}-{seq:04d}", name=f"{prod.name} {variant}", group_code=gc, group=gname, uom=prod.uom, kg=prod.kg, hs=prod.hs, hs_desc=prod.hs_desc,
                          gst=prod.gst, bcd_mfn=prod.bcd_mfn, bcd_fta=prod.bcd_fta, add_cn=prod.add_cn, base_price=base, margin=margin)
                mode = rng.random()                      # 65% both a local and an imported source, 20% local only, 15% imported only
                chosen: list[world.Vendor] = []
                if mode < 0.65 and domestic and foreign:
                    chosen = [rng.choice(domestic), rng.choice(foreign)]
                elif mode < 0.85 and domestic:
                    chosen = [rng.choice(domestic)]
                elif foreign:
                    chosen = [rng.choice(foreign)]
                else:
                    chosen = [rng.choice(domestic)]
                for v in chosen:
                    if v.country == "India":
                        price, lead = base * rng.uniform(0.98, 1.04), rng.randint(3, 10)
                        it.suppliers.append((v.name, round(price, 2), lead))
                    else:
                        c = world.IMPORT_COUNTRIES[v.country]
                        inr = base * rng.uniform(0.80, 0.92)                 # imports look cheaper per unit, before freight and duty
                        it.suppliers.append((v.name, round(inr / fx_on_base(fx, c.currency, start), 3 if inr < 100 else 2), c.transit_days + rng.randint(8, 14)))
                first_country = next(v.country for v in world.VENDORS if v.name == it.suppliers[0][0])
                it.country_of_origin = first_country
                it.moq = 1000 if base < 5 else 500 if base < 30 else 100 if base < 300 else 25 if base < 2000 else 5 if base < 10000 else 2
                items.append(it)
    for code, name, uom in SERVICES:
        items.append(Item(code=code, name=name, group_code="SVC", group=SERVICE_GROUP, uom=uom, kg=0, hs=None, hs_desc="", gst=18, bcd_mfn=0, bcd_fta=0, add_cn=0,
                          base_price=0, margin=0, stock=False))
    return items


def fx_on_base(fx: dict, ccy: str, day) -> float:
    return world.fx_on(fx, ccy, day)


def selling_price(it: Item, currency: str, fx: dict, day) -> float:
    """List price in `currency`: cost plus margin, exports carry a 6% premium for documentation and credit risk."""
    inr = it.base_price * (1 + it.margin) * (1.0 if currency == "INR" else 1.06)
    p = inr / world.fx_on(fx, currency, day)
    return round(p, 2 if p >= 10 else 3)
