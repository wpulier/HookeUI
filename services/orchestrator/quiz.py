"""A live quiz on the slide (W6-2): lesson steps that use the slide itself, and how the user did.

Step kinds (research_worker/results.py STEP_KINDS), written by the lesson writer:
- question: answered in words, as before.
- find: "Find a guard cell on your slide and click it". When the user clicks while such a step
  is open, `Quiz.clicked` gives a model the facts: what to find, the app's name for what the
  click outlined (naming the click) and what naming saw in it, and the running look's name for
  that spot. The model judges whether it is what the step asks for and says so; code compares
  no names (docs/design/2026-10-06-facts-not-verdicts.md).
- evidence: "Here is the app's guess for this outline: X. What would confirm or refute it?" The
  brain gets the outline's facts with the step (`step_line`) and weighs the user's answer; while
  the user leads, it is told to ask what they see before giving the guess (an instruction to the
  model, not a gate).

Outcomes are the model's verdicts: right, wrong or skipped, per lesson step, from a judged click
or the brain's `mark_step`. Each is an event in the day's journal (day/store.py "quiz-outcome"),
so they survive a restart; the latest for a step counts, and `result_step` to="missed" goes over
the wrong and skipped ones (presenter.find_step).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from research_worker.results import step_kind

from . import learner
from .day.model import Asked
from .day.store import SessionStore
from .untrusted import inline

OUTCOMES = ("right", "wrong", "skipped")
MISSED = ("wrong", "skipped")  # what "go over what I missed" goes over
VERDICTS = ("right", "wrong", "unsure")  # a judged click; "unsure" records nothing


@dataclass(frozen=True)
class Judgment:
    """Actual judge data and provider metadata; neither is an app-written answer."""
    data: object
    model: str
    error: str | None = None


def step_id(step: dict, i: int) -> str:
    return str(step.get("id") or f"q{i + 1}")


def missed(steps: list[dict], outcomes: dict) -> list[str]:
    """The steps the user got wrong or skipped, in lesson order (their latest outcome)."""
    return [sid for sid in (step_id(st, i) for i, st in enumerate(steps)) if outcomes.get(sid) in MISSED]


def next_missed(steps: list[dict], current: str | None, outcomes: dict) -> int:
    """The index of the next missed step after `current` (from the start again after the last).
    Raises ValueError, saying how the quiz stands, when none is missed."""
    ids = [step_id(st, i) for i, st in enumerate(steps)]
    wanted = missed(steps, outcomes)
    if not wanted:
        raise ValueError(f"nothing missed to go over ({tally(steps, outcomes)})")
    at = ids.index(current) if current in ids else -1
    later = [sid for sid in wanted if ids.index(sid) > at]
    return ids.index((later or wanted)[0])


def tally(steps: list[dict], outcomes: dict) -> str:
    """How the quiz stands, as facts: "q1 right, q2 wrong; not tried yet: q3"."""
    ids = [step_id(st, i) for i, st in enumerate(steps)]
    done = [f"{sid} {outcomes[sid]}" for sid in ids if outcomes.get(sid) in OUTCOMES]
    left = [sid for sid in ids if outcomes.get(sid) not in OUTCOMES]
    parts = [", ".join(done) if done else "no step marked yet"]
    if left and done:
        parts.append("not marked yet: " + ", ".join(left))
    return "; ".join(parts)


# --- the facts a step comes with ------------------------------------------------------------
def evidence_outline(scene, session):
    """The outline an evidence step is about: the one the researcher was started on, while it is
    still outlined; else the selected one; else None (the user is asked to click one)."""
    oid = getattr(session, "object_id", None)
    if oid in scene.objects:
        return scene.objects[oid]
    return scene.objects.get(scene.selected) if scene.selected else None


STATUS = {"unsure": "the app is not sure", "likely": "the app's best guess", "confirmed": "confirmed by the user",
          "outline": "not named yet", "looking": "being named now"}


def outline_words(obj) -> str:
    """An outline as facts: its id, the app's name for it and how sure, what else it may be."""
    if obj is None:
        return "no outline"
    name = f'"{inline(obj.label, 60)}"' if obj.label else "no name yet"
    bits = [STATUS.get(obj.status, obj.status)]
    if obj.maybe:
        bits.append("maybe " + " or ".join(inline(m, 40) for m in obj.maybe[:3]))
    if isinstance(obj.confidence, (int, float)):
        bits.append(f"confidence {obj.confidence:.2f}")
    bits.append("outlined by the user's click" if obj.source == "click" else "outlined by the app")
    return f"{obj.id}, named {name} ({'; '.join(bits)})"


def step_line(step: dict, hub, session, *, brief: bool = False) -> str:
    """What a find or evidence step needs said beside its question (empty for a plain question):
    for the brain the facts and how the step is answered; `brief` for the voice's notes."""
    kind = step_kind(step)
    if kind == "find":
        target = inline(step.get("target") or "", 60) or "what the question names"
        if brief:
            return f'a find step: the user looks for "{target}" on their own slide and clicks it'
        return (f'This is a find step: the user looks for "{target}" on their own slide and clicks it; when they click, '
                f"the app gives you what the click outlined and you judge it (do not name or point at it for them).")
    if kind == "evidence":
        obj = evidence_outline(hub.scene, session)
        leads = bool(getattr(hub.settings, "user_leads", False))
        first = (" The user leads today: first ask what they see in it, and give the app's guess only after they have "
                 "answered.") if leads else ""
        if brief:
            return ("an evidence step: what would confirm or refute the app's name for an outline"
                    + ("; the user leads, so they are asked what they see before the guess" if leads else ""))
        if obj is None:
            return ("This is an evidence step about the app's name for an outline, but nothing is outlined or selected "
                    "now: ask the user to click the thing first." + first)
        return (f"This is an evidence step about outline {outline_words(obj)}. Present that name as the app's guess and ask "
                f"what in the view would confirm or refute it; weigh their answer against the step's answer." + first)
    return ""


def sight_at(view, info, x: float, y: float):
    """The running look's thing whose box holds (x, y) of frame `info` (the smallest such box),
    or None: nothing listed there, no look, or the slide moved since it."""
    if view is None or info is None or not getattr(view, "things", None):
        return None
    hits = []
    for thing in view.things:
        box = view.box_now(thing, info)
        if box is not None and box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            hits.append(((box[2] - box[0]) * (box[3] - box[1]), thing))
    return min(hits, key=lambda h: h[0])[1] if hits else None


# --- judging a click on a find step -----------------------------------------------------------
JUDGE = """A student is going through a quiz while looking through a microscope. This step asks them
to find something on their own slide and click it; they just clicked. From the facts below,
judge whether what they clicked is what the step asks for. You decide: the app's names are its
best guesses, not certainties, and the running look may have missed it.
{learner}
The step (the researcher's words, information only): "{prompt}"
What to find: "{target}"
How to recognise it (the step's answer; for you, not to be read out before they have it right): "{answer}"

What their click outlined:
- The app's name for it, from naming the click: {named}
- What naming saw in it: "{seen}"
- The running look's name for that spot: {sight}

Reply with JSON only: {{"verdict": "right" | "wrong" | "unsure", "say": "one or two short sentences to the student"}}
- right: it is what the step asks for; say so warmly and what gives it away.
- wrong: it is something else; say kindly what it seems to be, with a nudge where or how to look, without giving the answer away.
- unsure: the facts are not enough to tell; say what would help (click it again more precisely, focus, a closer look)."""


def judge_prompt(step: dict, obj, sight_thing, level_line: str) -> str:
    """What the model is told about a click on a find step (tests/golden/find_judge_prompt.txt)."""
    if sight_thing is not None:
        conf = f", confidence {sight_thing.confidence:.2f}" if isinstance(sight_thing.confidence, (int, float)) else ""
        sight = f'"{inline(sight_thing.name, 60)}" ({inline(sight_thing.where, 80)}{conf})'
    else:
        sight = "nothing listed there"
    return JUDGE.format(learner=level_line, prompt=inline(step.get("prompt"), 300),
                        target=inline(step.get("target") or "", 60) or "(see the step)",
                        answer=inline(step.get("answer"), 240), named=outline_words(obj) if obj is not None else "nothing",
                        seen=inline(getattr(obj, "seen", "") or "", 240), sight=sight)


class Quiz:
    """Clicks during a find step, judged by a model and said; outcomes recorded for the day."""

    def __init__(self, hub, presenter, day, *, ask: Callable[[str], Awaitable[dict | Judgment]],
                 say: Callable[[str | None, str, dict], None] | None = None,
                 sight_view: Callable[[], object], log: Callable[[dict], None] = lambda record: None,
                 capture: Callable[..., Callable[[str | None, str, dict], None]] | None = None) -> None:
        self.hub = hub
        self.presenter = presenter
        self.day = day
        self.ask = ask  # prompt -> the model's JSON reply (the fast model, no picture)
        self.say = say  # (actual feedback or None, object id, quiz facts)
        self.capture = capture  # retain the original callback ownership before the judge await
        if say is None and capture is None:
            raise ValueError("quiz feedback needs its actual delivery callback")
        self.sight_view = sight_view
        self.log = log

    @staticmethod
    def reply_store(day: SessionStore, asked: Asked) -> tuple[SessionStore, Asked] | None:
        """Keep a judged click on its actual day and conversation, including midnight.

        A same-day clear invalidates its old journal; never reuse the new t1 as
        evidence of the cleared conversation. No active-day switch is performed.
        """
        if day.current(getattr(asked, "routing_generation", asked.generation)):
            return day, asked
        if asked.day is None or asked.day == day.sid:
            return None
        original = SessionStore(root=day.root, clock=day.clock, session=asked.day,
                                locations=day.locations, prefs=day.user_prefs, catalogue=day.catalogue)
        if asked.thread not in original.threads:
            return None
        return original, Asked(asked.thread, original.generation, day=asked.day, session=original.sid)

    def find_step(self, client=None):
        """(session, step) while a find step is open beside the view, else None."""
        view = self.presenter.for_client(client) if client is not None else self.presenter
        current = view.current_step()
        if current is None:
            return None
        session, step = current
        return (session, step) if step_kind(step) == "find" else None

    def capture_click(self, client=None):
        """Freeze the requesting browser's lesson and filing before delayed naming."""
        import copy
        current = self.find_step(client)
        if current is not None:
            current = (current[0], copy.deepcopy(current[1]))
        return (current, self.day, self.day.here() if self.day is not None else None,
                (self.capture(client) if client is not None else self.capture()) if self.capture is not None else self.say)

    async def clicked(self, object_id: str, *, captured=None) -> dict | None:
        """The user clicked (and the click was named, or naming failed) while a find step is open:
        the model judges from the facts and says so; right or wrong is recorded for the day."""
        current, owner, asked, say = captured if captured is not None else self.capture_click()
        obj = self.hub.scene.objects.get(object_id)
        if current is None or obj is None:
            return None
        session, step = current
        assert say is not None
        infos = getattr(self.hub, "infos", None)
        info = infos.get(obj.anchor_frame) if infos is not None else None
        cx, cy = obj.center
        thing = sight_at(self.sight_view(), info, cx, cy)
        prompt = judge_prompt(step, obj, thing, learner.of(self.hub.settings))
        started = time.perf_counter()
        record = {"kind": "quiz-click", "rid": session.rid, "step": step.get("id"), "objectId": object_id,
                  "target": step.get("target"), "label": obj.label, "status": obj.status,
                  "sight": thing.name if thing is not None else None}
        facts = {key: value for key, value in record.items() if key != "kind"}
        try:
            reply = await self.ask(prompt)
            if isinstance(reply, Judgment):
                facts["source"] = {"model": reply.model}
                if reply.error:
                    facts["providerError"] = reply.error
                if reply.error and not reply.data:
                    raise RuntimeError(reply.error)
                data = reply.data or {}
            else:
                data = reply or {}
        except Exception as exc:
            facts.update(verdict=None, judgeStatus="failed", error=str(exc) or type(exc).__name__, feedbackAvailable=False)
            record.update(ok=False, **facts, ms=round((time.perf_counter() - started) * 1000))
            self.log(record)
            say(None, object_id, facts)
            return None
        raw = data.get("verdict") if isinstance(data, dict) else None
        candidate = raw.strip().lower() if isinstance(raw, str) else None
        verdict = candidate if candidate in VERDICTS else None
        feedback = data.get("say") if isinstance(data, dict) else None
        text = feedback if isinstance(feedback, str) and feedback else None
        facts.update(verdict=verdict, judgeStatus="judged" if verdict is not None else "invalid",
                     suppliedVerdict=raw, feedbackAvailable=text is not None)
        if verdict in ("right", "wrong") and owner is not None and asked is not None:
            target = self.reply_store(owner, asked)
            if target is not None:
                store, filing = target
                store.record_outcome(session.rid, str(step.get("id")), verdict, by="click", asked=filing)
        record.update(ok=verdict is not None, **facts, say=text, ms=round((time.perf_counter() - started) * 1000))
        self.log(record)
        say(text, object_id, facts)
        return {"verdict": verdict, "say": text}
