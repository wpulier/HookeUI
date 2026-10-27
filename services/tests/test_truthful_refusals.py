"""A cloud call the app's own policy or the network refused is said in its own words (ticket 62, row 23; L7e).

The provider boundary returns what the real adapters return (vision.VisionResult with data None and the exception's
class and words as `error`), through the real Brain.quick, Brain.turn and answer admission. Seen in a fresh profile on
2026-10-20: the example's local-only policy became "something went wrong in the app", the monthly cap and a cut network
"the workspace notes planner did not answer; ask again".
"""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.dispatch import Refused, refusal
from orchestrator.brain.registry import ToolFailed
from orchestrator.ticket import issue, using
from orchestrator.vision import VisionResult
from research_worker.api_usage import LOCAL_ONLY
from tests.test_brain_dispatch_budget import MODEL, world
from tests.waiting import HANG_S

CAP = ("the monthly cap you set for cloud models is reached (about $0.01 of $0.00 this month, and this call may cost up "
       "to $0.02): Google wasn't asked. Raise or remove it in the spending panel")
ERRORS = {"local-only": f"ProviderRefused: {LOCAL_ONLY}", "capped": f"CapReached: {CAP}",
          "connection": "ConnectError: All connection attempts failed"}


def test_each_refusal_keeps_its_words_and_says_what_still_works():
    local, capped, connection = (refusal(ERRORS[k]) for k in ("local-only", "capped", "connection"))
    assert isinstance(local, ToolFailed) and local.said().startswith(LOCAL_ONLY) and not local.retry
    assert capped.said().startswith(CAP) and "spending panel" in capped.said() and not capped.retry
    assert "connection failed" in connection.said() and "internet connection" in connection.said() and connection.retry
    for refused in (local, capped, connection):
        assert "tools on this mac still work" in refused.said().lower()
        assert "did not answer" not in refused.said() and "something went wrong" not in refused.said()
    assert refusal("ValueError: unreadable reply") is None and refusal("") is None


def refusing(tmp_path, monkeypatch, error, *, fast=True):
    async def unused(entry):
        raise AssertionError("the scripted provider is replaced below")

    brain, hub, providers, logged = world(tmp_path, monkeypatch, unused, fast=fast)
    sent = []

    async def ask(images, prompt, max_output_tokens=1500):
        sent.append(prompt)  # what the real adapter returns when api_usage refused the call
        return VisionResult(None, 1, MODEL, error=error)
    brain.agent._visions[MODEL] = SimpleNamespace(ask=ask)
    return brain, hub, sent, logged


@pytest.mark.parametrize("kind", ["local-only", "capped"])
def test_a_policy_refusal_stops_the_quick_pass_in_its_own_words(tmp_path, monkeypatch, kind):
    async def scenario():
        brain, hub, sent, logged = refusing(tmp_path, monkeypatch, ERRORS[kind])
        request = issue(hub, "turn")
        try:
            with using(request):
                with pytest.raises(Refused) as raised:
                    await asyncio.wait_for(brain.quick("Which tool should I use to segment nuclei?"), HANG_S)
            assert raised.value.kind == kind and len(sent) == 1
            # Not noted as a fallback to the full look: the look would be refused the same way.
            assert not [r for r in logged if r.get("kind") == "fallback"]
        finally:
            request.seal()
    asyncio.run(scenario())


def test_a_follow_up_round_keeps_its_answer_when_refused(tmp_path, monkeypatch):
    async def scenario():
        brain, hub, sent, _ = refusing(tmp_path, monkeypatch, ERRORS["capped"])
        request = issue(hub, "turn")
        try:
            with using(request):
                assert await asyncio.wait_for(brain.quick("and the next one?", followup=True), HANG_S) is None
        finally:
            request.seal()
    asyncio.run(scenario())


def test_a_connection_failure_still_falls_back_to_the_full_look(tmp_path, monkeypatch):
    async def scenario():
        brain, hub, sent, logged = refusing(tmp_path, monkeypatch, ERRORS["connection"])
        request = issue(hub, "turn")
        try:
            with using(request):
                assert await asyncio.wait_for(brain.quick("Which tool should I use?"), HANG_S) is None
            assert [r["used"] for r in logged if r.get("kind") == "fallback"] == ["look"]
        finally:
            request.seal()
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["local-only", "capped", "connection"])
def test_the_answer_says_why_the_cloud_was_not_asked(tmp_path, monkeypatch, kind):
    async def scenario():
        brain, hub, sent, _ = refusing(tmp_path, monkeypatch, ERRORS[kind])
        with pytest.raises(ToolFailed) as raised:
            await asyncio.wait_for(brain.turn("Which tool should I use to segment nuclei?"), HANG_S)
        said = raised.value.said()
        assert "did not answer" not in said and "something went wrong" not in said
        assert {"local-only": LOCAL_ONLY, "capped": "monthly cap", "connection": "connection failed"}[kind] in said
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["local-only", "capped"])
def test_naming_a_click_says_the_refusal_in_its_own_words(tmp_path, kind):
    import cv2
    import numpy as np

    from orchestrator.agent import Agent
    from orchestrator.scene import SceneObject
    from orchestrator.tools import ToolError
    from tests.fakes import Hub

    hub = Hub()
    ok, jpeg = cv2.imencode(".jpg", np.full((720, 1280, 3), 200, np.uint8))
    hub.infos = {7: hub.latest_info}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg.tobytes()) if fid == 7 else None, is_fresh=lambda: True)
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[600, 300], [660, 300], [660, 360], [600, 360]]],
                              anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="click"))
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")

    async def ask(images, prompt, max_output_tokens=0):
        return VisionResult(None, 1, "stand-in", error=ERRORS[kind])  # what the real adapter keeps
    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    agent.log = lambda *a, **k: None
    agent.keep_image = lambda *a, **k: None
    with pytest.raises(ToolError) as raised:
        asyncio.run(agent.identify("o1"))
    said = raised.value.said()
    assert "did not answer" not in said and "ask again" not in said
    assert {"local-only": LOCAL_ONLY, "capped": "monthly cap"}[kind] in said


def test_a_refusal_that_ends_a_sentence_is_followed_by_one_never_by_a_semicolon():
    said = refusal(ERRORS["local-only"]).said()
    assert ".;" not in said and said == f"{LOCAL_ONLY} Tools on this Mac still work."
    assert refusal(ERRORS["capped"]).said().endswith("spending panel; tools on this Mac still work")
