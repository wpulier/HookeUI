"""Kept from going live, batch 1 (the design doc, G2): run with the live app up, `.venv/bin/python -m bench.flow6_replay OUT.json`.
Flow 6 replay on the live app: footage, two clicks, the cells outlined and the five largest highlighted, a seek like
the one that failed (1:07 -> 0:08), then every frame's epoch/tracked and every scene update's outline states, for
20 s. At the end the feed goes back to what it was, and only the outlines it made are removed; the cells it outlined
stay in the day's evidence (nothing is deleted) and their layer is hidden. Frames carry no bridge seq here, so a cut
taken for continuous motion and a still view look alike in this log: read `tracked` and the epoch."""
import asyncio
import json
import struct
import sys
import time
import urllib.request

import websockets

BRIDGE = "http://127.0.0.1:8765"
WS = "ws://127.0.0.1:8770/ws"
FOOTAGE = "real/2026-06-14-leaf-epidermis-3m43s.mp4"
OUT = sys.argv[1]


def post(path):
    req = urllib.request.Request(BRIDGE + path, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


async def main():
    log, state = [], {"latest": None, "objects": []}
    t0 = time.time()

    before = json.loads(urllib.request.urlopen(BRIDGE + "/feed", timeout=15).read())
    restore = (f"/feed?source=footage&footage={before['footage']}" if before.get("source") == "footage" and before.get("footage")
               else "/feed?source=camera")
    async with websockets.connect(WS, max_size=None) as ws:
        async def reader():
            async for msg in ws:
                t = round(time.time() - t0, 3)
                if isinstance(msg, (bytes, bytearray)):
                    if bytes(msg[:4]) != b"MMF1":
                        continue
                    n = struct.unpack(">I", bytes(msg[4:8]))[0]
                    h = json.loads(bytes(msg[8:8 + n]))
                    state["latest"] = h
                    shown = [o["id"] for o in state["objects"]
                             if o.get("track") == "tracking" and o.get("anchorEpoch") == h.get("epoch")]
                    log.append({"t": t, "frame": h.get("id"), "epoch": h.get("epoch"), "tracked": h.get("tracked"),
                                "steady": h.get("steady"), "dx": round(h.get("dx") or 0, 1), "dy": round(h.get("dy") or 0, 1),
                                "speed": h.get("speed"), "fov": h.get("fov"), "drawn": shown})
                else:
                    m = json.loads(msg)
                    if m.get("type") == "scene":
                        state["objects"] = [{k: o.get(k) for k in ("id", "track", "anchorEpoch", "anchorOffset", "anchorFrame", "label", "kind")}
                                            for o in m.get("objects") or []]
                        log.append({"t": t, "scene": state["objects"]})
                    elif m.get("type") in ("chat", "status", "error"):
                        log.append({"t": t, "msg": {k: m.get(k) for k in ("type", "text", "error")}})

        task = asyncio.create_task(reader())
        await asyncio.sleep(1)
        theirs = {o["id"] for o in state["objects"]}  # outlines already on screen are the user's: left alone
        try:
            post(f"/feed?source=footage&footage={FOOTAGE}")
            await asyncio.sleep(2)
            post("/feed/seek?seconds=0")  # as in the session that failed: the start played first, so it was seen before
            log.append({"t": round(time.time() - t0, 3), "event": "playing from 0 s"})
            await asyncio.sleep(30)
            post("/feed/seek?seconds=61")
            post("/feed/pause?paused=1")
            log.append({"t": round(time.time() - t0, 3), "event": "paused at 61 s"})
            await asyncio.sleep(4)
            # As in the session that failed: two clicks, the cells outlined, the five largest highlighted.
            for x, y in ((754.8, 454.0), (540.0, 520.0)):
                h = state["latest"]
                await ws.send(json.dumps({"type": "click", "frameId": h["id"], "x": x, "y": y}))
                log.append({"t": round(time.time() - t0, 3), "event": f"click on frame {h['id']} at {x},{y}"})
                await asyncio.sleep(4)
            await ws.send(json.dumps({"type": "tool", "name": "outline_cells", "args": {}}))
            log.append({"t": round(time.time() - t0, 3), "event": "outline_cells"})
            await asyncio.sleep(15)
            await ws.send(json.dumps({"type": "tool", "name": "highlight", "args": {"by": "area", "top": 5, "also": True}}))
            log.append({"t": round(time.time() - t0, 3), "event": "highlight five largest"})
            await asyncio.sleep(8)
            post("/feed/seek?seconds=67")
            post("/feed/pause?paused=0")
            log.append({"t": round(time.time() - t0, 3), "event": "playing from 67 s"})
            await asyncio.sleep(2)
            post("/feed/seek?seconds=8")
            log.append({"t": round(time.time() - t0, 3), "event": "seek to 8 s"})
            await asyncio.sleep(20)
            for o in list(state["objects"]):  # only the replay's own outlines go
                if o["id"] not in theirs:
                    await ws.send(json.dumps({"type": "remove", "id": o["id"]}))
            await ws.send(json.dumps({"type": "tool", "name": "present", "args": {"what": "cells", "do": "hide"}}))
            await asyncio.sleep(1)
        finally:
            try:
                post(restore)
                log.append({"t": round(time.time() - t0, 3), "event": f"feed restored ({before.get('source')})"})
            finally:
                task.cancel()
                with open(OUT, "w") as f:
                    json.dump(log, f)


asyncio.run(main())
