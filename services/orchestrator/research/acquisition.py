"""Mission-owned camera clips, retained as original JPEGs plus frame identities.

This does not switch Record or capture microphone/tab audio. Explicit scheduled
camera acquisition uses the existing frame intake and research memory allowance.
The ZIP is a lossless frame sequence, not a video clock. Bridge boot/sequence
identities allow a recording sidecar to supply bridgeAt when one exists.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path

CLIP_ID = re.compile(r"acq-[0-9a-f]{32}")


def save_clip(root: Path, frames, *, mission, slot, day: str, session: str | None = None) -> dict:
    """Worker-thread write; publish only after original bytes and manifest are durable. `day`: the date folder (its
    researcher's); `session`: the session it belongs to (its researcher's, ADR 0017), kept in its manifest."""
    clip_id = "acq-" + uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / day).mkdir(exist_ok=True, mode=0o700)
    folder = root / day / clip_id
    folder.mkdir(parents=True, mode=0o700)
    try:
        rows = []
        path = folder / "frames.zip"
        with path.open("xb") as raw:
            os.chmod(path, 0o600)
            with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_STORED) as archive:
                for n, (frame, info) in enumerate(frames):
                    data = frame.jpeg
                    if not data:
                        raise ValueError("a captured frame has no original pixels")
                    archive.writestr(f"{n:06}.jpg", data)
                    rows.append({"n": n, "frame": info.id, "seq": info.seq, "bootId": info.boot,
                                 "at": info.wall_at, "width": info.width, "height": info.height,
                                 "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                                 "receivedAt": getattr(frames, "received_times", {}).get(info.id),
                                 "epoch": getattr(info, "epoch", None), "dx": getattr(info, "dx", None),
                                 "dy": getattr(info, "dy", None), "disc": getattr(info, "disc", None),
                                 "tracked": getattr(info, "tracked", False), "steady": getattr(info, "steady", False),
                                 "fov": asdict(info.fov) if getattr(info, "fov", None) is not None else None,
                                 "scale": getattr(info, "scale", None)})
            raw.flush()
            os.fsync(raw.fileno())
        if len(rows) < 2:
            raise ValueError("a clip needs multiple original frames")
        from ..capture import RUN
        clocked = all(r["receivedAt"] is not None for r in rows)
        record = {"schema": 1, "id": clip_id, "day": day, **({"session": session} if session else {}), "run": RUN,
                  "missionId": mission.id, "rid": mission.rid,
                  "slot": slot.n, "due": slot.due, "requestedSeconds": mission.acquisition["seconds"],
                  "from": rows[0]["at"], "to": rows[-1]["at"], "frames": rows,
                  "clockDomain": f"intake-monotonic:{RUN}", "timingQualified": clocked,
                  "monotonicSeconds": rows[-1]["receivedAt"] - rows[0]["receivedAt"] if clocked else None,
                  "gaps": [{"after": a["n"], "before": b["n"],
                            "seconds": b["receivedAt"] - a["receivedAt"]}
                           for a, b in zip(rows, rows[1:]) if clocked and b["receivedAt"] - a["receivedAt"] > .25],
                  "clock": "orchestrator frame wall time; bridgeAt requires the original bridge sidecar",
                  "channels": {"camera": "captured", "microphone": "unavailable", "tabAudio": "unavailable",
                               "screen": "unavailable"}}
        manifest = folder / "manifest.pending"
        with manifest.open("x") as file:
            os.chmod(manifest, 0o600)
            json.dump(record, file, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(manifest, folder / "manifest.json")
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return record
    except BaseException:
        shutil.rmtree(folder)
        raise


async def acquire(hub, root: Path, mission, slot, day: str, check, owner_check=None, on_retained=None,
                  session: str | None = None) -> dict:
    from ..brain.toolbox.motion import gather

    check()
    frames = await gather(hub, mission.acquisition["seconds"], fresh=True, check=check, clocked=True)
    try:
        if owner_check is not None:
            owner_check()
        # The writer owns its lease even if cancellation retires the mission.
        frames.retain()

        def write():
            try:
                return save_clip(root, frames, mission=mission, slot=slot, day=day, session=session)
            finally:
                frames.release()

        writing = asyncio.get_running_loop().run_in_executor(None, write)

        def retained(future):
            # Cancellation cannot discard a completed write's original mission/slot association.
            # Keep its failed/cancelled outcome; this callback never resumes acquisition.
            try:
                saved = future.result()
            except BaseException:
                return
            slot.entry, slot.count = saved["id"], len(saved["frames"])
            first = saved["frames"][0]
            slot.capture = {"frame": first["frame"], "seq": first["seq"], "boot": first["bootId"],
                            "at": first["at"], "endedAt": saved["to"], "seconds": saved["monotonicSeconds"],
                            "clockDomain": saved["clockDomain"], "gaps": saved["gaps"]}
            if on_retained is not None and any(row is slot for row in mission.ledger):
                on_retained()

        writing.add_done_callback(retained)
        record = await asyncio.shield(writing)
        if owner_check is not None:
            owner_check()
        return {"ok": True, "id": record["id"], "count": len(record["frames"]), "capture": slot.capture}
    finally:
        frames.release()


def read_clip(root: Path, day: str, clip_id: str) -> dict | None:
    if not CLIP_ID.fullmatch(clip_id) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None
    try:
        record = json.loads((root / day / clip_id / "manifest.json").read_text())
        if record.get("id") != clip_id or record.get("day") != day:
            return None
        return record
    except (OSError, ValueError):
        return None


def picture(root: Path, day: str, clip_id: str, n: int, max_bytes: int | None = None) -> bytes | None:
    record = read_clip(root, day, clip_id)
    if record is None or type(n) is not int or not 0 <= n < len(record["frames"]):
        return None
    frame = record["frames"][n]
    if max_bytes is not None and frame["bytes"] > max_bytes:
        return None
    try:
        with zipfile.ZipFile(root / day / clip_id / "frames.zip") as archive:
            item = archive.getinfo(f"{n:06}.jpg")
            if item.file_size != frame["bytes"]:
                return None
            data = archive.read(item)
        return data if hashlib.sha256(data).hexdigest() == frame["sha256"] else None
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


def load_capture(hub, root: Path, day: str, clip_id: str, n: int | None):
    """Read one retained frame through the existing restart-safe capture identity path."""
    from types import SimpleNamespace
    from ..capture import captures
    from ..fov import FieldOfView

    record = read_clip(root, day, clip_id)
    if record is None:
        raise ValueError("that scheduled camera clip is not retained")
    n = len(record["frames"]) - 1 if n is None else n
    if type(n) is not int or not 0 <= n < len(record["frames"]):
        raise ValueError("that frame is outside the retained camera clip")
    row = record["frames"][n]
    if type(row.get("bytes")) is not int or row["bytes"] <= 0:
        raise ValueError("the retained camera frame has no valid byte size")
    room = captures(hub).memory.reserve("research", row["bytes"], "reading a retained camera frame")
    try:
        data = picture(root, day, clip_id, n, max_bytes=row["bytes"])
        if data is None:
            raise ValueError("that camera frame is missing or its byte identity changed")
        info = SimpleNamespace(id=row["frame"], seq=row["seq"], boot=row["bootId"], wall_at=row["at"],
            width=row["width"], height=row["height"], epoch=row.get("epoch"), dx=row.get("dx") or 0,
            dy=row.get("dy") or 0, disc=row.get("disc") or 0, tracked=row.get("tracked", False),
            steady=row.get("steady", False), fov=FieldOfView(**row["fov"]) if row.get("fov") else None)
        return captures(hub).read_back(info, data, row.get("scale"), record.get("run")), n, room
    except BaseException:
        room.release()
        raise


async def open_capture(hub, root: Path, day: str, clip_id: str, n: int | None):
    """A cancelled caller leaves its reservation with the reader until it really stops."""
    future = asyncio.get_running_loop().run_in_executor(None, load_capture, hub, root, day, clip_id, n)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        def release(completed):
            try:
                completed.result()[2].release()
            except BaseException:
                pass  # load_capture already released a failed read
        future.add_done_callback(release)
        raise


def load_frames(hub, root: Path, day: str, clip_id: str):
    """Original retained sequence, each frame admitted before reading its bytes."""
    from ..capture import captures
    from ..motiontracks import MotionFrames

    record = read_clip(root, day, clip_id)
    if record is None:
        raise ValueError("that scheduled camera clip is not retained")
    if not record.get("timingQualified"):
        raise ValueError("that clip has no qualified acquisition clock for motion measurement")
    frames = MotionFrames(captures(hub))
    try:
        for n in range(len(record["frames"])):
            capture, _, room = load_capture(hub, root, day, clip_id, n)
            try:
                frames.add(capture)
            finally:
                room.release()
        return frames
    except BaseException:
        frames.release()
        raise


async def open_frames(hub, root: Path, day: str, clip_id: str):
    future = asyncio.get_running_loop().run_in_executor(None, load_frames, hub, root, day, clip_id)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        def release(completed):
            try:
                completed.result().release()
            except BaseException:
                pass
        future.add_done_callback(release)
        raise
