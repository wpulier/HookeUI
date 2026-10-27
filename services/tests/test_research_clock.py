"""The clock never kills work (2026-10-11: Researcher 2 ran five motion tests, then a hard 75 s cut ended its write-up
and the card said it found nothing). An effort's time is where the researcher is told to write up; a write-up in
progress finishes; what it had the app measure is its work; the only stops are the stall rule, a capped day's limit and
the user's own "keep going?" after an hour or 3x its usual cost. Every model here is a fake: nothing is sent."""
import asyncio
import json

from research_worker import researcher as researcher_module
from research_worker.budget import Budget, Effort
from research_worker.engines import WRITE_UP_NOW, SolEngine
from tests.test_research_content import fake_openai, response, researcher, stopped_after
from tests.waiting import HANG_S

FINISH = {"title": "t", "summary": "s", "one_liner": "o", "significance": "low"}


def steps(events, kind=None):
    return [e["text"] for e in events if e["event"] == "step" and (kind is None or e["kind"] == kind)]


def test_a_write_up_past_the_efforts_time_finishes(tmp_path, monkeypatch):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            await asyncio.sleep(0.4)  # its round runs past the effort's whole time
            return response(("record_finding", {"text": "pores open at dawn"}))
        await asyncio.sleep(0.3)  # and so does its write-up: nothing cuts it
        return response(("finish", FINISH))

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 0.2, 1.0, "big", frozenset({"web_search", "record_finding", "finish"}), 8, "low"))
    asyncio.run(r.run(SolEngine()))
    assert events[-1]["status"] == "done" and events[-1]["result"]["title"] == "t"
    assert "Its time for this work is up: told to write up now" in steps(events, "warning")
    assert any(item.get("role") == "user" and WRITE_UP_NOW in json.dumps(item) for item in sent[1]["input"])
    assert sent[1]["tool_choice"] == "auto"  # told, not made to


class Measures:
    """Five motion tests through the app, then a write-up that never comes (the user stops it)."""
    model = "fake"
    over_budget = False

    async def run(self, r):
        for _ in range(5):
            await r.tool_run({"operation": "motion"})
        await asyncio.sleep(30)


def test_a_stopped_run_lists_the_measurements_it_had_the_app_make(tmp_path):
    r, events = researcher(tmp_path, {"effort": "quick", "label": "motion tests"})
    made = iter(range(2, 7))

    async def run_motion(op, **kw):
        n = next(made)
        return {"ok": True, "id": f"m{n}", "title": f"Motion · 02:4{n}", "words": f"measured 12 tracks: mean net speed {n}.5 px/s",
                "values": {"meanNetSpeed": n + 0.5}}

    r.request = run_motion
    stopped_after(r, Measures(), 0.3)
    end = events[-1]
    result = end["result"]
    assert end["status"] == "partial" and result["partial"]
    assert result["one_liner"] == "It stopped before writing up; it kept 5 motion measurements (m2, m3, m4, m5, m6)."
    assert [o["id"] for o in result["operations"]] == ["m2", "m3", "m4", "m5", "m6"]
    assert result["details"][0] == "Motion · 02:42 (m2): measured 12 tracks: mean net speed 2.5 px/s"
    assert result["operations"][4]["values"] == {"meanNetSpeed": 6.5}


def keeps_finding_new_things(monkeypatch, finish_after=None):
    """A model that finds something new every round and never writes up unless told: the loop the user feared."""
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        await asyncio.sleep(0.05)
        if finish_after is not None and len(sent) > finish_after:
            return response(("finish", FINISH))
        return response(("record_finding", {"text": f"finding {len(sent)}"}))

    fake_openai(monkeypatch, create)
    return sent


def answer(r, qid, text="yes, keep going"):
    with (r.dir / "inbox.jsonl").open("a") as inbox:
        inbox.write(json.dumps({"text": text, "answers": qid}) + "\n")


def test_after_its_hour_it_asks_and_no_answer_writes_up(tmp_path, monkeypatch):
    monkeypatch.setattr(researcher_module, "CHECK_IN_WAIT_S", 0.6)
    keeps_finding_new_things(monkeypatch)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 999, 99.0, "big", frozenset({"web_search", "record_finding", "finish"}), 999, "low"))
    r.next_check_at = 0.3  # "an hour", on a fake clock
    asyncio.run(asyncio.wait_for(r.run(SolEngine()), HANG_S))
    asked = [e for e in events if e["event"] == "request"]
    assert len(asked) == 1 and asked[0]["keepGoing"] and asked[0]["id"] == "q1"
    assert "No answer in 0 minutes: writing up what it has" in steps(events, "waiting")
    end = events[-1]
    assert end["status"] == "partial" and len(end["result"]["findings"]) >= 3  # it never wrote up; its work is kept


def test_a_yes_to_keep_going_carries_on(tmp_path, monkeypatch):
    monkeypatch.setattr(researcher_module, "CHECK_IN_WAIT_S", 5.0)
    sent = keeps_finding_new_things(monkeypatch, finish_after=12)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 999, 99.0, "big", frozenset({"web_search", "record_finding", "finish"}), 999, "low"))
    r.next_check_at = 0.2

    async def go():
        task = asyncio.create_task(r.run(SolEngine()))
        while not any(e["event"] == "request" for e in events):
            await asyncio.sleep(0.05)
        calls = len(sent)
        await asyncio.sleep(0.8)
        assert len(sent) == calls  # paused: no model call while it waits for the user
        answer(r, "q1")
        await asyncio.wait_for(task, HANG_S)

    asyncio.run(go())
    assert "The user answered; it reads their answer next" in steps(events, "waiting")
    assert any("yes, keep going" in json.dumps(c["input"]) for c in sent)  # the model reads the answer and decides
    assert events[-1]["status"] == "done"


def test_past_three_times_its_usual_cost_it_asks(tmp_path, monkeypatch):
    monkeypatch.setattr(researcher_module, "CHECK_IN_WAIT_S", 0.6)
    keeps_finding_new_things(monkeypatch)
    effort = Effort("t", 999, 0.01, "big", frozenset({"web_search", "record_finding", "finish"}), 999, "low")
    r, events = researcher(tmp_path, {"effort": "quick"}, effort)
    r.budget = Budget(effort, unlimited=True)  # no daily cap: the run's only money guard
    r.next_check_cost = 3 * effort.cap_usd
    asyncio.run(asyncio.wait_for(r.run(SolEngine()), HANG_S))
    asked = [e for e in events if e["event"] == "request"]
    assert len(asked) == 1 and asked[0]["keepGoing"] and "3 times what a t run usually costs" in asked[0]["why"]
    assert events[-1]["status"] == "partial"


def test_a_capped_day_has_no_cost_check_in_its_limit_writes_up(tmp_path, monkeypatch):
    keeps_finding_new_things(monkeypatch)
    effort = Effort("t", 999, 0.05, "big", frozenset({"web_search", "record_finding", "finish"}), 999, "low")
    r, events = researcher(tmp_path, {"effort": "quick"}, effort)  # Budget(effort): a capped day's per-run limit
    asyncio.run(asyncio.wait_for(r.run(SolEngine()), HANG_S))
    assert not [e for e in events if e["event"] == "request"]
    assert events[-1]["status"] in ("over_budget", "partial")


def test_repeating_the_same_finding_is_nothing_new(tmp_path, monkeypatch):
    async def create(**kwargs):  # the same finding every round, never a write-up
        return response(("record_finding", {"text": "Pores  open at DAWN"}))

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 999, 99.0, "big", frozenset({"web_search", "record_finding", "finish"}), 999, "low"))
    asyncio.run(asyncio.wait_for(r.run(SolEngine()), HANG_S))
    assert any(s.startswith("Nothing new in") for s in steps(events, "stall"))
    assert events[-1]["status"] == "partial"


def test_it_says_it_is_alive_while_it_works(tmp_path, monkeypatch):
    monkeypatch.setattr(researcher_module, "ALIVE_EVERY_S", 0.1)

    async def create(**kwargs):
        await asyncio.sleep(0.45)  # one long model call
        return response(("finish", FINISH))

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"})
    asyncio.run(r.run(SolEngine()))
    assert len([e for e in events if e["event"] == "alive"]) >= 3
    assert events[-1]["status"] == "done"


def test_the_brain_is_told_where_each_effort_is_told_to_write_up():
    from orchestrator.brain.toolbox.research import _effort_words

    words = _effort_words()
    assert "quick (about 75 s and 8 model rounds before it is told to write up)" in words
    assert "stops" not in words  # nothing stops at the effort's time


def test_an_update_given_mid_run_reaches_the_model_on_its_next_round(tmp_path, monkeypatch):
    sent = []

    async def create(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            answer(r, None, "also give the median")  # the user's update while it works (ResearchManager.note)
            return response(("record_finding", {"text": "mean speed 3.0 px/s"}))
        return response(("finish", FINISH))

    fake_openai(monkeypatch, create)
    r, events = researcher(tmp_path, {"effort": "quick"})
    asyncio.run(r.run(SolEngine()))
    assert "also give the median" in json.dumps(sent[1]["input"])
    assert "Got a note: also give the median" in steps(events)
