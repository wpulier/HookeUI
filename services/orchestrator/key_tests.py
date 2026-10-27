"""Whether a key works, by one cheap authenticated call to its service (catalog/services/<id>.toml `test`; P1 of
docs/design/2026-10-12-the-open-catalog.md).

Three outcomes, never confused, decided by the HTTP status and the exception's type, never by message text:
- **accepted**: the service answered the key;
- **rejected**: the service refused the key (401/403, and Gemini's 400 for a bad key on a call with no body);
- **unchecked**: the service could not say (a timeout, no connection, 429 rate limit or quota, a server error).

The key is passed in, never read from the environment here, and never appears in what is returned (`detail` is
redacted). Each call is a GET that costs nothing.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

TIMEOUT_S = 15.0


@dataclass(frozen=True)
class Outcome:
    state: str  # accepted | rejected | unchecked
    why: str = ""  # for unchecked: timed out, no connection, rate limited, the service had an error
    detail: str = ""  # what the service said back (a user name, a model count), redacted


# service id -> (method, url, headers or params builder, statuses that mean the key was refused, detail reader)
def _gemini(key):
    return "https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key": key}, {}


def _openai(key):
    return "https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}, {}


def _anthropic(key):
    return "https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}, {}


def _fal(key):
    return "https://rest.alpha.fal.ai/users/current", {"Authorization": f"Key {key}"}, {}


def _huggingface(key):
    return "https://huggingface.co/api/whoami-v2", {"Authorization": f"Bearer {key}"}, {}


def _roboflow(key):
    return "https://api.roboflow.com/", {}, {"api_key": key}


CALLS = {"gemini": _gemini, "openai": _openai, "anthropic": _anthropic, "fal": _fal, "huggingface": _huggingface,
         "roboflow": _roboflow}
REFUSED = {"gemini": {400, 401, 403}}  # Gemini answers a bad key on a GET with 400 (API_KEY_INVALID)
REFUSED_DEFAULT = {401, 403}


def _detail(service: str, body) -> str:
    if not isinstance(body, dict):
        return ""
    if service in ("gemini", "openai", "anthropic"):
        models = body.get("models") or body.get("data") or []
        return f"{len(models)} models" if isinstance(models, list) else ""
    if service == "huggingface":
        return f"account {body.get('name', '?')}"
    if service == "roboflow":
        return f"workspace {body.get('workspace', '?')}"
    return "authenticated"


def check(service: str, key: str, client: httpx.Client | None = None) -> Outcome:
    """One call to `service` with `key`: accepted, rejected or unchecked (with why)."""
    from research_worker import api_usage
    api_usage.require_provider()
    if service not in CALLS:
        return Outcome("unchecked", f"no test for {service}")
    if not key or not key.strip():
        return Outcome("rejected", detail="the key is empty")
    url, headers, params = CALLS[service](key.strip())
    own = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        response = client.get(url, headers=headers, params=params)
    except httpx.TimeoutException:
        return Outcome("unchecked", "timed out")
    except httpx.TransportError:
        return Outcome("unchecked", "no connection")
    finally:
        if own:
            client.close()
    status = response.status_code
    if 200 <= status < 300:
        try:
            body = response.json()
        except ValueError:
            body = None
        return Outcome("accepted", detail=_detail(service, body).replace(key, "<key>"))
    if status in REFUSED.get(service, REFUSED_DEFAULT):
        return Outcome("rejected")
    if status == 429:
        return Outcome("unchecked", "rate limited or out of quota")
    return Outcome("unchecked", f"the service had an error ({status})")
