"""The actual agent tools preserve scoped consent for capability setup in an isolated profile."""
import asyncio
import re

import pytest

from orchestrator.brain.registry import ToolContext, ToolFailed
from tests.capability_rig import Rig


@pytest.mark.parametrize("words,quoted", [("what can you do here?", "what"), ("yes", "yes")])
def test_quoted_words_without_a_matching_setup_offer_never_authorize_a_capability(tmp_path, words, quoted):
    rig = Rig(tmp_path)
    ctx = ToolContext("chat", 1, None, words)
    tool = rig.brain.registry.tools["turn_on"]
    with pytest.raises(ToolFailed):
        # This isolated profile has no OpenAI key: a bad consent check would create an unauthorized waiting intent,
        # without needing an actual download, installation or paid provider call to reproduce it.
        asyncio.run(tool.handler(ctx, {"job": "Talk with you", "asked_with": quoted}))
    assert rig.app.turn_on.wire() == {}


def offer_voice(rig):
    ctx = ToolContext("chat", 1, None, "can you talk with me?", asked=rig.app.day.here())
    said = asyncio.run(rig.brain.registry.tools["models"].handler(ctx, {"about": "Talk with you"}))
    found = re.search(r"Capability offer \((c\d+)\)", said)
    assert found is not None, said
    return found.group(1)


def test_an_offered_capability_can_be_confirmed_once_and_only_for_that_job(tmp_path):
    rig = Rig(tmp_path)
    pid = offer_voice(rig)
    tool = rig.brain.registry.tools["turn_on"]
    yes = ToolContext("chat", 2, None, "yes please", asked=rig.app.day.here())
    with pytest.raises(ToolFailed):
        asyncio.run(tool.handler(yes, {"job": "Find every cell", "confirms": pid, "asked_with": "yes please"}))
    said = asyncio.run(tool.handler(yes, {"job": "Talk with you", "confirms": pid, "asked_with": "yes please"}))
    assert "Turning on" in said and rig.app.turn_on.wire()["voice"]["state"] == "waiting"
    with pytest.raises(ToolFailed):
        asyncio.run(tool.handler(yes, {"job": "Talk with you", "confirms": pid, "asked_with": "yes please"}))


@pytest.mark.parametrize("reply", ["no", "don't turn it on", "cancel that"])
def test_a_refusal_to_a_real_offer_never_starts_setup(tmp_path, reply):
    rig = Rig(tmp_path)
    pid = offer_voice(rig)
    ctx = ToolContext("chat", 2, None, reply, asked=rig.app.day.here())
    with pytest.raises(ToolFailed):
        asyncio.run(rig.brain.registry.tools["turn_on"].handler(ctx, {
            "job": "Talk with you", "confirms": pid, "asked_with": reply}))
    assert not rig.app.turn_on.wire()


def test_an_offer_cannot_be_confirmed_from_a_different_session(tmp_path):
    rig = Rig(tmp_path)
    pid = offer_voice(rig)
    rig.app.day.new("Another specimen")
    ctx = ToolContext("chat", 2, None, "yes", asked=rig.app.day.here())
    with pytest.raises(ToolFailed):
        asyncio.run(rig.brain.registry.tools["turn_on"].handler(ctx, {
            "job": "Talk with you", "confirms": pid, "asked_with": "yes"}))
    assert not rig.app.turn_on.wire()


def test_an_explicit_named_setup_request_does_not_need_a_second_confirmation(tmp_path):
    rig = Rig(tmp_path)
    words = "please turn on Talk with you"
    ctx = ToolContext("chat", 1, None, words, asked=rig.app.day.here())
    said = asyncio.run(rig.brain.registry.tools["turn_on"].handler(ctx, {"job": "Talk with you", "asked_with": words}))
    assert "Turning on" in said and rig.app.turn_on.wire()["voice"]["state"] == "waiting"


@pytest.mark.parametrize('words', ['How do I turn on Talk with you?', 'What happens if I enable Talk with you?'])
def test_a_question_about_setup_is_not_an_instruction_to_start_it(tmp_path, words):
    rig = Rig(tmp_path)
    ctx = ToolContext('chat', 1, None, words, asked=rig.app.day.here())
    with pytest.raises(ToolFailed):
        asyncio.run(rig.brain.registry.tools['turn_on'].handler(ctx, {'job': 'Talk with you', 'asked_with': words}))
    assert not rig.app.turn_on.wire()


def test_partial_catalog_registry_keeps_queries_but_omits_unavailable_setup_effects(tmp_path):
    from orchestrator.brain.toolbox import Services, build

    app = Rig(tmp_path).app
    service = Services(hub=app.hub, capabilities=app.capabilities_wire)
    registry = build(service)
    assert 'models' in registry.tools and 'turn_on' not in registry.tools
    ctx = ToolContext('chat', 1, None, 'can you talk with me?', asked=app.day.here())
    said = asyncio.run(registry.tools['models'].handler(ctx, {'about': 'Talk with you'}))
    assert 'Capability offer' not in said and 'call turn_on' not in said
