"""How the user wants things explained (learner.py, W6-1): the user's preference (simple or technical,
hints first), kept until changed and across a restart, set by the user or by the set_level tool, and told to every
model that writes for the user as the same one line. Nothing filters or rewrites an answer for it."""
import asyncio
import json
import random
from types import SimpleNamespace

from orchestrator import learner
from orchestrator.app import App
from orchestrator.arena import Arena, arm_label
from orchestrator.brain import context
from orchestrator.brain.registry import ToolContext
from orchestrator.brain.state import Conversation
from orchestrator.commands import COMMANDS
from orchestrator.day import DayStore
from orchestrator.enrich import Enricher
from orchestrator.paths import Paths
from orchestrator.research import ResearchManager
from research_worker import researcher as researcher_module
from research_worker.researcher import Researcher
from tests.fakes import Hub as BaseHub, fast
from tests.test_asked import fast_brain
from tests.test_golden import GoldenAgent

TECHNICAL = learner.line("technical", True)


def test_one_line_per_level_and_hints_add_to_it():
    simple, technical = learner.line("simple"), learner.line("technical")
    assert simple != technical and "simple, in everyday words" in simple and "technical, the proper" in technical
    assert learner.line() == simple == learner.line("nonsense")  # the default: closest to the prompts' own wording
    assert TECHNICAL.startswith(technical) and "Hints first" in TECHNICAL and "Hints first" not in technical
    assert learner.of(SimpleNamespace(level="technical", hints_first=True)) == TECHNICAL


# --- kept until changed ----------------------------------------------------------------------
def build(tmp_path) -> App:
    return App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)


def test_the_studios_choice_is_kept_and_comes_back_after_a_restart(tmp_path):
    app = build(tmp_path)
    assert (app.hub.settings.level, app.hub.settings.hints_first) == ("simple", False)
    reply = asyncio.run(COMMANDS["settings"](app, None, {"settings": {"level": "technical", "hintsFirst": True}}))
    assert reply["ok"] and reply["settings"]["level"] == "technical" and reply["settings"]["hintsFirst"] is True
    bad = asyncio.run(COMMANDS["settings"](app, None, {"settings": {"level": "expert"}}))
    assert not bad["ok"] and "simple or technical" in bad["error"]
    # The user's preferences file (day/prefs.py), not the day's journal, keeps them.
    kept = [(e["key"], e["value"]) for e in map(json.loads, app.day.user_prefs.path.read_text().splitlines())
            if e["type"] == "preference"]
    assert kept == [("level", "technical"), ("hintsFirst", True)]  # the refused one was not kept
    assert '"preference"' not in app.day.journal.path.read_text()

    again = build(tmp_path)  # a restart: the day is rebuilt from its journal
    assert (again.hub.settings.level, again.hub.settings.hints_first) == ("technical", True)
    assert learner.line("technical", True) in context.scene_table(again.brain.moment())[0]
    # Kept once, in its own line: the day's digest does not repeat it.
    assert "level" not in again.brain.day_digest() and "hintsFirst" not in again.brain.day_digest()


def test_set_level_changes_it_keeps_it_and_tells_the_studio(tmp_path):
    brain, hub, _, _ = fast_brain({})
    brain.services.day = DayStore(root=tmp_path / "sessions")
    published = []
    brain.services.publish_settings = lambda: published.append((hub.settings.level, hub.settings.hints_first))

    def run(args, words):
        async def go():
            started = brain.registry.start("set_level", args, ToolContext("voice", 1, 7, words))
            await started.task
            return started

        return asyncio.run(go())

    done = run({"level": "technical"}, "I'm a biology student, be technical")
    assert done.ok and "technical terms" in done.summary
    assert run({"hints_first": True}, "give me hints first").ok
    assert published == [("technical", False), ("technical", True)]
    assert brain.services.day.prefs == {"level": "technical", "hintsFirst": True}
    assert DayStore(root=tmp_path / "sessions").prefs == {"level": "technical", "hintsFirst": True}  # across a restart
    refused = run({"level": "expert"}, "be an expert")
    assert not refused.ok and hub.settings.level == "technical"
    assert not run({}, "hmm").ok
    assert run({"level": "simple", "hints_first": False}, "explain it more simply, and just tell me").ok
    assert (hub.settings.level, hub.settings.hints_first) == ("simple", False)


# --- the same line in every prompt ----------------------------------------------------------
def test_the_brain_the_fast_pass_and_the_voice_get_the_same_line():
    brain, hub, _, calls = fast_brain({"intent": "question", "needs_look": True, "answer": "", "actions": []})
    hub.settings.level, hub.settings.hints_first = "technical", True
    moment = brain.moment()
    assert moment.learner == TECHNICAL
    assert TECHNICAL in context.scene_table(moment)[0]
    assert TECHNICAL.split(": ", 1)[1] in brain.view_note()
    assert TECHNICAL.split(": ", 1)[1] in brain.voice_recap()
    asyncio.run(fast(brain, "what is that?"))
    assert TECHNICAL in calls[-1]
    # A change of level is a new view note for the voice.
    hub.settings.level = "simple"
    assert context.view_key(brain.moment()) != context.view_key(moment)


def test_a_long_view_note_keeps_who_leads_and_the_level():
    brain, hub, _, _ = fast_brain({})
    hub.settings.level, hub.settings.user_leads = "technical", True
    moment = brain.moment()
    long = context.Moment(**{**moment.__dict__, "seen": "x" * 5000})
    note = context.view_note(long)
    assert len(note) <= context.NOTE_CHARS and "technical, the proper scientific terms" in note
    assert context.LEAD_NOTE.strip() in note


def test_researchers_lessons_and_description_cards_get_the_same_line(tmp_path, monkeypatch):
    hub = BaseHub(frame=False, specimen="leaf", research="fake")
    hub.settings.level, hub.settings.hints_first = "technical", True
    manager = ResearchManager(hub, root=tmp_path)
    snapshot = manager._write_snapshot(tmp_path / "r1", None)
    assert snapshot["learner"] == TECHNICAL  # every researcher, description cards included (enrich starts researchers)

    r = Researcher({"rid": "r1", "dir": str(tmp_path / "r1"), "brief": "a lesson on stomata", "effort": "standard",
                    "deliverable": "lesson", "snapshot": snapshot}, lambda e: None, None)
    assert TECHNICAL in r.system_prompt()
    plain = Researcher({"rid": "r2", "dir": str(tmp_path / "r2"), "brief": "x", "effort": "quick"}, lambda e: None, None)
    assert "The user's level" not in plain.system_prompt()  # a snapshot from before levels: no line

    prompts = []

    async def write(prompt, _researcher, **_):
        prompts.append(prompt)
        return "<!doctype html><html><body><section id=\"q1\">Question 1</section></body></html>", "test-model"

    monkeypatch.setattr(researcher_module, "write_lesson", write)
    result, _ = asyncio.run(r.call_tool("make_lesson", {"title": "Stomata", "key_ideas": ["Pores let gas in"], "questions": [
        {"question": "What lets gas in?", "answer": "the pores", "hint": "look for the slits"}]}))
    assert result["ok"] and TECHNICAL in prompts[0]


def test_a_description_written_for_another_level_is_redone(tmp_path):
    hub = BaseHub(frame=False, specimen="leaf epidermis", research="fake", image="")
    enrich = Enricher(hub, ResearchManager(hub, root=tmp_path), lambda m: None, root=tmp_path)
    before_levels = {"status": "ready", "specimen": "leaf epidermis"}
    assert not enrich._stale(before_levels)  # made before levels: as the default, simple
    hub.settings.level = "technical"
    assert enrich._stale(before_levels)
    assert not enrich._stale({**before_levels, "level": "technical"})


# --- wording, compared blind ----------------------------------------------------------------
def test_the_arena_compares_levels_blind(tmp_path):
    hub = BaseHub()
    prompts: list[str] = []
    agent = GoldenAgent(hub, Conversation(store=DayStore(root=tmp_path / "sessions")), prompts)
    arena = Arena(hub, agent, rng=random.Random(1), votes_log=tmp_path / "votes.jsonl")
    choices = [c["id"] for c in arena.state()["choices"]["ask"]]
    simple, technical = f"{hub.settings.vision}/box/simple", f"{hub.settings.vision}/box/technical"
    assert simple in choices and technical in choices
    assert not arena.configure({"enabled": True, "ask": [f"{hub.settings.vision}/box", simple, technical]})
    trial = asyncio.run(arena.ask_trial("what is that?"))
    told = {next((lvl for lvl in learner.LEVELS if learner.line(lvl) in p), "none") for p in prompts}
    assert told == {"none", "simple", "technical"}  # no line (as before levels), and each level's line
    assert all("wording" not in json.dumps(a.blind()) for a in trial.arms.values())  # blind until the vote
    assert arm_label("ask", technical).endswith("technical wording")
    assert arena.configure({"ask": [f"{hub.settings.vision}/box/expert", simple]})  # not a level
