"""Explicit answer-call scope, enforced at the provider entry point.

Specialist and research calls outside this scope retain their own declared
budgets. A scope covers only model work, never the subsequent tool execution.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from .admission import AdmissionDenied, MAX_CALLS

PLANNER_OUTPUT_TOKENS = 4096  # room for complete action envelopes; never an extra model round

from .registry import ToolFailed
from ..tools import ToolError

STILL_LOCAL = "tools on this Mac still work"


class Refused(ToolError):
    """A cloud call that the app's own policy or the network did not let through, said in its own words (ticket 62,
    row 23): the example's local-only policy, the monthly cap, no connection. Never "did not answer", never "something
    went wrong". `retry`: whether asking again, unchanged, can work."""

    def __init__(self, message: str, next: str | None = None, *, kind: str, retry: bool) -> None:
        super().__init__(message, next=next)
        self.kind, self.retry = kind, retry

    def said(self) -> str:
        """Its words, then what still works: one more sentence after a message that ends one (never ".;")."""
        message = str(self).rstrip()
        if not self.next:
            return message
        if message.endswith((".", "!", "?")):
            return f"{message} {self.next[:1].upper()}{self.next[1:]}."
        return f"{message}; {self.next}"


def not_answered(label: str, result, next: str = "ask again in a moment") -> ToolError:
    """What a provider result without data says: its refusal in its own words, else "<label> did not answer (…)"."""
    refused = refusal(getattr(result, "error", ""))
    if refused is not None:
        return refused
    return ToolError(f"{label} did not answer ({getattr(result, 'error', '') or 'unreadable reply'})", next=next)


def refusal(error: str) -> Refused | None:
    """The refusal a provider adapter kept as its result's error ("CapReached: …", vision.VisionResult), or None for
    any other failure. The prefix is the exception's own class name, as the adapters write it."""
    kind, _, words = str(error or "").partition(": ")
    if kind == "ProviderRefused":
        from research_worker.api_usage import LOCAL_ONLY

        return Refused(words or LOCAL_ONLY, next=STILL_LOCAL, kind="local-only", retry=False)
    if kind == "CapReached":
        return Refused(words or "the monthly cap you set for cloud models is reached; raise or remove it in the spending panel",
                       next=STILL_LOCAL, kind="capped", retry=False)
    if kind in ("ConnectError", "ConnectionError", "ConnectTimeout", "APIConnectionError"):
        return Refused("the cloud model could not be reached: the connection failed",
                       next=f"check this Mac's internet connection, then ask again; {STILL_LOCAL}", kind="connection", retry=True)
    return None


_PHASE = ContextVar("answer_dispatch_phase", default=None)
_ATTEMPTS = ContextVar("answer_dispatch_attempts", default=None)
_ADMITTED = ContextVar("answer_dispatch_admitted", default=None)


def admitted_facts():
    """The actual entered send in this coroutine, not a later budget snapshot."""
    entered = _ADMITTED.get()
    return dict(zip(("requestId", "attemptId", "phase"), entered)) if entered is not None else {}


@contextmanager
def capture_answer_attempts(attempt_ids):
    """Attribute admitted sends to this coroutine's owner, never a budget snapshot."""
    token = _ATTEMPTS.set(attempt_ids)
    try:
        yield
    finally:
        _ATTEMPTS.reset(token)


def in_answer_phase():
    return _PHASE.get() is not None


@contextmanager
def answer_phase(phase, reservation=None):
    token = _PHASE.set((phase, reservation))
    try:
        yield
    finally:
        _PHASE.reset(token)


async def dispatch_answer(send, *, model, effort=""):
    from ..ticket import current

    scope, request = _PHASE.get(), current()
    if scope is None:
        return await send()  # standalone/specialist work, not a brain answer
    if request is None or request.admission is None:
        raise AdmissionDenied("request-unbound")
    if request.superseded("session"):
        raise AdmissionDenied("request-canceled")
    budget = request.admission
    phase, reservation = scope
    attempt_id = f"{budget.request_id}/{getattr(budget, 'entered', budget.used) + 1}"
    observer = _ATTEMPTS.get()

    async def attempted():
        # Runs only after admission. Each sibling look binds its own list; refused
        # work and concurrent fast/final calls cannot enter this look's trace.
        try:
            if observer is not None and len(observer) < MAX_CALLS:
                observer.append(attempt_id)
        except Exception:
            pass  # optional attribution never changes provider outcome or admission
        token = _ADMITTED.set((budget.request_id, attempt_id, phase))
        try:
            from .. import execution
            try:
                execution.note(**admitted_facts())
            except Exception:
                pass  # optional inspection must not change admission or provider work
            result = await send()
        finally:
            _ADMITTED.reset(token)
        # Vision adapters preserve errors as results for standalone benches. An
        # answer attempt must still be recorded as failed, rather than successful.
        if hasattr(result, "data") and result.data is None:
            refused = refusal(getattr(result, "error", ""))
            if refused is not None:
                raise refused
            raise RuntimeError(f"{model} did not answer ({getattr(result, 'error', '') or 'unreadable reply'})")
        return result

    return await budget.dispatch(attempt_id=attempt_id, phase=phase,
                                 model=model, effort=effort, send=attempted, reservation=reservation)


class AnswerProvider:
    """Count each ask, including each closer-view round, not an outer look."""
    def __init__(self, provider, model, effort="", *, max_output_tokens=None):
        self.provider, self.model, self.effort = provider, model, effort
        self.max_output_tokens = max_output_tokens

    def __getattr__(self, name):
        return getattr(self.provider, name)

    async def ask(self, *args, **kwargs):
        if self.max_output_tokens is not None:
            kwargs.setdefault("max_output_tokens", self.max_output_tokens)
        return await dispatch_answer(lambda: self.provider.ask(*args, **kwargs),
                                     model=self.model, effort=self.effort)


async def query_answer(provider, model, images, prompt, *, max_output_tokens):
    """An inline query's interpretation spends this request's answer allowance."""
    from ..options import VISION

    effort = VISION[model].spec.partition("@")[2] if model in VISION else ""
    with answer_phase("query"):
        return await AnswerProvider(provider, model, effort).ask(images, prompt, max_output_tokens=max_output_tokens)
