"""What a researcher made, in readable form, and the result it leaves when it stops before writing up.

Every output carries `content` the app's brain can read (orchestrator/research/manager.py
Session.readable):
  lesson        {"kind": "lesson", "title", "key_ideas": [str],
                 "steps": [{"id": "q1", "prompt", "answer", "hint"}]}; a step that is not a plain
                question has a "kind" (STEP_KINDS): "find" (found on the user's own slide and
                clicked; "target": what to find) or "evidence" (what would confirm or refute the
                app's name for an outline)
  annotated     {"kind": "labels", "space": "picture", "unit": "fraction",
                 "items": [{"name", "where", "box": [x0, y0, x1, y1] (0-1 of the picture) or None}]}
                (the boxes are in the picture's own space, as fractions: orchestrator/spaces.py)
  illustration  {"kind": "text", "text": the prompt or description used, and the caption}
  video
"""
from __future__ import annotations

import re


def _text(value, limit: int) -> str:
    """A preview of one line, ending in "…" when it was cut (the content itself is kept whole: _whole)."""
    line = " ".join(str(value or "").split())
    return line if len(line) <= limit else line[: max(limit - 1, 0)].rstrip() + "…"


def _whole(value) -> str:
    """One line of content as the model wrote it, whitespace collapsed, never cut."""
    return " ".join(str(value or "").split())


def comparison_tables(value) -> list[dict]:
    """Validate complete model-authored tables. Never silently cut scientific rows or cells."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 3:
        raise ValueError("tables must be a list of at most 3 tables")
    def text(value, limit):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"table text must be nonempty and at most {limit} characters")
        return " ".join(value.split())
    out = []
    for table in value:
        if not isinstance(table, dict):
            raise ValueError("each table must have title, columns and rows")
        columns, rows = table.get('columns'), table.get('rows')
        if not isinstance(columns, list) or not 1 <= len(columns) <= 12:
            raise ValueError("a table needs 1 to 12 columns")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
            raise ValueError("a table needs 1 to 50 rows; split larger comparisons explicitly")
        if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            raise ValueError("each table row must contain one cell per column")
        out.append({'title': text(table.get('title'), 160), 'columns': [text(x, 120) for x in columns],
                    'rows': [[text(x, 400) for x in row] for row in rows]})
    return out


def full_report(value) -> dict | None:
    """Retain scientific prose verbatim within explicit limits; never truncate it."""
    if value is None:
        return None
    fields = ('methods', 'observations', 'measurements', 'limitations')
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('report requires methods, observations, measurements and limitations')
    out = {}
    for key in fields:
        text = value[key]
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise ValueError(f'report.{key} must contain 1 to 16000 characters; state unavailable evidence explicitly')
        out[key] = text
    return out


def box01(value) -> list[float] | None:
    """[x0, y0, x1, y1] as fractions of the photo, or None when it is not a usable box."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if not all(0.0 <= v <= 1.0 for v in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        return None
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def labels_content(labels: list[dict]) -> dict:
    # The boxes' space and unit are stated (invariant 14): the picture's own, as fractions of its width and height.
    # The research process does not import the app, so these are spaces.PICTURE and "fraction" (tests/test_spaces.py).
    return {"kind": "labels", "space": "picture", "unit": "fraction", "items": [{"name": _text(l.get("name"), 60), "where": _text(l.get("where"), 160),
                                         "box": box01(l.get("box"))} for l in labels]}


def text_content(text: str, caption: str) -> dict:
    return {"kind": "text", "text": _text(f"{_text(text, 1200)} ({caption})" if text else caption, 1500)}


QUESTION = re.compile(r"^\s*(?:q(?:uestion)?\s*\d+|\d+)\s*[.:)\-]\s*(.+)$", re.I)
ANSWER = re.compile(r"^\s*(?:a(?:nswer)?|ans)\s*\d*\s*[.:)\-]\s*(.+)$", re.I)
HINT = re.compile(r"^\s*hint\s*\d*\s*[.:)\-]\s*(.+)$", re.I)


def steps_from_outline(outline: str) -> tuple[list[str], list[dict]]:
    """Key ideas and quiz questions from a free-text outline ("Q1: ...", "Answer: ...").
    Used when the model gives make_lesson an outline instead of structured questions."""
    ideas, steps, in_quiz = [], [], False
    for raw in (outline or "").splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if not line:
            continue
        if re.match(r"^(quiz|questions?)\b[^:]*:?\s*$", line, re.I):
            in_quiz = True
            continue
        answer, hint = ANSWER.match(line), HINT.match(line)
        if steps and answer:
            steps[-1]["answer"] = _text(answer.group(1), 400)
            continue
        if steps and hint:
            steps[-1]["hint"] = _text(hint.group(1), 200)
            continue
        question = QUESTION.match(line)
        if question and (in_quiz or re.match(r"^\s*q", line, re.I) or line.rstrip().endswith("?")):
            text = question.group(1)
            inline = re.split(r"\s*\b(?:answer|ans)\s*:\s*", text, maxsplit=1, flags=re.I)
            steps.append({"id": f"q{len(steps) + 1}", "prompt": _text(inline[0], 400),
                          "answer": _text(inline[1], 400) if len(inline) > 1 else "", "hint": ""})
            continue
        if not in_quiz and len(ideas) < 8:
            ideas.append(_text(line, 240))
    return ideas, steps


STEP_KINDS = ("question", "find", "evidence")  # "question" is the default and is not written


def step_kind(step: dict | None) -> str:
    """What kind of step this is: "question" (answered in words), "find" (found on the user's
    own slide and clicked) or "evidence" (what would confirm or refute the app's name for an
    outline). Steps written before kinds are questions."""
    kind = str((step or {}).get("kind") or "question").strip().lower()
    return kind if kind in STEP_KINDS else "question"


def make_step(sid: str, raw: dict, old: dict | None = None) -> dict:
    """One lesson step from what a model wrote (`old`: the step it edits, for what it leaves out).
    A plain question keeps the shape steps always had; a find or evidence step adds its kind,
    and a find step what to find."""
    old = old or {}

    def field(name: str) -> str:  # whole: a lesson's words are kept as the model wrote them
        return _whole(raw.get(name) if raw.get(name) is not None else old.get(name))

    step = {"id": sid, "prompt": _whole(raw.get("question") or raw.get("prompt") or old.get("prompt")),
            "answer": field("answer"), "hint": field("hint")}
    kind = step_kind(raw if raw.get("kind") is not None else old)
    if kind != "question":
        step["kind"] = kind
    if kind == "find":
        step["target"] = field("target")
    return step


LESSON_MAX = 8  # key ideas, and steps, one lesson page holds (make_lesson refuses more, saying so)


def lesson_content(title: str, key_ideas, questions, outline: str = "") -> dict:
    ideas = [_whole(i) for i in key_ideas or [] if _whole(i)][:LESSON_MAX]
    steps = []
    for q in questions or []:
        if isinstance(q, dict) and _whole(q.get("question") or q.get("prompt")):
            steps.append(make_step(f"q{len(steps) + 1}", q))
    if outline and (not ideas or not steps):
        more_ideas, more_steps = steps_from_outline(outline)
        ideas = ideas or more_ideas
        steps = steps or more_steps
    return {"kind": "lesson", "title": _whole(title), "key_ideas": ideas, "steps": steps[:LESSON_MAX]}


def content_line(content: dict | None) -> str:
    """A few words about an output's content (for a partial result's summary)."""
    if not content:
        return ""
    kind = content.get("kind")
    if kind == "lesson":
        n = len(content.get("steps") or [])
        return f'a lesson "{content.get("title")}"' + (f" with {n} questions" if n else "")
    if kind == "labels":
        return "labels: " + ", ".join(i["name"] for i in content.get("items") or [] if i.get("name"))
    if kind == "text":
        return _text(content.get("text"), 200)
    return ""


OPERATION_NOUNS = {"motion": "motion measurement", "segment": "cell segmentation", "enhance": "clearer picture"}


def partial_result(brief: str, label: str, outputs: list[dict], status: str, *, findings: list[dict] = (),
                   searched: list[str] = (), operations: list[dict] = ()) -> dict:
    """The result of a researcher that stopped before writing up (stopped, out of money, made to write up and could
    not): what it found, made and measured is kept (status "partial") and described from itself: the operations it had
    the app run with the app's own words and numbers, its recorded findings whole with their sources, its outputs, and
    the pages its searches returned (never claimed as cited)."""
    # "timeout" is only the app's watchdog now (no clock stops a researcher): its process stopped answering.
    why = ("stopped answering the app" if status == "timeout" else "reached its spending limit" if status == "over_budget"
           else "stopped")
    made = [o for o in outputs if o.get("path")]
    lines = [_text(o.get("caption"), 160) + (f": {content_line(o.get('content'))}" if content_line(o.get("content")) else "")
             for o in made]
    ran = [o for o in operations if isinstance(o, dict) and o.get("id")]
    counts: dict[str, list[str]] = {}
    for o in ran:
        counts.setdefault(str(o.get("operation") or "operation"), []).append(str(o["id"]))
    measured = [f"{len(ids)} {OPERATION_NOUNS.get(op, op)}{'s' if len(ids) != 1 else ''} ({', '.join(ids)})"
                for op, ids in counts.items()]
    # Each one with the app's own words: its numbers are the work (never a recap a model wrote).
    ran_lines = [f"{o.get('title') or o['id']} ({o['id']}): {o.get('words') or 'made'}" for o in ran]
    found = [f for f in findings if isinstance(f, dict) and str(f.get("text") or "").strip()]
    kinds = {"html": "a lesson", "video": "a video", "image": "a picture"}
    kept = measured + ([f"{len(found)} finding{'s' if len(found) != 1 else ''}"] if found else []) + lines
    if not kept and searched:
        kept = [f"the {len(set(searched))} pages its searches returned"]
    first = (measured[0] if measured else kinds.get(made[0].get("kind"), "something") if made else
             f"{len(found)} finding{'s' if len(found) != 1 else ''}" if found else "no write-up")
    cited = list(dict.fromkeys(u for f in found for u in f.get("sources") or [] if isinstance(u, str) and u.startswith("http")))
    return {
        "title": _text(label or brief, 80) or "Research",
        "summary": f"It {why} before writing up, but kept " + ("; ".join(kept) if kept else "nothing") + ".",
        "one_liner": f"It {why} before writing up; it kept {first}.",
        "significance": "low", "details": ran_lines + lines, "look_for": "", "confidence": None,
        "sources": cited, "searched": list(dict.fromkeys(searched)), "findings": [dict(f) for f in found],
        **({"operations": [dict(o) for o in ran]} if ran else {}),
        "refs": [], "ref_candidates": [], "partial": True,
    }
