"""The stock pack's scheduled work: the daily inventory check, the procurement pass (approved reorders are picked up within minutes), and the agents' morning brief.

The core's scheduler runs the clock; this says what runs when. Every job returns at once when the pack is switched off, so switching it off needs no restart.
"""
from __future__ import annotations

import logging
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)
NAME = "stock_procurement"
INVENTORY_JOB = "inventory_check"
PROCUREMENT_JOB = "procurement_check"
BRIEF_JOB = "agent_brief"


class StockJobs:
    def __init__(self, scheduler: Any) -> None:
        self.scheduler = scheduler

    @property
    def app(self) -> Any:
        return self.scheduler.app

    @property
    def sched(self) -> Any:
        return self.scheduler.sched

    def _on(self) -> bool:
        return self.app.packs.is_enabled(NAME)

    # ---------------------------------------------------------------- jobs
    def _inventory_job(self) -> None:
        if not self._on():
            return
        try:
            report = self.app.inventory.run()
            if report.failed:
                log.error("inventory run %s FAILED, nothing was checked: %s", report.run_id, "; ".join(report.errors))
            else:
                log.info("inventory run %s: checked=%d new_alerts=%d errors=%d", report.run_id, report.checked, report.new_alerts, len(report.errors))
        except Exception:  # noqa: BLE001
            log.exception("inventory run failed")

    def _procurement_job(self) -> None:
        if not self._on():
            return
        try:
            report = self.app.procurement.run()
            if report.proposed or report.manual or report.errors:
                log.info("procurement run %s: proposed=%d manual=%d errors=%d", report.run_id, report.proposed, report.manual, len(report.errors))
        except Exception:  # noqa: BLE001
            log.exception("procurement run failed")

    def _brief_job(self) -> None:
        """The inventory agent's morning brief (only when the agents are on and a time is set). It can take minutes on a laptop; max_instances=1 keeps one at a time."""
        if not self._on():
            return
        try:
            svc = self.app.agents
            if not svc.enabled:
                return
            res = svc.brief("scheduler")
            log.info("agent brief: source=%s status=%s", res.get("source"), res.get("status"))
            if svc.settings.send_brief and res.get("text"):
                svc.tell_approvers(res["text"])
            svc.log.prune(svc.settings.keep_traces_days)
        except Exception:  # noqa: BLE001
            log.exception("agent brief failed")

    def _sync_brief_job(self) -> None:
        cfg = self.app.settings.agents()
        cron = cfg.daily_brief_cron.strip() if cfg.enabled and self._on() else ""
        job = self.sched.get_job(BRIEF_JOB)
        if not cron:
            if job:
                self.sched.remove_job(BRIEF_JOB)
            return
        trigger = CronTrigger.from_crontab(cron, timezone=ZoneInfo(self.app.settings.system().timezone))
        if job:
            self.sched.reschedule_job(BRIEF_JOB, trigger=trigger)
        else:
            self.sched.add_job(self._brief_job, trigger, id=BRIEF_JOB, replace_existing=True)
        log.info("the agents' stock brief runs on schedule %r", cron)

    def _trigger(self) -> CronTrigger:
        s = self.app.settings.system()
        return CronTrigger.from_crontab(s.check_cron, timezone=ZoneInfo(s.timezone))

    # ---------------------------------------------------------------- what the core's scheduler calls
    def add_jobs(self) -> None:
        self.sched.add_job(self._inventory_job, self._trigger(), id=INVENTORY_JOB, replace_existing=True)
        # Approved reorders are picked up within minutes, not at the next daily stock check.
        self.sched.add_job(self._procurement_job, "interval", minutes=3, id=PROCUREMENT_JOB, replace_existing=True)

    def after_start(self) -> None:
        self._sync_brief_job()

    def reschedule(self) -> None:
        """An administrator changed a setting: takes effect immediately, no restart."""
        if self.sched.get_job(INVENTORY_JOB):
            self.sched.reschedule_job(INVENTORY_JOB, trigger=self._trigger())
            log.info("inventory schedule is now %r", self.app.settings.system().check_cron)
        self._sync_brief_job()
