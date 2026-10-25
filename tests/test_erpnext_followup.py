"""The ERPNext adapter's reads for the purchasing follow-up and stock intelligence, against a fake HTTP session (NOT a live ERPNext: see the adapter's docstring).

The fake session returns every row of a table whatever the filters say, so these tests check the mapping and the filters sent, and that the adapter does its own sums."""
import json
from datetime import date

from test_erpnext_client import FakeSession, Resp, client


def filters_of(c, doctype):
    return [json.loads(p["filters"]) for u, p in c.session.calls if u.endswith(f"/api/resource/{doctype}") and p and "filters" in p]


def test_receipt_lines_carry_the_order_line_and_the_invoice():
    c = client({
        "Purchase Receipt": [{"name": "PR-1", "supplier": "SUP-A", "supplier_name": "Alpha", "posting_date": "2026-10-14", "docstatus": 1, "is_return": 0},
                             {"name": "PR-RET", "supplier": "SUP-A", "supplier_name": "Alpha", "posting_date": "2026-10-14", "docstatus": 1, "is_return": 1}],
        "Purchase Receipt Item": [{"parent": "PR-1", "item_code": "BOLT", "item_name": "Bolt", "uom": "Nos", "received_qty": 120, "qty": 110, "rejected_qty": 10, "rate": 10.5,
                                   "purchase_order": "PO-9", "purchase_order_item": "poi-1"}],
        "Purchase Order Item": [{"name": "poi-1", "parent": "PO-9", "qty": 100, "rate": 10.0, "schedule_date": "2026-10-12", "received_qty": 110}],
        "Purchase Invoice Item": [{"parent": "PINV-1", "item_code": "BOLT", "rate": 10.8, "purchase_receipt": "PR-1", "purchase_order": "PO-9"}],
        "Purchase Invoice": [{"name": "PINV-1", "status": "Unpaid", "docstatus": 1}],
    })
    (l,) = c.get_receipt_lines(date(2026, 10, 1))
    assert (l.receipt, l.supplier, l.item_code, l.received_qty, l.accepted_qty, l.rejected_qty, l.rate) == ("PR-1", "SUP-A", "BOLT", 120, 110, 10, 10.5)
    assert (l.purchase_order, l.po_qty, l.po_rate, l.po_due, l.po_received_before) == ("PO-9", 100, 10.0, date(2026, 10, 12), 0.0)       # 110 received includes this receipt
    assert (l.invoice, l.invoice_status, l.invoice_rate, l.submitted) == ("PINV-1", "Unpaid", 10.8, True)
    assert ["posting_date", ">=", "2026-10-01"] in filters_of(c, "Purchase Receipt")[0]


def test_receipts_without_invoices_or_readable_orders_are_still_returned():
    c = client({"Purchase Receipt": [{"name": "PR-1", "supplier": "SUP-A", "posting_date": "2026-10-14", "docstatus": 0}],
                "Purchase Receipt Item": [{"parent": "PR-1", "item_code": "BOLT", "qty": 5, "rate": 1.0}],
                "Purchase Order Item": 403, "Purchase Invoice Item": 403, "Purchase Invoice": []})
    (l,) = c.get_receipt_lines(date(2026, 10, 1))
    assert l.purchase_order is None and l.invoice is None and not l.submitted and l.received_qty == 5


def test_open_sales_orders_are_the_quantity_still_to_deliver_in_the_stock_unit():
    c = client({"Sales Order Item": [{"parent": "SO-1", "item_code": "BOLT", "qty": 10, "delivered_qty": 4, "conversion_factor": 12, "delivery_date": "2026-10-20"},
                                     {"parent": "SO-2", "item_code": "BOLT", "qty": 5, "delivered_qty": 0, "conversion_factor": 1, "delivery_date": "2026-10-21"},
                                     {"parent": "SO-3", "item_code": "BOLT", "qty": 5, "delivered_qty": 5, "conversion_factor": 1, "delivery_date": "2026-10-21"}],
                "Sales Order": [{"name": "SO-1", "status": "To Deliver and Bill"}, {"name": "SO-2", "status": "Closed"}, {"name": "SO-3", "status": "To Deliver and Bill"}]})
    out = c.get_open_sales_orders(["BOLT"], date(2026, 12, 31))
    assert [(s.due, s.qty) for s in out["BOLT"]] == [(date(2026, 10, 20), 72.0)]                                 # 6 boxes of 12; the closed and delivered ones are left out
    calls = [json.loads(p["fields"]) for u, p in c.session.calls if u.endswith("/Sales Order Item")]
    assert "customer" not in json.dumps(calls)                                                                   # no customer is read


class PostSession(FakeSession):
    def __init__(self, tables, reply=None, status=200):
        super().__init__(tables)
        self.posts, self.reply, self.status = [], reply or {"message": {"name": "COMM-0001"}}, status

    def post(self, url, json=None, timeout=None, **kw):
        self.posts.append((url, json))
        return Resp(self.reply, self.status)


def test_the_supplier_contact_is_read_from_the_supplier_then_its_contacts():
    c = client({"Supplier/SUP-A": {"name": "SUP-A", "email_id": "", "mobile_no": "", "supplier_primary_contact": ""},
                "Contact": [{"name": "C-2", "first_name": "Other", "email_id": "other@x.example", "mobile_no": "", "is_primary_contact": 0},
                            {"name": "C-1", "first_name": "Ravi", "email_id": "ravi@x.example", "mobile_no": "+91 98123 45678", "is_primary_contact": 1}]})
    got = c.get_supplier_contact("SUP-A")
    assert (got.email, got.mobile, got.contact_name) == ("ravi@x.example", "+91 98123 45678", "Ravi")
    assert [["Dynamic Link", "link_doctype", "=", "Supplier"], ["Dynamic Link", "link_name", "=", "SUP-A"]] == filters_of(c, "Contact")[0]


def test_a_supplier_without_any_contact_has_none():
    c = client({"Supplier/SUP-B": {"name": "SUP-B"}, "Contact": []})
    assert c.get_supplier_contact("SUP-B") is None


def test_item_alternatives_both_ways_only_when_two_way():
    c = client({"Item Alternative": [{"item_code": "OIL", "alternative_item_code": "OIL-B", "two_way": 0},
                                     {"item_code": "OIL-C", "alternative_item_code": "OIL", "two_way": 1},
                                     {"item_code": "OIL-D", "alternative_item_code": "OIL", "two_way": 0}]})
    assert c.get_item_alternatives("OIL") == ["OIL-B", "OIL-C"]


def test_stock_counts_leave_out_opening_stock():
    c = client({"Stock Reconciliation": [{"name": "SR-1", "posting_date": "2026-10-15", "docstatus": 1, "purpose": "Stock Reconciliation"},
                                         {"name": "SR-0", "posting_date": "2026-10-01", "docstatus": 1, "purpose": "Opening Stock"}],
                "Stock Reconciliation Item": [{"parent": "SR-1", "item_code": "BOLT", "warehouse": "Stores", "qty": 360, "current_qty": 300},
                                              {"parent": "SR-0", "item_code": "BOLT", "warehouse": "Stores", "qty": 100, "current_qty": 0}]})
    (line,) = c.get_stock_counts(date(2026, 9, 1))
    assert (line.document, line.counted_qty, line.system_qty, line.posting_date) == ("SR-1", 360, 300, date(2026, 10, 15))


def test_stock_values_and_last_purchases():
    c = client({"Bin": [{"item_code": "BOLT", "stock_value": 100.5}, {"item_code": "BOLT", "stock_value": 50}],
                "Purchase Receipt Item": [{"parent": "PR-1", "item_code": "BOLT"}, {"parent": "PR-2", "item_code": "BOLT"}],
                "Purchase Receipt": [{"name": "PR-1", "posting_date": "2026-09-01", "supplier": "SUP-A"}, {"name": "PR-2", "posting_date": "2026-10-01", "supplier": "SUP-B"}]})
    assert c.get_stock_values(["BOLT"]) == {"BOLT": 150.5}
    assert c.last_purchase_dates(["BOLT"]) == {"BOLT": (date(2026, 10, 1), "SUP-B")}


def test_email_goes_through_erpnexts_own_mail_and_is_recorded_on_the_document():
    from erp_agents.core.erp.erpnext import ERPNextClient
    s = PostSession({})
    c = ERPNextClient("http://erp.local/", "KEY", "SECRET", session=s)
    res = c.send_email(["ravi@x.example"], "Late delivery", "Please deliver", "Purchase Order", "PO-9")
    url, body = s.posts[0]
    assert url.endswith("/api/method/frappe.core.doctype.communication.email.make") and body["recipients"] == "ravi@x.example" and body["doctype"] == "Purchase Order"
    assert body["name"] == "PO-9" and body["send_email"] == 1 and res.ok and res.reference == "COMM-0001"


def test_a_refused_email_says_why():
    from erp_agents.core.erp.erpnext import ERPNextClient
    s = PostSession({}, reply={"exception": "frappe.exceptions.OutgoingEmailError: Please setup default Email Account"}, status=417)
    res = ERPNextClient("http://erp.local/", "KEY", "SECRET", session=s).send_email(["a@x.example"], "s", "b", "Supplier", "SUP-A")
    assert not res.ok and "default Email Account" in res.error


def test_documents_carry_the_supplier_id_as_well_as_its_name():
    c = client({"Purchase Order": [{"name": "PO-1", "status": "To Receive", "docstatus": 1, "supplier_name": "Alpha Ltd", "supplier": "SUP-A", "transaction_date": "2026-10-01",
                                    "schedule_date": "2026-10-10"}],
                "Purchase Order Item": [{"parent": "PO-1", "item_code": "BOLT", "qty": 10, "received_qty": 2, "uom": "Nos"}]})
    (d,) = c.get_documents("Purchase Order")
    assert (d["party"], d["party_id"], d["lines"][0]["done_qty"]) == ("Alpha Ltd", "SUP-A", 2)
