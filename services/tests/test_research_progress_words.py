"""A researcher's progress is said in plain words (plan Slice 7, the user's 2026-10-11 test: a card read "Researcher
1: Read d1: characters 0–4233"). What its card and chip show is a step's `text`; an entry's id, a character range,
an exception, a file or a URL go in its `detail`, kept for Activity and How it was made. Every step call in the
research worker is checked here, so a new one cannot put them back in the text."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKER = sorted((ROOT / "research_worker").glob("*.py"))
# What never goes in the text: names whose values are ids, URLs, exceptions or code locations.
FORBIDDEN = {"entry_id", "url", "exc", "where", "sample", "__name__", "session_index_error"}


def step_calls():
    for path in WORKER:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "step" and node.args:
                yield path, node


def interpolated(node) -> set[str]:
    """The names the text interpolates (f-string parts, + joins, calls building it). `said(exc)` is the one way an
    exception's words reach the text: only the app's own plain ones (OverBudget, RunFailed; researcher.said)."""
    names = set()
    for part in ast.walk(node):
        if isinstance(part, ast.FormattedValue) or isinstance(part, ast.Call):
            for inner in ast.walk(part):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "said":
                    continue
                if isinstance(inner, ast.Name) and not _inside_said(part, inner):
                    names.add(inner.id)
                elif isinstance(inner, ast.Attribute) and not _inside_said(part, inner):
                    names.add(inner.attr)
    return names


def _inside_said(root, target) -> bool:
    for call in ast.walk(root):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "said":
            if any(inner is target for arg in call.args for inner in ast.walk(arg)):
                return True
    return False


def test_every_step_text_is_plain_its_detail_holds_ids_urls_and_errors():
    calls = list(step_calls())
    assert len(calls) > 30  # the worker's steps are all here
    leaks = []
    for path, call in calls:
        bad = interpolated(call.args[0]) & FORBIDDEN
        if bad:
            leaks.append(f"{path.name}:{call.lineno} puts {sorted(bad)} in the text")
        kind = call.args[1] if len(call.args) > 1 else None
        if isinstance(kind, ast.Constant) and kind.value == "debug":
            leaks.append(f"{path.name}:{call.lineno} says a debug step")
    assert not leaks, leaks


@pytest.mark.parametrize("words, said", [
    ("made 141 cell outlines: Cell outlines · Cellpose-SAM · 01:07, for Researcher 1 (e3)",
     "made 141 cell outlines: Cell outlines · Cellpose-SAM · 01:07, for Researcher 1"),
    ("measured what moves (m2, m3)", "measured what moves"),
    ("made a clearer picture: Clearer picture · 02:40", "made a clearer picture: Clearer picture · 02:40"),
])
def test_the_apps_words_on_the_card_leave_out_the_ids_the_researcher_reads_by(words, said):
    from research_worker.researcher import said_words

    assert said_words(words) == said


def test_a_viewer_notice_says_shown_only_once_the_studio_confirmed_it():
    from orchestrator.announcer import shown_words

    assert shown_words("Researcher 1", "viewer rendered the exact retained work") == "Researcher 1: its work is shown beside the view"
    assert "not confirmed" in shown_words("Researcher 1", "viewer did not confirm")
    assert "result viewer status" not in shown_words("Researcher 1", None)
