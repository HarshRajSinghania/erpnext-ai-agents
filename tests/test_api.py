"""HTTP API: roles, admin settings, dry-run, the approval inbox a mobile app would use."""
import pytest
from fastapi.testclient import TestClient

from erp_agents.api.app import create_app
from erp_agents.erp.mock import MockERP


@pytest.fixture
def ctx(make_app, clock):
    cfg = {"auth": {"tokens": [
        {"token": "owner-tok", "user": "owner", "roles": ["admin", "approver"]},
        {"token": "mgr-tok", "user": "manager", "roles": ["approver"]},
        {"token": "view-tok", "user": "accountant", "roles": ["viewer"]},
    ]}}
    app = make_app(erp=MockERP(today=clock().date()), cfg=cfg)
    changes = []
    client = TestClient(create_app(app, on_system_change=lambda: changes.append(1)))
    return app, client, changes


def H(token):
    return {"Authorization": f"Bearer {token}"}


def test_authentication_is_required_and_roles_are_enforced(ctx):
    app, c, _ = ctx
    assert c.get("/health").status_code == 200                                   # liveness is public
    assert c.get("/api/v1/settings/system").status_code == 401
    assert c.get("/api/v1/settings/system", headers=H("wrong")).status_code == 401
    assert c.get("/api/v1/settings/system", headers=H("view-tok")).status_code == 200
    assert c.put("/api/v1/settings/policy/global/*", json={"set": {"min_qty": 5}}, headers=H("view-tok")).status_code == 403
    assert c.put("/api/v1/settings/policy/global/*", json={"set": {"min_qty": 5}}, headers=H("mgr-tok")).status_code == 403
    assert c.get("/api/v1/approvals", headers=H("view-tok")).status_code == 403   # viewers cannot see approvals
    assert c.post("/api/v1/inventory/run", headers=H("mgr-tok")).status_code == 403


def test_admin_changes_thresholds_and_sees_effect_and_audit(ctx):
    app, c, _ = ctx
    r = c.put("/api/v1/settings/policy/group/Fasteners", json={"set": {"min_days_cover": 20, "min_qty": 500}}, headers=H("owner-tok"))
    assert r.status_code == 200 and r.json()["overrides"] == {"min_days_cover": 20, "min_qty": 500}
    eff = c.get("/api/v1/settings/policy/item/BOLT-M8?group=Fasteners", headers=H("view-tok")).json()
    assert eff["effective"]["min_qty"] == 500
    assert eff["sources"]["min_qty"] == "group 'Fasteners' override"
    log = c.get("/api/v1/settings/audit", headers=H("view-tok")).json()
    assert {e["field"] for e in log} == {"min_days_cover", "min_qty"} and log[0]["actor"] == "owner"
    # clearing restores inheritance
    c.put("/api/v1/settings/policy/group/Fasteners", json={"clear": ["min_qty"]}, headers=H("owner-tok"))
    assert "min_qty" not in c.get("/api/v1/settings/policy/group/Fasteners", headers=H("owner-tok")).json()["overrides"]


def test_invalid_settings_get_a_clear_422(ctx):
    _, c, _ = ctx
    r = c.put("/api/v1/settings/policy/global/*", json={"set": {"min_qty": -1}}, headers=H("owner-tok"))
    assert r.status_code == 422 and "min_qty" in r.json()["detail"]
    r = c.put("/api/v1/settings/policy/global/*", json={"set": {"bogus": 1}}, headers=H("owner-tok"))
    assert r.status_code == 422
    r = c.put("/api/v1/settings/policy/item/", json={"set": {"min_qty": 1}}, headers=H("owner-tok"))
    assert r.status_code in (404, 422)


def test_schedule_change_triggers_reschedule(ctx):
    _, c, changes = ctx
    r = c.put("/api/v1/settings/system", json={"set": {"check_cron": "30 5 * * *"}}, headers=H("owner-tok"))
    assert r.status_code == 200 and r.json()["check_cron"] == "30 5 * * *" and changes == [1]
    assert c.put("/api/v1/settings/system", json={"set": {"check_cron": "nonsense"}}, headers=H("owner-tok")).status_code == 422


def test_schema_endpoint_lets_an_admin_ui_build_its_forms(ctx):
    _, c, _ = ctx
    schema = c.get("/api/v1/settings/schema", headers=H("view-tok")).json()
    assert "min_qty" in schema["policy"] and "check_cron" in schema["system"]
    assert schema["policy"]["min_qty"]["description"]


def test_dry_run_default_then_live_run_and_analysis(ctx):
    app, c, _ = ctx
    dry = c.post("/api/v1/inventory/run", headers=H("owner-tok")).json()                  # dry_run defaults to true
    assert dry["dry_run"] is True and dry["new_alerts"] == 6
    assert c.get("/api/v1/approvals", headers=H("owner-tok")).json() == []
    live = c.post("/api/v1/inventory/run?dry_run=false", headers=H("owner-tok")).json()
    assert live["new_alerts"] == 6
    a = c.get("/api/v1/inventory/analysis/PAINT-RED", headers=H("view-tok")).json()
    assert a["analysis"]["status"] == "EARLY_WARNING" and a["analysis"]["drivers"]
    assert c.get("/api/v1/inventory/analysis/NOPE", headers=H("view-tok")).status_code == 404
    alerts = c.get("/api/v1/inventory/alerts?status=awaiting", headers=H("view-tok")).json()
    assert len(alerts) == 6 and "Why you are seeing this".upper() in alerts[0]["reasoning"].upper()


def test_items_overview_feeds_the_admin_screen(ctx):
    _, c, _ = ctx
    assert c.get("/api/v1/inventory/items").status_code == 401
    rows = c.get("/api/v1/inventory/items", headers=H("view-tok")).json()
    assert len(rows) == 10 and rows[0]["severity_rank"] >= rows[-1]["severity_rank"]      # worst first
    bolt = next(r for r in rows if r["item_code"] == "BOLT-M8")
    assert bolt["on_hand"] is not None and bolt["uom"] and bolt["item_overrides"] == {}
    c.put("/api/v1/settings/policy/item/BOLT-M8", json={"set": {"min_qty": 123}}, headers=H("owner-tok"))
    bolt = next(r for r in c.get("/api/v1/inventory/items", headers=H("view-tok")).json() if r["item_code"] == "BOLT-M8")
    assert bolt["min_qty"] == 123 and bolt["item_overrides"] == {"min_qty": 123}
    c.put("/api/v1/settings/policy/item/BOLT-M8", json={"set": {"enabled": False}}, headers=H("owner-tok"))
    bolt = next(r for r in c.get("/api/v1/inventory/items", headers=H("view-tok")).json() if r["item_code"] == "BOLT-M8")
    assert bolt["status"] == "DISABLED"


def test_web_ui_is_served(ctx):
    _, c, _ = ctx
    r = c.get("/ui/")
    assert r.status_code == 200 and "Stock Assistant" in r.text
    assert c.get("/", follow_redirects=False).status_code in (302, 307)


def test_mobile_inbox_flow(ctx):
    """What a future mobile app does: list pending, open one, answer it."""
    app, c, _ = ctx
    c.post("/api/v1/inventory/run?dry_run=false", headers=H("owner-tok"))
    pending = c.get("/api/v1/approvals", headers=H("mgr-tok")).json()
    assert len(pending) == 6
    bolt = next(p for p in pending if p["request"]["facts"]["item_code"] == "BOLT-M8")
    assert {o["action"] for o in bolt["request"]["options"]} == {"approve", "snooze", "dismiss", "details"}
    detail = c.get(f"/api/v1/approvals/{bolt['id']}", headers=H("mgr-tok")).json()
    assert detail["request"]["sections"] and detail["request"]["details"]

    info = c.post(f"/api/v1/approvals/{bolt['id']}/decision", json={"action": "details"}, headers=H("mgr-tok"))
    assert info.status_code == 200 and info.json()["status"] == "info"

    ok = c.post(f"/api/v1/approvals/{bolt['id']}/decision", json={"action": "approve"}, headers=H("mgr-tok"))
    assert ok.status_code == 200 and ok.json()["status"] == "decided"
    again = c.post(f"/api/v1/approvals/{bolt['id']}/decision", json={"action": "approve"}, headers=H("owner-tok"))
    assert again.status_code == 409 and "Already answered" in again.json()["detail"]
    assert c.post(f"/api/v1/approvals/{bolt['id']}/decision", json={"action": "zzz"}, headers=H("mgr-tok")).status_code in (409, 422)

    wos = c.get("/api/v1/work-orders", headers=H("view-tok")).json()
    assert len(wos) == 1 and wos[0]["payload"]["item_code"] == "BOLT-M8" and wos[0]["payload"]["approved_via"] == "app"


def test_snooze_with_custom_days_through_the_api(ctx):
    app, c, _ = ctx
    c.post("/api/v1/inventory/run?dry_run=false", headers=H("owner-tok"))
    paint = next(p for p in c.get("/api/v1/approvals", headers=H("mgr-tok")).json() if p["request"]["facts"]["item_code"] == "PAINT-RED")
    c.post(f"/api/v1/approvals/{paint['id']}/decision", json={"action": "snooze", "params": {"days": 10}}, headers=H("mgr-tok"))
    al = app.alerts.latest_for_item("PAINT-RED")
    assert al.status == "snoozed" and (al.snoozed_until - app.clock()).days == 10


def test_unknown_webhook_channel_is_404(ctx):
    _, c, _ = ctx
    assert c.post("/webhooks/telegram", json={}).status_code == 404       # not enabled in this config


def test_list_endpoints_bound_the_limit_parameter(make_app):
    from fastapi.testclient import TestClient
    from erp_agents.api.app import MAX_LIST_LIMIT, create_app
    from conftest import OWNER
    app = make_app(cfg={"auth": {"tokens": [{"token": "t" * 40, "user": "owner", "roles": ["admin", "approver"]}]}})
    client = TestClient(create_app(app))
    h = {"Authorization": "Bearer " + "t" * 40}
    paths = ["/api/v1/inventory/alerts", "/api/v1/inventory/runs", "/api/v1/procurement/proposals", "/api/v1/settings/audit", "/api/v1/approvals"]
    for path in paths:
        for bad in (-1, 0, MAX_LIST_LIMIT + 1, 999_999_999):
            assert client.get(path, params={"limit": bad}, headers=h).status_code == 422, (path, bad)
        for good in (1, MAX_LIST_LIMIT):
            assert client.get(path, params={"limit": good}, headers=h).status_code == 200, (path, good)
        assert client.get(path, headers=h).status_code == 200            # the default still works
