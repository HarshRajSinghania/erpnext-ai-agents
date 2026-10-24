"""Background jobs: approval housekeeping and Telegram polling, plus whatever scheduled work the packs that are switched on add (the stock pack: the daily inventory check, the
procurement pass, the agents' morning brief)."""
from __future__ import annotations

import logging
import threading
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from .approvals.channels.telegram import TelegramChannel
from .approvals.inbound import process_inbound
from .bootstrap import App

log = logging.getLogger(__name__)
MAINTENANCE_JOB = "approval_maintenance"


class AgentScheduler:
    def __init__(self, app: App) -> None:
        self.app = app
        tz = ZoneInfo(app.settings.system().timezone)
        self.sched = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._providers: list[Any] = []              # what the packs add: objects with add_jobs(), after_start() and reschedule(); see add_jobs()
        for name in app.packs.enabled_names():       # each pack that is switched on says what it wants scheduled
            pack = app.packs.loaded.get(name)
            if pack is not None:
                pack.jobs(self)

    # ---------------------------------------------------------------- what a pack adds
    def add_jobs(self, provider: Any) -> None:
        """A pack's scheduled work: an object with `add_jobs()` (registers its jobs with `self.sched`, called before the scheduler starts), `after_start()` (anything that needs
        the scheduler running) and `reschedule()` (an administrator changed a setting). Its methods are also offered as the scheduler's own (`scheduler._brief_job`)."""
        self._providers.append(provider)

    def __getattr__(self, name: str) -> Any:
        # only reached when the scheduler has no such attribute: look in what the packs added (class attributes, so two objects can never ask each other forever)
        if not name.startswith("__"):
            for provider in self.__dict__.get("_providers", []):
                if hasattr(type(provider), name):
                    return getattr(provider, name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    # ---------------------------------------------------------------- jobs
    def _maintenance_job(self) -> None:
        try:
            svc = self.app.approvals
            expired, reminded, retried = svc.expire_due(), svc.remind_due(), svc.retry_failed_deliveries()
            if expired or reminded or retried:
                log.info("approvals: expired=%d reminded=%d redelivered=%d", len(expired), len(reminded), retried)
        except Exception:  # noqa: BLE001
            log.exception("approval maintenance failed")

    def reschedule(self) -> None:
        """Called when an admin changes the schedule - takes effect immediately, no restart."""
        for provider in self._providers:
            provider.reschedule()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        for provider in self._providers:
            provider.add_jobs()
        self.sched.add_job(self._maintenance_job, "interval", minutes=10, id=MAINTENANCE_JOB, replace_existing=True)
        self.sched.start()
        for provider in self._providers:
            provider.after_start()
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
