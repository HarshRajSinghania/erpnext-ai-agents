"""Posts the planned year into ERPNext, in date order. Idempotent: every document carries a marker (TD/<key>) in a normal field,
so a re-run finds what is already there, submits half-finished drafts and carries on."""
from __future__ import annotations

import json
import logging
import random
import time
from datetime import date
from typing import Any, Optional

from . import catalog, finance, sim, world
from .steps import Context
from .writer import Writer, WriterError

log = logging.getLogger("testdata")
MARK = "TD/"
# (doctype, field that holds our marker)
MARKER_FIELD = {sim.PO: ("Purchase Order", "order_confirmation_no"), sim.PR: ("Purchase Receipt", "supplier_delivery_note"), sim.PI: ("Purchase Invoice", "bill_no"),
                sim.SO: ("Sales Order", "po_no"), sim.DN: ("Delivery Note", "po_no"), sim.SI: ("Sales Invoice", "po_no"),
                sim.PAY_SUP: ("Payment Entry", "reference_no"), sim.PAY_CUST: ("Payment Entry", "reference_no"),
                sim.CUSTOMS: ("Journal Entry", "title"), sim.JV: ("Journal Entry", "title")}
BANK_INR, BANK_COLLECT = "HDFC Bank Current A/c", "ICICI Bank Collection A/c"
EEFC = {"USD": "SBI EEFC A/c (USD)", "EUR": "HSBC EEFC A/c (EUR)", "GBP": "Standard Chartered A/c (GBP)"}
WAREHOUSE = "Stores"


def strip_new(doc: Any) -> Any:
    """Make a document returned by an ERPNext mapper insertable: drop 'new-...' names and parent bookkeeping."""
    if isinstance(doc, list):
        return [strip_new(d) for d in doc]
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            if k in ("name", "owner", "creation", "modified", "modified_by", "parent", "parenttype", "parentfield", "idx", "docstatus", "__islocal", "__unsaved", "amended_from"):
                continue
            out[k] = strip_new(v)
        return out
    return doc


class Poster:
    def __init__(self, ctx: Context, plan: sim.Plan, only: Optional[set[str]] = None, extra_items: Optional[list[catalog.Item]] = None) -> None:
        self.ctx, self.w, self.plan = ctx, ctx.w, plan
        self.fx = world.fx_series()
        self.items = {i.code: i for i in [*catalog.build_items(), *(extra_items or [])]}
        self.vendors = {v.name: v for v in world.VENDORS}
        self.customers = {c.name: c for c in world.CUSTOMERS}
        self.profiles = finance.item_tax_profiles()
        self.rng = random.Random("post-v1")
        self.names: dict[str, str] = {}          # event key -> ERPNext document name
        self.amounts: dict[str, dict] = {}       # event key -> facts needed by later events (totals, tax rows)
        self.only = only
        self.failures: list[tuple[str, str]] = []
        self.templates: dict[tuple[str, str], list[dict]] = {}
        self._status: dict = {}

    # ------------------------------------------------------------------ helpers
    def acc(self, n: str) -> str:
        return self.ctx.acc(n)

    def rate(self, ccy: str, day: date) -> float:
        return world.fx_on(self.fx, ccy, day)

    def load_existing(self) -> int:
        n = 0
        seen = set()
        for kind, (doctype, fieldname) in MARKER_FIELD.items():
            if (doctype, fieldname) in seen:
                continue
            seen.add((doctype, fieldname))
            for r in self.w.find(doctype, [[fieldname, "like", f"{MARK}%"], ["company", "=", self.ctx.company]] if doctype != "Payment Entry" else [[fieldname, "like", f"{MARK}%"]],
                                 ["name", fieldname, "docstatus"], limit=100000):
                key = r[fieldname][len(MARK):].split(" ", 1)[0].split(":", 1)[0]
                self.names[key] = r["name"]
                self._status[(doctype, r["name"])] = r["docstatus"]
                n += 1
        return n

    def template_rows(self, doctype: str, category: str) -> list[dict]:
        k = (doctype, category)
        if k not in self.templates:
            doc = self.w.get(doctype, f"{category} - {self.ctx.abbr}")
            self.templates[k] = doc.get("taxes", [])
        return self.templates[k]

    def tax_rows(self, buying: bool, category: str, cost_center: str, ccy_rate: float) -> list[dict]:
        out = []
        for r in self.template_rows("Purchase Taxes and Charges Template" if buying else "Sales Taxes and Charges Template", category):
            row = {"charge_type": r["charge_type"], "account_head": r["account_head"], "description": r.get("description") or r["account_head"], "rate": r.get("rate") or 0,
                   "cost_center": cost_center}
            if r.get("row_id"):
                row["row_id"] = r["row_id"]
            if buying:
                row["category"] = r.get("category") or "Total"
                row["add_deduct_tax"] = r.get("add_deduct_tax") or "Add"
            if r["charge_type"] == "Actual":
                row["tax_amount"] = round((r.get("tax_amount") or 0) / ccy_rate, 2)
            out.append(row)
        return out

    def item_line(self, code: str, qty: float, rate: float, day: date, buying: bool, **extra) -> dict:
        it = self.items[code]
        prof = self.profiles.get(it.profile, {})
        line = {"item_code": code, "qty": qty, "rate": rate, "uom": it.uom, "warehouse": self.acc(WAREHOUSE),
                "cost_center": self.ctx.cc(finance.GROUP_COST_CENTRE[it.group_code]),
                "item_tax_template": f"{it.profile} - {self.ctx.abbr}", "item_tax_rate": json.dumps({self.acc(a): r for a, r in prof.items()})}
        line.update(extra)
        return line

    def mapped(self, method: str, source: str, **args) -> dict:
        doc = self.w.call(method, source_name=source, **args)
        if not doc:
            raise WriterError(f"{method}({source}) returned nothing")
        return strip_new(doc)

    def find_name(self, key: str) -> Optional[str]:
        return self.names.get(key)

    def finish(self, doctype: str, name: str) -> None:
        self.w.submit(doctype, name)

    def have(self, ev: sim.Event) -> bool:
        """True if the document for this event exists and is submitted (a leftover draft is submitted here)."""
        name = self.names.get(ev.key)
        if not name:
            return False
        doctype = MARKER_FIELD[ev.kind][0]
        if self._status.get((doctype, name), 1) == 0:
            self.w.submit(doctype, name)
            self._status[(doctype, name)] = 1
        return True

    def create_and_submit(self, ev: sim.Event, doc: dict) -> str:
        doctype = MARKER_FIELD[ev.kind][0]
        name = self.w.create(doctype, doc)
        self.w.submit(doctype, name)
        self.names[ev.key] = name
        self._status[(doctype, name)] = 1
        return name

    # ------------------------------------------------------------------ run
    def run(self, until: Optional[date] = None, since: Optional[date] = None, stop_on_error: bool = True, progress_every: int = 100) -> None:
        loaded = self.load_existing()
        log.info("found %d documents from earlier runs", loaded)
        todo = [e for e in self.plan.events if (until is None or e.day <= until) and (since is None or e.day >= since) and (not self.only or e.kind in self.only)]
        t0, done, last_month = time.time(), 0, None
        for ev in todo:
            if (ev.day.year, ev.day.month) != last_month:
                last_month = (ev.day.year, ev.day.month)
                log.info("== %s  (%d/%d events, %.0fs)", ev.day.strftime("%B %Y"), done, len(todo), time.time() - t0)
            try:
                if self.have(ev):
                    done += 1
                    continue
                if ev.parent and ev.parent not in self.names:
                    self.failures.append((ev.key, f"skipped: its parent {ev.parent} was not posted"))
                    continue
                getattr(self, "do_" + ev.kind.lower())(ev)
            except WriterError as exc:
                self.failures.append((ev.key, str(exc)))
                log.error("FAILED %s %s: %s", ev.kind, ev.key, exc)
                if stop_on_error:
                    raise
            done += 1
            if done % progress_every == 0:
                log.info("   %d/%d  (%.0fs)", done, len(todo), time.time() - t0)

    # ------------------------------------------------------------------ purchasing
    def do_po(self, ev: sim.Event) -> None:
        d, sup = ev.data, self.vendors[ev.data["supplier"]]
        rate = self.rate(d["currency"], ev.day)
        cat = world.vendor_tax_category(sup)
        cc = self.ctx.cc("Procurement & Imports")
        doc = {"supplier": sup.name, "company": self.ctx.company, "transaction_date": ev.day.isoformat(), "schedule_date": d["arrive"].isoformat(), "currency": d["currency"],
               "conversion_rate": rate, "set_warehouse": self.acc(WAREHOUSE), "order_confirmation_no": MARK + ev.key, "tax_category": cat,
               "buying_price_list": "Standard Buying" if d["currency"] == "INR" else f"Buying - {d['currency']}", "ignore_pricing_rule": 1,
               "items": [self.item_line(c, q, r, ev.day, True, schedule_date=d["arrive"].isoformat()) for c, q, r in d["lines"]],
               "taxes": self.tax_rows(True, cat, cc, rate)}
        self.create_and_submit(ev, doc)

    def do_pr(self, ev: sim.Event) -> None:
        po = self.names[ev.parent]
        doc = self.mapped("erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt", po)
        rate = self.rate(ev.data["currency"], ev.day)
        doc.update({"posting_date": ev.day.isoformat(), "posting_time": sim.TIME[sim.PR], "set_posting_time": 1, "supplier_delivery_note": MARK + ev.key, "conversion_rate": rate})
        self.create_and_submit(ev, doc)

    def do_pi(self, ev: sim.Event) -> None:
        pr = self.names[ev.parent]
        doc = self.mapped("erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice", pr)
        sup = self.vendors[ev.data["supplier"]]
        rate = self.rate(ev.data["currency"], ev.day)
        doc.update({"posting_date": ev.day.isoformat(), "set_posting_time": 1, "bill_no": MARK + ev.key, "bill_date": ev.day.isoformat(), "update_stock": 0})       # keeps the receipt's exchange rate: ERPNext insists on it
        doc.pop("due_date", None)
        doc.pop("payment_schedule", None)             # rebuilt from the supplier's payment terms and the invoice date
        if sup.country != "India":
            doc["taxes"] = []          # duties and IGST are paid to customs, not to the foreign supplier: see the customs entry
            doc["taxes_and_charges"] = ""
            for i in doc["items"]:                     # otherwise ERPNext re-adds every account of the item tax template
                i["item_tax_template"], i["item_tax_rate"] = "", "{}"
        self.create_and_submit(ev, doc)

    def do_customs(self, ev: sim.Event) -> None:
        """The importer pays freight, insurance, customs duty, surcharge and IGST at clearance (estimates were capitalised on the receipt)."""
        pr_doc = self.w.get("Purchase Receipt", self.names[ev.parent])
        valuation = igst = 0.0
        for t in pr_doc.get("taxes", []):
            amount = float(t.get("base_tax_amount_after_discount_amount") or t.get("base_tax_amount") or 0)
            if (t.get("category") or "") in ("Valuation", "Valuation and Total"):
                valuation += amount
            elif t.get("account_head") == self.acc("Input IGST on Imports"):
                igst += amount
        actual = round(valuation * self.rng.uniform(0.97, 1.05), 2)
        lines = [(self.acc("Expenses Included In Valuation"), actual, 0.0), (self.acc("Input IGST on Imports"), round(igst, 2), 0.0)]
        self.post_journal(ev, f"Customs, freight and duty for {ev.data['supplier']} ({ev.data['country']})", lines, bank=BANK_INR)

    # ------------------------------------------------------------------ selling
    def do_so(self, ev: sim.Event) -> None:
        d = ev.data
        cust = self.customers[d["customer"]]
        ccy = world.customer_currency(cust)
        rate = self.rate(ccy, ev.day)
        cat = world.customer_tax_category(cust)
        cc = self.ctx.cc("Export Sales" if cust.country != "India" else "Domestic Sales")
        items = []
        for code, qty in d["lines"]:
            it = self.items[code]
            price = catalog.selling_price(it, ccy, self.fx, ev.day) if ccy != "INR" else round(it.base_price * (1 + it.margin), 2)
            items.append(self.item_line(code, qty, price, ev.day, False, delivery_date=d["deliver_on"].isoformat()))
        doc = {"customer": cust.name, "company": self.ctx.company, "transaction_date": ev.day.isoformat(), "delivery_date": d["deliver_on"].isoformat(), "currency": ccy,
               "conversion_rate": rate, "set_warehouse": self.acc(WAREHOUSE), "po_no": MARK + ev.key, "po_date": ev.day.isoformat(), "tax_category": cat,
               "selling_price_list": "Standard Selling" if ccy == "INR" else f"Selling - {ccy}", "plc_conversion_rate": rate, "ignore_pricing_rule": 1,
               "items": items, "taxes": self.tax_rows(False, cat, cc, rate)}
        self.create_and_submit(ev, doc)

    def do_dn(self, ev: sim.Event) -> None:
        so = self.names[ev.parent]
        doc = self.mapped("erpnext.selling.doctype.sales_order.sales_order.make_delivery_note", so)
        ship = dict(ev.data["lines"])
        doc["items"] = [dict(i, qty=ship[i["item_code"]]) for i in doc["items"] if i["item_code"] in ship]
        cust = self.customers[ev.data["customer"]]
        rate = self.rate(world.customer_currency(cust), ev.day)
        doc.update({"posting_date": ev.day.isoformat(), "posting_time": sim.TIME[sim.DN], "set_posting_time": 1, "po_no": MARK + ev.key, "conversion_rate": rate, "plc_conversion_rate": rate})
        self.create_and_submit(ev, doc)

    def do_si(self, ev: sim.Event) -> None:
        dn = self.names[ev.parent]
        doc = self.mapped("erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice", dn)
        cust = self.customers[ev.data["customer"]]
        ccy = world.customer_currency(cust)
        rate = self.rate(ccy, ev.day)
        income = self.acc("Export Sales" if cust.country != "India" else "Domestic Sales")
        for i in doc["items"]:
            i["income_account"] = income
        doc.update({"posting_date": ev.day.isoformat(), "set_posting_time": 1, "po_no": MARK + ev.key, "conversion_rate": rate, "plc_conversion_rate": rate, "update_stock": 0})
        doc.pop("due_date", None)
        doc.pop("payment_schedule", None)
        self.create_and_submit(ev, doc)

    # ------------------------------------------------------------------ payments
    def _payment(self, ev: sim.Event, ref_doctype: str, ref_name: str, party_ccy: str, receive: bool) -> str:
        pe = strip_new(self.w.call("erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry", dt=ref_doctype, dn=ref_name))
        rate = self.rate(party_ccy, ev.day)
        if receive:
            bank = EEFC.get(party_ccy, BANK_COLLECT) if party_ccy != "INR" else BANK_COLLECT
            bank_ccy = party_ccy if party_ccy in EEFC else "INR"
            party_amount = pe["paid_amount"]
            pe.update({"paid_to": self.acc(bank), "paid_to_account_currency": bank_ccy})
            if bank_ccy == "INR":
                pe.update({"source_exchange_rate": rate, "target_exchange_rate": 1.0, "received_amount": round(party_amount * rate, 2)})
            else:
                pe.update({"source_exchange_rate": rate, "target_exchange_rate": rate, "received_amount": party_amount})
        else:
            party_amount = pe["received_amount"]
            pe.update({"paid_from": self.acc(BANK_INR), "paid_from_account_currency": "INR"})
            if party_ccy == "INR":
                pe.update({"source_exchange_rate": 1.0, "target_exchange_rate": 1.0})
            else:
                pe.update({"source_exchange_rate": 1.0, "target_exchange_rate": rate, "paid_amount": round(party_amount * rate, 2), "received_amount": party_amount})
        pe.update({"posting_date": ev.day.isoformat(), "reference_no": MARK + ev.key, "reference_date": ev.day.isoformat(), "mode_of_payment": "Wire Transfer" if not receive else "Cheque",
                   "company": self.ctx.company})
        return self.create_and_submit(ev, pe)

    def do_pay_sup(self, ev: sim.Event) -> None:
        self._payment(ev, "Purchase Invoice", self.names[ev.parent], ev.data["currency"], receive=False)

    def do_pay_cust(self, ev: sim.Event) -> None:
        cust = self.customers[ev.data["customer"]]
        self._payment(ev, "Sales Invoice", self.names[ev.parent], world.customer_currency(cust), receive=True)

    # ------------------------------------------------------------------ journals
    def post_journal(self, ev: sim.Event, title: str, lines: list[tuple[str, float, float]], bank: Optional[str] = BANK_INR, voucher_type: str = "Journal Entry") -> str:
        rows = [{"account": a, "debit_in_account_currency": dr, "credit_in_account_currency": cr, "cost_center": self.ctx.cc("Corporate & Admin")} for a, dr, cr in lines if dr or cr]
        if bank:
            diff = round(sum(r["debit_in_account_currency"] for r in rows) - sum(r["credit_in_account_currency"] for r in rows), 2)
            if diff:
                rows.append({"account": self.acc(bank), "debit_in_account_currency": -diff if diff < 0 else 0, "credit_in_account_currency": diff if diff > 0 else 0})
        doc = {"title": f"{MARK}{ev.key}: {title}", "voucher_type": voucher_type, "company": self.ctx.company, "posting_date": ev.day.isoformat(), "accounts": rows,
               "user_remark": title}
        return self.create_and_submit(ev, doc)

    def do_jv(self, ev: sim.Event) -> None:
        d = ev.data
        if d["type"] == "gst":
            self.settle_gst(ev)
            return
        lines = [(self.acc(a), dr, cr) for a, dr, cr in d["lines"]]
        self.post_journal(ev, d["title"], lines, bank=None if d["type"] == "dep" else BANK_INR, voucher_type="Depreciation Entry" if d["type"] == "dep" else "Journal Entry")

    def balance(self, account: str, day: date) -> float:
        """Debit minus credit on the account up to `day`, from the ledger."""
        rows = self.w.find("GL Entry", [["account", "=", self.acc(account)], ["posting_date", "<=", day.isoformat()], ["is_cancelled", "=", 0]],
                           ["debit", "credit"], limit=100000)
        return float(sum(r["debit"] or 0 for r in rows) - sum(r["credit"] or 0 for r in rows))

    def settle_gst(self, ev: sim.Event) -> None:
        """Net the output tax against the input credits and pay the difference (a simplified GSTR-3B)."""
        out = {a: -self.balance(a, ev.day) for a in ("Output CGST", "Output SGST", "Output IGST")}
        inp = {a: self.balance(a, ev.day) for a in ("Input CGST", "Input SGST", "Input IGST", "Input IGST on Imports")}
        total_out, total_in = sum(v for v in out.values() if v > 0), sum(v for v in inp.values() if v > 0)
        scale = min(1.0, total_out / total_in) if total_in else 0.0            # input credit beyond the output tax is carried forward
        lines = [(self.acc(a), round(v, 2), 0.0) for a, v in out.items() if v > 0.5] + [(self.acc(a), 0.0, round(v * scale, 2)) for a, v in inp.items() if v * scale > 0.5]
        if not lines:
            self.names[ev.key] = "(nothing to settle)"
            return
        self.post_journal(ev, ev.data["title"], lines, bank=BANK_INR)
