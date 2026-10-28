"""Which pages may open the orchestrator's WebSockets.

Browsers do not apply CORS to WebSockets, so any page could otherwise connect to
ws://127.0.0.1:8770. Only the studio (served from this Mac) may: a page from the web, or a
sandboxed research lesson (whose origin is "null"), is refused. Local tools that send no
Origin at all (the scenario harness, scripts) are allowed; the server only listens on
127.0.0.1.
"""
from __future__ import annotations

import os
import re

LOCAL_ORIGIN = re.compile(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")


def allowed_origin(origin: str | None) -> bool:
    if origin is None:
        return True
    return bool(LOCAL_ORIGIN.match(origin))


def studio_origin(origin: str | None, own_port: int | None) -> bool:
    """Stricter, for what only the studio itself may do (save a key): an Origin is required, local, and on this server's
    own port or the dev studio's (Vite on DEV_PORT, 5180 by default, proxies /api here). A tool with no Origin, or
    another local page on another port, is refused."""
    if not origin:
        return False
    match = LOCAL_ORIGIN.match(origin)
    if not match:
        return False
    port = int(match.group(2)[1:]) if match.group(2) else (443 if origin.startswith("https") else 80)
    return port in {own_port, int(os.environ.get("DEV_PORT") or 5180)}


# --- who may open a WebSocket (ADR 0026) ------------------------------------------------------------------------------
# A local program can send any Origin header, so an Origin alone can't tell the studio from a script.
# - The Mac app gives its window a studio token the server never serves (HOOKE_STUDIO_TOKEN in the server's environment,
#   injected into the WKWebView): with it set, a socket is the studio only when it presents that token.
# - A source checkout's studio, in a browser, has no such token: there a loopback Origin still marks the studio.
# - A script (the bench, the sim, a replay) presents the local token from `<data home>/agents/local-token` (0600), as
#   `Authorization: Bearer …`. Nothing else may open a socket.

def local_token_path(home) -> "Path":
    from pathlib import Path
    return Path(home) / "agents" / "local-token"


def local_token(home) -> str:
    """This data home's token for local scripts, made once (mode 0600)."""
    import secrets
    path = local_token_path(home)
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    token = secrets.token_urlsafe(32)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()  # another process made it first
    with os.fdopen(descriptor, "w") as stream:
        stream.write(token)
    return token


def script_headers(home=None) -> dict:
    """The header a local script opens a socket with (services/bench, services/sim)."""
    if home is None:
        from .paths import Paths, cli_environ
        home = Paths.from_env(cli_environ()).home  # a script finds the live app's home as the paths CLI does
    return {"Authorization": f"Bearer {local_token(home)}"}


def socket_kind(headers, query, home) -> str | None:
    """"studio", "script", or None (refused) for a WebSocket's request."""
    import secrets
    studio_token = os.environ.get("HOOKE_STUDIO_TOKEN")
    if studio_token:
        given = query.get("studio") or ""
        if given and secrets.compare_digest(given, studio_token):
            return "studio"
    else:
        origin = headers.get("origin")
        if origin is not None and LOCAL_ORIGIN.match(origin):
            return "studio"
    authorization = headers.get("authorization") or ""
    if home is not None and authorization.lower().startswith("bearer "):
        if secrets.compare_digest(authorization[7:].strip(), local_token(home)):
            return "script"
    return None
