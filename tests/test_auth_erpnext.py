"""Signing in through ERPNext: people and permissions come from ERPNext, nothing is maintained twice."""
import json
import logging

import pytest
import requests
from fastapi.testclient import TestClient

from conftest import FakeClock
from erp_agents.core.api.app import create_app
from erp_agents.core.auth.erpnext import CAP_DECIDE, CAP_LOOK, CAP_SETTINGS, ErpNextLogin, LoginError, Verification, derive_caps
from erp_agents.core.auth.sessions import LoginThrottle
from erp_agents.core.erp.mock import MockERP

ITEM_PERMS = [{"role": "Stock User", "permlevel": 0, "read": 1}, {"role": "Purchase User", "permlevel": 0, "read": 1, "create": 0},
              {"role": "Purchase Manager", "permlevel": 0, "read": 1}, {"role": "Accounts User", "permlevel": 0, "read": 1},
              {"role": "Item Manager", "permlevel": 0, "read": 1, "create": 1}]
ORDER_PERMS = [{"role": "Purchase User", "permlevel": 0, "read": 1, "create": 1}, {"role": "Purchase Manager", "permlevel": 0, "read": 1, "create": 1},
               {"role": "Stock User", "permlevel": 0, "read": 1}, {"role": "Accounts User", "permlevel": 1, "read": 1, "create": 1}]


# ------------------------------------------------------------------ what ERPNext's permission table means here
def caps(user, roles, item=ITEM_PERMS, order=ORDER_PERMS):
    return derive_caps(user, set(roles), item, order)


def test_a_stock_user_can_look_but_not_decide():
    assert caps("a@x.com", ["Stock User"]) == {CAP_LOOK}


def test_a_purchaser_needs_no_item_permission_to_be_let_in():
    no_item_rows = [{"role": "Stock User", "permlevel": 0, "read": 1}]                 # Purchase Manager is not in the Item table at all
    assert caps("p@x.com", ["Purchase Manager"], item=no_item_rows) == {CAP_LOOK, CAP_DECIDE}
    read_only_orders = [{"role": "Accounts User", "permlevel": 0, "read": 1}]
    assert caps("a@x.com", ["Accounts User"], item=[], order=read_only_orders) == {CAP_LOOK}


def test_whoever_may_create_purchase_orders_in_erpnext_may_decide_here():
    assert caps("p@x.com", ["Purchase Manager"]) == {CAP_LOOK, CAP_DECIDE}
    assert caps("p@x.com", ["Purchase User"]) == {CAP_LOOK, CAP_DECIDE}


def test_a_system_manager_may_change_settings_but_is_not_given_a_right_erpnext_did_not_give_them():
    assert caps("s@x.com", ["System Manager", "Stock User"]) == {CAP_LOOK, CAP_SETTINGS}                    # cannot approve an order
    assert caps("s@x.com", ["System Manager", "Purchase Manager"]) == {CAP_LOOK, CAP_DECIDE, CAP_SETTINGS}


def test_administrator_may_do_everything():
    assert caps("Administrator", []) == {CAP_LOOK, CAP_DECIDE, CAP_SETTINGS}


def test_no_matching_role_means_no_access():
    assert caps("n@x.com", ["Sales User"]) == frozenset()
    assert caps("n@x.com", []) == frozenset()


def test_rows_that_do_not_really_grant_it_are_ignored():
    assert caps("a@x.com", ["Accounts User"], order=ORDER_PERMS) == {CAP_LOOK}                              # permlevel 1 only
    owner_only = [{"role": "Stock User", "permlevel": 0, "read": 1, "if_owner": 1}]
    assert caps("a@x.com", ["Stock User"], item=owner_only, order=[]) == frozenset()                                    # only their own documents
    everyone = [{"role": "All", "permlevel": 0, "read": 1}]
    assert caps("a@x.com", [], item=everyone) == {CAP_LOOK}


# ------------------------------------------------------------------ a stand-in for ERPNext's login and user records
class Resp:
    def __init__(self, data=None, status=200):
        self._d, self.status_code, self.text = data if data is not None else {}, status, json.dumps(data or {})

    def json(self):
        return self._d


class Page:
    """An HTML answer (ERPNext's desk page)."""

    def __init__(self, text):
        self.status_code, self.text = 200, text

    def json(self):
        raise ValueError("not json")


class FakeErp:
    def __init__(self):
        self.boot_enabled = True          # ERPNext's desk page carries the person's roles and can_read / can_create
        self.expose_roles = False         # an ordinary user cannot see the Roles table of their own User record (true of the real ERPNext)
        self.users = {
            "priya@acme.com": dict(pwd="pw-priya", full_name="Priya Nair", roles=["Purchase Manager"], enabled=1, user_type="System User", mobile_no="919900000001"),
            "arjun@acme.com": dict(pwd="pw-arjun", full_name="Arjun Mehta", roles=["Stock User"], enabled=1, user_type="System User"),
            "alice@acme.com": dict(pwd="pw-alice", full_name="Alice Rao", roles=["System Manager", "Purchase Manager"], enabled=1, user_type="System User"),
            "sam@acme.com": dict(pwd="pw-sam", full_name="Sam Admin", roles=["System Manager"], enabled=1, user_type="System User"),
            "sunita@acme.com": dict(pwd="pw-sunita", full_name="Sunita Rao", roles=["Sales User"], enabled=1, user_type="System User"),
            "web@acme.com": dict(pwd="pw-web", full_name="Web Visitor", roles=["Stock User"], enabled=1, user_type="Website User"),
            "otp@acme.com": dict(pwd="pw-otp", full_name="Otto P", roles=["Purchase Manager"], enabled=1, user_type="System User"),
        }
        self.two_factor = {"otp@acme.com"}
        self.locked = set()
        self.down = False
        self.calls = []
        self.perm_reads = 0

    def session(self):
        return FakeSession(self)


class FakeSession:
    def __init__(self, erp):
        self.erp, self.user, self.tmp = erp, None, None

    def _guard(self):
        if self.erp.down:
            raise requests.ConnectionError("ERPNext is down")

    def post(self, url, json=None, timeout=None):
        self._guard()
        self.erp.calls.append(("POST", url))
        assert url.endswith("/api/method/login")
        if "tmp_id" in json:
            if json["tmp_id"] == self.tmp and json["otp"] == "123456":
                self.user, self.tmp = "otp@acme.com", None
                return Resp({"message": "Logged In"})
            return Resp({"message": "Invalid verification"}, 401)
        u = self.erp.users.get(json["usr"])
        if json["usr"] in self.erp.locked:
            return Resp({"message": "Your account has been locked"}, 401)
        if not u or u["pwd"] != json["pwd"]:
            return Resp({"message": "Invalid login credentials"}, 401)
        if json["usr"] in self.erp.two_factor:
            self.tmp = "tmp-1"
            return Resp({"verification": {"method": "OTP App", "prompt": "Enter the code from your authenticator app"}, "tmp_id": "tmp-1"})
        self.user = json["usr"]
        return Resp({"message": "Logged In", "full_name": u["full_name"]})

    def get(self, url, params=None, timeout=None):
        self._guard()
        self.erp.calls.append(("GET", url))
        if url.endswith("/api/method/frappe.auth.get_logged_user"):
            ok = self.user and self.user in self.erp.users and self.erp.users[self.user]["enabled"]
            return Resp({"message": self.user if ok else "Guest"})
        if url.endswith("/api/method/logout"):
            self.user = None
            return Resp({})
        if url.endswith("/app"):
            u = self.erp.users.get(self.user)
            if not u:
                return Page("<html>login</html>")
            if not self.erp.boot_enabled:
                return Page("<html>desk</html>")
            def table_allows(rows, flag):
                return any(r.get("permlevel", 0) == 0 and r.get(flag) and r["role"] in u["roles"] for r in rows)
            can_read = [d for d, rows in (("Item", ITEM_PERMS), ("Purchase Order", ORDER_PERMS)) if table_allows(rows, "read")]
            can_create = [d for d, rows in (("Item", ITEM_PERMS), ("Purchase Order", ORDER_PERMS)) if table_allows(rows, "create")]
            boot = {"user": {"name": self.user, "roles": [*u["roles"], "All", "Guest", "Desk User"], "can_read": can_read, "can_create": can_create}, "lang": "en"}
            return Page(f'<script>frappe.csrf_token = "csrf-{self.user}-0123456789";</script><script>frappe.boot = {json.dumps(boot)};</script>')
        if "/api/resource/User/" in url:
            u = self.erp.users.get(self.user)
            if not u:
                return Resp({}, 403)
            return Resp({"data": {"name": self.user, "enabled": u["enabled"], "full_name": u["full_name"], "user_type": u["user_type"],
                                  "mobile_no": u.get("mobile_no"), "roles": [{"role": r} for r in u["roles"]] if self.erp.expose_roles else []}})
        if url.endswith("getdoctype"):
            self.erp.perm_reads += 1
            rows = ITEM_PERMS if params["doctype"] == "Item" else ORDER_PERMS
            return Resp({"docs": [{"name": params["doctype"], "permissions": rows}]})
        raise AssertionError(url)


def provider(erp, **kw):
    return ErpNextLogin("http://erp.local", session_factory=erp.session, **kw)


def test_signing_in_returns_who_they_are_and_what_erpnext_lets_them_do():
    erp = FakeErp()
    _, ident = provider(erp).start("priya@acme.com", "pw-priya")
    assert (ident.user, ident.full_name, ident.level) == ("priya@acme.com", "Priya Nair", "decide")
    assert ident.caps == {CAP_LOOK, CAP_DECIDE} and ident.roles == ("Purchase Manager",) and ident.mobile == "919900000001"
    a = ident.actor()
    assert a.has_role("viewer") and a.has_role("approver") and not a.has_role("admin") and a.via == "erpnext"


def test_a_settings_person_who_may_not_create_orders_cannot_approve_one():
    erp = FakeErp()
    a = provider(erp).start("sam@acme.com", "pw-sam")[1].actor()
    assert a.has_role("admin") and a.has_role("viewer") and not a.has_role("approver")                       # no implied ladder
    b = provider(erp).start("alice@acme.com", "pw-alice")[1].actor()
    assert b.has_role("admin") and b.has_role("approver")


@pytest.mark.parametrize("user,pw,code", [("priya@acme.com", "wrong", "invalid"), ("nobody@acme.com", "x", "invalid"), ("sunita@acme.com", "pw-sunita", "no_access"),
                                          ("web@acme.com", "pw-web", "no_access")])
def test_sign_in_failures_have_a_clear_code(user, pw, code):
    with pytest.raises(LoginError) as e:
        provider(FakeErp()).start(user, pw)
    assert e.value.code == code


def test_a_locked_account_and_an_unreachable_erpnext_are_told_apart():
    erp = FakeErp()
    erp.locked.add("priya@acme.com")
    with pytest.raises(LoginError) as e:
        provider(erp).start("priya@acme.com", "pw-priya")
    assert e.value.code == "locked"
    erp.down = True
    with pytest.raises(LoginError) as e:
        provider(erp).start("priya@acme.com", "pw-priya")
    assert e.value.code == "unreachable"


def test_two_step_verification():
    erp = FakeErp()
    p = provider(erp)
    with pytest.raises(Verification) as v:
        p.start("otp@acme.com", "pw-otp")
    assert v.value.tmp_id == "tmp-1" and "authenticator" in v.value.prompt
    with pytest.raises(LoginError) as e:
        p.verify(v.value.session, "tmp-1", "000000")
    assert e.value.code == "verification_failed"
    assert p.verify(v.value.session, "tmp-1", "123456").user == "otp@acme.com"


def test_the_permission_table_is_read_once_not_on_every_sign_in():
    erp = FakeErp()
    erp.boot_enabled, erp.expose_roles = False, True                   # an ERPNext whose desk page does not carry it: the older route
    p = provider(erp)
    p.start("priya@acme.com", "pw-priya")
    first = erp.perm_reads
    p.start("arjun@acme.com", "pw-arjun")
    assert erp.perm_reads == first == 2


# ------------------------------------------------------------------ through the API
@pytest.fixture
def world(make_app, clock):
    erp = FakeErp()
    app = make_app(erp=MockERP(today=clock().date()), cfg={"auth": {"tokens": [{"token": "k" * 40, "user": "emergency", "roles": ["viewer", "approver", "admin"]}]}})
    app.config["auth"].pop("access_keys", None)                       # the shipped default (local only), not the test fixture's convenience
    app.login = ErpNextLogin("http://erp.local", session_factory=erp.session)
    from erp_agents.core.auth.sessions import SessionStore
    app.clock_s = FakeClock2()
    app.sessions = SessionStore(app.login, clock=app.clock_s, recheck_seconds=900, ttl_seconds=12 * 3600)
    app.throttle = LoginThrottle(limit=3, window=300, clock=app.clock_s)
    return app, erp, TestClient(create_app(app))


class FakeClock2:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def sign_in(client, user, pw):
    r = client.post("/api/v1/auth/login", json={"username": user, "password": pw})
    return r


def hdr(token):
    return {"Authorization": "Bearer " + token}


def test_login_gives_a_token_that_works_for_the_persons_level(world):
    app, erp, c = world
    r = sign_in(c, "priya@acme.com", "pw-priya")
    assert r.status_code == 200 and r.json()["level"] == "decide" and r.json()["full_name"] == "Priya Nair"
    t = r.json()["token"]
    assert "pw-priya" not in r.text
    assert c.get("/api/v1/inventory/runs", headers=hdr(t)).status_code == 200                        # look
    assert c.get("/api/v1/approvals", headers=hdr(t)).status_code == 200                             # decide
    assert c.put("/api/v1/settings/policy/global/*", json={"set": {"min_days_cover": 9}}, headers=hdr(t)).status_code == 403
    me = c.get("/api/v1/auth/me", headers=hdr(t)).json()
    assert me["user"] == "priya@acme.com" and me["roles"] == ["Purchase Manager"] and me["caps"] == ["decide", "look"]


def test_a_person_who_may_only_look_cannot_decide_or_change_anything(world):
    app, erp, c = world
    t = sign_in(c, "arjun@acme.com", "pw-arjun").json()["token"]
    assert c.get("/api/v1/inventory/alerts", headers=hdr(t)).status_code == 200
    assert c.get("/api/v1/approvals", headers=hdr(t)).status_code == 403
    assert c.put("/api/v1/settings/system", json={"set": {"max_reminders": 1}}, headers=hdr(t)).status_code == 403


def test_settings_changes_and_decisions_are_recorded_under_the_erpnext_user(world):
    app, erp, c = world
    t = sign_in(c, "alice@acme.com", "pw-alice").json()["token"]
    assert c.put("/api/v1/settings/policy/global/*", json={"set": {"min_days_cover": 9}}, headers=hdr(t)).status_code == 200
    audit = c.get("/api/v1/settings/audit", headers=hdr(t)).json()
    assert audit and audit[0]["actor"] == "alice@acme.com"
    app.inventory.run(item_codes=["BOLT-M8"])
    approval = app.approvals.list("pending")[0]
    assert c.post(f"/api/v1/approvals/{approval.id}/decision", json={"action": "approve"}, headers=hdr(t)).status_code == 200
    assert app.approvals.get(approval.id).decision.actor_id == "alice@acme.com"


def test_errors_tell_the_screen_what_to_say(world):
    app, erp, c = world
    r = sign_in(c, "priya@acme.com", "nope")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid"
    r = sign_in(c, "sunita@acme.com", "pw-sunita")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "no_access"
    erp.down = True
    r = sign_in(c, "priya@acme.com", "pw-priya")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "unreachable"


def test_wrong_passwords_are_throttled_and_the_right_one_is_not_tried_while_blocked(world):
    app, erp, c = world
    for _ in range(3):
        assert sign_in(c, "priya@acme.com", "nope").status_code == 401
    attempts_before = len([x for x in erp.calls if x[0] == "POST"])
    r = sign_in(c, "priya@acme.com", "pw-priya")
    assert r.status_code == 429 and r.json()["detail"]["code"] == "throttled"
    assert len([x for x in erp.calls if x[0] == "POST"]) == attempts_before                          # ERPNext was not even asked
    app.clock_s.advance(301)
    assert sign_in(c, "priya@acme.com", "pw-priya").status_code == 200


def test_the_verification_step_through_the_api(world):
    app, erp, c = world
    r = sign_in(c, "otp@acme.com", "pw-otp")
    assert r.status_code == 200 and r.json()["status"] == "verification" and "token" not in r.json()
    tmp = r.json()["tmp_id"]
    bad = c.post("/api/v1/auth/verify", json={"tmp_id": tmp, "otp": "000000"})
    assert bad.status_code == 401 and bad.json()["detail"]["code"] == "verification_failed"
    ok = c.post("/api/v1/auth/verify", json={"tmp_id": tmp, "otp": "123456"})
    assert ok.status_code == 200 and ok.json()["user"] == "otp@acme.com" and c.get("/api/v1/approvals", headers=hdr(ok.json()["token"])).status_code == 200


def test_signing_out_ends_the_session(world):
    app, erp, c = world
    t = sign_in(c, "priya@acme.com", "pw-priya").json()["token"]
    assert c.post("/api/v1/auth/logout", headers=hdr(t)).status_code == 200
    assert c.get("/api/v1/inventory/runs", headers=hdr(t)).status_code == 401


def test_a_removed_role_or_disabled_user_loses_access_at_the_next_recheck(world):
    app, erp, c = world
    t = sign_in(c, "priya@acme.com", "pw-priya").json()["token"]
    erp.users["priya@acme.com"]["roles"] = ["Sales User"]
    assert c.get("/api/v1/approvals", headers=hdr(t)).status_code == 200                              # not asked again yet
    app.clock_s.advance(16 * 60)
    app.login._perms.clear()
    assert c.get("/api/v1/approvals", headers=hdr(t)).status_code == 401
    t2 = sign_in(c, "alice@acme.com", "pw-alice").json()["token"]
    erp.users["alice@acme.com"]["enabled"] = 0
    app.clock_s.advance(16 * 60)
    assert c.get("/api/v1/inventory/runs", headers=hdr(t2)).status_code == 401


def test_a_short_erpnext_outage_does_not_sign_everybody_out(world):
    app, erp, c = world
    t = sign_in(c, "priya@acme.com", "pw-priya").json()["token"]
    erp.down = True
    app.clock_s.advance(16 * 60)
    assert c.get("/api/v1/inventory/runs", headers=hdr(t)).status_code == 200


def test_a_session_ends_after_its_lifetime(world):
    app, erp, c = world
    t = sign_in(c, "priya@acme.com", "pw-priya").json()["token"]
    app.clock_s.advance(13 * 3600)
    assert c.get("/api/v1/inventory/runs", headers=hdr(t)).status_code == 401


def test_the_emergency_access_key_still_works_and_is_reported_as_such(world):
    app, erp, _remote = world
    c = TestClient(create_app(app), base_url="http://127.0.0.1:8100", client=("127.0.0.1", 50000))        # the administrator, on the server machine
    k = "k" * 40
    assert c.get("/api/v1/approvals", headers=hdr(k)).status_code == 200
    me = c.get("/api/v1/auth/me", headers=hdr(k)).json()
    assert me["user"] == "emergency" and me["level"] == "settings" and me["access_key"] is True
    assert c.get("/api/v1/approvals", headers=hdr("not-a-token")).status_code == 401
    assert c.get("/api/v1/auth/mode").json() == {"erpnext": True, "access_key": True}


# ------------------------------------------------------------------ nobody but the administrator, on the server, uses an access key
K = "k" * 40


def test_an_ordinary_user_never_sees_or_can_use_the_access_key(world):
    app, erp, remote = world                                              # a request from another machine
    assert remote.get("/api/v1/approvals", headers=hdr(K)).status_code == 401
    assert remote.get("/api/v1/auth/mode").json() == {"erpnext": True, "access_key": False}      # the screen does not even offer it


def test_a_request_through_the_tunnel_is_not_the_server_machine_even_though_it_arrives_from_localhost(world):
    app, erp, _ = world
    local = TestClient(create_app(app), base_url="http://127.0.0.1:8100", client=("127.0.0.1", 50000))
    assert local.get("/api/v1/approvals", headers=hdr(K)).status_code == 200
    for tunnel_header in ({"Cf-Connecting-Ip": "203.0.113.9"}, {"X-Forwarded-For": "203.0.113.9"}, {"Cf-Ray": "abc"}):
        assert local.get("/api/v1/approvals", headers={**hdr(K), **tunnel_header}).status_code == 401, tunnel_header
        assert local.get("/api/v1/auth/mode", headers=tunnel_header).json()["access_key"] is False


def test_the_administrator_can_widen_or_close_the_access_key_route(world):
    app, erp, remote = world
    app.config["auth"]["access_keys"] = "anywhere"
    assert remote.get("/api/v1/approvals", headers=hdr(K)).status_code == 200
    app.config["auth"]["access_keys"] = "off"
    local = TestClient(create_app(app), base_url="http://127.0.0.1:8100", client=("127.0.0.1", 50000))
    assert local.get("/api/v1/approvals", headers=hdr(K)).status_code == 401


def test_a_signed_in_person_is_not_affected_by_the_access_key_rules(world):
    app, erp, remote = world
    t = sign_in(remote, "priya@acme.com", "pw-priya").json()["token"]
    assert remote.get("/api/v1/approvals", headers=hdr(t)).status_code == 200


def test_the_demo_without_erpnext_still_takes_its_keys_from_anywhere(make_app):
    app = make_app(cfg={"auth": {"tokens": [{"token": "d" * 40, "user": "demo", "roles": ["viewer"]}]}})
    assert TestClient(create_app(app)).get("/api/v1/inventory/runs", headers=hdr("d" * 40)).status_code == 200


def test_without_erpnext_the_login_endpoints_say_so(make_app):
    app = make_app()
    c = TestClient(create_app(app))
    assert c.get("/api/v1/auth/mode").json()["erpnext"] is False
    r = c.post("/api/v1/auth/login", json={"username": "a", "password": "b"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_enabled"


def test_passwords_never_reach_the_log(world, caplog):
    app, erp, c = world
    caplog.set_level(logging.DEBUG)
    sign_in(c, "priya@acme.com", "very-secret-pw")
    sign_in(c, "priya@acme.com", "pw-priya")
    assert "very-secret-pw" not in caplog.text and "pw-priya" not in caplog.text
    assert "failed sign-in for priya@acme.com" in caplog.text


def test_an_overlong_or_empty_login_is_a_clean_422(world):
    app, erp, c = world
    assert c.post("/api/v1/auth/login", json={"username": "", "password": "x"}).status_code == 422
    assert c.post("/api/v1/auth/login", json={"username": "a" * 500, "password": "x"}).status_code == 422
    assert c.post("/api/v1/auth/login", content=b"{bad", headers={"Content-Type": "application/json"}).status_code == 422


# ------------------------------------------------------------------ the service account should hold only what the app needs
def test_the_service_account_check_keeps_the_smallest_ordinary_set_and_names_risky_surplus_roles():
    from conftest import STOCK_NEEDS as NEEDS
    from erp_agents.core.auth.access import analyse
    perms = {}
    for dt, flag in NEEDS:
        perms.setdefault(dt, [])
    def grant(dt, flag, *roles):
        perms[dt] += [{"role": r, "permlevel": 0, flag: 1} for r in roles]
    for dt, flag in NEEDS:
        grant(dt, flag, "Purchase User")                              # one ordinary role covers everything...
    grant("Company", "read", "System Manager", "HR Manager")          # ...risky roles could cover some of it too
    r = analyse("agent@x.com", ["Purchase User", "System Manager", "HR Manager", "Sales User"], perms, NEEDS)
    assert r.keep == ["Purchase User"] and not r.missing
    assert r.surplus == ["HR Manager", "Sales User", "System Manager"] and r.risky_held == ["HR Manager", "System Manager"]


def test_the_service_account_check_reports_access_it_lacks_and_uses_a_risky_role_only_as_a_last_resort():
    from conftest import STOCK_NEEDS as NEEDS
    from erp_agents.core.auth.access import analyse
    perms = {dt: [] for dt, _ in NEEDS}
    for dt, flag in NEEDS:
        if (dt, flag) != ("Tax Rule", "read"):
            perms[dt].append({"role": "Stock User", "permlevel": 0, flag: 1})
    perms["Currency Exchange"] = [{"role": "Accounts Manager", "permlevel": 0, "read": 1}]
    r = analyse("agent@x.com", ["Stock User", "Accounts Manager"], perms, NEEDS)
    assert r.missing == [("Tax Rule", "read")]
    assert "Stock User" in r.keep


def test_an_unreachable_erpnext_is_reported_without_naming_the_internal_server(world, caplog):
    """Whoever asks (including a stranger) must not learn the internal ERPNext address from the error; the log keeps it for the administrator."""
    import logging
    app, erp, c = world
    erp.down = True
    with caplog.at_level(logging.WARNING):
        r = c.post("/api/v1/auth/login", json={"username": "priya@acme.com", "password": "pw-priya"})
    assert r.status_code == 503 and r.json()["detail"] == {"code": "unreachable", "message": "ERPNext cannot be reached right now."}
    assert "erp.local" not in r.text and "ERPNext is down" not in r.text and "ConnectionError" not in r.text
    assert any("ERPNext could not be reached" in rec.getMessage() and "ERPNext is down" in rec.getMessage() for rec in caplog.records)
