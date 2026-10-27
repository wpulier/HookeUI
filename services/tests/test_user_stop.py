"""The user's Stop says what it did (orchestrator/user_stop.py; L7b, ticket 62 row 12): what stopped, what was kept,
what was lost, and work that carries on because another request needs it. Nothing is said as stopped that the run did
not record as stopped."""
import asyncio

from orchestrator import user_stop


def test_kept_and_discarded_say_only_what_was_recorded_or_guaranteed():
    """Review of #365: nothing is said as kept that the run did not record; the runner guarantees nothing after a Stop
    is applied, and that is what is said of unfinished work."""
    stop = user_stop.Stop()
    stop.work("Outline every cell on the view", "stopped")
    assert stop.report("X")["words"].endswith("Kept: none recorded. Nothing of its unfinished work was applied.")
    failing = user_stop.Stop()
    failing.work("x", "failed: its process did not end")
    assert failing.report("X")["words"].endswith("Kept: none recorded. Discarded: none recorded.")


def test_a_stop_of_begun_work_says_what_stopped_was_kept_and_was_discarded():
    stop = user_stop.Stop()
    stop.did("the Cells choice Cellpose-SAM, for this run")
    stop.work("Outline every cell on the view", "stopped")
    said = stop.report("Outline every cell")
    assert said["outcome"] == "stopped" and said["stopped"] == ["Outline every cell on the view: stopped"]
    assert said["kept"] == ["the Cells choice Cellpose-SAM, for this run"]
    assert said["words"] == ("Stopped “Outline every cell” (you pressed Stop). Kept: the Cells choice Cellpose-SAM, for "
                             "this run. Nothing of its unfinished work was applied.")


def test_shared_work_is_never_said_as_stopped():
    """Another request still needs the job (contract 4): the Stop ended only this run's wait, and says so."""
    stop = user_stop.Stop()
    stop.shared("Outline every cell on this view (asked in chat too)")
    said = stop.report("Outline every cell")
    assert said["outcome"] == "nothing" and said["carriesOn"] and said["stopped"] == []
    assert said["words"].startswith("Stopped waiting for “Outline every cell” (you pressed Stop); its work was not ended.")
    assert "Still running because another request needs it: Outline every cell on this view" in said["words"]


def test_work_that_finished_first_is_said_as_finished_and_its_result_unused():
    stop = user_stop.Stop()
    stop.work("Count", "completed")
    said = stop.report("Count")
    assert said["outcome"] == "completed" and "had already finished" in said["words"]
    assert said["lost"] == ["the result it finished just as you pressed Stop (not used)"]


def test_work_still_ending_is_stopping_until_it_ends_then_says_how():
    stop, heard = user_stop.Stop(), []
    task = object()
    stop.late.add(task)
    stop.listeners.append(heard.append)
    assert stop.report("Outline every cell")["outcome"] == "stopping"
    stop.ended(task, "failed: its process did not end", "Outline every cell on the view")
    assert heard == [["failed: its process did not end"]]
    said = stop.report("Outline every cell")
    assert said["outcome"] == "failed" and said["stopped"] == ["Outline every cell on the view: failed: its process did not end"]


def test_a_stop_between_steps_ended_nothing_and_says_so():
    said = user_stop.Stop().report("Measure")
    assert said["outcome"] == "nothing" and said["words"] == ("You pressed Stop on “Measure”: none of its work was ended "
                                                             "(none was running). Kept: none recorded. Discarded: nothing.")


def test_the_holder_is_inherited_by_tasks_and_only_the_users_stop_reads_requested():
    async def main():
        with user_stop.stoppable() as stop:
            seen = await asyncio.create_task(asyncio.sleep(0, result=user_stop.current()))
            assert seen is stop and not user_stop.requested()
            stop.stop()
            assert user_stop.requested()
        assert user_stop.current() is None

    asyncio.run(main())


def test_the_same_run_twice_at_once_is_refused_before_it_starts_and_free_again_after():
    key = user_stop.identity("2026-10-20.2", "cells", {}, {}, {"source": "live"})
    assert key == user_stop.identity("2026-10-20.2", "cells", {}, {}, {"source": "live"})
    assert key != user_stop.identity("2026-10-20.2", "cells", {"diameter": 30}, {}, {"source": "live"})
    with user_stop.once(key, "Outline every cell", run="A"):
        try:
            with user_stop.once(key, "Outline every cell", run="B"):
                raise AssertionError("a second identical run started")
        except user_stop.AlreadyRunning as refused:
            assert refused.run == "A" and "not started twice" in str(refused)
        with user_stop.once(user_stop.identity("other"), "Measure"):
            pass  # other work is never held back
    with user_stop.once(key, "Outline every cell", run="C"):
        pass  # once the first has ended, the same run starts
