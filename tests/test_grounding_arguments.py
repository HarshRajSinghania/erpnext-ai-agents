"""The grounding check: the numbers in an answer come from the person's question or from the RESULTS of tool calls that succeeded, and from nowhere else.

Found in review (25 Oct 2026): Evidence.pool() also added the numbers in every call's ARGUMENTS, failed calls included. A model could type any number into a call the tools
rejected and then quote it as if a tool had returned it. Arguments never count now; a successful call's facts come back in its result, which these tests also pin down.
"""
import pytest

pytest.importorskip("mcp", reason="the optional 'agents' extra is not installed")

from conftest import OWNER, VIEWER
from erp_agents.assistant.verify import NOT_ENOUGH
from erp_agents.agents.runtime import Evidence, ToolRun, grounding_problems, substance_problems
from erp_agents.erp.mock import MockERP
from test_agents import done, say, use, with_model


def problems(text, ev):
    return grounding_problems([text], ev)


# ------------------------------------------------------------------ the pool itself
def test_numbers_from_the_question_are_known():
    assert problems("You asked for 500.", Evidence("order 500 of BOLT-M8")) == []


def test_numbers_from_a_successful_result_are_known():
    ev = Evidence("")
    ev.add(ToolRun("stock_status", {"item_code": "BOLT-M8"}, {"item_code": "BOLT-M8", "on_hand": 1250, "days": 12.3}, True))
    assert problems("BOLT-M8 has 1250 pcs, enough for 12 days.", ev) == []


def test_numbers_typed_into_a_failed_call_are_not_known():
    ev = Evidence("")
    ev.add(ToolRun("vendor_options", {"item_code": "NO-SUCH-ITEM", "qty": 987654}, {"error": "no item 'NO-SUCH-ITEM'"}, False))
    out = problems("The best quantity is 987654.", ev)
    assert out and "numbers that no tool returned: 987654" in out[0]
    assert 987654 not in ev.pool()


def test_a_failed_call_does_not_make_its_numbers_quotable_even_beside_successful_ones():
    ev = Evidence("")
    ev.add(ToolRun("stock_status", {"item_code": "BOLT-M8"}, {"item_code": "BOLT-M8", "on_hand": 1250}, True))
    ev.add(ToolRun("usage_history", {"item_code": "BOLT-M8", "weeks": 777}, {"error": "boom"}, False))
    assert problems("BOLT-M8 has 1250 pcs.", ev) == []
    assert "777" in problems("Look at the last 777 weeks.", ev)[0]


def test_a_number_the_model_put_in_a_successful_calls_arguments_is_not_known_unless_the_result_returns_it():
    ev = Evidence("")
    ev.add(ToolRun("stock_status", {"item_code": "BOLT-M8", "confidence": 4242}, {"on_hand": 1250}, True))
    assert "4242" in problems("Confidence is 4242.", ev)[0]


@pytest.mark.parametrize("tool,args,key", [("usage_history", {"item_code": "BOLT-M8", "weeks": 9}, "weeks"), ("vendor_options", {"item_code": "BOLT-M8", "qty": 640}, "qty")])
def test_every_argument_that_is_a_fact_comes_back_in_the_result_of_a_successful_call(make_app, clock, tool, args, key):
    """The reason it is safe to ignore arguments: the tools echo them. If a tool stopped doing so, a true statement would be rejected, and this test says why."""
    app = make_app(erp=MockERP(today=clock().date()))
    res = app.toolkit.call(tool, args, VIEWER)
    assert "error" not in res and res[key] == args[key]
    ev = Evidence("")
    ev.add(ToolRun(tool, args, res, True))
    assert problems(f"That is {args[key]}.", ev) == []


def test_stock_overviews_limit_is_reported_as_listed(make_app, clock):
    app = make_app(erp=MockERP(today=clock().date()))
    res = app.toolkit.call("stock_overview", {"limit": 3}, VIEWER)
    assert res["listed"] == min(3, res["needing_attention"])


def test_a_bare_figure_that_only_a_failed_call_contained_is_not_an_answer():
    ev = Evidence("")
    ev.add(ToolRun("stock_status", {"item_code": "X-1", "qty": 31337}, {"error": "no item"}, False))
    assert substance_problems("31337", ev)


# ------------------------------------------------------------------ through a whole task
@pytest.fixture
def world(make_app, clock):
    app = make_app(erp=MockERP(today=clock().date()))
    app.settings.update_section(OWNER, "agents", {"enabled": True})
    return app


def test_an_answer_built_on_a_rejected_call_is_refused_end_to_end(world):
    app = world
    with_model(app, use("vendor_options", item_code="NO-SUCH-ITEM", qty=987654), done(),
               say({"answerable": True, "answer": "Order 987654 pcs."}), say({"answerable": True, "answer": "Order 987654 pcs."}))
    res = app.agents.ask("Which vendor should we use for BOLT-M8?", VIEWER)
    assert res["status"] == "not_enough_information" and res["answer"] == NOT_ENOUGH and res["source"] == "rules"


def test_a_grounded_answer_after_a_rejected_call_is_still_accepted(world):
    app = world
    on_hand = app.toolkit.call("stock_status", {"item_code": "BOLT-M8"}, VIEWER)["on_hand"]
    with_model(app, use("stock_status", item_code="NO-SUCH-ITEM", extra_number=987654), use("stock_status", item_code="BOLT-M8"), done(),
               say({"answerable": True, "answer": f"BOLT-M8 has {on_hand} pcs on hand."}))
    res = app.agents.ask("How much BOLT-M8 do we have?", VIEWER)
    assert res["status"] == "answered" and str(on_hand) in res["answer"] and res["source"] == "model"


def test_a_number_from_the_question_is_still_accepted_end_to_end(world):
    app = world
    with_model(app, done(), say({"answerable": True, "answer": "You asked about 500 pcs of BOLT-M8."}))
    res = app.agents.ask("Is 500 pcs of BOLT-M8 too much?", VIEWER)
    assert res["status"] == "answered" and "500" in res["answer"]
