"""The stock pack's REST routes: thresholds and the procurement settings, the inventory views and runs, the vendor selector, the "Ask why" assistant, the chat, and the
agents' brief, second opinion and question endpoints.

The core mounts them on a router that answers 404 when the pack is switched off (after the usual sign-in check, so a stranger learns nothing). `require(role)` is the core's role
check, the same as on every other route.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from erp_agents.core.common import Actor
from erp_agents.core.erp.base import DEFAULT_IN_TRANSIT, DEFAULT_STOCK_ADD, DEFAULT_STOCK_SUBTRACT, IN_TRANSIT_LABEL, IN_TRANSIT_MODES, STOCK_COLUMNS
from erp_agents.core.settings_store import PermissionDenied, SettingsError
from .assistant.chat import ChatLimit
from .settings import MONITOR_COLUMNS


class ChatContext(BaseModel):
    items: list[str] = Field(default_factory=list, max_length=5)


class ChatBody(BaseModel):
    question: str = Field(min_length=1, max_length=300)
    context: Optional[ChatContext] = None
    explain: bool = False


class AskBody(BaseModel):
    approval_id: str
    question: str


class AgentAskBody(BaseModel):
    question: str = Field(min_length=1, max_length=300)


class PolicyUpdate(BaseModel):
    set: dict[str, Any] = Field(default_factory=dict, description="Fields to override. null switches a nullable threshold off.")
    clear: list[str] = Field(default_factory=list, description="Fields to inherit again.")


def routes(router: Any, ctx: Any, deps: Any) -> None:
    require, MAX_LIST_LIMIT = deps.require, deps.MAX_LIST_LIMIT

    @router.get("/api/v1/settings/procurement")
    def get_procurement(_: Actor = Depends(require("viewer"))) -> dict:
        return {"effective": ctx.settings.procurement().model_dump(), "overrides": ctx.settings._section_overrides("procurement")}

    @router.put("/api/v1/settings/procurement")
    def put_procurement(body: PolicyUpdate, actor: Actor = Depends(require("admin"))) -> dict:
        try:
            ctx.settings.update_procurement(actor, body.set, body.clear)
        except SettingsError as exc:
            raise HTTPException(422, str(exc))
        return {"effective": ctx.settings.procurement().model_dump(), "overrides": ctx.settings._section_overrides("procurement")}

    @router.get("/api/v1/settings/policy/{scope_type}/{scope_key:path}")
    def get_policy(scope_type: str, scope_key: str, group: Optional[str] = None, _: Actor = Depends(require("viewer"))) -> dict:
        """scope_type: global | group | item. For global use key '*'. For items, pass ?group= to see the inherited result."""
        if scope_type not in ("global", "group", "item"):
            raise HTTPException(404, "unknown scope")
        key = "*" if scope_type == "global" else scope_key
        item = key if scope_type == "item" else None
        grp = key if scope_type == "group" else group
        try:
            policy, sources = ctx.settings.policy_with_sources(item, grp)
        except SettingsError as exc:
            raise HTTPException(422, str(exc))
        return {"overrides": ctx.settings.get_overrides(scope_type, key), "effective": policy.model_dump(), "sources": sources}

    @router.put("/api/v1/settings/policy/{scope_type}/{scope_key:path}")
    def put_policy(scope_type: str, scope_key: str, body: PolicyUpdate, actor: Actor = Depends(require("admin"))) -> dict:
        try:
            policy = ctx.settings.update_policy(actor, scope_type, scope_key, body.set, body.clear)
        except SettingsError as exc:
            raise HTTPException(422, str(exc))
        except PermissionDenied as exc:
            raise HTTPException(403, str(exc))
        key = "*" if scope_type == "global" else scope_key
        return {"overrides": ctx.settings.get_overrides(scope_type, key), "validated_against_global": policy.model_dump()}

    @router.delete("/api/v1/settings/policy/{scope_type}/{scope_key:path}")
    def delete_policy(scope_type: str, scope_key: str, actor: Actor = Depends(require("admin"))) -> dict:
        ctx.settings.reset_scope(actor, scope_type, scope_key)
        return {"ok": True}

    @router.get("/api/v1/inventory/items")
    def list_items(_: Actor = Depends(require("viewer"))) -> list[dict]:
        """Every ERP item with live stock, days of cover and the thresholds in force (for the admin screen)."""
        return ctx.inventory.overview()

    @router.get("/api/v1/inventory/stock-options")
    def stock_options(_: Actor = Depends(require("viewer"))) -> dict:
        """What the stock-calculation settings can choose from: the ERP stock columns and its warehouses."""
        columns = [{"column": c, "label": label, "default": "subtract" if c in DEFAULT_STOCK_SUBTRACT else "add" if c in DEFAULT_STOCK_ADD else "not used"}
                   for c, (label, _attr, _phrase) in STOCK_COLUMNS.items()]
        try:
            warehouses, error = ctx.erp.list_warehouses(), None
        except Exception as exc:  # noqa: BLE001 - the screen still works, the filter just cannot offer a list
            warehouses, error = [], f"could not read warehouses from the ERP: {exc}"
        transit = {"label": IN_TRANSIT_LABEL, "default": DEFAULT_IN_TRANSIT, "modes": list(IN_TRANSIT_MODES)}
        return {"columns": columns, "in_transit": transit, "warehouses": warehouses, "warehouses_error": error,
                "monitor_columns": [{"column": c, "label": label} for c, label in MONITOR_COLUMNS.items()]}

    @router.get("/api/v1/inventory/analysis/{item_code}")
    def analysis(item_code: str, _: Actor = Depends(require("viewer"))) -> dict:
        try:
            a, sources = ctx.inventory.analyze_one(item_code)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        return {"analysis": a.to_dict(), "policy_sources": sources}

    @router.post("/api/v1/inventory/run")
    def run_agent(dry_run: bool = True, _: Actor = Depends(require("admin"))) -> dict:
        """Dry-run by default: shows what the agent *would* do without sending anything."""
        return ctx.inventory.run(dry_run=dry_run).to_dict()

    @router.get("/api/v1/inventory/alerts")
    def list_alerts(status: Optional[str] = None, item_code: Optional[str] = None, limit: int = Query(100, ge=1, le=MAX_LIST_LIMIT),
                    _: Actor = Depends(require("viewer"))) -> list[dict]:
        return [
            {"id": a.id, "item_code": a.item_code, "kind": a.kind, "severity": a.severity, "status": a.status,
             "created_at": a.created_at.isoformat(), "approval_id": a.approval_id, "status_note": a.status_note,
             "reasoning": a.reasoning_text}
            for a in ctx.alerts.list(status, item_code, limit)
        ]

    @router.get("/api/v1/inventory/runs")
    def runs(limit: int = Query(20, ge=1, le=MAX_LIST_LIMIT), _: Actor = Depends(require("viewer"))) -> list[dict]:
        rows = ctx.db.query("SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT ?", (limit,))
        return [{"id": r["id"], "started_at": r["started_at"], "dry_run": bool(r["dry_run"]), "summary": json.loads(r["summary_json"] or "{}")} for r in rows]

    @router.post("/api/v1/procurement/run")
    def run_procurement(dry_run: bool = True, _: Actor = Depends(require("admin"))) -> dict:
        """Dry-run by default: shows which vendor it would recommend for each approved reorder, sends nothing."""
        return ctx.procurement.run(dry_run=dry_run).to_dict()

    @router.get("/api/v1/procurement/proposals")
    def list_proposals(status: Optional[str] = None, item_code: Optional[str] = None, limit: int = Query(100, ge=1, le=MAX_LIST_LIMIT),
                       _: Actor = Depends(require("viewer"))) -> list[dict]:
        return [p.to_dict() for p in ctx.proposals.list(status, item_code, limit)]

    @router.get("/api/v1/procurement/preview/{item_code}")
    def preview_vendors(item_code: str, qty: float, cover_days: Optional[float] = None, lead_time_days: float = 7.0,
                        _: Actor = Depends(require("viewer"))) -> dict:
        """How the vendors for an item would rank for a given quantity (nothing is stored or sent)."""
        if qty <= 0:
            raise HTTPException(422, "qty must be above zero")
        try:
            return ctx.procurement.preview(item_code, qty, cover_days, lead_time_days)
        except Exception as exc:  # noqa: BLE001 - ERP errors should read clearly in the UI
            raise HTTPException(502, f"could not read vendors from the ERP: {exc}")

    @router.get("/api/v1/assistant/status")
    def assistant_status(_: Actor = Depends(require("viewer"))) -> dict:
        return ctx.assistant.status()

    @router.post("/api/v1/assistant/ask")
    def assistant_ask(body: AskBody, actor: Actor = Depends(require("approver"))) -> dict:
        try:
            return ctx.assistant.ask(body.approval_id, body.question, actor)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.get("/api/v1/assistant/history/{approval_id}")
    def assistant_history(approval_id: str, _: Actor = Depends(require("approver"))) -> list[dict]:
        return ctx.assistant.history(approval_id)

    @router.post("/api/v1/chat")
    def chat(body: ChatBody, actor: Actor = Depends(require("viewer"))) -> dict:
        ctx_dict = {"items": [c[:80] for c in body.context.items]} if body.context else None
        try:
            if body.explain:
                return ctx.chat.explain(body.question, actor.id, actor.has_role("approver"), ctx_dict)
            return ctx.chat.ask(body.question, actor.id, actor.has_role("approver"), ctx_dict, background=True)
        except ChatLimit as exc:
            raise HTTPException(429, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.get("/api/v1/agents/brief")
    def agents_brief(_: Actor = Depends(require("viewer"))) -> dict:
        row = ctx.agent_log.latest("inventory", "brief")
        if row is None:
            return {**ctx.agents.brief(use_model=False), "stored": False}
        return {"task_id": row["id"], "status": row["status"], "started_at": row["started_at"], "model": row["model"], "note": row["note"], "stored": True, **(row["result"] or {})}

    @router.post("/api/v1/agents/brief")
    def agents_brief_run(actor: Actor = Depends(require("approver"))) -> dict:
        return ctx.agents.brief_async(actor.id)

    @router.post("/api/v1/agents/ask")
    def agents_ask(body: AgentAskBody, actor: Actor = Depends(require("viewer"))) -> dict:
        if not ctx.agents.enabled:
            raise HTTPException(409, "The AI agents are switched off in Settings.")
        try:
            ctx.chat.throttle(actor.id)
            plan = ctx.agents.ask_plan(body.question, actor)
        except ChatLimit as exc:
            raise HTTPException(429, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"task_id": ctx.agents.submit(plan), "status": "running"}

    @router.get("/api/v1/agents/reviews/{approval_id}")
    def agents_review(approval_id: str, _: Actor = Depends(require("approver"))) -> dict:
        rows = ctx.agent_log.list(approval_id=approval_id, task="review", limit=1)
        return {"review": rows[0] if rows else None}

    @router.post("/api/v1/agents/reviews/{approval_id}")
    def agents_review_run(approval_id: str, force: bool = False, actor: Actor = Depends(require("approver"))) -> dict:
        try:
            return ctx.agents.review_async(approval_id, actor.id, force)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
