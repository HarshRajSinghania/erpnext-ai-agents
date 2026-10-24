"""Phase 3: two live business days (yesterday and today) of end-to-end cycles, built to exercise the Stock Assistant in as many different situations as possible.

It CONTINUES the history that is already in ERPNext instead of inventing a parallel one: goods are received against the old open purchase orders, old receipts
are invoiced and paid, old unpaid supplier and customer invoices are settled (in full, partly, in a foreign currency, with bank charges), old open sales orders
are delivered (in full and in part), and new cycles run start to finish: request for quotation -> supplier quotations -> purchase order -> receipt -> invoice ->
payment; purchase requisition -> order -> partial receipts; customer quotation -> sales order -> delivery -> invoice -> payment; returns both ways; reserved
stock; stock issues, transfers, receipts and a stock count. Customs and freight are posted on import receipts the way the year of data did.

Safe to repeat: every document carries a marker (TD/D3-<key>) in an ordinary field, so a second run finds what exists and carries on; documents that cannot
carry a marker are tracked in days_state.json. Everything created is listed in manifest.jsonl.

    python -m tools.testdata.cli days --plan                 what it would do (reads ERPNext, writes nothing)
    python -m tools.testdata.cli days                        do it (yesterday and today)
    python -m tools.testdata.cli days --only rfq1,mr1,oldpo  only these blocks
"""
from __future__ import annotations

import json
import logging
import math
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from . import catalog, post, sim, world
from .post import MARK, Poster, strip_new
from .steps import Context
from .writer import WriterError

log = logging.getLogger("testdata")
D = "D3-"                                   # every key of this phase starts with it
STATE = Path(__file__).resolve().parent / "days_state.json"
STORES = "Stores"
QUALITY_HOLD = "Quality Hold - Quarantine"
REJECTS = "Rejected & Scrap"
# where the marker lives for each document type (an ordinary writable field that survives mapping and is searchable)
MARKERS = {"Request for Quotation": "subject", "Supplier Quotation": "quotation_number", "Quotation": "title", "Material Request": "title", "Purchase Order": "order_confirmation_no",
           "Purchase Receipt": "supplier_delivery_note", "Purchase Invoice": "bill_no", "Sales Order": "po_no", "Delivery Note": "po_no", "Sales Invoice": "po_no",
           "Payment Entry": "reference_no", "Stock Entry": "remarks", "Journal Entry": "title", "Shipment": "description_of_content"}
EXPENSE_BANK_CHARGES = "Bank Charges"


class Skip(Exception):
    """A block cannot run (nothing suitable is open in ERPNext right now); say so and go on."""


def whole(x: float) -> float:
    return float(max(1, math.floor(x)))


class Days(Poster):
    def __init__(self, ctx: Context, d1: date, d2: date, seed: str = "days-v1") -> None:
        super().__init__(ctx, sim.Plan([], {}))
        self.d1, self.d2 = d1, d2
        self.rng = random.Random(seed)
        self.state: dict[str, list[str]] = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        self.doctype_of: dict[str, str] = {}
        self.draft: set[str] = set()
        self.made: Counter = Counter()           # documents created in THIS run, by type
        self.notes: list[str] = []               # things worth telling the person (a scenario that could not run, a setting changed)
        self.avail: dict[str, float] = {}        # item -> quantity on hand in the main store, tracked as this run moves stock
        self.bin_rate: dict[str, float] = {}

    # ------------------------------------------------------------------ bookkeeping
    def t(self, hh: int, mm: int = 0) -> str:
        return f"{hh:02d}:{mm:02d}:00"

    def load_names(self) -> int:
        n = 0
        for dt, field in MARKERS.items():
            for r in self.w.find(dt, [[field, "like", f"{MARK}{D}%"], ["docstatus", "<", 2]], ["name", field, "docstatus"], limit=100000):
                key = r[field][len(MARK):].split(" ", 1)[0].split(":", 1)[0]
                self.names[key], self.doctype_of[key] = r["name"], dt
                if r["docstatus"] == 0:
                    self.draft.add(key)
                n += 1
        for key, val in list(self.state.items()):                    # documents with no marker field
            if key.startswith("_"):
                continue
            dt, name = val
            if key not in self.names:
                if self.w.exists(dt, name) and self.w.get(dt, name).get("docstatus") != 2:
                    self.names[key], self.doctype_of[key] = name, dt
                    n += 1
        return n

    def put(self, key: str, doctype: str, doc: dict, submit: bool = True) -> str:
        """Create (and submit) a document unless this key was already posted. Returns its name either way."""
        if key in self.names:
            if key in self.draft and submit:
                self.w.submit(self.doctype_of[key], self.names[key])
                self.draft.discard(key)
            return self.names[key]
        field = MARKERS.get(doctype)
        if field:
            tag = MARK + key
            doc[field] = tag if field in ("quotation_number", "order_confirmation_no", "supplier_delivery_note", "bill_no", "po_no", "reference_no") else f"{tag} {doc.get(field, '')}".strip()
        name = self.w.create(doctype, doc)
        if submit:
            self.w.submit(doctype, name)
        self.names[key], self.doctype_of[key] = name, doctype
        self.state[key] = [doctype, name]
        self.save()
        self.made[doctype] += 1
        log.info("   %-22s %-28s %s", doctype, name, key)
        return name

    def save(self) -> None:
        STATE.write_text(json.dumps(self.state, indent=1), encoding="utf-8")

    def picked(self, label: str, chooser: Callable[[], list]) -> list:
        """Which old documents a block works on is decided once and remembered, so a re-run continues with the same ones."""
        picks = self.state.setdefault("_picks", {})
        if label not in picks:
            picks[label] = chooser()
            self.save()
        return picks[label]

    def doc(self, key: str) -> dict:
        return self.w.get(self.doctype_of[key], self.names[key])

    # ------------------------------------------------------------------ live facts
    def refresh_stock(self) -> None:
        self.avail, self.bin_rate = {}, {}
        for b in self.w.find("Bin", [["warehouse", "=", self.acc(STORES)]], ["item_code", "actual_qty", "valuation_rate"], limit=100000):
            self.avail[b["item_code"]] = float(b["actual_qty"])
            self.bin_rate[b["item_code"]] = float(b["valuation_rate"] or 0)

    def vendor_price(self, it: catalog.Item, vendor: world.Vendor, day: date) -> float:
        """The vendor's price in ITS currency: from the item's supplier list, or (for a vendor that only supplies the group) base price plus a premium."""
        for name, price, _lt in it.suppliers:
            if name == vendor.name:
                return round(price * self.rng.uniform(0.97, 1.04), 4 if price < 1 else 2)
        ccy = world.vendor_currency(vendor)
        return round(it.base_price * self.rng.uniform(1.04, 1.16) / self.rate(ccy, day), 4 if ccy == "JPY" else 3)

    def landed_inr(self, it: catalog.Item, vendor: world.Vendor, price: float, qty: float, day: date) -> float:
        return price * self.rate(world.vendor_currency(vendor), day) * qty * (1 + sim.landed_uplift(it, vendor.country))

    def po_base(self, sup: world.Vendor, day: date, arrive: date) -> dict:
        ccy, cat = world.vendor_currency(sup), world.vendor_tax_category(sup)
        rate, cc = self.rate(ccy, day), self.ctx.cc("Procurement & Imports")
        return {"supplier": sup.name, "company": self.ctx.company, "transaction_date": day.isoformat(), "schedule_date": arrive.isoformat(), "currency": ccy, "conversion_rate": rate,
                "set_warehouse": self.acc(STORES), "tax_category": cat, "buying_price_list": "Standard Buying" if ccy == "INR" else f"Buying - {ccy}", "ignore_pricing_rule": 1,
                "taxes": self.tax_rows(True, cat, cc, rate)}

    def items_for_cycles(self) -> list[catalog.Item]:
        """Items with two listed suppliers (one local, one importing), from different groups, that this store really holds."""
        out, seen = [], set()
        for it in catalog.build_items():
            if it.stock and len(it.suppliers) == 2 and it.group_code not in seen and it.code in self.avail and it.moq <= 1000:
                out.append(it)
                seen.add(it.group_code)
        return out

    def extra_vendor(self, it: catalog.Item) -> Optional[world.Vendor]:
        listed = {n for n, _p, _l in it.suppliers}
        pool = [v for v in world.VENDORS if v.kind == world.GOODS and it.group_code in v.supplies and v.name not in listed]
        return self.rng.choice(pool) if pool else None

    # ------------------------------------------------------------------ the building blocks (each returns the name it made)
    def receive_po(self, key: str, po: str, day: date, hh: int, *, ratio: float = 1.0, reject: float = 0.0, note: str = "") -> str:
        if key in self.names:
            return self.put(key, "Purchase Receipt", {})
        doc = self.mapped("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", po)
        po_doc = self.w.get("Purchase Order", po)
        rate = self.rate(po_doc["currency"], day)
        for line in doc["items"]:
            qty = whole(line["qty"] * ratio) if ratio < 1 else float(line["qty"])
            rej = whole(qty * reject) if reject else 0.0
            line.update({"qty": qty - rej, "received_qty": qty, "rejected_qty": rej})
            if rej:
                line["rejected_warehouse"] = self.acc(QUALITY_HOLD)
        doc.update({"posting_date": day.isoformat(), "posting_time": self.t(hh), "set_posting_time": 1, "conversion_rate": rate, "remarks": note})
        name = self.put(key, "Purchase Receipt", doc)
        for line in doc["items"]:
            self.avail[line["item_code"]] = self.avail.get(line["item_code"], 0) + float(line["qty"])
        return name

    def customs(self, key: str, pr_key: str, supplier: str, day: date, hh: int = 11) -> Optional[str]:
        """Freight, duty and import GST paid at clearance (only for goods that came from abroad)."""
        sup = self.vendors[supplier]
        if sup.country == "India":
            return None
        ev = sim.Event(day, sim.CUSTOMS, key, {"supplier": supplier, "country": sup.country}, parent=pr_key)
        if key in self.names:
            return self.names[key]
        self.do_customs(ev)
        self.made["Journal Entry"] += 1
        return self.names.get(key)

    def invoice_pr(self, key: str, pr_key: str, supplier: str, day: date) -> str:
        if key in self.names:
            return self.put(key, "Purchase Invoice", {})
        sup = self.vendors[supplier]
        doc = self.mapped("erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice", self.names[pr_key])
        doc.update({"posting_date": day.isoformat(), "set_posting_time": 1, "bill_date": day.isoformat(), "update_stock": 0})
        doc.pop("due_date", None)
        doc.pop("payment_schedule", None)
        if sup.country != "India":
            doc["taxes"], doc["taxes_and_charges"] = [], ""
            for i in doc["items"]:
                i["item_tax_template"], i["item_tax_rate"] = "", "{}"
        return self.put(key, "Purchase Invoice", doc)

    def pay(self, key: str, ref_doctype: str, ref_name: str, day: date, hh: int = 16, *, fraction: float = 1.0, bank_charge: float = 0.0, mode: Optional[str] = None) -> str:
        """Settle a purchase or sales invoice (full, or a fraction of what is outstanding). Foreign invoices pay at the day's rate, so exchange gain or loss arises as in life."""
        if key in self.names:
            return self.put(key, "Payment Entry", {})
        inv = self.w.get(ref_doctype, ref_name)
        receive = ref_doctype == "Sales Invoice"
        ccy = inv.get("currency") or "INR"
        args: dict[str, Any] = {}
        if fraction < 1.0:
            args["party_amount"] = round(float(inv["outstanding_amount"]) * fraction, 2)
        pe = strip_new(self.w.call("erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry", dt=ref_doctype, dn=ref_name, **args))
        rate = self.rate(ccy, day)
        if receive:
            bank = post.EEFC.get(ccy, post.BANK_COLLECT) if ccy != "INR" else post.BANK_COLLECT
            bank_ccy = ccy if ccy in post.EEFC else "INR"
            party_amount = pe["paid_amount"]
            pe.update({"paid_to": self.acc(bank), "paid_to_account_currency": bank_ccy})
            if bank_ccy == "INR":
                pe.update({"source_exchange_rate": rate, "target_exchange_rate": 1.0, "received_amount": round(party_amount * rate, 2)})
            else:
                pe.update({"source_exchange_rate": rate, "target_exchange_rate": rate, "received_amount": party_amount})
        else:
            party_amount = pe["received_amount"]
            pe.update({"paid_from": self.acc(post.BANK_INR), "paid_from_account_currency": "INR"})
            if ccy == "INR":
                pe.update({"source_exchange_rate": 1.0, "target_exchange_rate": 1.0})
            else:
                pe.update({"source_exchange_rate": 1.0, "target_exchange_rate": rate, "paid_amount": round(party_amount * rate, 2), "received_amount": party_amount})
        if bank_charge and ccy == "INR":
            # the bank keeps a fee: the party is credited in full, the bank moves a little less, the fee is booked as an expense
            if receive:
                pe["received_amount"] = round(pe["received_amount"] - bank_charge, 2)
            else:
                pe["paid_amount"] = round(pe["paid_amount"] + bank_charge, 2)
            pe["deductions"] = [{"account": self.acc(EXPENSE_BANK_CHARGES), "cost_center": self.ctx.cc("Corporate & Admin"), "amount": bank_charge if receive else -bank_charge}]
        pe.update({"posting_date": day.isoformat(), "reference_date": day.isoformat(), "mode_of_payment": mode or ("Cheque" if receive else "Wire Transfer"), "company": self.ctx.company,
                   "posting_time": self.t(hh)})
        return self.put(key, "Payment Entry", pe)

    # ------------------------------------------------------------------ buying cycles
    def block_rfq(self, cid: str, it: catalog.Item, qty: float, *, d_rfq: date, d_po: date, d_pr: Optional[date], d_pay: Optional[date], third: bool = True, partial: float = 1.0) -> None:
        """Request for quotation to every supplier of the item (and one more) -> a quotation from each -> order to the cheapest landed cost -> receipt -> invoice -> payment."""
        vendors = [self.vendors[n] for n, _p, _l in it.suppliers]
        extra = self.extra_vendor(it) if third else None
        if extra:
            vendors.append(extra)
        need = d_po + timedelta(days=10)
        rfq = self.put(f"{D}{cid}-RFQ", "Request for Quotation", {
            "company": self.ctx.company, "transaction_date": d_rfq.isoformat(), "subject": f"Quotation request: {it.name}",
            "message_for_supplier": f"Please quote your best price, lead time and payment terms for {qty:g} {it.uom} of {it.name}.",
            "suppliers": [{"supplier": v.name} for v in vendors],
            "items": [{"item_code": it.code, "qty": qty, "uom": it.uom, "stock_uom": it.uom, "conversion_factor": 1, "schedule_date": need.isoformat(), "warehouse": self.acc(STORES)}]})
        quotes: list[tuple[float, world.Vendor, str]] = []
        for n, v in enumerate(vendors, 1):
            price = self.vendor_price(it, v, d_rfq)
            sq_key = f"{D}{cid}-SQ{n}"
            if sq_key in self.names:
                sq = self.names[sq_key]
            else:
                doc = self.mapped("erpnext.buying.doctype.request_for_quotation.request_for_quotation.make_supplier_quotation_from_rfq", rfq, for_supplier=v.name)
                base = self.po_base(v, d_rfq, need)
                ccy, rate = base["currency"], base["conversion_rate"]
                doc.update({"transaction_date": d_rfq.isoformat(), "valid_till": (d_rfq + timedelta(days=14)).isoformat(), "currency": ccy, "conversion_rate": rate,
                            "buying_price_list": base["buying_price_list"], "tax_category": base["tax_category"], "ignore_pricing_rule": 1, "taxes": base["taxes"]})
                for line in doc["items"]:
                    tl = self.item_line(it.code, qty, price, d_rfq, True)
                    line.update({"rate": price, "qty": qty, "item_tax_template": tl["item_tax_template"], "item_tax_rate": tl["item_tax_rate"], "cost_center": tl["cost_center"]})
                sq = self.put(sq_key, "Supplier Quotation", doc)
            quotes.append((self.landed_inr(it, v, price, qty, d_rfq), v, sq))
        quotes.sort(key=lambda q: q[0])
        landed, winner, sq_name = quotes[0]
        self.notes.append(f"{cid}: {it.code} {qty:g} {it.uom}: quotes " + "; ".join(f"{v.name} {l:,.0f} INR landed" for l, v, _ in quotes) + f" -> ordered from {winner.name}")
        po_key = f"{D}{cid}-PO"
        if po_key in self.names:
            po = self.names[po_key]
        else:
            doc = self.mapped("erpnext.buying.doctype.supplier_quotation.supplier_quotation.make_purchase_order", sq_name)
            base = self.po_base(winner, d_po, need)
            price = float(self.w.get("Supplier Quotation", sq_name)["items"][0]["rate"])
            doc.update({"transaction_date": d_po.isoformat(), "schedule_date": need.isoformat(), "set_warehouse": base["set_warehouse"], "conversion_rate": base["conversion_rate"],
                        "taxes": base["taxes"], "taxes_and_charges": "", "ignore_pricing_rule": 1})
            for line in doc["items"]:
                tl = self.item_line(it.code, qty, price, d_po, True, schedule_date=need.isoformat())
                line.update({"item_tax_template": tl["item_tax_template"], "item_tax_rate": tl["item_tax_rate"], "cost_center": tl["cost_center"], "schedule_date": need.isoformat()})
            po = self.put(po_key, "Purchase Order", doc)
        if not d_pr:
            return
        pr_key = f"{D}{cid}-PR"
        self.receive_po(pr_key, po, d_pr, 10, ratio=partial)
        self.customs(f"{D}{cid}-CUS", pr_key, winner.name, d_pr)
        if partial < 1.0:
            return
        pi = self.invoice_pr(f"{D}{cid}-PI", pr_key, winner.name, d_pr)
        if d_pay:
            self.pay(f"{D}{cid}-PAY", "Purchase Invoice", pi, d_pay)

    def block_mr(self, cid: str, it: catalog.Item, qty: float, *, d_mr: date, d_po: Optional[date], receipts: list[tuple[date, float]], d_pay: Optional[date], vendor: Optional[str] = None) -> None:
        """Purchase requisition -> purchase order -> one or more receipts (the first may be partial) -> invoice -> payment."""
        need = (d_po or d_mr) + timedelta(days=9)
        mr = self.put(f"{D}{cid}-MR", "Material Request", {
            "material_request_type": "Purchase", "company": self.ctx.company, "transaction_date": d_mr.isoformat(), "schedule_date": need.isoformat(),
            "items": [{"item_code": it.code, "qty": qty, "uom": it.uom, "stock_uom": it.uom, "conversion_factor": 1, "schedule_date": need.isoformat(), "warehouse": self.acc(STORES)}]})
        if not d_po:
            return
        sup = self.vendors[vendor or it.suppliers[0][0]]
        po_key = f"{D}{cid}-PO"
        if po_key in self.names:
            po = self.names[po_key]
        else:
            price = self.vendor_price(it, sup, d_po)
            doc = self.mapped("erpnext.stock.doctype.material_request.material_request.make_purchase_order", mr)
            base = self.po_base(sup, d_po, need)
            doc.update({k: v for k, v in base.items() if k != "taxes"})
            doc.update({"taxes": base["taxes"], "taxes_and_charges": ""})
            for line in doc["items"]:
                tl = self.item_line(it.code, qty, price, d_po, True, schedule_date=need.isoformat())
                line.update({"rate": price, "item_tax_template": tl["item_tax_template"], "item_tax_rate": tl["item_tax_rate"], "cost_center": tl["cost_center"], "schedule_date": need.isoformat()})
            po = self.put(po_key, "Purchase Order", doc)
        last_pr = None
        for n, (day, share) in enumerate(receipts, 1):
            pr_key = f"{D}{cid}-PR{n}"
            self.receive_po(pr_key, po, day, 10 + n, ratio=share)
            self.customs(f"{D}{cid}-CUS{n}", pr_key, sup.name, day, 12)
            last_pr = (pr_key, day)
        if last_pr and d_pay:
            # invoice each receipt, pay the last one: a part-paid supplier account
            for n in range(1, len(receipts) + 1):
                pi = self.invoice_pr(f"{D}{cid}-PI{n}", f"{D}{cid}-PR{n}", sup.name, receipts[n - 1][0])
            self.pay(f"{D}{cid}-PAY", "Purchase Invoice", pi, d_pay)

    # ------------------------------------------------------------------ buying: continuing the old documents
    def block_oldpo(self) -> None:
        """Goods arrive against the old open purchase orders: full, partial (the rest arrives today), with a rejected share, and with import clearance."""
        def choose() -> list:
            rows = self.w.find("Purchase Order", [["company", "=", self.ctx.company], ["docstatus", "=", 1], ["per_received", "<", 100], ["status", "not in", ["Closed", "Cancelled", "Completed"]]],
                               ["name", "supplier", "currency", "schedule_date"], limit=5000)
            rows.sort(key=lambda r: (r["schedule_date"], r["name"]))
            want = [("INR", "full"), ("INR", "partial"), ("USD", "full"), ("USD", "reject"), ("CNY", "full"), ("CNY", "partial"), ("EUR", "full"), ("GBP", "full"), ("JPY", "full"), ("SGD", "full")]
            out, taken = [], set()
            for ccy, kind in want:
                row = next((r for r in rows if r["currency"] == ccy and r["name"] not in taken), None) or next((r for r in rows if r["name"] not in taken), None)
                if row:
                    taken.add(row["name"])
                    out.append([row["name"], row["supplier"], kind])
            return out
        picks = self.picked("oldpo", choose)
        if not picks:
            raise Skip("no open purchase order is left to receive against")
        invoiced: list[tuple[str, str]] = []
        for n, (po, sup, kind) in enumerate(picks, 1):
            cid = f"OPO{n}"
            day = self.d1 if n <= 6 else self.d2
            key = f"{D}{cid}-PR1"
            ratio, reject = {"full": (1.0, 0.0), "partial": (0.6, 0.0), "reject": (1.0, 0.1)}[kind]
            self.receive_po(key, po, day, 9 + (n % 5), ratio=ratio, reject=reject, note=f"Against {po} ({kind})")
            self.customs(f"{D}{cid}-CUS1", key, sup, day, 14)
            if kind == "partial":
                self.receive_po(f"{D}{cid}-PR2", po, self.d2, 15, ratio=1.0, note=f"Balance of {po}")
                self.customs(f"{D}{cid}-CUS2", f"{D}{cid}-PR2", sup, self.d2, 16)
            elif n in (1, 3, 5, 7, 8, 9):
                invoiced.append((cid, sup))
        for n, (cid, sup) in enumerate(invoiced, 1):
            pi = self.invoice_pr(f"{D}{cid}-PI", f"{D}{cid}-PR1", sup, self.d2)
            if n in (1, 2, 4):
                self.pay(f"{D}{cid}-PAY", "Purchase Invoice", pi, self.d2, fraction=0.5 if n == 2 else 1.0)

    def block_oldrcpt(self) -> None:
        """Old receipts that were never invoiced: invoice them, then pay (one in full, one in part, one with the bank's fee)."""
        def choose() -> list:
            rows = self.w.find("Purchase Receipt", [["company", "=", self.ctx.company], ["docstatus", "=", 1], ["per_billed", "<", 100], ["is_return", "=", 0]],
                               ["name", "supplier", "posting_date", "currency"], limit=500)
            rows.sort(key=lambda r: r["posting_date"])
            return [[r["name"], r["supplier"], r["currency"]] for r in rows[:5]]
        picks = self.picked("oldrcpt", choose)
        if not picks:
            raise Skip("every old receipt is already invoiced")
        for n, (pr, sup, ccy) in enumerate(picks, 1):
            cid = f"ORC{n}"
            self.names[f"{D}{cid}-OLD"], self.doctype_of[f"{D}{cid}-OLD"] = pr, "Purchase Receipt"
            pi = self.invoice_pr(f"{D}{cid}-PI", f"{D}{cid}-OLD", sup, self.d1)
            if n <= 3:
                self.pay(f"{D}{cid}-PAY", "Purchase Invoice", pi, self.d1 if n == 1 else self.d2, fraction=0.4 if n == 2 else 1.0, bank_charge=125.0 if (n == 3 and ccy == "INR") else 0.0)

    def block_oldpi(self) -> None:
        """Settle old unpaid supplier invoices: the oldest overdue ones first, across currencies; one only in part."""
        def choose() -> list:
            rows = self.w.find("Purchase Invoice", [["company", "=", self.ctx.company], ["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["is_return", "=", 0]],
                               ["name", "supplier", "due_date", "currency"], limit=2000)
            rows.sort(key=lambda r: (r["due_date"], r["name"]))
            out, seen = [], Counter()
            for r in rows:
                if seen[r["currency"]] < 2 and len(out) < 7:
                    out.append([r["name"], r["supplier"], r["currency"]])
                    seen[r["currency"]] += 1
            return out
        picks = self.picked("oldpi", choose)
        if not picks:
            raise Skip("no unpaid supplier invoice is open")
        for n, (pi, sup, ccy) in enumerate(picks, 1):
            self.pay(f"{D}OPI{n}-PAY", "Purchase Invoice", pi, self.d1 if n % 2 else self.d2, fraction=0.5 if n == 3 else 1.0, bank_charge=95.0 if (n == 1 and ccy == "INR") else 0.0)

    def block_pret(self) -> None:
        """Goods sent back to a supplier: a purchase return (stock leaves) and the debit note that reduces what is owed."""
        src = next((k for k in (f"{D}OPO1-PR1", f"{D}OPO3-PR1", f"{D}rfq1-PR") if k in self.names), None)
        if not src:
            raise Skip("no receipt from this run to return goods from")
        key = f"{D}PRET1"
        if key not in self.names:
            doc = self.mapped("erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_return", self.names[src])
            for line in doc["items"]:
                line["qty"] = -whole(abs(float(line["qty"])) * 0.15)
                line["received_qty"] = line["qty"]
            doc.update({"posting_date": self.d2.isoformat(), "posting_time": self.t(17), "set_posting_time": 1, "remarks": "Damaged in transit: returned to the supplier"})
            self.put(key, "Purchase Receipt", doc)
        sup = self.w.get("Purchase Receipt", self.names[key])["supplier"]
        dn_key = f"{D}PRET1-DN"
        if dn_key not in self.names:
            doc = self.mapped("erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice", self.names[key])
            doc.update({"posting_date": self.d2.isoformat(), "set_posting_time": 1, "update_stock": 0})
            doc.pop("due_date", None)
            doc.pop("payment_schedule", None)
            if self.vendors[sup].country != "India":
                doc["taxes"], doc["taxes_and_charges"] = [], ""
                for i in doc["items"]:
                    i["item_tax_template"], i["item_tax_rate"] = "", "{}"
            self.put(dn_key, "Purchase Invoice", doc)

    # ------------------------------------------------------------------ selling cycles
    def customer(self, *, currency: Optional[str] = None, india: Optional[bool] = None, nth: int = 0) -> world.Customer:
        pool = [c for c in world.CUSTOMERS if (currency is None or world.customer_currency(c) == currency) and (india is None or (c.country == "India") == india)]
        if not pool:
            raise Skip(f"no customer in {currency or 'India' if india else currency}")
        return pool[nth % len(pool)]

    def sellable(self, n: int, *, min_stock: float = 400, skip: tuple[str, ...] = ()) -> list[tuple[str, float]]:
        """n goods lines this store can really supply: (item code, quantity), a few percent of what is on the shelf."""
        goods = [i for i in catalog.build_items() if i.stock and i.group_code.startswith(("FG", "ELE", "MEC", "FAS")) and self.avail.get(i.code, 0) >= min_stock and i.code not in skip]
        goods.sort(key=lambda i: i.code)
        self.rng.shuffle(goods)
        return [(i.code, float(max(1, min(math.floor(self.avail[i.code] * 0.04), 300)))) for i in goods[:n]]

    def sales_prices(self, cust: world.Customer, code: str, day: date) -> float:
        it, ccy = self.items[code], world.customer_currency(cust)
        return catalog.selling_price(it, ccy, self.fx, day) if ccy != "INR" else round(it.base_price * (1 + it.margin), 2)

    def sales_base(self, cust: world.Customer, day: date, deliver_on: date) -> dict:
        ccy, cat = world.customer_currency(cust), world.customer_tax_category(cust)
        rate = self.rate(ccy, day)
        cc = self.ctx.cc("Export Sales" if cust.country != "India" else "Domestic Sales")
        return {"customer": cust.name, "company": self.ctx.company, "transaction_date": day.isoformat(), "currency": ccy, "conversion_rate": rate, "tax_category": cat,
                "selling_price_list": "Standard Selling" if ccy == "INR" else f"Selling - {ccy}", "plc_conversion_rate": rate, "ignore_pricing_rule": 1,
                "taxes": self.tax_rows(False, cat, cc, rate)}

    def sales_lines(self, cust: world.Customer, lines: list[tuple[str, float]], day: date, deliver_on: date, reserve: bool = False) -> list[dict]:
        out = []
        for code, qty in lines:
            extra: dict[str, Any] = {"delivery_date": deliver_on.isoformat()}
            if reserve:
                extra["reserve_stock"] = 1
            out.append(self.item_line(code, qty, self.sales_prices(cust, code, day), day, False, **extra))
        return out

    def quote(self, key: str, cust: world.Customer, lines: list[tuple[str, float]], day: date, valid_days: int = 14) -> str:
        doc = self.sales_base(cust, day, day)
        doc.update({"quotation_to": "Customer", "party_name": cust.name, "order_type": "Sales", "valid_till": (day + timedelta(days=valid_days)).isoformat(),
                    "items": self.sales_lines(cust, lines, day, day + timedelta(days=7))})
        return self.put(key, "Quotation", doc)

    def order_from_quote(self, key: str, q_name: str, cust: world.Customer, day: date, deliver_on: date) -> str:
        if key in self.names:
            return self.put(key, "Sales Order", {})
        doc = self.mapped("erpnext.selling.doctype.quotation.quotation.make_sales_order", q_name)
        base = self.sales_base(cust, day, deliver_on)
        doc.update({"transaction_date": day.isoformat(), "delivery_date": deliver_on.isoformat(), "set_warehouse": self.acc(STORES), "po_date": day.isoformat(), "conversion_rate": base["conversion_rate"]})
        for line in doc["items"]:
            line["delivery_date"] = deliver_on.isoformat()
        return self.put(key, "Sales Order", doc)

    def order(self, key: str, cust: world.Customer, lines: list[tuple[str, float]], day: date, deliver_on: date, reserve: bool = False) -> str:
        doc = self.sales_base(cust, day, deliver_on)
        doc.update({"delivery_date": deliver_on.isoformat(), "set_warehouse": self.acc(STORES), "po_date": day.isoformat(), "items": self.sales_lines(cust, lines, day, deliver_on, reserve)})
        return self.put(key, "Sales Order", doc)

    def deliver(self, key: str, so: str, day: date, hh: int, *, ratio: float = 1.0) -> str:
        """Ship what the store can supply: all of what is left on the order, or a share of it. Never more than is on hand."""
        if key in self.names:
            return self.put(key, "Delivery Note", {})
        doc = self.mapped("erpnext.selling.doctype.sales_order.sales_order.make_delivery_note", so)
        so_doc = self.w.get("Sales Order", so)
        rate = self.rate(so_doc["currency"], day)
        keep = []
        for line in doc["items"]:
            want = float(line["qty"]) * ratio
            want = whole(want) if ratio < 1 else want
            qty = min(want, math.floor(self.avail.get(line["item_code"], 0)))
            if qty > 0:
                line["qty"] = float(qty)
                keep.append(line)
        if not keep:
            raise Skip(f"nothing on {so} can be delivered from stock right now")
        doc["items"] = keep
        doc.update({"posting_date": day.isoformat(), "posting_time": self.t(hh), "set_posting_time": 1, "conversion_rate": rate, "plc_conversion_rate": rate})
        name = self.put(key, "Delivery Note", doc)
        for line in keep:
            self.avail[line["item_code"]] = self.avail.get(line["item_code"], 0) - float(line["qty"])
        return name

    def invoice_dn(self, key: str, dn_key: str, day: date, hh: int = 15) -> str:
        if key in self.names:
            return self.put(key, "Sales Invoice", {})
        dn = self.w.get("Delivery Note", self.names[dn_key])
        cust = self.customers[dn["customer"]]
        ccy = world.customer_currency(cust)
        rate = self.rate(ccy, day)
        doc = self.mapped("erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice", self.names[dn_key])
        income = self.acc("Export Sales" if cust.country != "India" else "Domestic Sales")
        for i in doc["items"]:
            i["income_account"] = income
        doc.update({"posting_date": day.isoformat(), "set_posting_time": 1, "conversion_rate": rate, "plc_conversion_rate": rate, "update_stock": 0})
        doc.pop("due_date", None)
        doc.pop("payment_schedule", None)
        return self.put(key, "Sales Invoice", doc)

    def block_quote_cycle(self, cid: str, cust: world.Customer, n_lines: int, *, d_q: date, d_so: Optional[date], deliveries: list[tuple[date, float]], invoice: bool, pay: Optional[tuple[date, float]],
                          valid_days: int = 14, skip: tuple[str, ...] = ()) -> None:
        """Customer quotation -> sales order -> one or more deliveries (the first may be partial) -> invoices -> payment."""
        lines = self.sellable(n_lines, skip=skip)
        if len(lines) < n_lines:
            raise Skip("not enough stocked items to quote")
        q = self.quote(f"{D}{cid}-Q", cust, lines, d_q, valid_days)
        if not d_so:
            return
        so = self.order_from_quote(f"{D}{cid}-SO", q, cust, d_so, d_so + timedelta(days=5))
        si = None
        for n, (day, share) in enumerate(deliveries, 1):
            self.deliver(f"{D}{cid}-DN{n}", so, day, 13 + n, ratio=share)
            if invoice:
                si = self.invoice_dn(f"{D}{cid}-SI{n}", f"{D}{cid}-DN{n}", day, 16)
        if pay and si:
            self.pay(f"{D}{cid}-PAY", "Sales Invoice", si, pay[0], 17, fraction=pay[1])

    def block_oldso(self) -> None:
        """Deliver against old open sales orders: two in full, two in part; invoice three, collect on two."""
        def choose() -> list:
            rows = self.w.find("Sales Order", [["company", "=", self.ctx.company], ["docstatus", "=", 1], ["per_delivered", "<", 100], ["status", "not in", ["Closed", "Cancelled", "Completed"]]],
                               ["name", "customer", "currency", "transaction_date", "per_delivered"], limit=5000)
            rows.sort(key=lambda r: (r["transaction_date"], r["name"]))
            out, ccys = [], Counter()
            for r in rows:
                so = self.w.get("Sales Order", r["name"])
                can = sum(1 for i in so["items"] if float(i["qty"]) - float(i.get("delivered_qty") or 0) > 0 and self.avail.get(i["item_code"], 0) >= 1)
                if can and ccys[r["currency"]] < 2 and len(out) < 4:
                    out.append([r["name"], r["customer"], "full" if len(out) < 2 else "part"])
                    ccys[r["currency"]] += 1
            return out
        picks = self.picked("oldso", choose)
        if not picks:
            raise Skip("no old open sales order can be delivered from the stock on hand")
        for n, (so, cust, kind) in enumerate(picks, 1):
            cid = f"OSO{n}"
            day = self.d1 if n <= 2 else self.d2
            try:
                self.deliver(f"{D}{cid}-DN", so, day, 10 + n, ratio=1.0 if kind == "full" else 0.5)
            except Skip as why:
                self.notes.append(f"{cid}: {why}")
                continue
            if n <= 3:
                si = self.invoice_dn(f"{D}{cid}-SI", f"{D}{cid}-DN", self.d2, 15)
                if n <= 2:
                    self.pay(f"{D}{cid}-PAY", "Sales Invoice", si, self.d2, 17, fraction=0.7 if n == 2 else 1.0)

    def block_oldsi(self) -> None:
        """Collect on old unpaid customer invoices: overdue ones first across currencies; part payments, a bank fee, a foreign-currency receipt."""
        def choose() -> list:
            rows = self.w.find("Sales Invoice", [["company", "=", self.ctx.company], ["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["is_return", "=", 0]],
                               ["name", "customer", "due_date", "currency"], limit=3000)
            rows.sort(key=lambda r: (r["due_date"], r["name"]))
            out, seen = [], Counter()
            for r in rows:
                if seen[r["currency"]] < 2 and len(out) < 7:
                    out.append([r["name"], r["customer"], r["currency"]])
                    seen[r["currency"]] += 1
            return out
        picks = self.picked("oldsi", choose)
        if not picks:
            raise Skip("no unpaid customer invoice is open")
        for n, (si, cust, ccy) in enumerate(picks, 1):
            self.pay(f"{D}OSI{n}-PAY", "Sales Invoice", si, self.d1 if n % 2 else self.d2, 11 + n, fraction=0.5 if n in (2, 5) else 1.0, bank_charge=180.0 if (n == 1 and ccy == "INR") else 0.0)

    def block_sret(self) -> None:
        """A customer sends goods back: the return delivery puts stock back, the credit note reduces what they owe."""
        src = next((k for k in (f"{D}q1-DN1", f"{D}OSO1-DN", f"{D}q3-DN1") if k in self.names), None)
        if not src:
            raise Skip("no delivery from this run to take goods back from")
        key = f"{D}SRET1"
        if key not in self.names:
            doc = self.mapped("erpnext.stock.doctype.delivery_note.delivery_note.make_sales_return", self.names[src])
            for line in doc["items"]:
                line["qty"] = -whole(abs(float(line["qty"])) * 0.2)
            doc.update({"posting_date": self.d2.isoformat(), "posting_time": self.t(17, 30), "set_posting_time": 1})
            self.put(key, "Delivery Note", doc)
            for line in doc["items"]:
                self.avail[line["item_code"]] = self.avail.get(line["item_code"], 0) + abs(float(line["qty"]))
        cn = f"{D}SRET1-CN"
        if cn not in self.names:
            doc = self.mapped("erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice", self.names[key])
            cust = self.customers[self.w.get("Delivery Note", self.names[key])["customer"]]
            income = self.acc("Export Sales" if cust.country != "India" else "Domestic Sales")
            for i in doc["items"]:
                i["income_account"] = income
            doc.update({"posting_date": self.d2.isoformat(), "set_posting_time": 1, "update_stock": 0})
            doc.pop("due_date", None)
            doc.pop("payment_schedule", None)
            self.put(cn, "Sales Invoice", doc)

    # ------------------------------------------------------------------ reserved stock
    def reservations(self, so: str) -> list[dict]:
        return self.w.find("Stock Reservation Entry", [["voucher_no", "=", so], ["docstatus", "=", 1]], ["name", "reserved_qty", "delivered_qty", "status"])

    def reserve(self, so: str) -> list[dict]:
        """What the 'Reserve Stock' button does: hold the ordered stock for this customer."""
        if not self.reservations(so):
            self.w.call("run_doc_method", docs=json.dumps(self.w.get("Sales Order", so)), method="create_stock_reservation_entries", args=json.dumps({"notify": False}))
        return self.reservations(so)

    def block_resv(self) -> None:
        """Sales orders that hold stock for the customer: one stays fully reserved, one is part-delivered from its reservation, one has its reservation cancelled again.
        Stock reservation is an ERPNext setting that was off; it is switched on (and the summary says so)."""
        ss = self.w.get("Stock Settings", "Stock Settings")
        if not ss.get("enable_stock_reservation"):
            self.w.update("Stock Settings", "Stock Settings", {"enable_stock_reservation": 1})
            self.notes.append("Stock reservation was OFF in Stock Settings: it is now ON (needed for reserved stock). Switch it off again there if you do not want it.")
        specs = [("RES1", self.customer(india=True, nth=1), self.d2), ("RES2", self.customer(currency="USD", nth=0), self.d2), ("RES3", self.customer(currency="SGD", nth=0), self.d2)]
        used: tuple[str, ...] = ()
        for cid, cust, day in specs:
            key = f"{D}{cid}-SO"
            if key in self.names:
                so = self.names[key]
            else:
                lines = self.sellable(1, min_stock=800, skip=used)
                if not lines:
                    raise Skip("no item has enough stock to reserve")
                used += tuple(c for c, _ in lines)
                so = self.order(key, cust, [(c, q * 2) for c, q in lines], day, day + timedelta(days=6), reserve=True)
            if cid == "RES3" and f"{D}RES3-CANCEL" in self.names:
                continue
            found = self.reserve(so)
            self.notes.append(f"{cid}: {so} holds {sum(float(r['reserved_qty']) for r in found):g} in {len(found)} reservation(s)" if found else f"{cid}: {so} was created but NO reservation could be made")
            if cid == "RES2" and found:
                self.deliver(f"{D}RES2-DN", so, day, 15, ratio=0.5)
            if cid == "RES3" and found:
                for r in found:
                    self.w.call("frappe.client.cancel", doctype="Stock Reservation Entry", name=r["name"])
                self.names[f"{D}RES3-CANCEL"] = so
                self.state[f"{D}RES3-CANCEL"] = ["Sales Order", so]
                self.save()
                self.notes.append(f"RES3: the reservation on {so} was cancelled again; the order stays open")

    # ------------------------------------------------------------------ stock moves
    def entry(self, key: str, kind: str, lines: list[dict], day: date, hh: int, remark: str, **extra: Any) -> str:
        items = []
        for ln in lines:
            it = self.items[ln["item_code"]]
            row = {"item_code": ln["item_code"], "qty": ln["qty"], "uom": it.uom, "stock_uom": it.uom, "conversion_factor": 1}
            row.update({k: v for k, v in ln.items() if k not in ("item_code", "qty")})
            items.append(row)
        doc = {"stock_entry_type": kind, "purpose": kind, "company": self.ctx.company, "posting_date": day.isoformat(), "posting_time": self.t(hh), "set_posting_time": 1, "remarks": remark, "items": items}
        doc.update(extra)
        return self.put(key, "Stock Entry", doc)

    def usage_rows(self, days: int = 90) -> list[dict]:
        """Per item: what the ledger says it really sold per day over the last `days` days, with what is on the shelf now. Read straight from ERPNext's stock ledger."""
        since = (self.d1 - timedelta(days=days)).isoformat()
        sold: Counter = Counter()
        for r in self.w.find("Stock Ledger Entry", [["posting_date", ">=", since], ["actual_qty", "<", 0], ["is_cancelled", "=", 0], ["voucher_type", "=", "Delivery Note"]],
                             ["item_code", "actual_qty"], limit=200000):
            sold[r["item_code"]] -= float(r["actual_qty"])
        out = []
        for code, total in sold.items():
            it = self.items.get(code)
            if not it or not it.stock:
                continue
            lead = min((lt for _n, _p, lt in it.suppliers), default=7)
            out.append({"item_code": code, "item_name": it.name, "on_hand": self.avail.get(code, 0.0), "position": self.avail.get(code, 0.0), "daily_use": total / days, "lead_time_days": lead})
        return out

    def block_stock(self) -> None:
        """Stock issues (three of them aimed to create out-of-stock, critical and early-warning situations), transfers between warehouses, receipts, and a stock count."""
        rows = [r for r in self.usage_rows() if r["daily_use"] > 0.5 and r["on_hand"] >= 100]
        by_use = sorted(rows, key=lambda r: -r["daily_use"])
        small = [r for r in by_use if 100 <= r["on_hand"] <= 8000]
        if len(small) < 6:
            raise Skip("not enough items with sales history and stock to aim stock issues at")
        out_item, crit_item, early_item = small[0], small[2], small[4]

        def target_issue(r: dict, cover_days: float) -> float:
            keep = (r["daily_use"] or 1) * cover_days
            return float(max(1, math.floor(min(r["on_hand"], r["position"] - keep if r["position"] is not None else r["on_hand"] * 0.5))))

        thr = lambda r: max(7.0, (r.get("lead_time_days") or 7) + 3)
        issues = [
            ("MI1", out_item, float(math.floor(out_item["on_hand"])), self.d2, 18, "Whole stock moved to a customer project: nothing left on the shelf", self.acc("Cost of Goods Sold")),
            ("MI2", crit_item, target_issue(crit_item, 1.5), self.d2, 19, "Production line took most of the stock", self.acc("Cost of Goods Sold")),
            ("MI3", early_item, target_issue(early_item, thr(early_item) + 9), self.d2, 20, "Large internal requisition: stock is falling fast", self.acc("Cost of Goods Sold")),
        ]
        extra_pool = [r for r in by_use if r["item_code"] not in {out_item["item_code"], crit_item["item_code"], early_item["item_code"]} and r["on_hand"] >= 300][3:7]
        if len(extra_pool) >= 2:
            issues += [("MI4", extra_pool[0], float(max(1, math.floor(extra_pool[0]["on_hand"] * 0.02))), self.d1, 15, "Samples issued to a prospective customer", self.acc("Cost of Goods Sold")),
                       ("MI5", extra_pool[1], float(max(1, math.floor(extra_pool[1]["on_hand"] * 0.01))), self.d1, 16, "Damaged in the warehouse: written off", self.acc("Stock Adjustment"))]
        for key, r, qty, day, hh, why, expense in issues:
            if qty <= 0:
                continue
            self.entry(f"{D}{key}", "Material Issue", [{"item_code": r["item_code"], "s_warehouse": self.acc(STORES), "qty": qty, "expense_account": expense,
                                                      "cost_center": self.ctx.cc(__import__("tools.testdata.finance", fromlist=["x"]).GROUP_COST_CENTRE[self.items[r["item_code"]].group_code])}], day, hh, why)
            self.avail[r["item_code"]] = self.avail.get(r["item_code"], 0) - qty
            self.notes.append(f"{key}: issued {qty:g} {self.items[r['item_code']].uom} of {r['item_code']} ({r['item_name']}), had {r['on_hand']:g}, {why.lower()}")
        movers = [r for r in by_use if r["on_hand"] >= 5000][:3]
        branches = ["Delhi NCR Distribution Center", "Bengaluru Distribution Center", "Pune Packing & Kitting Unit"]
        for n, (r, wh) in enumerate(zip(movers, branches), 1):
            qty = float(max(1, math.floor(r["on_hand"] * 0.03)))
            self.entry(f"{D}MT{n}", "Material Transfer", [{"item_code": r["item_code"], "s_warehouse": self.acc(STORES), "t_warehouse": self.acc(wh), "qty": qty}], self.d1 if n < 3 else self.d2, 9 + n,
                       f"Stock sent to {wh} for local orders")
            self.avail[r["item_code"]] = self.avail.get(r["item_code"], 0) - qty
        empties = [r for r in sorted(self.usage_rows(), key=lambda r: -r["daily_use"]) if r["on_hand"] <= 0][:2]
        for n, r in enumerate(empties, 1):
            code = r["item_code"]
            rate = round(self.items[code].base_price * 0.98, 2)
            self.entry(f"{D}MR{n}", "Material Receipt", [{"item_code": code, "t_warehouse": self.acc(STORES), "qty": float(120 * n), "basic_rate": rate}], self.d2, 9 + n,
                       "Found in a returned shipment: booked in as a small part of what is missing")
            self.avail[code] = self.avail.get(code, 0) + 120 * n
            self.notes.append(f"MR{n}: received {120 * n} of {code} which was out of stock: a partial recovery, still short")
        counts = [r for r in by_use if r["on_hand"] >= 800 and r["item_code"] not in {out_item["item_code"], crit_item["item_code"], early_item["item_code"]}][8:11]
        if counts:
            self.refresh_stock()
            lines = []
            for r in counts:
                code = r["item_code"]
                counted = float(max(0, math.floor(self.avail.get(code, r["on_hand"]) * self.rng.uniform(0.94, 0.985))))
                lines.append({"item_code": code, "warehouse": self.acc(STORES), "qty": counted, "valuation_rate": self.bin_rate.get(code) or round(self.items[code].base_price, 2)})
            self.put(f"{D}COUNT1", "Stock Reconciliation", {"purpose": "Stock Reconciliation", "company": self.ctx.company, "posting_date": self.d2.isoformat(), "posting_time": self.t(21),
                                                          "set_posting_time": 1, "expense_account": self.acc("Stock Adjustment"), "cost_center": self.ctx.cc("Corporate & Admin"), "items": lines})
            self.notes.append("COUNT1: a stock count found shortages (3-6%) on " + ", ".join(l["item_code"] for l in lines))

    # ------------------------------------------------------------------ shipments
    def block_ship(self) -> None:
        """A Shipment (ERPNext's shipping document: parcels, pickup and delivery details) for two of today's deliveries: one export, one domestic."""
        title = "Test Trading Co Dispatch Dock"
        address = f"{title}-Shipping"
        if not self.w.exists("Address", address):
            self.w.create("Address", {"address_title": title, "address_type": "Shipping", "address_line1": "Plot 14, Nhava Sheva Logistics Park", "city": "Mumbai", "state": "Maharashtra",
                                      "country": "India", "pincode": "400707", "is_your_company_address": 1, "links": [{"link_doctype": "Company", "link_name": self.ctx.company}]})
            self.notes.append(f"Added the company address '{address}' (a pickup address, which ERPNext requires on a Shipment).")
        sources = [("SHIP1", f"{D}q2-DN2", 2, 14.5), ("SHIP2", f"{D}q1-DN1", 3, 22.0)]
        done = 0
        for key, dn_key, parcels, kg in sources:
            if dn_key not in self.names:
                continue
            dn = self.w.get("Delivery Note", self.names[dn_key])
            self.put(f"{D}{key}", "Shipment", {
                "pickup_from_type": "Company", "pickup_company": self.ctx.company, "pickup_address_name": address,
                "delivery_to_type": "Customer", "delivery_customer": dn["customer"], "delivery_address_name": dn.get("shipping_address_name") or dn.get("customer_address"),
                "shipment_delivery_note": [{"delivery_note": self.names[dn_key], "grand_total": dn["grand_total"]}],
                "shipment_parcel": [{"length": 40, "width": 30, "height": 30, "weight": round(kg / parcels, 2), "count": parcels}],
                "description_of_content": f"Goods for {dn['customer']} against {self.names[dn_key]}", "value_of_goods": dn.get("base_grand_total") or dn["grand_total"],
                "pickup_date": self.d2.isoformat(), "pickup_from": "15:00:00", "pickup_to": "18:00:00", "shipment_type": "Goods", "pickup_type": "Pickup"})
            done += 1
        if not done:
            raise Skip("no delivery from this run to ship")

    # ------------------------------------------------------------------ the scenarios
    def qty(self, it: catalog.Item, k: float) -> float:
        return float(max(it.moq, 1) * k)

    def blocks(self) -> dict[str, Callable[[], None]]:
        cyc = self.items_for_cycles()
        if len(cyc) < 4:
            raise WriterError("fewer than four suitable items found for the quotation cycles")
        d1, d2 = self.d1, self.d2
        return {
            # purchasing cycles that start now
            "rfq1": lambda: self.block_rfq("rfq1", cyc[0], self.qty(cyc[0], 4), d_rfq=d1, d_po=d1, d_pr=d2, d_pay=d2),
            "rfq2": lambda: self.block_rfq("rfq2", cyc[1], self.qty(cyc[1], 3), d_rfq=d1, d_po=d1, d_pr=d2, d_pay=None),
            "rfq3": lambda: self.block_rfq("rfq3", cyc[2], self.qty(cyc[2], 2), d_rfq=d2, d_po=d2, d_pr=None, d_pay=None),
            "mr1": lambda: self.block_mr("mr1", cyc[3], self.qty(cyc[3], 5), d_mr=d1, d_po=d1, receipts=[(d1, 0.6), (d2, 1.0)], d_pay=d2),
            "mr2": lambda: self.block_mr("mr2", cyc[0], self.qty(cyc[0], 2), d_mr=d1, d_po=d2, receipts=[(d2, 1.0)], d_pay=None),
            "mr3": lambda: self.block_mr("mr3", cyc[1], self.qty(cyc[1], 2), d_mr=d2, d_po=None, receipts=[], d_pay=None),
            "mr4": lambda: self.block_mr("mr4", cyc[2], self.qty(cyc[2], 3), d_mr=d2, d_po=d2, receipts=[], d_pay=None),
            # purchasing: continuing old documents
            "oldpo": self.block_oldpo, "oldrcpt": self.block_oldrcpt, "oldpi": self.block_oldpi, "pret": self.block_pret,
            # selling cycles that start now: quotation -> order -> delivery -> invoice -> payment
            "q1": lambda: self.block_quote_cycle("q1", self.customer(india=True, nth=0), 3, d_q=d1, d_so=d1, deliveries=[(d2, 1.0)], invoice=True, pay=(d2, 1.0)),
            "q2": lambda: self.block_quote_cycle("q2", self.customer(currency="USD", nth=1), 2, d_q=d1, d_so=d1, deliveries=[(d1, 0.6), (d2, 1.0)], invoice=True, pay=(d2, 0.5)),
            "q3": lambda: self.block_quote_cycle("q3", self.customer(currency="EUR", nth=0), 2, d_q=d1, d_so=d2, deliveries=[(d2, 1.0)], invoice=True, pay=None),
            "q4": lambda: self.block_quote_cycle("q4", self.customer(currency="GBP", nth=0), 2, d_q=d2, d_so=d2, deliveries=[], invoice=False, pay=None),
            "q5": lambda: self.block_quote_cycle("q5", self.customer(india=True, nth=3), 3, d_q=d1, d_so=None, deliveries=[], invoice=False, pay=None, valid_days=21),
            "q6": lambda: self.block_quote_cycle("q6", self.customer(currency="AUD", nth=0), 2, d_q=d1, d_so=None, deliveries=[], invoice=False, pay=None, valid_days=0),
            # selling: continuing old documents
            "oldso": self.block_oldso, "oldsi": self.block_oldsi, "sret": self.block_sret,
            # reserved stock, and stock moves
            "resv": self.block_resv, "stock": self.block_stock, "ship": self.block_ship,
        }

    def run(self, only: Optional[list[str]] = None, keep_going: bool = True) -> None:
        found = self.load_names()
        log.info("found %d documents from earlier runs of this phase", found)
        self.refresh_stock()
        for name, fn in self.blocks().items():
            if only and name not in only:
                continue
            log.info("== %s", name)
            made_before, notes_before = sum(self.made.values()), len(self.notes)
            try:
                fn()
                if sum(self.made.values()) == made_before:
                    del self.notes[notes_before:]               # nothing new was made (a re-run): the explanations would only repeat or mislead
            except Skip as why:
                self.notes.append(f"{name}: skipped, {why}")
                log.warning("   skipped: %s", why)
            except WriterError as exc:
                self.failures.append((name, str(exc)))
                log.error("   FAILED %s: %s", name, exc)
                if not keep_going:
                    raise
