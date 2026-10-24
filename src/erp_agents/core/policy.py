"""Admin-configurable policy for the inventory agent.

Resolution order (later wins):  code defaults -> settings.yaml `policy:` ->
admin override on *global* -> on *item group* -> on *item*.
Every field has a description so an admin UI can be generated from the schema.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..erp.base import DEFAULT_IN_TRANSIT, DEFAULT_STOCK_ADD, DEFAULT_STOCK_SUBTRACT, IN_TRANSIT_COLUMN, IN_TRANSIT_LABEL, STOCK_COLUMNS


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(True, description="Monitor this item/group at all.")

    # ---- thresholds (the knobs the owner/admin cares about most) -------------
    min_qty: Optional[float] = Field(
        None, ge=0, description="Alert when stock position (on hand + on order) is at or below this quantity. Empty = rule off."
    )
    min_days_cover: Optional[float] = Field(
        7, gt=0, description="Alert when stock would last this many days or fewer at the planned usage rate. Empty = rule off."
    )
    use_lead_time_rule: bool = Field(
        True, description="Also alert when days of cover <= supplier lead time + safety days (i.e. it is time to order)."
    )
    safety_stock_days: float = Field(3, ge=0, description="Extra days of buffer added to lead time.")
    lead_time_days: Optional[float] = Field(
        None, gt=0, description="Override replenishment lead time. Empty = use the ERP item's lead time, else default."
    )
    default_lead_time_days: float = Field(7, gt=0, description="Used when neither override nor ERP lead time exists.")
    # ---- what counts as stock: the owner picks the ERP columns (see erp.base.STOCK_COLUMNS) and the warehouses
    stock_subtract: list[str] = Field(
        default_factory=lambda: list(DEFAULT_STOCK_SUBTRACT),
        description="ERP stock columns subtracted from on-hand stock to get AVAILABLE stock (e.g. reserved for sales orders). Empty = ignore reservations.",
    )
    stock_add: list[str] = Field(
        default_factory=lambda: list(DEFAULT_STOCK_ADD),
        description="ERP stock columns added to available stock to get the stock POSITION (e.g. on order, planned production). Empty = on-hand only.",
    )
    in_transit_stock: Literal["arriving", "shelf", "ignore"] = Field(
        DEFAULT_IN_TRANSIT,
        description="What stock in transit warehouses (moving between your warehouses) counts as: arriving = added to the stock position only, so it is not sold from yet; "
                    "shelf = counted like stock on the shelf; ignore = not counted at all.",
    )
    warehouses: list[str] = Field(
        default_factory=list, description="Only count stock (and usage) in these warehouses. Empty = all warehouses (or the ERP connection's own filter).",
    )

    # ---- how usage is estimated ----------------------------------------------
    usage_method: Literal["base", "recent", "blend"] = Field(
        "blend", description="base = long-window average, recent = short-window average, blend = weighted mix."
    )
    recent_window_days: int = Field(14, ge=3, le=120, description="Short window for 'recent' usage.")
    base_window_days: int = Field(90, ge=14, le=400, description="Long window for 'base' usage.")
    recent_weight: float = Field(0.4, ge=0, le=1, description="Weight of recent usage when usage_method = blend.")
    history_days: int = Field(
        480, ge=30, le=1100,
        description="How much consumption history to load from the ERP. Seasonal comparison needs about 15+ months (455+ days).",
    )
    trend_notable_pct: float = Field(25, ge=1, description="Usage change (%) vs base that is called out as a trend.")

    # ---- predictive early warnings (history based) -----------------------------
    predictive_enabled: bool = Field(True, description="Warn early when history says a threshold will be crossed soon.")
    lookahead_days: int = Field(14, ge=1, le=120, description="How far ahead the agent looks for a predicted crossing.")
    min_history_days: int = Field(30, ge=7, description="Minimum days of data needed before making predictions.")
    seasonal_adjust: bool = Field(True, description="Use last year's same-period demand uplift (needs >1 year of history).")
    seasonal_max_factor: float = Field(2.0, ge=1, le=5, description="Cap on the seasonal uplift applied to forecasts.")

    # ---- order suggestion handed to the procurement step -------------------------
    target_cover_days: float = Field(30, gt=0, description="Suggested order brings stock to lead + safety + this many days.")
    order_multiple: float = Field(1, gt=0, description="Round suggested quantity up to a multiple of this (pack size).")
    min_order_qty: float = Field(0, ge=0, description="Never suggest less than this.")

    # ---- alert behaviour -------------------------------------------------------------
    snooze_days: int = Field(3, ge=1, le=60, description="'Remind me later' length.")
    dismiss_days: int = Field(7, ge=1, le=180, description="'Ignore' length before the same condition may alert again.")
    reorder_cooldown_days: int = Field(3, ge=0, le=60, description="After approval, wait this long for a PO to appear before re-alerting.")

    @field_validator("stock_subtract", "stock_add", mode="after")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @field_validator("warehouses", mode="after")
    @classmethod
    def _clean_warehouses(cls, value: list[str]) -> list[str]:
        return [w for w in dict.fromkeys(w.strip() for w in value) if w]

    @model_validator(mode="after")
    def _check(self) -> "Policy":
        for field_name in ("stock_subtract", "stock_add"):
            unknown = [c for c in getattr(self, field_name) if c not in STOCK_COLUMNS]
            if unknown:
                raise ValueError(f"{field_name}: unknown stock column(s) {', '.join(unknown)} (choose from {', '.join(STOCK_COLUMNS)})")
        both = sorted(set(self.stock_subtract) & set(self.stock_add))
        if both:
            raise ValueError(f"stock column(s) {', '.join(both)} cannot be both added and subtracted")
        if self.recent_window_days >= self.base_window_days:
            raise ValueError("recent_window_days must be smaller than base_window_days")
        if self.history_days < self.base_window_days:
            raise ValueError("history_days must be at least base_window_days")
        return self


# Optional columns of the Stock Monitor table, in the order they appear. None is shown unless the owner adds it.
MONITOR_COLUMNS: dict[str, str] = {
    "available": "Available stock",
    **{c: label for c, (label, _attr, _phrase) in STOCK_COLUMNS.items()},
    IN_TRANSIT_COLUMN: IN_TRANSIT_LABEL,
    "position": "Stock position",
    "arrival_date": "Arrival date of open orders",
    "projected_qty": "ERPNext projected quantity",
}


class SystemSettings(BaseModel):
    """Global, non-item settings."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    monitor_columns: list[str] = Field(
        default_factory=list,
        description="Extra columns shown in the Stock Monitor table (e.g. ordered quantity, arrival date). Empty = only the essentials.",
    )

    @field_validator("monitor_columns", mode="after")
    @classmethod
    def _known_columns(cls, value: list[str]) -> list[str]:
        unknown = [c for c in value if c not in MONITOR_COLUMNS]
        if unknown:
            raise ValueError(f"unknown Stock Monitor column(s) {', '.join(unknown)} (choose from {', '.join(MONITOR_COLUMNS)})")
        return [c for c in MONITOR_COLUMNS if c in value]          # canonical order, no duplicates

    check_cron: str = Field("0 6 * * *", description="When the inventory agent runs (cron, server time zone).")
    timezone: str = Field("Asia/Kolkata", description="Time zone used for the schedule.")
    max_alerts_per_run: int = Field(20, ge=1, le=500, description="Most new approval requests sent per run (worst first).")
    approval_expiry_hours: int = Field(72, ge=1, description="A request with no answer expires after this long (never auto-approved).")
    reminder_after_hours: int = Field(24, ge=1, description="Send one reminder if there is no answer after this long.")
    max_reminders: int = Field(2, ge=0, le=10, description="Most reminders per request.")
    stock_alerts: bool = Field(
        True, description="Send an information-only stock alert (low stock, or needed urgently) BEFORE any request to reorder. An early warning then stays an alert and "
                          "does not ask you to reorder. Off = the old behaviour: the first message is the request itself.",
    )
    stock_alert_repeat_days: int = Field(7, ge=1, le=60, description="Repeat an alert for an item that is still low after this many days. A worse level is always sent at once.")
    max_notices_per_run: int = Field(20, ge=1, le=500, description="Most stock alerts sent per check (worst first), so one bad day does not flood your phone.")
    monitor_failures_before_alert: int = Field(3, ge=1, le=48, description="Tell the owner when the stock check has failed (for example ERPNext unreachable) this many runs in a row.")

    @model_validator(mode="after")
    def _check(self) -> "SystemSettings":
        parts = self.check_cron.split()
        if len(parts) != 5:
            raise ValueError("check_cron must have 5 fields, e.g. '0 6 * * *'")
        return self


# Placeholders the vendor note template may use (filled from the proposal; see procurement/messages.py).
VENDOR_NOTE_FIELDS = ("item", "item_code", "qty", "uom", "required_by", "reference", "vendor", "unit_price", "total")


class ProcurementSettings(BaseModel):
    """How the vendor selector ranks suppliers and what happens when the owner approves a purchase order."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    weight_cost: float = Field(40, ge=0, le=100, description="How much total cost matters when ranking vendors (relative weight).")
    weight_delivery: float = Field(30, ge=0, le=100, description="How much delivery time matters, judged against how soon you run out.")
    weight_reliability: float = Field(20, ge=0, le=100, description="How much on-time delivery history matters.")
    weight_terms: float = Field(10, ge=0, le=100, description="How much payment terms (credit days) matter.")
    prefer_listed_suppliers: bool = Field(True, description="Give a small bonus to suppliers named on the item record in the ERP.")
    flag_price_jump_pct: Optional[float] = Field(10, gt=0, description="Warn when a quote is this % above that supplier's average price. Empty = never warn.")
    exclude_new_vendors: bool = Field(False, description="Never recommend a supplier with no past orders for this item (they are still listed).")
    min_vendors: int = Field(2, ge=1, le=10, description="Compare at least this many vendors; the request says so when fewer qualify.")
    history_days: int = Field(365, ge=30, le=1825, description="How far back to look at past orders for prices, lead times and reliability.")
    use_quotations: Literal["off", "prefer", "only"] = Field(
        "off", description="Prices from supplier quotations (the ERP's Supplier Quotation): off = compare on the prices of past orders only; prefer = a vendor's valid quotation "
                           "replaces its last order price, and a vendor that has quoted but was never ordered from can be compared; only = compare only the vendors that have a valid quotation.",
    )
    quotation_max_age_days: int = Field(
        30, ge=1, le=365, description="A quotation with no end date counts for this many days after its date. A quotation with an end date counts until then.",
    )
    fallback_qty: Optional[float] = Field(
        None, gt=0, description="Order this quantity when the inventory agent could not size an order (no usage history). Empty = ask the owner to decide.",
    )
    po_mode: Literal["off", "draft"] = Field(
        "draft", description="draft = on approval, create a DRAFT purchase order in the ERP for you to review and submit; off = only record the decision.",
    )
    snooze_days: int = Field(2, ge=1, le=30, description="'Remind me later' length for a purchase order request.")
    requisition_first_groups: list[str] = Field(
        default_factory=list, max_length=100,
        description="Item groups that get a purchase REQUISITION first (a Material Request your buyer turns into an order), not a purchase order. Empty = none.",
    )
    requisition_first_items: list[str] = Field(
        default_factory=list, max_length=500,
        description="Individual items (item codes) that get a purchase requisition first, whatever their group. Empty = none.",
    )
    require_personal_login: bool = Field(
        False, description="Only create orders and requisitions under the approver's own ERPNext login. An approval that does not come from a signed-in console session "
                           "(for example from WhatsApp) then waits until it is approved in the console. Off = those are created by the service account, with the approver's name in a field.",
    )
    include_taxes: bool = Field(
        True, description="Compare vendors on the total cost: goods plus the duties and taxes your ERP applies to a purchase from that vendor "
                          "(read from the ERP's Tax Rules and purchase taxes templates). Off = compare on price only.",
    )
    include_recoverable_taxes_in_cost: bool = Field(
        False, description="Count taxes the ERP books as payable-only, not as item cost (e.g. input tax credit) as cost. Off = they are shown but do not make a vendor look dearer.",
    )
    no_tax_setup: Literal["flag", "exclude"] = Field(
        "flag", description="When the ERP has no purchase tax setup that applies to a vendor, so duties cannot be checked: flag = still compare but warn; exclude = never recommend them.",
    )
    include_vendor_note: bool = Field(True, description="Put a note for the vendor on the purchase order (shown to you before you approve).")
    vendor_note_template: str = Field(
        "Please confirm delivery by {required_by} for {qty} {uom} of {item}. Our reference: {reference}.",
        max_length=500,
        description="The vendor note. You can use: " + ", ".join("{" + k + "}" for k in VENDOR_NOTE_FIELDS) + ".",
    )
    vendor_note_urgent: str = Field(
        "This order is urgent: please expedite if you can and confirm the earliest delivery date.",
        max_length=300,
        description="Added to the note when even this vendor's usual delivery time is after you would run out of stock. Empty = never added.",
    )

    @model_validator(mode="after")
    def _check(self) -> "ProcurementSettings":
        if self.weight_cost + self.weight_delivery + self.weight_reliability + self.weight_terms <= 0:
            raise ValueError("at least one weight must be above zero")
        try:
            self.vendor_note_template.format_map({k: "x" for k in VENDOR_NOTE_FIELDS})
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"vendor_note_template can only use {', '.join('{' + k + '}' for k in VENDOR_NOTE_FIELDS)} ({exc!r} is not allowed)") from exc
        return self


class CalendarEvent(BaseModel):
    """A festival, promotion or other known period when demand differs from the usual."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str = Field(min_length=1, max_length=80, description="What it is, e.g. 'Diwali sale'.")
    kind: Literal["festival", "promotion", "other"] = Field("promotion", description="Only a label.")
    start: date_type = Field(description="First day of the period.")
    end: date_type = Field(description="Last day of the period (same as start for one day).")
    uplift_pct: float = Field(description="Expected change in daily demand during the period, in %. +40 = 40% more; -20 = 20% less.", gt=-100, le=1000)
    item_groups: list[str] = Field(default_factory=list, description="Only these item groups. Empty groups and items = every item.")
    items: list[str] = Field(default_factory=list, description="Only these item codes. Empty groups and items = every item.")
    note: str = Field("", max_length=300, description="Free text for you; not used in any calculation.")

    @model_validator(mode="after")
    def _order(self) -> "CalendarEvent":
        if self.end < self.start:
            raise ValueError(f"{self.name!r}: the end date is before the start date")
        return self


class CalendarSettings(BaseModel):
    """Known demand events. Used to adjust days of cover, early warnings and suggested order quantities."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(True, description="Use the calendar when working out demand. Off = ignore every event.")
    events: list[CalendarEvent] = Field(
        default_factory=list, max_length=300,
        description="Festivals and promotions. History-based seasonality may already include recurring festivals; "
                    "add events for what history cannot show (a new promotion, a one-off festival).",
    )


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")


class AssistantSettings(BaseModel):
    """The local language model that answers 'why?' questions. It only explains recorded data; it never decides."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(False, description="Let people ask questions about a request in the console. Off = the feature is hidden.")
    base_url: str = Field("http://127.0.0.1:11434", description="Address of the Ollama server. Must be on this machine unless 'allow_remote' is on.")
    model: str = Field("llama3.1", min_length=1, description="Ollama model name (as shown by 'ollama list').")
    timeout_seconds: int = Field(240, ge=10, le=1800, description="How long to wait for an answer. A laptop CPU can take a minute or more.")
    max_answer_chars: int = Field(900, ge=100, le=4000, description="Longer answers are rejected as unchecked.")
    allow_remote: bool = Field(False, description="Allow an Ollama server on another machine. Off keeps all data on this server.")

    @model_validator(mode="after")
    def _check(self) -> "AssistantSettings":
        from urllib.parse import urlparse
        url = urlparse(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("base_url must look like http://127.0.0.1:11434")
        if not self.allow_remote and url.hostname not in LOCAL_HOSTS:
            raise ValueError("base_url must be on this machine (localhost) unless allow_remote is switched on")
        return self


# Where the agents' language model can run. "ollama" is a model on this computer (or another one you run); the others are hosted services that bill per token.
# An API key is never a setting: it is read from the environment (the .env file) by config.py, so it cannot be typed into the screen or stored in the database.
AGENT_PROVIDERS = ("ollama", "anthropic", "openai", "gemini")
PROVIDER_LABELS = {"ollama": "Ollama (on this computer)", "anthropic": "Claude (Anthropic)", "openai": "OpenAI", "gemini": "Gemini (Google)"}
PROVIDER_URLS = {"ollama": "http://127.0.0.1:11434", "anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1", "gemini": "https://generativelanguage.googleapis.com"}
# Starting suggestions only: model names change. "Check connection" lists what the key can really use.
PROVIDER_MODELS = {"ollama": "llama3.1", "anthropic": "claude-haiku-5-5", "openai": "gpt-4o-mini", "gemini": "gemini-2.5-flash"}


class AgentSettings(BaseModel):
    """The AI agents (inventory and purchasing). A language model plans and words; the tools supply every number, and a person still approves every order.
    What is sent to the model is described in docs/PRIVACY.md."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = Field(False, description="Let the agents run. Off = nothing is sent to any model by the agents.")
    provider: Literal["ollama", "anthropic", "openai", "gemini"] = Field("ollama", description="Where the language model runs: ollama (this computer), anthropic (Claude), openai or gemini (hosted services, billed per token).")
    base_url: str = Field("", description="Address of the model server. Empty = the provider's own address (for Ollama http://127.0.0.1:11434). Fill it in for an Ollama on another machine or an OpenAI-compatible service.")
    model: str = Field("", description="The model's name. Empty = a suggested one for the provider. For Ollama it must support tool calling (see 'ollama list').")
    price_input_per_million: float = Field(0.0, ge=0, le=100000, description="What your provider charges per million input tokens, in your own currency (from its price list). With a price set the screen shows an estimated cost. 0 = do not estimate.")
    price_output_per_million: float = Field(0.0, ge=0, le=100000, description="What your provider charges per million output tokens, in your own currency. 0 = do not estimate.")
    monthly_token_limit: int = Field(0, ge=0, le=10_000_000_000, description="Most tokens (input and output) the agents may use on a hosted model in a calendar month. At the limit they stop using it and the plain rules answer instead. 0 = no limit.")
    timeout_seconds: int = Field(300, ge=30, le=1800, description="How long to wait for ONE model reply. A laptop CPU can take a minute or more.")
    tools_url: str = Field("", description="Use another MCP server's tools instead of the built-in ones (an address ending in /mcp). Its access key is read from the environment variable ERP_AGENTS_MCP_TOKEN. Empty = the built-in tools.")
    max_output_tokens: int = Field(400, ge=64, le=4000, description="Longest reply the model may write. A small model can run on without ever closing its answer; this stops it (a reply cut short is rejected like any other bad one).")
    max_steps: int = Field(6, ge=1, le=12, description="Most model replies in one task. Each reply may call tools; the task stops here even if the model wants more.")
    max_tool_calls: int = Field(10, ge=1, le=30, description="Most tool calls in one task.")
    allow_remote: bool = Field(False, description="Allow a model server on another machine or a hosted model. Off keeps everything the agents read on this server.")
    keep_traces_days: int = Field(30, ge=1, le=365, description="How long the record of what was sent to the model and what it answered is kept.")
    second_opinion: bool = Field(False, description="The purchasing agent reads each new order request and sends a short second opinion with it. It never changes the request.")
    daily_brief_cron: str = Field("", description="When the inventory agent writes its brief (cron, for example '0 9 * * *'). Empty = only when asked.")
    send_brief: bool = Field(False, description="Send the inventory brief to the approvers on Telegram and WhatsApp as a plain message.")

    def effective_base_url(self) -> str:
        return (self.base_url.strip() or PROVIDER_URLS[self.provider]).rstrip("/")

    def effective_model(self) -> str:
        return self.model.strip() or PROVIDER_MODELS[self.provider]

    @model_validator(mode="after")
    def _check(self) -> "AgentSettings":
        from urllib.parse import urlparse
        url = urlparse(self.effective_base_url())
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("base_url must look like http://127.0.0.1:11434")
        if not self.allow_remote and url.hostname not in LOCAL_HOSTS:
            if self.provider != "ollama" and not self.base_url.strip():
                raise ValueError(f"{PROVIDER_LABELS[self.provider]} is a hosted service: what the agents read is sent to it. Switch on allow_remote to confirm that, as described in the privacy notes")
            raise ValueError("base_url must be on this machine (localhost) unless allow_remote is switched on")
        if self.provider != "ollama" and self.base_url.strip() and url.scheme != "https" and url.hostname not in LOCAL_HOSTS:
            raise ValueError("a hosted service must be reached over https")
        if self.tools_url.strip():
            tools = urlparse(self.tools_url.strip())
            if tools.scheme not in ("http", "https") or not tools.hostname:
                raise ValueError("tools_url must look like http://127.0.0.1:8100/mcp")
            if not self.allow_remote and tools.hostname not in LOCAL_HOSTS:
                raise ValueError("tools_url must be on this machine (localhost) unless allow_remote is switched on")
        if self.daily_brief_cron.strip() and len(self.daily_brief_cron.split()) != 5:
            raise ValueError("daily_brief_cron must be a cron expression with 5 fields, for example '0 9 * * *'")
        return self


def policy_schema() -> dict:
    """Field metadata for admin UIs (name, type, default, description, constraints)."""
    return {
        "policy": Policy.model_json_schema()["properties"],
        "system": SystemSettings.model_json_schema()["properties"],
        "procurement": ProcurementSettings.model_json_schema()["properties"],
        "assistant": AssistantSettings.model_json_schema()["properties"],
        "agents": AgentSettings.model_json_schema()["properties"],
        "calendar": CalendarSettings.model_json_schema()["properties"],
    }
