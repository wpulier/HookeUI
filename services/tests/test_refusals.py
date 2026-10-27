"""Never a dead end (docs/design/2026-10-07-the-partner.md §1c; scaffolding Amendment 4, item 2): every refusal
the user can meet carries a plain next step (`next=`), or says why it has none (`next=None` with a
`# no next: <reason>` comment on its line), as test_capabilities holds every tool to the catalogue.

A refusal is any raise of ToolFailed, ResearchRefused or a class derived from either, found from the
class tree in the code itself. A step passed on from a wrapped exception (`getattr(exc, "next", None)`)
counts only with `# next: passed on` on its line: the wrapped type must be one that carries one."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "orchestrator"
BASES = {"ToolFailed", "ResearchRefused"}
# Files whose refusals are still being given a next step by the PR changing them; nothing is added to it.
PENDING: set[str] = set()


def _name(node) -> str:
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""


def refusal_classes() -> set[str]:
    """ToolFailed, ResearchRefused and every class derived from them, from the class tree in orchestrator/."""
    parents: dict[str, set[str]] = {}
    for path in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef):
                parents.setdefault(node.name, set()).update(_name(b) for b in node.bases)
    found = set(BASES)
    while True:
        more = {cls for cls, bases in parents.items() if bases & found} - found
        if not more:
            return found
        found |= more


def refusals():
    classes = refusal_classes()
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        lines = source.split("\n")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and _name(node.exc.func) in classes:
                yield rel, node, "\n".join(lines[node.lineno - 1:node.end_lineno])


def test_the_refusal_classes_are_found_from_the_class_tree():
    classes = refusal_classes()
    assert {"ToolError", "Unavailable", "CaptureGone", "Refused", "Ambiguous"} <= classes


def test_every_refusal_says_what_to_do_next_or_why_it_cannot():
    missing, unexplained, unpassed = [], [], []
    for rel, node, text in refusals():
        if rel in PENDING:
            continue
        given = [k for k in node.exc.keywords if k.arg == "next"]
        if not given:
            missing.append(f"{rel}:{node.lineno}")
            continue
        value = given[0].value
        if isinstance(value, ast.Constant) and value.value is None and "# no next:" not in text:
            unexplained.append(f"{rel}:{node.lineno}")
        if isinstance(value, ast.Call) and _name(value.func) == "getattr" and "# next: passed on" not in text:
            unpassed.append(f"{rel}:{node.lineno}")
    assert not missing, f"refusals with no next step (give next=..., or next=None  # no next: why): {missing}"
    assert not unexplained, f"next=None needs '# no next: <why>' on its line: {unexplained}"
    assert not unpassed, f"a passed-on next needs '# next: passed on' (and a wrapped type that carries one): {unpassed}"


def test_a_next_step_is_a_plain_sentence_for_the_user():
    for rel, node, _ in refusals():
        for k in node.exc.keywords:
            if k.arg == "next" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
                step = k.value.value
                assert step and step == step.strip() and step[0].islower() and not step.endswith("."), (rel, node.lineno, step)
                assert len(step) <= 90, (rel, node.lineno, step)


def test_the_pending_list_only_shrinks():
    assert not PENDING
