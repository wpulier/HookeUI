"""Made with: what a shared picture says made it (docs/design/2026-10-13-capabilities-for-everyone.md, C5; ComfyUI's lesson
"what you share carries what it needs").

A portable copy of a snapshot carries which catalog entries drew or found its outlines: a `tEXt` chunk
`ObservationUI-made-with`, JSON `{"format": 1, "capabilities": [{"key", "name", "version", "from", "link"}]}`. Only
entries are named (never a key, a path, a session or anything of the user's), only for outlines the picture shows, and
never for an outline of a blind comparison not yet voted on (its model stays unnamed until the vote).
Native result/snapshot bytes remain exact; their local sidecars supply identity for explicit portable downloads.

Opened on another Mac (a picture added to Assets, dropped on the stage), `offers` reads it back and says, for each, where
it stands there: on, one press away (its card's own step), not on this Mac, or not in this catalog at all (one of the
sharer's own: acquire its carried entry, or ask for its file). Nothing is turned on by reading it.
"""
from __future__ import annotations

import json
import io
import re
import struct
import zlib
from pathlib import Path
from typing import Iterable

from gpu_worker import manifests as catalog

from . import result_entries

CHUNK = b"ObservationUI-made-with"
FORMAT = 1
MAX_BYTES = 16 * 1024  # what is read back from a picture someone else made
SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _entry(impl: str) -> catalog.Manifest | None:
    """The catalog entry an implementation id runs, when it runs one (a reviewed model, one with its own adapter)."""
    if not impl:
        return None
    from . import operations

    key = operations.IMPL_MODEL.get(impl)  # a reviewed model, also under another id (EdgeTAM's automatic masks)
    if key is not None:
        return catalog.get(key)
    # One with its own adapter (env_runner.register): its implementation id is its entry's id, wherever it comes from.
    return catalog.get(impl) or next((m for m in catalog.mine().values() if m.id == impl), None)


def capture(m: catalog.Manifest, *, version: str | None = None, generation: str = "", carry: bool = True, memory=None) -> dict:
    """Capture catalog identity when this manifest actually runs; readers never call this to reconstruct history."""
    link = (m.credit or {}).get("link") or m.source.get("url") or ""
    acquisition = None
    if m.channel:
        channel = next((c for c in catalog.channels() if c["name"] == m.channel), {})
        # The installed snapshot folder retains the generation's channel commit even after a channel update.
        folder_parts = Path(m.folder or "").parts
        commit = next((p for p in folder_parts if re.fullmatch(r"[0-9a-f]{40}", p)), "")
        if not commit:
            from . import envs
            current = catalog.get(m.key)
            if current is not None and envs.entry_digest(m) == envs.entry_digest(current):
                commit = channel.get("commit", "")
        acquisition = _acquire({"url": channel.get("url"), "commit": commit, "entry": m.id})
    if acquisition is None and m.runner == "python-env":
        from . import publish_api
        acquisition = _acquire(publish_api.community_reference(m))
    portable, why = None, ""
    if carry and acquisition is None and m.runner == "python-env" and (m.namespace == "mine" or m.channel):
        from .share import CantShare
        try:
            if memory is None:
                raise ValueError("no shared memory admission owner")
            from . import env_runner

            installed = env_runner.in_use(m)  # its generation's proof travels with it, as Share a file carries it
            portable = result_entries.retain(m, memory, proof=installed.proof if installed is not None else None)
        except (CantShare, ValueError, RuntimeError) as exc:
            why = f"Capability sharing unavailable: {exc}"[:200]
        except OSError:
            why = "Capability sharing unavailable: its retained entry could not be read or saved."
    return {"key": m.key, "name": m.name, "version": str(version if version is not None else m.version),
            "from": "yours" if m.namespace == "mine" else (m.channel or "reviewed"),
            **({"acquire": acquisition} if acquisition else {}),
            **({"portable": portable} if portable else {}),
            **({"shareWhy": why} if why else {}),
            **({"generation": str(generation)} if generation else {}),
            **({"link": link} if str(link).startswith("https://") else {})}


def capture_implementation(implementation: str, *, version: str | None = None) -> dict | None:
    """Capture a fixed runner's catalog identity when it executes (not when history is read)."""
    m = _entry(implementation)
    return capture(m, version=version) if m is not None else None


def _historic(implementation: str, version=None, producer=None) -> dict | None:
    producer = producer if isinstance(producer, dict) else {}
    retained = producer.get("catalog")
    if isinstance(retained, dict):
        valid = _valid({"format": FORMAT, "capabilities": [retained]})
        if valid:
            return valid[0]
    m = _entry(implementation)
    if m is not None:
        # Legacy records can still be linked to a catalog card, but its version is not historical evidence.
        named = {k: v for k, v in capture(m, carry=False).items() if k not in ("version", "acquire")}
    elif producer.get("generation") or version or producer.get("version"):
        named = {"key": implementation, "name": implementation, "from": "unknown"}
    else:
        return None
    known_version = version or producer.get("version")
    if known_version:
        named["version"] = str(known_version)
    if producer.get("generation"):
        named["generation"] = str(producer["generation"])
    return named


def _keep(out: dict, named: dict | None) -> None:
    if named:
        out.setdefault((named["key"], named.get("version"), named.get("generation")), named)


def of_executions(hub, ids: Iterable[str]) -> list[dict]:
    """Every actual retained producer/version/generation, including entries removed since the run."""
    get = getattr(getattr(hub, "runner", None), "get", None)
    out = {}
    for eid in ids or ():
        x = get(eid) if get is not None else None
        if x is None or x.executed is None or (x.trial is not None and not x.revealed):
            continue
        _keep(out, _historic(str(x.executed), x.version, x.producer))
    return list(out.values())


def of_objects(objects: Iterable, hub=None) -> list[dict]:
    """Drawn and followed by the retained execution or producer stamp, never today's installed version."""
    get = getattr(getattr(hub, "runner", None), "get", None)
    out = {}
    for obj in objects:
        if getattr(obj, "trial", None):
            continue
        for stamp in (getattr(obj, "made_by", None), getattr(obj, "followed_by", None)):
            if not isinstance(stamp, dict):
                continue
            x = get(stamp.get("execution")) if get else None
            if x is not None:
                for named in of_executions(hub, [x.id]):
                    _keep(out, named)
            elif stamp.get("implementation"):
                _keep(out, _historic(str(stamp["implementation"]), stamp.get("version"), stamp.get("producer")))
    return list(out.values())


def of_dataset(folder: Path, most_bytes: int = 512 * 1024 * 1024) -> list[dict]:
    """Retained execution/reveal lifecycle in a recording or saved session; bounded, with no current-version guess."""
    records, revealed, out = {}, set(), {}
    read = 0
    try:
        with (Path(folder) / "events.jsonl").open("rb") as stream:
            while read < most_bytes:
                line = stream.readline(most_bytes - read + 1)
                if not line or read + len(line) > most_bytes:
                    break
                read += len(line)
                if b'"execution' not in line and b'"object-follow"' not in line:
                    continue
                try:
                    record = json.loads(line).get("record") or {}
                    kind = record.get("kind")
                except (ValueError, AttributeError):
                    continue
                if kind == "object-follow":
                    if record.get("producer") and record.get("implementation"):
                        _keep(out, _historic(str(record["implementation"]), producer=record["producer"]))
                    continue
                if kind not in ("execution", "execution-revealed"):
                    continue
                eid = str(record.get("id") or read)
                if kind == "execution-revealed":
                    revealed.add(eid)
                if record.get("executed") and record["executed"] != "arm":
                    # A reveal contains the full execution; an earlier redacted event cannot replace it.
                    records[eid] = record
        for eid, record in records.items():
            if record.get("trial") and eid not in revealed:
                continue
            _keep(out, _historic(str(record["executed"]), record.get("version"), record.get("producer")))
    except OSError:
        return []
    return list(out.values())


def embed(png: bytes, made: list[dict], *, portable: bool = False) -> bytes:
    """The PNG with its Made with chunk after its header (unchanged when nothing is named)."""
    if not made or not png.startswith(SIGNATURE):
        return png
    if not portable:
        made = [{k: v for k, v in named.items() if k != "portable"} for named in made]
    data = CHUNK + b"\0" + json.dumps({"format": FORMAT, "capabilities": made}, separators=(",", ":")).encode("latin-1",
                                                                                                         "replace")
    if len(data) > MAX_BYTES:
        raise ValueError("the picture's capability identities exceed the metadata size limit")
    chunk = struct.pack(">I", len(data)) + b"tEXt" + data + struct.pack(">I", zlib.crc32(b"tEXt" + data) & 0xFFFFFFFF)
    (n,) = struct.unpack(">I", png[8:12])
    after_header = 8 + 12 + n  # signature, then IHDR (length, type, data, crc)
    return png[:after_header] + chunk + (result_entries.chunks(made) if portable else b"") + png[after_header:]


JPEG_TAG = b"ObservationUI-made-with:"


def embed_jpeg(jpeg: bytes, made: list[dict], *, portable: bool = False) -> bytes:
    """A JPEG with its Made with in a comment segment (COM) after its start (unchanged when nothing is named)."""
    if not made or not jpeg.startswith(b"\xff\xd8"):
        return jpeg
    if not portable:
        made = [{k: v for k, v in named.items() if k != "portable"} for named in made]
    body = JPEG_TAG + json.dumps({"format": FORMAT, "capabilities": made}, separators=(",", ":")).encode()
    if len(body) > MAX_BYTES:
        raise ValueError("the picture's capability identities exceed the metadata size limit")
    return (jpeg[:2] + b"\xff\xfe" + struct.pack(">H", len(body) + 2) + body
            + (result_entries.chunks(made, jpeg=True) if portable else b"") + jpeg[2:])


def _read_jpeg(f) -> list[dict]:
    for _ in range(64):  # its segments before the picture: never a whole file
        marker = f.read(2)
        if len(marker) < 2 or marker[0] != 0xFF or marker[1] in (0xDA, 0xD9):
            return []
        (n,) = struct.unpack(">H", f.read(2))
        data = f.read(n - 2)
        if marker[1] == 0xFE and data.startswith(JPEG_TAG) and len(data) <= MAX_BYTES:
            return _valid(json.loads(data[len(JPEG_TAG):].decode("utf-8")))
    return []


def read(path: Path | bytes) -> list[dict]:
    """Identity from a picture file or already admitted bytes; [] when absent or unreadable, never guessed."""
    try:
        with (io.BytesIO(path) if isinstance(path, bytes) else Path(path).open("rb")) as f:
            head = f.read(8)
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                return _read_jpeg(f)
            if head != SIGNATURE:
                return []
            for _ in range(64):  # the chunk sits right after the header; never walk a whole file
                head = f.read(8)
                if len(head) < 8:
                    return []
                n, kind = struct.unpack(">I", head[:4])[0], head[4:]
                if kind == b"IDAT" or kind == b"IEND":
                    return []
                if kind == b"tEXt" and n <= MAX_BYTES:
                    data = f.read(n)
                    f.read(4)
                    key, _, text = data.partition(b"\0")
                    if key == CHUNK:
                        return _valid(json.loads(text.decode("latin-1")))
                    continue
                f.seek(n + 4, 1)
    except (OSError, ValueError, struct.error):
        return []
    return []


def _acquire(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    url, commit, entry = (str(value.get(k) or "") for k in ("url", "commit", "entry"))
    if (not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url)
            or not re.fullmatch(r"[0-9a-f]{40}", commit) or not catalog.TAG.fullmatch(entry)):
        return None
    return {"url": url, "commit": commit, "entry": entry}


def _valid(got: dict) -> list[dict]:
    if not isinstance(got, dict) or got.get("format") != FORMAT:
        return []
    out = []
    for c in got.get("capabilities") or []:
        if isinstance(c, dict) and isinstance(c.get("key"), str) and isinstance(c.get("name"), str):
            named = {k: str(c[k])[:200] for k in ("key", "name", "version", "from", "generation", "link", "shareWhy") if c.get(k)}
            acquire = _acquire(c.get("acquire"))
            if acquire:
                named["acquire"] = acquire
            portable = result_entries.descriptor(c.get("portable"))
            if portable:
                named["portable"] = portable
            out.append(named)
    return out[:20]


def of_asset(path: Path, *, include_local: bool = False) -> list[dict]:
    """What an asset says made it: a picture's own Made with (PNG or JPEG), or a recording's dataset summary
    (dataset.json `madeWith`, beside it or a folder up). [] when it says nothing: nothing is guessed."""
    path = Path(path)
    if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
        embedded = read(path)
        if embedded:
            return embedded
        # Native evidence stays byte-identical. Its local sidecar supplies identity for explicit portable export.
        stem = re.sub(r"^(d\d+)-\d+$", r"\1", path.stem)
        folder = path.parent
        if folder.name == "clean" and stem.startswith("snapshot-"):
            folder = folder.parent  # the snapshot's original and its clean analysis copy share one named sidecar
        try:
            with (folder / (stem + ".json")).open("rb") as stream:
                raw = stream.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                return []
            metadata = json.loads(raw)
            made = _valid({"format": FORMAT, "capabilities": metadata.get("madeWith") or []})
            # Native originals do not contain entry payloads. Only the local export worker may use their cache refs.
            return made if include_local else [{k: v for k, v in c.items() if k != "portable"} for c in made]
        except (OSError, ValueError, AttributeError):
            return []
    for folder in (path.parent, path.parent.parent):
        summary = folder / "dataset.json"
        if summary.is_file():
            try:
                return _valid({"format": FORMAT, "capabilities": json.loads(summary.read_text()).get("madeWith") or []})
            except (OSError, ValueError):
                return []
    return []


def offers(made: list[dict]) -> list[dict]:
    """Each capability a picture says made it, with where it stands on this Mac: {"key", "name", "state", "says",
    "model"} (state on | one-press | not-here | unknown). The card's own step turns it on; nothing here does."""
    from . import readiness

    out = []
    for c in made:
        key = c["key"]
        here = catalog.get(key) or catalog.mine().get(key) or (catalog.get(key.rsplit("/", 1)[-1])
                                                                if c.get("from") == "reviewed" else None)
        if here is None:
            acquire = _acquire(c.get("acquire"))
            if acquire:
                out.append({"key": key, "name": c["name"], "state": "acquire", "acquire": acquire,
                            "says": f"Get {c['name']} from {acquire['url']} at {acquire['commit'][:12]}. "
                                    "This adds its pinned community catalog; nothing runs until you allow its code and turn it on."})
                continue
            if result_entries.descriptor(c.get("portable")):
                out.append({"key": key, "name": c["name"], "state": "acquire",
                            "says": f"Get {c['name']} from this picture. It carries its producing entry, without weights. "
                                    "Review its card, allow its code, then turn it on."})
                continue
            whose = "one of the sharer's own" if c.get("from") == "yours" else f"from {c.get('from') or 'a catalog'}"
            out.append({"key": key, "name": c["name"], "state": "unknown",
                        "says": f"{c['name']} isn't in your catalog ({whose}): ask for its share file"
                                + (f", or see {c['link']}" if c.get("link") else "")
                                + (f". {c['shareWhy']}" if c.get("shareWhy") else "")})
            continue
        try:
            r = readiness.model(here.key, {})
        except KeyError:
            out.append({"key": key, "name": c["name"], "state": "unknown", "says": f"{c['name']} can't be read here"})
            continue
        if r.status == "ready":
            out.append({"key": key, "name": here.name, "state": "on", "model": here.key, "says": f"{here.name} is on"})
        elif r.status == "needs-setup" and r.steps:
            out.append({"key": key, "name": here.name, "state": "one-press", "model": here.key,
                        "says": f"{here.name} (not on yet): {r.steps[0].says}"})
        else:
            out.append({"key": key, "name": here.name, "state": "not-here", "model": here.key,
                        "says": f"{here.name} can't run on this Mac" + (f": {r.why}" if r.why else "")})
    return out


def acquire_reference(reference: dict) -> dict:
    """Fetch the immutable catalog only. Its normal trust and turn-on steps still gate all execution."""
    from . import channels
    got = _acquire(reference)
    if got is None:
        raise ValueError("this result has no valid pinned acquisition reference")
    existing = next((c for c in catalog.channels() if c.get("url") == got["url"] and c.get("commit") == got["commit"]), None)
    if existing:
        name = existing["name"]
    else:
        import hashlib
        name = "result-" + hashlib.sha256((got["url"] + got["commit"]).encode()).hexdigest()[:16]
        channels.add(got["url"], name, got["commit"])
    key = f"{name}/{got['entry']}"
    if catalog.get(key) is None:
        raise ValueError("the pinned catalog has no readable entry for this result")
    return {"model": key, "says": "Added its pinned community entry. Review its card, allow its code, then turn it on."}
