"""Admin-editable settings with scope inheritance and a full audit trail."""
from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import ValidationError

from .common import Actor, Clock, iso, utcnow
from .policy import AgentSettings, AssistantSettings, CalendarSettings, Policy, ProcurementSettings, SystemSettings
from .storage import Database, dumps, loads

SCOPES = ("global", "group", "item")
# Settings that were removed. Saved values for them are ignored (and dropped on the next save of that module).
RETIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "procurement": ("tax_rules", "home_country", "unknown_origin", "po_tax_template_import", "po_tax_template_domestic"),
}
GLOBAL_KEY = "*"


class SettingsError(ValueError):
    pass


class PermissionDenied(PermissionError):
    pass


class SettingsStore:
    def __init__(
        self,
        db: Database,
        base_policy: Optional[dict] = None,
        base_system: Optional[dict] = None,
        clock: Clock = utcnow,
        base_procurement: Optional[dict] = None,
        base_assistant: Optional[dict] = None,
        base_calendar: Optional[dict] = None,
        base_agents: Optional[dict] = None,
    ) -> None:
        self.db = db
        self.clock = clock
        self._base_policy = base_policy or {}
        self._base_system = base_system or {}
        # Module settings (one flat model per module, admin-editable, audited). A new module adds one entry here.
        self._sections: dict[str, tuple[type, dict]] = {
            "procurement": (ProcurementSettings, base_procurement or {}),
            "assistant": (AssistantSettings, base_assistant or {}),
            "calendar": (CalendarSettings, base_calendar or {}),
            "agents": (AgentSettings, base_agents or {}),
        }
        self._listeners: list[Callable[[str, str], None]] = []
        # Fail fast if the yaml defaults themselves are invalid.
        Policy.model_validate(self._base_policy)
        SystemSettings.model_validate(self._base_system)
        for model, base in self._sections.values():
            model.model_validate(base)

    # ---------------------------------------------------------------- listeners
    def on_change(self, fn: Callable[[str, str], None]) -> None:
        """fn(scope_type, scope_key) is called after every successful write (e.g. to reschedule)."""
        self._listeners.append(fn)

    # ---------------------------------------------------------------- reads
    def get_overrides(self, scope_type: str, scope_key: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT data_json FROM settings_overrides WHERE scope_type=? AND scope_key=?", (scope_type, scope_key)
        )
        return loads(row["data_json"]) if row else {}

    def effective_policy(self, item_code: Optional[str] = None, item_group: Optional[str] = None) -> Policy:
        return self._resolve(item_code, item_group)[0]

    def policy_with_sources(self, item_code: Optional[str] = None, item_group: Optional[str] = None) -> tuple[Policy, dict[str, str]]:
        """Effective policy plus, for every field an admin changed, where it came from."""
        return self._resolve(item_code, item_group)

    def _resolve(self, item_code: Optional[str], item_group: Optional[str]) -> tuple[Policy, dict[str, str]]:
        merged: dict[str, Any] = dict(self._base_policy)
        sources: dict[str, str] = {k: "config file" for k in self._base_policy}
        layers = [("global", GLOBAL_KEY)]
        if item_group:
            layers.append(("group", item_group))
        if item_code:
            layers.append(("item", item_code))
        for scope_type, key in layers:
            ov = self.get_overrides(scope_type, key)
            for field, value in ov.items():
                merged[field] = value
                sources[field] = f"{scope_type}{'' if scope_type == 'global' else ' ' + repr(key)} override"
        try:
            return Policy.model_validate(merged), sources
        except ValidationError as exc:
            raise SettingsError(f"Invalid effective policy for item={item_code!r} group={item_group!r}: {_first_error(exc)}") from exc

    def system(self) -> SystemSettings:
        merged = {**self._base_system, **self.get_overrides("system", GLOBAL_KEY)}
        return SystemSettings.model_validate(merged)

    def _section_overrides(self, kind: str) -> dict[str, Any]:
        """Saved values for a module, without settings that no longer exist (so an old save can never break loading)."""
        _model, _base = self._sections[kind]
        return {k: v for k, v in self.get_overrides(kind, GLOBAL_KEY).items() if k not in RETIRED_FIELDS.get(kind, ())}

    def section(self, kind: str) -> Any:
        if kind not in self._sections:
            raise SettingsError(f"unknown settings section {kind!r}")
        model, base = self._sections[kind]
        return model.model_validate({**base, **self._section_overrides(kind)})

    def procurement(self) -> ProcurementSettings:
        return self.section("procurement")

    def assistant(self) -> AssistantSettings:
        return self.section("assistant")

    def agents(self) -> AgentSettings:
        return self.section("agents")

    def calendar(self) -> CalendarSettings:
        return self.section("calendar")

    # ---------------------------------------------------------------- writes (admin only)
    def update_policy(
        self,
        actor: Actor,
        scope_type: str,
        scope_key: str,
        set_values: Optional[dict[str, Any]] = None,
        clear: Optional[list[str]] = None,
    ) -> Policy:
        """`set_values` may contain None for nullable thresholds (e.g. min_qty=None switches the rule off).
        `clear` removes an override so the value is inherited again."""
        self._require_admin(actor)
        if scope_type not in SCOPES:
            raise SettingsError(f"scope_type must be one of {SCOPES}")
        if scope_type == "global":
            scope_key = GLOBAL_KEY
        elif not scope_key or scope_key == GLOBAL_KEY:
            raise SettingsError("scope_key is required for group/item scope")
        set_values = set_values or {}
        clear = clear or []
        unknown = [f for f in [*set_values, *clear] if f not in Policy.model_fields]
        if unknown:
            raise SettingsError(f"Unknown policy field(s): {', '.join(unknown)}")

        old = self.get_overrides(scope_type, scope_key)
        new = {k: v for k, v in old.items() if k not in clear}
        new.update(set_values)

        # Validate: the new override layered on config + global (+ nothing else known here).
        probe = {**self._base_policy}
        if scope_type != "global":
            probe.update(self.get_overrides("global", GLOBAL_KEY))
        probe.update(new)
        try:
            validated = Policy.model_validate(probe)
        except ValidationError as exc:
            raise SettingsError(_first_error(exc)) from exc

        self._save("policy", scope_type, scope_key, old, new, actor)
        return validated

    def reset_scope(self, actor: Actor, scope_type: str, scope_key: str) -> None:
        self._require_admin(actor)
        if scope_type == "global":
            scope_key = GLOBAL_KEY
        old = self.get_overrides(scope_type, scope_key)
        if old:
            self._save("policy", scope_type, scope_key, old, {}, actor)

    def update_system(self, actor: Actor, set_values: dict[str, Any]) -> SystemSettings:
        self._require_admin(actor)
        unknown = [f for f in set_values if f not in SystemSettings.model_fields]
        if unknown:
            raise SettingsError(f"Unknown system field(s): {', '.join(unknown)}")
        old = self.get_overrides("system", GLOBAL_KEY)
        new = {**old, **set_values}
        try:
            validated = SystemSettings.model_validate({**self._base_system, **new})
        except ValidationError as exc:
            raise SettingsError(_first_error(exc)) from exc
        self._save("system", "system", GLOBAL_KEY, old, new, actor)
        return validated

    def update_section(self, actor: Actor, kind: str, set_values: dict[str, Any], clear: Optional[list[str]] = None) -> Any:
        """Change a module's settings. `clear` removes an override so the config-file/default value applies again."""
        self._require_admin(actor)
        if kind not in self._sections:
            raise SettingsError(f"unknown settings section {kind!r}")
        model, base = self._sections[kind]
        clear = clear or []
        unknown = [f for f in [*set_values, *clear] if f not in model.model_fields]
        if unknown:
            raise SettingsError(f"Unknown {kind} field(s): {', '.join(unknown)}")
        old = self._section_overrides(kind)
        new = {k: v for k, v in old.items() if k not in clear}
        new.update(set_values)
        try:
            validated = model.model_validate({**base, **new})
        except ValidationError as exc:
            raise SettingsError(_first_error(exc)) from exc
        self._save(kind, kind, GLOBAL_KEY, old, new, actor)
        return validated

    def update_procurement(self, actor: Actor, set_values: dict[str, Any], clear: Optional[list[str]] = None) -> ProcurementSettings:
        return self.update_section(actor, "procurement", set_values, clear)

    # ---------------------------------------------------------------- audit
    def audit(self, limit: int = 100, scope_type: Optional[str] = None, scope_key: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM settings_audit"
        params: list[Any] = []
        conds = []
        if scope_type:
            conds.append("scope_type=?")
            params.append(scope_type)
        if scope_key:
            conds.append("scope_key=?")
            params.append(scope_key)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [
            {
                "id": r["id"], "ts": r["ts"], "actor": r["actor"], "scope_type": r["scope_type"],
                "scope_key": r["scope_key"], "field": r["field"],
                "old": loads(r["old_json"]), "new": loads(r["new_json"]),
            }
            for r in self.db.query(sql, params)
        ]

    # ---------------------------------------------------------------- internals
    def _require_admin(self, actor: Actor) -> None:
        if not actor.has_role("admin"):
            raise PermissionDenied(f"{actor.id!r} is not allowed to change settings (admin role required)")

    def _save(self, kind: str, scope_type: str, scope_key: str, old: dict, new: dict, actor: Actor) -> None:
        now = iso(self.clock())
        db_scope = kind if kind == "system" or kind in self._sections else scope_type
        with self.db.transaction():
            if new:
                self.db.execute(
                    "INSERT INTO settings_overrides(scope_type, scope_key, data_json, updated_by, updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(scope_type, scope_key) DO UPDATE SET data_json=excluded.data_json, "
                    "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                    (db_scope, scope_key, dumps(new), actor.id, now),
                )
            else:
                self.db.execute("DELETE FROM settings_overrides WHERE scope_type=? AND scope_key=?", (db_scope, scope_key))
            for field in sorted(set(old) | set(new)):
                before, after = old.get(field, "<inherited>"), new.get(field, "<inherited>")
                if before != after:
                    self.db.execute(
                        "INSERT INTO settings_audit(ts, actor, scope_type, scope_key, field, old_json, new_json) VALUES (?,?,?,?,?,?,?)",
                        (now, actor.id, db_scope, scope_key, field, dumps(before), dumps(after)),
                    )
        for fn in self._listeners:
            fn(db_scope, scope_key)


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(x) for x in err.get("loc", ()))
    msg = err.get("msg", "invalid value")
    return f"{loc}: {msg}" if loc else msg
