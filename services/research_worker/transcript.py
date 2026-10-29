"""A run's conversation with its model, kept round by round (the demo of 2026-10-11: Researcher 3 never called `run`,
and why could not be read, since nothing kept what the model was offered and said).

One line per round in the file the app names (spec "transcript": research/transcripts/<day>/<rid>.jsonl, retained
research evidence, docs/saves.md; none unless a path is supplied): what that round's request added (the brief and pictures at first, then the tool
results, the user's notes and what the app told it), the tools it was offered and how it was asked to choose, and the
model's whole reply with its tool calls and usage. The instructions, sent every round, are kept only as their hash and
length; pictures only as their size. Web pages and tool results inside are untrusted data, kept as they came and never
read back into any prompt: the app never reads this file. A line is at most LINE_MAX bytes: past it, its long strings
are cut, each marked, since this is a log and not the work (the work is the researcher's outputs, steps and finding).
"""
from __future__ import annotations

import enum
import hashlib
import json
import time
from pathlib import Path
from typing import Any

LINE_MAX = 64 * 1024  # bytes of one round's line
CUTS = (16384, 4096, 1024, 256)  # each long string cut to this many characters, in turn, until the line fits
HEAD = ("at", "rid", "runId", "round", "engine", "model", "effort", "instructions", "tools", "toolChoice", "final")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def plain(value: Any) -> Any:
    """`value` as JSON: an SDK object as its fields, a picture (bytes, a data URL) as its size."""
    if hasattr(value, "model_dump"):  # the OpenAI and google-genai SDKs' objects (pydantic)
        try:
            value = value.model_dump(exclude_none=True)
        except Exception:  # noqa: BLE001 - a log never fails a run
            return str(value)
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, str) and value.startswith("data:"):
        return f"<picture: a data URL of {len(value)} characters>"
    if isinstance(value, enum.Enum):  # Gemini's finish reasons and the like: their value
        return plain(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "__dict__"):  # a plain object (an SDK's without pydantic, a test's stand-in): its public fields
        return {k: plain(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def _cut(value: Any, keep: int) -> Any:
    if isinstance(value, dict):
        return {k: _cut(v, keep) for k, v in value.items()}
    if isinstance(value, list):
        return [_cut(v, keep) for v in value]
    if isinstance(value, str) and len(value) > keep:
        return value[:keep] + f"… [cut: {len(value) - keep} more characters]"
    return value


def line(record: dict) -> str:
    """One round as a line of at most LINE_MAX bytes: whole when it fits, else with its long strings cut (marked, and
    `cut` says so), else only its head with why."""
    text = json.dumps(record, ensure_ascii=False, default=str)
    size = len(text.encode())
    if size <= LINE_MAX:
        return text
    for keep in CUTS:
        text = json.dumps({**_cut(record, keep), "cut": f"strings past {keep} characters cut: the round was {size} bytes"},
                          ensure_ascii=False, default=str)
        if len(text.encode()) <= LINE_MAX:
            return text
    return json.dumps({**{k: record[k] for k in HEAD if k in record},
                       "cut": f"the round was {size} bytes, past {LINE_MAX} even with its strings cut: only its head is kept"},
                      ensure_ascii=False, default=str)


def append(path: str | None, record: dict) -> None:
    """Append one round to the transcript at `path` (none: nothing kept). A log: it never fails a run."""
    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as f:
            f.write(line({"at": round(time.time(), 2), **record}) + "\n")
    except Exception:  # noqa: BLE001 - a full disk or an odd value costs the log line, never the run
        pass
