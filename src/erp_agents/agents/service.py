"""AgentService: the one object the rest of the application talks to for the AI agents.

It turns a request ("write the brief", "review this order request", "answer this question") into an agent job, runs it through the MCP tool server with the configured
model, and returns either the verified result (source "model") or the plain-rules result (source "rules"). Every task is recorded (agents/store.py). One task runs at a time:
a laptop has one model, and two agents talking over each other only make both slow.

With the agents switched off nothing is sent to any model; the brief and the order-request review still work from rules alone.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Optional

from pydantic import ValidationError

from ..approvals.models import ApprovalRecord
from ..assistant.chat import MAX_CHAT_QUESTION
from ..assistant.verify import NOT_ENOUGH
from ..core.common import Actor
from ..core.policy import AGENT_PROVIDERS, PROVIDER_LABELS, PROVIDER_MODELS, PROVIDER_URLS, AgentSettings
from ..procurement.agent import KIND_PO, KIND_PR
from . import inventory_agent as inventory
from . import purchase_agent as purchasing
from .llm import KEY_ENV, LLMError, ToolChatClient, describe, make_client
from .runtime import AgentJob, AgentRuntime, Evidence, Outcome, ToolRun
from .usage import month_start, summarize, token_summary

log = logging.getLogger(__name__)

INSTALL_HINT = 'The AI agents need an extra package: pip install "erp-agents[agents]".'
WORKERS = {name: Actor(f"agent:{name}", frozenset({"viewer"}), "agent") for name in ("inventory", "purchasing")}      # scheduled work can look, never prepare an order
BUSY = "The assistant is busy with another task. Please try again in a minute."


@dataclass
class Plan:
    job: AgentJob
    actor: Actor
    finish: Callable[[Outcome], dict[str, Any]]            # the result from a verified model answer
    fallback: Callable[[Outcome], dict[str, Any]]          # the result from rules alone (the model was unreachable or its answer was rejected)
    request_text: str = ""
    approval_id: Optional[str] = None
    asked_by: str = ""


class AgentService:
    def __init__(self, app: Any, client_factory: Optional[Callable[[Any], ToolChatClient]] = None) -> None:
        self.app = app
        self.client_factory = client_factory or (lambda cfg: make_client(cfg, self._keys()))
        self._lock = threading.Lock()
        try:
            app.agent_log.fail_stale()
        except Exception:  # noqa: BLE001
            log.exception("could not tidy the record of agent tasks")

    # ---------------------------------------------------------------- state
    @property
    def settings(self) -> Any:
        return self.app.settings.agents()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled)

    @property
    def log(self) -> Any:
        return self.app.agent_log

    def today(self) -> str:
        return self.app.inventory.today().isoformat()

    def _keys(self) -> dict[str, str]:
        """The hosted-model keys config.py found in the environment. They are never settings, and never leave this object except as a request header."""
        return self.app.config.get("llm_keys") or {}

    def describe(self) -> dict[str, Any]:
        return describe(self.settings, self._keys())

    def status(self, check_model: bool = False) -> dict[str, Any]:
        cfg = self.settings
        try:
            import mcp  # noqa: F401
            installed = True
        except ImportError:
            installed = False
        info = self.describe()
        out: dict[str, Any] = {"enabled": cfg.enabled, "model": info["model"], "model_server": info["base_url"], "provider": info["provider"], "provider_label": info["provider_label"],
                               "hosted": info["hosted"], "key_configured": info["key_configured"], "key_variable": info["key_variable"], "leaves_this_server": info["remote"],
                               "package_installed": installed, "busy": self._lock.locked(), "tools": cfg.tools_url or "built-in", "install_hint": None if installed else INSTALL_HINT}
        if check_model and cfg.enabled:
            try:
                client = self.client_factory(cfg)
                out["model_check"] = client.status() if hasattr(client, "status") else {"reachable": True, "model_installed": True}
            except LLMError as exc:                         # for a hosted provider: no key yet
                out["model_check"] = {"reachable": False, "model_installed": False, "error": str(exc)}
        keys = self._keys()
        sources = self.app.config.get("llm_key_sources") or {}
        out["providers"] = [{"id": p, "label": PROVIDER_LABELS[p], "hosted": p != "ollama", "base_url": PROVIDER_URLS[p], "model": PROVIDER_MODELS[p], "key_variable": KEY_ENV.get(p),
                             "key_configured": bool(keys.get(p)) if p != "ollama" else None, "key_source": sources.get(p), "own_variable": f"ERP_AGENTS_{p.upper()}_API_KEY" if p != "ollama" else None}
                            for p in AGENT_PROVIDERS]
        out["key_source"] = sources.get(cfg.provider)
        out["last"] = {k: self._brief_row(self.log.latest(*k.split(":"))) for k in ("inventory:brief", "purchasing:review")}
        return out

    def check(self, provider: str, base_url: str = "", model: str = "") -> dict[str, Any]:
        """Does this choice work? Asks the provider which models the key can use (no tokens are spent, and nothing from ERPNext is sent). Works on a choice that is not saved yet."""
        try:
            probe = AgentSettings(provider=provider, base_url=base_url, model=model, allow_remote=True)      # allow_remote: only the key is sent, to learn which models it can use
        except ValidationError as exc:
            raise ValueError(exc.errors()[0]["msg"].removeprefix("Value error, "))
        try:
            client = make_client(probe, self._keys())
        except LLMError as exc:
            return {"provider": provider, "model": probe.effective_model(), "reachable": False, "model_installed": False, "error": str(exc)}
        return {"provider": provider, "model": probe.effective_model(), **(client.status() if hasattr(client, "status") else {"reachable": True, "model_installed": True})}

    @staticmethod
    def _brief_row(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        return {k: row[k] for k in ("id", "status", "started_at", "model", "llm_seconds", "note")} if row else None

    # ---------------------------------------------------------------- running a plan
    def run(self, plan: Plan, wait: float = 1800) -> dict[str, Any]:
        return self._complete(self._start(plan), plan, wait)

    def submit(self, plan: Plan) -> str:
        """Start the plan in the background; the caller polls `task(id)`."""
        task_id = self._start(plan)
        threading.Thread(target=self._complete, args=(task_id, plan, 1800), name=f"agent-{plan.job.agent}-{plan.job.task}", daemon=True).start()
        return task_id

    def task(self, task_id: str) -> Optional[dict[str, Any]]:
        return self.log.get(task_id)

    def _start(self, plan: Plan) -> str:
        cfg, info = self.settings, self.describe()
        model, remote = info["model"], info["remote"]
        try:                                                # a client says what it really is (a test double, or a custom address); a missing key does not stop the task being recorded
            client = self.client_factory(cfg)
            model, remote = client.model, bool(client.remote)
        except LLMError:
            pass
        return self.log.start_task(plan.job.agent, plan.job.task, model, remote, plan.request_text, plan.approval_id, plan.asked_by or plan.actor.id, info["provider"])

    def _over_limit(self, cfg: Any) -> bool:
        return bool(cfg.monthly_token_limit and self.log.tokens_since(month_start(self.app.clock()), hosted_only=True) >= cfg.monthly_token_limit)

    def _complete(self, task_id: str, plan: Plan, wait: float) -> dict[str, Any]:
        cfg = self.settings
        outcome = Outcome()
        client: Optional[ToolChatClient] = None
        try:
            client = self.client_factory(cfg)
        except LLMError as exc:                             # a hosted provider without its key: the plain rules answer, and the note says what to add
            outcome.note = str(exc)
        hosted = bool(client is not None and getattr(client, "hosted", client.remote))
        if client is not None:
            if not self._lock.acquire(timeout=wait):
                outcome.note = "busy"
            else:
                try:
                    if client.remote and not cfg.allow_remote:
                        outcome.note = "the model server is not on this machine and 'allow_remote' is off"
                    elif hosted and self._over_limit(cfg):
                        outcome.note = f"the monthly limit of {cfg.monthly_token_limit:,} tokens for hosted models has been reached, so the plain rules answered (Settings > AI agents)"
                    else:
                        outcome = self._execute(plan, client, task_id)
                finally:
                    self._lock.release()
        result: Optional[dict[str, Any]] = None
        status, source = "ok", "model"
        if outcome.status == "ok" and outcome.data is not None:
            try:
                result = plan.finish(outcome)
            except Exception:  # noqa: BLE001 - an answer that passed the checks but cannot be put to use is treated like one that failed them
                log.exception("could not use the model's answer")
                outcome.note = "the model's answer could not be used"
        if result is None:
            try:
                result = plan.fallback(outcome)
            except Exception as exc:  # noqa: BLE001
                log.exception("the plain-rules fallback failed")
                result = {"error": f"{type(exc).__name__}"}
            status, source = ("fallback" if outcome.note != "busy" else "failed"), "rules"
        note = outcome.note or "; ".join(outcome.problems)
        stored = {**result, "source": source}
        if outcome.prompt_tokens or outcome.output_tokens:                       # kept with the result, so whoever polls the task can show what it cost
            stored["tokens"] = token_summary(outcome.prompt_tokens, outcome.output_tokens, cfg, hosted)
        self.log.finish_task(task_id, status, stored, note, outcome.steps, outcome.tool_calls, outcome.llm_seconds, outcome.prompt_tokens, outcome.output_tokens, outcome.trace)
        out = {"task_id": task_id, "status": status, "source": source, "model": (client.model if client is not None else self.describe()["model"]) if source == "model" else None, "note": note,
               "seconds": round(outcome.llm_seconds, 1), "provider": self.describe()["provider"], "hosted": hosted, **result}
        if outcome.prompt_tokens or outcome.output_tokens:
            out["tokens"] = token_summary(outcome.prompt_tokens, outcome.output_tokens, cfg, hosted)
        return out

    def _execute(self, plan: Plan, client: ToolChatClient, task_id: str) -> Outcome:
        try:
            from ..mcp_server import build_mcp_server
            from .bridge import ToolBridge
        except ImportError:
            return Outcome(status="failed", note=INSTALL_HINT)
        cfg = self.settings
        runtime = AgentRuntime(client, cfg.max_steps, cfg.max_tool_calls)

        async def go() -> Outcome:
            if cfg.tools_url:
                bridge = ToolBridge(url=cfg.tools_url, token=(self.app.config.get("mcp") or {}).get("client_token", ""))
            else:
                bridge = ToolBridge(server=build_mcp_server(self.app.toolkit, default_actor=plan.actor, via="agent", task_id=lambda: task_id))
            async with bridge:
                return await runtime.run(plan.job, bridge)
        try:
            return asyncio.run(go())
        except Exception as exc:  # noqa: BLE001 - whatever goes wrong, the person gets the plain-rules answer and the record says why
            log.exception("agent task failed")
            return Outcome(status="failed", note=f"the agent could not run ({type(exc).__name__}: {str(exc)[:160]})")

    def usage(self, days: int = 30) -> dict[str, Any]:
        """Tokens and tasks this calendar month (against the monthly limit) and over the last `days` days, by provider and model."""
        now = self.app.clock()
        since_month = month_start(now)
        return summarize(self.log.usage(since_month), self.log.usage(now - timedelta(days=days)), self.settings, since_month, days)

    def _evidence_without_model(self, job: AgentJob, actor: Actor) -> Evidence:
        """What the warm start would have fetched, fetched directly (no model, no MCP): the base of every plain-rules result."""
        ev = Evidence(job.question)
        for name, args in job.warm:
            res = self.app.toolkit.call(name, args, actor, via="agent")
            ev.add(ToolRun(name, args, res, "error" not in res))
        return ev

    # ---------------------------------------------------------------- the inventory brief
    def brief_plan(self, asked_by: str = "system") -> Plan:
        job = inventory.brief_job(self.today())
        actor = WORKERS["inventory"]

        def table(ev: Evidence) -> dict[str, Any]:
            """The rules' brief: the table is always theirs, and so is the headline unless the model's passes the checks."""
            found = ev.results("stock_overview")
            return inventory.rules_brief(found[0] if found else {"items": [], "counts": {}, "items_total": 0})

        def points(ev: Evidence) -> list[dict[str, str]]:
            return inventory.attention_points(ev, self.app.inventory.today())

        def shape(brief: dict[str, Any]) -> dict[str, Any]:
            return {**brief, "text": inventory.brief_text(brief)}

        def finish(o: Outcome) -> dict[str, Any]:
            ev = o.evidence or Evidence()
            data = o.data or {}
            brief = table(ev)
            return shape({**brief, "headline": data["headline"], "watch_outs": inventory.merge_watch_outs(points(ev), data["watch_outs"], tuple(p["item_code"] for p in brief["priorities"]))})

        def fallback(o: Outcome) -> dict[str, Any]:
            ev = o.evidence if (o.evidence and o.evidence.results("stock_overview")) else self._evidence_without_model(job, actor)
            return shape({**table(ev), "watch_outs": points(ev)})
        return Plan(job, actor, finish, fallback, request_text="daily stock brief", asked_by=asked_by)

    def _running(self, agent: str, task: str, approval_id: Optional[str] = None) -> Optional[str]:
        for row in self.log.list(agent=agent, task=task, approval_id=approval_id, limit=3):
            if row["status"] == "running":
                return row["id"]
        return None

    def brief_async(self, asked_by: str = "system") -> dict[str, Any]:
        """For a web request: the rules brief at once when the agents are off, otherwise start the agent in the background (or point to the one already running)."""
        if not self.enabled:
            return self.brief(asked_by)
        running = self._running("inventory", "brief")
        return {"task_id": running or self.submit(self.brief_plan(asked_by)), "status": "running"}

    def brief(self, asked_by: str = "system", use_model: Optional[bool] = None) -> dict[str, Any]:
        """The stock brief. With the agents off (or use_model=False) it is written by rules alone and not recorded."""
        plan = self.brief_plan(asked_by)
        if use_model is False or (use_model is None and not self.enabled):
            result = plan.fallback(Outcome())
            return {"task_id": None, "status": "rules", "source": "rules", "model": None, "note": "" if use_model is False else "The AI agents are switched off; this brief is written by rules alone.", **result}
        return self.run(plan)

    # ---------------------------------------------------------------- a second opinion on an order request
    def review_plan(self, approval_id: str, asked_by: str = "system") -> Plan:
        rec = self.app.approvals.get(approval_id)
        if rec is None or rec.kind not in (KIND_PO, KIND_PR):
            raise ValueError("A second opinion is available for purchase order and requisition requests only.")
        facts = rec.request.facts or {}
        if not facts.get("item_code") or not facts.get("qty"):
            raise ValueError("This request does not name an item and a quantity.")
        job = purchasing.review_job(facts, self.today())
        actor = WORKERS["purchasing"]

        def finish(o: Outcome) -> dict[str, Any]:
            return {**purchasing.review_result(o.data, facts, o.evidence or Evidence()), "request_key": self._key(facts)}

        def fallback(o: Outcome) -> dict[str, Any]:
            ev = o.evidence if (o.evidence and o.evidence.results("vendor_options")) else self._evidence_without_model(job, actor)
            return {**purchasing.rules_review(facts, ev), "request_key": self._key(facts)}
        return Plan(job, actor, finish, fallback, request_text=job.question, approval_id=approval_id, asked_by=asked_by)

    @staticmethod
    def _key(facts: dict[str, Any]) -> str:
        return f"{facts.get('item_code')}|{facts.get('qty')}|{facts.get('supplier')}"

    def _cached_review(self, approval_id: str) -> Optional[dict[str, Any]]:
        """The review already made of this request, if the request has not changed since."""
        rec = self.app.approvals.get(approval_id)
        for row in self.log.list(approval_id=approval_id, task="review", limit=3):
            if rec and row["status"] in ("ok", "fallback") and (row["result"] or {}).get("request_key") == self._key(rec.request.facts):
                return {"task_id": row["id"], "status": row["status"], "source": row["result"].get("source"), "cached": True, **row["result"]}
        return None

    def review(self, approval_id: str, asked_by: str = "system", force: bool = False, use_model: Optional[bool] = None) -> dict[str, Any]:
        plan = self.review_plan(approval_id, asked_by)
        cached = None if force else self._cached_review(approval_id)
        if cached:
            return cached
        if use_model is False or (use_model is None and not self.enabled):
            return {"task_id": None, "status": "rules", "source": "rules", "cached": False, **plan.fallback(Outcome())}
        return self.run(plan)

    def review_async(self, approval_id: str, asked_by: str = "system", force: bool = False) -> dict[str, Any]:
        """For a web request: the review at once if it exists or the agents are off, otherwise start it in the background."""
        plan = self.review_plan(approval_id, asked_by)               # raises ValueError for a request that cannot be reviewed
        cached = None if force else self._cached_review(approval_id)
        if cached:
            return cached
        if not self.enabled:
            return {"task_id": None, "status": "rules", "source": "rules", "cached": False, **plan.fallback(Outcome())}
        return {"task_id": self._running("purchasing", "review", approval_id) or self.submit(plan), "status": "running"}

    def on_submitted(self, record: ApprovalRecord) -> None:
        """Called for every new request that was sent out. With 'second_opinion' on, the purchasing agent reads it in the background."""
        cfg = self.settings
        if cfg.enabled and cfg.second_opinion and record.kind in (KIND_PO, KIND_PR):
            threading.Thread(target=self.review_and_tell, args=(record.id,), name="agent-second-opinion", daemon=True).start()

    def review_and_tell(self, approval_id: str) -> Optional[dict[str, Any]]:
        """Review the request and send the second opinion to the people who got it - only when it has something to say."""
        try:
            res = self.review(approval_id, asked_by="second_opinion")
        except Exception:  # noqa: BLE001
            log.exception("the second opinion on %s failed", approval_id)
            return None
        rec = self.app.approvals.get(approval_id)
        if res.get("cached") or rec is None or rec.status != "pending":
            return res
        if res.get("source") == "model" or res.get("verdict") == "check_first":
            self.app.approvals.tell_recipients(approval_id, purchasing.review_text(res))
        return res

    def tell_approvers(self, text: str) -> int:
        """A plain message to everyone who approves orders, on every channel they have (WhatsApp delivers plain messages only while its 24-hour window is open)."""
        reached = 0
        for recipient, channels in self.app.router.targets():
            for rc in channels:
                try:
                    res = self.app.router.channels[rc.type].send_text(rc.address, text)
                    reached += int(res.ok)
                    if not res.ok:
                        log.warning("could not send the brief to %s on %s: %s", recipient.id, rc.type, res.error)
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not send the brief to %s on %s: %s", recipient.id, rc.type, exc)
        return reached

    # ---------------------------------------------------------------- a question
    def ask_plan(self, question: str, actor: Actor) -> Plan:
        question = " ".join((question or "").split())
        if not question:
            raise ValueError("Please type a question.")
        if len(question) > MAX_CHAT_QUESTION:
            raise ValueError(f"Please keep the question under {MAX_CHAT_QUESTION} characters.")
        job = purchasing.ask_job(question, actor.id, self.today(), can_prepare=actor.has_role("approver"))

        def prepared(o: Outcome) -> Optional[dict[str, Any]]:
            for r in (o.evidence.results("prepare_order_request") if o.evidence else []):
                if r.get("request_id"):
                    return r
            return None

        def note_for(p: Optional[dict[str, Any]]) -> str:
            if not p:
                return ""
            return (f" I prepared the request ({p.get('request_title') or p.get('item_code')}) and sent it for approval. {p.get('note', '')}").rstrip()

        def finish(o: Outcome) -> dict[str, Any]:
            data = o.data or {}
            p = prepared(o)
            if not data.get("answerable"):
                return {"status": "not_enough_information", "answer": (NOT_ENOUGH + note_for(p)).strip(), "agent": job.agent, "prepared": p}
            return {"status": "answered", "answer": " ".join(data["answer"].split()) + note_for(p), "agent": job.agent, "prepared": p}

        def fallback(o: Outcome) -> dict[str, Any]:
            p = prepared(o)
            if o.note == "busy":
                return {"status": "busy", "answer": BUSY, "agent": job.agent, "prepared": p}
            if o.status == "rejected":
                return {"status": "not_enough_information", "answer": (NOT_ENOUGH + note_for(p)).strip(), "agent": job.agent, "prepared": p}
            return {"status": "unavailable", "answer": ("The AI assistant is not available just now." + note_for(p)).strip(), "agent": job.agent, "prepared": p}
        return Plan(job, actor, finish, fallback, request_text=question, asked_by=actor.id)

    def ask(self, question: str, actor: Actor) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "disabled", "answer": "The AI agents are switched off in Settings.", "task_id": None, "source": "rules"}
        return self.run(self.ask_plan(question, actor), wait=3)

    def chat_background(self, question: str, user: str, can_decide: bool) -> Optional[dict[str, Any]]:
        """For the web chat: start the agent in the background and hand back the task to poll (a reply can take minutes, longer than a tunnel waits). None = cannot help."""
        if not self.enabled:
            return None
        try:
            plan = self.ask_plan(question, Actor(user, frozenset({"approver"} if can_decide else {"viewer"}), "chat"))
        except ValueError:
            return None
        return {"task_id": self.submit(plan), "answer": "I am asking the AI assistant. On this computer that can take a few minutes; the answer will appear here."}

    def answer_for_chat(self, question: str, user: str, can_decide: bool) -> Optional[dict[str, Any]]:
        """Used by the chat when its rules did not understand a question. None = the agents cannot help (off, busy or not available): the chat shows its usual help."""
        if not self.enabled:
            return None
        res = self.ask(question, Actor(user, frozenset({"approver"} if can_decide else {"viewer"}), "chat"))
        return res if res.get("status") in ("answered", "not_enough_information") else None
