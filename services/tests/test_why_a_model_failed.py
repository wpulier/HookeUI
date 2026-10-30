"""Why a model could not be used, said with what to do: one mapping (research_worker/api_usage.refusal) that every surface
reads (the live partner spec's 4.8, horizontal 4: every claim comes from a check).

Will's try-out of 29 Oct: a video failed because Google had retired its model (Veo 3.1 Fast), and the voice said it failed
without the reason. The bar: "The video model was retired; I've switched to its successor", "Your Anthropic credit has run
out; add credit or pick another model". Errors here are shaped as the providers' clients raise them (an HTTP status, the
provider's code, the fixed refusal words a provider sends with no code), or as the adapters keep them ("Kind: words").
"""
import asyncio

import pytest

from orchestrator.brain.dispatch import Refused, refusal as brain_refusal
from orchestrator.brain.registry import ToolFailed
from research_worker import api_usage
from tests.test_truthful_refusals import refusing
from tests.waiting import HANG_S


def raised(status=None, message="", body=None, name="APIError", **attrs):
    kind = type(name, (Exception,), {**({"status_code": status} if status else {}), **attrs})
    error = kind(message)
    if body is not None:
        error.body = body
    return error


CASES = [
    # Google stopped serving a model: 404 on the model's own name (Veo 3.1 Fast, 22 Oct 2026).
    (raised(404, "models/veo-3.1-fast-generate-preview is not found for API version v1beta, or is not supported"),
     "retired"),
    (raised(404, "The model `gpt-5` does not exist", body={"error": {"code": "model_not_found"}}), "retired"),
    ({"code": 404, "status": "NOT_FOUND", "message": "models/veo-3.1-fast-generate-preview is not found"}, "retired"),
    # Out of credit: OpenAI's code, Anthropic's 400 whose only sign is its words, Gemini's prepayment.
    (raised(429, "You exceeded your current quota", body={"error": {"code": "insufficient_quota"}}), "no-credit"),
    ("RuntimeError: Anthropic 400: Your credit balance is too low to access the Anthropic API.", "no-credit"),
    (raised(429, "Your prepayment credits are depleted.", status_name="RESOURCE_EXHAUSTED"), "no-credit"),
    # A plain rate limit is not out of credit.
    ("ClientError: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'quota', 'status': 'RESOURCE_EXHAUSTED'}}",
     "rate-limit"),
    (raised(401, "invalid x-api-key"), "key-refused"),
    ("ClientError: 403 PERMISSION_DENIED. API key not valid.", "key-refused"),
    (ValueError("Missing key inputs argument! To use the Google AI API, provide (`api_key`) arguments."), "no-key"),
    ("ServerError: 503 UNAVAILABLE", "server"),
    ("APIConnectionError: Connection error.", "connection"),
    (TimeoutError(), "cut-off"),
    (ValueError("bad json"), "failed"),
]


@pytest.mark.parametrize("error,reason", CASES)
def test_why_is_read_from_the_error_itself(error, reason):
    assert api_usage.reason_of(error) == reason


def test_each_reason_is_said_with_what_to_do_naming_the_model_and_its_provider():
    retired = api_usage.refusal(CASES[0][0], model="Veo 3.1 Fast", provider="Google",
                                successor="Gemini Omni 1.1 Flash")
    assert retired == {"reason": "retired", "said": "Google has retired Veo 3.1 Fast",
                       "todo": "use Gemini Omni 1.1 Flash, its successor"}
    credit = api_usage.refusal(CASES[4][0], model="Claude Opus 5.5")  # its provider from its one name
    assert api_usage.refusal_words(credit) == ("Your Anthropic account is out of credit; add credit to your Anthropic "
                                               "account, or choose another model")
    assert api_usage.refusal(CASES[9][0], model="Gemini 3.8 Flash")["todo"] == "add your Gemini key in Models › Keys"
    assert api_usage.refusal(CASES[7][0], model="GPT-6.1 Sol")["said"] == "Your OpenAI key was refused"
    for reason, (said, todo) in api_usage.REASONS.items():
        assert said and todo, reason  # every reason has what to do


def test_a_call_that_never_reached_the_provider_without_its_key_is_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    assert api_usage.reason_of(AttributeError("no client"), keys=keys) == "no-key"
    assert api_usage.reason_of(CASES[0][0], keys=keys) == "retired", "an answer from the provider says its own reason"
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-key")
    assert api_usage.reason_of(AttributeError("no client"), keys=keys) == "failed"


def test_the_meters_outcomes_are_unchanged():
    """The performance meter's vocabulary (OUTCOMES, by HTTP status) stays as it was: the reasons are finer, beside it."""
    assert api_usage.outcome_of(CASES[3][0]) == "rate-limit" and api_usage.outcome_of(CASES[0][0]) == "failed"
    assert api_usage.outcome_of(raised(401, "x")) == "auth"


@pytest.mark.parametrize("error,label,said,todo", [
    ("RuntimeError: Anthropic 400: Your credit balance is too low to access the Anthropic API.", "Claude Opus 5.5",
     "Claude Opus 5.5 couldn't be used: your Anthropic account is out of credit",
     "add credit to your Anthropic account, or choose another model"),
    ("ClientError: 404 NOT_FOUND. {'error': {'code': 404, 'message': 'models/gemini-3.5-flash is not found'}}",
     "Gemini 3.5 Flash", "Google has retired Gemini 3.5 Flash", "use its successor, or choose another model"),
    ("ClientError: 429 RESOURCE_EXHAUSTED.", "Gemini 3.8 Flash",
     "Gemini 3.8 Flash couldn't be used: Google is limiting requests right now",
     "try again in a minute, or choose another model"),
])
def test_the_brain_says_a_models_refusal_by_its_reason_never_did_not_answer(error, label, said, todo):
    refused = brain_refusal(error, label)
    assert isinstance(refused, Refused) and str(refused) == said and refused.next == todo
    assert "did not answer" not in refused.said()
    assert brain_refusal("ValueError: unreadable reply", label) is None  # any other failure is said as before


def test_a_turn_whose_model_is_out_of_credit_answers_with_the_reason_and_what_to_do(tmp_path, monkeypatch):
    """Through the real Brain.turn: the quick pass falls back to the full look (another model may answer), and the full
    look's refusal is what the request says, with what to do."""
    error = "RuntimeError: Anthropic 400: Your credit balance is too low to access the Anthropic API."

    async def scenario():
        brain, hub, sent, logged = refusing(tmp_path, monkeypatch, error)
        with pytest.raises(ToolFailed) as failed:
            await asyncio.wait_for(brain.turn("Which tool should I use to segment nuclei?"), HANG_S)
        said = failed.value.said()
        assert "out of credit" in said and "add credit to your Anthropic account, or choose another model" in said
        assert "did not answer" not in said and "something went wrong" not in said
        assert [r["used"] for r in logged if r.get("kind") == "fallback"] == ["look"]  # the quick pass fell back
    asyncio.run(scenario())
