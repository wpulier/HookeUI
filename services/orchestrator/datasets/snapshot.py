"""Save the session as a dataset, on the user's press, without stopping it (the UX document §5,
docs/design/2026-10-07-the-partner.md §2.5; ADR 0008: only the user makes a dataset).

The Datasets tab asks for a preview of a window (this conversation, or today so far): what a dataset of it
would hold, what it would miss, what is still being worked on and where it would go. The user presses
Save, and the window's records, pictures, snapshots and researchers' work are frozen as they are at that
moment into a new dataset folder, with the camera recordings that cover any of it (linked, with their
sidecars). The session carries on: work after the press belongs to the session, or to a later snapshot.
Nothing is uploaded or analysed.

It reads only what is kept anyway (the day journals, the interaction and vote logs, snapshots,
researchers' folders, the bridge's recordings and the microscope facts), never the optional execution
log, so it is the same with that off (the partner §1f).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from ..paths import Paths
from . import manifest
from .check import problems
from .importer import days_between, gather
from .rig import FIELDS, NAME as RIG_NAME
from .writer import link_or_copy

SCOPES = {"conversation": "this conversation", "today": "today so far"}
TAIL_BYTES = 65536  # enough of a sidecar's end to find its last frame and whether it was saved
RESEARCH_ACTIVE = ("queued", "starting", "running", "revising")


def window(day, scope: str, now: float) -> tuple[float, float]:
    """The window a scope means, ending now: the active conversation from its start, or today from its
    first conversation's start. `day` is the SessionStore."""
    if scope not in SCOPES:
        raise ValueError(f"scope is one of {', '.join(SCOPES)}, not {scope!r}")
    if scope == "conversation":
        start = day.active.started
    else:
        start = min(t.started for t in day.threads.values())
    return float(min(start, now)), float(now)


def research_status(paths: Paths):
    """How a researcher stands now, read from its own folder (its status as it last wrote it)."""
    def status(day: str, rid: str) -> str | None:
        data = _research(paths, day, rid)
        return str(data.get("status")) if data.get("status") else None
    return status


def _research(paths: Paths, day: str, rid: str) -> dict:
    try:
        data = json.loads((paths.research / day / rid / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _names(paths: Paths, got) -> dict[str, str | None]:
    """Each researcher by the name the user sees on the shelf ("Researcher 3"), not its id."""
    days = {rid: day for day, rid in got.linked}
    return {r["rid"]: _research(paths, days.get(r["rid"], ""), r["rid"]).get("name") for r in got.research}


def recordings(paths: Paths, start: float, stop: float) -> list[dict]:
    """The bridge's camera recordings that cover any of start..stop, from their sidecars (docs/datasets.md):
    the video and sidecar paths, the span they cover, their bridge run, and whether the file is finished."""
    found = []
    folder = paths.recordings
    if not folder.is_dir():
        return found
    for sidecar in sorted(folder.glob("*.frames.jsonl")):
        video = sidecar.with_name(sidecar.name[: -len(".frames.jsonl")] + ".mp4")
        span = sidecar_span(sidecar)
        if span is None or not video.is_file():
            continue
        header, first, last, saved = span
        if last < start or first > stop:
            continue
        found.append({"video": video, "sidecar": sidecar, "from": first, "to": last, "boot": header.get("bootId"),
                      "owner": header.get("owner"), "finished": saved})
    return found


def sidecar_span(sidecar: Path) -> tuple[dict, float, float, bool] | None:
    """A recording's span from its sidecar (docs/datasets.md): its header, when it started, its last frame's time
    (its start when it has none yet) and whether the bridge says it was saved; None when it can't be read or says
    no start. Only the header and the file's end are read."""
    try:
        with sidecar.open("rb") as f:
            header = json.loads(f.readline() or b"{}")
            size = sidecar.stat().st_size
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace").splitlines()
    except (OSError, ValueError):
        return None
    first = header.get("startedAtEpoch") if isinstance(header, dict) else None
    if not isinstance(first, (int, float)):
        return None
    last, saved = None, False
    for raw in reversed(tail):
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(line, dict):
            continue
        saved = saved or line.get("type") == "saved"
        if last is None and "bridgeAt" in line:
            last = float(line["bridgeAt"])
        if last is not None and saved:
            break
    return header, float(first), last if last is not None else float(first), saved


# A population's files, and motion tracks' (orchestrator/motiontracks.py: their points and their captured view).
EVIDENCE_FILES = ("manifest.json", "capture.jpg", "source.image", "masks.npz", "tracks.npz", "tracks.jpg")


def _kind_of(eid: str, schema=None) -> str:
    """"tracks" for motion tracks (schema tracks/1, or an id m<n> not on disk yet), else "population"."""
    from ..motiontracks import SCHEMA as TRACKS

    if schema is not None:
        return "tracks" if schema == TRACKS else "population"
    return "tracks" if eid[:1] == "m" and eid[1:].isdigit() else "population"


def what_it_is(row: dict) -> str:
    """An evidence row in the user's words: motion tracks, or a set of outlines."""
    return f"Motion tracks ({row['id']})" if row.get("kind") == "tracks" else f"A set of outlines ({row['id']})"


def evidence_in(paths: Paths, start: float, stop: float, journaled: set[str] | frozenset = frozenset()) -> list[dict]:
    """The populations of the window (orchestrator/evidence.py) and its motion tracks (orchestrator/motiontracks.py,
    kind "tracks", schema tracks/1, each checked against its own files): made or revised inside it, or journaled
    as made or saved inside it (`journaled`: their ids from the day journal's evidence-made, evidence-saved,
    tracks-made and tracks-saved events).
    Each is checked as the app checks evidence it reads back: its manifest, its schema and every file against
    its checksum. One that passes is "whole"; one that does not, or has no manifest yet (still being saved at
    the press, or its save stopped), is "incomplete", with why, never listed as whole and never left out."""
    from ..evidence import SCHEMA
    from ..motiontracks import SCHEMA as TRACKS

    found, seen = [], set()
    root = getattr(paths, "evidence", None)
    for day in days_between(start, stop) if root is not None else []:
        folder = root / day
        for entry in sorted(folder.iterdir()) if folder.is_dir() else []:
            if not entry.is_dir():
                continue
            row = {"folder": entry, "day": day, "id": entry.name, "kind": _kind_of(entry.name), "revision": 0, "madeAt": None,
                   "title": None, "operation": None, "implementation": None, "members": 0, "tracks": 0, "status": "whole",
                   "why": None}
            try:
                made = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                if entry.name not in journaled:
                    continue
                found.append({**row, "status": "incomplete",
                              "why": "it has no manifest: it was still being saved at the press, or its save stopped"})
                seen.add(entry.name)
                continue
            times = [made.get("madeAt")] + [r.get("at") for r in made.get("revisions") or [] if isinstance(r, dict)]
            inside = any(isinstance(t, (int, float)) and start <= t <= stop for t in times)
            if not inside and entry.name not in journaled:
                continue
            row.update(id=str(made.get("id") or entry.name), kind=_kind_of(entry.name, made.get("schema")),
                       revision=made.get("revision") or 0,
                       madeAt=made.get("madeAt"), title=made.get("title"), operation=made.get("operation"),
                       implementation=made.get("implementation"),
                       members=sum(1 for m in made.get("members") or [] if (m or {}).get("status") not in ("deleted", "merged")),
                       tracks=len(made.get("tracks") or []) if made.get("schema") == TRACKS else 0)
            problem = None
            if made.get("schema") not in (SCHEMA, TRACKS):  # a population's, or motion tracks' (each checked by its own files)
                problem = f"its manifest's schema is {made.get('schema')!r}, not {SCHEMA} or {TRACKS}"
            for name, digest in (made.get("files") or {}).items():
                try:
                    ok = hashlib.sha256((entry / name).read_bytes()).hexdigest() == digest
                except OSError:
                    ok = False
                if not ok:
                    problem = problem or f"{name} does not match its manifest (a later save stopped part way)"
            if problem:
                row.update(status="incomplete", why=problem)
            found.append(row)
            seen.add(row["id"])
    for eid in sorted(set(journaled) - seen):  # made in the window, nothing on disk yet
        found.append({"folder": None, "day": None, "id": eid, "kind": _kind_of(eid), "revision": 0, "madeAt": None, "title": None,
                      "operation": None, "implementation": None, "members": 0, "tracks": 0, "status": "incomplete",
                      "why": "it was not saved yet at the press: save the session again to include it"})
    return found


def _journaled_evidence(events: list[dict]) -> set[str]:
    """The populations and motion tracks the day journal says were made or saved inside the window."""
    return {str(e["record"].get("id")) for e in events if e.get("stream") == "journal"
            and e["record"].get("type") in ("evidence-made", "evidence-saved", "tracks-made", "tracks-saved")
            and e["record"].get("id")}


def collected(paths: Paths, start: float, stop: float) -> list[dict]:
    """Datasets Record collected that overlap the window: they hold the per-frame facts for their part."""
    out = []
    for folder, summary in manifest.all_in(paths.datasets):
        if summary.get("reconstructed"):
            continue
        a, b = summary.get("startedAtEpoch"), summary.get("stoppedAtEpoch") or time.time()
        if isinstance(a, (int, float)) and a <= stop and b >= start:
            out.append({"id": folder.name, "name": summary.get("name") or "", "from": float(a), "to": float(b)})
    return out


def rig_facts(paths: Paths, start: float, stop: float) -> tuple[dict, list[dict]]:
    """The microscope facts the user entered (rig.json): those entered before the window are where it
    starts, those entered inside it are entered after its start (never written into its past)."""
    try:
        data = json.loads((paths.datasets / RIG_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, []
    at_start, inside = {}, []
    for field, fact in (data.get("facts") or {}).items():
        if field not in FIELDS or not isinstance(fact, dict):
            continue
        at = fact.get("at") or 0
        if at <= start:
            at_start[field] = {**fact, "carriedOver": True}
        elif at <= stop:
            inside.append({"field": field, **fact, "enteredAfter": True})
    return at_start, inside


def preview(paths: Paths, start: float, stop: float, scope: str = "") -> dict:
    """What a snapshot of start..stop would hold and miss, without writing anything: the review the
    user sees before Save (the UX document §5)."""
    got = gather(paths, start, stop, None, votes=True, research_status=research_status(paths))
    streams: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for e in got.events:
        streams[e["stream"]] = streams.get(e["stream"], 0) + 1
        if e["stream"] == "interaction":
            kind = str(e["record"].get("kind") or "other")
            kinds[kind] = kinds.get(kind, 0) + 1
    turns = sum(1 for e in got.events if e["stream"] == "journal" and e["record"].get("type") == "turn")
    snapshots = _snapshots_in(paths, start, stop)
    evidence = evidence_in(paths, start, stop, _journaled_evidence(got.events))
    whole = [e for e in evidence if e["status"] == "whole" and e["kind"] != "tracks"]
    whole_tracks = [e for e in evidence if e["status"] == "whole" and e["kind"] == "tracks"]
    torn = [e for e in evidence if e["status"] != "whole"]
    cams = recordings(paths, start, stop)
    datasets = collected(paths, start, stop)
    at_start, entered = rig_facts(paths, start, stop)
    pending = [r for r in got.research if r.get("status") in RESEARCH_ACTIVE]
    names = _names(paths, got)

    missing: list[str] = []
    if datasets:
        spans = ", ".join(f"{_clock(d['from'])}-{_clock(d['to'])} (dataset {d['id']})" for d in datasets)
        missing.append(f"Per-frame measurements are kept only while Record is on; Record covered {spans}, "
                       "and those datasets hold them.")
    else:
        missing.append("Per-frame measurements are kept only while Record is on, and Record was not on in this window.")
    covered = sum(max(0.0, min(c["to"], stop) - max(c["from"], start)) for c in cams)
    if not cams:
        missing.append("No camera recording covers this window, so it holds no video of the specimen.")
    elif covered < (stop - start) * 0.98:
        missing.append(f"Camera video covers {_duration(covered)} of the {_duration(stop - start)}.")
    unfinished = [c for c in cams if not c["finished"]]
    if unfinished:
        missing.append(f"{len(unfinished)} camera recording{'s are' if len(unfinished) > 1 else ' is'} still being "
                       "written; it is saved as it is now, and its sidecar says which frames are in.")
    scale = [p for p in got.scale_prefs if p["at"] <= start]
    if not (scale and (scale[-1].get("value") or {}).get("objective")):
        missing.append("The objective was not set at the start of the window, so sizes there are in pixels.")
    if not at_start and not entered:
        missing.append("Nothing about the microscope was entered (the Datasets tab's \"Your microscope\").")
    for e in torn:
        missing.append(f"{what_it_is(e)} {'are' if e['kind'] == 'tracks' else 'is'} not saved whole: {e['why']}. "
                       f"{'They are' if e['kind'] == 'tracks' else 'It is'} kept, marked incomplete.")
    missing.append("Nothing about the sample (what it is, how it was prepared, when it was collected) is kept "
                   "unless it was said in the conversation.")

    return {
        "scope": scope, "scopeText": SCOPES.get(scope, "the window"),
        "from": round(start, 3), "to": round(stop, 3), "fromText": manifest.iso(start), "toText": manifest.iso(stop),
        "holds": {"records": len(got.events), "turns": turns, "streams": streams, "interactions": kinds,
                  "pictures": got.pictures, "snapshots": snapshots, "evidence": len(whole),
                  "evidenceMembers": sum(e["members"] for e in whole),
                  "tracks": len(whole_tracks), "tracksCount": sum(e["tracks"] for e in whole_tracks),
                  "evidenceIncomplete": [{"id": e["id"], "why": e["why"]} for e in torn],
                  "researchers": [{"rid": r["rid"], "name": names.get(r["rid"]), "status": r.get("status"), "kept": r.get("kept")}
                                  for r in got.research],
                  "cameraRecordings": [{"from": c["from"], "to": c["to"], "finished": c["finished"], "owner": c["owner"]} for c in cams],
                  "cameraSeconds": round(covered, 1), "collected": datasets,
                  "microscope": sorted(at_start) + sorted({e["field"] for e in entered})},
        "missing": missing,
        "pending": [{"rid": r["rid"], "name": names.get(r["rid"]), "status": r.get("status")} for r in pending],
        "destination": str(paths.datasets / next_id(paths, start)),
        "staysHere": "Saved on this Mac only. Nothing is uploaded or analysed, and the session carries on.",
    }


def save(paths: Paths, start: float, stop: float, *, scope: str = "", name: str = "", clock=time.time,
         build: str | None = None) -> Path:
    """Write the snapshot: a new dataset folder, never over another. Returns its folder."""
    if stop <= start:
        raise ValueError("the window must end after it starts")
    root = paths.datasets
    root.mkdir(parents=True, exist_ok=True)
    # Written into a hidden folder and renamed into place only once it passes the check, so a save that
    # fails part way never leaves a "complete" dataset among the user's (ADR 0006); listings skip it.
    dataset_id = next_id(paths, start)
    folder = root / f".{dataset_id}.saving-{os.getpid()}"
    folder.mkdir()
    try:
        return _write(paths, folder, dataset_id, start, stop, scope=scope, name=name, clock=clock, build=build)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _write(paths: Paths, folder: Path, dataset_id: str, start: float, stop: float, *, scope: str, name: str, clock,
           build: str | None) -> Path:
    root = paths.datasets
    now = max(clock(), stop)  # saved at the press, which is never before the window it saves ends
    got = gather(paths, start, stop, folder, votes=True, research_status=research_status(paths))
    events = sorted(got.events, key=lambda e: e["t"])

    parts: list[dict] = []
    for n, cam in enumerate(recordings(paths, start, stop), 1):
        video, sidecar = f"{manifest.VIDEO}/part-{n}.mp4", f"{manifest.VIDEO}/part-{n}.frames.jsonl"
        kept = link_or_copy(cam["video"], folder / video)
        part = {"n": n, "video": video, "sidecar": sidecar, "source": str(cam["video"]),
                "sourceSidecar": str(cam["sidecar"]), "boot": cam["boot"], "joined": True,
                "videoKept": kept, "finished": cam["finished"],
                "covers": {"from": manifest.iso(cam["from"]), "to": manifest.iso(cam["to"])}}
        if cam["finished"]:
            part["sidecarKept"] = link_or_copy(cam["sidecar"], folder / sidecar)
        else:
            # Still being written: the video stays linked (a half-written MP4 copied would not play), and
            # its sidecar is copied as it stood at the press, so the frames the bridge writes afterwards
            # are not in this snapshot: a frame without a sidecar line is not in it (docs/datasets.md).
            last = _sidecar_until(cam["sidecar"], folder / sidecar, now)
            part["sidecarKept"] = "copied-at-press"
            part["coversTo"] = last
            part["afterPress"] = "frames the video holds past the sidecar's last line were recorded after the press"
        parts.append(part)
        events.append({"t": round(now, 4), "run": 1, "stream": "dataset",
                       "record": {"event": "video-part", "n": n, "source": str(cam["video"]), "kept": kept,
                                  "finished": cam["finished"]}})

    kept_evidence = []
    for ev in evidence_in(paths, start, stop, _journaled_evidence(got.events)):
        files = []
        for file_name in EVIDENCE_FILES if ev["folder"] is not None else ():
            source = ev["folder"] / file_name
            if source.is_file():
                kept_as = f"{manifest.PICTURES}/evidence-{ev['day']}-{ev['id']}-r{ev['revision']}-{file_name}"
                files.append({"file": kept_as, "kept": link_or_copy(source, folder / kept_as)})
        at = ev["madeAt"]
        kept_evidence.append({"id": ev["id"], "day": ev["day"], "kind": ev["kind"], "revision": ev["revision"], "status": ev["status"],
                              **({"why": ev["why"]} if ev["why"] else {}),
                              "madeAt": manifest.iso(at) if at else None, "madeAtEpoch": round(at, 3) if at else None,
                              "title": ev["title"], "operation": ev["operation"], "implementation": ev["implementation"],
                              "members": ev["members"], **({"tracks": ev["tracks"]} if ev["kind"] == "tracks" else {}),
                              "files": files})

    pending = [r for r in got.research if r.get("status") in RESEARCH_ACTIVE]
    for r in got.research:
        r["pending"] = r in pending
    datasets = collected(paths, start, stop)
    events.append({"t": round(now, 4), "run": 1, "stream": "dataset",
                   "record": {"event": "snapshot", "scope": scope, "from": manifest.iso(start), "to": manifest.iso(stop),
                              "pending": [r["rid"] for r in pending]}})
    with (folder / manifest.EVENTS).open("w", encoding="utf-8") as f:
        for line in events:
            f.write(json.dumps(line, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")

    # The microscope as it was at the window's start: the objective and field number the scale had then
    # (the last setting before it), and the facts the user had entered; changes inside are listed apart.
    rig: dict[str, dict] = {field: {"value": None, "source": "unknown"} for field in FIELDS}
    at_start, entered = rig_facts(paths, start, stop)
    rig.update(at_start)
    rig["objective"] = {"value": None, "source": "unknown", "note": "no objective was set before the start"}
    before = [p for p in got.scale_prefs if p["at"] <= start]
    value = (before[-1].get("value") or {}) if before else {}
    if value.get("objective"):
        rig["objective"] = {"value": value["objective"], "source": "user", "at": before[-1]["at"]}
    if value.get("fieldNumber") is not None and "fieldNumber" not in at_start:
        rig["fieldNumber"] = {"value": value["fieldNumber"], "source": "user" if got.field_number_given else "default"}
    rig_changes = [{"at": manifest.iso(p["at"]), "atEpoch": round(p["at"], 3), "objective": (p.get("value") or {}).get("objective"),
                    "fieldNumber": (p.get("value") or {}).get("fieldNumber"), "source": "user"}
                   for p in got.scale_prefs if start < p["at"] <= stop]

    summary = {
        "schema": manifest.SCHEMA, "kind": "microscopemind-dataset", "id": dataset_id, "name": name.strip()[:120],
        "status": "complete", "startedAt": manifest.iso(start), "startedAtEpoch": round(start, 3),
        "stoppedAt": manifest.iso(stop), "stoppedAtEpoch": round(stop, 3), "endedBy": "snapshot",
        "reconstructed": True,
        "edges": "A snapshot of the session, saved when the user pressed Save; the session carried on after it.",
        "format": "docs/datasets.md",
        "snapshot": {"at": manifest.iso(now), "atEpoch": round(now, 3), "scope": scope, "sessionContinues": True,
                     "pending": [{"rid": r["rid"], "status": r.get("status")} for r in pending],
                     "pendingNote": "researchers still working when it was saved are kept as they were then",
                     "collected": datasets, "uploaded": False},
        "reconstruction": {"at": manifest.iso(now), "importerBuild": build, "read": got.read,
                           "notCaptured": _not_captured(datasets, parts)
                           + [f"evidence {e['id']}: not saved whole ({e['why']}); kept and marked incomplete in evidence[]"
                              for e in kept_evidence if e["status"] != "whole"],
                           "researchers": got.research},
        "runs": [{"n": 1, "startedAt": manifest.iso(start), "startedAtEpoch": round(start, 3), "endedAtEpoch": round(stop, 3)}],
        "gaps": [], "app": {"build": build, "source": "the build running when it was saved"},
        "video": {"parts": parts}, "screen": [], "evidence": kept_evidence,
        "sourceLogs": [{"when": "snapshot", "at": manifest.iso(now), "atEpoch": round(now, 3), "files": got.read}],
        "rig": rig, "rigChanges": rig_changes, "rigEnteredAfter": entered, "sample": {},
        "counts": {"events": len(events), "frames": 0, "pictures": got.pictures},
    }
    try:  # what ran in it (made_with.py): who opens it elsewhere is offered what made it
        from ..made_with import of_dataset
        from ..result_entries import carry_dataset

        summary["madeWith"] = carry_dataset(folder, of_dataset(folder))
    except Exception:  # noqa: BLE001 - it is saved whatever happens to this
        pass
    manifest.write(folder, summary)
    found = problems(folder, name=dataset_id)
    if found:
        raise ValueError(f"{dataset_id} does not pass the dataset check: " + "; ".join(found))
    final = root / dataset_id
    if final.exists():  # another save took the name meanwhile: never written over
        raise ValueError(f"{dataset_id} was taken while this one was saved; save again")
    os.rename(folder, final)
    return final


def _sidecar_until(source: Path, target: Path, at: float) -> dict | None:
    """Copy a sidecar still being written as it stood at `at`: its header, and its frames and events up
    to then (a line half-written at that moment is left out). Returns the last frame kept: its time,
    `seq` and index."""
    target.parent.mkdir(parents=True, exist_ok=True)
    last = None
    with source.open("r", encoding="utf-8", errors="replace") as src, target.open("w", encoding="utf-8") as out:
        for raw in src:
            if not raw.endswith("\n"):
                break  # the line being written right now
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            when = line.get("bridgeAt", line.get("at"))
            if line.get("type") != "header" and isinstance(when, (int, float)) and when > at:
                break
            out.write(raw)
            if "bridgeAt" in line:
                last = {"at": manifest.iso(line["bridgeAt"]), "atEpoch": line["bridgeAt"], "seq": line.get("seq"),
                        "i": line.get("i")}
    return last


def next_id(paths: Paths, start: float) -> str:
    """ds-YYYYMMDD-HHMMSS by the window's start; a later snapshot of the same start is -2, -3... (a later
    version: a dataset is never written over)."""
    base = f"ds-{time.strftime('%Y%m%d-%H%M%S', time.localtime(start))}"
    candidate, n = base, 1
    while (paths.datasets / candidate).exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def _not_captured(datasets: list[dict], parts: list[dict]) -> list[str]:
    out = ["frames.jsonl: the app keeps per-frame facts only while Record is on"
           + (f" (the datasets {', '.join(d['id'] for d in datasets)} hold them for their part)" if datasets else "")]
    if not parts:
        out.append("video: no camera recording covers the window")
    out.append("screen: the studio's own screen recordings are not included (Record keeps them in its datasets)")
    return out


def _snapshots_in(paths: Paths, start: float, stop: float) -> int:
    n = 0
    for day in days_between(start, stop):
        folder = paths.snapshots / day
        for meta in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                taken = float(json.loads(meta.read_text()).get("wallAt") or 0)
            except (OSError, ValueError):
                continue
            n += start <= taken <= stop
    return n


def _clock(epoch: float) -> str:
    return time.strftime("%H:%M", time.localtime(epoch))


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
