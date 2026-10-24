"""Command line for the test-data loader.

    python -m tools.testdata.cli list                 show the steps
    python -m tools.testdata.cli run --step company   run one step (or several: --step a --step b; or --all)
    python -m tools.testdata.cli run --all --dry-run  show what would be created, change nothing
    python -m tools.testdata.cli cleanup-list         list everything created so far (from the manifest)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from erp_agents.core.config import load_config
from erp_agents.core.erp.erpnext import ERPNextClient

from .steps import STEPS, Context
from .writer import Writer, WriterError

MANIFEST = Path(__file__).resolve().parent / "manifest.jsonl"


def build_context(dry_run: bool, config: str) -> Context:
    cfg = load_config(config)["erp"]
    if cfg.get("type") != "erpnext":
        raise SystemExit("The test data loader needs erp.type: erpnext in the config.")
    client = ERPNextClient(cfg["url"], cfg["api_key"], cfg["api_secret"])
    return Context(Writer(client.session, client.base_url, MANIFEST, dry_run=dry_run))


def run_year(args) -> int:
    from collections import Counter
    from datetime import date as _date

    from . import post, sim
    plan = sim.build_plan()
    by = Counter(e.kind for e in plan.events)
    print("Plan:", ", ".join(f"{k} {v}" for k, v in sorted(by.items())), "|", {k: round(v) for k, v in plan.stats.items()})
    if args.plan_only:
        return 0
    ctx = build_context(False, args.config)
    poster = post.Poster(ctx, plan, only=set(args.kinds.split(",")) if args.kinds else None)
    try:
        poster.run(until=_date.fromisoformat(args.until) if args.until else None, since=_date.fromisoformat(args.since) if args.since else None,
                   stop_on_error=not args.keep_going)
    except WriterError as exc:
        print(f"\nSTOPPED: {exc}\nEverything posted before this is kept; re-running continues from here.")
        return 1
    finally:
        print("\nSummary:\n" + ctx.w.summary())
        for k, msg in poster.failures[:30]:
            print("  !", k, msg[:300])
    return 1 if poster.failures else 0


def run_scenarios(args) -> int:
    from collections import Counter
    from datetime import date as _date

    from . import post, scenarios, steps
    only = {c for c in (args.only or "").split(",") if c} or None
    plan = scenarios.build_plan(only=only)
    by = Counter(e.kind for e in plan.events)
    print("Plan:", ", ".join(f"{k} {v}" for k, v in sorted(by.items())), "| total", len(plan.events))
    if args.plan_only:
        return 0
    ctx = build_context(False, args.config)
    items = [i for i in scenarios.items() if not only or i.code in only]
    if not args.no_items:
        steps.step_items(ctx, items)
        steps.step_prices(ctx, items)
    poster = post.Poster(ctx, plan, only=set(args.kinds.split(",")) if args.kinds else None, extra_items=scenarios.items())
    try:
        poster.run(until=_date.fromisoformat(args.until) if args.until else None, since=_date.fromisoformat(args.since) if args.since else None,
                   stop_on_error=not args.keep_going)
    except WriterError as exc:
        print(f"\nSTOPPED: {exc}\nEverything posted before this is kept; re-running continues from here.")
        return 1
    finally:
        print("\nSummary:\n" + ctx.w.summary())
        for k, msg in poster.failures[:30]:
            print("  !", k, msg[:300])
    return 1 if poster.failures else 0


def run_days(args) -> int:
    from datetime import date as _date, timedelta

    from . import days
    today = _date.fromisoformat(args.d2) if args.d2 else _date.today()
    d1 = _date.fromisoformat(args.d1) if args.d1 else today - timedelta(days=1)
    ctx = build_context(False, args.config)
    poster = days.Days(ctx, d1, today)
    print(f"Two business days: {d1.isoformat()} (yesterday) and {today.isoformat()} (today)")
    try:
        poster.run(only=[b for b in (args.only or "").split(",") if b] or None, keep_going=not args.stop_on_error)
    except WriterError as exc:
        print(f"\nSTOPPED: {exc}\nEverything posted before this is kept; re-running continues from here.")
        return 1
    finally:
        print("\nCreated in this run:")
        for k, v in sorted(poster.made.items()):
            print(f"  {k:<28} {v}")
        print(f"  {'TOTAL':<28} {sum(poster.made.values())}")
        for n in poster.notes:
            print("  -", n)
        for k, msg in poster.failures:
            print("  !", k, msg[:400])
    return 1 if poster.failures else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="testdata")
    p.add_argument("--config", default="config/settings.erpnext.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("--step", action="append", default=[])
    r.add_argument("--all", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("cleanup-list")
    y = sub.add_parser("year", help="plan and post the year of transactions")
    y.add_argument("--plan-only", action="store_true", help="show the plan, post nothing")
    y.add_argument("--since")
    y.add_argument("--until")
    y.add_argument("--kinds", help="comma separated, e.g. PO,PR,PI")
    y.add_argument("--keep-going", action="store_true", help="log failures and continue instead of stopping")
    sc = sub.add_parser("scenarios", help="ten materials with several vendors and customers each, and a history of purchases and sales with distinct demand patterns")
    sc.add_argument("--plan-only", action="store_true", help="show the plan, post nothing")
    sc.add_argument("--no-items", action="store_true", help="do not create the items and prices (they exist already)")
    sc.add_argument("--only", help="comma separated item codes, e.g. MET-0025,CHM-0025")
    sc.add_argument("--since")
    sc.add_argument("--until")
    sc.add_argument("--kinds", help="comma separated, e.g. PO,PR,PI")
    sc.add_argument("--keep-going", action="store_true", help="log failures and continue instead of stopping")
    d = sub.add_parser("days", help="two live business days (yesterday and today) of end-to-end cycles on top of the existing history")
    d.add_argument("--only", help="comma separated block names, e.g. rfq1,mr1,oldpo")
    d.add_argument("--d1")
    d.add_argument("--d2")
    d.add_argument("--stop-on-error", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.cmd == "list":
        for name, (fn, doc) in STEPS.items():
            print(f"  {name:<18} {doc}")
        return 0
    if args.cmd == "cleanup-list":
        if not MANIFEST.exists():
            print("nothing created yet")
            return 0
        rows = [json.loads(l) for l in MANIFEST.read_text(encoding="utf-8").splitlines() if l.strip()]
        from collections import Counter
        for k, v in sorted(Counter(r["doctype"] for r in rows).items()):
            print(f"  {k:<40} {v}")
        print(f"  total {len(rows)} records, manifest: {MANIFEST}")
        return 0

    if args.cmd == "year":
        return run_year(args)
    if args.cmd == "days":
        return run_days(args)
    if args.cmd == "scenarios":
        return run_scenarios(args)
    names = list(STEPS) if args.all else args.step
    unknown = [n for n in names if n not in STEPS]
    if not names or unknown:
        print("choose --all or --step NAME; known:", ", ".join(STEPS), ("| unknown: " + ", ".join(unknown)) if unknown else "")
        return 2
    ctx = build_context(args.dry_run, args.config)
    for name in names:
        fn, doc = STEPS[name]
        t = time.time()
        print(f"\n== {name}: {doc}" + ("  [DRY RUN]" if args.dry_run else ""))
        try:
            fn(ctx)
        except WriterError as exc:
            print(f"STOPPED in step '{name}': {exc}")
            print("Everything created before this point is kept (re-running is safe: existing records are skipped).")
            return 1
        print(f"   done in {time.time() - t:.0f}s")
    print("\nSummary:\n" + ctx.w.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
