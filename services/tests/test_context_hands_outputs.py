"""I6 Hands-before-handoff source receipts over real application boundaries.

Camera/model replies and studio transport are explicitly synthetic. Hands, Brain,
registered highlight, Screen acknowledgment, original ActionRunner late callback,
Conversation and logging-off DayStore journal/reload are real. No model quality,
actual browser paint or live source registration is certified by these fixtures.
"""
import asyncio
import copy

import pytest

from orchestrator.brain.registry import ToolContext
from orchestrator.brain.speech import SpokenRequests
from orchestrator.screen import Screen
from tests.test_brain import FakeVision
from tests.test_context_output_identity import _birth_world, _commit_reanchor
from tests.waiting import HANG_S


def _hands_world(tmp_path):
    brain, hub, sight, obj, root, at = _birth_world(tmp_path)
    hub.settings.fast = "gemini-3.5-flash"
    brain.agent.replies = [{"answer": "A later unrelated response.", "targets": [], "actions": []} for _ in range(6)]
    brain.agent.fast_model = FakeVision(fast={
        "intent": "request", "needs_look": False, "answer": "Highlighting the requested stoma.",
        "actions": [{"tool": "highlight", "args": {"things": ["s2"], "keep": True}}]},
        rewrite={"answer": "The requested work is still running.", "actions": []})
    brain.registry.tools["highlight"].inline_ms = 1
    speech = SpokenRequests(brain)
    return brain, hub, sight, obj, root, at, speech


class HeldStudio:
    """Synthetic studio transport around the actual Screen send/confirm protocol.

    A show command is withheld after highlight has published its output. Every
    acknowledgment carries that exact change identity, not a generic visible flag.
    """
    def __init__(self, hub, at, *, fail_send=False):
        self.messages, self.reports, self.changed = [], [], asyncio.Event()
        self.screen = hub.screen = Screen(broadcast=self.send, clock=lambda: at)
        self.fail_send = fail_send
        self.report(False)

    def send(self, message):
        self.messages.append(message)
        self.changed.set()
        if self.fail_send:
            raise RuntimeError("synthetic studio transport failed after outline publication")

    def report(self, visible, changes=()):
        self.reports.extend(changes)
        self.screen.report({"items": [{"id": "outlines", "kind": "outlines", "visible": visible}],
                            "viewport": [1280, 720], "visible": True,
                            "applied": list(self.reports), "seq": len(self.reports) + 1}, client="synthetic-studio")

    async def wait_for_commands(self, count):
        while len(self.messages) < count:
            self.changed.clear()
            await asyncio.wait_for(self.changed.wait(), timeout=HANG_S)
        assert all(message["id"] == "outlines" and message["verb"] == "show"
                   for message in self.messages[:count])

    def release(self, index):
        self.report(True, [self.messages[index]["change"]])


def _late_notes(store):
    return [turn for thread in store.threads.values() for turn in thread.turns
            if turn.role == "note" and turn.text.startswith("highlight finished:")]


def _reload(brain, root, at, *, day="2026-10-08"):
    from orchestrator.brain.state import Conversation
    from orchestrator.day import DayStore

    store = DayStore(root=root, day=day, clock=lambda: at)
    brain.conversation = brain.agent.conversation = Conversation(store=store)
    return store


async def _finish_runs(studio, runs):
    """Always release/cancel owned work, including a failed assertion or timeout."""
    for index in range(len(studio.messages)):
        studio.release(index)
    tasks = {task for run in runs for task in (run.task, run.sequence) if task is not None}
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)  # let already-scheduled original late callbacks finish


@pytest.mark.parametrize("sibling", [False, True], ids=["later-unrelated-request", "concurrent-original-occurrences"])
def test_hands_output_before_handoff_keeps_the_original_turn_after_late_reload(tmp_path, sibling):
    async def sequence():
        brain, hub, sight, obj, root, at, speech = _hands_world(tmp_path)
        studio = HeldStudio(hub, at)
        runs, completed, contexts = [], [], []
        reported = asyncio.Event()

        async def late(ctx, run):
            if run.answer:
                return  # answer update reuses the receipt identity; await the operation event itself
            completed.append(run)
            contexts.append(ctx)
            reported.set()

        brain.report_late = late
        try:
            original = await asyncio.wait_for(speech.hands("highlight the other stoma too"), timeout=HANG_S)
            runs.extend(original["runs"])
            await studio.wait_for_commands(1)  # actual output exists, display acknowledgment is held
            assert runs[0].ok is None and runs[0].outputs
            born = runs[0].outputs[0].wire()
            assert (born["id"], born["run"], born["frame"], born["epoch"]) == (obj.id, "run-highlight-A", 7, 0)
            first = await brain.turn("highlight the other stoma too", "voice", handled=original)
            assert first["actions"][0]["ok"] is None
            assert original["ctx"].turn_id == first["turnId"]
            original_occurrence = runs[0].occurrence
            expected = [(runs[0], first["turnId"], original["ctx"].asked.thread)]

            if sibling:
                second = await asyncio.wait_for(speech.hands("also highlight the same stoma again"), timeout=HANG_S)
                runs.extend(second["runs"])
                await studio.wait_for_commands(2)
                other = await brain.turn("also highlight the same stoma again", "voice", handled=second)
                assert other["actions"][0]["ok"] is None
                assert second["ctx"] is not original["ctx"]
                assert runs[1].occurrence != original_occurrence
                assert runs[1].step[1] is not runs[0].step[1]
                assert other["turnId"] != first["turnId"]
                expected.append((runs[1], other["turnId"], second["ctx"].asked.thread))

            # A newer user turn/context and mutable placement are both unsuitable
            # fallbacks for the still-held original outputs.
            brain.day.open_thread("Unrelated later conversation")
            brain.agent.fast_model.fast = {"intent": "question", "needs_look": False,
                                           "answer": "An unrelated reply.", "actions": []}
            unrelated = await brain.turn("A different question about preparing tomorrow's slide", "chat")
            assert unrelated["turnId"] not in [turn for _, turn, _ in expected]
            _commit_reanchor(hub, obj, 9, 1)
            sight.view = None

            for index, (run, _, _) in reversed(list(enumerate(expected))):
                reported.clear()
                studio.release(index)
                await asyncio.wait_for(reported.wait(), timeout=HANG_S)
                assert any(item is run for item in completed) and run.ok is True
                assert any(item is (original["ctx"] if index == 0 else second["ctx"]) for item in contexts)
            assert runs[0].occurrence == original_occurrence
            assert runs[0].outputs[0].wire() == born, "binding a receipt must not mutate the immutable producer output"

            store = _reload(brain, root, at)
            notes = _late_notes(store)
            assert len(notes) == len(expected)
            refs = [row for note in notes for row in note.refs.get("outputRefs", [])]
            assert {row["turn_id"] for row in refs} == {turn for _, turn, _ in expected}, refs
            for _, turn_id, thread in expected:
                matching = [note for note in notes if any(row.get("turn_id") == turn_id
                                                         for row in note.refs.get("outputRefs", []))]
                assert len(matching) == 1 and matching[0].thread == thread and matching[0].channel == "voice"
                assert matching[0].refs["outputRefs"] == [{"id": obj.id, "run": "run-highlight-A",
                    "frame": 7, "epoch": 0, "created_at": 1000.0, "turn_id": turn_id}]
            store.switch(original["ctx"].asked.thread)
            assert any(row["turn_id"] == first["turnId"] and row["frame"] == 7 and row["epoch"] == 0
                       for row in brain.moment().awareness["outputRefs"])
        finally:
            await _finish_runs(studio, runs)
            speech.close()

    asyncio.run(sequence())


def test_truly_unbound_original_output_stays_unknown_after_an_unrelated_turn(tmp_path):
    async def sequence():
        brain, hub, _, obj, root, at, speech = _hands_world(tmp_path)
        studio = HeldStudio(hub, at)
        reported, runs = asyncio.Event(), []

        async def late(ctx, run):
            reported.set()

        brain.report_late = late
        original = ToolContext("voice", 0, 7, "synthetic unbound operation", asked=brain.conversation.here(),
                               seen_view=copy.deepcopy(brain.sight.view))
        try:
            runs.extend(await brain.run_actions([("highlight", {"things": ["s2"], "keep": True})], original))
            await studio.wait_for_commands(1)
            brain.agent.fast_model.fast = {"intent": "question", "needs_look": False, "answer": "A reply.", "actions": []}
            unrelated = await brain.turn("An unrelated real user request", "chat")
            studio.release(0)
            await asyncio.wait_for(reported.wait(), timeout=HANG_S)
            assert runs[0].ok is True and unrelated["turnId"] > 0
            refs = [row for note in _late_notes(_reload(brain, root, at)) for row in note.refs.get("outputRefs", [])]
            assert refs == [{"id": obj.id, "run": "run-highlight-A", "frame": 7, "epoch": 0,
                            "created_at": 1000.0, "turn_id": None}]
        finally:
            await _finish_runs(studio, runs)
            speech.close()

    asyncio.run(sequence())


@pytest.mark.parametrize("terminal", ["pending", "canceled", "failed"])
def test_actual_published_hands_output_without_success_cannot_certify_a_late_receipt(tmp_path, terminal):
    async def sequence():
        brain, hub, _, _, root, at, speech = _hands_world(tmp_path)
        studio = HeldStudio(hub, at, fail_send=terminal == "failed")
        reported, runs = asyncio.Event(), []

        async def late(ctx, run):
            reported.set()

        brain.report_late = late
        try:
            original = await asyncio.wait_for(speech.hands("highlight the other stoma too"), timeout=HANG_S)
            runs.extend(original["runs"])
            await studio.wait_for_commands(1)
            assert runs[0].outputs, "the actual producer ran; failure is not fabricated by omitting its output"
            if terminal == "failed":
                assert runs[0].ok is False
            else:
                assert runs[0].ok is None
                assert not any(note.refs.get("outputRefs") for note in _late_notes(brain.day))
                if terminal == "canceled":
                    runs[0].task.cancel()
                    await asyncio.wait_for(reported.wait(), timeout=HANG_S)
                    assert runs[0].ok is not True
            store = _reload(brain, root, at)
            assert not any(note.refs.get("outputRefs") for note in _late_notes(store))
            assert brain.moment().awareness["outputRefs"] == []
        finally:
            await _finish_runs(studio, runs)
            speech.close()

    asyncio.run(sequence())


def test_bound_hands_late_output_from_previous_session_does_not_certify_the_new_one(tmp_path):
    """Re-scoped to sessions (ADR 0017: midnight does nothing; a new session is the boundary)."""
    async def sequence():
        brain, hub, _, _, root, at, speech = _hands_world(tmp_path)
        studio = HeldStudio(hub, at)
        reported, runs = asyncio.Event(), []

        async def late(ctx, run):
            reported.set()

        brain.report_late = late
        try:
            original = await asyncio.wait_for(speech.hands("highlight the other stoma too"), timeout=HANG_S)
            runs.extend(original["runs"])
            await studio.wait_for_commands(1)
            result = await brain.turn("highlight the other stoma too", "voice", handled=original)
            assert original["ctx"].turn_id == result["turnId"]
            generation = original["ctx"].asked.generation
            sid = brain.day.new()
            assert sid != original["ctx"].asked.session and not brain.day.current(generation)
            studio.release(0)
            await asyncio.wait_for(reported.wait(), timeout=HANG_S)
            store = _reload(brain, root, at, day=sid)
            assert not any(note.refs.get("outputRefs") for note in _late_notes(store))
            assert brain.moment().awareness["outputRefs"] == []
        finally:
            await _finish_runs(studio, runs)
            speech.close()

    asyncio.run(sequence())
