"""Wire everything together from a config dict. The only module that knows all the parts."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .approvals.channels.base import Channel
from .approvals.channels.registry import build_channels
from .approvals.models import Recipient
from .approvals.router import ApprovalRouter
from .api.hardening import MIN_KEY_LENGTH, weak_key
from .auth.erpnext import ErpNextLogin
from .auth.sessions import LoginThrottle, SessionStore
from .approvals.service import ApprovalService
from .config import load_config
from .core.common import Actor, Clock, utcnow
from .core.settings_store import SettingsStore
from .core.storage import Database
from .core.workorders import WorkOrderQueue
from .erp.base import ERPClient
from .erp.erpnext import ERPNextClient
from .erp.mock import MockERP
from .inventory.agent import InventoryAgent
from .inventory.alerts import AlertRepo
from .assistant.chat import ChatService
from .assistant.conversation import Conversation
from .assistant.service import AssistantService
from .agents.service import AgentService
from .agents.store import AgentLog
from .toolkit import Toolkit
from .procurement.agent import ProcurementAgent
from .procurement.proposals import ProposalRepo

log = logging.getLogger(__name__)


@dataclass
class App:
    config: dict[str, Any]
    db: Database
    settings: SettingsStore
    erp: ERPClient
    channels: dict[str, Channel]
    router: ApprovalRouter
    approvals: ApprovalService
    alerts: AlertRepo
    workorders: WorkOrderQueue
    inventory: InventoryAgent
    tokens: dict[str, Actor]
    clock: Clock
    proposals: ProposalRepo
    procurement: ProcurementAgent
    assistant: AssistantService
    chat: Optional[ChatService] = None
    login: Optional[ErpNextLogin] = None            # sign-in through ERPNext (None for the demo ERP)
    sessions: Optional[SessionStore] = None
    throttle: Optional[LoginThrottle] = None
    conversation: Optional[Any] = None              # typing to the assistant from Telegram / WhatsApp
    toolkit: Optional[Any] = None                   # the tools (toolkit.py): what an MCP client or an agent may ask of the application
    agent_log: Optional[Any] = None                 # the record of what the agents did
    agents: Optional[Any] = None                    # the AI agents (agents/service.py); None until the optional 'agents' dependencies are installed


def build_erp(cfg: dict[str, Any], clock: Clock) -> ERPClient:
    kind = cfg.get("type", "mock")
    if kind == "mock":
        return MockERP(today=clock().date(), seed=int(cfg.get("seed", 7)))
    if kind == "erpnext":
        for key in ("url", "api_key", "api_secret"):
            if not cfg.get(key):
                raise ValueError(f"erp.{key} is required for erp.type=erpnext (set it in settings.yaml or the environment)")
        return ERPNextClient(
            cfg["url"], cfg["api_key"], cfg["api_secret"],
            warehouses=cfg.get("warehouses"), consumption_voucher_types=cfg.get("consumption_voucher_types"),
            item_groups=cfg.get("item_groups"), company=cfg.get("company"),
        )
    raise ValueError(f"Unknown erp.type {kind!r} (use 'mock' or 'erpnext')")


def build_app(
    config: Optional[dict[str, Any]] = None,
    config_path: Optional[str] = None,
    clock: Clock = utcnow,
    erp: Optional[ERPClient] = None,
    channels: Optional[dict[str, Channel]] = None,
    http: Any = None,
    db_path: Optional[str] = None,
    login: Optional[ErpNextLogin] = None,
) -> App:
    cfg = config if config is not None else load_config(config_path)

    path = db_path or cfg["database"]["path"]
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = Database(path)

    settings = SettingsStore(db, base_policy=cfg.get("policy") or {}, base_system=cfg.get("system") or {}, clock=clock,
                             base_procurement=cfg.get("procurement") or {}, base_assistant=cfg.get("assistant") or {},
                             base_calendar=cfg.get("calendar") or {}, base_agents=cfg.get("agents") or {})
    erp = erp or build_erp(cfg["erp"], clock)
    channels = channels if channels is not None else build_channels(cfg.get("channels") or {}, http=http)

    recipients = [Recipient.model_validate(r) for r in (cfg.get("approvals") or {}).get("recipients") or []]
    for r in recipients:
        for rc in r.channels:
            if rc.type not in channels:
                log.warning("recipient %s lists channel %r which is not enabled - it will be skipped", r.id, rc.type)
            else:
                problem = channels[rc.type].address_problem(rc.address)
                if problem:
                    log.warning("recipient %s on %s: the address %s - nothing will be sent to them there", r.id, rc.type, problem)
    router = ApprovalRouter(channels, recipients, (cfg.get("approvals") or {}).get("strategy", "all"))
    approvals = ApprovalService(db, router, system_settings=settings.system, clock=clock)
    alerts = AlertRepo(db, clock)
    workorders = WorkOrderQueue(db, clock)
    inventory = InventoryAgent(erp, settings, alerts, approvals, workorders, db, clock)
    proposals = ProposalRepo(db, clock)
    procurement = ProcurementAgent(erp, settings, proposals, approvals, workorders, db, clock)
    inventory.order_settled = procurement.order_settled
    procurement.on_order_removed = inventory.end_quiet_period
    assistant = AssistantService(db, settings, approvals, alerts, proposals, clock)

    tokens: dict[str, Actor] = {}
    for t in (cfg.get("auth") or {}).get("tokens") or []:
        if not t.get("token"):
            log.warning("auth token for %s is empty (is the environment variable set?) - skipped", t.get("user"))
            continue
        tokens[t["token"]] = Actor(id=t["user"], roles=frozenset(t.get("roles", ["viewer"])), via="api")
        if weak_key(t["token"], int((cfg.get("auth") or {}).get("min_key_length", MIN_KEY_LENGTH))):
            log.warning("access key for %s is short or well known: it is accepted only from this machine, never through a tunnel or proxy. "
                        "Set a long random one (at least %d characters) in .env to use it remotely.", t.get("user"), MIN_KEY_LENGTH)

    auth_cfg = cfg.get("auth") or {}
    if login is None and isinstance(erp, ERPNextClient) and auth_cfg.get("erpnext_login", True):
        login = ErpNextLogin(erp.base_url)
    sessions = SessionStore(login, ttl_seconds=float(auth_cfg.get("session_hours", 12)) * 3600,
                            recheck_seconds=float(auth_cfg.get("recheck_minutes", 15)) * 60) if login else None
    chat = ChatService(erp, inventory, procurement, proposals, approvals, settings, db, clock)
    app = App(cfg, db, settings, erp, channels, router, approvals, alerts, workorders, inventory, tokens, clock, proposals, procurement, assistant,
              chat=chat, login=login, sessions=sessions, throttle=LoginThrottle() if login else None,
              conversation=Conversation(approvals, assistant, chat, router, synchronous=bool((cfg.get("conversation") or {}).get("synchronous"))))
    app.agent_log = AgentLog(db, clock)
    app.toolkit = Toolkit(app, log_call=app.agent_log.log_tool)
    app.agents = AgentService(app)                 # the optional 'mcp' package is only needed when an agent actually runs
    approvals.on_submitted.append(app.agents.on_submitted)
    chat.agent_fallback = app.agents.answer_for_chat
    chat.agent_background = app.agents.chat_background
    return app
