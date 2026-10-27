"""One request's actual answer-model dispatches, including its late work.

Carry this object itself through handoffs; do not reconstruct it from ``used``.
Prepare inputs before dispatch, then supply the actual provider entry point as
``send``. A callback must represent one attempt, with no hidden provider retries.
Explicit specialist/research jobs have their own budgets, outside this module;
they must not produce an extra answer-model call on this request's behalf.

Like the brain's tasks, this object is confined to one asyncio event loop. Its
admission and reservation transitions never await. Concurrent callers therefore
cannot enter a fourth provider while earlier calls are awaiting their results.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TypeVar


MAX_CALLS = 3
_PHASES = frozenset({"fast", "hands", "look", "query", "compare", "final", "late"})
_SYNTHESIS = frozenset({"final", "late"})
_Result = TypeVar("_Result")


class AdmissionDenied(RuntimeError):
    """The provider was not entered; ``reason`` describes the admission refusal."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Attempt:
    """A snapshot of dispatch facts, independent of logs and tool outcomes."""

    request_id: str
    attempt_id: str
    phase: str
    model: str
    effort: str
    status: str = "running"


class RequestAdmission:
    """Fixed three-attempt allowance shared by every answer path of one request."""

    def __init__(self, request_id: str):
        self._request_id = request_id
        self._attempts: dict[str, Attempt] = {}
        self._final: object | None = None
        self._forgiven: set[str] = set()
        self._last_word: object | None = None  # the answer's own call beyond the three (reserve_last_word)
        self._last_word_given = False
        self._recheck: object | None = None  # one more try after a confirmed omission (reserve_recheck)
        self._recheck_given = False

    @property
    def request_id(self) -> str:
        return self._request_id

    @property
    def used(self) -> int:
        return len(self._attempts) - len(self._forgiven)

    @property
    def entered(self) -> int:
        """Every provider entry, a forgiven one included: attempt ids are never reused."""
        return len(self._attempts)

    def forgive(self, attempt_ids) -> bool:
        """A speculative look dropped before anything used it (a look started on spoken words, replaced by
        one that knows the steps run since) does not count against the request, once per request, so the
        look that replaces it can run. Its entry stays recorded: at most MAX_CALLS + 1 provider entries."""
        if self._forgiven:
            return False
        looks = [a for a in attempt_ids if a in self._attempts and self._attempts[a].phase == "look"]
        if not looks:
            return False
        self._forgiven.add(looks[0])
        return True

    @property
    def remaining(self) -> int:
        """Unused capacity available without consuming a final reservation."""
        return MAX_CALLS - self.used - int(self._final is not None)

    @property
    def attempts(self) -> tuple[Attempt, ...]:
        return tuple(self._attempts.values())

    def reserve_final(self) -> object:
        """Hold one synthesis slot, reusing the existing hold on repeated calls."""
        if self._final is None:
            if self.used >= MAX_CALLS:
                raise AdmissionDenied("budget-exhausted")
            self._final = object()
        return self._final

    def reserve_last_word(self) -> object | None:
        """The answer's own call, once per request, when the three are spent and no model has answered what
        happened (live 2026-10-11 01:07: recovery spent the third call and the user got a bare error card).
        Only a final synthesis may use it: at most MAX_CALLS + 1 entries (+ 1 more for a forgiven look)."""
        if self._last_word_given:
            return None
        self._last_word_given = True
        self._last_word = object()
        return self._last_word

    def reserve_recheck(self) -> object | None:
        """One more writing of the answer, once per request, only after a confirmed omission: the answer was told a
        step failed and did not say so (live 2026-10-11 02:40: "I switched to micro-sam and outlined the pith" while
        two steps had failed). Never a judge on every request: the omission is known from the answer's own reply
        (the ids it says it stated). Only a final synthesis may use it."""
        if self._recheck_given:
            return None
        self._recheck_given = True
        self._recheck = object()
        return self._recheck

    def release_final(self, reservation: object) -> bool:
        """Release only this request's active hold; spent attempts never change."""
        if reservation is self._final and self._final is not None:
            self._final = None
            return True
        return False

    async def dispatch(
        self,
        *,
        attempt_id: str,
        phase: str,
        model: str,
        effort: str,
        send: Callable[[], Awaitable[_Result]],
        reservation: object | None = None,
    ) -> _Result:
        """Admit once and enter the provider, preserving its result or exception.

        A distinct attempt ID is required for an intentional retry. Replays do
        not resend or return the original result; the caller keeps that task.
        Cancellation already requested on this task prevents dispatch. Once
        ``send`` is entered, failure, timeout or cancellation cannot refund it.
        """
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if phase not in _PHASES:
            raise AdmissionDenied("invalid-phase")
        if attempt_id in self._attempts:
            raise AdmissionDenied("duplicate-attempt")
        last_word = reservation is not None and (reservation is self._last_word or reservation is self._recheck)
        if last_word and phase != "final":
            raise AdmissionDenied("invalid-reservation")
        if reservation is not None and not last_word and (
            reservation is not self._final or phase not in _SYNTHESIS
        ):
            raise AdmissionDenied("invalid-reservation")
        if self.used >= MAX_CALLS and not last_word:
            raise AdmissionDenied("budget-exhausted")
        if reservation is None and self.remaining == 0:
            raise AdmissionDenied("final-reserved")

        # No await between these checks, consuming the slot and provider entry.
        attempt = Attempt(self.request_id, attempt_id, phase, model, effort)
        self._attempts[attempt_id] = attempt
        if last_word:
            if reservation is self._recheck:
                self._recheck = None
            else:
                self._last_word = None
        elif reservation is not None:
            self._final = None
        try:
            result = await send()
        except asyncio.CancelledError:
            self._attempts[attempt_id] = replace(attempt, status="canceled")
            raise
        except BaseException:
            self._attempts[attempt_id] = replace(attempt, status="failed")
            raise
        self._attempts[attempt_id] = replace(attempt, status="done")
        return result
