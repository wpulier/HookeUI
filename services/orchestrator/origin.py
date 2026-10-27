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
