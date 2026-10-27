"""The app's managed Fiji, kept as complete generations (phase 3's E3, the review's correction 2).

    <catalog>/tools/fiji/
      1/Fiji/            generation 1: the pinned build (catalog/models/fiji), unpacked, proven, its receipt last
      2/Fiji/            generation 2: generation 1 cloned (APFS) with one more update site, proven again
      current            the generation new work uses (replaced atomically); earlier ones are kept for work already
                         running on them and for rollback
      issued             the last number issued, retained even when Space removes every installed generation

- **A generation in use never changes.** Turning on an extension's update site makes the next generation: the current
  one cloned, the site added with Fiji's own updater (`--update add-update-site`), and only that site's files installed
  (`list-from-site`, then `update` of those: Fiji's own files stay the pinned build's; the dependencies the updater
  brings are recorded).
- **Its receipt is the whole installation:** the base build's hash, the update sites on, and every file with its
  checksum and timestamp from that generation's own `db.xml.gz`, written last. A generation without a receipt is never
  used.
- **It becomes current only once proven:** a fresh Fiji on it runs every reviewed binding's proof (`PROOFS`) and the
  smoke proof (a headless macro). A generation that fails one stays, marked failed with why, and the current one does
  not change.
- **Rollback** makes the previous whole generation current again.

Nothing here runs on the event loop: `Installs` (envs.py) runs these in its thread, said in Activity.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import time
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Mapping

from gpu_worker import manifests as catalog
from gpu_worker import model_files
from . import owned_process

RECEIPT = "receipt.json"
FAILED = ".failed"
BUILDING = ".building"
_NUMBER_LOCK = threading.Lock()
STEP_S = 900
SMOKE = 'print("fiji " + getVersion());\n'
# Reviewed bindings' proofs, run on every new generation before it is made current: id -> callable(fiji_root) raising
# on failure (fiji_bindings.py adds its own).
PROOFS: dict[str, Callable[[Path], str | dict]] = {}


class Failed(RuntimeError):
    pass


def root(environ: Mapping[str, str] = os.environ) -> Path:
    from . import paths

    return paths.tools(environ) / "fiji"


def entry() -> catalog.Manifest:
    m = catalog.get("fiji")
    if m is None:
        raise Failed("the catalog has no Fiji entry")
    return m


def current(environ: Mapping[str, str] = os.environ) -> int | None:
    try:
        n = int((root(environ) / "current").read_text().strip())
    except (OSError, ValueError):
        return None
    return n if (root(environ) / str(n) / RECEIPT).is_file() else None


def fiji_dir(n: int, environ: Mapping[str, str] = os.environ) -> Path:
    return root(environ) / str(n) / "Fiji"


def receipt(n: int, environ: Mapping[str, str] = os.environ) -> dict | None:
    try:
        return json.loads((root(environ) / str(n) / RECEIPT).read_text())
    except (OSError, ValueError):
        return None


def generations(environ: Mapping[str, str] = os.environ) -> list[dict]:
    """Every generation, oldest first: {n, state (current | kept | failed | building), receipt or why}."""
    out, cur = [], current(environ)
    base = root(environ)
    for d in sorted((p for p in base.iterdir() if p.is_dir() and p.name.isdigit()) if base.is_dir() else [],
                    key=lambda p: int(p.name)):
        n = int(d.name)
        r = receipt(n, environ)
        if r is not None:
            out.append({"n": n, "state": "current" if n == cur else "kept", "receipt": r})
        elif (base / f"{n}{FAILED}").is_file():
            out.append({"n": n, "state": "failed", "why": json.loads((base / f"{n}{FAILED}").read_text()).get("why", "")})
        else:
            out.append({"n": n, "state": "building"})
    return out


def sites(n: int | None = None, environ: Mapping[str, str] = os.environ) -> set[str] | None:
    """The update sites on in generation `n` (the current one by default); None when there is no Fiji yet."""
    n = current(environ) if n is None else n
    r = receipt(n, environ) if n is not None else None
    return {s["name"] for s in r["sites"]} if r else None


def _launcher(n: int, environ: Mapping[str, str] = os.environ) -> Path:
    return fiji_dir(n, environ) / "fiji"


def run_fiji(n: int, args: list[str], environ: Mapping[str, str] = os.environ, stop: Callable[[], bool] = lambda: False,
             what: str = "Fiji") -> str:
    """Fiji of generation `n`, headless, with a clean environment (its own JDK; nothing of this shell)."""
    env = {k: v for k, v in environ.items() if k in ("HOME", "USER", "LANG", "TMPDIR", "MICROMIND_REFUSE_NETWORK")}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    process = subprocess.Popen([str(_launcher(n, environ)), "--headless", *args], env=env, cwd=str(fiji_dir(n, environ)),
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        with owned_process.scope(stop=stop), owned_process.watching(process, STEP_S, what):
            out, _ = process.communicate()
    except owned_process.Stopped as exc:
        raise Failed(str(exc)) from exc
    finally:
        owned_process.terminate(process)
    if process.returncode:
        last = [line for line in out.splitlines() if line.strip() and not line.startswith("[")][-1:] or ["(no words)"]
        raise Failed(f"{what} failed: {last[0][:300]}")
    return out


def index(n: int, environ: Mapping[str, str] = os.environ) -> dict:
    """What generation `n` holds, from its own db.xml.gz: its update sites on and every file installed with its
    checksum and timestamp (what makes it reproducible, the review's correction 2)."""
    tree = ET.fromstring(gzip.decompress((fiji_dir(n, environ) / "db.xml.gz").read_bytes()))
    sites_on = [{"name": s.get("name"), "url": s.get("url"), "timestamp": s.get("timestamp")}
                for s in tree.iter("update-site") if s.get("active", "true") != "false"]
    files = []
    for p in tree.iter("plugin"):
        version = p.find("version")
        if version is None:
            continue  # known to a site, not installed here
        path = fiji_dir(n, environ) / p.get("filename", "")
        if path.exists():
            files.append({"file": p.get("filename"), "site": p.get("update-site", ""), "checksum": version.get("checksum"),
                          "timestamp": version.get("timestamp")})
    return {"sites": sites_on, "files": files}


def smoke(n: int, environ: Mapping[str, str] = os.environ) -> str:
    """A headless macro on generation `n`: its Fiji and JDK start, and ImageJ answers."""
    macro = root(environ) / str(n) / "smoke.ijm"
    macro.write_text(SMOKE)
    out = run_fiji(n, ["--console", "-macro", str(macro)], environ, what="its smoke test")
    line = next((l for l in out.splitlines() if l.startswith("fiji ")), "")
    if not line:
        raise Failed("its smoke test said nothing back")
    return line.removeprefix("fiji ")


def _prove(n: int, environ: Mapping[str, str], say: Callable[[str], None], stop=lambda: False) -> dict:
    from . import fiji_bindings  # noqa: F401 - every reviewed binding registers its proof (live 2026-10-13: a generation
    # set up before the bindings were imported had only the smoke proof, and its binding was offered unproven)

    from . import fiji_runner

    # The generation in use before this one: a function proven there must be proven here too (never a regression).
    before = current(environ)
    held = ((receipt(before, environ) or {}).get("proofs") or {}) if before is not None else {}
    try:
        with owned_process.scope(seconds=STEP_S, stop=stop):
            proofs = {"smoke": smoke(n, environ)}
            for name, proof in PROOFS.items():
                say(f"proving {name}")
                proofs[name] = proof(fiji_dir(n, environ))
            for m in fiji_runner.entries(environ):
                if m.namespace:
                    continue
                say(f"proving {m.name}")
                try:
                    proofs[m.key] = fiji_runner.proof(m, fiji_dir(n, environ), environ)
                except Failed as exc:
                    if fiji_runner.proof_current(m, held.get(m.key)):  # it worked before: this generation would lose it
                        raise Failed(f"{m.name} works on generation {before} and failed on this one, so it is not made "
                                     f"current: {exc}") from exc
                    proofs[m.key] = {**fiji_runner.identity(m), "passed": False, "why": str(exc)}  # not offered, said
    except owned_process.Stopped as exc:
        raise Failed(str(exc)) from exc
    return proofs


def _finish(n: int, base: dict, added: list[dict], proofs: dict, environ: Mapping[str, str], since: int | None) -> None:
    held = index(n, environ)
    r = {"n": n, "from": since, "base": base, "added": added, "sites": held["sites"], "files": held["files"],
         "proofs": proofs, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    tmp = root(environ) / str(n) / (RECEIPT + ".tmp")
    tmp.write_text(json.dumps(r, indent=1) + "\n")
    tmp.replace(root(environ) / str(n) / RECEIPT)  # last: from here on it is whole
    make_current(n, environ)


def make_current(n: int, environ: Mapping[str, str] = os.environ) -> None:
    if receipt(n, environ) is None:
        raise Failed(f"generation {n} is not whole")
    tmp = root(environ) / "current.tmp"
    tmp.write_text(f"{n}\n")
    tmp.replace(root(environ) / "current")


def _attempt(n: int, environ: Mapping[str, str], work: Callable[[], None]) -> None:
    base = root(environ)
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{n}{BUILDING}").write_text(json.dumps({"pid": os.getpid()}))
    try:
        work()
    except BaseException as exc:
        why = str(exc) if isinstance(exc, Failed) else f"{type(exc).__name__}: {exc}"
        (base / f"{n}{FAILED}").write_text(json.dumps({"why": why, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}))
        raise Failed(why) from exc
    finally:
        (base / f"{n}{BUILDING}").unlink(missing_ok=True)


def next_generation(environ: Mapping[str, str] = os.environ) -> int:
    """Never reuse an issued number, including after Space removes every runtime directory."""
    with _NUMBER_LOCK:
        base = root(environ)
        base.mkdir(parents=True, exist_ok=True)
        issued = [int(p.name.split('.')[0]) for p in base.iterdir() if p.name.split('.')[0].isdigit()]
        for name in ('current', 'issued'):
            try:
                issued.append(int((base / name).read_text()))
            except (OSError, ValueError):
                pass
        n = max(issued, default=0) + 1
        tmp = base / 'issued.tmp'
        tmp.write_text(str(n))
        tmp.replace(base / 'issued')
        return n


def first(environ: Mapping[str, str] = os.environ, say: Callable[[str], None] = lambda s: None,
          stop: Callable[[], bool] = lambda: False) -> int:
    """The downloaded build at a fresh number: unpacked in place, proven, its receipt last, made current."""
    m = entry()
    zipped = model_files.published(m, environ)
    if zipped is None:
        raise Failed("Fiji isn't downloaded yet: its Download button first")
    if current(environ) is not None:
        return current(environ)
    n = next_generation(environ)
    d = root(environ) / str(n)

    def work() -> None:
        if d.exists():
            shutil.rmtree(d)  # a failed try of generation 1: this is its retry
        d.mkdir(parents=True)
        say("unpacking Fiji")
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(zipped / m.files[0]["path"]), str(d)], check=True,
                       capture_output=True)
        say("starting it once to prove it runs")
        proofs = _prove(n, environ, say, stop)
        _finish(n, {"sha256": m.files[0]["sha256"], "url": m.files[0]["url"], "version": m.version}, [], proofs,
                environ, None)

    (root(environ) / f"{n}{FAILED}").unlink(missing_ok=True)
    _attempt(n, environ, work)
    return n


def add_site(name: str, url: str, environ: Mapping[str, str] = os.environ, say: Callable[[str], None] = lambda s: None,
             stop: Callable[[], bool] = lambda: False) -> int:
    """The next generation: the current one cloned, `name`'s update site added and only its files installed, every
    proof run again on it, then made current. The current one is never changed."""
    cur = current(environ)
    if cur is None:
        raise Failed("Fiji isn't set up yet: set it up first")
    if name in (sites(cur, environ) or set()):
        return cur
    n = next_generation(environ)
    d = root(environ) / str(n)

    def work() -> None:
        say(f"copying generation {cur}")
        subprocess.run(["cp", "-cR", str(root(environ) / str(cur) / "Fiji"), str(d / "Fiji")], check=True,
                       capture_output=True)
        say(f"adding the {name} update site")
        run_fiji(n, ["--update", "add-update-site", name, url], environ, stop, what=f"adding {name}")
        listed = run_fiji(n, ["--update", "list-from-site", name], environ, stop, what=f"reading {name}'s files")
        files = [line.split("\t")[0] for line in listed.splitlines() if "\t" in line and not line.startswith("[")]
        if not files:
            raise Failed(f"the {name} update site lists no files")
        say(f"installing {len(files)} files from {name}")
        run_fiji(n, ["--update", "update", *files], environ, stop, what=f"installing {name}'s files")
        say("proving it")
        proofs = _prove(n, environ, say, stop)
        _finish(n, receipt(cur, environ)["base"], [*receipt(cur, environ)["added"], {"site": name, "url": url}],
                proofs, environ, cur)

    d.mkdir(parents=True)
    _attempt(n, environ, work)
    return n


def proven(binding: str, environ: Mapping[str, str] = os.environ, *, n: int | None = None) -> str:
    """"" when the generation in use recorded `binding`'s proof, else why the binding can't run yet."""
    from . import fiji_bindings, fiji_runner, fiji_contributions

    folder = fiji_runner.entry_of(binding, environ)
    if folder is not None and folder.namespace:
        installed = fiji_contributions.in_use(folder, environ)
        if installed is not None and (n is None or installed.n == n):
            return ""
        if fiji_contributions.turned_off(folder, environ):
            return "This capability is turned off; Turn on checks and enables it again"
        return "This contribution needs its own confined Fiji proof; its card checks it without changing other capabilities"
    n = current(environ) if n is None else n
    if n is None:
        return "Fiji isn't set up here (its card on the Models page sets it up)"
    proof = ((receipt(n, environ) or {}).get("proofs") or {}).get(binding)
    folder = fiji_runner.entry_of(binding, environ)
    if folder is not None:  # a Fiji function folder: its proof must be of exactly this folder, worker and script
        if isinstance(proof, dict) and proof.get("passed") is False:
            return f"its proof failed on Fiji generation {n}: {proof.get('why')}"
        if not fiji_runner.proof_current(folder, proof):
            return f"not proven on Fiji generation {n} yet (Prove it on Fiji makes a new generation and proves it)"
        return ""
    if not proof or (binding == fiji_bindings.BINDING and not fiji_bindings.proof_current(proof)):
        return f"not proven on Fiji generation {n} yet (Prove it on Fiji makes a new generation and proves it)"
    return ""


def renew(environ: Mapping[str, str] = os.environ, say: Callable[[str], None] = lambda s: None,
          stop: Callable[[], bool] = lambda: False) -> int:
    """The next generation, the current one as it is, with every reviewed binding proven on it: how a binding added
    after a generation was made becomes usable (never by changing that generation's receipt)."""
    cur = current(environ)
    if cur is None:
        raise Failed("Fiji isn't set up yet: set it up first")
    n = max(g["n"] for g in generations(environ)) + 1
    d = root(environ) / str(n)

    def work() -> None:
        say(f"copying generation {cur}")
        subprocess.run(["cp", "-cR", str(root(environ) / str(cur) / "Fiji"), str(d / "Fiji")], check=True,
                       capture_output=True)
        say("proving it")
        proofs = _prove(n, environ, say, stop)
        _finish(n, receipt(cur, environ)["base"], receipt(cur, environ)["added"], proofs, environ, cur)

    d.mkdir(parents=True)
    _attempt(n, environ, work)
    return n


def check(environ: Mapping[str, str] = os.environ, say: Callable[[str], None] = lambda s: None) -> dict:
    """Every proof run again on the generation in use, changing nothing (make catalog-proof): {name: what it proved},
    or Failed with the proof's words."""
    n = current(environ)
    if n is None:
        raise Failed("Fiji isn't set up here (its card on the Models page sets it up)")
    try:
        return _prove(n, environ, say)
    except Failed:
        raise
    except Exception as exc:  # noqa: BLE001 - a proof's own error, said
        raise Failed(f"{type(exc).__name__}: {exc}") from exc


def rollback(environ: Mapping[str, str] = os.environ) -> int:
    """The previous whole generation made current again (the newer ones are kept)."""
    cur = current(environ)
    whole = [g["n"] for g in generations(environ) if g["state"] in ("current", "kept")]
    earlier = [n for n in whole if cur is not None and n < cur]
    if not earlier:
        raise Failed("there is no earlier Fiji to go back to")
    make_current(earlier[-1], environ)
    return earlier[-1]


def build_hash(n: int, environ: Mapping[str, str] = os.environ) -> str:
    """One hash of everything a generation holds (its receipt's files), for comparing two."""
    r = receipt(n, environ) or {}
    return hashlib.sha256(json.dumps(sorted((f["file"], f["checksum"]) for f in r.get("files", []))).encode()).hexdigest()[:16]
