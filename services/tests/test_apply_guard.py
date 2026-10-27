"""Invariant 2 (ADR 0010): a result changes what is on screen only through apply.apply.

apply checks, immediately before writing, that the request's ticket is not superseded or cancelled,
that the target is still there, and (for new geometry on the live view) that the place is verified.
This test lists every direct write to the scene in the orchestrator, by function, with why it is not
a late result. A new direct writer fails it until it either goes through apply or is listed here with
its reason.

Kinds of reason:
- "apply": the write is the `write` given to apply.apply (it runs right after apply's checks).
- "at once": the user's (or the brain's) own edit, made synchronously when the request asks for it,
  before anything is awaited; it supersedes earlier requests through the scene's generations.
- "gated": a request's replacement of the app's earlier outlines, made only while its ticket is not
  superseded, bumping the generation so older requests' late results change nothing.
- "same step": data written onto an outline in the same synchronous step that apply just added it.
- "internal": the hub's re-anchor loop, the one listed exception (it verifies by template itself).
"""
import ast
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
WRITES = ("add", "update", "revise", "remove", "clear")

ALLOWED = {
    "agent.py:point.one": "apply (an existing outline's new name) / same step (the confidence of the outline just added)",
    "agent.py:point": "gated: the previous answer's outlines are replaced only while this request's ticket stands",
    "agent.py:identify.note": "apply (naming a click's outline, only while it is there)",
    "announcer.py:decided": "at once: the user accepted a correction",
    "app.py:_clear_view": "at once: a new day clears the view (and supersedes every request of the old day)",
    "arena.py:discard_current": "at once: a new trial or leaving arena mode drops the unvoted arms",
    "arena.py:vote": "at once: the user's vote keeps the winner's outlines",
    "brain/toolbox/finding.py:add_finding_tools.find": "gated: find replaces the app's earlier highlights",
    "brain/toolbox/population.py:add_correction_tool.correct_cells": "at once: the user's correction (M2.3) takes off "
                                                                      "the app's outlines of the cells it removes; the user's own stay",
    "brain/toolbox/scene.py:add_scene_tools.clear": "at once: the clear the request asked for (bumping generations)",
    "brain/toolbox/scene.py:add_scene_tools.name_outline": "at once: the user names an outline",
    "brain/toolbox/view.py:add_highlight.highlight": "same step: the label of the outline just made",
    "brain/toolbox/view.py:note_guess": "same step: the guess's note says what its check found, once it is done",
    "brain/toolbox/view.py:add_highlight._members": "same step: as its measured outline is added, a first guess that "
                                                     "holds gives it its name and how sure (M2.1), and the request's own "
                                                     "first guess is taken off; only one this request's look made, unnamed since",
    "brain/toolbox/view.py:add_highlight.replace_earlier": "gated: highlight replaces the app's earlier highlights "
                                                             "only while this request's ticket stands",
    "brain/loop.py:mark_first_guesses": "apply (a target a step measures, marked a first guess as it starts; "
                                        "only one this request's look made, never one the user named or confirmed)",
    "commands.py:after_change": "at once: switching taught labels off removes what they showed",
    "brain/toolbox/view.py:redraw_with_choice": "gated: an outline drawn again with the model a request chose replaces "
                                                "the app's earlier one only while this request's ticket stands (Slice 5)",
    "brain/toolbox/choosing.py:keep": "gated: a kept comparison arm replaces the app's earlier outline of that thing "
                                      "only while this request's ticket stands (Slice 5)",
    "commands.py:remove": "at once: the user removes an outline",
    "commands.py:name_drawn": "internal: naming a drawn outline failed, so it gets back the name it had (only while naming left it 'looking')",
    "commands.py:pin_note": "at once: the user pins a note",
    "dissect.py:_finish.write": "apply (an object's parts, each placed where its pixels are verified now)",
    "hub.py:_apply_reanchor": "internal: re-anchoring verifies each outline by its own template",
    "hub.py:_carry_over": "internal: a verified rejoin carries outlines anchored in the epoch it leaves (there "
                          "before it or landing after it) over by its translation, unverified until re-anchoring "
                          "finds their own pixels",
    "objtrack.py:_write": "apply (the follower's moves, with the tracking session as their generation)",
    "outline_check.py:_run.write": "apply (a repair, only if the outline is unchanged since the check began)",
    "outline_check.py:_unresolved.write": "apply (the check's doubt kept on the outline it checked, only while unchanged)",
    "taught.py:_match_object": "apply (the closest taught label, only while the outline is there)",
    "tools.py:add_outline.write": "apply (every new outline)",
    "tools.py:show_first_guess": "at once: the first-guess ring drawn as the request starts",
    "tools.py:resolve_first_guess": "at once: the first-guess ring removed when the answer comes",
    "tools.py:clear": "at once: the studio's clear",
}


def writers() -> set[str]:
    found = set()
    for path in sorted(ORCHESTRATOR.rglob("*.py")):
        rel = path.relative_to(ORCHESTRATOR).as_posix()

        class Visit(ast.NodeVisitor):
            def __init__(self):
                self.stack = []

            def visit_FunctionDef(self, node):
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node):
                if isinstance(node.func, ast.Attribute) and node.func.attr in WRITES \
                        and ast.unparse(node.func.value).endswith("scene"):
                    found.add(f"{rel}:{'.'.join(self.stack)}")
                self.generic_visit(node)

        Visit().visit(ast.parse(path.read_text()))
    return found


def test_every_direct_scene_writer_is_listed_with_why_it_is_not_a_late_result():
    unlisted = sorted(writers() - set(ALLOWED))
    assert not unlisted, "write through apply.apply, or list with a reason:\n" + "\n".join(unlisted)


def test_the_list_names_only_writers_that_exist():
    assert set(ALLOWED) <= writers(), sorted(set(ALLOWED) - writers())


def test_every_result_write_names_its_scope():
    """A write with no scope skipped the ticket's check, so a spoken cancel or a clear did not stop it
    (naming a click, the taught-label match, the outline check: scaffolding review, M1.1 item 4b).
    Every call of apply.apply names the scope it writes."""
    missing = []
    for path in sorted(ORCHESTRATOR.rglob("*.py")):
        rel = path.relative_to(ORCHESTRATOR).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "apply":
                scope = next((k.value for k in node.keywords if k.arg == "scope"), None)
                if scope is None or (isinstance(scope, ast.Constant) and scope.value is None):
                    missing.append(f"{rel}:{node.lineno}")
    assert not missing, "apply() without a scope:\n" + "\n".join(missing)
