"""The acting bench's deterministic cases (bench/acting): the app does what was asked, in order,
and never says it is done before the board shows it. A case marked `xfail` fails today for
the reason it gives, and is expected to pass once the PR named there lands (strict: a fixed
case must drop its mark)."""
import asyncio
import time

import pytest

from bench.acting.run import load_cases, run_case

CASES = load_cases()


@pytest.fixture(autouse=True)
def scripted_installs(monkeypatch):
    """FakeGPU supplies these models; the developer's installed packages are not the case's setup."""
    from orchestrator.operations import INSTALLED

    for name in ("sam3", "micro-sam", "cellpose", "open_clip", "trackastra"):
        monkeypatch.setitem(INSTALLED, name, lambda: "")


@pytest.mark.parametrize("case", [
    pytest.param(c, id=c["id"], marks=[pytest.mark.wall_clock] if c.get("expect", {}).get("within") else [])
    for c in CASES])
def test_acting_case(case, request):
    """A case's `within` limits are wall-clock bounds (tests/conftest.py): a predicate that held, but later than its
    limit, fails only with make test-timing; one that never held fails here."""
    if case.get("xfail"):
        pytest.xfail(case["xfail"]) if not _passes(case) else pytest.fail(
            f"{case['id']} passes now: remove its xfail ({case['xfail']})")
    report = asyncio.run(run_case(case))
    late = {words for words, _at, _by in report.late}
    assert not [f for f in report.failures if f not in late], "; ".join(report.failures)
    for words, at, by in report.late:
        request.getfixturevalue("wall_clock").within(at, by, words)


def _passes(case) -> bool:
    return asyncio.run(run_case(case)).ok


@pytest.mark.parametrize("local", [(2026, 10, 16, 0, 5), (2027, 3, 15, 0, 30)],
                         ids=["just-after-midnight", "the-hour-after-a-23-hour-day"])
def test_yesterdays_session_is_found_just_after_midnight(local, new_york):
    """The session a case seeds `days_ago: 1` is on yesterday's date and the app's "yesterday" names that date, at
    any time of day: seeded a day and an hour back, it fell two dates back from 00:00 to 01:00 (2026-10-16). The
    session store's clock runs from just after local midnight, and on the day after clocks went forward."""
    case = next(c for c in CASES if c["id"] == "chat-go-back-to-yesterdays-leaf-session")
    offset = time.mktime((*local, 0, 0, 0, -1)) - time.time()
    report = asyncio.run(run_case(case, day_clock=lambda: time.time() + offset))
    assert report.ok, "; ".join(report.failures)


@pytest.mark.parametrize("case_id,slow", [pytest.param(c, slow, id=c) for c, slow in (
    ("chat-what-colour-are-the-cells-outlined-in", ("CELLS_S", 8.0)),
    ("chat-highlight-the-blue-ones-coloured-by-size", ("CELLS_S", 8.0)),
    ("chat-colour-cells-by-look-group", ("CELLS_S", 8.0)),
    ("chat-keep-the-compared-outline-you-choose", ("OUTLINE_S", 3.0)))])
def test_a_follow_up_is_typed_once_the_answer_before_is_in(case_id, slow, monkeypatch):
    """The user types a follow-up after reading the answer to the request before it, however long that took. With
    the cell finder taking 8 s instead of 1.2 (or one outline 3 s instead of 0.15), as a starved CI runner made them
    (2026-10-21), "colour them by size" at 6 s came before any cells, "colour them by group" before any groups, "keep
    the SAM 2.1 one" before the comparison, and "what colour..." before the brain could be told how they are drawn."""
    from bench.acting import world

    monkeypatch.setattr(world, *slow)
    report = asyncio.run(run_case(next(c for c in CASES if c["id"] == case_id)))
    assert report.ok, "; ".join(report.failures)


def test_the_live_failures_are_all_here():
    """The 2026-10-04 cases stay in the bench (services/logs/interactions.jsonl)."""
    ids = {c["id"] for c in CASES}
    assert {"live-140652", "live-140948", "live-152750", "live-153656", "live-140842"} <= ids
