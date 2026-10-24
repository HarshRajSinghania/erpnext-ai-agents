"""Command line: try it, operate it, and administer it without touching the API."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

from .approvals.channels.telegram import TelegramChannel
from .approvals.render import render_plain
from .bootstrap import App, build_app
from .config import load_config
from .core.common import Actor
from .core.settings_store import SettingsError
from .inventory.analysis import SEV_NAMES, Analysis

CLI_ACTOR = Actor(id="cli", roles=frozenset({"admin", "approver"}), via="cli")


def _app(args) -> App:
    return build_app(config=load_config(args.config))


def _parse_value(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _print_analysis(a: Analysis) -> None:
    u = a.usage
    print(f"{a.item_code}  {a.item_name}   status={a.status} severity={SEV_NAMES[a.severity]}")
    print(f"  on hand {a.on_hand:g} {a.uom} | on order {a.incoming:g} | usage {u.planning_rate:.2f}/day (recent {u.recent_rate:.2f}, base {u.base_rate:.2f}) | forecast {a.forecast_rate:.2f}/day")
    if a.cover_days_on_hand is not None:
        print(f"  cover {a.cover_days_on_hand:.1f} days | stock-out {a.stockout_date} | lead time {a.lead_time_days:g}d ({a.lead_time_source})")
    for r in a.reasons:
        print(f"  - {r.text}")
    for d in a.drivers:
        print(f"  * {d}")
    for n in a.notes:
        print(f"  ! {n}")


# ------------------------------------------------------------------ commands
def cmd_init(args) -> int:
    target = Path(args.dir) / "config"
    target.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parent / "settings.example.yaml"
    dst = target / "settings.yaml"
    if dst.exists() and not args.force:
        print(f"{dst} already exists (use --force to overwrite)")
        return 1
    shutil.copy(src, dst)
    print(f"Wrote {dst}. Edit it, set the environment variables it mentions, then run: erp-agents run-once --dry-run")
    return 0


def cmd_demo(args) -> int:
    from .config import DEFAULT_CONFIG
    import copy
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg["database"]["path"] = ":memory:"
    app = build_app(config=cfg)
    app.channels["console"].writer = lambda s: print(s) if args.verbose else None
    report = app.inventory.run()
    print(f"\nChecked {report.checked} items on the demo ERP: {report.ok} fine, {report.early_warnings} early warnings, {report.breaches} threshold breaches.\n")
    for act in report.actions:
        print(f"  {act['item_code']:<10} {act['status']:<14} {act['severity']:<14} -> {act['action']}")
    pending = app.approvals.list(status="pending")
    if not pending:
        return 0
    if args.interactive:
        for rec in sorted(pending, key=lambda r: r.request.facts.get("item_code", "")):
            print("\n" + "=" * 72 + "\n" + render_plain(rec.request) + "\n")
            while True:
                choice = input("[a]pprove  [s]nooze  [i]gnore  [d]etails  [enter]=skip > ").strip().lower()
                action = {"a": "approve", "s": "snooze", "i": "dismiss", "d": "details"}.get(choice)
                if not choice:
                    break
                if not action:
                    continue
                res = app.approvals.decide(rec.id, action, CLI_ACTOR, "cli")
                if res.status == "info":
                    print(render_plain(rec.request, sections=res.info_sections, with_title=False))
                    continue
                print(res.message)
                break
        print("\nWork orders handed to procurement:")
        for wo in app.workorders.list():
            p = wo["payload"]
            print(f"  {wo['type']}: {p['item_code']} ~{p['suggested_qty']:g} {p['uom']} ({p['severity']})")
    else:
        first = sorted(pending, key=lambda r: -["INFO", "EARLY_WARNING", "HIGH", "CRITICAL"].index(r.request.severity))[0]
        print("\nExample approval message (what the owner receives):\n" + "=" * 72)
        print(render_plain(first.request))
        print("=" * 72 + "\nRun with --interactive to answer the requests yourself, or --verbose to print all of them.")
    return 0


def cmd_run_once(args) -> int:
    app = _app(args)
    report = app.inventory.run(dry_run=args.dry_run, item_codes=args.item or None)
    mode = "DRY RUN (nothing sent)" if args.dry_run else "LIVE"
    print(f"{mode}: checked {report.checked}, ok {report.ok}, early warnings {report.early_warnings}, breaches {report.breaches}, "
          f"stock alerts {report.new_notices}, new reorder requests {report.new_alerts}, skipped {report.skipped}, resolved {report.resolved}")
    for act in report.actions:
        if act["action"] != "ok":
            print(f"  {act['item_code']:<14} {act['severity']:<14} {act['action']}")
    for err in report.errors:
        print(f"  ERROR {err}")
    return 1 if report.errors else 0


def cmd_analyze(args) -> int:
    app = _app(args)
    try:
        a, sources = app.inventory.analyze_one(args.item)
    except KeyError as exc:
        print(exc)
        return 1
    _print_analysis(a)
    if sources:
        print("  settings in force: " + ", ".join(f"{k} ({v})" for k, v in sorted(sources.items())))
    return 0


def cmd_policy(args) -> int:
    app = _app(args)
    key = "*" if args.scope == "global" else args.key
    if args.scope != "global" and not key:
        print("--key is required for group/item scope")
        return 2
    try:
        if args.action == "show":
            item = key if args.scope == "item" else None
            group = key if args.scope == "group" else None
            policy, sources = app.settings.policy_with_sources(item, group)
            print("overrides:", json.dumps(app.settings.get_overrides(args.scope, key), indent=2))
            for k, v in policy.model_dump().items():
                print(f"  {k:<22} {v!s:<12} {('<- ' + sources[k]) if k in sources else ''}")
        elif args.action == "set":
            changes = dict(kv.split("=", 1) for kv in args.values)
            app.settings.update_policy(CLI_ACTOR, args.scope, key, {k: _parse_value(v) for k, v in changes.items()})
            print("saved")
        elif args.action == "clear":
            app.settings.update_policy(CLI_ACTOR, args.scope, key, clear=args.values)
            print("cleared (now inherited)")
        elif args.action == "reset":
            app.settings.reset_scope(CLI_ACTOR, args.scope, key)
            print("all overrides removed for this scope")
        elif args.action == "audit":
            for r in app.settings.audit(30, args.scope if args.scope != "global" else None, key if args.scope != "global" else None):
                print(f"{r['ts']}  {r['actor']:<8} {r['scope_type']}:{r['scope_key']}  {r['field']}: {r['old']} -> {r['new']}")
    except (SettingsError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    return 0


def cmd_approvals(args) -> int:
    app = _app(args)
    if args.action == "list":
        for rec in app.approvals.list(args.status or None):
            print(f"{rec.id}  {rec.status:<9} {rec.request.severity:<13} {rec.request.headline}")
        return 0
    res = app.approvals.decide(args.id, args.choice, CLI_ACTOR, "cli")
    if res.status == "info":
        print(render_plain(res.approval.request, sections=res.info_sections, with_title=False))
    else:
        print(res.message)
    return 0 if res.ok else 1


def cmd_alerts(args) -> int:
    app = _app(args)
    for al in app.alerts.list(args.status, args.item):
        print(f"{al.id}  {al.created_at:%Y-%m-%d %H:%M}  {al.item_code:<12} {al.kind:<14} {SEV_NAMES[al.severity]:<13} {al.status:<10} {al.status_note}")
    return 0


def cmd_procurement(args) -> int:
    app = _app(args)
    if args.action == "run":
        report = app.procurement.run(dry_run=args.dry_run)
        mode = "DRY RUN (nothing sent)" if args.dry_run else "LIVE"
        print(f"{mode}: considered {report.considered}, proposed {report.proposed}, needs-your-decision {report.manual}, skipped {report.skipped}, resumed {report.resumed}")
        for act in report.actions:
            extra = f"-> {act['vendor']} x {act['qty']:g} = {act['total']:,.2f} (score {act['score']})" if "vendor" in act else act.get("problem", "")
            print(f"  {act['item_code']:<14} {act['action']:<20} {extra}")
        for err in report.errors:
            print(f"  ERROR {err}")
        return 1 if report.errors else 0
    for p in app.proposals.list(args.status):
        print(f"{p.id}  {p.created_at:%Y-%m-%d %H:%M}  {p.item_code:<12} {p.status:<12} {p.supplier_name or '-':<20} {p.po_name or ''}  {p.status_note}")
    return 0


def cmd_erp_check(args) -> int:
    app = _app(args)
    print("healthcheck:", "OK" if app.erp.healthcheck() else "FAILED")
    items = app.erp.list_items()
    print(f"{len(items)} stock items found")
    for it in items[:5]:
        st = app.erp.get_stock([it.code])[it.code]
        print(f"  {it.code}: {it.name} | group={it.group} | on hand {st.on_hand:g} | on order {st.incoming:g} | lead time {it.lead_time_days}")
    print("Compare these quantities with Stock > Stock Balance in ERPNext before trusting any alert.")
    return 0


def cmd_erp_setup(args) -> int:
    """One-time ERPNext customisation: the 'Agent proposal reference' field on Purchase Order (prevents duplicate orders)."""
    app = _app(args)
    ensure = getattr(app.erp, "ensure_po_reference_field", None)
    if ensure is None:
        print("this ERP adapter needs no setup")
        return 0
    try:
        print(f"Purchase Order fields 'Agent proposal reference' and 'Approved by (Stock Assistant)': {ensure()}")
    except Exception as exc:  # noqa: BLE001
        print(f"setup failed: {exc}")
        return 1
    return 0


def cmd_erp_check_access(args) -> int:
    """Read-only: is the service account limited to what the Stock Assistant needs?"""
    from .auth.access import check_service_account
    app = _app(args)
    erp = app.erp
    if not hasattr(erp, "session"):
        print("this ERP adapter has no service account to check")
        return 0
    r = check_service_account(erp.session, erp.base_url)
    print(f"Service account: {r.user}, {len(r.roles)} roles")
    if r.missing:
        print("It is MISSING access the app needs:", ", ".join(f"{d} ({p})" for d, p in r.missing))
    print(f"Roles that cover everything the app needs ({len(r.keep)}): {', '.join(r.keep) or 'none'}")
    if r.surplus:
        print(f"Roles it does not need ({len(r.surplus)}): {', '.join(r.surplus)}")
    if r.risky_held:
        print("High-risk roles among them:", ", ".join(r.risky_held))
        print("Remove these in ERPNext (User > Roles) from the service account; a stolen key could otherwise change anything in ERPNext.")
    elif not r.surplus:
        print("Nothing to remove.")
    return 1 if r.missing else 0


def cmd_telegram(args) -> int:
    app = _app(args)
    ch = app.channels.get("telegram")
    if not isinstance(ch, TelegramChannel):
        print("The Telegram channel is not switched on. Put TELEGRAM_BOT_TOKEN in .env (create the bot with @BotFather), then try again.")
        return 1
    try:
        return _telegram_action(app, ch, args)
    except (RuntimeError, ValueError) as exc:
        print(f"Telegram said no: {exc}")
        return 1


def _telegram_recipients(app) -> list[tuple[str, str]]:
    return [(r.id, c.address or "") for r in app.router.recipients for c in r.channels if c.type == "telegram"]


def _telegram_action(app, ch: TelegramChannel, args) -> int:
    if args.action == "set-webhook":
        if not args.url:
            print("Give the public address: erp-agents telegram set-webhook https://<your-tunnel>/webhooks/telegram")
            return 1
        print(ch.set_webhook(args.url))
        return 0
    if args.action == "delete-webhook":
        print(ch.delete_webhook())
        return 0
    if args.action == "chats":
        chats = ch.recent_chats()
        if not chats:
            print("Nobody has written to the bot in the last 24 hours. Open your bot in Telegram, press Start (or send /start), then run this again.\n"
                  "(If the app is running in polling mode it answers /start itself with the chat id, and this list stays empty because the app already read the message.)")
            return 0
        print("People who wrote to the bot (use the chat id as OWNER_TELEGRAM_CHAT_ID):")
        for c in chats:
            print(f"  chat id {c['chat_id']:>12}   {c['name']}" + (f" (@{c['username']})" if c["username"] else "") + f"   last wrote: {c['last_text']!r}")
        return 0
    if args.action == "test":
        targets = [(rid, addr) for rid, addr in _telegram_recipients(app) if ch.address_problem(addr) is None]
        if not targets:
            print("No recipient has a usable Telegram chat id yet. Write /start to the bot, put the number it sends back in OWNER_TELEGRAM_CHAT_ID, restart.")
            return 1
        bad = 0
        for rid, addr in targets:
            res = ch.send_text(addr, "Stock Assistant: this is a test message. If you can read it, Telegram is connected and low-stock alerts will arrive here.")
            print(f"  {rid} (chat {addr}): " + ("delivered" if res.ok else f"FAILED - {res.error}"))
            bad += 0 if res.ok else 1
        return 1 if bad else 0
    # status
    me = ch.me()
    print(f"Bot: @{me.get('username')} ({me.get('first_name')})   mode: {ch.mode}")
    info = ch.webhook_info()
    if ch.mode == "polling":
        print("Webhook: " + ("NONE (good for polling)" if not info.get("url") else f"still registered ({info['url']}); the app clears it when it starts in polling mode"))
    else:
        print("Webhook: " + (info.get("url") or "NOT registered: run telegram set-webhook https://<your-tunnel>/webhooks/telegram"))
        if info.get("last_error_message"):
            print(f"  Telegram's last delivery error: {info['last_error_message']}")
        print(f"  waiting updates: {info.get('pending_update_count', 0)}")
    recipients = _telegram_recipients(app)
    if not recipients:
        print("Recipients on Telegram: none listed in approvals.recipients")
    for rid, addr in recipients:
        problem = ch.address_problem(addr)
        print(f"Recipient {rid}: " + ("chat id " + addr + " ok" if problem is None else "NOT READY - the address " + problem))
    return 0


def cmd_mcp(args) -> int:
    """Serve the stock tools to an MCP client (Claude Desktop, another agent) over stdin/stdout. Who the client is: `mcp.stdio_role` in the settings file (viewer or approver)."""
    try:
        from .mcp_server import build_mcp_server
    except ImportError:
        print('The MCP server needs an extra package: pip install "erp-agents[agents]"', file=sys.stderr)
        return 2
    import anyio
    app = _app(args)
    role = str((app.config.get("mcp") or {}).get("stdio_role", "viewer")).lower()
    if role not in ("viewer", "approver"):
        print("mcp.stdio_role must be 'viewer' (look only) or 'approver' (may also prepare an order request for a person to answer).", file=sys.stderr)
        return 2
    console = app.channels.get("console")
    if console is not None:                           # stdout belongs to the protocol: nothing else may be written to it
        console.writer = lambda text: print(text, file=sys.stderr)
    actor = Actor(id="mcp:stdio", roles=frozenset({role}), via="mcp-stdio")
    print(f"Stock Assistant MCP server (stdio) is ready, acting as '{role}'.", file=sys.stderr)
    anyio.run(build_mcp_server(app.toolkit, default_actor=actor, via="mcp-stdio").run_stdio_async)
    return 0


def _tokens_text(tokens: dict[str, Any]) -> str:
    text = f"{tokens['total']:,} tokens ({tokens['input']:,} in, {tokens['output']:,} out)"
    return text + (f", about {tokens['estimated_cost']:g} at your prices" if tokens.get("estimated_cost") is not None else "")


def _print_agent_result(res: dict[str, Any]) -> None:
    who = f"model {res['model']} via {res.get('provider', 'ollama')}, {res.get('seconds', 0)}s" if res.get("source") == "model" else "plain rules (no AI used)"
    if res.get("hosted") and res.get("tokens"):
        who += "; " + _tokens_text(res["tokens"])
    if res.get("text"):
        print(res["text"])
    elif res.get("answer") is not None:
        print(res["answer"])
    elif res.get("verdict"):
        from .agents.purchase_agent import review_text
        print(review_text(res))
    print(f"\n[{who}; task {res.get('task_id') or '-'}]" + (f" Note: {res['note']}" if res.get("note") else ""))


def cmd_agents(args) -> int:
    from .agents.privacy import privacy_summary
    app = _app(args)
    svc = app.agents
    if args.action == "status":
        print(json.dumps(svc.status(check_model=True), indent=2, default=str))
    elif args.action == "privacy":
        print(json.dumps(privacy_summary(svc.settings), indent=2))
    elif args.action == "brief":
        _print_agent_result(svc.brief("cli", use_model=False if args.rules else None))
    elif args.action == "review":
        if not args.text:
            print("Give the request id: erp-agents agents review <request id>   (see 'erp-agents approvals list')")
            return 2
        try:
            _print_agent_result(svc.review(args.text[0], "cli", force=args.force, use_model=False if args.rules else None))
        except ValueError as exc:
            print(exc)
            return 1
    elif args.action == "ask":
        question = " ".join(args.text or [])
        if not question:
            print('Give a question: erp-agents agents ask "what is running low?"')
            return 2
        try:
            _print_agent_result(svc.ask(question, CLI_ACTOR))
        except ValueError as exc:
            print(exc)
            return 1
    elif args.action == "usage":
        u = svc.usage(args.limit)
        month = u["month_hosted"]
        print(f"This month (since {u['month_since']}), hosted models: {month['tasks']} tasks, {_tokens_text(month)}"
              + (f"; limit {u['monthly_token_limit']:,} tokens, {u['limit_used_percent']}% used" if u["monthly_token_limit"] else "; no monthly limit"))
        print(f"This month, models on this computer: {u['month_local']['tasks']} tasks, {u['month_local']['total']:,} tokens (free)")
        print(f"Last {u['days']} days by model:")
        for r in u["by_model"]:
            cost = f", about {r['estimated_cost']:g}" if r["estimated_cost"] is not None else ""
            print(f"  {r['provider']:9} {r['model']:28} {'hosted' if r['hosted'] else 'local ':6} {r['tasks']:>4} tasks  {r['input']:>10,} in  {r['output']:>9,} out{cost}")
    elif args.action == "tasks":
        for t in svc.log.list(limit=args.limit):
            print(f"{t['started_at'][:19]}  {t['id']}  {t['agent']}/{t['task']}  {t['status']:9}  {t['llm_seconds'] or 0:>6}s  steps={t['steps']} tools={t['tool_calls']}  {t['model']}  {t['note'] or ''}")
    return 0


def cmd_serve(args) -> int:
    from contextlib import asynccontextmanager

    import uvicorn
    from .api.app import create_app
    from .scheduler import AgentScheduler

    app = _app(args)
    scheduler = AgentScheduler(app)

    @asynccontextmanager
    async def lifespan(_):
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown()

    api = create_app(app, on_system_change=scheduler.reschedule, lifespan=lifespan)
    srv = app.config.get("server", {})
    host = args.host or srv.get("host", "127.0.0.1")
    mode = str(srv.get("remote_access", "full")).lower()
    print(f"Remote access: {'only the channel webhooks are reachable through a tunnel or proxy' if mode == 'webhooks_only' else 'the console and API are reachable through a tunnel or proxy (server.remote_access: webhooks_only closes them)'}.")
    from .api.hardening import access_key_warning
    warning = access_key_warning(app.config.get("auth"), bool(app.tokens))
    if warning:
        print(warning)
    if host not in ("127.0.0.1", "::1", "localhost"):
        print(f"WARNING: listening on {host}, not only on this machine. Anybody who can reach that address can reach the sign-in page.")
    uvicorn.run(api, host=host, port=args.port or int(srv.get("port", 8100)), log_level="info", server_header=False)
    return 0


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="erp-agents", description="Agentic inventory monitoring for small-business ERP")
    p.add_argument("--config", default=None, help="path to settings.yaml (default: $ERP_AGENTS_CONFIG or config/settings.yaml)")
    p.add_argument("-v", "--verbose-log", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="write a starter config/settings.yaml")
    s.add_argument("--dir", default=".")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("demo", help="run the agent on a built-in demo ERP (no setup needed)")
    s.add_argument("--interactive", action="store_true", help="answer the approval requests in the terminal")
    s.add_argument("--verbose", action="store_true", help="print every approval message")
    s.set_defaults(fn=cmd_demo)

    s = sub.add_parser("run-once", help="run the inventory check now")
    s.add_argument("--dry-run", action="store_true", help="analyse only; send nothing, store nothing")
    s.add_argument("--item", action="append", help="limit to this item code (repeatable)")
    s.set_defaults(fn=cmd_run_once)

    s = sub.add_parser("analyze", help="show the analysis for one item")
    s.add_argument("item")
    s.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("policy", help="view/change thresholds (admin)")
    s.add_argument("action", choices=["show", "set", "clear", "reset", "audit"])
    s.add_argument("--scope", choices=["global", "group", "item"], default="global")
    s.add_argument("--key", help="item group name or item code")
    s.add_argument("values", nargs="*", help="set: field=value (e.g. min_qty=150 min_days_cover=null); clear: field names")
    s.set_defaults(fn=cmd_policy)

    s = sub.add_parser("approvals", help="list/answer approval requests")
    s2 = s.add_subparsers(dest="action", required=True)
    ls = s2.add_parser("list")
    ls.add_argument("--status", default="pending")
    dc = s2.add_parser("decide")
    dc.add_argument("id")
    dc.add_argument("choice", help="approve | snooze | dismiss | details")
    s.set_defaults(fn=cmd_approvals)

    s = sub.add_parser("alerts", help="list alerts")
    s.add_argument("--status")
    s.add_argument("--item")
    s.set_defaults(fn=cmd_alerts)

    s = sub.add_parser("procurement", help="vendor selector: run it or list its proposals")
    s.add_argument("action", choices=["run", "proposals"])
    s.add_argument("--dry-run", action="store_true", help="run: show what it would recommend; send and store nothing")
    s.add_argument("--status", help="proposals: filter by status")
    s.set_defaults(fn=cmd_procurement)

    s = sub.add_parser("erp-check", help="test the ERP connection and print sample stock")
    s.set_defaults(fn=cmd_erp_check)

    s = sub.add_parser("erp-setup", help="one-time: add the 'Agent proposal reference' field to Purchase Order in ERPNext")
    s.set_defaults(fn=cmd_erp_setup)

    s = sub.add_parser("erp-check-access", help="read-only: does the ERPNext service account hold only the roles the app needs?")
    s.set_defaults(fn=cmd_erp_check_access)

    s = sub.add_parser("telegram", help="Telegram: status, test message, find chat ids, register/remove the webhook")
    s.add_argument("action", choices=["status", "test", "chats", "set-webhook", "delete-webhook"])
    s.add_argument("url", nargs="?", help="set-webhook only: the public https address of /webhooks/telegram")
    s.set_defaults(fn=cmd_telegram)

    s = sub.add_parser("mcp", help="serve the stock tools to an MCP client over stdio (needs the 'agents' extra)")
    s.set_defaults(fn=cmd_mcp)

    s = sub.add_parser("agents", help="the AI agents: status, today's stock brief, a second opinion on a request, ask a question, what was sent to the model")
    s.add_argument("action", choices=["status", "brief", "review", "ask", "tasks", "usage", "privacy"])
    s.add_argument("text", nargs="*", help="review: the request id; ask: the question")
    s.add_argument("--rules", action="store_true", help="brief/review: use rules only, send nothing to any model")
    s.add_argument("--force", action="store_true", help="review: ask again even if this request was reviewed")
    s.add_argument("--limit", type=int, default=15, help="tasks: how many; usage: how many days back")
    s.set_defaults(fn=cmd_agents)

    s = sub.add_parser("serve", help="start the API, scheduler and channel listeners")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)
    return p


def parse_cli(parser: argparse.ArgumentParser, argv: Optional[list[str]] = None) -> argparse.Namespace:
    """parse_args, except that `policy set --scope item --key X min_qty=5` also works on Python 3.10 and 3.11. There argparse matches the optional positional (the
    field=value list) before it has seen the options, then rejects what follows them as unrecognised. Anything left over belongs to that list."""
    args, extra = parser.parse_known_args(argv)
    if extra:
        if getattr(args, "fn", None) is cmd_policy and not any(x.startswith("-") for x in extra):
            args.values = [*args.values, *extra]
        else:
            parser.error("unrecognized arguments: " + " ".join(extra))
    return args


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):      # old Windows code pages must not crash on the emoji/bullets in messages
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001  # not a real text stream (e.g. under test capture): nothing to reconfigure  # nosec B110
            pass
    args = parse_cli(build_parser(), argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose_log else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
