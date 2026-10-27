"""Sign in to GitHub in one step, for Share with everyone (docs/design/2026-10-13-capabilities-for-everyone.md, C5).

GitHub's device flow (docs.github.com, "Authorizing OAuth apps", device flow): the app asks GitHub for a short code, the
studio shows it with GitHub's page ("Open github.com/login/device and type WDJB-MJHT"), the user approves there in their
own browser, signed in as they already are, and the app's polling receives a token. No password ever passes through the
app, and nothing is typed into it.

**What it needs, and doesn't have yet:** the project's own OAuth app registered on GitHub with Device Flow enabled. Its
client ID is public by GitHub's design (the device flow uses no client secret), so it is this module's `CLIENT_ID`, the
same in every copy of the app; `MICROMIND_GITHUB_CLIENT_ID` overrides it (a fork, a test). Until it is registered,
`CLIENT_ID` is empty and every call says so (`NotSetUp`), with what works today: publishing with Git and `gh` signed in.
No client identity is invented here. To register it (the project's owner, once): GitHub → Settings → Developer settings
→ OAuth Apps → New OAuth App; name "ObservationUI", homepage the project's page, callback URL the same page (unused by the
device flow), tick **Enable Device Flow**; copy its Client ID into `CLIENT_ID`.

**The token** is kept in the macOS Keychain (keychain.py, account `GITHUB_SIGNIN`), never on a wire, in a log or told to a
model. It asks for `public_repo` (to fork the catalog and open a pull request) and `read:user user:email` (whose name and
verified email the DCO sign-off certifies). Sign out deletes it; the user can also revoke it on GitHub.

Signed in, Share with everyone goes through GitHub's API with this token (publish_api.py: the exact reviewed package, a
fork, one signed-off commit, the pull request), with no checkout and no Git; without it, publish.py's Git and `gh` route.
The sign-in itself still waits for the project's registered OAuth app (`CLIENT_ID`).
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Mapping

from . import keychain

CLIENT_ID = ""  # the project's OAuth app (Device Flow enabled): empty until it is registered (see the docstring)
SCOPES = "public_repo read:user user:email"
DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
GRANT = "urn:ietf:params:oauth:grant-type:device_code"
ACCOUNT = "GITHUB_SIGNIN"  # its Keychain account
TODAY = ("Share with everyone works today with Git and the GitHub command line signed in (gh auth login); one-click "
         "sign-in waits for the project's own GitHub app")

Post = Callable[[str, dict], dict]


class NotSetUp(RuntimeError):
    """One-click sign-in isn't set up in this copy of the app (no client ID)."""


class SignInFailed(RuntimeError):
    """What GitHub said, in plain words."""


def client_id(environ: Mapping[str, str] = os.environ) -> str:
    return (environ.get("MICROMIND_GITHUB_CLIENT_ID") or CLIENT_ID).strip()


def _post(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(url, data=data, headers={"Accept": "application/json",
                                                              "User-Agent": "ObservationUI"})
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 - GitHub's fixed https endpoints
        return json.loads(response.read(64 * 1024))


_lock = threading.Lock()
_pending: dict[str, dict] = {}  # handle -> {device_code, interval, until}: the device code stays here


def status(environ: Mapping[str, str] = os.environ) -> dict:
    """{"setUp", "signedIn", "says"}: what the studio shows beside Share with everyone."""
    if not client_id(environ):
        return {"setUp": False, "signedIn": False, "says": f"One-click GitHub sign-in isn't set up in this app yet. {TODAY}."}
    signed = bool(keychain.get(ACCOUNT))
    return {"setUp": True, "signedIn": signed,
            "says": "Signed in to GitHub" if signed else "Sign in to GitHub to share with everyone"}


def start(environ: Mapping[str, str] = os.environ, post: Post = _post) -> dict:
    """Ask GitHub for a code; {"id", "code", "open", "every", "until"} for the studio (the device code stays here)."""
    cid = client_id(environ)
    if not cid:
        raise NotSetUp(f"One-click GitHub sign-in isn't set up in this app yet. {TODAY}.")
    if not keychain.enabled():
        raise SignInFailed("this Mac has no Keychain the app can keep a GitHub sign-in in")
    try:
        got = post(DEVICE_URL, {"client_id": cid, "scope": SCOPES})
    except OSError as exc:
        raise SignInFailed(f"couldn't reach GitHub ({type(exc).__name__}): check the connection and try again") from exc
    if got.get("error") or not got.get("device_code") or not got.get("user_code"):
        raise SignInFailed(_said(got))
    handle = secrets.token_hex(8)
    every = max(5, int(got.get("interval") or 5))
    until = time.time() + int(got.get("expires_in") or 900)
    with _lock:
        _pending[handle] = {"device_code": got["device_code"], "interval": every, "until": until, "client": cid}
    return {"id": handle, "code": got["user_code"], "open": got.get("verification_uri") or "https://github.com/login/device",
            "every": every, "until": until}


def poll(handle: str, post: Post = _post) -> dict:
    """One check of a sign-in in progress: {"state": waiting|done|expired|denied, "every", "says"}."""
    with _lock:
        one = _pending.get(handle)
    if one is None:
        return {"state": "expired", "says": "That sign-in is no longer waiting: start it again"}
    if time.time() > one["until"]:
        _forget(handle)
        return {"state": "expired", "says": "The code expired before it was approved: start again"}
    try:
        got = post(TOKEN_URL, {"client_id": one["client"], "device_code": one["device_code"], "grant_type": GRANT})
    except OSError:
        return {"state": "waiting", "every": one["interval"], "says": "Waiting for GitHub (couldn't reach it just now)"}
    error = got.get("error")
    if error == "authorization_pending":
        return {"state": "waiting", "every": one["interval"], "says": "Waiting for you to approve it on GitHub"}
    if error == "slow_down":  # GitHub's rule: wait longer between checks from now on
        with _lock:
            one["interval"] = int(got.get("interval") or one["interval"] + 5)
        return {"state": "waiting", "every": one["interval"], "says": "Waiting for you to approve it on GitHub"}
    if error in ("expired_token", "token_expired"):
        _forget(handle)
        return {"state": "expired", "says": "The code expired before it was approved: start again"}
    if error == "access_denied":
        _forget(handle)
        return {"state": "denied", "says": "You declined it on GitHub: nothing was kept"}
    if error or not got.get("access_token"):
        _forget(handle)
        raise SignInFailed(_said(got))
    keychain.set(ACCOUNT, str(got["access_token"]))
    _forget(handle)
    return {"state": "done", "says": "Signed in to GitHub"}


def sign_out() -> bool:
    signed = bool(keychain.get(ACCOUNT))
    keychain.delete(ACCOUNT)
    return signed


def token() -> str | None:
    """The kept token, for the one call that publishes (never a wire, a log or a prompt)."""
    return keychain.get(ACCOUNT)


def _forget(handle: str) -> None:
    with _lock:
        _pending.pop(handle, None)


def _said(got: dict) -> str:
    words = {"incorrect_client_credentials": "GitHub doesn't know this app's client ID",
             "device_flow_disabled": "the project's GitHub app doesn't have Device Flow turned on",
             "unsupported_grant_type": "GitHub refused the sign-in's kind"}
    error = str(got.get("error") or "no code")
    return words.get(error) or f"GitHub said: {str(got.get('error_description') or error)[:200]}"
