"""Cancelling or changing an order that this application already created in ERPNext: the ERP layer's safety rules first, then the flows on top of it."""
import json

import pytest

from erp_agents.erp.erpnext import ERPNextClient


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._d


class Session:
    """A fake ERPNext that knows one purchase order and records what it was asked to do."""

    def __init__(self, doc=None, read_status=200, write=(200, {"message": "ok"})):
        self.doc, self.read_status, self.write, self.calls, self.headers = doc, read_status, write, [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, None))
        if self.read_status != 200 or self.doc is None:
            return Resp({"exc": "not found"}, self.read_status if self.doc is not None else 404)
        return Resp({"data": self.doc})

    def delete(self, url, timeout=None, **kw):
        self.calls.append(("DELETE", url, kw.get("headers")))
        return Resp(self.write[1], self.write[0])

    def post(self, url, json=None, timeout=None, **kw):
        self.calls.append(("POST", url, json))
        return Resp(self.write[1], self.write[0])

    def put(self, url, json=None, timeout=None, **kw):
        self.calls.append(("PUT", url, json))
        return Resp(self.write[1], self.write[0])


def order(**kw):
    return {"name": "PUR-ORD-2026-00743", "docstatus": 0, "custom_agent_ref": "abc123", "per_received": 0, "per_billed": 0, **kw}


def erp(session):
    return ERPNextClient("http://erp.local", "K", "S", session=session)


def writes(session):
    return [c for c in session.calls if c[0] != "GET"]


class Acting:
    def __init__(self, session, user="priya@example.com", csrf="tok"):
        self.session, self.user, self.csrf = session, user, csrf


def test_a_draft_that_we_created_is_deleted():
    s = Session(order())
    res = erp(s).cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
    assert res.ok and res.note == "deleted the draft"
    assert writes(s) == [("DELETE", "http://erp.local/api/resource/Purchase Order/PUR-ORD-2026-00743", None)]


def test_a_submitted_order_that_we_created_is_cancelled_not_deleted():
    s = Session(order(docstatus=1))
    res = erp(s).cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
    assert res.ok and res.note == "cancelled it"
    assert writes(s) == [("POST", "http://erp.local/api/method/frappe.client.cancel", {"doctype": "Purchase Order", "name": "PUR-ORD-2026-00743"})]


def test_a_revised_order_carries_the_proposal_id_with_a_suffix_and_is_still_ours():
    s = Session(order(custom_agent_ref="abc123-2"))
    assert erp(s).cancel_purchase_order("PUR-ORD-2026-00743", "abc123").ok
    for other in ("abc1234", "ABC123", "zzz", ""):                                                      # another proposal, or no reference at all: not ours, left alone
        s = Session(order(custom_agent_ref=other))
        res = erp(s).cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
        assert not res.ok and "not created by the Stock Assistant for this request" in res.error and writes(s) == []
    assert not erp(Session(order())).cancel_purchase_order("PUR-ORD-2026-00743", "").ok                  # no reference given: nothing is touched


@pytest.mark.parametrize("fields,words", [({"per_received": 40}, "40% of it has been received"), ({"per_billed": 100}, "100% billed"), ({"docstatus": 1, "per_received": 100}, "100% of it has been received")])
def test_an_order_with_goods_or_an_invoice_against_it_is_left_to_a_person(fields, words):
    s = Session(order(**fields))
    res = erp(s).cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
    assert not res.ok and words in res.error and "in ERPNext" in res.error and writes(s) == []


def test_an_order_that_is_already_gone_or_cancelled_is_a_success_with_nothing_to_do():
    s = Session(None)
    assert erp(s).cancel_purchase_order("PUR-ORD-2026-00743", "abc123").note == "already gone" and writes(s) == []
    s = Session(order(docstatus=2))
    res = erp(s).cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
    assert res.ok and res.note == "already cancelled" and writes(s) == []


def test_a_refusal_from_erpnext_says_who_lacks_what():
    svc = erp(Session(order(), write=(403, {"exc": "PermissionError: No permission for Purchase Order"})))
    res = svc.cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
    assert not res.ok and "does not allow the service account to delete" in res.error and "Delete permission on Purchase Order" in res.error
    sub = erp(Session(order(docstatus=1), write=(403, {"exc": "PermissionError"}))).cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
    assert "to cancel a purchase order" in sub.error and "Cancel permission" in sub.error
    mine = Session(order(docstatus=0))
    asked = erp(Session(order())).cancel_purchase_order("PUR-ORD-2026-00743", "abc123", acting=Acting(Session(order(), write=(403, {"exc": "no"})), "priya@example.com"))
    assert "does not allow priya@example.com to delete" in asked.error and mine is not None
    other = erp(Session(order(), write=(417, {"exc": "Cannot delete: linked with Purchase Receipt"}))).cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
    assert not other.ok and "refused to delete it (HTTP 417)" in other.error


def test_the_person_acting_is_the_one_who_deletes_with_their_own_session_and_token():
    mine = Session(order())
    service = Session(order())
    res = erp(service).cancel_purchase_order("PUR-ORD-2026-00743", "abc123", acting=Acting(mine))
    assert res.ok and writes(service) == []                                                              # the service account only reads
    assert writes(mine) == [("DELETE", "http://erp.local/api/resource/Purchase Order/PUR-ORD-2026-00743", {"X-Frappe-CSRF-Token": "tok"})]


def test_an_unreachable_erpnext_changes_nothing_and_says_so():
    import requests

    class Down(Session):
        def get(self, *a, **k):
            raise requests.ConnectionError("refused")
    res = erp(Down(order())).cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
    assert not res.ok and "could not reach ERPNext" in res.error
    broken = erp(Session(order(), read_status=500)).cancel_purchase_order("PUR-ORD-2026-00743", "abc123")
    assert not broken.ok and "HTTP 500" in broken.error


# ---------------------------------------------------------------- changing the quantity in the same order (reported 24 Oct 2026: it was deleted and re-created)
ROW = {"name": "row1", "item_code": "BOLT-M8", "qty": 500, "rate": 36.87, "conversion_factor": 1, "uom": "Nos", "schedule_date": "2026-11-02", "received_qty": 0, "billed_amt": 0,
       "description": "Bolt M8 x 40"}


def test_a_draft_is_saved_with_the_new_quantity_in_the_same_order():
    s = Session(order(items=[dict(ROW)], grand_total=29496.0))
    res = erp(s).update_purchase_order_qty("PUR-ORD-2026-00743", "abc123", "BOLT-M8", 800)
    assert res.ok and res.name == "PUR-ORD-2026-00743" and not res.submitted
    (put,) = writes(s)
    assert put[0] == "PUT" and put[1].endswith("/api/resource/Purchase Order/PUR-ORD-2026-00743")
    assert put[2]["items"][0]["qty"] == 800 and put[2]["items"][0]["name"] == "row1" and put[2]["items"][0]["rate"] == 36.87      # same line, same price
    assert not [c for c in s.calls if c[0] in ("DELETE",) or "cancel" in c[1]]                                                     # nothing deleted or cancelled


def test_a_submitted_order_is_changed_with_update_items_and_stays_submitted():
    s = Session(order(docstatus=1, status="To Receive and Bill", items=[dict(ROW)], grand_total=29496.0))
    res = erp(s).update_purchase_order_qty("PUR-ORD-2026-00743", "abc123", "BOLT-M8", 800)
    assert res.ok and res.submitted and res.total == 29496.0
    (post,) = writes(s)
    assert post[1] == "http://erp.local/api/method/erpnext.controllers.accounts_controller.update_child_qty_rate"
    assert post[2]["parent_doctype"] == "Purchase Order" and post[2]["parent_doctype_name"] == "PUR-ORD-2026-00743" and post[2]["child_docname"] == "items"
    (line,) = json.loads(post[2]["trans_items"])
    assert line == {"docname": "row1", "item_code": "BOLT-M8", "qty": 800, "rate": 36.87, "conversion_factor": 1, "uom": "Nos", "schedule_date": "2026-11-02", "description": "Bolt M8 x 40"}


def test_update_items_is_sent_every_other_line_unchanged_so_erpnext_deletes_none():
    """ERPNext's Update Items deletes any line it is not sent (validate_and_delete_children), and blanks a description that is not sent."""
    other = {"name": "row2", "item_code": "NUT-M8", "qty": 200, "rate": 3.1, "conversion_factor": 1, "uom": "Nos", "schedule_date": "2026-11-02", "description": "Nut M8"}
    s = Session(order(docstatus=1, status="To Receive and Bill", items=[dict(ROW), dict(other)]))
    assert erp(s).update_purchase_order_qty("PUR-ORD-2026-00743", "abc123", "BOLT-M8", 800).ok
    lines = {l["docname"]: l for l in json.loads(writes(s)[0][2]["trans_items"])}
    assert set(lines) == {"row1", "row2"} and lines["row1"]["qty"] == 800
    assert {k: lines["row2"][k] for k in ("item_code", "qty", "rate", "uom", "schedule_date", "description")} == {k: other[k] for k in ("item_code", "qty", "rate", "uom", "schedule_date", "description")}


@pytest.mark.parametrize("doc,qty,why", [
    (order(custom_agent_ref="someone-else", items=[dict(ROW)]), 800, "not created by the Stock Assistant"),
    (order(docstatus=2, items=[dict(ROW)]), 800, "cancelled in ERPNext"),
    (order(docstatus=1, status="Closed", items=[dict(ROW)]), 800, "Closed in ERPNext"),
    (order(docstatus=1, status="On Hold", items=[dict(ROW)]), 800, "On Hold in ERPNext"),
    (order(docstatus=1, items=[{**ROW, "received_qty": 300}]), 200, "300 have already been received"),
    (order(docstatus=1, items=[{**ROW, "billed_amt": 11061.0}]), 100, "has already been invoiced"),
    (order(items=[dict(ROW), dict(ROW, name="row2")]), 800, "2 lines for BOLT-M8"),
])
def test_what_cannot_be_changed_is_left_alone(doc, qty, why):
    s = Session(doc)
    res = erp(s).update_purchase_order_qty("PUR-ORD-2026-00743", "abc123", "BOLT-M8", qty)
    assert not res.ok and why in res.error and not writes(s)


def test_a_missing_order_and_a_refusal_are_reported():
    assert "no longer in ERPNext" in erp(Session(None)).update_purchase_order_qty("X", "abc123", "BOLT-M8", 5).error
    s = Session(order(items=[dict(ROW)]), write=(403, {"exc_type": "PermissionError", "_server_messages": "[]"}))
    res = erp(s).update_purchase_order_qty("PUR-ORD-2026-00743", "abc123", "BOLT-M8", 800)
    assert not res.ok and "Write permission on Purchase Order" in res.error and "the service account" in res.error


def test_in_the_console_the_change_is_made_under_the_persons_own_login():
    service, mine = Session(order(docstatus=1, items=[dict(ROW)])), Session(order(docstatus=1, items=[dict(ROW)]))
    res = erp(service).update_purchase_order_qty("PUR-ORD-2026-00743", "abc123", "BOLT-M8", 800, acting=Acting(mine))
    assert res.ok and not writes(service) and len(writes(mine)) == 1
