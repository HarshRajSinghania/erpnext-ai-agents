"""The stock and procurement pack: its entry object. `PACK` is what the core loads (core/packs.py) when the pack is switched on.

`setup(app)` builds this pack's services on top of the core (the inventory and procurement agents, the chat and the "Ask why" assistant, the conversation on Telegram and
WhatsApp), registers what it adds to the core's registries (settings, database tables, tools, approval kinds, agent tasks), and hands them to the application as services, so
`app.inventory`, `app.procurement`, `app.alerts`, `app.proposals`, `app.assistant` and `app.chat` keep working. It imports the core and itself, never another pack.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from erp_agents.core.packs import Pack, load_manifest

from .agents.tasks import StockAgentTasks
from .assistant.chat import ChatService
from .assistant.conversation import Conversation
from .assistant.service import AssistantService
from .inventory.agent import InventoryAgent
from .inventory.alerts import AlertRepo
from .procurement.agent import ProcurementAgent
from .procurement.proposals import ProposalRepo
from .schema import ensure_schema
from .settings import attach as attach_settings
from .tools import StockTools

HERE = Path(__file__).resolve().parent
MANIFEST = load_manifest(HERE / "manifest.yaml")
NAME = MANIFEST.name
# kinds of approval request that are information only: nobody is reminded about them
NO_REMINDER_KINDS = ("inventory.alert", "procurement.order")
# kinds whose WhatsApp message carries its own buttons: sent as plain messages, never with the default template (its buttons say Create order / Remind me / Reject)
WHATSAPP_PLAIN_KINDS = ("procurement.order",)


class StockProcurementPack(Pack):
    manifest = MANIFEST

    def setup(self, app: Any) -> None:
        cfg = app.config
        attach_settings(app.settings, cfg.get("policy"), cfg.get("procurement"), cfg.get("calendar"))
        ensure_schema(app.db)
        alerts = AlertRepo(app.db, app.clock)
        inventory = InventoryAgent(app.erp, app.settings, alerts, app.approvals, app.workorders, app.db, app.clock)
        proposals = ProposalRepo(app.db, app.clock)
        procurement = ProcurementAgent(app.erp, app.settings, proposals, app.approvals, app.workorders, app.db, app.clock)
        inventory.order_settled = procurement.order_settled                 # a quiet period ends when its order is done
        procurement.on_order_removed = inventory.end_quiet_period           # and when the order is cancelled
        assistant = AssistantService(app.db, app.settings, app.approvals, alerts, proposals, app.clock)
        chat = ChatService(app.erp, inventory, procurement, proposals, app.approvals, app.settings, app.db, app.clock)
        app.services.update(alerts=alerts, inventory=inventory, proposals=proposals, procurement=procurement, assistant=assistant, chat=chat)
        app.conversation = Conversation(app.approvals, assistant, chat, app.router, synchronous=bool((cfg.get("conversation") or {}).get("synchronous")))

        app.approvals.no_reminder_kinds.update(NO_REMINDER_KINDS)
        for channel in app.channels.values():
            plain = getattr(channel, "plain_only", None)
            if plain is not None:
                for kind in WHATSAPP_PLAIN_KINDS:
                    plain(kind)

        for definition in StockTools(app).definitions():
            app.toolkit.register(NAME, definition.spec, definition.handler)

        tasks = StockAgentTasks(app.agents)
        app.agents.add_tasks(tasks, last=("inventory:brief", "purchasing:review"))
        app.approvals.on_submitted.append(app.agents.on_submitted)          # a second opinion on each new order request, when switched on
        chat.agent_fallback = app.agents.answer_for_chat
        chat.agent_background = app.agents.chat_background

    def routes(self, router: Any, ctx: Any, deps: Any) -> None:
        from .api import routes
        routes(router, ctx, deps)

    def jobs(self, scheduler: Any) -> None:
        from .jobs import StockJobs
        scheduler.add_jobs(StockJobs(scheduler))

    def web_dir(self) -> Path:
        return HERE / "web"

    def cli_commands(self) -> list[Any]:
        from .cli import COMMANDS
        return COMMANDS

    def agent_actions(self) -> dict[str, Any]:
        from .cli import AGENT_ACTIONS
        return AGENT_ACTIONS

    def mcp_tools(self, server: Any, run: Any) -> None:
        from .mcp_tools import register
        register(server, run)

    def mcp_instructions(self) -> str:
        from .mcp_tools import INSTRUCTIONS
        return INSTRUCTIONS


PACK = StockProcurementPack()
