"""Researchers think with Claude Opus 5.5 by default (Will, 2026-10-30) and can write a rendered HTML page (make_page).
The Messages API is a fake here: nothing is sent."""
import asyncio
import json

from research_worker import engines
from research_worker.budget import EFFORTS, Effort
from research_worker.engines import AnthropicError, ClaudeEngine, make_engine
from tests.test_research_content import researcher

FINISH = {"title": "t", "summary": "s", "one_liner": "o", "significance": "low"}
PAGE = ("<!doctype html><html><body><svg id='chart'></svg><script>const areas=[210,340,95];"
        "document.getElementById('chart').dataset.n=areas.length</script></body></html>")


class FakeMessages:
    """Answers each POST /v1/messages with the next scripted reply, keeping every body it was sent."""

    def __init__(self, *replies) -> None:
        self.replies, self.sent = list(replies), []

    async def post(self, path, json=None):
        self.sent.append(json)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Reply(reply)


class Reply:
    def __init__(self, body: dict) -> None:
        self.body, self.status_code, self.text = body, 200, ""

    def json(self) -> dict:
        return self.body


def message(*blocks, stop="tool_use") -> dict:
    return {"id": "msg_1", "stop_reason": stop, "content": list(blocks),
            "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 0}}


def use(name: str, args: dict, n: int = 1) -> dict:
    return {"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": args}


def test_opus_is_the_default_engine_and_gpt_stays_an_option(monkeypatch):
    monkeypatch.delenv("MICROMIND_RESEARCH_ENGINE", raising=False)
    from orchestrator.options import RESEARCH_ENGINE, Settings

    assert Settings().research == "claude-opus-5-5"
    assert {"claude-opus-5-5", "gpt-6.1-sol"} <= set(RESEARCH_ENGINE)
    assert RESEARCH_ENGINE["claude-opus-5-5"].needs == "ANTHROPIC_API_KEY"
    assert isinstance(make_engine(None), ClaudeEngine)
    assert type(make_engine("gpt-6.1-sol")).__name__ == "SolEngine"


def test_a_claude_run_searches_uses_its_tools_and_finishes(tmp_path):
    fake = FakeMessages(
        message({"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {"query": "stomata density"}},
                {"type": "web_search_tool_result", "tool_use_id": "srv_1",
                 "content": [{"type": "web_search_result", "url": "https://example.org/stomata", "title": "Stomata"}]},
                use("record_finding", {"text": "pores"})),
        message(use("finish", FINISH, 2)))
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"web_search", "record_finding", "finish"}), 8, "low"))
    asyncio.run(r.run(ClaudeEngine(client=fake)))
    assert events[-1]["status"] == "done" and len(fake.sent) == 2
    first, second = fake.sent
    assert first["model"] == "claude-opus-5-5" and first["output_config"] == {"effort": "low"}
    assert first["tool_choice"] == {"type": "auto"}  # this model refuses a forced tool choice
    assert ({"type": "web_search_20260209", "name": "web_search"} in first["tools"]) == (r.web_search_allowed() and r.budget.unlimited)
    assert "https://example.org/stomata" in r.search_sources
    assert len(r.recorded) == 1
    # Its assistant turn went back whole (thinking included), then the tool's answer.
    assert second["messages"][1]["content"][0]["type"] == "thinking"
    result = second["messages"][2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"


def test_a_write_up_round_offers_only_finish_and_says_so(tmp_path):
    fake = FakeMessages(message(use("finish", FINISH)))
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"record_finding", "finish"}), 8, "low"))
    r.forced_write_up = "Write up now."
    asyncio.run(r.run(ClaudeEngine(client=fake)))
    body = fake.sent[0]
    assert [t["name"] for t in body["tools"]] == ["finish"] and body["tool_choice"] == {"type": "auto"}
    assert events[-1]["status"] == "done"


def test_an_anthropic_credit_refusal_names_anthropic(tmp_path, monkeypatch):
    async def instant(_):
        return None

    monkeypatch.setattr(engines.asyncio, "sleep", instant)
    fake = FakeMessages(AnthropicError(400, json.dumps({"error": {"type": "invalid_request_error",
                                                                   "message": "Your credit balance is too low"}})))
    r, events = researcher(tmp_path, {"effort": "quick"},
                           Effort("t", 120, 1.0, "big", frozenset({"record_finding", "finish"}), 8, "low"))
    asyncio.run(r.run(ClaudeEngine(client=fake)))
    assert events[-1]["status"] == "failed"
    assert any("Anthropic says this account has no credits left" in json.dumps(e) for e in events)


def test_make_page_keeps_a_rendered_html_output_from_the_data_it_read(tmp_path):
    fake = FakeMessages(
        message(use("make_page", {"kind": "visual", "title": "Cell sizes", "summary": "Areas of 3 cells from e1",
                                  "data_from": ["e1"], "html": PAGE})),
        message(use("finish", FINISH, 2)))
    r, events = researcher(tmp_path, {"effort": "quick"}, EFFORTS["quick"])
    assert "make_page" in r.tool_names()
    asyncio.run(r.run(ClaudeEngine(client=fake)))
    outputs = [e for e in events if e["event"] == "output"]
    assert len(outputs) == 1
    page = outputs[0]
    assert page["kind"] == "html" and page["contentKind"] == "visual" and page["caption"] == "Interactive visual: Cell sizes"
    assert page["content"]["data_from"] == ["e1"]
    assert (tmp_path / "r1" / page["path"]).read_text().startswith("<!doctype html>")
    assert events[-1]["status"] == "done"


def test_make_page_refuses_a_page_that_is_not_a_whole_document(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "quick"}, EFFORTS["quick"])
    reply, picture = asyncio.run(r.tool_make_page({"kind": "presentation", "title": "x", "summary": "y",
                                                   "html": "<div>slides</div>"}))
    assert "error" in reply and picture is None


def test_a_follow_up_answer_or_a_description_is_not_offered_make_page(tmp_path):
    answer, _ = researcher(tmp_path / "a", {"effort": "quick", "deliverable": "answer"}, EFFORTS["quick"])
    description, _ = researcher(tmp_path / "d", {"effort": "quick", "purpose": "description"}, EFFORTS["quick"])
    assert "make_page" not in answer.tool_names() and "make_page" not in description.tool_names()


def test_a_write_up_round_still_makes_the_page_asked_for_once():
    """Live, 30 Oct: "compare them, and make me a short presentation" ran both models, then the stall rule made it write
    up with only finish, and it ended "I haven't built the presentation yet". A round made to write up offers make_page
    until a page is made, then finish alone."""
    from research_worker.researcher import Researcher

    r = Researcher.__new__(Researcher)
    r.tool_names = lambda: ["run", "make_page", "finish"]
    assert r.final_tools() == ["make_page", "finish"]
    r.pages_made = 1
    assert r.final_tools() == ["finish"]
    r.pages_made, r.tool_names = 0, (lambda: ["run", "finish"])
    assert r.final_tools() == ["finish"], "never a tool this effort does not have"
