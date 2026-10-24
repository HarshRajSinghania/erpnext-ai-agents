"""Writes test data into ERPNext through its REST API.

Design rules (this tool changes a live system, so it is careful):
  * Idempotent: every create first checks whether the record exists, so a run can be repeated or resumed.
  * Everything it creates is appended to a manifest file (doctype, name, time) so it can be listed and cleaned up.
  * A dry run changes nothing and reports what it would create.
  * Failures keep ERPNext's own message, so a rejected record says why.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import requests

log = logging.getLogger("testdata")


class WriterError(RuntimeError):
    pass


def erp_message(resp: Any) -> str:
    """The readable part of an ERPNext error response."""
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return (getattr(resp, "text", "") or "")[:300]
    msgs = data.get("_server_messages")
    if msgs:
        try:
            parts = [json.loads(m).get("message", "") for m in json.loads(msgs)]
            return " | ".join(p for p in parts if p)[:600]
        except Exception:  # noqa: BLE001
            return str(msgs)[:600]
    return str(data.get("exception") or data.get("message") or data)[:600]


class Writer:
    def __init__(self, session: Any, base_url: str, manifest: Optional[Path] = None, dry_run: bool = False, timeout: float = 120.0,
                 pause: float = 0.0) -> None:
        self.s, self.base, self.dry_run, self.timeout, self.pause = session, base_url.rstrip("/"), dry_run, timeout, pause
        self.manifest = manifest
        self.stats: Counter = Counter()
        if manifest and not dry_run:
            manifest.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ low level
    def _url(self, doctype: str, name: Optional[str] = None) -> str:
        return f"{self.base}/api/resource/{quote(doctype, safe='')}" + (f"/{quote(name, safe='')}" if name is not None else "")

    def _call(self, method: str, url: str, **kw: Any) -> Any:
        last: Optional[Exception] = None
        for attempt in range(4):
            try:
                resp = getattr(self.s, method)(url, timeout=self.timeout, **kw)
            except requests.RequestException as exc:
                last = exc
            else:
                if resp.status_code in (502, 503, 504) or (resp.status_code == 500 and "deadlock" in (resp.text or "").lower()):
                    last = WriterError(f"HTTP {resp.status_code}")
                else:
                    return resp
            time.sleep(1.5 * (attempt + 1))
        raise WriterError(f"{method.upper()} {url}: {last}")

    def exists(self, doctype: str, name: str) -> bool:
        r = self._call("get", self._url(doctype, name))
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            return False
        raise WriterError(f"checking {doctype} {name!r}: HTTP {r.status_code} {erp_message(r)}")

    def get(self, doctype: str, name: str) -> dict:
        r = self._call("get", self._url(doctype, name))
        if r.status_code != 200:
            raise WriterError(f"reading {doctype} {name!r}: HTTP {r.status_code} {erp_message(r)}")
        return r.json().get("data") or {}

    def find(self, doctype: str, filters: list, fields: Optional[list] = None, limit: int = 1000) -> list[dict]:
        out: list[dict] = []
        start = 0
        while True:
            r = self._call("get", self._url(doctype), params={"fields": json.dumps(fields or ["name"]), "filters": json.dumps(filters),
                                                              "limit_start": start, "limit_page_length": min(limit, 1000)})
            if r.status_code != 200:
                raise WriterError(f"listing {doctype}: HTTP {r.status_code} {erp_message(r)}")
            batch = r.json().get("data", [])
            out += batch
            if len(batch) < min(limit, 1000) or len(out) >= limit:
                return out[:limit]
            start += len(batch)

    def _record(self, doctype: str, name: str) -> None:
        if self.manifest and not self.dry_run:
            with self.manifest.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"doctype": doctype, "name": name, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")

    # ------------------------------------------------------------------ create
    def create(self, doctype: str, doc: dict) -> str:
        """Insert a document and return its name. No existence check: use ensure() for named records."""
        if self.dry_run:
            self.stats[f"{doctype}: would create"] += 1
            return doc.get("name") or doc.get("title") or doc.get("account_name") or "(new)"
        r = self._call("post", self._url(doctype), json={**doc, "doctype": doctype})
        if r.status_code >= 400:
            raise WriterError(f"creating {doctype} {self._label(doc)}: HTTP {r.status_code} {erp_message(r)}")
        name = (r.json().get("data") or {}).get("name", "")
        self.stats[f"{doctype}: created"] += 1
        self._record(doctype, name)
        if self.pause:
            time.sleep(self.pause)
        return name

    def create_many(self, doctype: str, docs: list[dict], batch: int = 50) -> int:
        """Insert many documents of one type in batches (one request per batch). The caller filters out existing ones."""
        if not docs:
            return 0
        if self.dry_run:
            self.stats[f"{doctype}: would create"] += len(docs)
            return len(docs)
        done = 0
        for i in range(0, len(docs), batch):
            chunk = [{**d, "doctype": doctype} for d in docs[i:i + batch]]
            r = self._call("post", f"{self.base}/api/method/frappe.client.insert_many", json={"docs": json.dumps(chunk)})
            if r.status_code >= 400:
                raise WriterError(f"creating {len(chunk)} x {doctype} (batch starting {self._label(chunk[0])}): HTTP {r.status_code} {erp_message(r)}")
            names = r.json().get("message") or []
            for n in names:
                self._record(doctype, n if isinstance(n, str) else str(n))
            done += len(chunk)
            self.stats[f"{doctype}: created"] += len(chunk)
        return done

    def ensure(self, doctype: str, name: str, doc: dict) -> str:
        """Create `doc` unless a record called `name` exists. Returns 'exists' or 'created'."""
        if not self.dry_run and self.exists(doctype, name):
            self.stats[f"{doctype}: existed"] += 1
            return "exists"
        self.create(doctype, doc)
        return "created"

    def ensure_one(self, doctype: str, filters: list, doc: dict) -> str:
        """Create `doc` unless a record matching `filters` exists (for documents with automatic names)."""
        if not self.dry_run and self.find(doctype, filters, limit=1):
            self.stats[f"{doctype}: existed"] += 1
            return "exists"
        self.create(doctype, doc)
        return "created"

    def update(self, doctype: str, name: str, changes: dict) -> None:
        if self.dry_run:
            self.stats[f"{doctype}: would update"] += 1
            return
        r = self._call("put", self._url(doctype, name), json=changes)
        if r.status_code >= 400:
            raise WriterError(f"updating {doctype} {name!r}: HTTP {r.status_code} {erp_message(r)}")
        self.stats[f"{doctype}: updated"] += 1

    def submit(self, doctype: str, name: str) -> None:
        """Submit a draft document (posts it to the ledgers)."""
        if self.dry_run:
            self.stats[f"{doctype}: would submit"] += 1
            return
        doc = self.get(doctype, name)
        if doc.get("docstatus") == 1:
            return
        r = self._call("post", f"{self.base}/api/method/frappe.client.submit", json={"doc": json.dumps(doc)})
        if r.status_code >= 400:
            raise WriterError(f"submitting {doctype} {name!r}: HTTP {r.status_code} {erp_message(r)}")
        self.stats[f"{doctype}: submitted"] += 1

    def report(self, report_name: str, filters: dict) -> dict:
        """Run an ERPNext report and return {'columns': [...], 'result': [...]}."""
        r = self._call("post", f"{self.base}/api/method/frappe.desk.query_report.run", json={"report_name": report_name, "filters": json.dumps(filters)})
        if r.status_code >= 400:
            raise WriterError(f"report {report_name}: HTTP {r.status_code} {erp_message(r)}")
        return r.json().get("message") or {}

    def call(self, method_path: str, **payload: Any) -> Any:
        """Call a whitelisted server method."""
        if self.dry_run:
            self.stats[f"method {method_path}: would call"] += 1
            return None
        r = self._call("post", f"{self.base}/api/method/{method_path}", json=payload)
        if r.status_code >= 400:
            raise WriterError(f"{method_path}: HTTP {r.status_code} {erp_message(r)}")
        return r.json().get("message")

    @staticmethod
    def _label(doc: dict) -> str:
        for k in ("name", "item_code", "supplier_name", "customer_name", "account_name", "company_name", "title", "warehouse_name", "cost_center_name", "label",
                  "bank_name", "year", "template_name", "group_name", "tax_category"):
            if doc.get(k):
                return repr(doc[k])
        return ""

    def summary(self) -> str:
        return "\n".join(f"  {k}: {v}" for k, v in sorted(self.stats.items())) or "  (nothing)"
