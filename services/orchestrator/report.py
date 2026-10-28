"""Report it: the facts of a capability that didn't work, for whoever looks after it (docs/design/2026-10-13-capabilities-
for-everyone.md, C7; row 4 of "how this could go wrong": "Report it sends the facts, never her pictures").

`prepare(key)` writes, on this Mac, a plain report of one catalog entry: what it is and where it comes from, its version,
which of its environments ran or failed and why (its install's own words, its proof's), its last check, and this Mac's
macOS, chip and app edition. Nothing leaves the Mac: the studio shows the report to read, copies it, and opens the page
where it is reported (the reviewed catalog's issues, a community catalog's own, or "send it to who shared it" for a
shared file or one of yours) with only a title in its address.

Only controlled facts are in the copyable report (`facts`): what the entry is and where it comes from, its versions and
runtimes, and for each failure a **stage and a reason code** from a fixed list (`REASONS`), never the words its code
said: an adapter's error may echo a path, a specimen's words or a picture's bytes, and no redaction turns that into a
controlled fact (Codex's review of 817ee62e). The raw words stay on this Mac (`local_details`, shown apart, never in the
report). Whatever is still written is scrubbed of every configured key's value and key-shaped strings as a last guard.
"""
from __future__ import annotations

import json
import os
import platform
import re
from typing import Mapping

from gpu_worker import manifests as catalog

ISSUES = "https://github.com/wpulier/HookeUI/issues/new"
SECRETS = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|hf_[A-Za-z0-9]{8,}|gh[opsu]_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|"
                     r"AIza[0-9A-Za-z_\-]{20,}|xox[abprs]-[A-Za-z0-9\-]{8,}|(?i:bearer)\s+[A-Za-z0-9._\-]{8,}|"
                     r"[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*\s*[=:]\s*\S+)")
MAX_WORDS = 1500  # characters of one thing it said


def _configured() -> list[str]:
    """The actual values of every key this Mac has configured, longest first (never shown; only redacted)."""
    values = []
    try:
        from . import keys

        values += [r.value for r in keys.resolve().values() if r.value]
    except Exception:  # noqa: BLE001 - no keys readable: the patterns still apply
        pass
    try:
        from . import github_signin

        values.append(github_signin.token() or "")
    except Exception:  # noqa: BLE001
        pass
    return sorted({v for v in values if len(v) >= 8}, key=len, reverse=True)


def redact(text: object) -> str:
    """No configured key's value and no key- or token-shaped string; all of it kept, home paths too (a log on this Mac)."""
    out = str(text or "")
    for value in _configured():
        out = out.replace(value, "[removed]")
    return SECRETS.sub("[removed]", out)


def scrub(text: object, environ: Mapping[str, str] = os.environ) -> str:
    """No key, token or home path in what is reported (see the module's docstring)."""
    out = redact(text)
    home = os.path.expanduser("~")
    if home and home != "/":
        out = out.replace(os.path.realpath(home), "~").replace(home, "~")
    return out[-MAX_WORDS:]


def facts(key: str, environ: Mapping[str, str] = os.environ) -> dict:
    """What the report says (see the module's docstring)."""
    from . import channels, envs

    m = catalog.get(key)
    if m is None:
        raise KeyError(key)
    out: dict = {"capability": m.name, "entry": m.key, "version": m.version,
                 "from": channels.said(m.channel, environ) if m.channel else ("one of yours" if m.namespace == "mine"
                                                                              else "the reviewed catalog"),
                 "runner": m.runner or "none", "link": (m.credit or {}).get("link") or m.source.get("url") or ""}
    if m.runner == "python-env" or envs.spec(m):
        from . import env_runner

        running = env_runner.in_use(m, environ) if m.runner == "python-env" else None
        out["running"] = running.m.version if running is not None else "none"
        tried = []
        for d, receipt in envs.generations(m, environ):
            proof = env_runner._proof_of(d) if m.runner == "python-env" else None
            on = ((proof or {}).get("input") or {}).get("evidenceClass")
            tried.append({"version": receipt.get("version"), "python": receipt.get("python"), "uv": receipt.get("uv"),
                          "packages": receipt.get("packages"), "at": receipt.get("at"),
                          "proof": None if proof is None else {
                              "ok": bool(proof.get("ok")), "reason": reason(proof.get("why")) if not proof.get("ok") else "",
                              "at": proof.get("at"),
                              "on": {"generated-fixture": "a generated picture", "asset-image": "a camera picture"}.get(
                                  on, "a picture")}})
        out["environments"] = tried
        failed = envs.folder(m, environ).with_name(envs.folder(m, environ).name + envs.FAILED)
        if failed.is_file():
            try:
                out["install failed"] = reason(json.loads(failed.read_text()).get("why"))
            except (OSError, ValueError, AttributeError):
                out["install failed"] = "its failure record can't be read"
    out["this Mac"] = {"macOS": platform.mac_ver()[0] or platform.platform(), "chip": platform.machine(),
                       "edition": environ.get("MICROMIND_EDITION") or "research"}
    return out


# A failure's reason, as a code from this fixed list, read from where the app's own words put it (never the error's text).
REASONS = (
    ("timed out", re.compile(r"timed out|past its limit|took longer", re.I)),
    ("did not start", re.compile(r"didn't start|did not start|startup", re.I)),
    ("gave the wrong output", re.compile(r"label map|not the .* it declares|isn't the|its table is not|points are not|"
                                         r"lie outside the picture|must be nonnegative", re.I)),
    ("refused a package build", re.compile(r"ready-built packages|build", re.I)),
    ("a package or file didn't match its hash", re.compile(r"hash|sha256|isn't the file", re.I)),
    ("the sandbox refused it", re.compile(r"sandbox", re.I)),
    ("ran out of memory", re.compile(r"memory|MemoryError", re.I)),
    ("stopped", re.compile(r"stopped|cancelled", re.I)),
)


def reason(text) -> str:
    """A failure's reason code (REASONS), else "its code raised an error": never its words."""
    words = str(text or "")
    return next((code for code, pattern in REASONS if pattern.search(words)), "its code raised an error") if words else ""


def local_details(key: str, environ: Mapping[str, str] = os.environ) -> list[str]:
    """The raw words its proof and install said, for this Mac's own eyes (shown apart, never in the copyable report)."""
    from . import env_runner, envs

    m = catalog.get(key)
    if m is None:
        return []
    if not (m.runner == "python-env" or envs.spec(m)):
        return []  # no environment of its own (a built-in in the GPU worker): nothing installed here to have failed
    out = []
    for d, _receipt in envs.generations(m, environ):
        proof = env_runner._proof_of(d) if m.runner == "python-env" else None
        if proof and not proof.get("ok") and proof.get("why"):
            out.append(str(proof["why"])[:2000])
    failed = envs.folder(m, environ).with_name(envs.folder(m, environ).name + envs.FAILED)
    try:
        out.append(str(json.loads(failed.read_text()).get("why"))[:2000])
    except (OSError, ValueError, AttributeError):
        pass
    tool_failure = last_tool_failure(m.key, environ)
    if tool_failure:
        out.append(tool_failure)
    return out


def last_tool_failure(key: str, environ: Mapping[str, str] = os.environ) -> str:
    """What its interactive tool last said on this Mac when it did not open (scientific_host.keep_failure), its end."""
    from .paths import Paths
    from .scientific_host import failure_name

    kept = sorted(Paths.from_env(environ).logs.glob(f"tool-failures/*/*-{failure_name(key)}.log"),
                  key=lambda p: (p.parent.name, p.name))
    try:
        return kept[-1].read_text(encoding="utf-8", errors="replace")[-2000:] if kept else ""
    except OSError:
        return ""


def where(m: catalog.Manifest) -> str:
    """The page it is reported on ("" when it is reported to a person: one of yours, a shared file)."""
    if m.namespace == "mine" and not m.channel:
        return ""
    if m.channel:
        from . import channels

        c = next((c for c in catalog.channels() if c["name"] == m.channel), {})
        url = str(c.get("url") or "")
        if channels.source(m.channel) == "shared-file" or not url.startswith("https://github.com/"):
            return ""
        return url.removesuffix(".git").rstrip("/") + "/issues/new"
    return ISSUES


def prepare(key: str, environ: Mapping[str, str] = os.environ) -> dict:
    """{"title", "text", "url", "says"}: the report to read and copy, and where it goes (nothing is sent)."""
    m = catalog.get(key)
    if m is None:
        raise KeyError(key)
    found = facts(key, environ)
    lines = [f"**{scrub(found['capability'])}** ({found['entry']}, {scrub(found['version'])}), from {found['from']}"]
    if found.get("link"):
        lines.append(f"Its page: {scrub(found['link'])}")
    if "running" in found:
        lines.append(f"Running here: {scrub(found['running'])}")
    for one in found.get("environments", []):
        proof = one["proof"]
        said = ("not proven yet" if proof is None else
                f"proof {'passed' if proof['ok'] else 'failed: ' + proof['reason']}"
                + f" (on {proof['on']}, {proof.get('at') or ''})")
        lines.append(f"- {one['version']} · Python {one['python']} · {one['packages']} packages · {one['uv']} · {said}")
    if found.get("install failed"):
        lines.append(f"Its last install failed: {found['install failed']}")
    mac = found["this Mac"]
    lines.append(f"This Mac: macOS {mac['macOS']}, {mac['chip']}, {mac['edition']} edition")
    lines.append("(Prepared by HookeUI's Report it from controlled facts only: no error text, pictures, sessions, "
                 "keys or paths are in it.)")
    text = scrub("\n".join(lines), environ)
    url = where(m)
    title = scrub(f"{m.name} ({m.version}) doesn't work", environ)[:120]
    says = ("Read it, copy it, then open its page to post it." if url else
            "Read it, copy it, and send it to whoever made or shared it (it has no public page).")
    return {"title": title, "text": text, "url": url, "says": says,
            "local": [scrub(w, environ) for w in local_details(key, environ)]}  # this Mac's only, shown apart
