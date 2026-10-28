"""Drafts: the builder's workbench (docs/design/2026-10-13-capabilities-for-everyone.md, C4).

For a link no template runs (a GitHub repository, a model that needs its own code), a researcher with the builder's
tools (research_worker, deliverable "adapter") reads its pages and drafts an adapter. The app does everything that
touches this Mac, here, on the researcher's request:

- `write`: the draft's `adapter.py`, `requirements.in` and manifest fields, into `<catalog>/drafts/<id>/`;
- `pin`: one of its files (its weights) downloaded once, size-capped, hashed and kept in the draft's `files/`;
- `attempt`: its requirements locked **as wheels only** (`uv pip compile --only-binary :all: --generate-hashes`: no
  package's build script ever runs), its Python made from that lock, and the adapter run on the user's picture
  **inside macOS's sandbox** (sandbox.py: it is code nobody reviewed), its result or its error said back.

**Owned, start to end:** every step's child processes (uv) and downloads run under one owned bound (owned_process): the
researcher's Stop (`stop`) and the builder's whole budget (BUDGET_S from the draft's creation, never per step) end them,
their process groups are reaped, and a try ended that way is never recorded as a success.

**What was tried is what is used:** a try that ran records the digest of exactly what it ran (adapter, requirements,
lockfile, fields, pinned files) and keeps a copy of it (`tried/`). Writing or pinning again makes the draft untried; a try
whose draft changed while it ran is not a success; and Use it copies the tried copy only while it and the draft are still
exactly what ran.

**Reviewed before it is admitted** (ticket 62 L13): `review` shows exactly what ran (its adapter, requirements, locked
packages, pinned files with their origin and hash, what it imports, every try with its error, and the static checks a
contribution gets); Use it takes back that review's digest, so what is admitted is what the user read.

A draft is never a choice and never runs outside the sandbox. **Use it** (`promote`) moves a draft that ran into your own
entries (`mine/models/<id>/`), its pinned files adopted together into the models folder, where it is installed and
proven as any entry of yours, still sandboxed, its card saying it was drafted by AI; a failure leaves nothing half-made
and says why. Nothing here calls a model: the researcher does.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Mapping

import numpy as np

from gpu_worker import manifests as catalog

from . import owned_process

PYTHON = "3.13.5"
MAX_PIN = 4 * 1024 ** 3  # one pinned file at most (weights)
MAX_TEXT = 200_000  # the adapter and requirements, each at most
STEP_S = 900.0  # locking or building at most
BUDGET_S = 900.0  # the builder's whole budget (research_worker/budget.py "build"), from the draft's creation
FIELDS = {"name", "what", "licence", "licence_note", "tier", "makers", "link", "paper", "imports", "tags"}


class DraftError(RuntimeError):
    """What the builder is told went wrong, in plain words (its error for the next try)."""


def root(environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.catalog_root(environ) / "drafts"


def folder(did: str, environ: Mapping[str, str] = os.environ) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", did or ""):
        raise DraftError(f"{did!r} is no draft")
    return root(environ) / did


def _uv() -> str:
    return shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")


def _state(did: str, environ: Mapping[str, str] = os.environ) -> dict:
    try:
        return json.loads((folder(did, environ) / "draft.json").read_text())
    except (OSError, ValueError):
        raise DraftError(f"there is no draft {did}") from None


TRIED = ("adapter.py", "requirements.in", "env.lock")


def digest(d: Path, state: dict, lock: bool = True) -> str:
    """Exactly what a try runs: its adapter, requirements, lockfile (without `lock`: what a try is asked to run, before it
    locks), fields and pinned files (by their hashes)."""
    h = hashlib.sha256()
    for f in TRIED if lock else TRIED[:2]:
        p = d / f
        h.update(f.encode() + b"\0" + (p.read_bytes() if p.is_file() else b"") + b"\0")
    h.update(json.dumps({"job": state.get("job", "find"), "input": state.get("input"), "fields": state.get("fields") or {}, "files": sorted((x["path"], x["sha256"])
                                                                              for x in state.get("files", []))},
                        sort_keys=True).encode())
    return h.hexdigest()


def _untried(state: dict) -> None:
    """Something it runs changed: what ran before is no longer what it is."""
    if state.get("state") == "ran":
        state["state"] = "drafting"
        state["changedSinceTry"] = True
    state.pop("tried", None)
    state.pop("comparison", None)


def _remaining(state: dict) -> float:
    return max(0.0, float(state.get("createdAt") or time.time()) + BUDGET_S - time.time())


_LOCK = threading.RLock()  # every change of a draft's files and state: a try's success never interleaves with an edit


def _save_state(did: str, state: dict, environ) -> None:
    p = folder(did, environ) / "draft.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(p)


def create(link: str, picture: bytes | None = None, environ: Mapping[str, str] = os.environ, *, job: str = "find", trial: str = "") -> str:
    """A new draft for `link`, with the user's picture (else the app's representative one) as what it is tried on."""
    from .link_recognise import link_name
    from .mine import slug
    from .own_trials import JOBS
    if job not in JOBS:
        raise DraftError("choose the picture job first")

    name = link_name(link) or "draft"  # its repository's name, never the commit's hash or one of its files
    base = slug(name)[:40].strip("-") or "draft"
    did, n = base, 2
    while (root(environ) / did).exists():
        did, n = f"{base}-{n}", n + 1
    d = root(environ) / did
    (d / "files").mkdir(parents=True)
    origin = {"evidenceClass": "user-input", "identity": "selected-picture"}
    if picture is None:
        from .env_runner import representative

        import cv2

        rgb, origin = representative(environ)
        ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        picture = jpeg.tobytes()
    (d / "picture.jpg").write_bytes(picture)
    _save_state(did, {"id": did, "name": name, "link": link, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "createdAt": time.time(),
                      "state": "drafting", "tries": [], "files": [], "job": job, "output": JOBS[job][1], "trial": trial,
                      "input": {**origin, "sha256": hashlib.sha256(picture).hexdigest()}}, environ)
    return did


def assign(did: str, researcher: str, environ: Mapping[str, str] = os.environ) -> None:
    with _LOCK:
        state = _state(did, environ)
        state["researcher"] = researcher
        _save_state(did, state, environ)


def finish(did: str, status: str, environ: Mapping[str, str] = os.environ) -> None:
    with _LOCK:
        state = _state(did, environ)
        state["terminal"] = {"status": status, "at": time.time()}
        if state.get("state") not in ("ran", "used"):
            state["state"] = status if status in ("cancelled", "interrupted", "timeout", "over_budget") else "finished"
        _save_state(did, state, environ)


def recover(environ: Mapping[str, str] = os.environ) -> None:
    """Called once when the app starts. A prior process's draft cannot still have a live owner."""
    for row in wire(environ):
        if row["state"] != "used" and not row.get("terminal"):
            finish(row["id"], "interrupted", environ)


def community(did: str, environ: Mapping[str, str] = os.environ) -> dict:
    """A local editable handoff, with only controlled facts; no source code, pictures, paths or raw error text."""
    from urllib.parse import urlsplit, urlunsplit

    state = _state(did, environ)
    source = urlsplit(state.get("link") or "")
    link = urlunsplit(("https", source.hostname or "", source.path, "", ""))
    tries = state.get("tries") or []
    passed = sum(t.get("ok") is True for t in tries)
    status = (state.get("terminal") or {}).get("status") or state.get("state", "unknown")
    text = (f"Help adapting a capability for HookeUI\n\nSource: {link}\n"
            f"Builder state: {status}. {len(tries)} attempt(s), {passed} completed contract smoke checks.\n"
            "The adapter was drafted by AI. A smoke check does not establish scientific correctness.\n\n"
            "What I hoped to do: [add the task and expected result]\n"
            "What I need help with: [describe the problem after reviewing the local diagnostics]\n\n"
            "Pictures, source code, model files, session details, paths and raw diagnostics are excluded. "
            "Review this draft before sharing; add an example only if you deliberately choose to share it.\n")
    (folder(did, environ) / "community-draft.md").write_text(text)
    return {"title": "Help adapting a capability", "text": text, "localOnly": True,
            "says": "Prepared here for you to review and edit. Nothing was sent."}


def write(did: str, adapter: str, requirements: str, fields: dict, environ: Mapping[str, str] = os.environ) -> dict:
    """The draft's adapter, requirements and what it says of itself (see the module's docstring)."""
    d = folder(did, environ)
    state = _state(did, environ)
    if len(adapter) > MAX_TEXT or len(requirements) > MAX_TEXT:
        raise DraftError("the adapter and requirements are text files of at most 200 KB each")
    if "def segment(" not in adapter:
        raise DraftError("the adapter defines segment(picture, params), returning the selected job’s declared output")
    lines = [ln.strip() for ln in requirements.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    for ln in lines:
        if not re.fullmatch(r"[A-Za-z0-9_.\-\[\],]+\s*(==|>=|<=|~=|!=|<|>)?\s*[A-Za-z0-9_.*+\-]*(\s*;.*)?", ln):
            raise DraftError(f"{ln!r} is no requirement: one package a line, with a version (no links, no options)")
    unknown = set(fields) - FIELDS
    if unknown:
        raise DraftError(f"fields are {', '.join(sorted(FIELDS))}; not {', '.join(sorted(unknown))}")
    with _LOCK:
        state = _state(did, environ)
        (d / "adapter.py").write_text(adapter)
        (d / "requirements.in").write_text("\n".join(lines) + "\n")
        (d / "env.lock").unlink(missing_ok=True)  # locked again from these requirements by the next try
        state.update(fields={k: fields[k] for k in fields if fields[k] not in (None, "")},
                     written=time.strftime("%H:%M:%S"))
        _untried(state)
        _save_state(did, state, environ)
    return {"ok": True, "requirements": lines}


def pin(did: str, url: str, environ: Mapping[str, str] = os.environ, http=None,
        stop: Callable[[], bool] = lambda: False) -> dict:
    """One file it needs (its weights), downloaded once from a public https address, size-capped, hashed and kept; ended
    by `stop` or the builder's budget, mid-read too (its connection closed), and then nothing of it is kept."""
    d = folder(did, environ)
    state = _state(did, environ)
    if not url.startswith("https://"):
        raise DraftError("a pinned file comes from a public https address")
    name = re.sub(r"[^\w.\-]", "_", url.split("?")[0].rstrip("/").split("/")[-1])[:120] or "file"
    target = d / "files" / name
    if http is None:
        import httpx as http
    h, size = hashlib.sha256(), 0
    part = target.with_name(target.name + ".part")
    try:
        with owned_process.scope(seconds=_remaining(state), stop=stop) as bound, \
                http.stream("GET", url, follow_redirects=True, timeout=60) as r:
            if r.status_code >= 400:
                raise DraftError(f"{url} answered {r.status_code}")
            with _closing_when(bound, r), part.open("wb") as out:
                for chunk in r.iter_bytes(1 << 20):
                    bound.check()
                    size += len(chunk)
                    if size > MAX_PIN:
                        raise DraftError(f"{name} is over {MAX_PIN // 1024 ** 3} GB")
                    h.update(chunk)
                    out.write(chunk)
            bound.check()
    except owned_process.Stopped as exc:
        part.unlink(missing_ok=True)
        raise DraftError(f"downloading {name} was stopped ({exc})") from exc
    except DraftError:
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - its plain reason, for the next try
        part.unlink(missing_ok=True)
        stopped = isinstance(exc, (OSError, RuntimeError)) and _stopped(stop, state)
        raise DraftError(f"downloading {name} was stopped" if stopped else
                         f"couldn't download {url} ({type(exc).__name__})") from exc
    pinned = {"path": name, "bytes": size, "sha256": h.hexdigest(), "url": url}
    with _LOCK:
        part.replace(target)
        state = _state(did, environ)  # as it is now (another step may have written meanwhile)
        state["files"] = [f for f in state.get("files", []) if f["path"] != name] + [pinned]
        _untried(state)
        _save_state(did, state, environ)
    return pinned


def _stopped(stop, state) -> bool:
    return bool(stop()) or _remaining(state) <= 0


class _closing_when:
    """Closes a streaming response the moment the bound trips, so a read blocked on the network ends at once."""

    def __init__(self, bound, response) -> None:
        self.bound, self.response, self.done = bound, response, threading.Event()

    def __enter__(self):
        def watch():
            while not self.done.wait(owned_process.POLL_S):
                try:
                    self.bound.check()
                except owned_process.Stopped:
                    self.response.close()
                    return

        self.thread = threading.Thread(target=watch, name="draft-download-bound", daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.done.set()
        self.thread.join()


def _run(cmd: list[str], what: str, env: dict | None = None) -> str:
    """One step's child (uv), in its own process group under the owned bound: Stop or the budget ends and reaps it."""
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    try:
        with owned_process.scope(), owned_process.watching(process, STEP_S, what):
            out = owned_process.drained(process)  # its last words only
    except owned_process.Stopped as exc:
        raise DraftError(f"{what} was stopped ({exc})") from exc
    finally:
        owned_process.terminate(process)
    if process.returncode:
        raise DraftError(f"{what} failed: {(out or '').strip()[-1500:]}")
    return out


def _manifest(did: str, d: Path, state: dict) -> catalog.Manifest:
    """The draft as a manifest (mine-like, runner python-env), read as any entry is."""
    from .mine import write_entry

    f = state.get("fields") or {}
    write_entry(d, {
        "id": did, "name": f.get("name") or state.get("name") or did, "runs": "this-mac", "version": f"drafted {state.get('written', '')}",
        "licence": [did], "licence_note": f.get("licence_note") or f.get("licence") or "not stated",
        "kind": "model", "what": " ".join(str(f.get("what") or "Drafted by AI from its link").split()[:12]),
        "runner": "python-env", "source": {"url": state["link"]}, "files": state.get("files", []),
        "adapter": "adapter:segment",
        "env": {"python": PYTHON, "lock": "env.lock", "imports": list(f.get("imports") or [])},
        "component": {"what": f"{f.get('name') or state.get('name') or did} (drafted by AI from {state['link']})",
                      "licence": f.get("licence") or "not stated", "url": state["link"],
                      "tier": f.get("tier") if f.get("tier") in ("open", "terms", "noncommercial") else "noncommercial"},
        "needs_to_get": ["download"] if state.get("files") else [], "needs_to_run": [f"install:mine/{did}"],
        "credit": {k: v for k, v in {"authors": f.get("makers"), "link": state["link"], "paper": f.get("paper")}.items() if v},
        "tags": [t for t in (f.get("tags") or []) if isinstance(t, str) and catalog.TAG.fullmatch(t)][:12],
        "host": "own", "documented_mb": 1500, "load_limit_s": 300},
        [{"operation": "cells" if state.get("job", "find") == "find" else f"draft-{did}", "impl": did,
          "input": "image.2d.rgb8", "output": state.get("output", "labels.instance.2d")}])
    m, problems = catalog.read(d / catalog.MANIFEST, "mine")
    if m is None:
        raise DraftError("its manifest doesn't read: " + "; ".join(map(str, problems)))
    return m


def attempt(did: str, environ: Mapping[str, str] = os.environ, stop: Callable[[], bool] = lambda: False, *, memory=None) -> dict:
    """Lock it (wheels only), make its Python, and run it on the picture inside the sandbox (see the module's
    docstring), all under one owned bound (Stop, the builder's budget). {"ok", "count", "says"} or {"ok": False,
    "error"}: what the builder reads for its next try."""
    d = folder(did, environ)
    state = _state(did, environ)
    try:
        with owned_process.scope(seconds=_remaining(state), stop=stop) as bound:
            return _attempt(did, d, state, bound, environ, memory)
    except owned_process.Stopped as exc:
        with _LOCK:
            state = _state(did, environ)
            state.setdefault("tries", []).append({"at": time.strftime("%H:%M:%S"), "ok": False,
                                                  "error": f"stopped ({exc})"})
            _untried(state)
            _save_state(did, state, environ)
        return {"ok": False, "error": f"the try was stopped ({exc}): nothing of it is kept as a success"}


def _attempt(did: str, d: Path, state: dict, bound, environ, memory=None) -> dict:
    from . import env_runner, sandbox

    before = digest(d, state, lock=False)
    if not (d / "adapter.py").is_file():
        raise DraftError("write the adapter first")
    if not sandbox.available():
        raise DraftError("macOS's sandbox isn't available here: a draft never runs without it")
    tool = _uv()
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "TMPDIR")}
    env.update(UV_PYTHON_PREFERENCE="only-managed", MACOSX_DEPLOYMENT_TARGET="14.0")
    record = {"at": time.strftime("%H:%M:%S")}
    try:
        _run([tool, "pip", "compile", "--quiet", "--generate-hashes", "--only-binary", ":all:", "--python-version", "3.13",
              "--python-platform", "aarch64-apple-darwin", str(d / "requirements.in"), "-o", str(d / "env.lock")],
             "locking its requirements (wheels only)", env)
        lock = (d / "env.lock").read_bytes()
        env_dir = d / "env"
        stamp = hashlib.sha256(lock).hexdigest()
        if (env_dir / ".lock-sha256").read_text().strip() != stamp if (env_dir / ".lock-sha256").is_file() else True:
            shutil.rmtree(env_dir, ignore_errors=True)
            _run([tool, "python", "install", "--quiet", PYTHON], "getting its Python", env)
            _run([tool, "venv", "--quiet", "--python", PYTHON, str(env_dir)], "making its Python", env)
            _run([tool, "pip", "sync", "--quiet", "--require-hashes", "--only-binary", ":all:", "--python",
                  str(env_dir / "bin" / "python"), str(d / "env.lock")], "installing its packages", env)
            (env_dir / ".lock-sha256").write_text(stamp)
        m = _manifest(did, d, state)
        entry = env_dir / "entry" / did  # the adapter as tried (env_runner's Installed.entry)
        shutil.rmtree(entry, ignore_errors=True)
        entry.mkdir(parents=True)
        for f in ("adapter.py", catalog.MANIFEST):
            shutil.copyfile(d / f, entry / f)
        import cv2

        data = (d / "picture.jpg").read_bytes()
        from .source_images import _shape
        width, height, _, _ = _shape(data)
        if not sandbox.required(m):
            raise DraftError("a draft is code nobody reviewed: it runs only inside the sandbox")
        installed = env_runner.Installed(m, env_dir, {}, True)
        client = env_runner.Client(installed, environ, files=d / "files")  # its pinned files, from the draft
        started = time.monotonic()
        try:
            from .capture import Memory, MB
            with (memory or Memory.from_env()).reserve("analysis", width * height * 28 + len(data) * 2 + 16 * MB, "draft result") as lease:
                picture = cv2.cvtColor(cv2.imdecode(np.frombuffer(data, np.uint8),
                                                  cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION), cv2.COLOR_BGR2RGB)
                def admit(header, size):
                    lease.grow(5 * size + 16 * MB, "draft output picture")
                out = client.run(picture, {}, output=state.get("output", "labels.instance.2d"), admit=admit)
                from .own_trials import output
                result = output(d, "result", out, (d / "picture.jpg").read_bytes(), (time.monotonic() - started) * 1000)
        finally:
            client.stop()
        count = int(out.get("count") or len(out.get("polygons") or []))
        record.update(ok=True, count=count, ms=result["ms"], output=state.get("output", "labels.instance.2d"))
        says = f"it ran the selected job on the retained picture inside the sandbox ({result['ms']} ms)"
    except (DraftError, env_runner.Failed) as exc:
        record.update(ok=False, error=str(exc)[-1500:])
        with _LOCK:
            state = _state(did, environ)
            state.setdefault("tries", []).append(record)
            _untried(state)
            _save_state(did, state, environ)
        return {"ok": False, "error": record["error"]}
    with _LOCK:  # its success, published only as one step with the check that nothing changed and nothing ended it
        bound.check()
        state = _state(did, environ)
        if digest(d, state, lock=False) != before:
            record.update(ok=False, error="the draft changed while it was being tried: try it again")
            state.setdefault("tries", []).append(record)
            _untried(state)
            _save_state(did, state, environ)
            return {"ok": False, "error": record["error"]}
        state.setdefault("tries", []).append(record)
        if record["ok"]:
            tried = d / "tried"  # exactly what ran: what Use it copies
            shutil.rmtree(tried, ignore_errors=True)
            tried.mkdir()
            for f in TRIED:
                shutil.copyfile(d / f, tried / f)
            state.update(state="ran", tried=digest(d, state), changedSinceTry=False)
        else:
            _untried(state)
        _save_state(did, state, environ)
    return {"ok": True, "count": count, "says": says}



def _has_run(state: dict) -> None:
    if state.get("state") != "ran" or not state.get("tried"):
        raise DraftError("it changed since it last ran: try it again before using it" if state.get("changedSinceTry")
                         else "it hasn't run on the picture yet: it is not offered until it has")


def _ran(did: str, d: Path, state: dict) -> Path:
    """The kept copy of exactly what ran, checked to still be what ran (else what the user must do first)."""
    _has_run(state)
    tried = d / "tried"
    if digest(tried, state) != state["tried"] or digest(d, state) != state["tried"]:
        raise DraftError("what would be used isn't exactly what ran: try it again before using it")
    return tried


_REVIEWS: dict[tuple[str, str], dict] = {}  # (draft, what ran) -> its review: read again only when what ran changes


def review(did: str, environ: Mapping[str, str] = os.environ) -> dict:
    """What Use it would admit, for the user to read before admitting it (ticket 62 L13): exactly the code that ran (its
    adapter, requirements and locked packages), every file it pinned with where it came from and its hash, every try with
    its error kept, the static checks a contribution gets (contribute.preflight on that same copy) and the facts of what
    it imports. `digest` is what Use it must be given back: a draft changed after its review is not admitted."""
    with _LOCK:
        d = folder(did, environ)
        state = _state(did, environ)
        tried = _ran(did, d, state)
        key = (did, state["tried"])
        if key not in _REVIEWS:
            _REVIEWS[key] = _review(did, tried, state)
        return {**_REVIEWS[key], "tries": [dict(t) for t in state.get("tries") or []]}


def _review(did: str, tried: Path, state: dict) -> dict:
    import ast
    import tempfile

    from . import contribute

    adapter = (tried / "adapter.py").read_text(errors="replace")
    requirements = [ln for ln in (tried / "requirements.in").read_text().splitlines() if ln.strip()]
    packages = sorted({ln.split()[0].rstrip("\\") for ln in (tried / "env.lock").read_text().splitlines()
                       if re.match(r"^[A-Za-z0-9_.\-\[\]]+==\S+", ln)})
    try:
        tree = ast.parse(adapter)
        imports = sorted({(n.module if isinstance(n, ast.ImportFrom) else a.name).split(".")[0]
                          for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                          for a in (n.names if isinstance(n, ast.Import) else [n]) if getattr(n, "level", 0) == 0
                          and (n.module if isinstance(n, ast.ImportFrom) else a.name)})
    except SyntaxError:
        imports = []
    with tempfile.TemporaryDirectory(prefix="draft-review-") as scratch:  # the same static checks a contribution gets
        copy = Path(scratch) / did
        copy.mkdir()
        for f in TRIED:
            shutil.copyfile(tried / f, copy / f)
        _manifest(did, copy, state)
        # A "TODO" in the AI's own text is the reviewer's to read; only a scaffold placeholder left unfilled blocks.
        unfinished = {f.name for f in copy.iterdir() if f.is_file() and contribute.unfilled(f.read_text(errors="replace"))}
        checks = [{"blocks": f.blocks, "says": f.says} for f in contribute.preflight(str(copy))
                  if (f.blocks or "catalog format 1" not in f.says)
                  and not (f.says.endswith("still says TODO: fill each one in") and f.says.split()[0] not in unfinished)]
    files = [{k: f[k] for k in ("path", "bytes", "sha256", "url") if k in f} for f in state.get("files", [])]
    # The pinned files are its makers' code or data, read by the adapter: code (.py) is scanned too, its findings said as
    # notes (the contribution rules bind the entry's own code); anything else is checked by its hash only (L13 review).
    from .publish import FORBIDDEN

    scanned, by_hash = ["adapter.py", "requirements.in", "env.lock"], []
    for f in files:
        if not f["path"].endswith(".py"):
            by_hash.append(f["path"])
            continue
        scanned.append(f"{f['path']} (pinned)")
        text = (tried.parent / "files" / f["path"]).read_text(errors="replace")
        for pattern, why in FORBIDDEN:
            hit = pattern.search(text)
            if hit and not (why is None and "local_files_only=True" in hit.group(0)):
                checks.append({"blocks": False, "says": f"its pinned {f['path']} "
                               f"{why or 'loads with from_pretrained without local_files_only=True'}: read whether the "
                               "adapter calls that part"})
        if contribute.CUDA_CODE.search(text):
            checks.append({"blocks": False, "says": f"its pinned {f['path']} puts work on CUDA somewhere: read whether "
                                                    "the adapter calls that part"})
    facts = [f"It runs adapter.py ({len(adapter.splitlines())} lines) in Python {PYTHON} with {len(packages)} locked "
             f"package{'s' if len(packages) != 1 else ''}, each installed as a ready-built wheel checked by its hash.",
             f"It imports {', '.join(imports) or 'nothing'}."]
    facts.append(f"It reads {len(files)} pinned file{'s' if len(files) != 1 else ''}: "
                 + "; ".join(f"{f['path']} ({f['bytes']:,} bytes, sha256 {f['sha256'][:12]}…) from {f['url']}" for f in files)
                 + "." if files else "It reads no pinned files: whatever it runs comes from its packages.")
    facts.append(f"Static checks scanned {', '.join(scanned)}"
                 + (f"; {', '.join(by_hash)} {'was' if len(by_hash) == 1 else 'were'} checked by hash only." if by_hash else "."))
    return {"digest": state["tried"], "source": state.get("link", ""), "adapter": adapter, "requirements": requirements,
            "packages": packages, "files": files, "imports": imports, "checks": checks, "facts": facts,
            "scanned": scanned, "byHash": by_hash,
            "says": "Drafted by AI from its source and run only inside the sandbox. Read what it runs before Use it: "
                    "its tries show a contract check on your picture, not that its results are right."}


def promote(did: str, environ: Mapping[str, str] = os.environ, *, reviewed: str = "") -> dict:
    """Use it, after its review: a draft that ran becomes one of your entries (mine/models/<id>/), its files adopted into
    the models folder, installed and proven as any entry of yours (sandboxed: nobody but you reviewed it). `reviewed` is
    the review's digest: what is admitted is exactly what the user read."""
    with _LOCK:  # no edit lands between the check that it is what ran and its copy
        return _promote(did, environ, reviewed)


def _promote(did: str, environ, reviewed: str) -> dict:
    from . import mine

    d = folder(did, environ)
    state = _state(did, environ)
    _has_run(state)
    comparison = state.get("comparison") or {}
    if state.get("trial") or state.get("job", "find") != "find":
        if comparison.get("digest") != state["tried"] or comparison.get("inputSha256") != state["input"]["sha256"]:
            raise DraftError("Compare this draft with the current default before Use it")
    tried = _ran(did, d, state)
    seen = review(did, environ)
    if not reviewed or reviewed != seen["digest"]:
        raise DraftError("review what it runs before Use it: it changed since you read it, or you haven't yet"
                         if reviewed else "review what it runs before Use it")
    blocking = [c["says"] for c in seen["checks"] if c["blocks"]]
    if blocking:
        raise DraftError("its review found what stops it being used: " + "; ".join(blocking[:3]))
    target_id = mine._free_id(did, environ)
    target = mine.root(environ) / target_id
    target.mkdir(parents=True)
    try:
        for f in TRIED:  # the copy of exactly what ran
            shutil.copyfile(tried / f, target / f)
        if target_id != did:
            state = {**state, "id": target_id}
        _manifest(target_id, target, state)
        m = catalog.mine(environ).get(f"mine/{target_id}")
        if m is None:
            raise DraftError("its entry doesn't read: " + "; ".join(map(str, catalog.problems(environ).get(f"mine/{target_id}", []))))
        if state.get("files"):
            _adopt(m, d / "files", environ)
    except Exception as exc:  # nothing half-made is left as one of yours; the draft and its tries stay as they were
        shutil.rmtree(target, ignore_errors=True)
        catalog.manifests.cache_clear()
        if isinstance(exc, DraftError):
            raise
        raise DraftError(f"it couldn't be added as one of yours, so nothing was added: {exc}") from exc
    state["state"] = "used"
    state["model"] = m.key
    state["reviewed"] = {"digest": reviewed, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    _save_state(did, state, environ)
    return {"model": m.key, "says": f"{m.name} is one of your models now, drafted by AI and reviewed by you: turn it on "
                                    "(it installs and checks itself inside the sandbox)"}


def _adopt(m: catalog.Manifest, files: Path, environ) -> None:
    """Every file it pinned, staged together and published as its revision, each checked against its pin (a draft may
    pin several: downloads.adopt_file stages a model's one file)."""
    from . import downloads

    if downloads.model_files.published(downloads.model_files.owner(m), environ) is not None:
        return
    with downloads.Lock(m, environ):
        stage = downloads.staging(m, environ)
        stage.mkdir(parents=True, exist_ok=True)
        for f in m.files:
            downloads.clone((files / f["path"]).resolve(), stage / f["path"])
        downloads.publish(m, "pinned by the builder", environ)


def wire(environ: Mapping[str, str] = os.environ) -> list[dict]:
    out = []
    for p in sorted(root(environ).glob("*/draft.json")) if root(environ).is_dir() else ():
        try:
            s = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        tries = s.get("tries") or []
        seen = None
        if s.get("state") == "ran" and s.get("id"):  # what Use it would admit, to read first
            if _LOCK.acquire(blocking=False):  # never waits: Use it holds the lock while it checks its files' hashes
                try:
                    seen = review(s["id"], environ)
                except (DraftError, OSError) as exc:
                    seen = {"unavailable": str(exc)}
                finally:
                    _LOCK.release()
            else:
                seen = {"busy": True, "unavailable": "it is being updated right now"}  # the next broadcast shows it
        out.append({"review": seen, "reviewed": s.get("reviewed"), "id": s.get("id"),
                    "name": (s.get("fields") or {}).get("name") or s.get("name") or s.get("id"),
                    "link": s.get("link"), "state": s.get("state"), "tries": len(tries),
                    "last": tries[-1] if tries else None, "model": s.get("model", ""),
                    "input": s.get("input"), "terminal": s.get("terminal"), "trial": s.get("trial"), "job": s.get("job"),
                    "comparison": s.get("comparison")})
    return out
