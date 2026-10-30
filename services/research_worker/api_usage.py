"""Local API cost estimates, never an invoice. No prompts, images or credentials.

Configured by the server lifecycle; standalone tests/benchmarks do not write to a user's
library. Research charges arrive over the existing worker IPC. SQLite upserts make
cumulative voice usage idempotent, including reconnects (each session has its own key).
"""
from __future__ import annotations

import contextlib
from contextlib import asynccontextmanager, contextmanager
import contextvars
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from .budget import PRICES

_active: UsageLedger | None = None
_provider_allowed = None
_local_only = contextvars.ContextVar("provider-local-only", default=False)
LOCAL_ONLY = "This example session runs locally. Open another session to use AI or cloud models."


class ProviderRefused(RuntimeError):
    """Session data policy refused the request before provider admission."""


@contextmanager
def local_only(enabled: bool):
    """A request's captured policy follows its asynchronous children across session switches."""
    token = _local_only.set(_local_only.get() or enabled)
    try:
        yield
    finally:
        _local_only.reset(token)


def require_provider():
    if _local_only.get() or (_provider_allowed is not None and not _provider_allowed()):
        raise ProviderRefused(LOCAL_ONLY)

# The optional monthly cap (C7; none by default). With one set, a call is admitted only at its own most (`bound`, from its
# list price and the most it may take in and give out), written into the ledger as its row before it is sent and settled to
# its real charge when its usage returns; one whose usage never returns stays counted at its most, across restarts too.
# A model whose price isn't known here can't be bounded, so under a cap it isn't asked. A row from before bounds (its charge
# unknown) counts at UNKNOWN_USD.
UNKNOWN_USD = 0.02
MOST_IN_TOKENS = 200_000  # what a call may take in, at most, when its caller says nothing smaller
MOST_OUT_TOKENS = 16_000
VOICE_BOUND_MIN = 2  # a voice session holds its next minutes ahead, renewed at each usage update, ended before they don't fit
VOICE_USD_PER_MIN = 0.05
# Voices billed by the tokens each response reports (voice/route.py records them): their open time writes no charge of
# its own, and their minutes ahead are held at this estimate (audio both ways, the pictures and the context re-read).
TOKEN_BILLED_USD_PER_MIN = {"gpt-realtime-2.1": 0.30}
_settling: contextvars.ContextVar = contextvars.ContextVar("api-usage-settling", default=None)




def tokens_in(prompt: str, pictures: int = 0, per_picture: int = 2_000, extra: str = "") -> int:
    """The most a call can take in: its words' and declarations' UTF-8 bytes (no token is shorter than a byte, so this is
    a true upper bound for text) and, per picture, a ceiling above what either provider counts for a camera frame (Gemini
    at high media resolution 1,120; OpenAI's high detail for 1920x1080 about 1,105), plus a margin for the request's own
    framing."""
    return len((prompt or "").encode()) + len((extra or "").encode()) + pictures * per_picture + 1_000


# Endpoints billed a fixed list price per call, not by tokens or by time: the price, from the provider's own pricing API.
# An endpoint billed by compute time (fal-ai/sam-3-1 itself, per second) has no limit the request can set: never here.
# Each says where its price was read and when, in every ledger row it prices, so a stale price is visible; the same
# price is in its service's `costs` (catalog/services/fal.toml). Read again: GET api.fal.ai/v1/models/pricing?endpoint_id=
CALL_PRICES = {"fal-ai/sam-3-1/image-rle": (0.01, "fal's pricing API", "2026-10-20")}  # 0.01 USD per unit (one call)


def call_price(model: str) -> float | None:
    """What one call to an endpoint billed per call costs at its list price (None: not billed per call)."""
    return CALL_PRICES[model][0] if model in CALL_PRICES else None


def call_basis(model: str) -> str:
    """A per-call charge's basis in the ledger: the list price, where it was read and when."""
    _usd, source, read = CALL_PRICES[model]
    return f"List price per call ({source}, read {read}) · before discounts"


def counted_unknown(model: str) -> tuple[float, bool]:
    """What one call to `model` whose usage never returns counts toward the month, as month() counts it: (its most,
    True) when a monthly limit admitted it at that most (its allowance is kept), else (UNKNOWN_USD, False)."""
    ledger = _active
    most = bound(model)
    if ledger is not None and most is not None and ledger.cap() is not None:
        return most, True
    return UNKNOWN_USD, False


def bound(model: str, in_tokens: int | None = None, out_tokens: int | None = None) -> float | None:
    """The most one call to `model` can cost, from its list price (None: no price known here, so it can't be bounded).
    `out_tokens` is the call's own output limit, which the provider enforces and which counts thinking with the visible
    answer (OpenAI's `max_output_tokens` includes reasoning; Gemini's includes thoughts: Google's Generate Content docs,
    "Token limits"), so nothing is added for thinking and its level is never lowered. An endpoint billed per call is
    bounded by that price."""
    if (per_call := call_price(model)) is not None:
        return per_call
    price = PRICES.get(model)
    if isinstance(price, dict) and "in" in price and "out" in price:
        return ((in_tokens or MOST_IN_TOKENS) * price["in"] + (out_tokens or MOST_OUT_TOKENS) * price["out"]) / 1e6
    if isinstance(price, dict):  # a picture model: its most for one picture in and one out
        return ((in_tokens or 20_000) * (price.get("image_in") or 0) + 4_000 * (price.get("image_out") or 0)) / 1e6
    return None


class CapReached(RuntimeError):
    """The monthly cloud cap the user set is reached: said, never a silent failure."""


def configure(ledger: UsageLedger | None, *, provider_allowed=None, outcome=None) -> None:
    """`outcome(model, ok, why)`: told how each call through `attempt` ended, as the running app's record of each model's
    real use (orchestrator/observed.call_used); never in a process that does not set it (the research worker's)."""
    global _active, _provider_allowed, _outcome
    _active = ledger
    _provider_allowed = provider_allowed
    _outcome = outcome


_outcome = None
# How a call ended that says nothing about the model: the app's own doing (its deadline or a newer request cancelled it;
# the cap kept it from being sent). Every other failure is the model's or its provider's, said with its reason.
NOT_THE_MODELS = ("cut-off", "capped")


def _told(model: str, ok: bool, outcome: str = "", exc: BaseException | None = None) -> None:
    hook = _outcome
    if hook is None or (not ok and outcome in NOT_THE_MODELS):
        return
    why = ""
    if not ok:  # why in a person's words with what to do (refusal), the provider's own words beside it
        words = " ".join(str(exc or "").split())
        found = refusal(exc, model=model)
        why = ((words or type(exc).__name__) if found["reason"] == "failed"
               else refusal_words(found) + (f" ({words[:120]})" if words else ""))
    try:
        hook(model, ok, why)
    except Exception:  # noqa: BLE001 - keeping the record never breaks the call
        pass


def provider_for(model: str) -> str:
    """Who serves a model, by its API id or its one name ("Claude Opus 5.5")."""
    model = str(model or "").lower()
    if model.startswith(('gemini', 'veo')) or model == 'google_search':
        return 'Google'
    if model.startswith('claude'):
        return 'Anthropic'
    if model.startswith('gpt') or model == 'web_search':
        return 'OpenAI'
    return 'Unknown provider'


class UsageLedger:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(root / 'usage.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value REAL)')
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('started', ?)", (time.time(),))
        self.db.execute('''CREATE TABLE IF NOT EXISTS charges (
            id TEXT PRIMARY KEY, day TEXT, at REAL, provider TEXT, model TEXT,
            area TEXT, usd REAL, basis TEXT, units REAL)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS charges_day ON charges(day)')
        self.db.execute('CREATE TABLE IF NOT EXISTS unknown_allowances (id TEXT PRIMARY KEY, usd REAL NOT NULL)')
        # Older versions kept failed calls' admission maximum in the charge column. Preserve that enforcement
        # allowance across upgrades while restoring the actual charge to unknown for every analytics consumer.
        self.db.execute("INSERT OR IGNORE INTO unknown_allowances SELECT id, usd FROM charges "
                        "WHERE usd IS NOT NULL AND basis LIKE '%: counted at its most'")
        self.db.execute("UPDATE charges SET usd=NULL WHERE basis LIKE '%: counted at its most'")
        self.db.commit()
        self.failures = 0
        self._cap = ...  # the monthly cap, read at most every few seconds (cap()), fresh inside admission
        self._cap_read = 0.0

    def record(self, model: str, usd: float | None = None, *, provider: str | None = None,
               area: str = 'Live', basis: str = 'List-price estimate', units: float = 1,
               key: str | None = None, day: str | None = None) -> None:
        if usd is not None and (not math.isfinite(usd) or usd < 0):
            usd = None
        with self.lock, self.db:
            if usd is not None and key:
                self.db.execute('DELETE FROM unknown_allowances WHERE id=?', (key,))
            self.db.execute('''INSERT INTO charges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET usd=excluded.usd, basis=excluded.basis, units=excluded.units''',
                (key or uuid.uuid4().hex, day or time.strftime('%Y-%m-%d'), time.time(),
                 provider or provider_for(model), model, area, usd, basis, units))

    # --- the optional monthly cap (C7) ---------------------------------------------------------------------------
    def cap(self, fresh: bool = False) -> float | None:
        """The monthly cap (None: none). Admission reads it fresh inside its transaction (`fresh`), so a cap set through
        another connection holds at once; elsewhere it is read at most every few seconds."""
        if fresh or self._cap is ... or time.monotonic() - self._cap_read > 5.0:
            with self.lock:
                row = self.db.execute("SELECT value FROM metadata WHERE key='monthly-cap'").fetchone()
            self._cap = None if row is None or row[0] is None or row[0] < 0 else float(row[0])
            self._cap_read = time.monotonic()
        return self._cap

    @contextmanager
    def _immediate(self):
        """One write transaction taken before the balance is read (SQLite's BEGIN IMMEDIATE): every connection to this
        ledger, in any process, admits one call at a time against the same rows (Codex's review of d1252d67)."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.db.rollback()
                raise
            else:
                self.db.commit()

    def set_cap(self, usd: float | None) -> None:
        """A monthly cap on the app's own cloud calls (None: no cap). Kept in the ledger, so a restart keeps it."""
        if usd is not None and (not math.isfinite(usd) or usd < 0):
            raise ValueError("a monthly cap is a sum of dollars, zero or more")
        with self.lock, self.db:
            self.db.execute("INSERT INTO metadata VALUES ('monthly-cap', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (-1.0 if usd is None else float(usd),))
        self._cap = None if usd is None else float(usd)

    def month(self, month: str | None = None) -> dict:
        """This month's enforcement total: known estimates, in-flight holds, and retained unknown-cost allowances.
        Older unbounded unknown charges count at UNKNOWN_USD. Use breakdown() for usage analytics, not this total."""
        month = month or time.strftime('%Y-%m')
        with self.lock:
            priced, unknown, held, allowance = self.db.execute(
                "SELECT COALESCE(SUM(c.usd), 0), SUM(c.usd IS NULL), "
                "COALESCE(SUM(CASE WHEN c.basis LIKE 'In flight%' THEN c.usd END), 0), "
                "COALESCE(SUM(CASE WHEN c.usd IS NULL THEN COALESCE(a.usd, ?) ELSE 0 END), 0) "
                "FROM charges c LEFT JOIN unknown_allowances a ON a.id=c.id WHERE c.day LIKE ?",
                (UNKNOWN_USD, f"{month}-%")).fetchone()
        spent = float(priced) + float(allowance)
        return {"month": month, "estimatedUsd": round(spent, 6), "unknownCalls": int(unknown or 0),
                "unknownAllowanceUsd": round(float(allowance), 6),
                "inFlightUsd": round(float(held or 0), 6), "cap": self.cap()}

    def admit(self, model: str, provider: str, area: str, most: float | None, key: str | None = None) -> str | None:
        """One call, admitted within the cap at its most (`most`), its row written before it is sent; else CapReached.
        None when there is no cap (nothing is reserved)."""
        if self.cap() is None and self.cap(fresh=True) is None:
            return None
        if most is None:
            raise CapReached(f"{model}'s price isn't known here, so its cost can't be bounded: under the monthly cap you "
                             "set it isn't asked (raise or remove the cap in the spending panel to use it)")
        token = key or uuid.uuid4().hex
        with self._immediate():  # the check and the reservation are one step: two calls never spend the same allowance
            cap = self.cap(fresh=True)
            if cap is None:
                return None
            now = self.month()
            if now["estimatedUsd"] + most > cap:
                raise CapReached(f"the monthly cap you set for cloud models is reached (about ${now['estimatedUsd']:.2f} "
                                 f"of ${cap:.2f} this month, and this call may cost up to ${most:.2f}): {provider} "
                                 "wasn't asked. Raise or remove it in the spending panel")
            self.db.execute(
                "INSERT INTO charges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET usd=excluded.usd, basis=excluded.basis",
                (token, time.strftime('%Y-%m-%d'), time.time(), provider, model, area, float(most),
                 'In flight: counted at its most until its usage returns', 1))
        return token

    def renew(self, token: str, most: float, model: str, provider: str, area: str, month: str | None = None) -> bool:
        """Hold `most` again under `token` (a voice session's minutes ahead) in one write transaction, against the cap read
        now and every other row of the month: False when it no longer fits (its hold is then released)."""
        month = month or time.strftime('%Y-%m')
        with self._immediate():
            cap = self.cap(fresh=True)
            if cap is None:
                self.db.execute("DELETE FROM charges WHERE id=?", (token,))
                return True
            held = self.db.execute("SELECT COALESCE(usd, 0) FROM charges WHERE id=? AND day LIKE ?",
                                   (token, f"{month}-%")).fetchone()
            spent = self.month(month)["estimatedUsd"] - (float(held[0]) if held else 0.0)
            if spent + most > cap + 1e-9:
                self.db.execute("UPDATE charges SET usd=0, basis=? WHERE id=?", ("Released: it no longer fit the cap", token))
                return False
            self.db.execute(
                "INSERT INTO charges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET usd=excluded.usd, basis=excluded.basis",
                (token, f"{month}-{time.strftime('%d')}" if month == time.strftime('%Y-%m') else f"{month}-01", time.time(),
                 provider, model, area, float(most), 'In flight: held ahead, renewed on time', 1))
        return True

    def settle(self, token: str, usd: float | None, basis: str) -> None:
        """Settle a reserved call's estimate; unknown cost retains its enforcement allowance separately from analytics."""
        if usd is not None and (not math.isfinite(usd) or usd < 0):
            usd = None
        with self.lock, self.db:
            if usd is None:
                self.db.execute("INSERT INTO unknown_allowances SELECT id, usd FROM charges WHERE id=? AND usd IS NOT NULL "
                                "ON CONFLICT(id) DO UPDATE SET usd=excluded.usd", (token,))
                self.db.execute("UPDATE charges SET usd=NULL, basis=? WHERE id=?",
                                (f"{basis}; cost unknown, limit allowance retained", token))
            else:
                self.db.execute("DELETE FROM unknown_allowances WHERE id=?", (token,))
                self.db.execute("UPDATE charges SET usd=?, basis=? WHERE id=?", (float(usd), basis, token))

    def breakdown(self, period: str | None = None) -> list[dict]:
        """A period's tracked charges (`period`: a month, YYYY-MM, or a day, YYYY-MM-DD; default this month) by provider,
        model and area: known charges and records of unknown cost, never holds for work in progress (month()'s
        inFlightUsd) or the allowance an unknown charge is counted at under a limit (the Spending menu's analytics)."""
        period = period or time.strftime('%Y-%m')
        with self.lock:
            rows = self.db.execute(
                """SELECT provider, model, area, COUNT(*), COALESCE(SUM(usd), 0), SUM(usd IS NULL),
                   GROUP_CONCAT(DISTINCT basis), SUM(units) FROM charges
                   WHERE day LIKE ? AND COALESCE(basis, '') NOT LIKE 'In flight%' AND COALESCE(basis, '') NOT LIKE 'Released%'
                   AND id NOT LIKE '%:ahead' AND id NOT LIKE 'research-hold:%'
                   GROUP BY provider, model, area ORDER BY provider, model, area""", (f"{period}%",)).fetchall()
        return [dict(provider=p, model=m, area=a, events=n, estimatedUsd=round(cost, 6), unpriced=nul, basis=b,
                     units=units) for p, m, a, n, cost, nul, b, units in rows]

    def fits(self, most: float) -> bool:
        """Whether a run of up to `most` fits what is left of the month (True with no cap)."""
        cap = self.cap()
        return cap is None or self.month()["estimatedUsd"] + most <= cap

    def over(self) -> bool:
        """Whether this month's spend, holds included, has passed the cap (a voice session in progress ends then)."""
        cap = self.cap()
        return cap is not None and self.month()["estimatedUsd"] > cap + 1e-9

    def seed_research(self, day: str, research_usd: float) -> None:
        """Freeze retained spend once, so late provider charges cannot erase earlier spend."""
        with self.lock, self.db:
            tracked = self.db.execute("SELECT COALESCE(SUM(usd), 0) FROM charges WHERE day=? AND area IN ('Research', 'Presentations')", (day,)).fetchone()[0]
            self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)',
                            (f'research-base:{day}', max(0, research_usd - tracked)))

    def summary(self, day: str, research_usd: float = 0) -> dict:
        self.seed_research(day, research_usd)
        with self.lock:
            rows = self.db.execute('''SELECT provider, model, area, COUNT(*), COALESCE(SUM(usd), 0),
                SUM(usd IS NULL), GROUP_CONCAT(DISTINCT basis), SUM(units)
                FROM charges WHERE day=? GROUP BY provider, model, area ORDER BY provider, model, area''', (day,)).fetchall()
            started = self.db.execute("SELECT value FROM metadata WHERE key='started'").fetchone()[0]
            earlier = self.db.execute("SELECT value FROM metadata WHERE key=?", (f"research-base:{day}",)).fetchone()[0]
        entries = [dict(provider=p, model=m, area=a, events=n, estimatedUsd=round(cost, 6),
                        unpriced=nul, basis=b, units=units) for p, m, a, n, cost, nul, b, units in rows]
        # Existing research totals predate provider-level tracking. Do not count new research twice.
        if earlier > 0.001:  # the old ledger rounds each run to four decimal places
            entries.append(dict(provider='Earlier research', model='Provider breakdown unavailable', area='Research',
                                events=0, estimatedUsd=round(earlier, 6), unpriced=0, units=0,
                                basis='Retained research ledger'))
        return dict(day=day, trackingSince=started, entries=entries,
                    estimatedUsd=round(sum(r['estimatedUsd'] for r in entries), 6),
                    unpriced=sum(r['unpriced'] for r in entries), recordingErrors=self.failures)

    def close(self) -> None:
        with self.lock:
            self.db.close()


def record(model: str, usd: float | None = None, **kwargs) -> None:
    if _active is None:
        return
    settling = _settling.get()
    if settling is not None and settling[1] == model and not kwargs.get("key"):  # the reserved call's own charge
        _settling.set(None)
        try:
            _active.settle(settling[0], usd, kwargs.get("basis") or "List-price estimate")
        except (OSError, sqlite3.Error, ValueError, TypeError):
            _active.failures += 1
        return
    try:
        _active.record(model, usd, **kwargs)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        _active.failures += 1  # tracking must never break the actual interaction; panel exposes gaps


# Why a provider call did not return, from the error itself (its type and HTTP status), never from its words alone:
# the meter says when a provider is slow, rate limited or refusing a key, so a slow answer is never mistaken for a
# regression and a quota problem is never hidden (live 2026-10-11: nine Gemini calls cut off at the app's own 15 s
# and 20 s deadlines, recorded only as "Interrupted or failed").
OUTCOMES = {"cut-off": "cut off by the app's deadline or cancelled", "rate-limit": "rate limit or quota (HTTP 429)",
            "auth": "key, permission or billing refused (HTTP 401/403)", "server": "provider error (HTTP 5xx)",
            "connection": "connection failed", "failed": "failed",
            "capped": "not asked: the monthly cap you set is reached"}
RECENT_S = 600.0
_recent: list[tuple[float, str, str, str]] = []  # (monotonic, provider, area, outcome), the last RECENT_S
_recent_lock = threading.Lock()


def outcome_of(exc: BaseException) -> str:
    """What a provider call's exception says happened: one of OUTCOMES (its own `outcome` when its caller knew it)."""
    import asyncio

    if getattr(exc, "outcome", None) in OUTCOMES:
        return exc.outcome
    if isinstance(exc, (asyncio.CancelledError, TimeoutError)):
        return "cut-off"
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    words = f"{type(exc).__name__} {exc}".lower()
    if status == 429 or "resource_exhausted" in words or "insufficient_quota" in words or "ratelimit" in words:
        return "rate-limit"
    if status in (401, 403) or "permission_denied" in words or "unauthenticated" in words or "billing" in words:
        return "auth"
    if isinstance(status, int) and 500 <= status < 600:
        return "server"
    if isinstance(exc, (ConnectionError, OSError)) or "connect" in type(exc).__name__.lower():
        return "connection"
    return "failed"


# Why a model could not be used, as a reason a person understands and what they can do about it: the one mapping every
# surface reads (the researcher's steps and result, the brain's answer through brain/dispatch.refusal, the model cards'
# last use, the voice's diagnostics, the engines' credit refusal). Will's try-out of 29 Oct: a video failed because
# Google had retired its model, and the voice said it without the reason. It is read from the error's own HTTP status and
# provider codes, and from the few fixed refusal texts a provider sends with no code of its own (Anthropic's credit
# refusal is a 400 whose only sign is its words); the provider's own words stay beside it as the detail, never instead.
CREDIT_CODES = frozenset({"insufficient_quota", "insufficient_credits", "billing_not_active", "credit_balance_exhausted"})
CREDIT_TEXTS = ("credit balance is too low", "you have no credits remaining", "prepayment credits are depleted")
RETIRED_CODES = frozenset({"model_not_found"})
RETIRED_TEXTS = ("has been deprecated", "is no longer available", "has been shut down", "has been retired")
KEY_CODES = frozenset({"invalid_api_key", "api_key_invalid", "permission_denied", "unauthenticated", "authentication_error"})
KEY_TEXTS = ("api key not valid", "invalid api key", "incorrect api key", "invalid x-api-key")
# (what happened, what to do): {provider} is the provider's name ("the provider" when not known), {your} "your Google"
# (else "your"), {key} its key's name with a space ("Gemini ", else ""), {model} the model's one name (names.py).
KEY_NAMES = {"Google": "Gemini"}  # a provider whose key is called after its API (Models › Keys says "Gemini")
REASONS = {
    "retired": ("{provider} has retired {model}", "use its successor, or choose another model"),
    "no-credit": ("{your} account is out of credit", "add credit to {your} account, or choose another model"),
    "no-key": ("no {key}key is set here", "add your {key}key in Models › Keys"),
    "key-refused": ("your {key}key was refused", "check your {key}key in Models › Keys"),
    "rate-limit": ("{provider} is limiting requests right now", "try again in a minute, or choose another model"),
    "server": ("{provider} had an error on its side", "try again in a moment"),
    "connection": ("{provider} could not be reached", "check this Mac's internet connection, then try again"),
    "cut-off": ("it took too long and was cut off", "try again"),
    "capped": ("the monthly cap you set for cloud models is reached", "raise or remove it in Spending"),
    "failed": ("{provider} could not run {model}", "try again, or choose another model"),
}
# Where an adapter's kept words give the HTTP status: "HTTP 404", "Error code: 429", "ClientError: 404 NOT_FOUND",
# "'code': 404", "Anthropic 400: …", or the words starting with it.
_STATUS = re.compile(r"(?:\bHTTP|\bError code:|\bstatus(?: code)?:?|['\"]code['\"]:|\b[A-Za-z]*Error:"
                     r"|\b(?:Anthropic|OpenAI|Google|Gemini|fal))\s*(\d{3})\b|^\s*(\d{3})\b", re.I)


def _error_parts(error) -> tuple[int | None, str, set[str]]:
    """(its HTTP status, its words in lower case, its provider codes) from an exception, an operation's error dict
    ({code, status, message}) or the words an adapter kept ("RuntimeError: Anthropic 400: Your credit balance…")."""
    import json as _json

    codes: set[str] = set()
    if isinstance(error, dict):
        status = error.get("code") if isinstance(error.get("code"), int) else None
        for key in ("status", "code", "type"):
            if isinstance(error.get(key), str):
                codes.add(error[key].lower())
        words = " ".join(str(error.get(k) or "") for k in ("status", "message", "type", "code"))
    elif isinstance(error, BaseException):
        status = next((s for s in (getattr(error, "status_code", None), getattr(error, "code", None),
                                   getattr(getattr(error, "response", None), "status_code", None))
                       if isinstance(s, int) and 100 <= s < 600), None)
        for key in ("status", "code", "type"):
            if isinstance(getattr(error, key, None), str):
                codes.add(getattr(error, key).lower())
        body = getattr(error, "body", None)
        if isinstance(body, str):
            with contextlib.suppress(ValueError):
                body = _json.loads(body)
        if isinstance(body, dict):
            inner = body.get("error") if isinstance(body.get("error"), dict) else body
            codes |= {str(inner.get(k)).lower() for k in ("code", "type", "status") if isinstance(inner.get(k), str)}
        words = f"{type(error).__name__} {error} {getattr(error, 'message', '') or ''} {body if body else ''}"
    else:
        status, words = None, str(error or "")
    if status is None:
        found = _STATUS.search(words)
        status = int(found.group(1) or found.group(2)) if found else None
    return status, " ".join(words.split()).lower(), codes


def reason_of(error, *, keys: tuple[str, ...] = ()) -> str:
    """Why a model call failed: one of REASONS, from the error itself. `keys`: the environment keys the call needs, any
    one of which is enough; with none set here, an error that never reached the provider (no HTTP status) is "no-key"."""
    import asyncio
    import os

    if isinstance(error, BaseException):
        if getattr(error, "outcome", None) in ("cut-off", "capped"):
            return error.outcome
        if isinstance(error, CapReached):
            return "capped"
        if isinstance(error, (asyncio.CancelledError, TimeoutError)):
            return "cut-off"
    status, words, codes = _error_parts(error)
    if keys and status is None and not any(os.environ.get(k) for k in keys):
        return "no-key"  # it never reached the provider, and none of the keys it needs is set here
    if words.startswith(("capreached", "cap reached")):
        return "capped"
    if codes & CREDIT_CODES or any(c in words for c in CREDIT_CODES) or any(t in words for t in CREDIT_TEXTS):
        return "no-credit"
    if codes & RETIRED_CODES or "model_not_found" in words or any(t in words for t in RETIRED_TEXTS) or (
            status in (404, 410) and "model" in words):
        return "retired"
    if status == 429 or "resource_exhausted" in codes or "resource_exhausted" in words or "rate_limit" in words:
        return "rate-limit"
    if status in (401, 403) or codes & KEY_CODES or any(c in words for c in (*KEY_CODES, *KEY_TEXTS)):
        return "key-refused"
    if status is None and ("api key" in words or "api_key" in words or "missing key" in words):
        return "no-key"  # the client refused before sending: no key it could use (google-genai's ValueError)
    if isinstance(status, int) and 500 <= status < 600:
        return "server"
    if isinstance(error, BaseException) and outcome_of(error) in ("connection", "cut-off"):
        return outcome_of(error)
    if any(w in words for w in ("connecterror", "connectionerror", "connecttimeout", "apiconnectionerror")):
        return "connection"
    return "failed"


def refusal(error, *, model: str = "", provider: str = "", keys: tuple[str, ...] = (), successor: str = "") -> dict:
    """{reason, said, todo}: why `model` (its one name) could not be used, in a person's words, and what they can do.
    `successor`: the model's successor, when its catalog names one, said as what to use instead of a retired one."""
    reason = reason_of(error, keys=keys)
    who = provider or (provider_for(model) if model else "")
    if who in ("", "Unknown provider"):  # else who its own words name ("Anthropic 400: …"), when they name one
        _, words, _ = _error_parts(error)
        who = next((name for name, said in (("Anthropic", "anthropic"), ("OpenAI", "openai"), ("Google", "gemini"),
                                            ("Google", "google")) if said in words), "")
    said, todo = REASONS[reason]
    fields = {"provider": who or "the provider", "model": model or "this model", "your": f"your {who}" if who else "your",
              "key": f"{KEY_NAMES.get(who, who)} " if who else ""}
    said, todo = said.format(**fields), todo.format(**fields)
    if reason == "retired" and successor:
        todo = f"use {successor}, its successor"
    return {"reason": reason, "said": said[:1].upper() + said[1:], "todo": todo}


def refusal_clause(found: dict) -> str:
    """Its "said" to follow other words ("… couldn't be used: your Anthropic account is out of credit"): a name keeps
    its capital."""
    said = found["said"]
    first = said.split(" ", 1)[0]
    return said[:1].lower() + said[1:] if first in ("Your", "No", "It", "The") else said


def refusal_words(found: dict) -> str:
    """One sentence of a refusal: what happened, then what to do ("Google has retired Veo 3.1 Fast; use …")."""
    return f"{found['said']}; {found['todo']}" if found.get("todo") else found["said"]


def _seen(provider: str, area: str, outcome: str) -> None:
    now = time.monotonic()
    with _recent_lock:
        _recent.append((now, provider, area, outcome))
        while _recent and now - _recent[0][0] > RECENT_S:
            _recent.pop(0)


# How long each model took lately (models_in_use.py: who's powering what, with its speed): a call's seconds by the name
# it was made under (a cloud model's API name in `attempt`; an operation's runs by "<setting>:<option>",
# operations.Runner), only calls that returned. The last RECENT_S, at most LATENCY_MAX in all.
LATENCY_MAX = 2000
_latency: list[tuple[float, str, float]] = []  # (monotonic, name, ms)


def note_latency(name: str, ms: float) -> None:
    now = time.monotonic()
    with _recent_lock:
        _latency.append((now, str(name), float(ms)))
        while _latency and (now - _latency[0][0] > RECENT_S or len(_latency) > LATENCY_MAX):
            _latency.pop(0)


def latency(name: str, window_s: float = RECENT_S) -> dict | None:
    """{"p50Ms", "calls"} of `name`'s calls that returned in the last `window_s`, or None when there were none."""
    now = time.monotonic()
    with _recent_lock:
        took = sorted(ms for at, n, ms in _latency if n == name and now - at <= window_s)
    if not took:
        return None
    return {"p50Ms": round(took[(len(took) - 1) // 2], 1), "calls": len(took)}


def recent(window_s: float = RECENT_S) -> dict:
    """Per provider, the calls of the last `window_s` by outcome ("ok" and OUTCOMES' keys)."""
    now = time.monotonic()
    out: dict[str, dict[str, int]] = {}
    with _recent_lock:
        rows = [r for r in _recent if now - r[0] <= window_s]
    for _at, provider, _area, outcome in rows:
        counts = out.setdefault(provider, {})
        counts[outcome] = counts.get(outcome, 0) + 1
    return out


@asynccontextmanager
async def attempt(model: str, *, provider: str | None = None, area: str = 'Live', in_tokens: int | None = None,
                  out_tokens: int | None = None, most: float | None = None, unbounded: str | None = None):
    """Sent calls without returned usage are unknown charges, never silently free. Each call's outcome is kept, with
    why it did not return when it did not (outcome_of). Under a monthly cap, the call is admitted at its most first (its
    caller's `most`, else `bound` from its price and token limits) or refused (CapReached) before anything is sent; its row
    is then settled by the usage its caller records next (`record`/`response` in the same task). `unbounded`: why a
    feature the call uses can't be bounded (a search billed per query with no limit in the request): under a cap it is
    refused saying so, never admitted on a guess."""
    require_provider()
    provider = provider or provider_for(model)
    ledger = _active
    try:
        if unbounded and ledger is not None and (ledger.cap() is not None or ledger.cap(fresh=True) is not None):
            raise CapReached(f"under the monthly cap you set, {provider} isn't asked: {unbounded}. Raise or remove the "
                             "cap in the spending panel to use it")
        token = ledger.admit(model, provider, area, most if most is not None else bound(model, in_tokens, out_tokens)) \
            if ledger is not None else None
    except CapReached:
        _seen(provider, area, "capped")  # the meter's Providers says why it wasn't asked
        raise
    if token is not None:
        _settling.set((token, model))
    began = time.monotonic()
    try:
        yield
    except BaseException as exc:
        outcome = outcome_of(exc)
        _seen(provider, area, outcome)
        if getattr(exc, "ran", None) is False:  # its caller knows the provider refused it before running it, or it was never sent
            record(model, 0.0, provider=provider, area=area, basis=f'Refused before it ran: {OUTCOMES[outcome]}; nothing billed')
        else:
            record(model, provider=provider, area=area, basis=f'Interrupted or failed: {OUTCOMES[outcome]}; usage unavailable')
            _told(model, False, outcome, exc)
        raise
    _seen(provider, area, "ok")
    note_latency(model, (time.monotonic() - began) * 1000)
    _told(model, True)


def search_queries(result) -> list[str]:
    """Only observable search arguments, never thought steps or signatures."""
    queries = []
    for step in getattr(result, 'steps', None) or []:
        if getattr(step, 'type', None) == 'google_search_call':
            args = getattr(step, 'arguments', None)
            values = args.get('queries', []) if isinstance(args, dict) else getattr(args, 'queries', [])
            queries.extend(q for q in values or [] if isinstance(q, str))
    for candidate in getattr(result, 'candidates', None) or []:
        grounding = getattr(candidate, 'grounding_metadata', None)
        queries.extend(getattr(grounding, 'web_search_queries', None) or [])
    return queries


def response(model: str, result, *, provider: str, area: str = 'Live', tier: str | None = None,
             search: bool = False) -> None:
    if provider == 'Google':
        usage = getattr(result, 'usage_metadata', None)
        incoming = getattr(usage, 'prompt_token_count', None)
        outgoing = getattr(usage, 'candidates_token_count', None)
        if outgoing is not None:
            outgoing += getattr(usage, 'thoughts_token_count', 0) or 0
        if usage is None:
            usage = getattr(result, 'usage', None)
            incoming = getattr(usage, 'total_input_tokens', None)
            outgoing = getattr(usage, 'total_output_tokens', None)
            if outgoing is not None:
                outgoing += getattr(usage, 'total_thought_tokens', 0) or 0
    else:
        usage = getattr(result, 'usage', None)
        incoming = getattr(usage, 'input_tokens', None)
        outgoing = getattr(usage, 'output_tokens', None)  # includes reasoning
    price = PRICES.get(model)
    known = isinstance(price, dict) and 'in' in price and tier in (None, 'default', 'auto')
    usd = ((incoming * price['in'] + outgoing * price['out']) / 1e6
           if known and incoming is not None and outgoing is not None else None)
    record(model, usd, provider=provider, area=area,
           basis='Reported tokens · list price before discounts' if usd is not None else 'Usage or price unavailable')
    if search:
        queries = search_queries(result)
        record('google_search', len(queries) * PRICES['google_search'] if queries else None,
               provider='Google', area=area, units=len(queries),
               basis='Reported queries · before free allowance' if queries else 'Search query usage unavailable')


RENEW_S = 30.0  # a voice session's allowance is renewed this often, by the clock, whether or not usage arrives


class VoiceUsage:
    """A voice session's spend and, under a monthly cap, its allowance: its next VOICE_BOUND_MIN minutes held ahead in a
    row of their own, renewed on time by the clock (`renew`, every RENEW_S from voice/route.py, and at each usage update)
    in one write transaction; its spend counted at the larger of the seconds the provider reported and the time it has
    been open (sent but unreported audio is never free); each calendar month its own rows; released whenever it ends.

    - **One at a time, then over** (Codex's diagnosis of 89e834b5, point 6): renewal, usage and finish are serialized by
      one lock, and finish is terminal: a renewal already running completes before finish settles, and one after it
      holds nothing and says False, so a finished session's hold is never restored.
    - **A month's end** (point 5): its seconds up to the boundary (the last observation plus the wall time from it to the
      first second of the new month, never more than it has used) are the old month's; the new month's allowance is held
      before its seconds are written there."""

    def __init__(self, model: str, clock=None, wall=None):
        require_provider()
        self.model, self.key = model, uuid.uuid4().hex
        self.clock = clock or (lambda: time.monotonic())  # looked up at each call: a test's clock patches the module's
        self.wall = wall or (lambda: time.time())
        self.lock = threading.RLock()
        # The ledger it holds its minutes in, kept for its whole life: renewed, recorded and released there, whatever the
        # app's ledger is by then (Codex's hold diagnosis, 2026-10-14: settling on the module's at the end could release
        # nothing, or another ledger's row).
        self.ledger: UsageLedger | None = _active
        self.finished = False
        self.started = self.clock()
        self.seconds: float | None = None
        self.month = self._month_now()
        self.day = time.strftime('%Y-%m-%d', time.localtime(self.wall()))  # the day this month's row is filed on
        self.month_from = 0.0  # its seconds when this month's segment began (a session that runs past midnight of a month)
        self.seen_used, self.seen_wall = 0.0, self.wall()  # its last observation: seconds used, and when (wall clock)
        if self.ledger is not None and not self.renew():  # under a monthly cap: its first minutes, or refused before it opens
            self.finish()
            raise CapReached("the monthly cap you set for cloud models doesn't leave room for a voice session: raise or "
                             "remove it in the spending panel")

    def _month_now(self) -> str:
        return time.strftime('%Y-%m', time.localtime(self.wall()))

    @property
    def hold(self) -> str:
        return self._hold_of(self.month)

    def _hold_of(self, month: str) -> str:
        return f"{self.key}:{month}:ahead"

    def _next_month(self) -> tuple[str, float]:
        """The month after this one, and when it begins (wall clock)."""
        y, m = (int(x) for x in self.month.split("-"))
        nxt = f"{y + (m == 12):04d}-{m % 12 + 1:02d}"
        return nxt, time.mktime(time.strptime(nxt + "-01", "%Y-%m-%d"))

    def _hold_next(self) -> bool:
        """Within its minutes ahead of a month's end, the next month's are held too, before any of its seconds are
        used there (Codex's recheck of 73626a0d: admission across the boundary, not only accounting)."""
        if self.ledger is None:
            return True
        nxt, begins = self._next_month()
        if begins - self.wall() > VOICE_BOUND_MIN * 60:
            return True
        return self.ledger.renew(self._hold_of(nxt), VOICE_BOUND_MIN * self.per_min, self.model, 'OpenAI', 'Voice', nxt)

    def used(self) -> float:
        """Its seconds so far: what the provider reported, or the time it has been open, whichever is more."""
        return max(self.seconds or 0.0, self.clock() - self.started)

    def _roll(self) -> bool:
        """The month changed since its last observation: the old month gets its seconds to the boundary and its hold is
        released; the new month's allowance is held first (False: it doesn't fit). True otherwise."""
        now = self._month_now()
        if now == self.month:
            return True
        boundary = time.mktime(time.strptime(now + "-01", "%Y-%m-%d"))
        at_boundary = min(self.used(), self.seen_used + max(0.0, boundary - self.seen_wall))
        self._record(at_boundary, "Open time, to the end of the month")
        if self.ledger is not None:
            self.ledger.settle(self.hold, 0.0, "Voice session's minutes ahead: the month ended")
        self.month_from, self.month, self.day = at_boundary, now, time.strftime('%Y-%m-%d', time.localtime(self.wall()))
        return self._hold_ahead()

    def _hold_ahead(self) -> bool:
        if self.ledger is None:
            return True
        return self.ledger.renew(self.hold, VOICE_BOUND_MIN * self.per_min, self.model, 'OpenAI', 'Voice', self.month)

    def renew(self) -> bool:
        """Its next minutes held again and its spend counted so far, in one transaction against the cap now; False when
        they no longer fit, or once it has finished: then the session must end (voice/route.py)."""
        with self.lock:
            if self.finished:
                return False
            if self.ledger is None:
                return True
            rolled = self._roll()  # a new month's allowance is held before its seconds are written there
            self._record(self.used(), 'Open time, counted until usage is reported')
            return self._hold_ahead() and self._hold_next() and rolled  # against what it has spent now

    def over_cap(self) -> bool:
        """The next minutes no longer fit (renewed now): the session ends."""
        return not self.renew()

    def update(self, seconds) -> None:
        if isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0:
            with self.lock:
                if self.finished:
                    return
                self.seconds = max(self.seconds or 0, seconds)
                self._roll()
                self._record(self.used(), 'Provider-reported seconds')

    @property
    def per_min(self) -> float:
        """Its minutes ahead are held at this rate: GPT-Live's per-minute price, or a token-billed voice's estimate."""
        return TOKEN_BILLED_USD_PER_MIN.get(self.model, VOICE_USD_PER_MIN)

    def _record(self, seconds: float, basis: str) -> None:
        here = max(0.0, seconds - self.month_from)  # this month's seconds only
        if self.model in TOKEN_BILLED_USD_PER_MIN:  # its charges are its responses' tokens, recorded as they come
            self.seen_used, self.seen_wall = max(self.seen_used, seconds), self.wall()
            return
        if self.ledger is not None:  # its own ledger's row, as record() would write the app's
            try:
                self.ledger.record(self.model, here * VOICE_USD_PER_MIN / 60 if self.model == 'gpt-live-1' else None,
                                   provider='OpenAI', area='Voice', basis=basis, units=here, key=f"{self.key}:{self.month}",
                                   day=self.day)
            except (OSError, sqlite3.Error, ValueError, TypeError):
                self.ledger.failures += 1  # tracking must never break the actual interaction; panel exposes gaps
        self.seen_used, self.seen_wall = max(self.seen_used, seconds), self.wall()

    def finish(self, confirmed: bool = True, why: str = "") -> None:
        """Ended (also a session that never started): its spend counted and its hold released, once, after any renewal
        in progress; nothing renews it after. Not `confirmed` (its provider's end couldn't be confirmed, `why`): its
        minutes ahead are kept counted against the limit as an unknown cost, the ledger's tracking gap, never released as
        if it had surely ended."""
        with self.lock:
            if self.finished:
                return
            self.finished = True
            # A final usage event may be unavailable on disconnect. Include the remaining wall time,
            # clearly estimated, rather than claiming the last periodic usage update is the whole bill.
            self._roll()
            self._record(self.used(), 'Session duration estimate')
            if self.ledger is not None:
                if confirmed:
                    self.ledger.settle(self.hold, 0.0, 'Voice session ended: its minutes ahead released')
                    self.ledger.settle(self._hold_of(self._next_month()[0]), 0.0,
                                       "Voice session ended: next month's minutes ahead released")
                else:
                    said = f"Voice session's end not confirmed ({why or 'its provider did not confirm it'})"
                    # The time after its estimate is unknown, cap or no cap: a charge of unknown cost, the ledger's own
                    # tracking gap (Spending's unknown costs), so no clean final estimate is shown for it.
                    try:
                        self.ledger.record(self.model, None, provider='OpenAI', area='Voice', units=0,
                                           basis=f"{said}: its time after this is unknown",
                                           key=f"{self.key}:unconfirmed", day=self.day)
                    except (OSError, sqlite3.Error, ValueError, TypeError):
                        self.ledger.failures += 1  # tracking must never break the actual interaction; panel exposes gaps
                    self.ledger.settle(self.hold, None, said)  # under a cap: its minutes ahead stay counted
                    self.ledger.settle(self._hold_of(self._next_month()[0]), None, said)
