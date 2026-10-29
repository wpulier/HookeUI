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
FALLBACK = "do_in_hooke"

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


def server_segments(inter: list[dict]) -> list[tuple[float, float]]:
    """The same from the server's partner-response records (acoustic replay of the packaged app has no page to hear)."""
    out = []
    for r in inter:
        if r.get("kind") == "partner-response" and isinstance(r.get("firstAudioT"), (int, float)):
            end = r.get("audioEndT") if isinstance(r.get("audioEndT"), (int, float)) else r["firstAudioT"]
            out.append((r["firstAudioT"], max(end, r["firstAudioT"])))
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


def partner_calls(inter: list[dict], step_n, start: float, end: float) -> list[Call]:
    out = []
    for r in inter:
        if r.get("kind") != "partner-tool":
            continue
        t = r.get("startT") if isinstance(r.get("startT"), (int, float)) else r.get("t")
        mine = (r.get("n") == step_n) if step_n is not None and r.get("n") is not None else \
            isinstance(t, (int, float)) and start <= t < end
        if not mine:
            continue
        confirmed = r.get("reportT") if r.get("confirmed") is True and isinstance(r.get("reportT"), (int, float)) else None
        out.append(Call(str(r.get("name")), t, r.get("ok"), confirmed, bool(r.get("screenChange")), r.get("rid"),
                        r.get("ridT") if isinstance(r.get("ridT"), (int, float)) else None, str(r.get("error") or ""),
                        r.get("endT") if isinstance(r.get("endT"), (int, float)) else None))
    return sorted(out, key=lambda c: c.start)


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
            resource = r.get("resource") if isinstance(r.get("resource"), dict) else {}
            call.rid, call.rid_at = r.get("rid") or resource.get("id") or key, at
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
    out = []
    for r in inter:
        if r.get("kind") == "partner-response" and r.get("cause") in ("proactive", "reconnect") \
                and isinstance(r.get("firstAudioT"), (int, float)):
            end = r.get("audioEndT") if isinstance(r.get("audioEndT"), (int, float)) else r["firstAudioT"] + 10
            out.append((r["firstAudioT"] - NEAR_S, end))
    return out


def score_step(step: dict, expect: dict, window: tuple[float, float], bench: dict, conv: list[dict],
               inter: list[dict], segments, verdict: dict | None = None, unprompted=()) -> dict:
    """One step against its expectation. Every number is None when it does not apply or was not observed."""
    start, end = window
    release = released(step)
    n = turn_number(bench["events"], start, end)
    lines = voice_lines(conv, start, end) or lines_from_responses(inter, start, end)
    lines = [l for l in lines if not any(a <= l["first"] <= b for a, b in unprompted)]
    calls = partner_calls(inter, n, start, end)
    source = "partner" if calls else "cards"  # the one brain's own tool records, else the brain's tool cards (GPT-Live)
    calls = calls or card_calls(conv, start, end)
    acting = [c for c in calls if c.name not in POINTING]
    tools = expect.get("tools")  # acceptable first acting tools; [] = none expected
    row: dict = {"id": step["id"], "kind": step.get("kind"), "say": step.get("say"), "n": n, "toolSource": source,
                 "tools": [c.name for c in calls], "lines": [l["text"] for l in lines], "failures": []}
    fail = row["failures"].append

    # Feel: release to first audio, heard in the page (else the server's own record of it).
    first = first_on_after(segments, release, end)
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
    row["acting"] = len(acting)

    # Researchers: started in this step, and how soon.
    started = {}
    for c in acting:
        if c.name == "start_researcher" and c.ok is not False and (c.rid or c.rid_at):
            started.setdefault(c.rid or f"{c.start}", c.rid_at or c.start)
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

    # Outcome visible and spoken: the first line at or after the confirmation.
    outcome_lines = [l for l in lines if visible_at is not None and l["first"] >= visible_at - NEAR_S]
    spoken_at = outcome_lines[0]["first"] if outcome_lines else None
    row["visibleS"] = round(visible_at - release, 3) if visible_at is not None else None
    row["spokenS"] = round(spoken_at - release, 3) if spoken_at is not None else None
    row["outcomeS"] = round(max(visible_at, spoken_at) - release, 3) if visible_at is not None and spoken_at is not None else None
    row["multi"] = bool(expect.get("multi"))
    if visible_at is not None and spoken_at is None:
        fail("the outcome was not said")

    # Said once: lines after the confirmation (the judge's count when it ran).
    if expect.get("visible") or expect.get("outcome"):
        said = (verdict or {}).get("outcome_mentions")
        row["outcomesSaid"] = said if isinstance(said, int) else (len(outcome_lines) if visible_at is not None else None)
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
        row["statusRight"] = want is not None and said == [want]
        if not row["statusRight"]:
            fail(f"status named researcher {said or 'none'}, expected {want}")

    # Nothing on screen that was not asked for.
    row["unaskedChanges"] = sum(1 for c in acting if c.screen_change and c.confirmed_at is not None
                                and tools is not None and c.name not in (tools or []))

    # False done: a line the judge says claims a visible change, with no confirmation by the time it was said.
    claims = (verdict or {}).get("claims_on_screen")
    if isinstance(claims, list):
        confirmed = sorted(c.confirmed_at for c in calls if c.confirmed_at is not None)
        false = [i for i in claims if isinstance(i, int) and 0 <= i < len(lines)
                 and not any(t <= lines[i]["first"] + NEAR_S for t in confirmed)]
        row["falseDone"] = len(false)
        if false:
            fail(f"said done without a studio report: {[lines[i]['text'][:60] for i in false]}")
    else:
        row["falseDone"] = None
    # Without the judge: a screen change that was never confirmed, and the voice spoke after it (to be read).
    row["unconfirmedThenSpoke"] = sum(1 for c in acting if c.screen_change and c.confirmed_at is None
                                      and any(l["first"] > c.start for l in lines))

    # The restart step's checks (the Project, its table, Tab and researcher output are there after reopening).
    checks = [e for e in bench["events"] if e.get("e") == "check" and e.get("step") == step["id"]]
    if checks:
        row["checks"] = {e["name"]: bool(e.get("ok")) for e in checks}
        for e in checks:
            if not e.get("ok"):
                fail(f"after restart: {e['name']} missing ({e.get('detail', '')})")
    if not lines and not step.get("restart") and not expect.get("silent"):
        fail("no answer")
    if verdict and verdict.get("answered") is False:
        fail(f"not answered: {verdict.get('why', '')}")
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
    """When the user was talking: each key hold, else each step's words, plus the quiet after (USER_BUSY_S)."""
    return [(opened(s), released(s) + USER_BUSY_S) for s in steps]


def proactive(inter: list[dict], segments, busy) -> list[dict]:
    """Each event the one brain told unprompted: event to speech, whether it spoke over the user, one line per event."""
    out, seen = [], {}
    for r in inter:
        if r.get("kind") != "partner-proactive":
            continue
        key = f"{r.get('event')}|{r.get('ref')}"
        seen[key] = seen.get(key, 0) + (1 if r.get("responseCreatedT") else 0)
        event_t = r.get("eventT") if isinstance(r.get("eventT"), (int, float)) else r.get("t")
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


def live_view(bench: dict) -> dict:
    """Frames on /ws per second (fps) and their delay, before Talk (the baseline) and while the journey ran."""
    steps = bench["steps"]
    first = opened(steps[0]) if steps else None
    before = [e for e in bench["events"] if e.get("e") == "frames" and first is not None and e["t"] < first]
    during = [e for e in bench["events"] if e.get("e") == "frames" and (first is None or e["t"] >= first)]

    def stats(rows):
        return {"fps": median([r["fps"] for r in rows if isinstance(r.get("fps"), (int, float))]),
                "delayP90": pct([r["delayP90"] for r in rows if isinstance(r.get("delayP90"), (int, float))], 90),
                "samples": len(rows)}

    return {"baseline": stats(before), "during": stats(during)}


# --- a run -----------------------------------------------------------------------------------------------------------

def score_run(bench: dict, conv: list[dict], inter: list[dict], plan: dict, verdicts: dict | None = None) -> dict:
    """One repetition of a plan: its steps and its run-wide numbers."""
    bench = load_bench(bench)
    steps = [s for s in bench["steps"] if not s.get("setup")]
    expects = {s["id"]: s.get("expect") or {} for s in plan.get("steps") or []}
    wins = windows(steps)
    end = wins[-1][1] if wins else 0
    segments = audio_segments(bench["events"], end) or server_segments(inter)
    rows = []
    unprompted = unprompted_spans(inter)
    started_numbers: list[int] = []  # the researchers this run started, by their board number
    board = {}  # rid -> the number its board row shows ("Researcher 3"); else the number in an "r3" id
    for e in bench["events"]:
        if e.get("e") == "researchers":
            for row in e.get("rows") or []:
                if row.get("rid") and row.get("number") is not None:
                    board[row["rid"]] = row["number"]
    for step, win in zip(steps, wins):
        expect = expects.get(step["id"], {})
        if expect.get("status") and started_numbers:
            bench["_expectedResearcher"] = started_numbers[-1]
        row = score_step(step, expect, win, bench, conv, inter, segments, (verdicts or {}).get(step["id"]), unprompted)
        for rid in row["researcherIds"]:
            number = board.get(rid)
            if number is None and re.fullmatch(r"r\d+", str(rid)):
                number = int(str(rid)[1:])
            if number is not None:
                started_numbers.append(number)
        rows.append(row)
    bench.pop("_expectedResearcher", None)
    busy = holds(steps)
    return {"steps": rows, "marks": marks(bench, segments, expects, steps, wins),
            "proactive": proactive(inter, segments, busy), "reconnects": reconnects(inter, bench),
            "liveView": live_view(bench), "error": bench.get("error"), "audioFrom": "page" if
            audio_segments(bench["events"], end) else "server"}


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
        "falseDone": {"count": sum(r["falseDone"] for r in judged), "judgedSteps": len(judged),
                      "unconfirmedThenSpoke": sum(r["unconfirmedThenSpoke"] for r in steps)},
        "researchers": {"wrongCount": sum(1 for r in steps if any("researchers started" in f for f in r["failures"])),
                        "startMax": max((r["researcherStartS"] for r in researcher_rows), default=None),
                        "n": len(researcher_rows)},
        "status": {"right": sum(1 for r in status if r["statusRight"]), "n": len(status)},
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
        "unasked": sum(r["unaskedChanges"] for r in steps),
        "liveView": {"baselineFps": base_fps, "fps": run_fps, "baselineDelayP90": base_delay, "delayP90": run_delay},
        "stepFailures": sum(1 for r in steps if r["failures"]),
        "runsClean": sum(1 for run in runs if not any(r["failures"] for r in run["steps"]) and not run.get("error")),
        "errors": [run["error"] for run in runs if run.get("error")],
    }


# --- the scoreboard --------------------------------------------------------------------------------------------------

def _line(area, name, value, threshold, ok) -> dict:
    """ok: True pass, False fail, None not measured (never counted as a pass)."""
    return {"area": area, "line": name, "value": value, "threshold": threshold,
            "result": "pass" if ok is True else "FAIL" if ok is False else "not measured"}


def _fmt(v, unit=" s"):
    return "—" if v is None else f"{v:.2f}{unit}" if isinstance(v, float) else f"{v}{unit}"


def scoreboard(s: dict) -> list[dict]:
    """The standard's lines, each pass, FAIL or not measured, from a summary."""
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
    f = s["falseDone"]
    L.append(_line("Acting", 'False "done"', f"{f['count']} in {f['judgedSteps']} judged steps; unconfirmed then spoke "
                   f"{f['unconfirmedThenSpoke']}", "0", None if not f["judgedSteps"] else f["count"] == 0))
    r = s["researchers"]
    L.append(_line("Acting", "One researcher per request", f"{r['wrongCount']} steps wrong", "0",
                   r["wrongCount"] == 0 if s["steps"] else None))
    L.append(_line("Acting", "Researcher started", f"max {_fmt(r['startMax'])} (n {r['n']})", f"≤ {S['researcherStartMax']} s",
                   None if not r["n"] else r["startMax"] <= S["researcherStartMax"]))
    st = s["status"]
    L.append(_line("Acting", "Status names the right researcher", f"{st['right']} of {st['n']}", "all",
                   None if not st["n"] else st["right"] == st["n"]))
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
    L.append(_line("Robustness", "Live view frame rate and delay unchanged",
                   f"fps {_fmt(lv['fps'], '')} vs {_fmt(lv['baselineFps'], '')}, delay p90 {_fmt(lv['delayP90'], ' ms')} vs "
                   f"{_fmt(lv['baselineDelayP90'], ' ms')}", f"fps ≥ {S['fpsKept']:.0%}, delay +≤ {S['delayAddedMaxS'] * 1000:.0f} ms",
                   None if fps_ok is None or delay_ok is None else fps_ok and delay_ok))
    L.append(_line("Robustness", "Runs with zero step failures", f"{s['runsClean']} of {s['runs']} ({s['stepFailures']} failed steps)",
                   "all runs", None if not s["runs"] else s["runsClean"] == s["runs"]))
    L.append(_line("Robustness", 'Never "restart the app"', str(s["restartAdvice"]), "0", s["restartAdvice"] == 0 if s["steps"] else None))
    pl = s["plain"]
    L.append(_line("Elegance", "One or two sentences", f"{_fmt(pl['share'], '')} of {pl['n']} lines", f"≥ {S['plainShare']:.0%}",
                   None if pl["share"] is None else pl["share"] >= S["plainShare"]))
    L.append(_line("Elegance", "Nothing on screen not asked for", str(s["unasked"]), "0", s["unasked"] == 0 if s["steps"] else None))
    return L


def meets(board: list[dict]) -> bool:
    """The standard holds only when every line passes: a line not measured is not a pass."""
    return all(line["result"] == "pass" for line in board)


def markdown(result: dict) -> str:
    """The short scoreboard: one table, then each failed step."""
    head = result.get("about", {})
    out = [f"# Live partner journey: {head.get('voice', '?')} ({head.get('talk', '?')}), plan {head.get('plan', '?')}", "",
           f"{result['summary']['runs']} run(s), {result['summary']['steps']} steps. Audio heard {head.get('audioFrom', 'in the page')}; "
           f"{head.get('evidence', 'recorded audio injected as the microphone')}. Not Will in a room.", "",
           f"**Standard met: {'yes' if meets(result['scoreboard']) else 'no'}**", "",
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
