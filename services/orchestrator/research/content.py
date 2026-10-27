"""A researcher's work as readable text: lessons, labels, captions, answers.

New outputs carry their own `content` (research_worker/results.py). Lessons written before
that only exist as HTML; `lesson_from_html` recovers their questions (and answers when they
can be found) from the page as text. The HTML is untrusted model output: it is only read
with regular expressions, never parsed by a browser or run.
"""
from __future__ import annotations

import html as htmllib
import re

from research_worker.results import step_kind

_DROP = re.compile(r"<(script|style|template|noscript|svg)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|div|section|article|li|ul|ol|h[1-6]|br|tr|details|summary|fieldset|legend|label|figure)\b[^>]*>", re.I)
_STEP_ID = re.compile(r"<(\w+)\b[^>]*\bid\s*=\s*[\"']?(?:q|question|step)-?(\d{1,2})[\"'\s>]", re.I)
_HEADING = re.compile(r"(?:^|\n)\s*(?:question|q)\s*(\d{1,2})\s*[.:)\-]?\s*", re.I)


def _text(fragment: str) -> str:
    """Visible text of an HTML fragment, one line per block."""
    fragment = _DROP.sub(" ", fragment)
    fragment = re.sub(r"<(select|textarea|button)\b[^>]*>.*?</\1\s*>", " ", fragment, flags=re.I | re.S)  # form controls
    fragment = _BLOCK.sub("\n", fragment)
    text = htmllib.unescape(_TAG.sub(" ", fragment))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _script_answers(page: str) -> dict[int, str]:
    """Answers the page keeps in its script as numbered strings ({1: "...", 2: "..."}), read
    as text only."""
    found: dict[int, str] = {}
    for script in re.findall(r"<script\b[^>]*>(.*?)</script\s*>", page, re.I | re.S):
        for key, value in re.findall(r"""(?:^|[{,\s])["']?q?(\d{1,2})["']?\s*:\s*(?:\{[^{}]*?(?:answer|explanation|feedback)\s*:\s*)?"((?:[^"\\\n]|\\.){20,})\"""",
                                     script, re.I):
            n = int(key)
            if n not in found:
                value = re.sub(r"\\u([0-9a-fA-F]{4})", lambda u: chr(int(u.group(1), 16)), value)
                found[n] = _clip(value.replace('\\"', '"').replace("\\n", " "), 500)
    return found


def _answer_in(block: str) -> str:
    """Text of an answer, explanation or feedback element inside a step, if it has any."""
    for match in re.finditer(r"<(\w+)\b[^>]*\b(?:class|id)\s*=\s*[\"'][^\"']*(answer|explanation|solution|feedback)[^\"']*[\"'][^>]*>(.*?)</\1\s*>",
                             block, re.I | re.S):
        if match.group(1).lower() in ("select", "input", "textarea", "label", "button"):
            continue
        text = _text(match.group(3))
        if len(text) > 3:
            return _clip(text, 500)
    details = re.search(r"<details\b[^>]*>(.*?)</details\s*>", block, re.I | re.S)
    if details:
        text = re.sub(r"^(show )?(the )?answer:?\s*", "", _text(re.sub(r"<summary\b.*?</summary\s*>", "", details.group(1), flags=re.I | re.S)), flags=re.I)
        if text:
            return _clip(text, 500)
    return ""


def _prompt_in(block: str, n: int) -> str:
    lines = _text(block).splitlines()
    lines = [re.sub(rf"^\s*(question|q|step)\s*{n}\s*[.:)\-]?\s*", "", line, flags=re.I) for line in lines]
    lines = [line for line in lines if line]
    return _clip(lines[0] if lines else "", 400)


def lesson_from_html(page: str) -> dict | None:
    """The lesson's title, key ideas and steps ({"id": "q1", "prompt", "answer", "hint"})
    from its HTML; None when no questions can be found."""
    if not page:
        return None
    body = page[page.lower().find("<body"):] if "<body" in page.lower() else page
    title_m = re.search(r"<h1\b[^>]*>(.*?)</h1\s*>", body, re.I | re.S) or re.search(r"<title\b[^>]*>(.*?)</title\s*>", page, re.I | re.S)
    title = _clip(_text(title_m.group(1)), 80) if title_m else "Lesson"
    answers = _script_answers(page)
    visible = _DROP.sub(" ", body)
    steps: list[dict] = []
    marks = []
    for m in _STEP_ID.finditer(visible):
        n = int(m.group(2))
        if n not in [k for k, _ in marks]:
            marks.append((n, m.start()))
    if marks:
        for i, (n, start) in enumerate(marks):
            end = marks[i + 1][1] if i + 1 < len(marks) else len(visible)
            block = visible[start:end]
            if i + 1 == len(marks):  # the last one ends where the next section starts
                tail = re.search(r"<(section|h2)\b", block[1:], re.I)
                block = block[: tail.start() + 1] if tail else block
            steps.append({"id": f"q{n}", "prompt": _prompt_in(block, n), "answer": _answer_in(block) or answers.get(n, ""),
                          "hint": ""})
    else:  # "Question 1 ..." headings in the text
        text = _text(visible)
        found = list(_HEADING.finditer(text))
        for i, m in enumerate(found):
            n = int(m.group(1))
            chunk = text[m.end(): found[i + 1].start() if i + 1 < len(found) else len(text)]
            lines = [line for line in chunk.splitlines() if line.strip()]
            answer = next((re.sub(r"^\s*answer\s*:?\s*", "", l, flags=re.I) for l in lines if re.match(r"^\s*answer\b", l, re.I)), "")
            if lines and all(s["id"] != f"q{n}" for s in steps):
                steps.append({"id": f"q{n}", "prompt": _clip(lines[0], 400), "answer": _clip(answer, 500) or answers.get(n, ""),
                              "hint": ""})
    steps = [s for s in steps if s["prompt"]]
    if not steps:
        return None
    ideas = [_clip(_text(f"{a} {b}"), 240) for a, b in
             re.findall(r"<p\b[^>]*>\s*<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)\s*>(.*?)</p\s*>", visible, re.I | re.S)][:6]
    return {"kind": "lesson", "title": title, "key_ideas": ideas, "steps": steps[:8]}


def render(content: dict | None, compact: bool = False) -> list[str]:
    """Readable lines for one output's content (compact: long texts clipped, for an overview;
    a single step or part is asked for in full)."""
    if not content:
        return []

    def clip(text: str, n: int) -> str:
        return _clip(text, n) if compact else text

    kind = content.get("kind")
    if kind == "lesson":
        lines = [f'Lesson "{content.get("title")}"']
        if content.get("key_ideas"):
            ideas = content["key_ideas"][:4] if compact else content["key_ideas"]
            lines.append("Key ideas: " + "; ".join(clip(i, 90) for i in ideas))
        steps = content.get("steps") or []
        for n, step in enumerate(steps, 1):
            kind = step_kind(step)
            how = (f", find it on the slide and click it: {clip(step.get('target') or '', 60)}" if kind == "find"
                   else ", evidence for the app's name for an outline" if kind == "evidence" else "")
            lines.append(f"{step['id']} (question {n} of {len(steps)}{how}): {clip(step['prompt'], 180)}")
            if step.get("answer"):
                lines.append(f"  answer: {clip(step['answer'], 180)}")
            if step.get("hint"):
                lines.append(f"  hint: {clip(step['hint'], 100)}")
        return lines
    if kind == "labels":
        lines = ["Labels on the photo:"]
        for item in content.get("items") or []:
            box = item.get("box")
            where = f" at x {box[0]:.2f}-{box[2]:.2f}, y {box[1]:.2f}-{box[3]:.2f} of the photo" if box else ""
            lines.append(f"- {item.get('name')}: {item.get('where') or 'no position given'}{where}")
        return lines
    if kind == "text":
        return [f"Made from: {content.get('text')}"]
    return []
