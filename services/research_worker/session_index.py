"""A researcher's read-only view of the session's one index (scaffolding Amendment 2, point 7;
`orchestrator/resources.py`): what the session has made so far, the same entries, ids and provenance every
reader sees (the brain, the studio, programs outside the app). Readers never write.

It reads the app's own HTTP API on this machine (`GET /api/resources...`), at the base URL the research
manager puts in the worker's spec (`resources_url`). That is a direct local call, never the web path:
`safe_fetch` refuses private addresses by design, and this must reach only the app itself.

What it reads is the researcher's own session (ADR 0017: a session is the project, not the day), the one it was started
in, whatever session is open now and on whichever days the session ran: its id goes as the index's `day` parameter,
which takes a session id.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
TIMEOUT_S = 5.0
TEXT_LIMIT = 12000  # characters of an entry's reading or a listing given to the model at once
PICTURE_LIMIT = 8_000_000  # bytes


class IndexUnavailable(Exception):
    """The index could not be read (the app is not answering, or it refused)."""


class NoEntry(Exception):
    """No entry with that id in the researcher's session."""


class NoPicture(Exception):
    """The entry has no picture with that number."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The app never redirects its index; a redirect is refused rather than followed off this machine."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


class SessionIndex:
    def __init__(self, base: str, session: str | None, timeout: float = TIMEOUT_S) -> None:
        parts = urllib.parse.urlsplit(base or "")
        if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS:
            raise ValueError(f"the session index is read only from this machine, not {base!r}")
        self.base = base.rstrip("/")
        self.session = session  # the researcher's session id (day/sid.py), its scope in the index
        self.timeout = timeout
        # Never through a proxy, never redirected elsewhere: this is the app on this machine.
        self._open = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open

    def _get(self, path: str, **params) -> tuple[bytes, str]:
        query = {k: v for k, v in {"day": self.session, **params}.items() if v not in (None, "")}
        url = f"{self.base}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        try:
            with self._open(url, timeout=self.timeout) as response:
                return response.read(PICTURE_LIMIT + 1), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NoEntry(path) from None
            # The app's own words for why ("restart reading at offset 0"): its text, never a web page's.
            said = ""
            try:
                said = str(json.loads(exc.read(4000).decode("utf-8")).get("error") or "")
            except (ValueError, AttributeError, OSError):
                said = ""
            raise IndexUnavailable(f"the app answered {exc.code}" + (f": {said}" if said else "")) from None
        except (urllib.error.URLError, OSError) as exc:
            raise IndexUnavailable(f"the app did not answer ({getattr(exc, 'reason', exc)})") from None

    def _json(self, path: str, **params) -> dict:
        body, _ = self._get(path, **params)
        try:
            data = json.loads(body.decode("utf-8"))
        except ValueError:
            raise IndexUnavailable("the app's answer was not JSON") from None
        return data if isinstance(data, dict) else {"value": data}

    def summary(self) -> dict:
        """Each kind of entry, with how many, the newest, what made them, what is live and what could be made."""
        return self._json("/api/resources/summary")

    def list(self, kind: str | None = None, query: str | None = None) -> dict:
        return self._json("/api/resources", kind=kind, q=query)

    def read(self, entry_id: str) -> dict:
        """One entry in full: {"entry": ..., "detail": {"text", "values", "columns"?, "members"?}}."""
        return self._json(f"/api/resources/{urllib.parse.quote(entry_id, safe='')}")

    def read_page(self, entry_id: str, offset: int = 0, revision: str | None = None) -> dict:
        """One bounded page; later pages must name the exact original reading."""
        if type(offset) is not int or offset < 0 or (offset and not revision):
            raise IndexUnavailable('continuation needs a nonnegative offset and original revision')
        return self._json(f"/api/resources/{urllib.parse.quote(entry_id, safe='')}", offset=offset, revision=revision)

    def picture(self, entry_id: str, n: int = 0) -> tuple[bytes, str]:
        try:
            body, kind = self._get(f"/api/resources/{urllib.parse.quote(entry_id, safe='')}/picture", n=n)
        except NoEntry:
            raise NoPicture(entry_id) from None
        if len(body) > PICTURE_LIMIT:
            raise IndexUnavailable("the picture is too large")
        return body, kind


def capped(data: dict, limit: int = TEXT_LIMIT) -> tuple[str, bool]:
    """The data as JSON text for the model, cut at `limit` characters (said when it was cut)."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return (text, False) if len(text) <= limit else (text[:limit], True)


def newest_first(listing: dict, limit: int = TEXT_LIMIT) -> tuple[str, int]:
    """A listing for the model: whole entries, newest first (those are what a researcher is asked about),
    as many as fit in `limit` characters. Returns the text and how many older entries were left out."""
    def when(entry: dict) -> float:
        try:
            return float(entry.get("at") or 0)  # epoch seconds (orchestrator/resources.py Entry.wire)
        except (TypeError, ValueError):
            return 0.0

    entries = sorted(listing.get("entries") or [], key=when, reverse=True)
    kept, size = [], 0
    for entry in entries:
        piece = json.dumps(entry, ensure_ascii=False, separators=(",", ":"), default=str)
        if kept and size + len(piece) > limit:
            break
        kept.append(entry)
        size += len(piece)
    text = json.dumps({**listing, "entries": kept, "order": "newest first"}, ensure_ascii=False,
                      separators=(",", ":"), default=str)
    return text, len(entries) - len(kept)
