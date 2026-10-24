"""Does the agent's ERPNext service account have only the access the application needs?

The service account does the background reading and creates the drafts. If it holds dozens of roles (System Manager, ...), then a bug or a stolen key can do far more than this
app ever should. What the application needs is what the packs that are switched on declare in their manifests (`erp_permissions`: (DocType, permission) pairs); this module
finds the smallest set of the account's existing roles that covers it and names the surplus roles. It only READS; changing roles is done by a person in ERPNext (User > Roles).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

RISKY = {"System Manager", "Administrator", "Accounts Manager", "HR Manager", "Website Manager", "Script Manager"}


@dataclass
class AccessReport:
    user: str
    roles: list[str]
    missing: list[tuple[str, str]]
    keep: list[str]
    surplus: list[str]
    risky_held: list[str]


def _grants(rows: list[dict], flag: str) -> set[str]:
    return {r["role"] for r in rows if int(r.get("permlevel") or 0) == 0 and r.get(flag) and not r.get("if_owner") and r.get("role")}


def analyse(user: str, roles: list[str], perms_by_doctype: dict[str, list[dict]], needs: list[tuple[str, str]]) -> AccessReport:
    """Pure: which roles the account must keep to cover `needs` ((DocType, permission) pairs), which are surplus, and which of the surplus are high-risk."""
    held = set(roles)
    covering: dict[tuple[str, str], set[str]] = {}
    for dt, flag in needs:
        covering[(dt, flag)] = _grants(perms_by_doctype.get(dt, []), flag) & (held | {"All"})
    missing = [n for n, who in covering.items() if not who]
    todo = {n for n, who in covering.items() if who}
    keep: list[str] = []
    for pool in (sorted(held - RISKY), sorted(held & RISKY)):         # ordinary roles first; a high-risk one only if nothing else covers a need
        while todo:
            best = max(pool, key=lambda r: sum(1 for n in todo if r in covering[n]), default=None)
            gained = {n for n in todo if best in covering[n]} if best else set()
            if not gained:
                break
            keep.append(best)
            todo -= gained
    surplus = sorted(held - set(keep))
    return AccessReport(user, sorted(held), missing, keep, surplus, sorted(set(surplus) & RISKY))


def check_service_account(session: Any, base_url: str, needs: list[tuple[str, str]]) -> AccessReport:
    """Ask ERPNext (as the service account itself) what it holds and what each doctype's permission table says."""
    base = base_url.rstrip("/")
    user = session.get(f"{base}/api/method/frappe.auth.get_logged_user", timeout=30).json().get("message")
    doc = session.get(f"{base}/api/resource/User/{user}", timeout=30).json().get("data") or {}
    roles = sorted({r["role"] for r in doc.get("roles", []) if r.get("role")})
    if not roles:                       # an ordinary (lean) account does not get the Roles table with its own User record: list its role assignments instead
        r = session.get(f"{base}/api/method/frappe.client.get_list", timeout=30,
                        params={"doctype": "Has Role", "parent": "User", "fields": json.dumps(["role"]), "filters": json.dumps([["parent", "=", user]]), "limit_page_length": 500})
        roles = sorted({row["role"] for row in (r.json().get("message") or []) if row.get("role")})
    perms: dict[str, list[dict]] = {}
    for dt in sorted({d for d, _ in needs}):
        r = session.get(f"{base}/api/method/frappe.desk.form.load.getdoctype", params={"doctype": dt}, timeout=60)
        docs = r.json().get("docs") or []
        perms[dt] = (docs[0].get("permissions") if docs else None) or []
    return analyse(user, roles, perms, needs)
