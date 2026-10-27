"""Portable capability bytes carried by a result, separate from its small Made with identity.

Only exact producer folders are retained. PNG private ancillary chunks and JPEG APP15 segments carry the ordinary
bounded share file, never weights. Reading identity imports nothing; explicit acquisition verifies its content hash
and then uses the existing untrusted share importer. All entry payloads in one picture together are bounded.
"""
from __future__ import annotations

import hashlib
import asyncio
import io
import json
from contextlib import contextmanager
import os
import re
import struct
import tempfile
import zlib
from pathlib import Path

from gpu_worker import manifests as catalog

from . import bounded_files, share

PNG = b"\x89PNG\r\n\x1a\n"
PNG_KIND = b"obUi"
JPEG_TAG = b"ObservationUI-entry\0"
MAX_BYTES = share.MAX_TOTAL + 64 * 1024  # ZIP directory/headers in addition to the bounded entry's files
MAX_SCAN = MAX_BYTES + 256 * 1024
PART = 60_000


def descriptor(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    digest, size = value.get("sha256"), value.get("bytes")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    if type(size) is not int or not 0 < size <= MAX_BYTES:
        return None
    return {"sha256": digest, "bytes": size}


def _root() -> Path:
    return share.root() / "results"


_RETAINED: dict[tuple, dict] = {}  # an entry folder as it was (its files' sizes and times) -> its retained pack


def _folder_state(manifest) -> tuple:
    folder = catalog.entry_folder(manifest)
    try:
        return (str(folder), tuple(sorted((e.name, e.stat(follow_symlinks=False).st_size,
                                           e.stat(follow_symlinks=False).st_mtime_ns) for e in os.scandir(folder))))
    except OSError:
        return (str(folder), None)


def retain(manifest, memory, proof: dict | None = None) -> dict:
    """Keep the exact producing entry now, so later edits/removal cannot change what a result carries: packed with its
    publisher and its installed generation's proof (`proof`), as Share a file packs it. Packed once per entry folder as
    it stands (Spec review of 64f49d1b, P3-4: every run re-zipped and hashed it); any change to its files packs it
    again, and a retained pack that has gone is made again."""
    _off_loop()
    key = (_folder_state(manifest), json.dumps(proof or {}, sort_keys=True, default=str))
    known = _RETAINED.get(key) if key[0][1] is not None else None
    if known is not None and (_root() / (known["sha256"] + share.SUFFIX)).is_file():
        return dict(known)
    with memory.reserve("analysis", 5 * share.MAX_TOTAL, "retaining a portable capability entry"):
        info = _retain(manifest, proof)
    if key[0][1] is not None:
        _RETAINED[key] = dict(info)
    return info


def _off_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise ValueError("portable entry processing must run off the live event loop")


def _retain(manifest, proof: dict | None = None) -> dict:
    data = share.pack(manifest, proof=proof, max_total=share.MAX_TOTAL)
    if len(data) > MAX_BYTES:
        raise share.CantShare("this entry is too large to carry inside a result")
    info = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    folder = _root()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (info["sha256"] + share.SUFFIX)
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=folder, delete=False) as stream:
            tmp = Path(stream.name)
            stream.write(data)
        tmp.replace(path)
    return info


@contextmanager
def picture(original: bytes, made: list[dict], memory):
    """A portable copy, only on a worker, admitted through its complete encoding/write lifetime.

    The caller keeps the native original separately. A refused or failed export raises a useful error; it never
    changes or replaces that original. No enlarged byte buffer survives the context's reservation.
    """
    _off_loop()
    from .made_with import embed, embed_jpeg, read
    if not made:
        raise ValueError("this picture has no retained capability identity to carry")
    unavailable = next((c.get("shareWhy") for c in made if c.get("shareWhy")), None)
    if unavailable:
        raise ValueError(str(unavailable))
    unique = {d["sha256"]: d["bytes"] for c in made if (d := descriptor(c.get("portable")))}
    total = sum(unique.values())
    if total > MAX_BYTES:
        raise ValueError("these capabilities exceed the 10 MiB portable-picture allowance; share their entry files separately")
    with memory.reserve("analysis", 6 * (len(original) + total) + 64 * 1024, "making a portable picture copy"):
        # A received portable picture is self-contained. Verify its carried bytes, then reuse the picture exactly;
        # no sender cache, acquisition or repeated payload growth is needed to share it onward.
        if read(original) == made:
            for digest, size in unique.items():
                with io.BytesIO(original) as stream:
                    _payload(stream, {"sha256": digest, "bytes": size})
            yield original, made
            return
        data = embed(original, made, portable=True) if original.startswith(PNG) else embed_jpeg(original, made, portable=True)
        yield data, made


def clear_exports(older_than_s: float = 3600.0) -> int:
    """Remove portable copies left in `exports/` (a download the browser never started, an app stopped mid-download):
    each is a temporary copy, its original untouched elsewhere. Only regular files older than `older_than_s`, so a
    download being served now is never removed. The number removed."""
    import time

    folder = share.root() / "exports"
    removed = 0
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return 0
    for entry in entries:
        try:
            info = entry.stat(follow_symlinks=False)
            if entry.is_file(follow_symlinks=False) and time.time() - info.st_mtime > older_than_s:
                os.unlink(entry.path)
                removed += 1
        except OSError:
            continue  # gone already, or not ours to remove
    return removed


def export_image(read, maximum: int, made: list[dict], memory) -> dict:
    """Make a separate downloadable file on a worker. Admission precedes source/payload reads, hashing and copies."""
    _off_loop()
    if maximum <= 0 or maximum > 32 * 1024 * 1024:
        raise ValueError("this original is missing or too large for a portable picture")
    clear_exports()  # an app left running for days sweeps as it exports, not only at start
    folder = share.root() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with memory.reserve("analysis", maximum, "reading the original for a portable picture"):
            original = read(maximum)
            if not original or len(original) > maximum:
                raise ValueError("the original is missing, changed, or larger than expected")
            if not original.startswith((PNG, b"\xff\xd8")):
                raise ValueError("only PNG and JPEG originals can be shared as portable pictures")
            with picture(original, made, memory) as (data, _):
                suffix = ".png" if original.startswith(PNG) else ".jpg"
                with tempfile.NamedTemporaryFile(dir=folder, suffix=suffix, delete=False) as stream:
                    tmp = Path(stream.name)
                    stream.write(data)
                # How many entries it carries as bytes (an unpublished one); a reviewed entry is named only.
                carried = len({d["sha256"] for c in made if (d := descriptor(c.get("portable")))})
                return {"path": tmp, "originalSha256": hashlib.sha256(original).hexdigest(),
                        "shareSha256": hashlib.sha256(data).hexdigest(), "carried": carried, "named": len(made)}
    except BaseException:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise


def export_path(path: Path, made: list[dict], memory) -> dict:
    """Export an already-owned local image; its bytes stay under the shared lease until its share file is written."""
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        maximum = path.stat().st_size
        return export_image(lambda limit: bounded_files.read_at(directory, path.name, limit), maximum, made, memory)
    finally:
        os.close(directory)


def carry_dataset(folder: Path, made: list[dict]) -> list[dict]:
    """Copy bounded entry sidecars into the dataset itself on its save/check worker; never sender-cache references."""
    _off_loop()
    out, total, copied = [], 0, set()
    for named in made:
        info = descriptor(named.get("portable"))
        if info is None:
            out.append(named)
            continue
        target = Path(folder) / "capabilities" / (info["sha256"] + share.SUFFIX)
        tmp = None
        try:
            if info["sha256"] not in copied:
                total += info["bytes"]
                if total > MAX_BYTES:
                    raise ValueError("its entry exceeds this dataset's portable capability allowance")
                target.parent.mkdir(parents=True, exist_ok=True)
                source = _root() / target.name
                descriptor_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                import stat
                with os.fdopen(descriptor_fd, "rb") as incoming:
                    size = os.fstat(incoming.fileno())
                    if not stat.S_ISREG(size.st_mode) or size.st_size != info["bytes"]:
                        raise ValueError("its retained entry is missing or changed")
                    digest, count = hashlib.sha256(), 0
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as outgoing:
                        tmp = Path(outgoing.name)
                        while block := incoming.read(64 * 1024):
                            count += len(block)
                            if count > info["bytes"]:
                                raise ValueError("its retained entry grew beyond its declared size")
                            digest.update(block)
                            outgoing.write(block)
                    if count != info["bytes"] or digest.hexdigest() != info["sha256"]:
                        raise ValueError("its retained entry no longer matches its producer")
                tmp.replace(target)
                copied.add(info["sha256"])
            out.append(named)
        except (OSError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "its retained entry could not be read or saved"
            out.append({**{k: v for k, v in named.items() if k != "portable"},
                        "shareWhy": f"Dataset capability sharing unavailable: {reason}."[:200]})
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
    return out


def _check(data: bytes, info: dict) -> bytes:
    if len(data) != info["bytes"] or hashlib.sha256(data).hexdigest() != info["sha256"]:
        raise ValueError("the result's capability bytes do not match its producing entry")
    return data


def chunks(made: list[dict], *, jpeg: bool = False) -> bytes:
    """Encode retained shares without putting entry bytes on the catalog/session/usage wire."""
    pieces, seen, total = [], set(), 0
    for named in made:
        info = descriptor(named.get("portable"))
        if info is None or info["sha256"] in seen:
            continue
        seen.add(info["sha256"])
        total += info["bytes"]
        if total > MAX_BYTES:
            raise ValueError("the capabilities are too large to carry together in one result")
        directory = os.open(_root(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            data = _check(bounded_files.read_at(directory, info["sha256"] + share.SUFFIX, MAX_BYTES), info)
        finally:
            os.close(directory)
        digest = bytes.fromhex(info["sha256"])
        if jpeg:
            count = (len(data) + PART - 1) // PART
            for index, start in enumerate(range(0, len(data), PART)):
                body = JPEG_TAG + digest + struct.pack(">HH", index, count) + data[start:start + PART]
                pieces.append(b"\xff\xef" + struct.pack(">H", len(body) + 2) + body)
        else:
            body = digest + data
            pieces.append(struct.pack(">I", len(body)) + PNG_KIND + body
                          + struct.pack(">I", zlib.crc32(PNG_KIND + body) & 0xFFFFFFFF))
    return b"".join(pieces)


def _payload(stream, info: dict) -> bytes:
    head = stream.read(8)
    digest = bytes.fromhex(info["sha256"])
    jpeg = head[:2] == b"\xff\xd8"
    if not jpeg and head != PNG:
        raise ValueError("this result is not a PNG or JPEG carrying an entry")
    stream.seek(2 if jpeg else 8)
    parts, expected, scanned = {}, None, 0
    for _ in range(512):
        header = stream.read(4 if jpeg else 8)
        if len(header) != (4 if jpeg else 8):
            break
        if jpeg:
            if header[0] != 0xFF or header[1] in (0xDA, 0xD9):
                break
            n = struct.unpack(">H", header[2:])[0] - 2
            candidate = header[1] == 0xEF
        else:
            n, kind = struct.unpack(">I4s", header)
            if kind in (b"IDAT", b"IEND"):
                break
            candidate = kind == PNG_KIND
        scanned += n + len(header) + (0 if jpeg else 4)
        if n < 0 or scanned > MAX_SCAN:
            raise ValueError("the result's embedded entry metadata exceeds its size limit")
        if not candidate:
            stream.seek(n + (0 if jpeg else 4), 1)
            continue
        data = stream.read(n)
        if len(data) != n:
            raise ValueError("the result's embedded entry is incomplete")
        if not jpeg:
            crc = stream.read(4)
            if crc != struct.pack(">I", zlib.crc32(PNG_KIND + data) & 0xFFFFFFFF):
                raise ValueError("the result's embedded entry is damaged")
            if data[:32] == digest:
                return _check(data[32:], info)
        elif data.startswith(JPEG_TAG + digest):
            offset = len(JPEG_TAG) + 32
            index, count = struct.unpack(">HH", data[offset:offset + 4])
            if not 0 <= index < count <= (MAX_BYTES + PART - 1) // PART or index in parts:
                raise ValueError("the result's embedded entry has invalid or duplicate parts")
            if expected is not None and count != expected:
                raise ValueError("the result's embedded entry has inconsistent parts")
            expected, parts[index] = count, data[offset + 4:]
            if len(parts) == expected:
                return _check(b"".join(parts[i] for i in range(expected)), info)
    raise ValueError("this result does not carry the capability bytes it names; ask for its share file")


def acquire(path: Path, value: dict, memory) -> dict:
    """Explicitly import a result's pinned portable entry, preserving the usual Allow code and Install steps."""
    _off_loop()
    info = descriptor(value)
    if info is None:
        raise ValueError("the result has no valid portable entry reference")
    try:
        with memory.reserve("analysis", 5 * MAX_BYTES, "reading a capability carried by a result"):
            if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
                with Path(path).open("rb") as stream:
                    data = _payload(stream, info)
            else:
                dataset = next((p for p in (path.parent, path.parent.parent) if (p / "dataset.json").is_file()), None)
                if dataset is None:
                    raise ValueError("this recording has no retained dataset capability folder")
                directory = os.open(dataset / "capabilities", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    data = _check(bounded_files.read_at(directory, info["sha256"] + share.SUFFIX, MAX_BYTES), info)
                finally:
                    os.close(directory)
            folder = _root()
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=folder, suffix=share.SUFFIX, delete=False) as stream:
                tmp = Path(stream.name)
                stream.write(data)
            try:
                return share.open_file(tmp)
            finally:
                tmp.unlink(missing_ok=True)
    except (OSError, struct.error, share.CantShare) as exc:
        raise ValueError(f"could not acquire the capability carried by this result: {exc}") from exc
