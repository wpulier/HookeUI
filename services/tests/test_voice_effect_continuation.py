"""Voice camera planning observes completed fast effects: one owner per request.

Hands' steps that need no look run first and the look continues from their receipts. A look entered
before those steps ran was planned on a board they changed: it is dropped unused (refunded, never
executed) and replaced by one that knows them, so a correct fast plan is never thrown away (live
2026-10-10 20:33: hands' find(eye inside s1) was dropped and the look's zoom on "target 1" failed)."""
import asyncio

from tests.test_brain import obj
from tests.test_speech import spoken


def test_fast_steps_run_once_and_an_entered_look_is_replaced_by_one_that_knows_them(tmp_path):
    speech, brain, hub, agent, *_ = spoken(
        tmp_path,
        {"intent": "request", "needs_look": True, "answer": "Clearing and looking.",
         "actions": [{"tool": "clear", "args": {"what": "outlines"}}]},
        {"answer": "Marking the eye.", "targets": [{"label": "left eye"}], "actions": []},
    )
    old = obj(hub, "person")
    agent.replies.append({"answer": "Marking the eye.", "targets": [{"label": "left eye"}], "actions": []})  # the replacement
    original_ask = agent.fast_model.ask
    original_run = brain.run_actions
    ran = []

    async def observed_run(actions, ctx, numbers=None):
        ran.extend(name for name, _ in actions)
        return await original_run(actions, ctx, numbers)

    brain.run_actions = observed_run
    looks = []
    start = brain.start_look

    def track(*a, **k):
        look = start(*a, **k)
        looks.append(look)
        return look

    brain.start_look = track

    async def go():
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(images, prompt, **kwargs):
            if "only your notes" in prompt:
                entered.set()
                await release.wait()
            return await original_ask(images, prompt, **kwargs)

        agent.fast_model.ask = delayed
        hands = speech.hands("clear those and highlight his left eye")
        await entered.wait()
        handed = asyncio.create_task(speech.handoff("clear those and highlight his left eye", "d-eye"))
        await asyncio.sleep(0.02)
        release.set()
        try:
            await handed
            await hands
        finally:
            speech.close()

    asyncio.run(go())
    assert ran.count("clear") == 1  # hands' step ran once, and only once
    assert len(looks) == 2 and looks[0].used is False and looks[1].used is True
    assert looks[0].admission is looks[1].admission  # one request
    assert "cleared 1 outline" in looks[1].moment.history or any(
        "cleared 1 outline" in call["question"] for call in agent.calls)  # the look continued from the receipt
    assert old.id not in hub.scene.objects
    assert [o.label for o in hub.scene.objects.values()] == ["left eye"]
    assert not any(row.get("proposedActions") for row in agent.logged)  # no plan was thrown away


def test_voice_look_started_after_fast_effects_receives_their_receipts(tmp_path):
    speech, brain, hub, agent, *_ = spoken(
        tmp_path,
        {"intent": "request", "needs_look": True, "answer": "Clearing and looking.",
         "actions": [{"tool": "clear", "args": {"what": "outlines"}}]},
        {"answer": "Looking for the eye.", "targets": [], "actions": []},
    )
    old = obj(hub, "person")

    async def go():
        try:
            handled = await speech.hands("clear those and highlight his left eye")
            assert handled["look"] is not None
            await speech.handoff("clear those and highlight his left eye", "d-eye")
        finally:
            speech.close()

    asyncio.run(go())
    assert len(agent.calls) == 1
    assert old.id not in hub.scene.objects
    assert "cleared 1 outline" in agent.calls[0]["question"]
    assert f"{old.id} " not in agent.calls[0]["tools"][1]
