"""Score a voice journeys run against the experience contract (docs/bench/voice-journeys.md).

Timings come from the stack's own logs: the conversation log (every spoken fragment and every
outline with its time, to the millisecond) and the interaction log (hand-offs, unprompted speech,
the voice's notes, hands' judgements), joined to the player's marks (when each step's words
started and ended, on the same clock). What can't be timed (was a line filler, a repeat, a promise
kept, the answer right for the picture) is judged by a model from another family than the ones
judged (GPT-6.1 Sol judging Gemini's eyes and GPT-Live's words), given the picture the eyes saw.

  cd services && .venv/bin/python -m bench.voice_journeys.score --out /tmp/vj-main --marks /tmp/vj-main/marks.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

VIEW = ("view", "plural")
JUDGE = "openai:gpt-6.1-sol@low"

JUDGE_PROMPT = """You are scoring a live voice assistant for a microscope against a strict standard. The
assistant is one mind: its eyes describe the view, its voice talks, its hands outline things on
screen. Image 1 (if given) is what its eyes saw around when the user spoke.

The user said (kind of moment: {kind}): "{say}"
{earlier}
What the voice said afterwards, in order (seconds after the user's last word):
{lines}
What the app did on screen afterwards (seconds after the user's last word):
{actions}

Judge, from these facts only:
- Label every voice line: "filler" (acknowledges, stalls or narrates checking/looking/waiting and says
  nothing about the specimen or the result: "one sec", "let me look", "okay, sure", "still on it"),
  "content" (says something about what is there, or the result, or what is being done with substance),
  "promise" (says a step will happen: "I'll point it out"), "status" (reports something done or not done),
  "repeat" (says again what an earlier line already said), or "other".
- promises_unkept: promises no action above carried out by the end.
- false_done: anything said to be done, shown or found that the actions above do not show.
- answered: true if, by the end, the user got what they asked for in words (and on screen when asked).
- correct_for_now: for a question about the view, "yes", "partly" or "no" against image 1; "n/a" otherwise
  or with no image.
- pointing_matches: for a question about something in view, true if an outline above is on the thing
  talked about (by its label), false if not, null if no outline or not such a question.
- side_talk_treated_as_request: when the kind is "side" (said to an audience, not to the assistant),
  true if the assistant acted on it or answered it as a request; else false. null for other kinds.
- memory_consistent: for kind "typed", true if the answer agrees with what the voice said earlier
  (given above); else null.
- note_answers: for a question about the view, true if the note the voice held when the user stopped
  talking (given below) holds what is needed to answer it correctly for image 1, false if not, null
  if no note is given or not such a question.

The note the voice held when the user stopped talking:
{note}

Reply with JSON only:
{{"labels": ["filler"|"content"|"promise"|"status"|"repeat"|"other", ...one per voice line],
 "promises_unkept": [], "false_done": [], "answered": true, "correct_for_now": "yes",
 "pointing_matches": null, "side_talk_treated_as_request": null, "memory_consistent": null,
 "note_answers": null, "why": "one sentence"}}"""


def read_jsonl(path: Path) -> list[dict]:
    """Records with "t" (epoch seconds): logs from before records carried it are timed by "at", to the second."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r.get("t"), (int, float)) and isinstance(r.get("at"), str):
            try:
                r["t"] = time.mktime(time.strptime(r["at"][:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                pass
        out.append(r)
    return out


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))], 2)


def spoken(conv: list[dict], start: float, end: float) -> list[dict]:
    """The voice's lines in [start, end): one per lineId, with its first fragment's time and final text."""
    lines: dict[str, dict] = {}
    for r in conv:
        at = r.get("at")
        if r.get("type") != "chat" or r.get("role") != "assistant" or not r.get("voice"):
            continue
        if not isinstance(at, (int, float)) or not start <= at < end:
            continue
        key = r.get("lineId") or f"{at}"
        line = lines.setdefault(key, {"first": at, "text": ""})
        line["first"] = min(line["first"], at)
        line["text"] = r.get("text") or line["text"]
    return sorted(lines.values(), key=lambda l: l["first"])


def typed_answer(conv: list[dict], start: float, end: float) -> list[dict]:
    return [{"first": r["at"], "text": r.get("text", "")} for r in conv
            if r.get("type") == "chat" and r.get("role") == "assistant" and not r.get("voice")
            and isinstance(r.get("at"), (int, float)) and start <= r["at"] < end]


def actions(conv: list[dict], start: float, end: float) -> list[dict]:
    out = []
    for r in conv:
        at = r.get("at")
        if r.get("type") != "chat-action" or not isinstance(at, (int, float)) or not start <= at < end:
            continue
        outlines = [o for o in r.get("outputs") or [] if o.get("kind") == "outline"]
        out.append({"at": min([o.get("created_at") or at for o in outlines] or [at]), "tool": r.get("tool"),
                    "ok": r.get("ok"), "summary": r.get("summary", ""),
                    "outline": bool(outlines) or (r.get("tool") == "highlight" and r.get("ok") is True)})
    return sorted(out, key=lambda a: a["at"])


def state(ok) -> str:
    """A step's row: done, failed, or still running (ok None: a late step not settled yet)."""
    return "ok" if ok is True else "failed" if ok is False else "still running"


def picture(logs: Path, inter: list[dict], end: float) -> bytes | None:
    """What the eyes saw nearest after the user spoke (else just before)."""
    sights = [r for r in inter if r.get("kind") == "sight" and r.get("image") and isinstance(r.get("t"), (int, float))]
    if not sights:
        return None
    after = [r for r in sights if r["t"] >= end - 1]
    pick = min(after, key=lambda r: r["t"]) if after else max(sights, key=lambda r: r["t"])
    path = logs / pick["image"]
    return path.read_bytes() if path.exists() else None


async def judge(model, step: dict, lines: list[dict], acts: list[dict], image: bytes | None, earlier: str,
                note: str = "") -> dict:
    end = step["end"]
    prompt = JUDGE_PROMPT.format(
        kind=step["kind"], say=step["say"], earlier=earlier, note=note[:2000] or "(none logged)",
        lines="\n".join(f"{i + 1}. (+{l['first'] - end:.1f} s) {l['text']}" for i, l in enumerate(lines)) or "(nothing)",
        actions="\n".join(f"- (+{a['at'] - end:.1f} s) {a['tool']}: {state(a['ok'])}: {a['summary']}"
                          for a in acts) or "(nothing)")
    result = await model.ask([image] if image else [], prompt, max_output_tokens=1200)
    return result.data or {"error": result.error or result.raw_text[:200]}


async def score(out: Path, marks: list[dict], use_judge: bool) -> dict:
    logs = out / "home" / "logs"
    conv, inter = read_jsonl(logs / "conversation.jsonl"), read_jsonl(logs / "interactions.jsonl")
    model = None
    if use_judge:
        from orchestrator.keys import load_keys
        from orchestrator.vision import make_vision
        load_keys()
        model = make_vision(JUDGE)
    rows, earlier_spots = [], ""
    session = (marks[0]["start"] - 5, marks[-1]["end"] + 40) if marks else (0, 0)
    for i, step in enumerate(marks):
        end = step["end"]
        until = marks[i + 1]["start"] if i + 1 < len(marks) else end + 40
        lines = typed_answer(conv, step["start"], until) if step["kind"] == "typed" else spoken(conv, step["start"] + 0.3, until)
        acts = actions(conv, step["start"], until)
        handoffs = [r for r in inter if r.get("kind") == "voice-handoff" and step["start"] <= (r.get("t") or 0) < until]
        notes = [r for r in inter if r.get("kind") == "voice-note" and (r.get("t") or 0) <= end]
        note = notes[-1] if notes else None
        row = {"id": step["id"], "kind": step["kind"], "say": step["say"],
               "firstWordsS": round(lines[0]["first"] - end, 2) if lines else None,
               "delegated": bool(handoffs), "lines": [l["text"] for l in lines],
               "pointS": next((round(a["at"] - end, 2) for a in acts if a["outline"] and a["at"] >= step["start"]), None),
               "actions": [f"{a['tool']}: {state(a['ok'])}: {a['summary']}" for a in acts],
               "noteAgeS": (round(end - note["t"] + float(note.get("lookAgeS") or 0), 1) if note else None)}
        if model is not None:
            earlier = (f"Earlier the voice had said about the dark spots: {earlier_spots}" if step["kind"] == "typed" and earlier_spots else "")
            verdict = await judge(model, step, lines, acts, picture(logs, inter, end), earlier,
                                  note=(note or {}).get("text", "") if step["kind"] in VIEW else "")
            labels = verdict.get("labels") or []
            row["judge"] = verdict
            content = next((j for j, label in enumerate(labels) if label in ("content", "status")), None)
            row["firstContentS"] = round(lines[content]["first"] - end, 2) if content is not None and content < len(lines) else None
            row["filler"] = sum(1 for label in labels if label == "filler")
            row["repeats"] = sum(1 for label in labels if label == "repeat")
            row["ackOnly"] = bool(lines) and all(label == "filler" for label in labels) and not verdict.get("answered")
        if step["id"] == "dark-spots":
            earlier_spots = " ".join(row["lines"])
        rows.append(row)
    unprompted = [r.get("text") for r in inter if r.get("kind") == "voice-unprompted" and session[0] <= (r.get("t") or 0) <= session[1]]
    view = [r for r in rows if r["kind"] in VIEW]
    judged = [r for r in rows if "judge" in r]
    summary = {
        "firstWords": {"p50": pct([r["firstWordsS"] for r in view if r["firstWordsS"] is not None], 50),
                       "p90": pct([r["firstWordsS"] for r in view if r["firstWordsS"] is not None], 90)},
        "firstContent": {"p50": pct([r["firstContentS"] for r in view if r.get("firstContentS") is not None], 50),
                         "p90": pct([r["firstContentS"] for r in view if r.get("firstContentS") is not None], 90)},
        "pointing": {"p50": pct([r["pointS"] for r in view if r["pointS"] is not None], 50),
                     "p90": pct([r["pointS"] for r in view if r["pointS"] is not None], 90),
                     "pointed": sum(1 for r in view if r["pointS"] is not None), "of": len(view)},
        "delegatedViewQuestions": sum(1 for r in view if r["delegated"]),
        "filler": sum(r.get("filler", 0) for r in rows),
        "repeats": sum(r.get("repeats", 0) for r in rows),
        "ackOnlyThenSilence": sum(1 for r in rows if r.get("ackOnly")),
        "promisesUnkept": sum(len(r["judge"].get("promises_unkept") or []) for r in judged),
        "falseDone": sum(len(r["judge"].get("false_done") or []) for r in judged),
        "correctForNow": {k: sum(1 for r in judged if r["kind"] in VIEW and r["judge"].get("correct_for_now") == k)
                          for k in ("yes", "partly", "no")},
        "sideTalkActedOn": sum(1 for r in judged if r["judge"].get("side_talk_treated_as_request")),
        "memoryConsistent": next((r["judge"].get("memory_consistent") for r in judged if r["kind"] == "typed"), None),
        "notesAnswer": {"yes": sum(1 for r in judged if r["kind"] in VIEW and r["judge"].get("note_answers") is True),
                        "of": sum(1 for r in judged if r["kind"] in VIEW and r["judge"].get("note_answers") is not None)},
        "unprompted": unprompted,
        "noteAgeAtEnd": {"p50": pct([r["noteAgeS"] for r in view if r["noteAgeS"] is not None], 50),
                         "p90": pct([r["noteAgeS"] for r in view if r["noteAgeS"] is not None], 90)},
    }
    return {"summary": summary, "steps": rows}


def report(result: dict) -> str:
    s = result["summary"]
    out = ["# Voice journeys", "", "| Contract row | Result |", "|---|---|",
           f"| First words (view questions) | p50 {s['firstWords']['p50']} s, p90 {s['firstWords']['p90']} s |",
           f"| First content | p50 {s['firstContent']['p50']} s, p90 {s['firstContent']['p90']} s |",
           f"| Filler lines | {s['filler']} |",
           f"| Correct for now | {s['correctForNow']} |",
           f"| Note age at end of speech | p50 {s['noteAgeAtEnd']['p50']} s, p90 {s['noteAgeAtEnd']['p90']} s |",
           f"| The note held the answer | {s['notesAnswer']['yes']} of {s['notesAnswer']['of']} |",
           f"| Pointing | {s['pointing']['pointed']} of {s['pointing']['of']} pointed; p50 {s['pointing']['p50']} s, p90 {s['pointing']['p90']} s |",
           f"| Repeats | {s['repeats']} |", f"| Acknowledgement only, then silence | {s['ackOnlyThenSilence']} |",
           f"| Promises not kept / done without proof | {s['promisesUnkept']} / {s['falseDone']} |",
           f"| Side talk acted on | {s['sideTalkActedOn']} |", f"| Memory both ways | {s['memoryConsistent']} |",
           f"| Unprompted lines | {len(s['unprompted'])} {s['unprompted'][:5]} |",
           f"| View questions handed over | {s['delegatedViewQuestions']} |", "", "## Steps", ""]
    for r in result["steps"]:
        out.append(f"**{r['id']}** ({r['kind']}) \"{r['say']}\": first words {r['firstWordsS']} s, first content "
                   f"{r.get('firstContentS')} s, point {r['pointS']} s, handed over {r['delegated']}")
        for i, text in enumerate(r["lines"]):
            label = (r.get("judge", {}).get("labels") or [None] * 99)[i] if i < len(r.get("judge", {}).get("labels") or []) else ""
            out.append(f"  - [{label}] {text}")
        for a in r["actions"]:
            out.append(f"  - action: {a}")
        if "judge" in r:
            out.append(f"  - judge: {r['judge'].get('why', r['judge'].get('error', ''))}")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="the stack's scratch folder (stack.py up --out)")
    parser.add_argument("--marks", required=True, help="the player's marks (window.__bench.state.marks, saved as JSON)")
    parser.add_argument("--no-judge", action="store_true", help="timings only, no model calls")
    args = parser.parse_args()
    out = Path(args.out)
    marks = json.loads(Path(args.marks).read_text())
    marks = marks.get("marks", marks) if isinstance(marks, dict) else marks
    if marks and isinstance(marks[0], list):  # compact [id, start, end]: the rest from the steps
        steps = {s["id"]: s for s in json.loads((Path(__file__).parent / "steps.json").read_text())["steps"]}
        marks = [{"id": i, "kind": steps[i]["kind"], "say": steps[i]["say"], "start": a, "end": b} for i, a, b in marks]
    for m in marks:  # the player keeps epoch milliseconds
        if m["start"] > 1e11:
            m["start"], m["end"] = m["start"] / 1000, m["end"] / 1000
    result = asyncio.run(score(out, marks, not args.no_judge))
    (out / "score.json").write_text(json.dumps(result, indent=1))
    text = report(result)
    (out / "score.md").write_text(text)
    print(text)


if __name__ == "__main__":
    main()
