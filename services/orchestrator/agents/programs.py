"""Which outside agents' programs are installed on this Mac (ADR 0029).

Hooke starts only programs the user installed themselves, found where their installers put them. The Mac app's own
PATH is launchd's, so the usual install folders are searched too. Nothing is downloaded or installed here.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

VERSION_S = 8.0


@dataclass(frozen=True)
class Program:
    id: str  # codex | claude-code | grok | gemini
    label: str
    command: str  # the executable's name
    kind: str  # the door connection's kind (door/connections.py KINDS)
    protocol: str  # app-server | stream-json | acp
    how: str  # what the user does to install and sign in, said when it isn't found


PROGRAMS = (
    Program("codex", "Codex", "codex", "codex", "app-server",
            "install Codex (npm install -g @openai/codex or brew install codex), then run codex once to sign in"),
    Program("claude-code", "Claude Code", "claude", "claude-code", "stream-json",
            "install Claude Code from claude.com/code, then run claude once to sign in"),
    Program("grok", "Grok Build", "grok", "other", "acp",
            "install Grok Build from x.ai, then run grok once to sign in"),
    Program("gemini", "Gemini CLI", "gemini", "other", "acp",
            "install Gemini CLI (npm install -g @google/gemini-cli), then run gemini once to sign in"),
)
BY_ID = {p.id: p for p in PROGRAMS}


def _folders() -> list[str]:
    home = Path.home()
    extra = [home / ".local/bin", home / ".bun/bin", home / ".npm-global/bin", home / ".volta/bin",
             home / ".cargo/bin", home / ".grok/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    nvm = home / ".nvm/versions/node"
    if nvm.is_dir():
        extra += sorted((p / "bin" for p in nvm.iterdir()), reverse=True)
    return [str(p) for p in extra]


def search_path() -> str:
    """PATH for finding and running the programs: the user's own, then the usual install folders."""
    seen, parts = set(), []
    for part in os.environ.get("PATH", "").split(os.pathsep) + _folders():
        if part and part not in seen:
            seen.add(part)
            parts.append(part)
    return os.pathsep.join(parts)


def find(program: Program) -> str | None:
    if os.environ.get("MICROMIND_AGENT_PROGRAMS") == "none":  # the tests' world (tests/conftest.py): never this Mac's
        return None
    override = os.environ.get(f"HOOKE_AGENT_{program.id.upper().replace('-', '_')}")  # a developer's own copy
    if override and os.access(override, os.X_OK):
        return override
    return shutil.which(program.command, path=search_path())


def version(path: str) -> str | None:
    """The program's own --version line; blocking, so called off the event loop."""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=VERSION_S,
                             env={**os.environ, "PATH": search_path()}, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    line = (out.stdout or out.stderr).strip().splitlines()
    return line[0][:80] if line else None


def installed() -> list[dict]:
    """Every program Hooke can start, with whether it is here. Blocking (it runs --version)."""
    rows = []
    for program in PROGRAMS:
        path = find(program)
        rows.append({"id": program.id, "label": program.label, "installed": bool(path), "path": path,
                     "version": version(path) if path else None, "protocol": program.protocol,
                     "next": None if path else program.how})
    return rows
