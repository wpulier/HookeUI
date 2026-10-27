"""Invariant 1 (ADR 0010): slow code never looks a frame up again by id after it has waited.

The frame ring keeps a frame's pixels for 4 s (frames.py) and its facts for 30 s (hub.INFOS_KEPT); a
tool that waits for a model and then asks for its frame by id may find nothing (2026-10-06: "Find
failed: 'NoneType' object has no attribute 'jpeg'"). Slow code takes a Capture (capture.py) before it
waits and keeps it.

This guard reads every async function in the orchestrator: after its first `await`, no call may look a
frame up by id (`source.get(`, `infos[...]`, `infos.get(`, `hub.gray(`). The allow-list says why
each remaining one is safe; it only shrinks.

Worker-thread functions the guard cannot see (no await in them, but they run beside the loop that
fills the ring) take a frame's bytes and facts together in one step instead:
`magnifier.sharpest_frame` (and so `crop_jpeg`) returns a Capture with the bytes it scored;
`taught.crop_source` takes the outline's origin capture, or reads its bytes once on the loop.
"""
import ast
from pathlib import Path

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"

# module:function -> why it is safe
ALLOWED = {
    # Watching on: each pass waits for the next frame, then reads the newest one at once (choosing
    # and reading with no wait in between), keeping its bytes with its facts.
    "brain/toolbox/motion.py:gather": "reads the newest frame right after choosing it, with no wait in between",
    # The hub's own frame path: each pass takes the newest frame and reads it at once.
    "hub.py:_candidate_loop": "the frame path: decodes the newest frame right after choosing it",
    "hub.py:_reanchor_loop": "the frame path: decodes the newest frame right after choosing it",
    "hub.py:_on_frame": "the frame intake publishes the frames it was just handed, with their facts",
}


def lookups(node: ast.AST, helpers: frozenset = frozenset()) -> list[tuple[int, str]]:
    """Frame lookups by id in `node`: the ring (`source.get`), the facts (`infos`), the hub's decode
    (`hub.gray`), the capture store by id (`captures(...).get`, or a name bound to `captures(...)`),
    and calls (`self.name(...)` or `name(...)`) of the synchronous `helpers` of the same module that
    make one of those."""
    stores = {t.id for sub in ast.walk(node) if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call)
              and ast.unparse(sub.value.func).endswith("captures") for t in sub.targets if isinstance(t, ast.Name)}
    found = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            owner = ast.unparse(sub.func.value)
            if sub.func.attr == "get" and (owner.endswith("source") or owner.endswith("infos")):
                found.append((sub.lineno, ast.unparse(sub)[:80]))
            if sub.func.attr == "gray" and owner.endswith("hub"):
                found.append((sub.lineno, ast.unparse(sub)[:80]))
            if sub.func.attr == "get" and (owner in stores or (isinstance(sub.func.value, ast.Call)
                                                                and ast.unparse(sub.func.value.func).endswith("captures"))):
                found.append((sub.lineno, ast.unparse(sub)[:80]))
            if sub.func.attr in helpers and owner == "self":
                found.append((sub.lineno, ast.unparse(sub)[:80]))
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in helpers:
            found.append((sub.lineno, ast.unparse(sub)[:80]))
        if isinstance(sub, ast.Subscript) and ast.unparse(sub.value).endswith("infos"):
            found.append((sub.lineno, ast.unparse(sub)[:80]))
    return found


def sync_helpers(tree: ast.AST) -> frozenset:
    """A module's synchronous functions that look a frame up by id themselves: calling one after an
    await is the same lookup (objtrack._found decoded `hub.gray(info.id)` after waiting for EdgeTAM)."""
    return frozenset(fn.name for fn in ast.walk(tree)
                     if isinstance(fn, ast.FunctionDef) and not fn.name.startswith("__") and lookups(fn))


def first_await(fn: ast.AsyncFunctionDef) -> int | None:
    lines = [n.lineno for n in ast.walk(fn) if isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
             and not _inside_nested(fn, n)]
    return min(lines) if lines else None


def _inside_nested(fn, node) -> bool:
    for sub in ast.walk(fn):
        if sub is not fn and isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if any(n is node for n in ast.walk(sub)):
                return True
    return False


def offenders() -> list[str]:
    out = []
    for path in sorted(ORCHESTRATOR.rglob("*.py")):
        tree = ast.parse(path.read_text())
        rel = path.relative_to(ORCHESTRATOR).as_posix()
        helpers = sync_helpers(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            awaited = first_await(fn)
            if awaited is None:
                continue
            for line, text in lookups(fn, helpers):
                if line > awaited and f"{rel}:{fn.name}" not in ALLOWED:
                    out.append(f"{rel}:{line} ({fn.name}): {text}")
    return out


def test_slow_code_never_looks_a_frame_up_again_after_waiting():
    found = offenders()
    assert not found, "frame looked up by id after an await (take a Capture first; capture.py):\n" + "\n".join(found)


def test_every_allowed_exception_still_exists():
    names = set()
    for path in sorted(ORCHESTRATOR.rglob("*.py")):
        rel = path.relative_to(ORCHESTRATOR).as_posix()
        names |= {f"{rel}:{fn.name}" for fn in ast.walk(ast.parse(path.read_text())) if isinstance(fn, ast.AsyncFunctionDef)}
    assert set(ALLOWED) <= names, set(ALLOWED) - names
