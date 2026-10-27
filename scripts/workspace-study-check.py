#!/usr/bin/env python3
"""Exercise production HTTP/WS paths on an explicitly isolated Studio.

Run with the services Python. The exercise creates retained local data. Verify
reads the resulting receipt after a launcher restart, without running analysis.
This proves backend evidence, not browser rendering or scientific accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import zipfile
from urllib.parse import urlencode, urlparse
from uuid import uuid4

import cv2
import httpx
import numpy as np
import websockets


def local_url(value: str) -> str:
    parsed = urlparse(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port in {None, 8765, 8770} or parsed.path not in {"", "/"}):
        raise argparse.ArgumentTypeError("Use an isolated loopback port, never production 8765 or 8770.")
    return value.rstrip("/")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def table_digest(table: dict) -> str:
    retained = {key: table[key] for key in ("id", "revision", "basis", "columns", "rows")}
    return digest(json.dumps(retained, sort_keys=True, separators=(",", ":")).encode())


async def command(socket, kind: str, *, expect_ok: bool = True, **args) -> dict:
    request = uuid4().hex
    await socket.send(json.dumps({"type": kind, "requestId": request, **args}))
    async with asyncio.timeout(90):
        while True:
            message = await socket.recv()
            if isinstance(message, bytes):
                continue
            reply = json.loads(message)
            if reply.get("type") == "result" and reply.get("requestId") == request:
                if expect_ok and not reply.get("ok"):
                    raise AssertionError(f"{kind} refused: {reply}")
                return reply


async def get(client, path: str):
    response = await client.get(path)
    response.raise_for_status()
    return response.json()


async def upload_asset(client, args, data: bytes, title: str, *, microscope: bool) -> dict:
    filename = f"workspace-study-{uuid4().hex[:8]}.png"
    async with httpx.AsyncClient(timeout=90) as bridge:
        uploaded = await bridge.post(args.bridge_url + "/assets/upload", params={"filename": filename,
            "title": title, "specimen": title, "microscope": "1" if microscope else "0"},
            headers={"Origin": args.url, "X-MicroscopeMind": "add-footage", "Content-Type": "application/octet-stream"},
            content=data)
    uploaded.raise_for_status()
    body = uploaded.json()
    assert body["ok"], body
    stored_name = Path(body["item"]["name"]).name
    library = await get(client, "/api/library?" + urlencode({"fresh": "true", "q": Path(stored_name).stem}))
    asset = next(item for item in library["assets"] if item["file"] == stored_name)
    assert asset["microscope"] is microscope, asset
    return asset


async def inspect(client, receipt: dict, *, current: bool = True) -> dict:
    sid, rid, revision = receipt["session"], receipt["result"], receipt["revision"]
    document = await get(client, f"/api/resources/{rid}?" + urlencode({"day": sid}))
    entry = document["entry"]
    assert entry["day"] == sid and entry["id"] == rid and entry["version"] == revision
    assert entry["madeBy"]["implementation"] == "basic-bright-objects", entry["madeBy"]
    source = entry["source"]["sourceImage"]
    assert source["asset"] == receipt["asset"], source
    assert source["session"] == sid, source
    assert document["detail"]["producer"]["input"]["sourceImage"] == source
    response = await client.get(f"/api/resources/{rid}/table?" + urlencode({"session": sid, "revision": revision}))
    table = None
    if current:
        response.raise_for_status()
        table = response.json()
        assert table["rows"], "The real microscope example retained no measured objects."
        assert table["basis"].get("umPerPx") in {None, 0}, table["basis"]
        assert any(column["unit"] == "px²" for column in table["columns"]), table["columns"]
        assert not any("µm" in column["unit"] for column in table["columns"])
    else:
        assert response.status_code == 409 and "session changed" in response.json()["error"].lower()
    masks = await client.get(f"/api/resources/{rid}/masks.npz?" + urlencode({"day": sid}))
    masks.raise_for_status()
    with np.load(io.BytesIO(masks.content), allow_pickle=False) as archive:
        assert archive.files, "The retained mask archive is empty."
        mask_arrays = len(archive.files)
        if table:
            assert sum(name.endswith(".bits") for name in archive.files) == len(table["rows"])
    picture = await client.get(entry["pictures"][0])
    picture.raise_for_status()
    pixels = cv2.imdecode(np.frombuffer(picture.content, np.uint8), cv2.IMREAD_UNCHANGED)
    assert pixels is not None, "The retained picture cannot be decoded."
    native_hash = None
    if current:
        exchange = await client.get(f"/api/resources/{rid}/exchange?" + urlencode({"session": sid, "revision": revision}))
        exchange.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(exchange.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            native = archive.read(manifest["sourceFile"])
        native_hash = digest(native)
        assert native_hash == source["sha256"], "The exchange differs from the producer's source bytes."
        original = cv2.imdecode(np.frombuffer(native, np.uint8), cv2.IMREAD_UNCHANGED)
        assert original is not None and digest(original.tobytes()) == source["pixelSha256"]
    wrong = await client.get(f"/api/resources/{rid}/table?" + urlencode({"session": sid, "revision": revision + 1}))
    assert wrong.status_code == 409, "A table accepted the wrong revision."
    return {"tableSha256": table_digest(table) if table else None, "masksSha256": digest(masks.content),
            "picturePixelsSha256": digest(pixels.tobytes()), "pictureShape": list(pixels.shape),
            "pictureMeaning": "Retained JPEG display preview, not original scientific pixels.",
            "originalSha256": native_hash,
            "maskArrays": mask_arrays, "rowCount": len(table["rows"]) if table else None,
            "source": source, "madeBy": entry["madeBy"], "producer": document["detail"].get("producer")}


async def exercise(args, report: dict) -> None:
    async with httpx.AsyncClient(base_url=args.url, timeout=90) as client:
        report["build"] = (await get(client, "/api/health"))["build"]
        example = await client.get("/api/onboarding/example")
        example.raise_for_status()
        report["dataset"] = {"name": "Bundled real human mitosis microscopy image", "sha256": digest(example.content),
                             "microscope": True,
                             "calibration": "None. Pixel units only.", "responses": "Actual local Bright objects operation. No model."}
        socket_url = args.url.replace("http://", "ws://") + "/ws"
        async with websockets.connect(socket_url, origin=args.url, max_size=16 * 1024 * 1024) as socket:
            started = await command(socket, "onboarding", action="restart")
            sid = started["onboarding"]["session"]
            assert started["onboarding"]["localOnly"] is True
            asset = await upload_asset(client, args, example.content, "Workspace study human cells", microscope=True)
            preview = await get(client, f"/api/library/{asset['id']}")
            assert not preview["sessions"], "A library upload unexpectedly joined a project."
            report["checks"].append("Upload uses the production bridge and preview does not add project membership.")
            await command(socket, "source", open=asset["id"], session=sid)
            await command(socket, "view", held=False)
            result = await command(socket, "action", capability="cells", args={},
                                   use={"cells": "basic-bright-objects"}, run=uuid4().hex)
            report["action"] = result
            async with asyncio.timeout(30):
                while True:
                    index = await get(client, "/api/resources?" + urlencode({"day": sid}))
                    populations = [entry for entry in index["entries"] if entry["kind"] == "population"
                                   and entry["source"].get("sourceImage", {}).get("asset") == asset["id"]]
                    if populations and populations[-1]["state"] == "saved":
                        break
                    await asyncio.sleep(0.2)
            entry = populations[-1]
            report["receipt"] = {"session": sid, "asset": asset["id"], "result": entry["id"], "revision": entry["version"]}
            report["evidence"] = await inspect(client, report["receipt"])
            assert report["evidence"]["source"]["sha256"] == report["dataset"]["sha256"]
            report["checks"].append("Action/run_tool retained real source identity, masks, measured pixel units and revision-bound table.")
            membership = await get(client, f"/api/library/{asset['id']}")
            assert sid in json.dumps(membership["sessions"]), membership["sessions"]
            other = await command(socket, "session", action="new", name="Workspace study recovery check")
            assert other["session"] != sid
            away = await inspect(client, report["receipt"], current=False)
            for key in ("masksSha256", "picturePixelsSha256"):
                assert away[key] == report["evidence"][key], f"{key} changed after project switch."
            await command(socket, "session", action="switch", id=sid)
            await command(socket, "source", open=asset["id"], session=sid)
            returned = await inspect(client, report["receipt"])
            assert returned["tableSha256"] == report["evidence"]["tableSha256"]
            report["checks"].append("Historical masks and JPEG preview stay readable; Measurements refuses another active project; returning restores the exact table and original exchange bytes without recomputation.")


async def verify(args, report: dict) -> None:
    saved = json.loads(args.receipt.read_text())
    assert saved["ok"] and saved["receipt"]
    async with httpx.AsyncClient(base_url=args.url, timeout=90) as client:
        actual = await inspect(client, saved["receipt"])
        for key in ("tableSha256", "masksSha256", "picturePixelsSha256", "source"):
            assert actual[key] == saved["evidence"][key], f"Retained {key} changed."
        report["receipt"], report["evidence"] = saved["receipt"], actual
        report["build"] = (await get(client, "/api/health"))["build"]
        report["checks"].append("Read-only recovery verified exact saved table, masks and preview, plus original exchange bytes against producer hashes. No analysis command sent.")


async def failure_cases(args, report: dict) -> None:
    report["dataset"] = {"name": "Synthetic uniform black 8-bit PNG and unsupported 16-bit PNG, 64 by 64 pixels",
                         "responses": "Actual production command and analysis responses. No model."}
    async with httpx.AsyncClient(base_url=args.url, timeout=90) as client:
        original = (await get(client, "/api/sessions"))["current"]
        original_sources = await get(client, f"/api/sessions/{original}/sources")
        report["build"] = (await get(client, "/api/health"))["build"]
        async with websockets.connect(args.url.replace("http://", "ws://") + "/ws", origin=args.url,
                                      max_size=16 * 1024 * 1024) as socket:
            try:
                started = await command(socket, "onboarding", action="restart")
                sid = started["onboarding"]["session"]
                report["session"] = sid
                report["replies"] = {}
                for title, kind, values in (
                    ("unknownCapability", "action", {"capability": "workspace-study-missing", "args": {}}),
                    ("unavailableModel", "action", {"capability": "cells", "args": {}, "use": {"cells": "cellpose-sam"}}),
                    ("cancelMissingRun", "action-cancel", {"run": "workspace-study-never-started"}),
                ):
                    reply = await command(socket, kind, expect_ok=False, **values)
                    report["replies"][title] = reply
                    assert reply["ok"] is False and reply.get("error"), reply
                report["checks"].append("Unknown capability, unavailable Cellpose-SAM and cancellation of a nonexistent run return explicit refusals. No fallback runs.")
                ok, black = cv2.imencode(".png", np.zeros((64, 64), np.uint8))
                assert ok
                asset = await upload_asset(client, args, black.tobytes(), "Synthetic empty black image", microscope=False)
                await command(socket, "source", open=asset["id"], session=sid)
                empty = await command(socket, "action", capability="cells", args={},
                                      use={"cells": "basic-bright-objects"}, run=uuid4().hex)
                report["replies"]["emptyResult"] = empty
                index = await get(client, "/api/resources?" + urlencode({"day": sid}))
                entry = next(row for row in index["entries"] if row["kind"] == "population"
                             and row["source"].get("sourceImage", {}).get("asset") == asset["id"])
                table = await get(client, f"/api/resources/{entry['id']}/table?" + urlencode({"session": sid, "revision": entry["version"]}))
                assert table["count"] == 0 and table["rows"] == []
                report["checks"].append("A synthetic black image produces a real retained empty population and a zero-row table.")
                ok, deep = cv2.imencode(".png", np.zeros((64, 64), np.uint16))
                assert ok
                unsupported = await upload_asset(client, args, deep.tobytes(), "Synthetic unsupported 16-bit image", microscope=False)
                await command(socket, "source", open=unsupported["id"], session=sid)
                refusal = await command(socket, "action", expect_ok=False, capability="cells", args={},
                                        use={"cells": "basic-bright-objects"}, run=uuid4().hex)
                report["replies"]["unsupportedDepth"] = refusal
                assert refusal["ok"] is False and "8-bit" in refusal["error"], refusal
                report["checks"].append("A synthetic 16-bit PNG is refused with the supported 8-bit export step.")
            finally:
                await command(socket, "session", action="switch", id=original)
                on_stage = original_sources.get("onStage")
                if isinstance(on_stage, str) and on_stage:
                    await command(socket, "source", open=on_stage, session=original)


async def known_geometry(args, report: dict) -> None:
    pixels = np.zeros((96, 96), np.uint8)
    pixels[:, :20] = 255
    pixels[:, 76:] = 255
    expected = {"areasPx2": [1920, 1920], "centersPx": [[10.0, 48.0], [86.0, 48.0]], "brightness": [255, 255]}
    report["dataset"] = {"name": "Synthetic 96 by 96 image with two full-height 20-pixel white bands",
                         "microscope": False, "calibration": "None. Pixel units only.",
                         "responses": "Actual production Bright objects operation. No model.",
                         "expected": expected, "oracle": "Rectangular raster area and pixel-center arithmetic. No segmentation code."}
    async with httpx.AsyncClient(base_url=args.url, timeout=90) as client:
        original = (await get(client, "/api/sessions"))["current"]
        original_sources = await get(client, f"/api/sessions/{original}/sources")
        report["build"] = (await get(client, "/api/health"))["build"]
        async with websockets.connect(args.url.replace("http://", "ws://") + "/ws", origin=args.url,
                                      max_size=16 * 1024 * 1024) as socket:
            try:
                started = await command(socket, "onboarding", action="restart")
                sid = started["onboarding"]["session"]
                encoded, png = cv2.imencode(".png", pixels)
                assert encoded
                asset = await upload_asset(client, args, png.tobytes(), "Synthetic known geometry bands", microscope=False)
                await command(socket, "source", open=asset["id"], session=sid)
                report["action"] = await command(socket, "action", capability="cells", args={},
                    use={"cells": "basic-bright-objects"}, run=uuid4().hex)
                rid = report["action"]["table"]
                revision = report["action"]["tableIdentity"]["revision"]
                report["receipt"] = {"session": sid, "asset": asset["id"], "result": rid, "revision": revision}
                table = await get(client, f"/api/resources/{rid}/table?" + urlencode({"session": sid, "revision": revision}))
                rows = sorted(table["rows"], key=lambda row: row["x"])
                observed = {"areasPx2": [row["areaPx"] for row in rows],
                            "centersPx": [[row["x"], row["y"]] for row in rows],
                            "brightness": [row["intensity"] for row in rows]}
                report["observed"] = observed
                assert observed == expected, {"expected": expected, "observed": observed}
                assert table["count"] == 2
                report["edgeFlags"] = [row["edgeCut"] for row in rows]
                if not all(report["edgeFlags"]):
                    report["knownLimitations"] = ["Full-image native analysis reports edgeCut=false for bands touching the image boundary. Areas, centers and intensities remain independently checked."]
                async with asyncio.timeout(30):
                    while True:
                        document = await get(client, f"/api/resources/{rid}?" + urlencode({"day": sid}))
                        if document["entry"]["state"] == "saved":
                            break
                        await asyncio.sleep(.2)
                report["evidence"] = await inspect(client, report["receipt"])
                report["checks"].append("Two synthetic bands retain exactly 1920 pixels each, centers at (10,48) and (86,48), brightness255 and pixel units through the production operation. Edge flags are recorded separately.")
            finally:
                await command(socket, "session", action="switch", id=original)
                on_stage = original_sources.get("onStage")
                if isinstance(on_stage, str) and on_stage:
                    await command(socket, "source", open=on_stage, session=original)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("exercise", "verify", "failure-cases", "known-geometry"))
    parser.add_argument("--url", required=True, type=local_url)
    parser.add_argument("--bridge-url", type=local_url)
    parser.add_argument("--receipt", type=Path, help="Prior exercise JSON for read-only verification.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.mode in {"exercise", "failure-cases", "known-geometry"} and not args.bridge_url:
        parser.error("exercise, failure-cases and known-geometry require --bridge-url for the isolated camera bridge")
    if args.mode == "verify" and not args.receipt:
        parser.error("verify requires --receipt")
    report = {"class": "Real local HTTP/WebSocket backend. No browser rendering claim.",
              "mode": args.mode, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "url": args.url, "checks": [], "ok": False}
    try:
        run = {"exercise": exercise, "verify": verify, "failure-cases": failure_cases, "known-geometry": known_geometry}[args.mode]
        asyncio.run(run(args, report))
        report["ok"] = True
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"ok": report["ok"], "checks": report["checks"], "failure": report.get("failure"), "output": str(args.output)}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
