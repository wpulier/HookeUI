"""Several ways to one thing (orchestrator/compose.py): the first accepted wins and the rest are called off; a hedge
starts only while what it hedges is still running; a cascade tries in turn. Every try is recorded."""
import asyncio

from orchestrator.compose import Try, first


def way(log, name, seconds, result, error=None):
    async def run():
        log.append(f"{name} started")
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            log.append(f"{name} called off")
            raise
        if error:
            raise error
        return result
    return run


def outcomes(records):
    return {r["name"]: r["outcome"] for r in records}


def test_a_race_takes_the_first_accepted_and_calls_off_the_rest():
    log = []
    result, records = asyncio.run(first([
        Try("web", way(log, "web", 0.2, "web photo")),
        Try("commons", way(log, "commons", 0.01, None)),  # answered first, not accepted
        Try("other", way(log, "other", 0.05, "other photo")),
    ]))
    assert result == "other photo"
    assert outcomes(records) == {"web": "called off", "commons": "not accepted", "other": "accepted"}
    assert "web called off" in log and records[2]["endS"] < 0.2


def test_a_hedge_starts_only_while_what_it_hedges_is_still_running():
    log = []
    slow = asyncio.run(first([
        Try("web", way(log, "web", 0.3, "first answer")),
        Try("web again", way(log, "web again", 0.01, "second answer"), after_s=0.05, hedges="web"),
    ]))
    assert slow[0] == "second answer" and outcomes(slow[1]) == {"web": "called off", "web again": "accepted"}
    assert 0.05 <= slow[1][1]["startS"] < 0.15
    log.clear()
    quick = asyncio.run(first([
        Try("web", way(log, "web", 0.01, None)),  # back, not accepted, before the hedge was due
        Try("web again", way(log, "web again", 0.01, "x"), after_s=0.05, hedges="web"),
    ]))
    assert quick[0] is None and outcomes(quick[1]) == {"web": "not accepted", "web again": "not started"}
    assert log == ["web started"]


def test_a_cascade_tries_in_turn_past_failures_and_refusals():
    log = []
    result, records = asyncio.run(first([
        Try("edgetam", way(log, "edgetam", 0.01, None)),
        Try("sam2.1", way(log, "sam2.1", 0.01, None, error=RuntimeError("no GPU"))),
        Try("micro-sam", way(log, "micro-sam", 0.01, "outline")),
        Try("never", way(log, "never", 0.01, "x")),
    ], in_turn=True))
    assert result == "outline" and log == ["edgetam started", "sam2.1 started", "micro-sam started"]
    assert outcomes(records) == {"edgetam": "not accepted", "sam2.1": "failed", "micro-sam": "accepted",
                                 "never": "not started"}
    assert records[1]["error"] == "RuntimeError: no GPU"


def test_a_failure_is_recorded_and_the_others_still_answer_and_nothing_accepted_is_none():
    log = []
    result, records = asyncio.run(first([
        Try("a", way(log, "a", 0.01, None, error=ValueError("403"))),
        Try("b", way(log, "b", 0.02, None)),
    ]))
    assert result is None and outcomes(records) == {"a": "failed", "b": "not accepted"}


def test_cancelling_the_whole_calls_off_every_try_still_running():
    log = []

    async def go():
        tries = [Try("a", way(log, "a", 1.0, "x")), Try("b", way(log, "b", 1.0, "y"))]
        task = asyncio.ensure_future(first(tries))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return tries

    tries = asyncio.run(go())
    assert {t.record["outcome"] for t in tries} == {"called off"} and sorted(log)[-2:] == ["b called off", "b started"]
