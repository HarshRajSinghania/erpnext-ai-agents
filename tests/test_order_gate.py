"""The order gate lives in the core and applies to every pack: a tool whose effect is "prepare" (it sends an approval request to people) is fenced whatever a pack's own gate says.

(1) a job that offers one must carry a gate, or it does not run; (2) it is offered only to a caller whose role the tool needs; (3) a call is refused unless that role is held, and at
most one such call is made per task, even if the pack's gate would allow it. The stock pack's own gate (that the person asked for an order, for what) is tested in test_agents.py.
"""
import pytest

pytest.importorskip("mcp", reason="the optional 'agents' extra is not installed")

from conftest import APPROVER, OWNER, VIEWER
from erp_agents.core.agents.runtime import AgentJob
from erp_agents.core.agents.service import Plan, fence_prepare_tools
from erp_agents.core.erp.mock import MockERP
from test_agents import done, say, use, with_model

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer"], "properties": {"answer": {"type": "string"}}}
PREPARE = "prepare_order_request"


@pytest.fixture
def world(make_app, clock):
    app = make_app(erp=MockERP(today=clock().date()))
    app.settings.update_section(OWNER, "agents", {"enabled": True})
    return app


def job(tools=("stock_status", PREPARE), gate=None):
    return AgentJob(agent="demo", task="ask", system="s", user="u", tools=tuple(tools), schema=SCHEMA, check=lambda d, ev: [], gate=gate, question="please order BOLT-M8")


def plan(j, actor):
    return Plan(j, actor, lambda o: {"status": "answered", "answer": (o.data or {}).get("answer", "")}, lambda o: {"status": "fallback", "answer": "rules"}, request_text="t", asked_by=actor.id)


def requests(app):
    return [r for r in app.approvals.list("pending", 50) if r.kind.startswith("procurement.")]


def test_a_job_that_offers_a_prepare_tool_without_a_gate_does_not_run(world):
    app = world
    llm = with_model(app, use(PREPARE, item_code="BOLT-M8", qty=500, reason="x"), done(), say({"answer": "ok"}))
    res = app.agents.run(plan(job(gate=None), APPROVER))
    assert res["status"] == "fallback" and "carries no gate" in res["note"]
    assert llm.calls == [] and requests(app) == []                                                    # the model was never asked, nothing was prepared


def test_a_pack_gate_that_lets_everything_through_cannot_get_a_second_call_past_the_core(world):
    app = world
    with_model(app, use(PREPARE, item_code="BOLT-M8", qty=500, reason="a"), use(PREPARE, item_code="BOLT-M8", qty=600, reason="b"), done(), say({"answer": "ok"}))
    llm = app.agents.client_factory(None)
    res = app.agents.run(plan(job(gate=lambda name, args, ev: None), APPROVER))
    assert res["status"] == "answered" and len(requests(app)) == 1
    assert any("only one prepare_order_request call can be made per question" in m["content"] for c in llm.calls for m in c["messages"] if m.get("role") == "tool")


def test_a_caller_without_the_role_is_not_offered_the_tool_and_cannot_use_it(world):
    app = world
    fenced = fence_prepare_tools(job(gate=lambda *a: None), app.toolkit, VIEWER)
    assert PREPARE not in fenced.tools and "stock_status" in fenced.tools
    assert PREPARE in fence_prepare_tools(job(gate=lambda *a: None), app.toolkit, APPROVER).tools
    llm = with_model(app, use(PREPARE, item_code="BOLT-M8", qty=500, reason="x"), done(), say({"answer": "ok"}))
    app.agents.run(plan(job(gate=lambda *a: None), VIEWER))
    assert PREPARE not in llm.calls[0]["tools"] and requests(app) == []
    gate = fence_prepare_tools(job(gate=lambda *a: None), app.toolkit, VIEWER).gate
    from erp_agents.core.agents.runtime import Evidence
    assert "needs the approver level" in gate(PREPARE, {}, Evidence())                                 # and a call that comes anyway is refused by the gate


def test_the_packs_own_gate_speaks_first(world):
    app = world
    llm = with_model(app, use(PREPARE, item_code="BOLT-M8", qty=500, reason="x"), done(), say({"answer": "ok"}))
    app.agents.run(plan(job(gate=lambda name, args, ev: "the person did not ask for this" if name == PREPARE else None), APPROVER))
    assert requests(app) == [] and any("the person did not ask for this" in m["content"] for c in llm.calls for m in c["messages"] if m.get("role") == "tool")


def test_a_job_with_read_tools_only_is_left_exactly_as_it_is(world):
    app = world
    j = job(tools=("stock_status", "stock_overview"), gate=None)
    assert fence_prepare_tools(j, app.toolkit, VIEWER) is j                                             # no gate needed when nothing can prepare anything


def test_a_tool_of_a_pack_that_is_switched_off_is_not_a_prepare_tool_to_fence(world):
    app = world
    app.settings.update_section(OWNER, "packs", {"enabled": {"stock_procurement": False}})
    assert fence_prepare_tools(job(gate=None), app.toolkit, APPROVER).tools == job(gate=None).tools      # unknown to the toolkit now: nothing to fence, and the call itself is refused
    assert "switched off" in app.toolkit.call(PREPARE, {"item_code": "BOLT-M8", "reason": "x"}, OWNER)["error"]
