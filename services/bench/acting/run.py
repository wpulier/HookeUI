"""Run the acting bench's cases and score them.

    python -m bench.acting.run            # every case, a table
    python -m bench.acting.run --case live-140652 --verbose
    python -m bench.acting.run --models real --attempts 3 --out DIR --arm defaults: --arm early-off:speculate=off

`--models real` runs live_cases.json with the real models, in real time, on real pictures (world.py):
each case `--attempts` times in every arm (settings, one variable at a time), the arms taking turns, every
attempt and every provider call kept in `--out/<arm>` with the hash of the cases and of the scoring, so arms
compare only on the same frozen cases. A case passes only when all its attempts pass.

A case (cases.json) has:
- `slide`: things on the slide (name, box in frame pixels at rest, outlinable);
- `board`: what is on screen at the start (outlines, magnifier, the running look, a move, the specimen named);
- `events`: at case seconds, what the user `say`s (a piece of speech), a `handoff` by the voice,
  what the user `type`s in the chat (once the chat has answered what they typed before), a slide `move`, a new `look`;
  A request among several may carry its own `expect` (board predicates): what the user is told about it
  (its `id`) is checked against those, not against the case's end;
- `fast` / `vision`: how the models answer, as rules matched on the words;
- `check`: the outline check's verdicts (outline_check.py), each `on` the thing the checked outline is
  drawn around, `box_of` the thing whose box it gives; without it the check does not run;
- `expect`: `board` predicates that must hold once the request is done, `possible` (false: it
  cannot be done, and must end in an honest failure), `within` (seconds by which a predicate
  holds), `settle` (how long to wait after the last event), `told` (words the user must have been told),
  `brain_told` (words the brain's scene table held), `never_told` (words nothing said may contain),
  `logged` (interaction-log record fields), `queried` (per-tool result words), `downloaded` (models the agent
  started downloading; an empty list: none) and `opened` (pages the agent opened in the studio).
- `setup.not_downloaded`: models this case's Mac has not downloaded (the Models page's rows say so).

Scores:
- `final`: the board predicates at the end;
- `done_before_effect`: each time the user was told it was done while the board did not show it
  yet (or never would);
- `honest_failure`: a request that could not be done was said to have failed;
- `late`: a predicate that held later than `within` allows;
- `duplicates`: two outlines of one thing.
"""
from __future__ import annotations

import argparse
import re
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.brain import speech
from orchestrator.voice.openai_live import findings_text

from .world import World, kind_of

CASES = Path(__file__).with_name("cases.json")
LIVE_CASES = Path(__file__).with_name("live_cases.json")
# Scaled with the case's time. RECENT_VOICE_S is not: it is only ever compared with the speech clock,
# which the world gives in case seconds.
TIMINGS = ("JOIN_PIECES_S", "JOIN_UNFINISHED_S", "WAIT_FOR_WORDS_S", "WORDS_GRACE_S", "POINT_AFTER_S", "HANDS_KEPT_S",
           "HANDS_RESULT_S")


def load_cases(path: Path = CASES) -> list[dict]:
    return json.loads(path.read_text())["cases"]


def load_live(path: Path = LIVE_CASES) -> list[dict]:
    """The live cases, each with the picture it is `on` (its slide, and its board under the case's own)."""
    doc = json.loads(path.read_text())
    cases = []
    for case in doc["cases"]:
        picture = doc["pictures"][case["on"]]
        cases.append({"picture": picture["picture"], "slide": picture["slide"], **case,
                      "board": {**picture.get("board", {}), **case.get("board", {})}})
    return cases


def holds(pred: dict, board: dict) -> bool:
    """One board predicate: {"outlined": "xylem"}, {"not_outlined": "phloem"},
    {"magnifier": "open"|"closed"}, {"magnifier_shows": "reference"}, {"magnifier_beside": "reference"}, {"selected": "stoma"}, {"outlines": 2}, {"over": "cell 3"} (an outline
    covers that thing on the slide), {"population": 6} (the newest population's members), {"outlines_visible": true},
    {"magnifier_picture": "stoma 1 and stoma 2"} (the open magnifier shows a picture with that in its label),
    {"snapshot_shown": "request-id"} (that request's retained image and source receipt match the displayed capture);
    sessions (ADR 0017): {"session": "same"|"new"}, {"session_named": "leaf"}, {"conversations": 2},
    {"answer_filed": "guard cells"}, {"resumed": true}."""
    labels = [o["label"].lower() for o in board["outlines"]]
    if "over" in pred:  # a name, or a list of names any of which will do
        boxes = [board.get("things", {}).get(n.lower()) for n in names(pred["over"])]
        return any(box is not None and overlap(o["box"], box) > 0.5 for box in boxes for o in board["outlines"])
    if "on" in pred:  # {"on": "left eye"} (or a list): an outline of that thing itself, not of something round it
        return any(on(o["box"], board, n) for n in names(pred["on"]) for o in board["outlines"])
    if "magnified" in pred:  # {"magnified": "left eye"} (or a list): the magnifier is open on an outline of that thing
        shown = next((o for o in board["outlines"] if o["id"] == board["magnifier"].get("object")), None)
        return board["magnifier"]["open"] and shown is not None and any(on(shown["box"], board, n) for n in names(pred["magnified"]))
    if "population" in pred:
        return (board.get("population") or {}).get("count") == pred["population"]
    if "outlines_visible" in pred:  # {"outlines_visible": true}: the app's outlines drawn, as the studio reports it
        return board.get("outlinesVisible", True) == pred["outlines_visible"]
    if "coloured_by" in pred:  # {"coloured_by": "areaPx"}: the shown layer, coloured by that column, as the studio reports it
        return (board.get("layer") or {}).get("column") == pred["coloured_by"]
    if "made_by" in pred:  # {"made_by": ["guard cell", "sam2.1-tiny"]}: an outline of that (by label, or on the thing) made by
        label, impl = pred["made_by"]  # that model (or by any of a list of models)
        return any((label.lower() in o["label"].lower() or on(o["box"], board, label)) and o.get("madeBy") in names(impl)
                   for o in board["outlines"])
    if "setting" in pred:  # {"setting": ["outline", "edgetam"]}: the default now (a one-request choice leaves it)
        group, value = pred["setting"]
        return (board.get("settings") or {}).get(group) == value
    if "viewer" in pred:  # {"viewer": "open"|"closed"}: a researcher's work beside the view
        return bool((board.get("viewer") or {}).get("open")) == (pred["viewer"] == "open")
    if "population_made_by" in pred:  # {"population_made_by": "micro-sam-lm"}: cells segmented by that model (anyone's)
        return any(p.get("implementation") == pred["population_made_by"] for p in board.get("populations") or [])
    if "outlined" in pred:
        return any(pred["outlined"].lower() in label for label in labels)
    if "not_outlined" in pred:
        return not any(pred["not_outlined"].lower() in label for label in labels)
    if "magnifier_picture" in pred:
        comparison = board["magnifier"].get("comparison")
        if comparison is not None:
            # Legacy compare controls judge the shown originals in their actual
            # order. Source-qualified display labels are not an identity oracle.
            wanted = [label.lower() for label in pred["magnifier_picture"].split(" and ")]
            return board["magnifier"]["open"] and wanted == [label.lower() for label in comparison["labels"]]
        if board["magnifier"].get("requiresRetainedComparison"):
            return False  # the two named legacy controls cannot pass from a matching title alone
        shown = board["magnifier"].get("picture") or ""
        return board["magnifier"]["open"] and pred["magnifier_picture"].lower() in shown.lower()
    if "snapshot_shown" in pred:
        return bool((board.get("snapshot") or {}).get("request") == pred["snapshot_shown"])
    if "magnifier" in pred:
        return board["magnifier"]["open"] == (pred["magnifier"] == "open")
    if "magnifier_shows" in pred:  # {"magnifier_shows": "reference"}: the kind of picture it shows (media.py)
        return board["magnifier"].get("shows") == pred["magnifier_shows"]
    if "magnifier_beside" in pred:  # {"magnifier_beside": "reference"}: that kind of picture beside the close-up
        return board["magnifier"].get("beside") == pred["magnifier_beside"]
    if "selected" in pred:
        sel = next((o for o in board["outlines"] if o["id"] == board["selected"]), None)
        return sel is not None and pred["selected"].lower() in sel["label"].lower()
    if "outlines" in pred:
        return len(board["outlines"]) == pred["outlines"]
    session = board.get("session") or {}
    if "session" in pred:  # {"session": "same"|"new"}: the session the case began in is still open, or another is
        return session.get("first") == (pred["session"] == "same")
    if "session_named" in pred:  # {"session_named": "leaf"}: the session open is called that (words of its name)
        return pred["session_named"].lower() in (session.get("name") or "").lower()
    if "conversations" in pred:  # {"conversations": 2}: the session open has that many conversations
        return session.get("conversations") == pred["conversations"]
    if "answer_filed" in pred:  # {"answer_filed": "guard cells"}: the answer is in the session open's conversation
        return pred["answer_filed"].lower() in (session.get("answer") or "").lower()
    if "resumed" in pred:  # {"resumed": true}: a restart opens the session open now (the one the app was in)
        return (session.get("resumed") == session.get("id")) == pred["resumed"]
    raise ValueError(f"unknown predicate {pred}")


def name(pred: dict) -> str:
    key, value = next(iter(pred.items()))
    return f"{key} {value}"


@dataclass
class Report:
    case: str
    final_ok: bool
    failures: list[str] = field(default_factory=list)  # what went wrong, in words
    # The failures that are a `within` predicate holding, but late: (its failure's words, when it held, by when).
    # Wall-clock: test_acting checks them only with make test-timing, since a loaded Mac, not the code, makes them late.
    late: list[tuple[str, float, float]] = field(default_factory=list)
    done_before_effect: int = 0
    claims: list = field(default_factory=list)
    boards: list = field(default_factory=list)
    logged: list = field(default_factory=list)
    first_effect: float | None = None  # case seconds to the first change of the board
    done_at: float | None = None  # case seconds by which every predicate held (None: never)
    jargon: list[str] = field(default_factory=list)  # beginner cases: terms the user neither used nor asked about
    answered_at: float | None = None  # case seconds to the first answer said or shown (chat or voice)
    calls: list = field(default_factory=list)  # live cases: every provider call (diagnostic, never scored)
    prompts: dict = field(default_factory=dict)  # live cases: each prompt sent, by its hash

    @property
    def ok(self) -> bool:
        return not self.failures


# The vocabulary scan (Amendment 4, item 8): for beginner cases only, any term the user was told that they
# neither used nor asked about: model names, ids, and the app's own words for its machinery. Reported, never
# scored (expert and technical-level cases are never scanned).
INTERNAL_WORDS = ("capture", "epoch", "route", "raw", "execution", "population", "presentation", "fallback", "ticket")


def model_names() -> list[str]:
    from orchestrator.operations import OPERATIONS

    names = {i.label.split(" (")[0] for op in OPERATIONS.values() for i in op.implementations if i.kind == "model"}
    names |= {"Cellpose", "EdgeTAM", "SAM", "micro-sam", "Gemini", "GPT", "Molmo", "DINOv2", "BioCLIP", "MedSigLIP"}
    return sorted(n for n in names if len(n) > 2)


def jargon(said: str, user: str) -> list[str]:
    """The terms in `said` that a beginner would not know and did not use themselves (`user`)."""
    found = []
    low_user, low_said = user.lower(), said.lower()

    def used(word: str) -> bool:  # the user's own words, as words ("draw" is not "raw"; review S4), plurals too
        return re.search(rf"\b{re.escape(word.lower())}s?\b", low_user) is not None

    for name in model_names():
        if re.search(rf"\b{re.escape(name.lower())}\b", low_said) and not used(name):
            found.append(name)
    for word in INTERNAL_WORDS:
        if re.search(rf"\b{word}s?\b", low_said) and not used(word):
            found.append(word)
    for ident in re.findall(r"(?<![\w@])(?:[cdeors]\d+|@\d+)\b", said):  # the app's ids: c12, d3, e3, o4, r2, s1, @1 (review S4)
        if not used(ident):
            found.append(ident)
    return sorted(set(found))


def first_held(boards, pred) -> float | None:
    return next((t for t, b in boards if holds(pred, b)), None)


def duplicates(board: dict) -> list[str]:
    """Two outlines of one thing. Not when each was drawn by a model the request chose, and by different ones
    ("outline it with SAM 2.1, then with micro-sam" keeps both; Slice 5)."""
    out, seen = [], []
    for o in board["outlines"]:
        for p in seen:
            if p.get("chosen") and o.get("chosen") and p.get("madeBy") != o.get("madeBy"):
                continue
            if p["label"].lower() == o["label"].lower() and overlap(p["box"], o["box"]) > 0.5:
                out.append(o["label"])
        seen.append(o)
    return out


def names(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def on(box, board: dict, thing: str) -> bool:
    """An outline of the thing itself: it overlaps the thing's box by half of the two together (IoU 0.5)."""
    from .world import iou

    target = board.get("things", {}).get(thing.lower())
    return target is not None and iou(box, target) >= 0.5


def overlap(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / area if area > 0 else 0.0


async def play(world: World, events: list[dict]) -> None:
    """The case's events at their times; hand-offs and typed requests run as they would."""
    running: list[asyncio.Task] = []
    answered: asyncio.Future | None = None  # the request typed last, until its answer is in
    for event in sorted(events, key=lambda e: e["at"]):
        await world.clock.sleep(event["at"] - world.clock.now())
        if "say" in event:
            # `span`: the words' start and end on the voice's own clock (ms), as GPT-Live's transcript gives them
            world.speech.heard(event["say"], tuple(event["span"]) if event.get("span") else None)
        elif "handoff" in event:
            words = event["handoff"] if isinstance(event["handoff"], str) else ""
            running.append(asyncio.ensure_future(handoff(world, words, event.get("id", "d1"), event.get("offset_ms"))))
        elif "type" in event:
            # The user types the next request once the chat has answered the one before, as in the studio: its
            # `at` is the earliest it is typed. The case clock runs 10x faster than the app's own work, so on a
            # starved CPU a request typed at a fixed time came before the cells it was about (CI, 2026-10-21).
            answered = asyncio.ensure_future(typed(world, event["type"], event.get("id"), after=answered))
            running.append(answered)
        elif "click" in event:  # the user clicks a thing on the live view (commands.click)
            running.append(asyncio.ensure_future(world.click(event["click"])))
        elif "restart" in event:  # the app restarts (App.build): the day and what was made stay
            world.restart()
        elif "move" in event:
            world.move(**event["move"])
        elif "look" in event:
            world.look(event["look"])
    for task in running:
        await task


async def handoff(world: World, words: str, delegation_id: str, offset_ms=None) -> None:
    world.speech.handed_over()

    async def say(text: str) -> None:  # live cases: GPT-Live says the answer as soon as it is written (route.py)
        world.claim(text, kind_of(text), "voice", delegation_id)

    found = await world.speech.handoff(words, delegation_id, say if world.real else None, offset_ms=offset_ms)
    text = findings_text(found)
    # GPT-Live says what the findings say: a thing outlined, or a done-claim in the answer.
    kind = "failed" if "Could not:" in text else ("done" if found.get("outlined") else kind_of(found.get("answer", "")))
    world.claim(text, kind, "voice", delegation_id)


async def typed(world: World, words: str, about: str | None = None, after: asyncio.Future | None = None) -> None:
    if after is not None:
        await asyncio.wait([after])  # the answer before is in (its own failure is its own task's)
    result = await world.brain.turn(words, "chat")
    if about:
        world.request_turns[about] = result["turnId"]
    world.claim(result["answer"], kind_of(result["answer"]), "chat", about)
    for action in result.get("actions") or []:  # the answer's action lines under it: a step's report, finished ones
        if action.get("ok") is not None and action.get("summary"):
            world.claim(action["summary"], "info" if action["ok"] else "failed", "chat-action", about)


async def run_case(case: dict, scale: float = 0.1, models: str = "scripted", day_clock=None) -> Report:
    """`day_clock`: the session store's clock (the wall clock by default); a test pins the time of day with it."""
    import os

    saved = {k: getattr(speech, k) for k in TIMINGS}
    for k in TIMINGS:
        setattr(speech, k, saved[k] * scale)
    # A scripted case's `keys`: the providers' keys a choice checks for (a cloud model "can run here"), given stand-in
    # values for the case only; the scripted models never use them. Real runs have the real keys.
    stand_in = [k for k in case.get("keys", []) if models == "scripted" and not os.environ.get(k)]
    for k in stand_in:
        os.environ[k] = "acting-bench-stand-in"
    try:
        world = World(case, scale, models, day_clock)
        # Case deadlines begin when its scripted events can start, after fixture construction.
        # Imports, generated pixels and retained-board setup are not request latency.
        world.clock.t0 = time.monotonic()
        stop = asyncio.Event()
        watcher = asyncio.ensure_future(world.watch(stop))
        await play(world, case.get("events", []))
        await world.clock.sleep(case.get("expect", {}).get("settle", 6.0))
        stop.set()
        await watcher
    finally:
        for k, v in saved.items():
            setattr(speech, k, v)
        for k in stand_in:
            os.environ.pop(k, None)
    return score(case, world)


def score(case: dict, world: World) -> Report:
    expect = case.get("expect", {})
    preds = expect.get("board", [])
    possible = expect.get("possible", True)
    final = world.board()
    report = Report(case["id"], all(holds(p, final) for p in preds), claims=world.claims, boards=world.boards,
                    logged=world.agent.logged)
    start = world.boards[0][1] if world.boards else final
    report.first_effect = next((t for t, b in world.boards if b != start), None)
    report.answered_at = next((c.at for c in world.claims if c.by in ("chat", "voice") and c.text.strip()), None)
    report.calls, report.prompts = world.calls, world.prompts
    held = [first_held(world.boards, p) for p in preds]
    report.done_at = max(held) if held and all(h is not None for h in held) else None
    if possible and not report.final_ok:
        missing = [name(p) for p in preds if not holds(p, final)]
        report.failures.append(f"not done in the end: {', '.join(missing)}")
    # A request of several in a case may say what it alone must leave (its event's `expect`): what the user was
    # told about that request is checked against that, not against what a later request is to do.
    own = {e["id"]: e["expect"] for e in case.get("events", []) if e.get("id") and e.get("expect") is not None}
    # A spoken request starts when its words are said, not at its hand-off (hands can act before it).
    events = sorted(case.get("events", []), key=lambda e: e["at"])
    requests = sorted((max((s["at"] for s in events if "say" in s and s["at"] <= e["at"]), default=e["at"]), e["id"])
                      for e in events if e.get("id"))
    for c in world.claims:
        # Said without saying which request (the voice's "Done."): about the latest request made before it.
        about = c.about or next((rid for at, rid in reversed(requests) if at <= c.at), None)
        wanted = own.get(about, preds)
        if c.kind == "done" and not (possible and all(holds(p, c.board) for p in wanted)):
            report.done_before_effect += 1
            report.failures.append(f"said done at {c.at:.1f}s ({c.by}: {c.text[:70]!r}) before it was")
    if not possible and not any(c.kind == "failed" for c in world.claims):
        report.failures.append("could not be done, and was never said to have failed")
    for words in expect.get("told", []):  # what the user must have been told (a number from what was measured)
        if not any(words.lower() in c.text.lower() for c in world.claims):
            report.failures.append(f"never told {words!r}")
    answers = [c for c in world.claims if c.by in ("chat", "voice")]
    for alternatives in expect.get("answer_says", []):  # the answer itself says it (any of these words), not an action line
        if not any(w.lower() in c.text.lower() for w in names(alternatives) for c in answers):
            report.failures.append(f"the answer never said {' or '.join(map(repr, names(alternatives)))}")
    for words in expect.get("brain_told", []):  # a fact on screen the brain was given (going live, G3)
        if not any(words in table for table in world.agent.tables):
            report.failures.append(f"the brain was never told {words!r}")
    for words in expect.get("never_told", []):  # what nothing may say (a window the app does not have)
        said = next((c for c in world.claims if words.lower() in c.text.lower()), None)
        if said is not None:
            report.failures.append(f"told {words!r} ({said.by}: {said.text[:70]!r})")
    for wanted in expect.get("logged", []):  # what the app must have recorded (an outline check's decision)
        if not any(all(str(v).lower() in str(r.get(k, "")).lower() for k, v in wanted.items()) for r in world.agent.logged):
            report.failures.append(f"never logged {wanted}")
    for model in expect.get("downloaded", []):  # a download the agent started (get_model, on the user's yes)
        if model not in getattr(world, "downloads", []):
            report.failures.append(f"never started downloading {model}")
    if "downloaded" in expect and not expect["downloaded"] and getattr(world, "downloads", []):
        report.failures.append(f"started downloading {world.downloads} with no yes")
    for page in expect.get("opened", []):  # a page the agent opened in the studio (open_models)
        if page not in getattr(world, "pages", []):
            report.failures.append(f"never opened {page}")
    for tool, phrases in expect.get("queried", {}).items():  # what a tool itself said (a query's words go to the model)
        said = [text for name_, ok, text in getattr(world, "ran", []) if name_ == tool and ok]
        if not said:
            report.failures.append(f"{tool} never answered")
        for words in phrases:
            if said and not any(words.lower() in text.lower() for text in said):
                report.failures.append(f"{tool} never said {words!r} (it said {said[-1][:160]!r})")
    for held in expect.get("held", []):  # {"after": 6, "board": {...}}: it held at some moment (from `after` s on)
        if not any(t >= held.get("after", 0.0) and holds(held["board"], b) for t, b in world.boards):
            report.failures.append(f"{name(held['board'])}: never held" + (f" after {held['after']}s" if held.get("after") else ""))
    for pred_name, limit in expect.get("within", {}).items():
        pred = next(p for p in preds if name(p) == pred_name)
        t = first_held(world.boards, pred)
        if t is None or t > limit:
            report.failures.append(f"{pred_name}: {'never' if t is None else f'at {t:.1f}s'}, wanted by {limit}s")
            if t is not None:
                report.late.append((report.failures[-1], t, limit))
    dupes = duplicates(final)
    if dupes:
        report.failures.append(f"duplicate outlines: {', '.join(dupes)}")
    if case.get("beginner"):
        user = " ".join(str(e.get("say") or e.get("type") or e.get("handoff") or "") for e in case.get("events", []))
        # Everything the user is shown: answers, action lines, late steps, spoken lines and the magnifier's labels.
        shown = [c.text for c in world.claims] + list(world.hub.studio.picture_labels)
        report.jargon = jargon(" ".join(shown), user)
    return report


def frozen(path: Path) -> str:
    """The cases and how they are scored, as one hash: two runs compare only when theirs are the same."""
    import hashlib
    import inspect

    from .world import FakeGPU, iou

    scoring = (holds, score, on, names, overlap, iou, FakeGPU.real_segment, kind_of, duplicates, first_held, name)
    return hashlib.sha256(path.read_bytes() + "".join(inspect.getsource(f) for f in scoring).encode()).hexdigest()[:12]


def answer_times(case: dict, r: Report, empty: bool = False) -> list[dict]:
    """Time to answer, per request: from its first word (a typed line's send, a spoken request's first piece) to the
    first answer about it shown in the chat (typed) or said by the voice (spoken), before the next request starts.
    A request answered never has `answer` None. `empty`: an empty answer counts (the scripted cases, whose rewrite
    rounds mostly have no scripted reply: the turn's end is what is timed there)."""
    events = sorted(case.get("events", []), key=lambda e: e["at"])
    starts = []
    for e in events:
        if "type" in e:
            starts.append({"at": e["at"], "id": e.get("id"), "channel": "chat"})
        elif "say" in e and not (starts and starts[-1]["channel"] == "voice" and starts[-1].get("open")):
            starts.append({"at": e["at"], "id": None, "channel": "voice", "open": True})
        elif "handoff" in e and starts and starts[-1]["channel"] == "voice":
            starts[-1].update(open=False, id=e.get("id"))
    out = []
    for n, s in enumerate(starts):
        until = starts[n + 1]["at"] if n + 1 < len(starts) else float("inf")
        said = next((c for c in r.claims if c.by == s["channel"] and (empty or c.text.strip())
                     and (c.about == s["id"] if s["id"] and c.about else s["at"] <= c.at < until)), None)
        out.append({"id": s["id"], "channel": s["channel"], "at": s["at"],
                    "answer": round(said.at - s["at"], 3) if said is not None else None})
    return out


def raw(case_id: str, attempt: int, r: Report, case: dict | None = None) -> dict:
    """One attempt as it happened: what the user was told and when, every board, what the app logged, every call."""
    return {"case": case_id, "attempt": attempt, "ok": r.ok, "failures": r.failures,
            "firstEffect": r.first_effect, "doneAt": r.done_at, "answeredAt": r.answered_at,
            "doneBeforeEffect": r.done_before_effect,
            "answers": answer_times(case, r) if case is not None else [],
            "claims": [{"at": round(c.at, 3), "by": c.by, "kind": c.kind, "text": c.text, "about": c.about} for c in r.claims],
            "boards": [{"at": round(t, 3), "outlines": [[o["label"], o["box"]] for o in b["outlines"]],
                        "magnifier": b["magnifier"]} for t, b in r.boards],
            "logged": r.logged, "calls": [{k: v for k, v in c.items()} for c in r.calls]}


def median(values) -> float | None:
    values = sorted(v for v in values if v is not None)
    return round(values[len(values) // 2], 2) if values else None


def p90(values) -> float | None:
    """The 90th percentile (nearest rank)."""
    import math

    values = sorted(v for v in values if v is not None)
    return round(values[max(0, math.ceil(0.9 * len(values)) - 1)], 2) if values else None


def latency(answers: list[dict]) -> dict:
    """Time to answer, per request (answer_times), typed and spoken: p50 and p90, and how many were never answered.
    The latency gate (docs/bench/acting.md) holds these to the baseline."""
    out = {}
    for channel, name in (("chat", "Typed"), ("voice", "Spoken")):
        times = [a["answer"] for a in answers if a["channel"] == channel]
        out[f"answer{name}P50"], out[f"answer{name}P90"] = median(times), p90(times)
        out[f"answer{name}N"], out[f"unanswered{name}"] = len(times), sum(t is None for t in times)
    return out


def looks(logged: list[dict]) -> dict:
    """What the speculative looks did (EARLY_LOOK_TYPED, SPECULATE): typed early looks started and used, and looks that
    entered the provider and were dropped (each refunded once to its request, admission.forgive)."""
    early = [r for r in logged if r.get("kind") == "early-look"]
    return {"earlyLooks": len(early), "earlyLooksUsed": sum(bool(r.get("used")) for r in early),
            "lookRefunds": sum(bool(r.get("forgiven")) for r in early),
            "looksDiscarded": sum(1 for r in logged if r.get("kind") == "look-discarded" and r.get("attemptIds"))}


def summarize(cases: list[dict], rows: list, settings: dict, stamp: dict, attempts: int) -> dict:
    """`stamp`: the frozen hash and the commit as the run began (the code that ran)."""
    per_case = {}
    by_id = {case["id"]: case for case in cases}
    for case in cases:
        mine = [r for cid, _, r in rows if cid == case["id"]]
        per_case[case["id"]] = {"passed": f"{sum(r.ok for r in mine)}/{len(mine)}", "allPassed": all(r.ok for r in mine),
                                "answer": median(r.answered_at for r in mine), "effect": median(r.first_effect for r in mine),
                                "done": median(r.done_at for r in mine), "calls": median(len(r.calls) for r in mine),
                                "doneBeforeEffect": sum(r.done_before_effect for r in mine)}
    reports = [r for _, _, r in rows]
    answers = [a for cid, _, r in rows for a in answer_times(by_id[cid], r)]
    seen = looks([record for r in reports for record in r.logged])
    return {**stamp, "settings": settings, "attempts": attempts,
            "casesAllPassed": f"{sum(v['allPassed'] for v in per_case.values())}/{len(per_case)}",
            "attemptsPassed": f"{sum(r.ok for r in reports)}/{len(reports)}",
            "doneBeforeEffect": sum(r.done_before_effect for r in reports),
            "answer": median(r.answered_at for r in reports), "effect": median(r.first_effect for r in reports),
            "done": median(r.done_at for r in reports), "calls": median(len(r.calls) for r in reports),
            "callsP90": p90(len(r.calls) for r in reports), **latency(answers), **seen,
            "cases": per_case}


def live(args) -> None:
    """The live cases with the real models, each case `attempts` times in every arm (`--arm NAME:group=option,...`;
    `--set` alone is one arm). The arms take turns attempt by attempt, so a slow spell at the provider falls on all
    of them alike; each arm's attempts and summary go in its own folder under `--out`."""
    import os
    import subprocess
    import tempfile

    from orchestrator.keys import load_keys

    load_keys()  # from the real keys file, before the saves move to a scratch home
    os.environ.setdefault("MICROMIND_HOME", tempfile.mkdtemp(prefix="micromind-acting-live-"))  # never the real saves
    path = Path(args.cases) if args.cases else LIVE_CASES
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                                text=True).stdout.strip())
    stamp = {"frozen": frozen(path), "dirty": dirty,
             "commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()}
    arms = {}
    for spec in args.arm or [f"run:{','.join(args.set)}"]:
        name, _, pairs = spec.partition(":")
        arms[name] = dict(item.split("=", 1) for item in pairs.split(",") if item)
    cases = [c for c in load_live(path) if not args.case or c["id"] in args.case.split(",")]
    out = Path(args.out) if args.out else None
    for name in arms:
        if out is not None:
            (out / name / "prompts").mkdir(parents=True, exist_ok=True)
    rows = {name: [] for name in arms}
    for case in cases:
        for n in range(1, args.attempts + 1):
            for name, settings in arms.items():
                r = asyncio.run(run_case({**case, "settings": {**case.get("settings", {}), **settings}}, 1.0, "real"))
                rows[name].append((case["id"], n, r))
                print(f"{'ok  ' if r.ok else 'FAIL'} {name:<14} {case['id']:<36} #{n} answer {r.answered_at or 0:5.1f}s "
                      f"effect {r.first_effect or 0:5.1f}s done {r.done_at or 0:5.1f}s calls {len(r.calls)}  "
                      f"{'; '.join(r.failures)}", flush=True)
                if args.verbose:
                    for c in r.claims:
                        print(f"     {c.at:5.2f}s {c.by} [{c.kind}] {c.text[:140]}")
                if out is not None:
                    folder = out / name
                    (folder / f"{case['id']}-{n}.json").write_text(json.dumps(raw(case["id"], n, r, case), indent=1, default=str))
                    for key, prompt in r.prompts.items():
                        if not (folder / "prompts" / f"{key}.txt").exists():
                            (folder / "prompts" / f"{key}.txt").write_text(prompt)
    for name, settings in arms.items():
        summary = summarize(cases, rows[name], settings, stamp, args.attempts)
        print(name, json.dumps({k: v for k, v in summary.items() if k != "cases"}))
        if out is not None:
            (out / name / "summary.json").write_text(json.dumps(summary, indent=1))


def report(root: Path) -> dict:
    """Every arm's kept attempts under `root` (runs split over processes keep `<part>/<arm>/<case>-<n>.json`),
    read back into one table per arm: a case passes only when all its attempts did; times are medians."""
    arms: dict[str, dict[str, list[dict]]] = {}
    frozen_by_arm: dict[str, set] = {}
    for summary in root.rglob("summary.json"):
        frozen_by_arm.setdefault(summary.parent.name, set()).add(json.loads(summary.read_text()).get("frozen"))
    for path in sorted(root.rglob("*-[0-9].json")):
        row = json.loads(path.read_text())
        arms.setdefault(path.parent.name, {}).setdefault(row["case"], []).append(row)
    table = {}
    for arm, cases in sorted(arms.items()):
        rows = [r for rs in cases.values() for r in rs]
        calls = [len(r["calls"]) for r in rows]
        table[arm] = {"frozen": sorted(frozen_by_arm.get(arm, {"?"})),
                      "casesAllPassed": f"{sum(all(r['ok'] for r in rs) for rs in cases.values())}/{len(cases)}",
                      "attemptsPassed": f"{sum(r['ok'] for r in rows)}/{len(rows)}",
                      "doneBeforeEffect": sum(r["doneBeforeEffect"] for r in rows),
                      "answer": median(r["answeredAt"] for r in rows), "effect": median(r["firstEffect"] for r in rows),
                      "done": median(r["doneAt"] for r in rows), "calls": median(calls), "callsP90": p90(calls),
                      "callsTotal": sum(calls),
                      # Typed answers show when the turn ends; spoken ones as soon as written (the stand-in voice).
                      "answerTyped": median(r["answeredAt"] for r in rows if "-chat-" in r["case"]),
                      "answerSpoken": median(r["answeredAt"] for r in rows if "-voice-" in r["case"]),
                      # Per request (answer_times; runs kept before 2026-10-11 have none): the latency gate's numbers.
                      **latency([a for r in rows for a in r.get("answers") or []]),
                      **looks([record for r in rows for record in r.get("logged") or []]),
                      "failed": sorted({r["case"] for r in rows if not r["ok"]})}
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description="The acting bench (deterministic, or --models real).")
    parser.add_argument("--case", help="run only this case id")
    parser.add_argument("--scale", type=float, default=0.1, help="how much faster than real time")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--models", choices=("scripted", "real"), default="scripted")
    parser.add_argument("--cases", help="the cases file (live: live_cases.json)")
    parser.add_argument("--attempts", type=int, default=3, help="live: attempts per case (all must pass)")
    parser.add_argument("--set", action="append", default=[], metavar="GROUP=OPTION", help="live: a setting to compare")
    parser.add_argument("--arm", action="append", metavar="NAME:GROUP=OPTION,...",
                        help="live: an arm to compare (repeat; NAME: alone is the defaults); arms take turns")
    parser.add_argument("--out", help="live: where every attempt and prompt is kept")
    parser.add_argument("--report", metavar="DIR", help="live: every arm's kept attempts under DIR, as one table")
    args = parser.parse_args()
    if args.report:
        for arm, row in report(Path(args.report)).items():
            print(f"{arm:<14} {json.dumps(row)}")
        return None
    if args.models == "real":
        return live(args)
    cases = [c for c in load_cases() if not args.case or c["id"] == args.case]
    reports = [asyncio.run(run_case(c, args.scale)) for c in cases]
    for r in reports:
        mark = "ok  " if r.ok else "FAIL"
        timing = f"first effect {r.first_effect:.1f}s" if r.first_effect is not None else "no effect"
        print(f"{mark} {r.case:<34} {timing:<20} {'; '.join(r.failures)}"
              + (f"  [beginner words: {', '.join(r.jargon)}]" if r.jargon else ""))
        if args.verbose:
            for t, b in r.boards:
                print(f"     {t:5.2f}s board {[o['label'] for o in b['outlines']]} magnifier={b['magnifier']['open']}")
            for c in r.claims:
                print(f"     {c.at:5.2f}s {c.by} [{c.kind}] {c.text[:110]}")
    beginners = [r for r in reports if r.case in {c["id"] for c in cases if c.get("beginner")}]
    xfail = {c["id"] for c in cases if c.get("xfail")}
    # Time to answer with the stand-in models' fixed latencies (FAST_S, VISION_S), in case seconds: what it measures
    # is how many calls stand in a row before each answer, the same on every run.
    answers = latency([a for c, r in zip(cases, reports) for a in answer_times(c, r, empty=True)])
    print(f"\ntime to answer (stand-in latencies, case seconds): typed p50 {answers['answerTypedP50']} "
          f"p90 {answers['answerTypedP90']} (n {answers['answerTypedN']}); spoken p50 {answers['answerSpokenP50']} "
          f"p90 {answers['answerSpokenP90']} (n {answers['answerSpokenN']})")
    print(f"{sum(r.ok for r in reports if r.case not in xfail)}/{len(reports) - len(xfail)} cases pass, "
          f"{sum(not r.ok for r in reports if r.case in xfail)}/{len(xfail)} expected failures (xfail) fail")
    print(f"\n{sum(r.ok for r in reports)}/{len(reports)} cases pass; "
          f"said done before it was: {sum(r.done_before_effect for r in reports)}"
          + (f"; beginner cases with words they did not use: {sum(bool(r.jargon) for r in beginners)} of {len(beginners)}"
             if beginners else ""))


if __name__ == "__main__":
    main()
