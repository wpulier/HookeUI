"""The quick decision's prompt in a cacheable order (brain/fast.py `prompt`, ORDERS) and Anthropic's prompt cache
(vision.Prefixed, AnthropicVision): "static-first" puts the instructions and tool list, the same call after call, ahead
of this call's notes and words; Claude is sent that part marked for its cache, every other reader gets the same whole
string; and a cached call is priced from what it wrote to and read from the cache. Nothing here calls a provider."""
import asyncio

import pytest

from orchestrator.brain import fast
from orchestrator.brain.context import Moment
from orchestrator.vision import AnthropicVision, Prefixed, anthropic_usage, anthropic_usd

TOOLS = "- zoom(level): magnify\n- highlight(things): point at seen things"
KEY = "synthetic-anthropic-key"


def moment(seen: str = "s1 stoma (centre)", screen: str = "o1 'stoma' is selected", leads: bool = False) -> Moment:
    return Moment(outlined=('o1 "stoma"',), outlined_plain=(), selected="o1", seen=seen, view=None, screen=screen,
                  screen_voice=screen, researchers="none", roster="", history="(none)", recap_lines=(), title="",
                  summary="", thread_tag="", digest="", user_leads=leads, learner="")


@pytest.mark.parametrize("followup,point_only", [(False, False), (True, False), (False, True)])
def test_static_first_keeps_the_same_part_first_and_this_calls_notes_and_words_last(followup, point_only):
    one = fast.prompt(moment(), "Magnify that.", TOOLS, followup=followup, point_only=point_only, order="static-first")
    other = fast.prompt(moment(seen="s9 hair (edge)", screen="the magnifier is open", leads=True), "Close it.", TOOLS,
                        followup=followup, point_only=point_only, order="static-first")
    assert isinstance(one, Prefixed) and isinstance(other, Prefixed)
    assert one[:one.stable] == other[:other.stable], "the cached part never changes with the notes or the words"
    assert TOOLS in one[:one.stable] and "Reply with JSON only:" in one[:one.stable]
    for said in ("s1 stoma (centre)", "Magnify that.", "o1 'stoma' is selected"):
        assert said not in one[:one.stable] and said in one[one.stable:]
    assert fast.LEAD.strip() in other[other.stable:] and fast.LEAD.strip() not in one
    assert (fast.POINT_ONLY.strip() in one[:one.stable]) == point_only


@pytest.mark.parametrize("followup,point_only", [(False, False), (True, False), (False, True)])
def test_notes_first_is_the_prompt_as_it_was_written(followup, point_only):
    m = moment(leads=True)
    made = fast.prompt(m, "Magnify that.", TOOLS, followup=followup, point_only=point_only, order="notes-first")
    template = fast.FOLLOWUP if followup and not point_only else fast.FAST
    from orchestrator.brain.context import fast_fields

    written = template.format(**fast_fields(m), question="Magnify that.", tools=TOOLS, lead=fast.LEAD)
    if point_only:
        written = written.replace("Reply with JSON only:", fast.POINT_ONLY.strip() + "\n\nReply with JSON only:")
    assert made == written and not isinstance(made, Prefixed)
    with pytest.raises(ValueError):
        fast.prompt(m, "Magnify that.", TOOLS, order="sideways")


def test_both_orders_say_the_same_things():
    m = moment(leads=True)
    notes_first = fast.prompt(m, "Magnify that.", TOOLS, order="notes-first")
    static_first = fast.prompt(m, "Magnify that.", TOOLS, order="static-first")
    pieces = [fast.FAST_INTENT, fast.FAST_RULES.format(tools=TOOLS).strip(), fast.LEAD.strip(), 'User: "Magnify that."']
    assert all(piece in notes_first and piece in static_first for piece in pieces)


class Response:
    is_error, status_code, text = False, 200, ""

    def __init__(self, usage):
        self.usage = usage

    def json(self):
        return {"model": "claude-opus-5-5", "stop_reason": "end_turn", "usage": self.usage,
                "content": [{"type": "text", "text": '{"intent": "request", "needs_look": false, "answer": "Magnifying."}'}]}


def sent_by(prompt, usage, monkeypatch, fast_mode=True):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    vision, posted = AnthropicVision("claude-opus-5-5", effort="low", fast=fast_mode), {}

    class Client:
        async def post(self, path, json):
            posted.update(json)
            return Response(usage)

    vision.client = Client()
    return asyncio.run(vision.ask([], prompt)), posted


def test_claude_is_sent_the_stable_part_marked_for_its_cache(monkeypatch):
    prompt = fast.prompt(moment(), "Magnify that.", TOOLS, order="static-first")
    usage = {"input_tokens": 1600, "output_tokens": 120, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 34000}
    result, posted = sent_by(prompt, usage, monkeypatch)
    first, last = posted["messages"][0]["content"]
    assert first == {"type": "text", "text": prompt[:prompt.stable], "cache_control": {"type": "ephemeral"}}
    assert last == {"type": "text", "text": prompt[prompt.stable:] + "\n\nReply with the JSON object only."}
    assert result.usage == {"input": 35600, "output": 120, "cached": 34000, "cacheWrite": 0}
    assert result.data["answer"] == "Magnifying."


def test_a_plain_prompt_is_sent_as_before(monkeypatch):
    _, posted = sent_by("What is this?", {"input_tokens": 900, "output_tokens": 40}, monkeypatch)
    assert posted["messages"][0]["content"] == [{"type": "text", "text": "What is this?\n\nReply with the JSON object only."}]


def test_a_cached_call_is_priced_from_its_cache_writes_and_reads():
    # Opus 5.5 fast mode (pricing page, 2026-11-05): $8 in, $40 out; a 5-minute write 1.25x the input, a read $0.40.
    written = anthropic_usage({"input_tokens": 1600, "output_tokens": 150, "cache_creation_input_tokens": 34000,
                               "cache_read_input_tokens": 0})
    read = anthropic_usage({"input_tokens": 1600, "output_tokens": 150, "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 34000})
    uncached = anthropic_usage({"input_tokens": 35600, "output_tokens": 150})
    assert anthropic_usd("claude-opus-5-5", uncached, fast=True) == pytest.approx((35600 * 8 + 150 * 40) / 1e6)
    assert anthropic_usd("claude-opus-5-5", written, fast=True) == pytest.approx((1600 * 8 + 34000 * 10 + 150 * 40) / 1e6)
    assert anthropic_usd("claude-opus-5-5", read, fast=True) == pytest.approx((1600 * 8 + 34000 * 0.40 + 150 * 40) / 1e6)
    assert anthropic_usd("claude-opus-5-5", read) == pytest.approx((1600 * 4 + 34000 * 0.20 + 150 * 20) / 1e6)
    assert anthropic_usd("claude-sonnet-5-5", read) == pytest.approx((1600 * 2 + 34000 * 0.10 + 150 * 10) / 1e6)
    assert anthropic_usd("claude-fable-9", read) is None, "a model with no price here is unknown, never guessed"
