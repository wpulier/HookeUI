"""Which defaults really ran, and why calls did not return (live 2026-10-11: "am I even on the right model?"; nine Gemini
calls cut off at the app's own deadlines were recorded only as "Interrupted or failed"; and every restart said "Seeing
... can't run now (needs GEMINI_API_KEY)" because saved choices were judged before the keys were loaded)."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import fallbacks
from research_worker import api_usage


def test_a_providers_calls_are_kept_with_why_they_did_not_return():
    class RateLimited(Exception):
        status_code = 429

    async def go():
        async with api_usage.attempt("gemini-3.8-flash", provider="Google", area="Chat"):
            pass
        for exc in (asyncio.TimeoutError(), RateLimited("quota")):
            with pytest.raises(type(exc)):
                async with api_usage.attempt("gemini-3.8-flash", provider="Google", area="Chat"):
                    raise exc

    before = api_usage.recent().get("Google", {})
    asyncio.run(go())
    after = api_usage.recent()["Google"]
    assert after.get("ok", 0) - before.get("ok", 0) == 1
    assert after.get("cut-off", 0) - before.get("cut-off", 0) == 1
    assert after.get("rate-limit", 0) - before.get("rate-limit", 0) == 1


@pytest.mark.parametrize("exc,outcome", [
    (asyncio.CancelledError(), "cut-off"), (TimeoutError(), "cut-off"),
    (type("E", (Exception,), {"status_code": 401})("no"), "auth"),
    (type("E", (Exception,), {"status_code": 503})("busy"), "server"),
    (ConnectionResetError(), "connection"), (ValueError("bad json"), "failed"),
])
def test_why_a_call_did_not_return_is_read_from_the_error_itself(exc, outcome):
    assert api_usage.outcome_of(exc) == outcome


def test_exceptions_are_grouped_with_how_often_and_the_latest_why():
    clock = [100.0]
    record = fallbacks.Fallbacks(clock=lambda: clock[0])
    record.note("quick decisions", "Gemini 3.8 Flash", "the full look", "no answer within 15 s")
    clock[0] = 130.0
    record.note("quick decisions", "Gemini 3.8 Flash", "the full look", "no answer within 15 s")
    assert record.recent() == [{"role": "quick decisions", "default": "Gemini 3.8 Flash", "used": "the full look",
                                "times": 2, "why": "no answer within 15 s", "agoS": 0}]
    clock[0] = 130.0 + fallbacks.WINDOW_S + 1
    assert record.recent() == []  # only the last ten minutes


def test_a_quick_decision_that_does_not_come_is_noted_as_the_full_look_answering(monkeypatch):
    from orchestrator.brain import loop as loop_module
    from tests.test_sight import make_brain

    brain, hub, agent = make_brain({"intent": "question"}, {"answer": "A stoma.", "targets": []})
    monkeypatch.setattr(loop_module, "PLANNER_TIMEOUT_S", 0.01)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr("orchestrator.brain.fast.decide", slow)
    assert asyncio.run(brain.quick("what is that?")) is None
    noted = fallbacks.of(hub).recent()
    assert noted and noted[0]["used"] == "the full look" and "no answer within" in noted[0]["why"]


def test_a_restart_judges_saved_cloud_choices_with_the_keys_loaded(tmp_path, monkeypatch):
    """Saved choices are judged runnable after the keys are loaded, so a cloud model whose key is in the keys file is
    restored, never said to need its key."""
    from orchestrator.app import App
    from orchestrator.day import DayStore
    from orchestrator.paths import Paths

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    paths = Paths.under(tmp_path)
    paths.keys_file.parent.mkdir(parents=True, exist_ok=True)
    paths.keys_file.write_text("OPENAI_API_KEY=stand-in\n")
    day = DayStore(root=paths.sessions)
    day.set_pref("defaults", {"vision": {"option": "gpt-6.1-sol", "at": 1.0, "by": "user"}})
    app = App.build("http://127.0.0.1:1", paths, use_gpu=False)
    try:
        assert app.hub.settings.vision == "gpt-6.1-sol"
    finally:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_a_typed_turn_says_when_only_its_answer_is_left_to_write():
    """The chat's working line follows the request's state: once its steps have run, the turn says it is answering
    (live 2026-10-11: "Working..." stayed 18.7 s after the photo was on screen while the answer was written)."""
    from tests.test_sight import make_brain

    brain, hub, agent = make_brain({"intent": "request", "needs_look": False, "answer": "Selecting it.",
                                    "actions": [{"tool": "select", "args": {"object": "o1"}}]},
                                   {"answer": "A stoma.", "targets": []})
    from tests.test_brain import obj

    obj(hub, "stoma")
    asyncio.run(brain.turn("select that stoma", "chat"))
    assert {"type": "thinking", "on": True, "phase": "answering"} in hub.sent
