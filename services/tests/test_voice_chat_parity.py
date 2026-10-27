"""Voice and chat show a request the same way (the user's notes of 2026-10-11: by voice the chat showed no step
working, no photo card and no "Photo displayed", while typed the same request did).

For every capability the capability matrix plans for both chat and the voice (tests/test_capability_matrix.py
PLANS), its plan runs once typed, through the studio's own ask command, and once spoken, through the spoken-request
handling (SpokenRequests) with the app's own dialogue. Each step the request ran is in the chat exactly once, by its
own identity (typed: the answer line's steps or standalone rows; spoken: standalone rows, the spoken transcript being
its words), and a typed request and a spoken one show the same steps."""
from __future__ import annotations

import asyncio
import time

import pytest

from tests.test_capability_matrix import PLANS, groups, world  # noqa: F401  (the fixture)


def both() -> list[str]:
    asked = {}
    for capability, actor in groups():
        asked.setdefault(capability, set()).add(actor)
    return sorted(c for c, actors in asked.items() if {"chat", "voice"} <= actors and PLANS[c].expect == "ok")


def filed_steps(lines: list[dict]) -> dict[str, str]:
    """Each step the chat was given, by its action id: {actionId: tool}."""
    out: dict[str, str] = {}
    for line in lines:
        steps = line.get("actions") if line.get("type") == "chat" else [line] if line.get("type") == "chat-action" else []
        for step in steps or []:
            if isinstance(step, dict) and step.get("actionId") and step.get("tool"):
                out[step["actionId"]] = step["tool"]
    return out


async def typed(rig, plan, words: str) -> None:
    from orchestrator.commands import COMMANDS
    from tests.capability_rig import PLAN

    token = PLAN.set([{"tool": tool, "args": args} for tool, args in plan])
    try:
        await asyncio.wait_for(COMMANDS["ask"](rig.app, None, {"type": "ask", "text": words or "do it"}), 20.0)
    finally:
        PLAN.reset(token)


async def spoken(rig, plan, words: str) -> None:
    from orchestrator.brain import speech
    from orchestrator.brain.speech import SpokenRequests
    from tests.capability_rig import PLAN, TIMINGS, Voice

    token = PLAN.set([{"tool": tool, "args": args} for tool, args in plan])
    saved = {k: getattr(speech, k) for k in TIMINGS}
    for k, v in TIMINGS.items():
        setattr(speech, k, v)
    try:
        requests = SpokenRequests(rig.brain, rig.app.dialogue, Voice(), clock=time.monotonic)
        requests.heard(words or "do it", None)
        requests.handed_over()
        # A settled transcript, as the capability rig's voice: the hand-off is asked once its words are joined and hands
        # has started (a hand-off racing its words is test_speech's).
        if requests.pending is not None:
            await asyncio.wait_for(asyncio.shield(requests.pending), 20.0)
        await asyncio.wait_for(requests.handoff(words or "do it", "d1"), 20.0)
    finally:
        for k, v in saved.items():
            setattr(speech, k, v)
        PLAN.reset(token)


async def shown_by(rig, world, capability: str, actor: str) -> tuple[dict[str, str], dict[str, str]]:
    """(the steps the request ran, the steps the chat was given), each {actionId: tool}."""
    from tests import capability_rig as setups

    plan = PLANS[capability]
    await setups.prepare(rig, plan.setup)
    for step in plan.before:
        await rig.turn([(step[0], rig.fill(step[1]))], plan.words)
    steps = [(tool, rig.fill(args)) for tool, args in plan.steps]
    shown: list[dict] = []
    original = rig.app.dialogue.show

    def show(line, *args, **kwargs):
        shown.append(dict(line))
        return original(line, *args, **kwargs)

    rig.app.dialogue.show = show
    before = len(rig.runs)
    await (typed if actor == "chat" else spoken)(rig, steps, plan.words)
    await rig.settle([r for r, _ in rig.runs[before:]])
    ran = {r.occurrence.action_id: r.tool for r, channel in rig.runs[before:]
           if channel == ("chat" if actor == "chat" else "voice") and r.occurrence is not None}
    return ran, filed_steps(shown)


@pytest.mark.parametrize("capability", both())
def test_a_spoken_request_shows_its_steps_in_the_chat_as_a_typed_one(world, capability):  # noqa: F811
    from tests.capability_rig import Rig

    results = {}
    for actor in ("chat", "voice"):
        rig = Rig(world["root"] / actor)
        results[actor] = asyncio.run(shown_by(rig, world, capability, actor))
    for actor, (ran, filed) in results.items():
        missing = {aid: tool for aid, tool in ran.items() if aid not in filed}
        assert not missing, f"{actor}: steps the request ran that the chat never showed: {sorted(missing.values())}"
    assert sorted(results["chat"][1].values()) == sorted(results["voice"][1].values()), (
        f"typed showed {sorted(results['chat'][1].values())}, spoken showed {sorted(results['voice'][1].values())}")
