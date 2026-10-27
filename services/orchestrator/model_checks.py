"""Whether each model the capabilities list names runs here: its last check, said beside the user's verdicts and
told to the models (the user's Q8 and Q20 of 2026-10-11; Slice 8 of that day's plan).

Live 2026-10-11: SAM 3.1 and micro-sam on this Mac were shown "available" whether or not they could run, and the
first anyone heard of micro-sam's 120 s cold load was the frozen view. The user's bar: "every model in the
capabilities list works for the live voice, chat and researchers, or the model and the UI say it's broken".

- **What is checked** (`entries`): every option of a model role in contract/capabilities.json, every option of a
  method whose choice runs a model of its own (pointing, dissect, following, the voice's link, the lesson and claim
  checks), and every model fixed in the code, its fallback too: one entry per (role, option), named once
  (names.said). Options that run no model ("off", "auto") are no entry.
- **How** (`Checker`): each entry's probe runs its model once on a real frame (the assets store's leaf frame,
  FRAME), through the app's own code: the runner (operations.Runner, origin "check": never an outcome) and the GPU
  workers for what runs on this Mac, the app's provider classes for the cloud. Entries that share a probe
  (vision:gemini-3.8-flash and fast:gemini-3.8-flash ask the same provider spec) run it once. The live view comes
  first: one model at a time, GPU calls in the workers' lowest class ("research"), loads through Slice 1's path (the
  memory guard, said while loading), and a model the check loaded is let go after it (op `unload`) unless it was
  loaded before. Inside the live app (Check now) nothing it loads pushes out a model the live view keeps loaded
  (loads.sparing): a model that does not fit in the memory free is not checked, said why (live 2026-10-11 16:31: its
  Molmo, 12.5 GB, pushed out the live app's Cellpose-SAM and DINOv2, and swap-ins reached 6,387/s). No tool runs: a
  tool would change what the user sees.
- **States** (STATES): works; broken, with the error's own words; not installed, with how to install it (code, its
  own Python, or weights not downloaded yet); needs a key; not in this edition; not checked, with why (a WebRTC voice
  needs a browser, a video costs about $0.40, the follower's model while the live app follows, a model there is no
  memory for beside the live view's, or the check's named input is unavailable).
- **Kept** in paths.checks (checks.jsonl, a performance log beside the verdicts): one line a check with its time, how long it
  took, the load it needed, the build, who ran it and an estimated cost; the latest line per entry counts
  (`Checks.latest`).
- **Said**: the capabilities wire carries each option's `check` (capabilities.wire) and the Capabilities page shows
  it beside, never over, the user's verdicts; the agent's capabilities text says "<name> is broken here: <why>"; `use`
  and `set_default` refuse a model whose last check on this build was broken (`broken`). One from an earlier build
  is said, never refused: the code may have changed since.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from gpu_worker.manifests import local as _local

STATES = ("works", "broken", "not-installed", "needs-key", "not-in-edition", "not-checked")
STATE_WORDS = {"works": "works", "broken": "broken", "not-installed": "not installed", "needs-key": "needs a key",
               "not-in-edition": "not in this edition", "not-checked": "not checked"}

# The real frame every probe runs on (assets/README.md: the bench's frames), and where its things are: a stoma
# in the leaf epidermis of 2026-06-14 (frame 12 s of leaf-132922), and the eyepiece circle.
FRAME = "frames/leaf-132922-012s.jpg"
STOMA_BOX = (825.0, 350.0, 885.0, 430.0)
DISC = (695.0, 360.0, 395.0)
PRIORITY = "research"  # the GPU workers' lowest class: served only when nothing of the app's own waits
CLOUD_PROBES = ("vision", "text", "image", "video", "voice", "webrtc", "fal")
VIDEO_USD = 0.40  # Veo 3.1 Fast's shortest clip (4 s at budget.PRICES' $0.10 a second): not run unless asked
# How long one probe may take, its model's load included: micro-sam loaded cold in 120 s under load on 2026-10-11 and
# SAM 3.1 on this Mac loads 3.5 GB; a cloud call answers in seconds; a video in minutes.
TIMEOUT_S = {"local": 600.0, "cloud": 120.0, "video": 420.0, "voice": 60.0}
# A model the check never lets go of: the click worker's EdgeTAM and the follower's (loads.py keeps them too).
KEEP_LOADED = {("micromind-gpu", "edgetam"), ("micromind-gpu-follow", "edgetam-video")}
RAW = re.compile(r"^[A-Z][A-Za-z]*(?:Error|Exception|Fault|Exit|Interrupt|Timeout): ")  # operations.RAW_ERROR
SAY_PROMPT = ('Reply with JSON only: {"ok": true, "what": "<two words for what this picture shows>"}')
TEXT_PROMPT = 'Reply with JSON only: {"ok": true}'


@dataclass(frozen=True)
class Entry:
    """One model as the capabilities list names it: a role's option, or a model fixed in the code."""
    role: str  # an option group ("outline") or a fixed role ("fixed:link")
    option: str  # the option's id; a fixed role's "model", or "fallback" for the one the code takes when it can't
    name: str  # its one name with where it runs (names.said)
    where: str  # "mac" | "cloud"
    probe: tuple  # what runs it: (kind, what); entries with the same probe share one run
    uses: tuple = ()  # licences.COMPONENTS ids
    needs: str = ""  # an environment key it cannot run without
    installed: str = ""  # operations.INSTALLED key
    weights: str = ""  # the catalog model whose pinned files it needs (downloaded with Download, never on first use)
    group_option: tuple | None = None  # (group, option) of options.GROUPS, for options.unavailable

    @property
    def key(self) -> str:
        return f"{self.role}:{self.option}"


# --- the entries --------------------------------------------------------------------------------------------------
# Method roles whose options run a model of their own, and the probe of each such option. A behaviour choice that runs
# no model ("box", "slide", "off") is no entry.
METHOD_PROBES = {
    # Pointing "masks" asks the heavy worker for EdgeTAM's automatic masks (masks.py), as CELLS edgetam-automask does.
    ("pointing", "masks"): ("cells", "edgetam-automask"), ("pointing", "molmo"): ("molmo", "molmo2-4b"),
    ("dissect", "grid"): ("regions", "edgetam"), ("dissect", "vlm-boxes"): ("regions", "edgetam"),
    ("track", "objects"): ("track", "edgetam-video"),
    ("voicelink", "relay"): ("voice", "gpt-live-1"), ("voicelink", "webrtc"): ("webrtc", "gpt-live-1"),
    ("lesson_check", "check"): ("text", "gemini:gemini-3.8-flash"),
    ("lesson_check", "rewrite"): ("text", "gemini:gemini-3.8-flash"),
    ("claim_check", "model"): ("text", "gemini:gemini-3.8-flash"),  # research_worker fast_json: CHECK_MODEL
    # The pointing reflex's model (brain/loop.py Brain.reflex): "lean" is the fast model's default, the others named.
    ("reflex", "lean"): ("vision", "gemini-3.8-flash"), ("reflex", "lean-3.5-flash"): ("vision", "gemini-3.5-flash"),
    ("reflex", "lean-lite"): ("vision", "gemini-3.5-flash-lite"),
    # Photo search: both run the grounded lookup and its pixel check (research_worker.retrieval: LOOKUP_MODEL); Commons'
    # catalogue beside it is a web service with no model of its own.
    ("reference_search", "web"): ("text", "gemini:gemini-3.8-flash"),
    ("reference_search", "web+commons"): ("text", "gemini:gemini-3.8-flash"),
    # The outline check's redraw with another model (OUTLINE_REPAIR): that model outlining, as OUTLINE's own check does.
    ("outline_repair", "sam2.1-tiny"): ("outline", "sam2.1-tiny"),
    ("outline_repair", "sam3.1-local"): ("outline", "sam3.1-local"),
    ("outline_repair", "micro-sam-lm"): ("outline", "micro-sam-lm"),
}
# What runs each model fixed in the code (contract/capabilities.json roles "fixed:..."; their fallbacks).
FIXED_PROBES = {
    "fixed:video": ("video", "veo"), "fixed:lesson-check": ("text", "gemini"), "fixed:lesson-writer": ("text", "anthropic"),
    "fixed:lesson-writer:fallback": ("text", "openai"), "fixed:editor": ("text", "openai"),
    "fixed:find-all": ("fal", "sam3.1-all"), "fixed:group-names": ("zeroshot", "medsiglip"),
    "fixed:link": ("link", "trackastra"), "fixed:retrieval": ("text", "gemini"),
}
# The catalog model whose pinned files each local model and implementation needs: looked for as readiness does (the
# models folder, or an old cache at the pinned revision), so a check never starts a download of gigabytes unless asked
# (`make capability-check DOWNLOAD=1`). By model, not by Hugging Face repository: Trackastra's and micro-sam's files
# come from their own URLs (the catalog audit, 2026-10-12).
WEIGHTS = {key: m.id for m in _local().values() if m.files or m.files_from
           for key in (m.id, *(i.impl for i in m.implements))}
_entries: list[Entry] | None = None


def invalidate() -> None:
    """A catalog registration changed the available operations; rebuild the proof entries on next read."""
    global _entries
    _entries = None


def entries() -> list[Entry]:
    """Every model the capabilities list names, as entries, in the list's order of roles."""
    global _entries
    if _entries is None:
        _entries = _build()
    return list(_entries)


def _key_of(uses, *given: str) -> str:
    """The key an option needs: its own `needs`, else the key of the service it uses (capabilities.KEYS: a lesson
    check's Gemini, the voice link's OpenAI)."""
    from .capabilities import KEYS

    return next((g for g in given if g), "") or next((KEYS[u] for u in uses if u in KEYS), "")


def _build() -> list[Entry]:
    from . import capabilities, names
    from .operations import OPERATIONS
    from .options import GROUPS, VISION

    out: list[Entry] = []
    kinds = {"looks": "embed"}  # the operation that embeds how members look runs the worker's `embed`
    selects = {op.selected_by: op for op in OPERATIONS.values() if op.selected_by}
    for role in capabilities.load()["roles"]:
        rid = role["id"]
        if rid.startswith("fixed:"):
            out += _fixed(role)
            continue
        if rid not in GROUPS:
            continue
        for option in GROUPS[rid].values():
            if not capabilities.runs(option.uses):
                continue  # no model: "off", "auto", a way of working
            op = selects.get(rid)
            impl = next((i for i in op.implementations if i.option == option.id), None) if op is not None else None
            name = capabilities.said_option(rid, {"id": option.id, "label": option.label})
            uses, chosen = tuple(option.uses), (rid, option.id)
            if impl is not None:
                where = names.where_of(impl) or "mac"
                probe = ("fal", impl.id) if impl.worker == "cloud" else (kinds.get(op.id, op.id), impl.id)
                out.append(Entry(rid, option.id, name, where, probe, uses, _key_of(uses, option.needs, impl.needs),
                                 impl.installed or next((op.impl(s).installed for s in impl.steps if op.impl(s).installed), ""),
                                 WEIGHTS.get(impl.id) or next((WEIGHTS[s] for s in impl.steps if s in WEIGHTS), ""),
                                 chosen))
                continue
            if option.id in VISION and rid in ("vision", "sight", "fast"):
                probe = ("vision", option.id)  # Agent.vision(option): the same provider spec in each of the three
            elif rid == "zeroshot":
                probe = ("zeroshot", "medsiglip")
            elif rid == "voice":
                probe = ("voice", option.id)
            elif rid in ("research", "presentation"):
                probe = ("text", _provider(option.id) + ":" + option.id)
            elif rid == "image":
                probe = ("image", option.id)
            elif chosen in METHOD_PROBES:
                probe = METHOD_PROBES[chosen]
            else:
                raise ValueError(f"model_checks: no probe for {rid}:{option.id} (add it to METHOD_PROBES)")
            local = probe[0] not in CLOUD_PROBES
            out.append(Entry(rid, option.id, name, "mac" if local else "cloud", probe, uses, _key_of(uses, option.needs),
                             weights=WEIGHTS.get(probe[1], "") if local else "", group_option=chosen))
    return out


def _provider(model: str) -> str:
    return "anthropic" if model.startswith("claude") else "gemini" if model.startswith("gemini") else "openai"


def _fixed(role: dict) -> list[Entry]:
    """A fixed role's model, and its fallback when the code has one (fixed:lesson-writer: GPT-6.1 Sol without an
    Anthropic key)."""
    from . import capabilities, names
    from .capabilities import KEYS

    out = []
    for option, entry in (("model", role), *((("fallback", role["fallback"]),) if role.get("fallback") else ())):
        uses = tuple(entry.get("uses", ()))
        key = role["id"] if option == "model" else f"{role['id']}:fallback"
        kind, what = FIXED_PROBES[key]
        value = str(capabilities.constant(entry["source"]))
        if kind == "text":
            what = f"{what}:{value}"
        runs = {"both": "cloud"}.get(capabilities.runs(uses), capabilities.runs(uses)) or "mac"
        needs = next((KEYS[u] for u in uses if u in KEYS), "")
        installed = "trackastra" if kind == "link" else ""
        out.append(Entry(role["id"], option, names.said(entry.get("label", value), runs), runs, (kind, what), uses, needs,
                         installed, WEIGHTS.get(what, "") if kind == "zeroshot" else ""))
    return out


def find(role: str, option: str) -> Entry | None:
    return next((e for e in entries() if e.role == role and e.option == option), None)


# --- the store ----------------------------------------------------------------------------------------------------
class Checks:
    """The app's own checks: one JSON line a check in `path` (paths.checks), the latest per entry counts. `version`:
    the build it runs (capabilities.build), so a check from an earlier build is told apart."""

    def __init__(self, path: Path | None, version: str = "") -> None:
        self.path = Path(path) if path is not None else None
        self.version = version
        self._lock = threading.Lock()
        self._seen: tuple | None = None
        self._latest: dict[str, dict] = {}
        self.running: dict | None = None  # the check under way: {"by", "done", "of", "now"} (the page shows it)

    def record(self, entry: Entry, state: str, why: str = "", **facts) -> dict:
        if state not in STATES:
            raise ValueError(f"a check is one of {', '.join(STATES)}")
        line = {"role": entry.role, "option": entry.option, "name": entry.name, "where": entry.where, "state": state,
                "why": str(why or "")[:300], "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "build": self.version, **{k: v for k, v in facts.items() if v is not None}}
        if self.path is not None:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
        self._latest[entry.key] = line
        return line

    def latest(self) -> dict[str, dict]:
        """"role:option" -> its newest check (read again only when the file changed)."""
        if self.path is None:
            return dict(self._latest)
        try:
            stat = self.path.stat()
        except OSError:
            return dict(self._latest)
        seen = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if seen != self._seen:
            latest: dict[str, dict] = {}
            try:
                lines = self.path.read_text().splitlines()
            except OSError:
                lines = []
            for text in lines:
                try:
                    line = json.loads(text)
                except ValueError:
                    continue  # a line cut off mid-write is skipped, never fatal
                if isinstance(line, dict) and line.get("role") and line.get("option") and line.get("state") in STATES:
                    latest[f"{line['role']}:{line['option']}"] = line
            self._latest, self._seen = latest, seen
        return dict(self._latest)

    def of(self, role: str, option: str) -> dict | None:
        return self.latest().get(f"{role}:{option}")


def checks_of(hub) -> Checks | None:
    return getattr(hub, "checks", None)


def wire(line: dict | None, version: str = "") -> dict | None:
    """A check as the capabilities wire carries it (capabilities.wire): its state, why, when, and whether it was made
    on an earlier build than this one."""
    if not line:
        return None
    out = {"state": line["state"], "why": line.get("why", ""), "at": line.get("at", "")}
    for key in ("ms", "loadMs", "detail", "costUsd", "by"):
        if line.get(key) is not None:
            out[key] = line[key]
    if version and line.get("build") and line["build"] != version:
        out["earlierBuild"] = line["build"]
    return out


def at_words(at: str) -> str:
    """"02:41" from a check's ISO time (its own clock's), or the date and time when not today."""
    try:
        when = datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return str(at or "")
    today = datetime.now(when.tzinfo).date()
    return when.strftime("%H:%M") if when.date() == today else when.strftime("%Y-%m-%d %H:%M")


def broken(hub, role: str, option: str) -> str:
    """Why `option` of `role` is broken here, from its last check on this build ("" when it was not): what `use` and
    `set_default` refuse with. One from an earlier build is said (`said`), never refused."""
    store = checks_of(hub)
    line = store.of(role, option) if store is not None else None
    if not line or line.get("state") != "broken":
        return ""
    if store.version and line.get("build") and line["build"] != store.version:
        return ""
    return line.get("why") or "it failed when checked"


def refusal(hub, role: str, option: str, name: str) -> str:
    """"<name> is broken here: <why> (checked at 02:41)", or "" (broken)."""
    why = broken(hub, role, option)
    if not why:
        return ""
    line = checks_of(hub).of(role, option)
    return f"{name} is broken here: {why} (checked at {at_words(line.get('at', ''))})"


def said(line: dict | None, name: str, version: str = "") -> str:
    """How the agent's capabilities text says a model's last check: only a broken one ("" otherwise: what works needs
    no word, and what can't run is said by availability)."""
    if not line or line.get("state") != "broken":
        return ""
    when = at_words(line.get("at", ""))
    if version and line.get("build") and line["build"] != version:
        return (f"{name} was broken here at {when} on an earlier build ({line.get('why') or 'it failed'}); not "
                "checked on this one")
    return f"{name} is broken here: {line.get('why') or 'it failed when checked'} (checked at {when})"


# --- before running: what can be said without running it ------------------------------------------------------------
def weights_here(model: str, environ=os.environ) -> bool:
    """Whether a catalog model's pinned files are on this Mac, as readiness says (by size and content id)."""
    from gpu_worker import manifests as catalog

    from . import readiness

    m = catalog.get(model) if model else None
    return m is None or readiness.obtained(m, environ)[0] is not False


def fetch_weights(model: str, environ=os.environ) -> None:
    """DOWNLOAD=1: a catalog model's pinned files, fetched and verified into the models folder before it is checked
    (downloads.py: the loaders never fetch them). A download that fails is left to the check, which says it."""
    from gpu_worker import manifests as catalog

    from . import downloads

    m = catalog.get(model) if model else None
    if m is None or weights_here(model, environ):
        return
    try:
        downloads.download(m, environ=environ)
    except downloads.Failed:
        pass


def before(entry: Entry, *, environ=os.environ, download: bool = False, video: bool = False,
           live: bool = False) -> tuple[str, str]:
    """(state, why) when the entry can't run here, or ("", "") when it should be run: this edition, a key, its code
    or its weights, or a probe this check does not run (with why)."""
    from . import licences
    from .operations import INSTALLED
    from .options import unavailable

    why = licences.why_not(entry.uses)
    if why:
        return "not-in-edition", why
    if entry.needs and not environ.get(entry.needs):
        return "needs-key", f"needs {entry.needs}"
    if entry.group_option is not None:
        why = unavailable(*entry.group_option, environ=environ)
        if why.startswith("needs "):
            return "needs-key", why
        if why:
            return "not-installed", why
    if entry.installed:
        why = INSTALLED[entry.installed]()
        if why:
            return "not-installed", why
    if entry.where == "mac" and entry.weights and not download and not weights_here(entry.weights, environ):
        return "not-installed", (f"its files ({entry.weights}) are not downloaded here yet: its Download button on the Models "
                                 f"page, or make catalog-download ID={entry.weights} (make capability-check DOWNLOAD=1 "
                                 "downloads them first)")
    kind = entry.probe[0]
    if kind == "webrtc":
        return "not-checked", "a WebRTC voice needs a browser's offer; checked live (make voice-report)"
    if kind == "video" and not video:
        return "not-checked", (f"a video costs about ${VIDEO_USD:.2f} a check; make capability-check-cloud VIDEO=1 "
                               "checks it")
    if kind == "track" and live:
        # The follow worker holds one session (gpu_worker/worker.py track: `reset` forgets every object it follows).
        return "not-checked", ("inside the live app it would reset the follower's session; make capability-check "
                               "checks it with the app stopped")
    return "", ""


# --- running ------------------------------------------------------------------------------------------------------
class CheckInputUnavailable(OSError):
    """The named benchmark input could not be read; no model result can be inferred."""


def frame() -> bytes:
    """The leaf frame (FRAME) from the assets store, as JPEG bytes."""
    from .assets import store

    path = store().bench(FRAME)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CheckInputUnavailable(f"check input {FRAME} is unavailable: {exc}") from exc
    if not data:
        raise CheckInputUnavailable(f"check input {FRAME} is empty: {path}")
    return data


class Checker:
    """Runs entries' probes, one model at a time, and records each entry's state (Checks.record). `live`: inside the
    running app ("Check now"): never what would change the user's view or restart the follower."""

    def __init__(self, hub, agent, store: Checks, *, by: str, jpeg: bytes | None = None, live: bool = False,
                 download: bool = False, video: bool = False, environ=os.environ, log: Callable[[dict], None] | None = None,
                 progress: Callable[[dict], None] | None = None, public_example: bool = False) -> None:
        self.hub, self.agent, self.store, self.by = hub, agent, store, by
        self.jpeg = jpeg
        self.live, self.download, self.video, self.environ = live, download, video, environ
        self.log = log or (lambda record: None)
        self.progress = progress or (lambda state: None)
        self._capture = None
        from . import capability_example

        self.public_example = public_example
        self.box = capability_example.BOX if public_example else STOMA_BOX
        self.disc = capability_example.DISC if public_example else DISC
        self.cost = 0.0
        self.voice_connect = None  # GPT-Live's connection (openai_live `connect`): the SDK's unless a test gives one
        self.voice_speech: bytes | None = None  # the spoken request's PCM (a test's); else `speak(SPOKEN)`
        self.voice_prompt: str | None = None  # the app's voice prompt (the bench gives route.voice_prompt(app.brain))

    async def run(self, chosen: list[Entry]) -> list[dict]:
        """Each entry's state, its probe run once for every entry that shares it; in the list's order."""
        groups: dict[tuple, list[Entry]] = {}
        for entry in chosen:
            groups.setdefault(entry.probe, []).append(entry)
        lines: list[dict] = []
        done = 0
        self.store.running = {"by": self.by, "done": 0, "of": len(chosen), "now": ""}
        try:
            for probe, group in groups.items():
                runnable = []
                for entry in group:
                    if self.download and entry.where == "mac" and entry.weights:
                        await asyncio.to_thread(fetch_weights, entry.weights, self.environ)
                    state, why = before(entry, environ=self.environ, download=self.download, video=self.video,
                                        live=self.live)
                    if state:
                        lines.append(self.store.record(entry, state, why, by=self.by, probe=probe[0]))
                        done += 1
                    else:
                        runnable.append(entry)
                if not runnable:
                    continue
                self.store.running.update(done=done, now=runnable[0].name)
                self.progress(dict(self.store.running))
                facts = await self.probe(probe)
                state = facts.pop("state", None) or ("broken" if facts.get("why") else "works")
                for entry in runnable:
                    lines.append(self.store.record(entry, state, facts.get("why", ""), by=self.by, probe=probe[0],
                                                   **{k: v for k, v in facts.items() if k != "why"}))
                    done += 1
                self.log({"kind": "model-check", "probe": list(probe), "entries": [e.key for e in runnable],
                          "state": lines[-1]["state"], **{k: v for k, v in facts.items() if k in ("ms", "loadMs", "why")}})
        finally:
            self.store.running = None
            self.progress({"by": self.by, "done": done, "of": len(chosen), "now": "", "finished": True})
        return lines

    async def probe(self, probe: tuple) -> dict:
        """Run one probe: {"ms", "loadMs"?, "detail", "costUsd"?} when it ran, with "why" when it failed; "state"
        "not-checked" when inside the live app its model would have needed room only unloading could make."""
        from .loads import sparing

        kind, what = probe
        t0 = time.perf_counter()
        limit = TIMEOUT_S.get(kind, TIMEOUT_S["local"] if kind not in CLOUD_PROBES else TIMEOUT_S["cloud"])
        # Inside the live app a load of the check's never pushes out what the live view keeps loaded (loads.sparing):
        # one that does not fit is refused, however deep in the runner it was asked for.
        with (sparing() if self.live else contextlib.nullcontext([])) as refused:
            try:
                facts = await asyncio.wait_for(getattr(self, f"_{kind.replace('-', '_')}")(what), limit)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                facts = {"why": f"no answer within {limit:.0f} s"}
            except CheckInputUnavailable as exc:
                facts = {"state": "not-checked", "why": str(exc)}
            except Exception as exc:  # noqa: BLE001 - every failure is the check's finding, in the error's own words
                facts = {"why": _words(exc)}
        if refused:
            facts = {"state": "not-checked", "why": no_room_words(refused[0])}
        facts.setdefault("ms", round((time.perf_counter() - t0) * 1000))
        if facts.get("costUsd"):
            self.cost += float(facts["costUsd"])
        return facts

    # --- what runs on this Mac --------------------------------------------------------------------------------------
    def capture(self):
        """The frame as a view no live frame has (a negative id, no epoch: capture.read_back), so nothing places it
        on the live view and no worker's per-frame cache mixes it with one."""
        if self._capture is None:
            from .capture import captures

            seq = int(time.time() * 1000) % 1_000_000_000
            info = SimpleNamespace(id=1, width=96 if self.public_example else 1280, height=96 if self.public_example else 720, dx=0.0, dy=0.0, epoch=None, fov=None, disc=0.0,
                                   tracked=False, wall_at=time.time(), seq=seq, boot="capability-check", steady=True)
            self._capture = captures(self.hub).read_back(info, self.jpeg or frame(), None, run=f"capability-check-{seq}")
        return self._capture

    async def _worker_run(self, client, model: str, call: Callable) -> dict:
        """A worker call that may load `model`: how long the load took (the worker's own measure, loads.py), and
        the model let go after unless it was loaded before or is one the live view keeps."""
        from .operations import started

        loads = getattr(self.hub, "loads", None)
        if loads is not None:
            await loads.fits(client, model)  # inside the live app: not even started for a model there is no room for
        client = await started(client)
        held_before = model in (getattr(client, "resident", None) or {})
        try:
            facts = dict(await call(client))
            held = (getattr(client, "resident", None) or {}).get(model) or {}
            if not held_before and held.get("seconds") is not None:
                facts["loadMs"] = round(float(held["seconds"]) * 1000)
            return facts
        finally:
            if (not held_before and (getattr(client, "name", ""), model) not in KEEP_LOADED
                    and model in (getattr(client, "resident", None) or {})):
                try:
                    loads = getattr(self.hub, "loads", None)  # its own worker is stopped (Loads.let_go)
                    await (loads.let_go(client, model) if loads is not None
                           else client.call("unload", timeout=30.0, priority="interactive", model=model))
                except Exception as exc:  # noqa: BLE001 - said in the log; the check stands
                    self.log({"kind": "model-check-unload", "model": model, "error": _words(exc)})

    async def _run_operation(self, operation: str, impl_id: str, prompt, params: dict | None = None) -> dict:
        from .operations import OPERATIONS, runner, worker_client

        impl = OPERATIONS[operation].impl(impl_id)
        # The model the worker loads for it (gpu_worker/loading.model_of): a composite's last step; automatic masks
        # are EdgeTAM's. A big model is checked in its own worker, as it runs (hub.host_for).
        model = impl.steps[-1] if impl.steps else {"edgetam-automask": "edgetam"}.get(impl_id, impl_id)
        client = worker_client(self.hub, impl.worker, model=model) if impl.worker in ("interactive", "analysis") else None

        async def go(_client=None):
            run, capture = runner(self.hub), self.capture()
            try:
                result = await run.run(operation, capture, prompt, choice=impl_id, origin="check", ticket=None,
                                       params={"priority": PRIORITY, **(params or {})})
            except Exception as exc:
                # The runner says a raw error to the user as "... did not work this time"; the check keeps the real
                # one, from the execution's attempt (operations.Runner._execute).
                done = next((e for e in reversed(run.executions.values()) if e.capture == capture.id
                             and e.operation == operation and e.requested == impl_id), None)
                raw = next((a.error for a in reversed(done.attempts) if a.error), "") if done is not None else ""
                if raw:
                    raise RuntimeError(raw.split(": ", 1)[1] if RAW.match(raw) else raw) from exc
                raise
            return {"output": result.output, "executed": result.execution.executed, "execution": result.execution.id}

        out = await (self._worker_run(client, model, go) if client is not None else go())
        output = out.pop("output")
        return {**{k: v for k, v in out.items() if k == "loadMs"}, "execution": out["execution"],
                "detail": _made(operation, output)}

    async def _outline(self, impl: str) -> dict:
        from .operations import Prompt

        return await self._run_operation("outline", impl, Prompt("box", box=self.box))

    async def _fal(self, impl: str) -> dict:
        from .operations import Prompt

        if impl == "sam3.1-all":
            return await self._run_operation("find-like", impl, Prompt("example", box=self.box))
        from .operations import OPERATIONS, started, worker_client

        if OPERATIONS["outline"].impl(impl).steps:
            # EdgeTAM then SAM 3.1 (cloud) can run only when its EdgeTAM step can (operations.availability: the click
            # worker ready), even for a box that goes straight to SAM 3.1: the first cloud check, 2026-10-11 08:31,
            # called it broken ("the outline model is still loading") with no click worker started.
            await started(worker_client(self.hub, "interactive"))
        return await self._run_operation("outline", impl, Prompt("box", box=self.box))

    async def _cells(self, impl: str) -> dict:
        from .operations import Prompt

        # The disc as a tuple: the runner keys a job by its parameters (a list can't be a key).
        return await self._run_operation("cells", impl, Prompt("field"), {"disc": self.disc} if impl == "edgetam-automask" else None)

    def _crop(self) -> bytes:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(self.jpeg or frame(), np.uint8), cv2.IMREAD_COLOR)
        x0, y0, x1, y1 = (int(v) for v in self.box)
        return cv2.imencode(".jpg", image[max(0, y0 - 20):y1 + 20, max(0, x0 - 20):x1 + 20], [int(cv2.IMWRITE_JPEG_QUALITY), 92])[1].tobytes()

    def _heavy(self):
        return getattr(self.hub, "gpu_heavy", None) or getattr(self.hub, "gpu", None)

    async def _embed(self, model: str) -> dict:
        heavy = self._heavy()
        if heavy is None:
            raise RuntimeError("no GPU worker here")

        async def go(client):
            reply = await client.call("embed", timeout=120.0, priority=PRIORITY, jpegs=[self._crop()], model=model)
            if reply.get("model", model) != model:
                raise RuntimeError(f"the worker embedded with {reply.get('model')}, not {model}")
            vectors = reply.get("vectors") or []
            return {"detail": f"1 picture embedded ({len(vectors[0]) if vectors else 0} numbers)"}

        return await self._worker_run(heavy, model, go)

    async def _zeroshot(self, _what: str) -> dict:
        heavy = self._heavy()
        if heavy is None:
            raise RuntimeError("no GPU worker here")

        async def go(client):
            reply = await client.call("zeroshot", timeout=120.0, priority=PRIORITY, jpegs=[self._crop()],
                                      names=["stoma", "guard cell", "epidermal cell"], template="a {} in a leaf epidermis")
            ranking = (reply.get("rankings") or [[]])[0]
            return {"detail": f"ranked 3 names, first {ranking[0][0]!r}" if ranking else "ranked nothing"}

        return await self._worker_run(heavy, "medsiglip", go)

    async def _regions(self, _what: str) -> dict:
        gpu = getattr(self.hub, "gpu", None)
        if gpu is None:
            raise RuntimeError("no GPU worker here")
        x0, y0, x1, y1 = self.box
        polygon = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]

        async def go(client):
            capture = self.capture()
            reply = await client.call("regions", timeout=60.0, priority=PRIORITY, frame_id=capture.id, jpeg=capture.jpeg,
                                      polygons=[polygon])
            return {"detail": f"{len(reply.get('regions') or [])} parts found inside the box"}

        return await self._worker_run(gpu, "edgetam", go)

    async def _track(self, _what: str) -> dict:
        follow = getattr(self.hub, "gpu_follow", None) or self._heavy()
        if follow is None:
            raise RuntimeError("no GPU worker here")

        async def go(client):
            # A session of its own, newer than any the worker has taken (gpu_worker/inbox.py refuses a step older than
            # the newest: the bench's first run of 2026-10-11 sent -time and was refused). Only ever on a follow worker
            # of the check's own (`before`: never inside the live app), whose later sessions it would shadow.
            capture, session = self.capture(), int(time.time())
            first = await client.call("track", timeout=120.0, priority="interactive", frame_id=capture.id, jpeg=capture.jpeg,
                                      start=[{"id": "check", "box": list(self.box)}], stop=[], session=session, seq=1,
                                      reset=True)
            await client.call("track", timeout=30.0, priority="interactive", frame_id=capture.id, jpeg=capture.jpeg,
                              start=[], stop=["check"], session=session, seq=2, reset=False)
            return {"detail": f"followed 1 outline ({len(first.get('objects') or [])} placed)"}

        return await self._worker_run(follow, "edgetam-video", go)

    async def _molmo(self, _what: str) -> dict:
        from .molmo import molmo_client

        gpu = getattr(self.hub, "gpu_molmo", None)
        if gpu is None:
            raise RuntimeError("no Molmo worker here")

        async def go(_client):
            client = await molmo_client(self.hub)  # loads it once (molmo.py), as pointing "molmo" does
            load = getattr(client, "molmo_load", None) or {}
            capture = self.capture()
            reply = await client.call("molmo_point", timeout=120.0, priority=PRIORITY, frame_id=capture.id,
                                      jpeg=capture.jpeg, query="Point to a dark circle." if self.public_example else "Point to the stoma.",
                                      crop=[0, 0, 96, 96] if self.public_example else [300, 0, 800, 720])
            facts = {"detail": f"{len(reply.get('points') or [])} points"}
            if load.get("loadMs") is not None:
                facts["loadMs"] = round(float(load["loadMs"]))
            return facts

        # Let go after unless it was loaded before (about 12 GB: never left behind by a check, loads.py unloads it too).
        return await self._worker_run(gpu, "molmo2-4b", go)

    async def _link(self, _what: str) -> dict:
        import numpy as np

        from .licences import adapter

        def work():
            labels = np.zeros((2, 96, 96), np.int32)
            labels[0, 20:40, 20:40], labels[1, 22:42, 23:43] = 1, 1
            labels[0, 60:80, 50:70], labels[1, 61:81, 52:72] = 2, 2
            views = (labels > 0).astype(np.float32) * 200.0 + 20.0
            module = adapter("trackastra")
            edges = module.link(views, labels)
            return {"detail": f"{len(edges)} links between two synthetic views"}

        return await asyncio.to_thread(work)

    # --- the cloud ----------------------------------------------------------------------------------------------------
    async def _vision(self, option: str) -> dict:
        from .options import VISION

        result = await self.agent.vision(option).ask([self.jpeg or frame()], SAY_PROMPT, max_output_tokens=200)
        model = VISION[option].spec.partition(":")[2].partition("@")[0]
        return _answered(result.data, getattr(result, "error", None), model, getattr(result, "usage", {}) or {})

    async def _text(self, what: str) -> dict:
        provider, _, model = what.partition(":")
        if provider == "anthropic":
            return await _anthropic(model, self.environ)
        if provider == "gemini":
            from .vision import make_vision

            result = await make_vision(f"gemini:{model}@low").ask([], TEXT_PROMPT, max_output_tokens=200)
        else:
            from .vision import make_vision

            result = await make_vision(f"openai:{model}@low").ask([], TEXT_PROMPT, max_output_tokens=200)
        return _answered(result.data, getattr(result, "error", None), model, getattr(result, "usage", {}) or {})

    async def _image(self, model: str) -> dict:
        from research_worker.budget import PRICES

        if model.startswith("gemini"):
            from google import genai
            from google.genai import types

            response = await genai.Client().aio.models.generate_content(
                model=model, contents="A small green circle on a white background.",
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
            parts = [p for c in (response.candidates or []) for p in (c.content.parts if c.content else [])]
            made = sum(1 for p in parts if getattr(p, "inline_data", None) is not None)
        else:
            import openai

            # As the research worker asks it (researcher._image_call): 1024 px square, medium quality.
            response = await openai.AsyncOpenAI().images.generate(model=model, prompt="A small green circle on a white "
                                                                  "background.", size="1024x1024", quality="medium")
            made = len(response.data or [])
        if not made:
            raise RuntimeError("it answered with no picture")
        price = PRICES.get(model) or {}
        # About 1120 picture tokens out (the S4 spike's medium pictures): an estimate, said as one.
        return {"detail": f"{made} picture made", "costUsd": round((20 * price.get("text_in", 5) + 1120 * price.get("image_out", 30)) / 1e6, 4)}

    async def _video(self, model: str) -> dict:
        from research_worker.researcher import VIDEO_MODEL

        from google import genai
        from google.genai import types

        client = genai.Client()
        operation = await client.aio.models.generate_videos(model=VIDEO_MODEL, prompt="A green leaf cell, still.",
                                                            config=types.GenerateVideosConfig(duration_seconds=4))
        for _ in range(60):
            if operation.done:
                break
            await asyncio.sleep(5)
            operation = await client.aio.operations.get(operation)
        if not operation.done:
            raise RuntimeError("no video after 5 minutes")
        videos = getattr(operation.response, "generated_videos", None) or []
        if not videos:
            raise RuntimeError("it answered with no video")
        return {"detail": "a 4 s video made", "costUsd": VIDEO_USD}

    async def _voice(self, model: str) -> dict:
        """GPT-Live hears a spoken request (the words synthesized as bench/gpt_live_spike.py does: macOS `say`, 16 kHz)
        and hands it to the app as a delegation, the app's answer going back, as the studio's voice does (voice/route.py:
        the app's voice prompt, the same session code). Typed words (send_text: trusted instructions) never made it
        delegate: the first cloud check, 2026-10-11 08:31, waited 30 s for one; GPT-Live takes no typed user turns."""
        from .voice.base import ToolSpec, VoiceConfig
        from .voice.openai_live import OpenAILiveSession

        pcm = self.voice_speech if self.voice_speech is not None else await asyncio.to_thread(speak, SPOKEN)
        delegated = asyncio.get_running_loop().create_future()

        async def handler(args: dict) -> dict:
            if not delegated.done():
                delegated.set_result(time.perf_counter())
            return {"answer": "The app outlined the stoma (a capability check)."}

        tool = ToolSpec("ask_app", "Hand the user's request to the app, which sees the microscope and acts.",
                        {"type": "object", "properties": {"question": {"type": "string"}}}, handler)
        session = OpenAILiveSession(VoiceConfig(model=model, system_prompt=self.voice_prompt or voice_prompt(),
                                                tools=[tool], input_rate=VOICE_RATE), connect=self.voice_connect)
        t0 = time.perf_counter()
        await session.start()
        try:
            chunk = VOICE_RATE * 2 * 40 // 1000  # 40 ms, sent as a microphone sends it
            audio = pcm + bytes(VOICE_RATE * 2 * 3)  # then 3 s of quiet: the end of the user's turn
            spoken_end = None
            for i in range(0, len(audio), chunk):
                if delegated.done():
                    break
                await session.send_audio(audio[i:i + chunk])
                if spoken_end is None and i + chunk >= len(pcm):
                    spoken_end = time.perf_counter()
                await asyncio.sleep(0.04)
            try:
                at = await asyncio.wait_for(delegated, 20.0)
            except asyncio.TimeoutError:
                raise RuntimeError("GPT-Live did not hand the spoken request to the app within 20 s of the words") from None
        finally:
            await session.close()
        after = at - (spoken_end or at)
        # No list price for GPT-Live here (budget.PRICES): estimated from the session's length at gpt-realtime's about
        # $0.06 a minute of audio (an estimate, said as one).
        return {"detail": f"handed over {after:.1f} s after the words ended", "ms": round(after * 1000),
                "costUsd": round((time.perf_counter() - t0) / 60 * 0.06, 4)}


SPOKEN = "Please outline the stoma on the slide."
VOICE_RATE = 16000  # the microphone's rate the studio sends (openai_live resamples it to 24 kHz)


def speak(text: str) -> bytes:
    """`text` spoken by macOS `say`, as 16 kHz mono 16-bit PCM (bench/live_probe.tts does the same)."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="micromind-voice-check-") as folder:
        aiff, pcm = Path(folder) / "said.aiff", Path(folder) / "said.pcm"
        try:
            subprocess.run(["say", "-v", "Samantha", "-o", str(aiff), text], check=True, capture_output=True, timeout=30)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", str(VOICE_RATE), "-ac", "1",
                            "-f", "s16le", str(pcm)], check=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"the request could not be spoken here (macOS say and ffmpeg): {exc}") from exc
        return pcm.read_bytes()


def voice_prompt() -> str:
    """The voice's instructions as the studio gives them (voice/route.voice_prompt), with every tool's family."""
    from .brain.menu import voice_abilities
    from .brain.toolbox import catalogue
    from .prompts import LIVE_VOICE_DELEGATE

    return LIVE_VOICE_DELEGATE.format(abilities=voice_abilities(list(catalogue().tools.values())))


async def _anthropic(model: str, environ=os.environ) -> dict:
    """Claude as the research worker asks it (researcher.write_lesson: the Messages API over httpx)."""
    import httpx

    from research_worker.budget import tokens_cost

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://api.anthropic.com/v1/messages", headers={
            "x-api-key": environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 50, "messages": [{"role": "user", "content": TEXT_PROMPT}]})
        if r.status_code >= 400:
            raise RuntimeError(f"Anthropic answered {r.status_code}: {r.text[:200]}")
        data = r.json()
    usage = data.get("usage") or {}
    text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
    if not text.strip():
        raise RuntimeError("it answered with no text")
    return {"detail": "answered", "costUsd": round(tokens_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0)), 6)}


def _answered(data, error, model: str, usage: dict) -> dict:
    from research_worker.budget import PRICES, tokens_cost

    if error:
        raise RuntimeError(str(error))
    if not isinstance(data, dict) or not data:
        raise RuntimeError("it answered with nothing the app could read")
    facts = {"detail": f"answered{(': ' + str(data.get('what'))[:40]) if data.get('what') else ''}"}
    if model in PRICES and usage:
        facts["costUsd"] = round(tokens_cost(model, usage.get("input") or 0, (usage.get("output") or 0) + (usage.get("thinking") or 0)), 6)
    return facts


def _made(operation: str, output: dict) -> str:
    """What one operation's run made, in words."""
    if operation == "cells":
        n = output.get("count")
        if n is None:
            n = len(output.get("regions") or output.get("polygons") or [])
        if (output.get("producer") or {}).get("maskContract") == "native-bright-regions-v1":
            return f"{n} bright regions detected; not biological cell identities"
        return f"{n} cells segmented"
    if operation == "find-like":
        return f"{len(output.get('matches') or [])} matches"
    n = len(output.get("polygons") or [])
    return f"{n} outline{'s' if n != 1 else ''} in the box" if n else "nothing outlined in the box (it ran)"


def no_room_words(refusal) -> str:
    """Why a model was not checked inside the live app (loads.NoRoom): the memory it needs, what is free, and what
    loading it would have pushed out."""
    from .loads import gb, model_name

    kept = [model_name(m) for m in refusal.kept]
    pushed = ", ".join(kept[:-1]) + f" and {kept[-1]}" if len(kept) > 1 else "".join(kept)
    taking = f"push out {pushed}, which the live view keeps loaded" if kept else "take memory the live view is using"
    return (f"inside the live app it needs {gb(refusal.need_mb)} of memory and {gb(refusal.free_mb)} is free: loading "
            f"it would {taking}; make capability-check checks it with the app stopped")


def _words(exc: BaseException) -> str:
    """An error in its own words, short: "RuntimeError: out of memory" as "out of memory"."""
    text = str(exc).strip() or type(exc).__name__
    return " ".join(text.split())[:300]


def chosen(scope: str) -> list[Entry]:
    """The entries a check covers: "local" (what runs on this Mac), "cloud" (what runs in the cloud), "all"."""
    if scope == "all":
        return entries()
    return [e for e in entries() if (e.where == "mac") == (scope == "local")]


def summary(lines: list[dict]) -> dict:
    """How many of each state, and the broken ones by name."""
    counts: dict[str, int] = {}
    for line in lines:
        counts[line["state"]] = counts.get(line["state"], 0) + 1
    return {"counts": counts, "broken": [f"{l['name']}: {l.get('why')}" for l in lines if l["state"] == "broken"]}


__all__ = ["Checker", "Checks", "Entry", "STATES", "before", "broken", "chosen", "entries", "find", "frame", "refusal",
           "said", "summary", "wire"]
