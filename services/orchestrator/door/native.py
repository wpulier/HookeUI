"""The researcher's answer to an agent's connection request, signed by the Hooke app's own window (ADR 0026).

The Mac app makes a P-256 key pair in memory each time it launches. It gives this server only the public half, in
the environment it starts it with (`HOOKE_CONFIRM_KEY`: the raw X9.63 point, base64). Its native dialog, not the web
page, asks the researcher, and it signs `message(...)` with the private half. A local program can copy the studio's
Origin and header, but it has no private key, and it can't change the environment of a server already running. So
it can't answer for the researcher.

A source checkout started with `make start` has no Mac app and no key. There, answering takes `HOOKE_DEV_CONFIRM=1`,
which the developer sets knowingly, and then the studio's own answer counts as it did before.
"""
from __future__ import annotations

import base64
import os

from .connections import Refused

PREFIX = "hooke-agent-connect"


def message(pid: str, code: str, answer: str) -> bytes:
    """What the window signs: the exact request, its code and the answer (packaging/macos AgentShared.swift)."""
    return f"{PREFIX}\n{pid}\n{code}\n{answer}".encode()


def public_key(environ=os.environ):
    raw = environ.get("HOOKE_CONFIRM_KEY")
    if not raw:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(raw, validate=True))
    except Exception as error:  # a key that can't be read refuses every answer rather than accept any
        raise Refused(503, "confirm-key-unreadable", f"Hooke's confirm key couldn't be read: {error}",
                      next="restart Hooke") from None


def check(pid: str, code: str, answer: str, signature, *, studio_ok: bool, environ=os.environ) -> str:
    """Whose answer this is: "native" (signed by this launch's window) or "development" (the studio's, allowed only
    with HOOKE_DEV_CONFIRM=1 and no key). Anything else is refused."""
    key = public_key(environ)
    if key is None:
        if environ.get("HOOKE_DEV_CONFIRM") == "1" and studio_ok:
            return "development"
        raise Refused(403, "native-confirm-required", "Connecting an agent needs the Hooke app's own confirm.",
                      next="confirm it in the Hooke Mac app (a source checkout needs HOOKE_DEV_CONFIRM=1)")
    if not isinstance(signature, str) or not signature:
        raise Refused(403, "native-confirm-required", "Only the Hooke window's own dialog can answer a connection.",
                      next="answer it in the Hooke window")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        key.verify(base64.b64decode(signature, validate=True), message(pid, code, answer), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        raise Refused(403, "bad-confirm", "That answer wasn't signed by this Hooke window.",
                      next="answer it in the Hooke window") from None
    return "native"
