"""The tools: the questions and actions an AI client or agent may put to the application, as plain Python.

Everything an agent is allowed to know or do goes through here, and nowhere else:

  * the MCP server (mcp_server.py) publishes these tools to any MCP client, and the agents (agents/) are one such client;
  * the numbers always come from the analysis, the vendor ranking and ERPNext, never from a language model;
  * a tool either READS, or PREPARES an approval request for a person to answer. No tool orders, approves, pays or changes a setting;
  * every call is checked against the caller's role (the same roles as the REST API) and logged;
  * results are small (an agent on a laptop model pays for every word) and carry no customer names, no people's names or addresses.

No web, HTTP, scheduler or SQL library is imported here (docs/DEVELOPING.md): the layer can move into a Frappe app unchanged.
"""
from __future__ import annotations

import difflib
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Optional

from .core.common import Actor
from .inventory.agent import on_demand_payload

log = logging.getLogger(__name__)

READ, PREPARE = "read", "prepare"
MAX_RESULT_CHARS = 7000                     # a tool result is cut here: it all goes into a model's context
OVERVIEW_TTL_SECONDS = 60
KIND_WORDS = {"inventory.alert": "stock alert", "inventory.reorder": "reorder suggestion", "procurement.po": "purchase order request", "procurement.pr": "purchase requisition request",
              "procurement.manual": "request needing a decision", "inventory.monitor_down": "warning"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    properties: dict[str, dict[str, Any]]
    required: tuple[str, ...]
    role: str                                # viewer | approver (what ERPNext lets the caller do)
    effect: str                              # read | prepare

    def input_schema(self) -> dict[str, Any]:
        return {"type": "object", "properties": self.properties, "required": list(self.required)}


ITEM = {"type": "string", "description": "Item code, for example ELE-0025"}

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("stock_overview", "Which items need attention (out of stock, low, or running down), worst first, plus the number of items in each state. Start here.",
             {"group": {"type": "string", "description": "Only this item group"}, "limit": {"type": "integer", "description": "How many items to list (default 10, at most 25)"}}, (), "viewer", READ),
    ToolSpec("stock_status", "Stock, usage and days of cover for ONE item: on hand, available, on order, in transit, daily use, trend, lead time, suggested order quantity.",
             {"item_code": ITEM}, ("item_code",), "viewer", READ),
    ToolSpec("usage_history", "Quantity delivered to customers each week for ONE item, oldest first, to see growth, decline, seasons or lumpy demand.",
             {"item_code": ITEM, "weeks": {"type": "integer", "description": "How many weeks back, 1 to 52 (default 13)"}}, ("item_code",), "viewer", READ),
    ToolSpec("vendor_options", "Compare the vendors that can supply ONE item for a quantity: price each, total with duties and taxes, delivery days, on-time record, score. Best first.",
             {"item_code": ITEM, "qty": {"type": "number", "description": "Quantity to buy, in the item's unit"}}, ("item_code", "qty"), "viewer", READ),
    ToolSpec("open_orders", "Purchase orders (or requisitions) that are still open or draft, with vendor, ordered, received and still-to-come quantity. Optionally for one item. "
                            "An order past its due date with quantity still to come is marked late, with days_late; late_count says how many.",
             {"item_code": ITEM, "kind": {"type": "string", "enum": ["purchase_order", "requisition"], "description": "Default purchase_order"}}, (), "viewer", READ),
    ToolSpec("waiting_requests", "Requests waiting for a person's answer (stock alerts, reorder suggestions, order requests), optionally for one item.",
             {"item_code": ITEM}, (), "viewer", READ),
    ToolSpec("prepare_order_request", "Prepare a purchase request for a person to approve: compares vendors and sends the usual request with Create order, Change quantity and Change vendor. "
                                      "It orders NOTHING by itself. Use it only when a person has asked for an order.",
             {"item_code": ITEM, "qty": {"type": "number", "description": "Quantity; leave out to use the suggested quantity"},
              "reason": {"type": "string", "description": "One sentence: why this order, for the approver"}}, ("item_code", "reason"), "approver", PREPARE),
)
SPEC = {t.name: t for t in TOOLS}


def _r(x: Any, nd: int = 2) -> Any:
    if x is None or isinstance(x, (str, bool)):
        return x
    try:
        v = round(float(x), nd)
    except (TypeError, ValueError):
        return x
    return int(v) if v == int(v) and abs(v) < 1e15 else v


def _days_late(due: Any, still_to_come: float, today: date) -> int:
    """How many days past its due date an order is while quantity is still to come; 0 if it is not late (or has no usable date)."""
    if not due or still_to_come <= 0:
        return 0
    try:
        when = due if isinstance(due, date) else date.fromisoformat(str(due)[:10])
    except ValueError:
        return 0
    return max((today - when).days, 0)


def _clip(text: Any, n: int = 200) -> str:
    """Whitespace tidied and cut at a word (with an ellipsis) when it is too long, never in the middle of one."""
    clean = " ".join(str(text or "").split())
    if len(clean) <= n:
        return clean
    return clean[: n - 1].rsplit(" ", 1)[0].rstrip(" ,;:(") + "\u2026"


def coerce_arguments(spec: ToolSpec, args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Make the arguments fit the tool: numbers sent as text become numbers (small models do that), unknown names are dropped and reported, a wrong value is an error."""
    out: dict[str, Any] = {}
    ignored: list[str] = []
    for key, val in (args or {}).items():
        prop = spec.properties.get(key)
        if prop is None:
            ignored.append(str(key))
            continue
        kind = prop.get("type")
        try:
            if kind == "integer":
                out[key] = int(float(val))
            elif kind == "number":
                v = float(str(val).replace(",", "")) if isinstance(val, str) else float(val)
                if v != v or v in (float("inf"), float("-inf")):
                    raise ValueError("not a finite number")
                out[key] = v
            elif kind == "string":
                if val is None:
                    continue
                out[key] = str(val).strip()
            else:
                out[key] = val
        except (TypeError, ValueError):
            raise ValueError(f"'{key}' must be {'a whole number' if kind == 'integer' else 'a number' if kind == 'number' else 'text'}, not {val!r}")
        if "enum" in prop and out.get(key) not in prop["enum"]:
            raise ValueError(f"'{key}' must be one of {', '.join(prop['enum'])}")
    missing = [k for k in spec.required if out.get(k) in (None, "")]
    if missing:
        raise ValueError("missing: " + ", ".join(missing))
    return out, ignored


class Toolkit:
    def __init__(self, app: Any, log_call: Optional[Callable[..., None]] = None) -> None:
        self.app = app
        self.log_call = log_call                        # log_call(actor, via, tool, args, ok, ms, task_id, error)
        self._overview: Optional[tuple[float, list[dict[str, Any]]]] = None
        self._items: Optional[tuple[float, list[Any]]] = None

    # ---------------------------------------------------------------- the one entry point
    def specs(self, actor: Optional[Actor] = None) -> list[ToolSpec]:
        return [t for t in TOOLS if actor is None or actor.has_role(t.role)]

    def call(self, name: str, arguments: Optional[dict[str, Any]], actor: Actor, via: str = "api", task_id: Optional[str] = None) -> dict[str, Any]:
        """Run one tool. Never raises: a refusal or a failure comes back as {"error": "..."} so an agent can read it and carry on."""
        t0 = time.monotonic()
        spec = SPEC.get(name)
        args: dict[str, Any] = dict(arguments or {})
        error: Optional[str] = None
        result: dict[str, Any]
        if spec is None:
            result, error = {"error": f"unknown tool '{name}'. Available: {', '.join(SPEC)}"}, "unknown tool"
        elif not actor.has_role(spec.role):
            result, error = {"error": f"not allowed: '{name}' needs the {spec.role} level in ERPNext"}, "not allowed"
        else:
            try:
                args, ignored = coerce_arguments(spec, args)
                result = getattr(self, "_" + name)(actor, **args)
                if ignored:
                    result["ignored_arguments"] = ignored
                if "error" in result:
                    error = str(result["error"])[:200]
            except ValueError as exc:
                result, error = {"error": str(exc)}, str(exc)[:200]
            except Exception as exc:  # noqa: BLE001 - a tool must never take the agent down with it
                log.exception("tool %s failed", name)
                result, error = {"error": f"could not do that just now ({type(exc).__name__}: {_clip(exc, 160)})"}, f"{type(exc).__name__}"
        if len(json.dumps(result, default=str)) > MAX_RESULT_CHARS:
            result = {"warning": "result shortened", **_shorten(result)}
        if self.log_call is not None:
            try:
                self.log_call(actor.id, via, name, args, error is None, int((time.monotonic() - t0) * 1000), task_id, error)
            except Exception:  # noqa: BLE001
                log.exception("could not log the call of %s", name)
        return result

    # ---------------------------------------------------------------- helpers
    def _all_items(self) -> list[Any]:
        now = time.monotonic()
        if self._items is None or now - self._items[0] > OVERVIEW_TTL_SECONDS:
            self._items = (now, self.app.erp.list_items())
        return self._items[1]

    def _item(self, code: str) -> tuple[Optional[Any], Optional[dict[str, Any]]]:
        """(item, None), or (None, an error that suggests what was probably meant)."""
        items = self._all_items()
        want = code.strip().lower()
        for i in items:
            if i.code.lower() == want or i.name.lower() == want:
                return i, None
        names = {i.code: i for i in items}
        close = difflib.get_close_matches(code.upper(), list(names), n=3, cutoff=0.6)
        close += [i.code for i in items if want and want in i.name.lower() and i.code not in close][:3]
        return None, {"error": f"no item '{code}'", "did_you_mean": [{"item_code": c, "item_name": names[c].name} for c in close[:5]]}

    def _rows(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._overview is None or now - self._overview[0] > OVERVIEW_TTL_SECONDS:
            self._overview = (now, self.app.inventory.overview())
        return self._overview[1]

    # ---------------------------------------------------------------- the tools
    def _stock_overview(self, actor: Actor, group: Optional[str] = None, limit: int = 10) -> dict[str, Any]:
        limit = max(1, min(int(limit or 10), 25))
        rows = [r for r in self._rows() if not group or (r.get("group") or "").lower() == group.lower()]
        counts = Counter(r["status"] for r in rows)
        needing = sorted((r for r in rows if r["status"] in ("BREACH", "EARLY_WARNING")), key=lambda r: (-r["severity_rank"], r["cover_days"] if r["cover_days"] is not None else 1e9, r["item_code"]))
        return {"items_total": len(rows), "counts": dict(counts), "needing_attention": len(needing), "listed": min(limit, len(needing)),
                "items": [{"item_code": r["item_code"], "item_name": r["item_name"], "uom": r["uom"], "status": r["status"], "on_hand": _r(r["on_hand"]), "available": _r(r["available"]),
                           "on_order": _r(r["incoming"]), "in_transit": _r(r.get("in_transit")), "daily_use": _r(r["daily_use"]), "cover_days": _r(r["cover_days"], 1),
                           "lead_days": _r(r["lead_time_days"], 1), "next_arrival": r["next_arrival"], "why": _clip(r["headline"], 260)} for r in needing[:limit]]}

    def _stock_status(self, actor: Actor, item_code: str) -> dict[str, Any]:
        item, err = self._item(item_code)
        if err:
            return err
        a, _src = self.app.inventory.analyze_one(item.code)
        d = a.to_dict()
        u = d.get("usage") or {}
        return {"item_code": a.item_code, "item_name": a.item_name, "uom": a.uom, "status": a.status, "on_hand": _r(a.on_hand), "reserved": _r(a.reserved), "available": _r(a.available),
                "on_order": _r(a.incoming), "in_transit": _r(a.in_transit), "position": _r(a.position), "daily_use": _r(u.get("planning_rate")), "recent_daily_use": _r(u.get("recent_rate")),
                "base_daily_use": _r(u.get("base_rate")), "trend": u.get("trend"), "trend_pct": _r(u.get("trend_pct"), 0), "cover_days": _r(a.cover_days_on_hand, 1),
                "lead_days": _r(a.lead_time_days, 1), "stockout_date": a.stockout_date, "next_arrival": a.next_arrival, "suggested_qty": _r(a.suggested_qty),
                "suggested_qty_basis": _clip(a.suggested_qty_basis, 200), "reasons": [_clip(r["text"], 200) for r in d.get("reasons", [])][:3], "notes": [_clip(n, 160) for n in a.notes][:3],
                "data_days": u.get("data_days"), "in_transit_counted_as": (a.policy or {}).get("in_transit_stock")}

    def _usage_history(self, actor: Actor, item_code: str, weeks: int = 13) -> dict[str, Any]:
        item, err = self._item(item_code)
        if err:
            return err
        weeks = max(1, min(int(weeks or 13), 52))
        today = self.app.inventory.today()
        days = self.app.erp.get_consumption([item.code], today - timedelta(days=weeks * 7), today).get(item.code, [])
        buckets = [0.0] * weeks
        for d in days:
            age = (today - d.day).days
            if 0 < age <= weeks * 7:
                buckets[weeks - 1 - (age - 1) // 7] += d.qty
        total = sum(buckets)
        return {"item_code": item.code, "item_name": item.name, "uom": item.uom, "weeks": weeks, "total": _r(total), "average_per_day": _r(total / (weeks * 7)),
                "weeks_with_sales": sum(1 for b in buckets if b > 0),
                "weekly": [{"week_ending": (today - timedelta(days=7 * (weeks - 1 - i) + 1)).isoformat(), "units": _r(b)} for i, b in enumerate(buckets)]}

    def _vendor_options(self, actor: Actor, item_code: str, qty: float) -> dict[str, Any]:
        item, err = self._item(item_code)
        if err:
            return err
        if not 0 < qty <= 1_000_000_000:
            return {"error": "qty must be more than zero"}
        a, _src = self.app.inventory.analyze_one(item.code)
        ranking = self.app.procurement.preview(item.code, qty, a.cover_days_on_hand, float(a.lead_time_days or 7), item.uom)
        vendors = sorted(ranking["vendors"], key=lambda v: (not v["eligible"], -(v.get("score") or 0)))
        ok = [v for v in vendors if v["eligible"]]
        return {"item_code": item.code, "item_name": item.name, "qty": _r(qty), "uom": item.uom, "best": ok[0]["name"] if ok else None, "days_of_cover_now": _r(a.cover_days_on_hand, 1),
                "vendors": [{"vendor": v["name"], "supplier_id": v.get("supplier"), "country": v.get("country"), "eligible": v["eligible"], "price_each": _r(v.get("unit_price")), "total": _r(v.get("total"), 0),
                             "duties_and_taxes_in_cost": _r(v.get("tax_total"), 0), "delivery_days": _r(v.get("lead_days"), 1), "on_time_pct": _r((v.get("on_time_rate") or 0) * 100, 0) if v.get("on_time_rate") is not None else None,
                             "past_orders": v.get("orders"), "score": _r(v.get("score"), 1), "arrives_after_stockout": bool(v.get("arrives_after_stockout")), "why_not": _clip(v.get("why_not"), 160)} for v in vendors[:8]],
                "notes": [_clip(n, 160) for n in ranking.get("notes", [])][:3]}

    def _open_orders(self, actor: Actor, item_code: Optional[str] = None, kind: str = "purchase_order") -> dict[str, Any]:
        code = None
        if item_code:
            item, err = self._item(item_code)
            if err:
                return err
            code = item.code
        doctype = "Material Request" if kind == "requisition" else "Purchase Order"
        docs = [d for d in self.app.erp.get_documents(doctype, item_code=code, limit=100) if d["docstatus"] == 0 or (d["docstatus"] == 1 and d["status"] not in ("Completed", "Closed", "Cancelled", "Delivered", "Received"))]
        today = self.app.inventory.today()
        rows = []
        for d in docs:
            lines = [l for l in d["lines"] if not code or l.get("item_code") == code] or d["lines"]
            ordered, got = sum(float(l.get("qty") or 0) for l in lines), sum(float(l.get("done_qty") or 0) for l in lines)
            row = {"document": d["name"], "vendor": d.get("party"), "item": (lines[0].get("item_code") if lines else None), "uom": (lines[0].get("uom") if lines else None), "ordered": _r(ordered),
                   "received": _r(got), "still_to_come": _r(max(ordered - got, 0)), "due": d.get("due"), "state": d["status"] if d["docstatus"] == 1 else "Draft (not submitted)"}
            late = _days_late(d.get("due"), max(ordered - got, 0), today)
            if late:
                row.update(late=True, days_late=late)
            rows.append(row)
        listed = rows[:15]
        return {"kind": kind, "item_code": code, "open_documents": len(docs), "late_count": sum(1 for r in rows if r.get("late")), "listed": len(listed), "documents": listed}

    def _waiting_requests(self, actor: Actor, item_code: Optional[str] = None) -> dict[str, Any]:
        code = None
        if item_code:
            item, err = self._item(item_code)
            if err:
                return err
            code = item.code
        pending = [r for r in self.app.approvals.list("pending", 200) if not code or (r.request.facts or {}).get("item_code") == code]
        return {"item_code": code, "waiting": len(pending), "requests": [{"request_id": r.id, "type": KIND_WORDS.get(r.kind, "request"), "title": _clip(r.request.title, 120), "urgency": r.request.severity,
                                                                         "asked": r.created_at.date().isoformat(), "item_code": (r.request.facts or {}).get("item_code"), "qty": _r((r.request.facts or {}).get("qty"))} for r in pending[:15]]}

    def _prepare_order_request(self, actor: Actor, item_code: str, reason: str, qty: Optional[float] = None) -> dict[str, Any]:
        item, err = self._item(item_code)
        if err:
            return err
        if qty is not None and not 0 < qty <= 1_000_000_000:
            return {"error": "qty must be more than zero"}
        moq_of = getattr(self.app.erp, "item_min_order_qty", None)
        moq = moq_of(item.code) if (moq_of and qty) else None
        if moq and qty < moq:
            return {"error": f"the ERP does not accept an order below this item's minimum order quantity of {_r(moq)} {item.uom}"}
        a, _src = self.app.inventory.analyze_one(item.code)
        payload = on_demand_payload(a, actor.id, qty, _clip(reason, 300))
        res = self.app.procurement.request_purchase(payload, actor.id)
        rec, status = res.get("approval"), res["status"]
        out: dict[str, Any] = {"status": status, "item_code": item.code}
        if status == "need_qty":
            return {**out, "error": "there is no usage history to size an order from: ask the person for a quantity", "note": "nothing was prepared"}
        if status == "refused" or rec is None:
            return {**out, "error": f"could not prepare an order request ({res.get('why', 'unknown reason')})", "note": "nothing was prepared"}
        f = rec.request.facts or {}
        out.update({"request_id": rec.id, "request_title": _clip(rec.request.title, 140), "qty": _r(f.get("qty")), "vendor": f.get("supplier"), "total": _r(f.get("total"), 0)})
        out["note"] = {"created": "The request was sent to the approvers. Nothing is ordered until a person presses Create order.",
                       "refreshed": "A request that was already waiting had to be rebuilt; the new one was sent. Nothing is ordered until a person presses Create order.",
                       "waiting": "A request for this item is already waiting for a person's answer; no second one was made.",
                       "reorder_waiting": "A reorder suggestion for this item is already waiting for a person's answer; no request was made.",
                       "manual": "No vendor could be chosen automatically; a person was asked to decide."}.get(status, "")
        return out


def _shorten(result: dict[str, Any]) -> dict[str, Any]:
    """A result that is too long loses the tail of its longest list."""
    out = dict(result)
    lists = [k for k, v in out.items() if isinstance(v, list)]
    while lists and len(json.dumps(out, default=str)) > MAX_RESULT_CHARS:
        k = max(lists, key=lambda x: len(out[x]))
        if len(out[k]) <= 1:
            break
        out[k] = out[k][: max(1, len(out[k]) // 2)]
    return out
