"""The live partner's acceptance standard (Will, 2026-10-29), scored per step and per run from recorded event logs.

Everything is on one clock, epoch seconds (the player keeps milliseconds; `load_bench` converts):
- the player's log (harness.js `window.__bench.state`, saved as bench.json by drive.mjs or acoustic.py): each step's
  words (start, end), the talk key (down, up), and events: the voice's audio becoming audible or silent as heard in the
  page ("audio"), every /ws/voice message both ways ("ws-in", "ws-out"), marks drawn and cleared ("mark"), the live
  view's frames ("frames"), faults injected ("fault"), the researchers board ("researchers") and the checks after a
  restart ("check");
- the stack's conversation log (conversation.jsonl): what each voice said (chat lines) and each tool card (chat-action);
- the stack's interaction log (interactions.jsonl): the one brain's partner-* records (PARTNER-CONTRACT.md: talk, turn,
  response, barge, tool, marks, proactive, reconnect), and GPT-Live's voice-handoff records.

Both voices are scored by the same code. What only the one brain has (marks, proactive lines, reconnect, do_in_hooke)
is "not measured" for GPT-Live, never a pass. Code measures times, counts and identities; whether a spoken line
claims something is on screen is judged by a model (`judge.py`) and checked here against the studio's reports. The
leak and phrase detectors below find the known failures of 2026-10-28 in what was said; they measure, they never
decide anything for the app.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

TAIL_S = 40.0  # the last step's window runs this long after its words
NEAR_S = 0.3  # a line that starts this close before a confirmation still says it (the report and the words race)
SPEAKS_S = 3.0  # a mark drawn this long before the voice starts speaking is "before speech"
SPEECH_END_CLEAR_S = 2.0  # marks clear about 1.5 s after the voice stops (contract); this is the bound checked
USER_BUSY_S = 1.5  # "not talking": no key held, no speech in the last 1.5 s (Amendment 4)
POINTING = {"mark"}  # tools that point rather than act: never the "first try"
# The fallback hand-off ("no tool for this": the whole request to the brain), capped by the standard at under 5% of the
# one brain's acting calls. think (the frontier mind behind the voice, claude/p25-think) is the intended route for hard
# and multi-step work, not a fallback (the coordinator, 2026-10-29): it is the right tool where a step expects it and the
# wrong one where not, and its share is reported by kind of step, never capped.
FALLBACK = "do_in_hooke"
THINK = "think"

# The standard's numbers (PARTNER-CONTRACT.md "The acceptance standard"). One place, so the scoreboard and the tests
# read the same thresholds.
STANDARD = {
    "firstAudioP50": 0.8, "firstAudioP95": 1.5,
    "bargeSilentMax": 0.15,
    "singleP50": 3.0, "singleP95": 5.0,
    "multiMax": 8.0,
    "silenceMax": 1.5,
    "doInHookeShare": 0.05,
    "researcherStartMax": 2.0,
    "markLatencyMax": 0.2,
    "eventSpokenMax": 2.0,
    "reconnectMax": 2.0,
    "fpsKept": 0.9,  # live view frame rate at least this share of the baseline before Talk
    "delayAddedMaxS": 0.05,  # live view delay p90 no more than this above the baseline
    "plainShare": 0.9,  # answers of one or two sentences (the bench's reading of "one or two plain sentences")
}

# What was meant for the model and must never be spoken (live 2026-10-28 and the app's own note wording).
LEAK_PHRASES = ("nothing was outlined", "nothing's outlined", "nothing is outlined", "context only", "silent context",
                "not a user request", "do not acknowledge", "ask_app", "do_in_hooke", "look_hard", "function call",
                "tool call", "the delegation", "screen now:", "project brief", "untrusted")
LEAK_PATTERNS = (
    re.compile(r"[{\[]\s*\""),  # JSON
    re.compile(r"\"\s*:\s*[\"{\[\d]"),
    re.compile(r"\b(?:call|rtc|resp|req|item|evt|event|line)_[A-Za-z0-9]{3,}"),  # provider and app ids
    re.compile(r"\b[a-z]+_[a-z]+(?:_[a-z]+)*\b"),  # snake_case: a tool or field name
    re.compile(r"\b[0-9a-f]{12,}\b"),  # hashes
    re.compile(r"\b(?:o|e|r|w|d)\d{1,4}\b"),  # outline, entry, researcher, window ids ("saved as e3")
    re.compile(r"\bw-[a-z0-9-]{3,}\b"),
)
CANT_SEE = ("can't see your screen", "cannot see your screen", "can't see the screen", "cannot see the screen",
            "don't have access to your screen", "unable to see your screen", "can't see what's on your screen")
RESTART_ADVICE = ("restart the app", "restart hooke", "quit and reopen", "reopen the app", "relaunch", "close and reopen",
                  "quit the app", "restart your app")
NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty".split())}
NAMED = re.compile(r"\b(?:researcher|analyst|number)\s+(?:#\s*)?(\d+|" + "|".join(NUMBER_WORDS) + r")\b", re.I)


def leaks(text: str) -> list[str]:
    """What in a spoken line was meant for the model: the known phrases, JSON, ids."""
    low = text.lower()
    found = [p for p in LEAK_PHRASES if p in low]
    found += [m.group(0) for pattern in LEAK_PATTERNS for m in pattern.finditer(text)]
    return found


def phrases(text: str, among: tuple[str, ...]) -> list[str]:
    low = text.lower().replace("’", "'")
    return [p for p in among if p in low]


def sentences(text: str) -> int:
    return max(1, len([s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()])) if text.strip() else 0


def researcher_numbers(text: str) -> list[int]:
    out = []
    for m in NAMED.finditer(text):
        word = m.group(1).lower()
        out.append(int(word) if word.isdigit() else NUMBER_WORDS[word])
    return out


def pct(values: list[float], p: float) -> float | None:
    """The p-th percentile, nearest rank (as score.py)."""
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))], 3)


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


# --- loading ---------------------------------------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            if not isinstance(record.get("t"), (int, float)) and isinstance(record.get("at"), str):
                try:
                    record["t"] = time.mktime(time.strptime(record["at"][:19], "%Y-%m-%dT%H:%M:%S"))
                except ValueError:
                    pass
            out.append(record)
    return out


def seconds(value):
    """The player's epoch milliseconds as epoch seconds; seconds pass through."""
    return value / 1000 if isinstance(value, (int, float)) and value > 1e11 else value


def load_bench(raw: dict) -> dict:
    """The player's state with every time in epoch seconds."""
    steps = []
    for step in raw.get("steps") or []:
        s = dict(step)
        for key in ("start", "end", "down", "up", "bargeAt"):
            if key in s:
                s[key] = seconds(s[key])
        steps.append(s)
    events = []
    for event in raw.get("events") or []:
        e = dict(event)
        e["t"] = seconds(e.get("t"))
        events.append(e)
    events.sort(key=lambda e: e["t"] or 0)
    return {**raw, "steps": steps, "events": events, "started": seconds(raw.get("started")),
            "ended": seconds(raw.get("ended"))}


# --- what the page heard ---------------------------------------------------------------------------------------------

def audio_segments(events: list[dict], until: float | None = None) -> list[tuple[float, float]]:
    """When the voice was audible in the page: the remote track's level and the element not muted (harness.js)."""
    out, on = [], None
    for e in events:
        if e.get("e") != "audio":
            continue
        if e.get("on") and on is None:
            on = e["t"]
        elif not e.get("on") and on is not None:
            out.append((on, e["t"]))
            on = None
    if on is not None:
        out.append((on, until if until is not None else on))
    return out


def audio_ends(inter: list[dict]) -> dict[str, float]:
    """When each response's audio ended (partner-audio: over WebRTC the output buffer stops or is cleared after
    response.done, so partner-response's own audioEndT is only when the text finished)."""
    return {r["responseId"]: r["audioEndT"] for r in inter if r.get("kind") == "partner-audio"
            and r.get("responseId") and isinstance(r.get("audioEndT"), (int, float))}


def response_end(r: dict, ends: dict[str, float]) -> float | None:
    end = ends.get(r.get("responseId")) if r.get("responseId") else None
    return end if end is not None else r.get("audioEndT") if isinstance(r.get("audioEndT"), (int, float)) else None


def server_segments(inter: list[dict]) -> list[tuple[float, float]]:
    """The same from the server's records (acoustic replay of the packaged app has no page to hear): each response
    from output_audio_buffer.started to its audio's end."""
    ends, out = audio_ends(inter), []
    for r in inter:
        if r.get("kind") == "partner-response" and isinstance(r.get("firstAudioT"), (int, float)):
            end = response_end(r, ends)
            out.append((r["firstAudioT"], max(end if end is not None else r["firstAudioT"], r["firstAudioT"])))
    return sorted(out)


def first_on_after(segments, t: float, before: float) -> float | None:
    return next((a for a, _ in segments if t <= a < before), None)


def audible_at(segments, t: float) -> tuple[float, float] | None:
    return next(((a, b) for a, b in segments if a <= t < b), None)


def longest_gap(segments, start: float, end: float) -> float | None:
    """The longest silence in [start, end] (the time before the first audio counts: it is silence while working)."""
    if end <= start:
        return None
    gap, at = 0.0, start
    for a, b in segments:
        if b <= start or a >= end:
            continue
        gap = max(gap, max(0.0, a - at))
        at = max(at, b)
    return round(max(gap, end - at), 3)


# --- steps -----------------------------------------------------------------------------------------------------------

def opened(step: dict) -> float:
    """When the step began: the key down in hold mode, else the first word."""
    return step["down"] if isinstance(step.get("down"), (int, float)) else step["start"]


def released(step: dict) -> float:
    """When the user finished: the key up in hold mode, else the end of the words."""
    return step["up"] if isinstance(step.get("up"), (int, float)) else step["end"]


def windows(steps: list[dict]) -> list[tuple[float, float]]:
    out = []
    for i, step in enumerate(steps):
        until = opened(steps[i + 1]) if i + 1 < len(steps) else released(step) + TAIL_S
        out.append((opened(step), until))
    return out


def voice_lines(conv: list[dict], start: float, end: float) -> list[dict]:
    """The voice's lines in [start, end): one per lineId, with its first fragment's time and final text (score.py)."""
    lines: dict[str, dict] = {}
    for r in conv:
        at = r.get("at")
        if r.get("type") != "chat" or r.get("role") != "assistant" or not r.get("voice"):
            continue
        if not isinstance(at, (int, float)) or not start <= at < end:
            continue
        key = r.get("lineId") or f"{at}"
        line = lines.setdefault(key, {"first": at, "text": "", "id": key})
        line["first"] = min(line["first"], at)
        line["text"] = r.get("text") or line["text"]
    return sorted(lines.values(), key=lambda l: l["first"])


def fragments(bench: dict) -> dict[str, list[tuple[float, str]]]:
    """Each voice line's text as it grew in the page (harness "line" events): lineId -> [(time, text so far)]."""
    out: dict[str, list[tuple[float, str]]] = {}
    for e in bench["events"]:
        if e.get("e") == "line" and e.get("id"):
            out.setdefault(e["id"], []).append((e["t"], str(e.get("text") or "")))
    return out


def grew_after(line: dict, t: float) -> float | None:
    """When the line first had new words at or after `t` (its start when the page saw no growth)."""
    if line["first"] >= t:
        return line["first"]
    return next((at for at, _ in line.get("growth") or [] if at >= t), None)


def said_at(line: dict, words) -> float:
    """When `words` of the line were said: the first time the page showed them in it; else when the line began."""
    if isinstance(words, str) and words.strip():
        want = re.sub(r"\W+", " ", words.lower()).strip()
        for at, text in line.get("growth") or []:
            if want and want in re.sub(r"\W+", " ", text.lower()):
                return at
    return line["first"]


def lines_from_responses(inter: list[dict], start: float, end: float) -> list[dict]:
    """The one brain's said lines from its partner-response records (when the journal has none)."""
    out = []
    for r in inter:
        t = r.get("firstAudioT") if isinstance(r.get("firstAudioT"), (int, float)) else r.get("t")
        if r.get("kind") == "partner-response" and (r.get("transcript") or "").strip() and isinstance(t, (int, float)) \
                and start <= t < end:
            out.append({"first": t, "text": r["transcript"], "id": r.get("responseId") or f"{t}",
                        "cause": r.get("cause")})
    return sorted(out, key=lambda l: l["first"])


def api_researchers(bench: dict) -> dict[str, dict]:
    """The researchers the orchestrator listed during the run (/api/research, harness "researchers" events from the
    API): rid -> its latest row (number, created, finished, status). Empty for runs from before the player read it."""
    out: dict[str, dict] = {}
    for e in bench["events"]:
        if e.get("e") != "researchers":
            continue
        for row in e.get("rows") or []:
            if row.get("source") == "api" and row.get("rid"):
                out[row["rid"]] = {**out.get(row["rid"], {}), **row}
    return out


@dataclass
class Call:
    name: str
    start: float
    ok: bool | None
    confirmed_at: float | None  # the studio report that confirmed its visible change
    screen_change: bool
    rid: str | None = None
    rid_at: float | None = None
    error: str = ""
    end: float | None = None
    number: int | None = None  # the researcher's number ("Researcher 2"), when the call says it
    call_id: str | None = None
    inner: list = None  # think: the frontier mind's own tool calls ({name, ok, confirmed})


def partner_calls(inter: list[dict], step_n, start: float, end: float) -> list[Call]:
    out = []
    for r in inter:
        if r.get("kind") != "partner-tool":
            continue
        t = r.get("startT") if isinstance(r.get("startT"), (int, float)) else r.get("t")
        # In the step's window, and of its turn when both say one (the studio's numbers start again after a reload).
        mine = isinstance(t, (int, float)) and start <= t < end and \
            (step_n is None or r.get("n") is None or r.get("n") == step_n)
        if not mine:
            continue
        confirmed = r.get("reportT") if r.get("confirmed") is True and isinstance(r.get("reportT"), (int, float)) else None
        out.append(Call(str(r.get("name")), t, r.get("ok"), confirmed, bool(r.get("screenChange")), r.get("rid"),
                        r.get("ridT") if isinstance(r.get("ridT"), (int, float)) else None, str(r.get("error") or ""),
                        r.get("endT") if isinstance(r.get("endT"), (int, float)) else None, call_id=r.get("callId")))
    return sorted(out, key=lambda c: c.start)


def thinks(inter: list[dict]) -> dict[str, dict]:
    """think's tasks by the call that started them (claude/p25-think): the call returns at once; partner-think says how
    the frontier mind's work ended (its own tool calls inside, doneT); partner-think-said when its result was handed
    to the voice; the voice's next "think" response says it, often during a later step. callId -> the task."""
    said = {r.get("task"): r.get("responseCreatedT") for r in inter if r.get("kind") == "partner-think-said"
            and isinstance(r.get("responseCreatedT"), (int, float))}
    ends = audio_ends(inter)
    spoken = sorted((r["firstAudioT"], response_end(r, ends) or r["firstAudioT"] + 10) for r in inter
                    if r.get("kind") == "partner-response" and r.get("cause") == "think"
                    and isinstance(r.get("firstAudioT"), (int, float)))
    out = {}
    for r in inter:
        if r.get("kind") != "partner-think" or not r.get("callId"):
            continue
        at = said.get(r.get("task"))
        span = next(((a, b) for a, b in spoken if at is not None and a >= at - NEAR_S), None)
        out[r["callId"]] = {"task": r.get("task"), "n": r.get("n"), "ok": r.get("ok"), "doneT": r.get("doneT"),
                            "tools": [t for t in r.get("tools") or [] if isinstance(t, dict)], "error": r.get("error"),
                            "refused": r.get("refused"), "saidT": at, "span": span}
    return out


def with_thinks(calls: list[Call], tasks: dict[str, dict]) -> list[Call]:
    """A think call carries its task's end: done when the frontier mind finished, confirmed on screen then when one of
    its own tool calls was."""
    for c in calls:
        task = tasks.get(c.call_id or "") if c.name == "think" else None
        if task is None:
            continue
        c.inner = task["tools"]
        done = task["doneT"] if isinstance(task["doneT"], (int, float)) else None
        c.end, c.ok = done or c.end, task["ok"] if task["ok"] is not None else c.ok
        c.screen_change = any(t.get("confirmed") is not None for t in c.inner)
        c.confirmed_at = done if any(t.get("confirmed") is True for t in c.inner) else None
        c.error = str(task.get("error") or c.error or "")
    return calls


def card_calls(conv: list[dict], start: float, end: float) -> list[Call]:
    """Tool cards (chat-action lines) in [start, end): one per action, at its first showing; ok and the time it first
    showed ok (a late ✓ is shown again with the same action id)."""
    calls: dict[str, Call] = {}
    for r in conv:
        at = r.get("at")
        if r.get("type") != "chat-action" or not isinstance(at, (int, float)) or not start <= at < end:
            continue
        key = r.get("actionId") or f"{r.get('tool')}@{at}"
        call = calls.get(key)
        if call is None:
            call = calls[key] = Call(str(r.get("tool")), at, r.get("ok"), None, False)
        call.ok = r.get("ok") if r.get("ok") is not None else call.ok
        if r.get("ok") is True and call.confirmed_at is None:
            call.confirmed_at = at
        if call.name == "start_researcher" and r.get("ok") is True and call.rid_at is None:
            # Its researcher: the card's research-run output names it, its summary says its number.
            outputs = [o.get("resource") for o in r.get("outputs") or [] if isinstance(o, dict) and isinstance(o.get("resource"), dict)]
            resource = r.get("resource") if isinstance(r.get("resource"), dict) else {}
            rid = r.get("rid") or next((o.get("researcherId") for o in outputs if o.get("researcherId")), None) or resource.get("id")
            call.rid, call.rid_at = rid or key, at
            named = re.search(r"\bResearcher (\d+)\b", str(r.get("summary") or ""))
            call.number = int(named.group(1)) if named else None
        call.end = at
    return sorted(calls.values(), key=lambda c: c.start)


def turn_number(bench_events: list[dict], start: float, end: float):
    """The studio's turn number for this step (its talk down message on /ws/voice)."""
    for e in bench_events:
        m = e.get("m") or {}
        if e.get("e") == "ws-out" and m.get("type") == "talk" and m.get("down") and start - 0.5 <= e["t"] < end:
            return m.get("n")
    return None


def unprompted_spans(inter: list[dict]) -> list[tuple[float, float]]:
    """When the one brain spoke on its own (a proactive event, a reconnect line): those lines belong to no step."""
    ends, out = audio_ends(inter), []
    for r in inter:
        if r.get("kind") == "partner-response" and r.get("cause") in ("proactive", "reconnect") \
                and isinstance(r.get("firstAudioT"), (int, float)):
            end = response_end(r, ends)
            out.append((r["firstAudioT"] - NEAR_S, end if end is not None else r["firstAudioT"] + 10))
    return out


def step_lines(conv: list[dict], inter: list[dict], start: float, end: float, unprompted=(), own=(), others=()) -> list[dict]:
    """The voice's lines answering a step (what the judge numbers and the scorer reads, in the same order): the
    journal's, else the one brain's responses, without what it said on its own (events, reconnects). A think result
    belongs to the step that handed it over: its spoken span (`own`) is this step's wherever it falls, and another
    step's (`others`) is not."""
    lines = voice_lines(conv, start, end) or lines_from_responses(inter, start, end)
    lines = [l for l in lines if not any(a <= l["first"] <= b for a, b in [*unprompted, *others])]
    seen = {l["id"] for l in lines}
    for a, b in own:
        for l in voice_lines(conv, a - NEAR_S, b) or lines_from_responses(inter, a - NEAR_S, b):
            if l["id"] not in seen:
                seen.add(l["id"])
                lines.append(l)
    return sorted(lines, key=lambda l: l["first"])


def step_view(bench: dict, conv: list[dict], inter: list[dict], window: tuple[float, float], unprompted=(),
              tasks: dict | None = None):
    """A step's turn number, its tool calls (think's with their tasks) and the lines answering it."""
    start, end = window
    n = turn_number(bench["events"], start, end)
    calls = partner_calls(inter, n, start, end)
    source = "partner" if calls else "cards"  # the one brain's own tool records, else the brain's tool cards (GPT-Live)
    calls = with_thinks(calls, tasks or {}) if calls else card_calls(conv, start, end)
    mine = {c.call_id for c in calls if c.name == "think"}
    own = [t["span"] for cid, t in (tasks or {}).items() if cid in mine and t["span"]]
    others = [t["span"] for cid, t in (tasks or {}).items() if cid not in mine and t["span"]]
    return n, calls, source, step_lines(conv, inter, start, end, unprompted, own, others)


def restart_checks(checks: list[dict], conv: list[dict], step: dict) -> list[dict]:
    """A restart keeps where it was: the Project the journal filed lines in just before the restart is the one it files
    them in after it. That is the project check for any run (the page's own check says the same for runs since it
    compares with the Project open before the restart). Checks the first player wrote (2026-10-29) asked for a fixed
    Project and read researchers from a board the bench's studio does not draw: its project check is the journal's,
    and its researcher check, which could see none, is not measured."""
    at = step["start"]
    filed = [(r["at"], r.get("day")) for r in conv if isinstance(r.get("at"), (int, float)) and r.get("day")]
    before = max((x for x in filed if x[0] < at - 1), default=(None, None))[1]
    after = min((x for x in filed if x[0] > at + 1), default=(None, None))[1]
    out = []
    for check in checks:
        name, detail = str(check.get("name", "")), str(check.get("detail", ""))
        legacy = "before the restart" not in detail and "Researcher " not in detail
        if name.startswith("project:") and legacy and before and after:
            check.update(ok=before == after, detail=f"the journal's Project before the restart {before}, after it {after}")
        elif name.startswith("researcher:") and legacy and detail.startswith("rows: ;"):
            check.update(ok=None, detail="not measured: the first player read researchers from a board not drawn here")
        out.append(check)
    if not any(str(c.get("name", "")).startswith("project:") for c in out) and before and after:
        out.append({"name": "project (journal)", "ok": before == after,
                    "detail": f"the journal's Project before the restart {before}, after it {after}"})
    return out


def score_step(step: dict, expect: dict, window: tuple[float, float], bench: dict, conv: list[dict],
               inter: list[dict], segments, verdict: dict | None = None, unprompted=()) -> dict:
    """One step against its expectation. Every number is None when it does not apply or was not observed."""
    start, end = window
    release = released(step)
    n, calls, source, lines = step_view(bench, conv, inter, window, unprompted, bench.get("_thinks"))
    grown = bench.get("_fragments") or {}
    for l in lines:
        l["growth"] = grown.get(l["id"], [])
    acting = [c for c in calls if c.name not in POINTING]
    tools = expect.get("tools")  # acceptable first acting tools; [] = none expected
    row: dict = {"id": step["id"], "kind": step.get("kind"), "say": step.get("say"), "n": n, "toolSource": source,
                 "tools": [c.name for c in calls], "lines": [l["text"] for l in lines], "failures": []}
    fail = row["failures"].append

    # Feel: release to first audio, heard in the page (else the server's own record of it). A step without words (an
    # idle wait, the restart) has no release.
    spoken_step = step.get("kind") not in ("idle", "restart")
    first = first_on_after(segments, release, end) if spoken_step else None
    row["firstAudioS"] = round(first - release, 3) if first is not None else None

    # Barge-in: the key down while the voice was audible, to silence (and staying silent while held).
    if step.get("interrupt"):
        down = opened(step)
        heard = audible_at(segments, down)
        if heard is None:
            row["bargeS"], row["bargeNote"] = None, "the voice was not audible at key-down"
        else:
            row["bargeS"] = round(heard[1] - down, 3)
            again = first_on_after(segments, heard[1], release)
            row["bargeLeak"] = again is not None
            if row["bargeS"] > STANDARD["bargeSilentMax"] or again is not None:
                fail("barge-in not silent")

    # Acting: the right tool on the first try.
    first_tool = acting[0].name if acting else None
    row["firstTool"] = first_tool
    if tools is not None:
        if first_tool is None:
            row["toolRight"] = not expect.get("toolRequired", bool(tools))
        else:
            row["toolRight"] = first_tool in tools
        if not row["toolRight"]:
            fail(f"first tool {first_tool or 'none'}, expected {' or '.join(tools) or 'none'}")
    row["doInHooke"] = sum(1 for c in calls if c.name == FALLBACK)
    row["think"] = sum(1 for c in calls if c.name == THINK)
    row["thinkExpected"] = THINK in (tools or [])
    if any(c.inner is not None for c in calls):  # what the frontier mind did behind think
        row["thinkTools"] = [t.get("name") for c in calls for t in (c.inner or [])]
    row["acting"] = len(acting)

    # Researchers: started in this step, and how soon. The orchestrator's own list says which researchers were made in
    # the step (its `created`), whoever started them; without it, the start_researcher calls that succeeded.
    started = {}
    listed = bench.get("_researchers") or {}
    if listed:
        for rid, r in listed.items():
            if isinstance(r.get("created"), (int, float)) and opened(step) - 0.5 <= r["created"] < end:
                started[rid] = r["created"]
    else:
        for c in acting:
            if c.name == "start_researcher" and c.ok is not False and (c.rid or c.rid_at):
                started.setdefault(c.rid or f"{c.start}", c.rid_at or c.start)
    row["researcherNumbers"] = sorted({n for n in [(listed.get(rid) or {}).get("number") for rid in started]
                                       + [c.number for c in acting if c.name == "start_researcher" and c.rid in started]
                                       if isinstance(n, int)})
    row["researchers"] = len(started)
    row["researcherIds"] = sorted(started)
    expected_researchers = expect.get("researchers", 0)
    if row["researchers"] != expected_researchers:
        fail(f"{row['researchers']} researchers started, expected {expected_researchers}")
    row["researcherStartS"] = round(min(started.values()) - release, 3) if started else None

    # Visible: the first confirmed change by an expected tool (any screen change when none are listed); a researcher
    # step's change is its card, there once the researcher has started.
    visible_at = None
    if expect.get("visible"):
        wanted = [c.confirmed_at for c in acting if (not tools or c.name in tools) and c.confirmed_at is not None]
        # A single step's change is its first; a multi-step request is done at its last.
        visible_at = (max if expect.get("multi") else min)(wanted, default=None)
        if visible_at is None and started:
            visible_at = min(started.values())
        if visible_at is None and not expect.get("fails"):
            fail(f"not confirmed on screen: {expect['visible']}")

    # Outcome visible and spoken: the first words said at or after the confirmation (a line already under way when the
    # report came counts from the words it said after it, as the page saw them grow).
    after = [(grew_after(l, visible_at - NEAR_S), l) for l in lines] if visible_at is not None else []
    outcome_lines = [l for at, l in after if at is not None]
    spoken_at = min((at for at, _ in after if at is not None), default=None)
    row["visibleS"] = round(visible_at - release, 3) if visible_at is not None else None
    row["spokenS"] = round(spoken_at - release, 3) if spoken_at is not None else None
    row["outcomeS"] = round(max(visible_at, spoken_at) - release, 3) if visible_at is not None and spoken_at is not None else None
    row["multi"] = bool(expect.get("multi"))
    if visible_at is not None and spoken_at is None:
        fail("the outcome was not said")

    # A request that cannot succeed: its outcome is the failure, said after the call that failed (else after release).
    if expect.get("fails") and visible_at is None:
        failed = [c for c in acting if c.ok is False]
        anchor = (failed[0].end or failed[0].start) if failed else release
        outcome_lines = [l for l in lines if grew_after(l, anchor - NEAR_S) is not None]
        if any(c.confirmed_at is not None and c.screen_change for c in acting):
            fail("a request that cannot succeed changed the screen")
    # Said once: lines after the confirmation (the judge's count when it ran).
    if expect.get("visible") or expect.get("outcome"):
        said = (verdict or {}).get("outcome_mentions")
        counted = len(outcome_lines) if visible_at is not None or expect.get("fails") else None
        row["outcomesSaid"] = said if isinstance(said, int) else counted
        if row["outcomesSaid"] is not None and row["outcomesSaid"] != 1:
            fail(f"outcome said {row['outcomesSaid']} times")
    else:
        row["outcomesSaid"] = None

    # No silence over 1.5 s while working: from release to the outcome said (else to the last word in the step).
    # Nothing said and nothing confirmed: the work ran to its last call's end (else the step's end) in silence.
    if acting:
        work_end = max(visible_at or 0, spoken_at or 0) or max((b for a, b in segments if start <= a < end), default=None) \
            or max((c.end or c.confirmed_at or c.start for c in acting), default=None) or end
        row["longestSilenceS"] = longest_gap(segments, release, max(work_end, release))
    else:
        row["longestSilenceS"] = None

    # Nothing meant for the model spoken; never "I can't see your screen"; never "restart the app".
    row["leaks"] = [hit for l in lines for hit in leaks(l["text"])]
    row["cantSee"] = [hit for l in lines for hit in phrases(l["text"], CANT_SEE)]
    row["restartAdvice"] = [hit for l in lines for hit in phrases(l["text"], RESTART_ADVICE)]
    for what, hits in (("spoke text meant for the model", row["leaks"]), ('said it cannot see the screen', row["cantSee"]),
                       ("told the user to restart", row["restartAdvice"])):
        if hits:
            fail(f"{what}: {hits[:3]}")
    row["sentences"] = [sentences(l["text"]) for l in lines]

    # Status answers name the right researcher (its number, as the board shows it).
    if expect.get("status"):
        want = bench.get("_expectedResearcher")
        said = sorted({k for l in lines for k in researcher_numbers(l["text"])})
        row["statusNamed"], row["statusExpected"] = said, want
        # No researcher from the plan's researcher step: that step failed, and there is no right answer to check here.
        row["statusRight"] = None if want is None else said == [want]
        if row["statusRight"] is False:
            fail(f"status named researcher {said or 'none'}, expected {want}")

    # Nothing on screen that was not asked for.
    row["unaskedChanges"] = sum(1 for c in acting if c.screen_change and c.confirmed_at is not None
                                and tools is not None and c.name not in (tools or []))

    # False done: a line the judge says claims a visible change, with no confirmation by the time it was said.
    # A claim is {"line": i, "words": "..."} (or just i): it was said when the page first showed its words.
    claims = (verdict or {}).get("claims_on_screen")
    if isinstance(claims, list):
        confirmed = sorted(c.confirmed_at for c in calls if c.confirmed_at is not None)
        false = []
        for claim in claims:
            i, words = (claim.get("line"), claim.get("words")) if isinstance(claim, dict) else (claim, None)
            if isinstance(i, int) and 0 <= i < len(lines):
                at = said_at(lines[i], words)
                if not any(t <= at + NEAR_S for t in confirmed):
                    false.append((words or lines[i]["text"])[:60])
        row["falseDone"] = len(false)
        if false:
            fail(f"said done without a studio report: {false}")
    else:
        row["falseDone"] = None
    # Without the judge: a screen change that was never confirmed, and the voice spoke after it (to be read).
    row["unconfirmedThenSpoke"] = sum(1 for c in acting if c.screen_change and c.confirmed_at is None
                                      and any(l["first"] > c.start for l in lines))

    # The restart step's checks (the Project, its table, Tab and researcher output are there after reopening).
    checks = [dict(e) for e in bench["events"] if e.get("e") == "check" and e.get("step") == step["id"]]
    if step.get("restart"):
        checks = restart_checks(checks, conv, step)
    if checks:  # ok None: it was not there before the restart (an earlier step's failure, not counted again here)
        row["checks"] = {e["name"]: e.get("ok") for e in checks}
        for e in checks:
            if e.get("ok") is False:
                fail(f"after restart: {e['name']} missing ({e.get('detail', '')})")
    recovered = [e for e in bench["events"] if e.get("e") == "recovered" and e.get("step") == step["id"]]
    if recovered:
        row["recovered"] = [{"what": e.get("what"), "from": e.get("from"), "ok": e.get("ok")} for e in recovered]
    if any(e.get("e") == "staged" and e.get("step") == step["id"] for e in bench["events"]):
        row["staged"] = True  # the bench put the specimen back on the stage after this step
    if not lines and spoken_step and not expect.get("silent"):
        fail("no answer")
    if verdict and verdict.get("answered") is False:
        fail(f"not answered: {verdict.get('why', '')}")
    # How it felt as a colleague (judge.py partner_quality, 0-3): reported, never a step failure.
    quality = (verdict or {}).get("partner_quality")
    row["partnerQuality"] = quality if isinstance(quality, (int, float)) and not isinstance(quality, bool) and 0 <= quality <= 3 else None
    row["judge"] = verdict
    return row


# --- marks, events, reconnect, the live view -------------------------------------------------------------------------

def marks(bench: dict, segments, expects: dict[str, dict], steps: list[dict], wins) -> list[dict]:
    """Each mark the studio drew: how soon after the speech about it started, whether its target was the one expected
    and resolved, whether it followed its target, and when it cleared against the end of the speech."""
    drawn: dict[str, dict] = {}
    for e in bench["events"]:
        key = f"{e.get('id')}|{e.get('target')}"
        if e.get("e") == "mark" and e.get("on"):
            m = drawn.setdefault(key, {"id": e.get("id"), "target": e.get("target"), "shown": e["t"], "cleared": None,
                                       "samples": 0, "following": 0})
            if e.get("rect") and e.get("targetRect"):
                m["samples"] += 1
                m["following"] += int(overlap(e["rect"], e["targetRect"]) >= 0.8)
        elif e.get("e") == "mark" and not e.get("on") and key in drawn and drawn[key]["cleared"] is None:
            drawn[key]["cleared"] = e["t"]
    resolved: dict[str, dict] = {}
    for e in bench["events"]:
        m = e.get("m") or {}
        if e.get("e") == "ws-out" and m.get("type") == "marks-shown":
            for r in m.get("resolved") or []:
                resolved[f"{m.get('id')}|{r.get('target')}"] = r
    out = []
    for key, m in drawn.items():
        step = next((s for s, (a, b) in zip(steps, wins) if a <= m["shown"] < b), None)
        expect = expects.get(step["id"], {}) if step else {}
        heard = audible_at(segments, m["shown"])
        speech = heard[0] if heard else first_on_after(segments, m["shown"], m["shown"] + SPEAKS_S)
        latency = round(m["shown"] - speech, 3) if speech is not None else None
        r = resolved.get(key, {})
        wanted = expect.get("marks")
        target_right = None if wanted is None else any(str(m["target"]).startswith(p) for p in wanted)
        speech_end = next((b for a, b in segments if a <= m["shown"] + SPEAKS_S and b >= m["shown"]), None)
        clear = round(m["cleared"] - speech_end, 3) if m["cleared"] is not None and speech_end is not None else None
        out.append({"step": step["id"] if step else None, "target": m["target"], "latencyS": latency,
                    "resolved": r.get("ok", True) if r else None, "targetRight": target_right,
                    "following": round(m["following"] / m["samples"], 2) if m["samples"] else None,
                    "cleared": m["cleared"] is not None, "clearAfterSpeechS": clear})
    # Asked for but never drawn (unresolved): the reply says why.
    for key, r in resolved.items():
        if key not in drawn and not r.get("ok"):
            out.append({"step": None, "target": r.get("target"), "latencyS": None, "resolved": False,
                        "targetRight": None, "following": None, "cleared": None, "clearAfterSpeechS": None,
                        "why": r.get("why")})
    return out


def overlap(a, b) -> float:
    """Intersection over union of two [x, y, w, h] rects."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def holds(steps: list[dict]) -> list[tuple[float, float]]:
    """When the user was talking: each key hold, else each step's words, plus the quiet after (USER_BUSY_S). A step
    with no words and no key (an idle wait, a restart) is nobody talking: an event told during it is not over the user."""
    return [(opened(s), released(s) + USER_BUSY_S) for s in steps
            if s.get("say") or isinstance(s.get("down"), (int, float))]


def proactive(inter: list[dict], segments, busy, researchers: dict | None = None) -> list[dict]:
    """Each event the one brain told unprompted: event to speech, whether it spoke over the user, one line per event.
    A researcher's event happened when it finished (the orchestrator's list); the record's eventT is when the line
    was sent, after any wait for quiet."""
    out, seen = [], {}
    for r in inter:
        if r.get("kind") != "partner-proactive":
            continue
        key = f"{r.get('event')}|{r.get('ref')}"
        seen[key] = seen.get(key, 0) + (1 if r.get("responseCreatedT") else 0)
        event_t = r.get("eventT") if isinstance(r.get("eventT"), (int, float)) else r.get("t")
        finished = ((researchers or {}).get(str(r.get("ref"))) or {}).get("finished")
        if str(r.get("event", "")).startswith("researcher") and isinstance(finished, (int, float)) \
                and isinstance(event_t, (int, float)) and finished <= event_t:
            event_t = finished
        created = r.get("responseCreatedT")
        spoke = first_on_after(segments, created, created + 15) if isinstance(created, (int, float)) else None
        if spoke is None and isinstance(created, (int, float)):
            spoke = next((x.get("firstAudioT") for x in inter if x.get("kind") == "partner-response"
                          and x.get("cause") == "proactive" and isinstance(x.get("firstAudioT"), (int, float))
                          and x["firstAudioT"] >= created), None)
        over = spoke is not None and any(a <= spoke < b for a, b in busy)
        out.append({"event": r.get("event"), "ref": r.get("ref"), "skipped": r.get("skipped"),
                    "userBusy": r.get("userBusy"),
                    "spokenS": round(spoke - event_t, 3) if spoke is not None and isinstance(event_t, (int, float)) else None,
                    "overUser": over, "pointed": bool(r.get("markTarget"))})
    for item in out:
        item["lines"] = seen.get(f"{item['event']}|{item['ref']}", 0)
    return out


def reconnects(inter: list[dict], bench: dict) -> list[dict]:
    faults = [e["t"] for e in bench["events"] if e.get("e") == "fault" and e.get("fault") == "close-provider"]
    out = []
    for r in inter:
        if r.get("kind") != "partner-reconnect":
            continue
        ms = r.get("ms")
        ready = r.get("readyT")
        fault = max((t for t in faults if isinstance(ready, (int, float)) and t <= ready), default=None)
        out.append({"reason": r.get("reason"), "s": round(ms / 1000, 3) if isinstance(ms, (int, float)) else None,
                    "faultToReadyS": round(ready - fault, 3) if fault is not None else None,
                    "midSpeech": r.get("midSpeech")})
    return out


def live_view(bench: dict, baseline: list[dict] | None = None) -> dict:
    """Frames on /ws per second (fps) and their delay, before Talk (the baseline) and while the journey ran. Only the
    seconds with a source on the stage count: the empty stage (nothing to view) is no live view, not a slow one. Runs
    from before the player said so count from their first frame on. Without frames before Talk, the baseline is the
    setup's (`baseline`)."""
    steps = bench["steps"]
    first = opened(steps[0]) if steps else None
    frames = [e for e in bench["events"] if e.get("e") == "frames"]

    def live(rows):
        if any("stage" in r for r in rows):
            return [r for r in rows if r.get("stage") != "empty"]
        flowing = next((i for i, r in enumerate(rows) if r.get("n")), None)
        return rows[flowing:] if flowing is not None else []

    # The baseline is the live view while it was on (its seconds with frames); the journey's seconds count as they
    # came, a freeze with a source on the stage included.
    before = [r for r in live([e for e in frames if first is not None and e["t"] < first]) if r.get("n")]
    during = live([e for e in frames if first is None or e["t"] >= first])
    if not before and baseline:
        before = [r for r in live(baseline) if r.get("n")]

    def stats(rows):
        return {"fps": median([r["fps"] for r in rows if isinstance(r.get("fps"), (int, float))]),
                "delayP90": pct([r["delayP90"] for r in rows if isinstance(r.get("delayP90"), (int, float))], 90),
                "samples": len(rows)}

    return {"baseline": stats(before), "during": stats(during), "live": bool(during)}


# --- a run -----------------------------------------------------------------------------------------------------------

def score_run(bench: dict, conv: list[dict], inter: list[dict], plan: dict, verdicts: dict | None = None,
              baseline: list[dict] | None = None) -> dict:
    """One repetition of a plan: its steps and its run-wide numbers. `baseline`: the setup's frames events, the live
    view's baseline when the run has none before Talk."""
    bench = load_bench(bench)
    steps = [s for s in bench["steps"] if not s.get("setup")]
    expects = {s["id"]: s.get("expect") or {} for s in plan.get("steps") or []}
    wins = windows(steps)
    end = wins[-1][1] if wins else 0
    # What was heard: in the page; else the one brain's own audio records; else (GPT-Live heard acoustically) each
    # spoken line's first words, as an instant.
    heard_from = "page"
    segments = audio_segments(bench["events"], end)
    if not segments:
        segments, heard_from = server_segments(inter), "server"
    if not segments:
        segments = [(l["first"], l["first"]) for l in voice_lines(conv, wins[0][0] if wins else 0, end)]
        heard_from = "transcripts"
    rows = []
    unprompted = unprompted_spans(inter)
    # The researcher a status step must name: the one the plan's researcher step started (its number, from the
    # orchestrator's list, else the board, else what its start_researcher card said).
    started_numbers: list[int] = []
    board = {}
    for e in bench["events"]:
        if e.get("e") == "researchers":
            for row in e.get("rows") or []:
                if row.get("rid") and row.get("number") is not None:
                    board[row["rid"]] = row["number"]
    bench["_researchers"] = api_researchers(bench)
    bench["_fragments"] = fragments(bench)
    bench["_thinks"] = thinks(inter)
    for step, win in zip(steps, wins):
        expect = expects.get(step["id"], {})
        # "latest": the one the plan's researcher step started; a number: the researcher the user named ("researcher 2").
        status = expect.get("status")
        bench["_expectedResearcher"] = status if isinstance(status, int) and not isinstance(status, bool) else \
            started_numbers[-1] if status and started_numbers else None
        row = score_step(step, expect, win, bench, conv, inter, segments, (verdicts or {}).get(step["id"]), unprompted)
        if expect.get("researchers"):
            numbers = sorted({board[rid] for rid in row["researcherIds"] if rid in board} | set(row["researcherNumbers"]))
            started_numbers += numbers[-1:]
        rows.append(row)
    for key in ("_expectedResearcher", "_fragments", "_researchers", "_thinks"):
        bench.pop(key, None)
    busy = holds(steps)
    return {"steps": rows, "marks": marks(bench, segments, expects, steps, wins),
            "proactive": proactive(inter, segments, busy, api_researchers(bench)), "reconnects": reconnects(inter, bench),
            "liveView": live_view(bench, [{**e, "t": seconds(e.get("t"))} for e in baseline or []]),
            "error": bench.get("error"), "audioFrom": heard_from}


def think_share(steps: list[dict]) -> dict:
    """think's calls over the one brain's acting calls, by kind of step (act, view, ask, status...), and the steps that
    used it where the plan did not expect it (a quick step: its first-try line fails there too)."""
    by_kind: dict[str, list[int]] = {}
    for r in steps:
        k = by_kind.setdefault(str(r.get("kind") or "?"), [0, 0])
        k[0] += r.get("think", 0)
        k[1] += r["acting"]
    calls, of = sum(k[0] for k in by_kind.values()), sum(k[1] for k in by_kind.values())
    return {"calls": calls, "of": of, "share": round(calls / of, 3) if of else None,
            "byKind": {kind: k for kind, k in sorted(by_kind.items()) if k[0]},
            "unexpected": [r["id"] for r in steps if r.get("think") and not r.get("thinkExpected")]}


def summarise(runs: list[dict]) -> dict:
    """Every run pooled: the numbers the standard names."""
    steps = [r for run in runs for r in run["steps"]]
    marks_ = [m for run in runs for m in run["marks"]]
    events = [p for run in runs for p in run["proactive"]]
    recon = [r for run in runs for r in run["reconnects"]]
    first = [r["firstAudioS"] for r in steps if r.get("firstAudioS") is not None]
    single = [r["outcomeS"] for r in steps if r.get("outcomeS") is not None and not r["multi"]]
    multi = [r["outcomeS"] for r in steps if r.get("outcomeS") is not None and r["multi"]]
    barge = [r["bargeS"] for r in steps if r.get("bargeS") is not None]
    silence = [r["longestSilenceS"] for r in steps if r.get("longestSilenceS") is not None]
    said = [r["outcomesSaid"] for r in steps if r.get("outcomesSaid") is not None]
    tool_rows = [r for r in steps if r.get("toolRight") is not None]
    # do_in_hooke is the one brain's: its share is over the one brain's own calls (GPT-Live hands everything over).
    calls = sum(r["acting"] for r in steps if r["toolSource"] == "partner")
    fallback = sum(r["doInHooke"] for r in steps if r["toolSource"] == "partner")
    judged = [r for r in steps if r.get("falseDone") is not None]
    researcher_rows = [r for r in steps if r.get("researcherStartS") is not None]
    status = [r for r in steps if r.get("statusRight") is not None]
    spoken = [s for r in steps for s in r["sentences"]]
    views = [run["liveView"] for run in runs]
    base_fps = median([v["baseline"]["fps"] for v in views if v["baseline"]["fps"] is not None])
    run_fps = median([v["during"]["fps"] for v in views if v["during"]["fps"] is not None])
    base_delay = median([v["baseline"]["delayP90"] for v in views if v["baseline"]["delayP90"] is not None])
    run_delay = median([v["during"]["delayP90"] for v in views if v["during"]["delayP90"] is not None])
    no_live = sum(1 for v in views if not v.get("live", True))
    return {
        "runs": len(runs), "steps": len(steps),
        "firstAudio": {"p50": median(first), "p95": pct(first, 95), "n": len(first)},
        "barge": {"max": max(barge) if barge else None, "leaks": sum(1 for r in steps if r.get("bargeLeak")), "n": len(barge)},
        "single": {"p50": median(single), "p95": pct(single, 95), "n": len(single)},
        "multi": {"max": max(multi) if multi else None, "n": len(multi)},
        "silence": {"max": max(silence) if silence else None, "p95": pct(silence, 95), "n": len(silence)},
        "saidOnce": {"once": sum(1 for s in said if s == 1), "n": len(said),
                     "never": sum(1 for s in said if s == 0), "more": sum(1 for s in said if s > 1)},
        "leaks": sum(len(r["leaks"]) for r in steps),
        "leakLines": [h for r in steps for h in r["leaks"]][:20],
        "cantSee": sum(len(r["cantSee"]) for r in steps),
        "restartAdvice": sum(len(r["restartAdvice"]) for r in steps),
        "toolFirstTry": {"right": sum(1 for r in tool_rows if r["toolRight"]), "n": len(tool_rows)},
        "doInHooke": {"calls": fallback, "of": calls, "share": round(fallback / calls, 3) if calls else None},
        "think": think_share([r for r in steps if r["toolSource"] == "partner"]),
        "falseDone": {"count": sum(r["falseDone"] for r in judged), "judgedSteps": len(judged),
                      "unconfirmedThenSpoke": sum(r["unconfirmedThenSpoke"] for r in steps)},
        "researchers": {"wrongCount": sum(1 for r in steps if any("researchers started" in f for f in r["failures"])),
                        "startMax": max((r["researcherStartS"] for r in researcher_rows), default=None),
                        "n": len(researcher_rows)},
        "status": {"right": sum(1 for r in status if r["statusRight"]), "n": len(status),
                   "unchecked": sum(1 for r in steps if r.get("statusExpected", 0) is None)},
        "marks": {"n": len(marks_),
                  "latencyMax": max((m["latencyS"] for m in marks_ if m["latencyS"] is not None), default=None),
                  "latencyP95": pct([m["latencyS"] for m in marks_ if m["latencyS"] is not None], 95),
                  "unresolved": sum(1 for m in marks_ if m["resolved"] is False),
                  "wrongTarget": sum(1 for m in marks_ if m["targetRight"] is False),
                  "notFollowing": sum(1 for m in marks_ if m["following"] is not None and m["following"] < 0.9),
                  "clearedLate": sum(1 for m in marks_ if m["clearAfterSpeechS"] is not None
                                     and m["clearAfterSpeechS"] > SPEECH_END_CLEAR_S),
                  "neverCleared": sum(1 for m in marks_ if m["cleared"] is False)},
        "events": {"n": len([p for p in events if not p.get("skipped")]),
                   "spokenMax": max((p["spokenS"] for p in events if p["spokenS"] is not None), default=None),
                   "overUser": sum(1 for p in events if p["overUser"]),
                   "repeated": sum(1 for p in events if p["lines"] > 1),
                   "pointed": sum(1 for p in events if p["pointed"])},
        "reconnect": {"max": max((r["s"] for r in recon if r["s"] is not None), default=None), "n": len(recon)},
        "plain": {"share": round(sum(1 for s in spoken if s <= 2) / len(spoken), 3) if spoken else None, "n": len(spoken)},
        "quality": {"mean": round(statistics.mean(q), 2) if (q := [r["partnerQuality"] for r in steps
                                                                    if r.get("partnerQuality") is not None]) else None,
                    "n": len(q), "under2": [r["id"] for r in steps if r.get("partnerQuality") is not None
                                            and r["partnerQuality"] < 2]},
        "unasked": sum(r["unaskedChanges"] for r in steps),
        "liveView": {"baselineFps": base_fps, "fps": run_fps, "baselineDelayP90": base_delay, "delayP90": run_delay,
                     "runsWithoutLiveView": no_live},
        "stepFailures": sum(1 for r in steps if r["failures"]),
        "runsClean": sum(1 for run in runs if not any(r["failures"] for r in run["steps"]) and not run.get("error")),
        "errors": [run["error"] for run in runs if run.get("error")],
    }


# --- the scoreboard --------------------------------------------------------------------------------------------------

NOT_APPLICABLE = "not applicable to this voice"
# Lines of what only the one brain does (its own tools, marks, events told unprompted, its reconnect): GPT-Live hands
# every request to the brain and has none of them. Barge-in applies to both (GPT-Live's open microphone is interrupted
# by speaking over it).
ONE_BRAIN_ONLY = {"do_in_hooke share", "think share", "Mark after speech starts", "Id marks correct and following", "Cleared at speech end",
                  "Event spoken when idle", "Never over the user, one line, pointing", "Reconnect"}
VOICES_WITHOUT = {"gpt-live-1"}


def _line(area, name, value, threshold, ok, why: str = "") -> dict:
    """ok: True pass, False fail, None not measured (never counted as a pass)."""
    return {"area": area, "line": name, "value": value, "threshold": threshold,
            "result": "pass" if ok is True else "FAIL" if ok is False else "not measured" + (f": {why}" if why else "")}


def _fmt(v, unit=" s"):
    return "—" if v is None else f"{v:.2f}{unit}" if isinstance(v, float) else f"{v}{unit}"


def scoreboard(s: dict, voice: str | None = None) -> list[dict]:
    """The standard's lines, each pass, FAIL or not measured, from a summary; for a voice without the one brain's own
    features, those lines are not applicable to it (still not a pass)."""
    S = STANDARD
    L = []
    fa = s["firstAudio"]
    L.append(_line("Feel", "Release to first audio", f"p50 {_fmt(fa['p50'])}, p95 {_fmt(fa['p95'])} (n {fa['n']})",
                   f"p50 ≤ {S['firstAudioP50']} s, p95 ≤ {S['firstAudioP95']} s",
                   None if not fa["n"] else fa["p50"] <= S["firstAudioP50"] and fa["p95"] <= S["firstAudioP95"]))
    b = s["barge"]
    L.append(_line("Feel", "Barge-in silent", f"max {_fmt(b['max'])}, came back {b['leaks']} (n {b['n']})",
                   f"≤ {S['bargeSilentMax']} s, stays silent", None if not b["n"] else b["max"] <= S["bargeSilentMax"] and not b["leaks"]))
    sg = s["single"]
    L.append(_line("Feel", "Single-step: visible and spoken", f"p50 {_fmt(sg['p50'])}, p95 {_fmt(sg['p95'])} (n {sg['n']})",
                   f"p50 ≤ {S['singleP50']} s, p95 ≤ {S['singleP95']} s",
                   None if not sg["n"] else sg["p50"] <= S["singleP50"] and sg["p95"] <= S["singleP95"]))
    m = s["multi"]
    L.append(_line("Feel", "Multi-step: visible and spoken", f"max {_fmt(m['max'])} (n {m['n']})", f"≤ {S['multiMax']} s",
                   None if not m["n"] else m["max"] <= S["multiMax"]))
    si = s["silence"]
    L.append(_line("Feel", "Longest silence while working", f"max {_fmt(si['max'])} (n {si['n']})", f"≤ {S['silenceMax']} s",
                   None if not si["n"] else si["max"] <= S["silenceMax"]))
    so = s["saidOnce"]
    L.append(_line("Feel", "Each outcome said exactly once", f"{so['once']} of {so['n']} (never {so['never']}, more {so['more']})",
                   "all", None if not so["n"] else so["once"] == so["n"]))
    L.append(_line("Feel", "Nothing meant for the model spoken", f"{s['leaks']} {s['leakLines'][:3]}", "0",
                   s["leaks"] == 0 if s["steps"] else None))
    L.append(_line("Seeing", 'Never "I can\'t see your screen"', str(s["cantSee"]), "0", s["cantSee"] == 0 if s["steps"] else None))
    t = s["toolFirstTry"]
    L.append(_line("Acting", "Right tool on the first try", f"{t['right']} of {t['n']}", "all",
                   None if not t["n"] else t["right"] == t["n"]))
    d = s["doInHooke"]
    L.append(_line("Acting", "do_in_hooke share", f"{d['calls']} of {d['of']}" + (f" ({d['share']:.0%})" if d["share"] is not None else ""),
                   f"< {S['doInHookeShare']:.0%}", None if not d["of"] else d["share"] < S["doInHookeShare"]))
    th = s.get("think") or {}
    kinds = ", ".join(f"{kind} {k[0]} of {k[1]}" for kind, k in (th.get("byKind") or {}).items())
    unexpected = th.get("unexpected") or []
    L.append({"area": "Acting", "line": "think share", "gate": False, "threshold": "reported, not a gate",
              "value": (f"{th['calls']} of {th['of']}" + (f" ({th['share']:.0%})" if th.get("share") is not None else "")
                        + (f"; by step kind: {kinds}" if kinds else "")
                        + (f"; where not expected: {len(unexpected)} ({', '.join(dict.fromkeys(unexpected))})" if unexpected else ""))
              if th.get("of") else "—",
              "result": "reported" if th.get("of") else "not measured"})
    f = s["falseDone"]
    L.append(_line("Acting", 'False "done"', f"{f['count']} in {f['judgedSteps']} judged steps; unconfirmed then spoke "
                   f"{f['unconfirmedThenSpoke']}", "0", None if not f["judgedSteps"] else f["count"] == 0))
    r = s["researchers"]
    L.append(_line("Acting", "One researcher per request", f"{r['wrongCount']} steps wrong", "0",
                   r["wrongCount"] == 0 if s["steps"] else None))
    L.append(_line("Acting", "Researcher started", f"max {_fmt(r['startMax'])} (n {r['n']})", f"≤ {S['researcherStartMax']} s",
                   None if not r["n"] else r["startMax"] <= S["researcherStartMax"]))
    st = s["status"]
    L.append(_line("Acting", "Status names the right researcher", f"{st['right']} of {st['n']}"
                   + (f" ({st['unchecked']} with no researcher from the plan's researcher step)" if st.get("unchecked") else ""),
                   "all", None if not st["n"] else st["right"] == st["n"]))
    mk = s["marks"]
    L.append(_line("Pointing", "Mark after speech starts", f"p95 {_fmt(mk['latencyP95'])}, max {_fmt(mk['latencyMax'])} (n {mk['n']})",
                   f"≤ {S['markLatencyMax']} s", None if mk["latencyMax"] is None else mk["latencyMax"] <= S["markLatencyMax"]))
    L.append(_line("Pointing", "Id marks correct and following",
                   f"unresolved {mk['unresolved']}, wrong target {mk['wrongTarget']}, not following {mk['notFollowing']}",
                   "0", None if not mk["n"] else not (mk["unresolved"] or mk["wrongTarget"] or mk["notFollowing"])))
    L.append(_line("Pointing", "Cleared at speech end", f"late {mk['clearedLate']}, never {mk['neverCleared']}",
                   f"≤ {SPEECH_END_CLEAR_S} s after speech", None if not mk["n"] else not (mk["clearedLate"] or mk["neverCleared"])))
    ev = s["events"]
    L.append(_line("Knowing", "Event spoken when idle", f"max {_fmt(ev['spokenMax'])} (n {ev['n']})", f"≤ {S['eventSpokenMax']} s",
                   None if ev["spokenMax"] is None else ev["spokenMax"] <= S["eventSpokenMax"]))
    L.append(_line("Knowing", "Never over the user, one line, pointing",
                   f"over {ev['overUser']}, repeated {ev['repeated']}, pointed {ev['pointed']} of {ev['n']}", "0, 0, all",
                   None if not ev["n"] else not ev["overUser"] and not ev["repeated"] and ev["pointed"] == ev["n"]))
    rc = s["reconnect"]
    L.append(_line("Conversation", "Reconnect", f"max {_fmt(rc['max'])} (n {rc['n']})", f"≤ {S['reconnectMax']} s",
                   None if rc["max"] is None else rc["max"] <= S["reconnectMax"]))
    lv = s["liveView"]
    fps_ok = None if lv["fps"] is None or lv["baselineFps"] is None else lv["fps"] >= S["fpsKept"] * lv["baselineFps"]
    delay_ok = None if lv["delayP90"] is None or lv["baselineDelayP90"] is None else \
        lv["delayP90"] <= lv["baselineDelayP90"] + S["delayAddedMaxS"] * 1000
    no_live = lv.get("runsWithoutLiveView", 0)
    L.append(_line("Robustness", "Live view frame rate and delay unchanged",
                   f"fps {_fmt(lv['fps'], '')} vs {_fmt(lv['baselineFps'], '')}, delay p90 {_fmt(lv['delayP90'], ' ms')} vs "
                   f"{_fmt(lv['baselineDelayP90'], ' ms')}" + (f"; {no_live} run(s) with nothing on the stage" if no_live else ""),
                   f"fps ≥ {S['fpsKept']:.0%}, delay +≤ {S['delayAddedMaxS'] * 1000:.0f} ms",
                   False if False in (fps_ok, delay_ok) else None if None in (fps_ok, delay_ok) else True,
                   "no live view on the stage" if no_live and no_live == s["runs"] else
                   "no live view before Talk to compare with" if lv["baselineFps"] is None else ""))
    L.append(_line("Robustness", "Runs with zero step failures", f"{s['runsClean']} of {s['runs']} ({s['stepFailures']} failed steps)",
                   "all runs", None if not s["runs"] else s["runsClean"] == s["runs"]))
    L.append(_line("Robustness", 'Never "restart the app"', str(s["restartAdvice"]), "0", s["restartAdvice"] == 0 if s["steps"] else None))
    pl = s["plain"]
    L.append(_line("Elegance", "One or two sentences", f"{_fmt(pl['share'], '')} of {pl['n']} lines", f"≥ {S['plainShare']:.0%}",
                   None if pl["share"] is None else pl["share"] >= S["plainShare"]))
    L.append(_line("Elegance", "Nothing on screen not asked for", str(s["unasked"]), "0", s["unasked"] == 0 if s["steps"] else None))
    # Overall: how much it felt like a sharp colleague (the judge's partner_quality, 0-3). Reported, not a gate.
    q = s.get("quality") or {}
    under = q.get("under2") or []
    L.append({"area": "Overall", "line": "Partner quality (0-3, judged)", "gate": False,
              "value": (f"mean {q['mean']:.2f} over {q['n']} steps; under 2: {len(under)}"
                        + (f" ({', '.join(dict.fromkeys(under))})" if under else "")) if q.get("mean") is not None else "—",
              "threshold": "reported, not a gate", "result": "reported" if q.get("mean") is not None else "not measured"})
    if voice in VOICES_WITHOUT:
        for line in L:
            if line["line"] in ONE_BRAIN_ONLY:
                line["result"] = NOT_APPLICABLE
    return L


def meets(board: list[dict]) -> bool:
    """The standard holds only when every line passes: a line not measured, or not applicable to the voice, is not a
    pass (a voice without a feature the standard asks for does not meet it). Reported lines (think's share, partner
    quality) are never a gate."""
    return all(line["result"] == "pass" for line in board if line.get("gate", True))


def markdown(result: dict) -> str:
    """The short scoreboard: one table, then each failed step."""
    head = result.get("about", {})
    out = [f"# Live partner journey: {head.get('voice', '?')} ({head.get('talk', '?')}), plan {head.get('plan', '?')}", "",
           f"{result['summary']['runs']} run(s), {result['summary']['steps']} steps. Audio heard {head.get('audioFrom', 'in the page')}; "
           f"{head.get('evidence', 'recorded audio injected as the microphone')}. Not Will in a room.", "",
           f"**Standard met: {'yes' if meets(result['scoreboard']) else 'no'}**", "",
           *([f"The bench put the journey back in its Project after {len(result['recovered'])} step(s) that left it "
              "elsewhere (scored as they were; the steps after them are not scored again for it)."] if result.get("recovered") else []),
           *([f"The bench put the specimen back on an empty stage {result['staged']} time(s) (a Project reopened, or a step "
              "left the stage empty)."] if result.get("staged") else []),
           *([""] if result.get("recovered") or result.get("staged") else []),
           "| Area | Line | Measured | Standard | Result |", "|---|---|---|---|---|"]
    for line in result["scoreboard"]:
        out.append(f"| {line['area']} | {line['line']} | {line['value']} | {line['threshold']} | {line['result']} |")
    failed = [(i, r) for i, run in enumerate(result["runs"]) for r in run["steps"] if r["failures"]]
    if failed:
        out += ["", "## Failed steps", ""]
        for i, r in failed:
            out.append(f"- run {i + 1}, **{r['id']}** \"{r['say']}\": " + "; ".join(r["failures"]))
    return "\n".join(out) + "\n"


# --- pricing (the runner settles the budget from these) --------------------------------------------------------------

# Per 1M tokens (PARTNER-CONTRACT.md "Verified facts"): gpt-realtime-2.1.
REALTIME_PRICE = {"inText": 4.0, "inAudio": 32.0, "inImage": 5.0, "outText": 24.0, "outAudio": 64.0}


def realtime_cost(inter: list[dict]) -> float | None:
    """The one brain's provider-reported usage (partner-response records) priced; None when there is none."""
    total, seen = 0.0, False
    for r in inter:
        usage = r.get("usage") if r.get("kind") == "partner-response" else None
        if not isinstance(usage, dict):
            continue
        seen = True
        for key, price in REALTIME_PRICE.items():
            total += float(usage.get(key) or 0) * price / 1e6
    return round(total, 4) if seen else None
