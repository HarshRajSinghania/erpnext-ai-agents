"""Background jobs: the daily inventory check, approval housekeeping, and Telegram polling."""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .approvals.channels.telegram import TelegramChannel
from .approvals.inbound import process_inbound
from .bootstrap import App

log = logging.getLogger(__name__)
INVENTORY_JOB = "inventory_check"
MAINTENANCE_JOB = "approval_maintenance"
PROCUREMENT_JOB = "procurement_check"
BRIEF_JOB = "agent_brief"


class AgentScheduler:
    def __init__(self, app: App) -> None:
        self.app = app
        tz = ZoneInfo(app.settings.system().timezone)
        self.sched = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ---------------------------------------------------------------- jobs
    def _inventory_job(self) -> None:
        try:
            report = self.app.inventory.run()
            if report.failed:
                log.error("inventory run %s FAILED, nothing was checked: %s", report.run_id, "; ".join(report.errors))
            else:
                log.info("inventory run %s: checked=%d new_alerts=%d errors=%d", report.run_id, report.checked, report.new_alerts, len(report.errors))
        except Exception:  # noqa: BLE001
            log.exception("inventory run failed")

    def _procurement_job(self) -> None:
        try:
            report = self.app.procurement.run()
            if report.proposed or report.manual or report.errors:
                log.info("procurement run %s: proposed=%d manual=%d errors=%d", report.run_id, report.proposed, report.manual, len(report.errors))
        except Exception:  # noqa: BLE001
            log.exception("procurement run failed")

    def _maintenance_job(self) -> None:
        try:
            svc = self.app.approvals
            expired, reminded, retried = svc.expire_due(), svc.remind_due(), svc.retry_failed_deliveries()
            if expired or reminded or retried:
                log.info("approvals: expired=%d reminded=%d redelivered=%d", len(expired), len(reminded), retried)
        except Exception:  # noqa: BLE001
            log.exception("approval maintenance failed")

    def _brief_job(self) -> None:
        """The inventory agent's morning brief (only when the agents are on and a time is set). It can take minutes on a laptop; max_instances=1 keeps one at a time."""
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
        cron = cfg.daily_brief_cron.strip() if cfg.enabled else ""
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

    def reschedule(self) -> None:
        """Called when an admin changes the schedule - takes effect immediately, no restart."""
        if self.sched.get_job(INVENTORY_JOB):
            self.sched.reschedule_job(INVENTORY_JOB, trigger=self._trigger())
            log.info("inventory schedule is now %r", self.app.settings.system().check_cron)
        self._sync_brief_job()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.sched.add_job(self._inventory_job, self._trigger(), id=INVENTORY_JOB, replace_existing=True)
        self.sched.add_job(self._maintenance_job, "interval", minutes=10, id=MAINTENANCE_JOB, replace_existing=True)
        # Approved reorders are picked up within minutes, not at the next daily stock check.
        self.sched.add_job(self._procurement_job, "interval", minutes=3, id=PROCUREMENT_JOB, replace_existing=True)
        self.sched.start()
        self._sync_brief_job()
        for name, ch in self.app.channels.items():
            if isinstance(ch, TelegramChannel) and ch.mode == "polling":
                t = threading.Thread(target=self._poll_telegram, args=(ch,), name="telegram-poller", daemon=True)
                t.start()
                self._threads.append(t)

    def shutdown(self) -> None:
        self._stop.set()
        if self.sched.running:
            self.sched.shutdown(wait=False)

    def _poll_telegram(self, ch: TelegramChannel) -> None:
        try:
            ch.delete_webhook()      # polling and webhooks are mutually exclusive in Telegram
        except Exception as exc:  # noqa: BLE001
            log.warning("could not clear Telegram webhook: %s", exc)
        backoff = 2.0
        while not self._stop.is_set():
            try:
                for event in ch.poll_once(timeout=20):
                    process_inbound(event, self.app.router, self.app.approvals, self.app.conversation)
                backoff = 2.0
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram polling error: %s (retry in %.0fs)", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
