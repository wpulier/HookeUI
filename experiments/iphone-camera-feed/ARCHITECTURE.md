# How the camera bridge works

For developers. What it is for and how to use it are in [README.md](README.md); why it is this way, in
[LEARNINGS.md](LEARNINGS.md) and ADR 0023.

The bridge runs on the macOS system Python (3.9), standard library only, so it starts with nothing installed. It is
the only program that opens the camera.

## The frame path

```text
iPhone ── Continuity Camera ──> capture-app (Swift, one frame in flight) ──┐
                                                                           ├─> the bridge's reader ─> frame cache ─> GET /iphone.mjpeg ─> orchestrator ─> studio
Blackmagic Camera ── SRT (cable or Wi-Fi) ──> FFmpeg listener ─────────────┘        │
                                                                                    ├─> recording writer (its own thread, sidecar per frame)
test footage (simulated_camera.py) replaces the device at its three touch points ───┘
```

- **Newest frame only, everywhere:** no stage queues frames. The capture app keeps one in flight. The bridge hands
  each frame to `/iphone.mjpeg` readers and the recorder separately. The recorder drops frames more than 3 s behind
  rather than holding the live view back.
- **What each frame carries:** `X-Frame-Seq` (the bridge's frame number) and `X-Frame-At` (when the bridge took it).
  These give the studio's meter the true delay.
- **A stream** (`stream_source.py`) is like test footage: it replaces only the device's three touch points (listing,
  capture command, reset), so nothing downstream can tell what the source is.
- **The other camera:** a camera this Mac sees goes through the capture app by its name, as the iPhone does, and a
  stream address through `stream_source.py`. `reset_continuity_agent_if_allowed` refuses for it, and Reset camera is a
  fresh capture.

## Processes and threads

| | Where | What it does |
|---|---|---|
| HTTP server | `bridge_routes.py` | the route table; every request on its own thread |
| monitor | `bridge_monitor.py` | keeps one capture running: restarts a dead one, heals a silent iPhone (asked again, backing off 30 s to 5 min), notices frozen frames |
| reader | `bridge_monitor.py` | reads the capture's JPEG stream into the frame cache |
| recording writer | `bridge_recording.py` | the camera recording and its sidecar, off the frame path |
| phone link | `phone_control.PhoneLink` | everything said to Blackmagic Camera, so no request or frame ever waits on the phone |
| Phone check | `phone_lab.PhoneCheck` | the guided check, when started |
| settings server | `settings_server.py` | port 8767, the Blackmagic settings file only, for the phone on the Mac's network |
| source trial, freeze heal | `bridge_status.py`, `bridge_monitor.py` | short-lived |

## The modules

`iphone_bridge_server.py` is the entry point. It holds the live state (the chosen source, test footage) and
`CameraBridge`, assembled from mixins in their own modules: `MonitorMixin`, `RecordingMixin` and `StatusMixin`.

**Why the modules reach the main module through `self.main`:** the tests load the main module afresh for each test and
replace names on it (`make_device_snapshot`, `native_capture_ready`, `SIMULATED_CAMERA`...). The modules beside it
are imported once, so they reach those names through `self.main` (`bridge_common.ModuleView`), a view of the main
module's names as they are now.

**When adding to the bridge:**
- put live state and anything a test replaces in the main module;
- put pure helpers in the module of their subject;
- add a route to `ROUTES` in `bridge_routes.py`, with its `Guard` (the header and the refusal) if only a local page may
  press it.

## The phone link (Blackmagic)

`PhoneLink` runs on its own thread. It asks the phone about every 2 s while the camera page or the Phone panel is open
or there is no picture, and every 10 s while the picture flows.

```text
          ┌────────────── no phone answering ──────────────┐
          v                                                 │
   find the phone (Bonjour, or the cable's address) ──> read its state
          │
          ├─ Blackmagic chosen, no picture ──> aim its stream at this Mac over the link (the cable when it shares a
          │                                    network, else Wi-Fi) and start it, at most every 12 s
          │       ├─ just started: 20 s grace before anything is restarted
          │       ├─ streaming the whole grace, nothing arrives: a fresh listener, then the phone's stream once
          │       │  (at most once a minute)
          │       └─ the app has no server for the cable's address: Wi-Fi, and the cable tried again after 60 s
          ├─ the link changed (cable in or out) ──> move the stream, even while live
          ├─ iPhone chosen ──> stop the phone's stream once, so the phone can be locked
          └─ every 30 s: the phone's crash reports (devicectl); a crash within 30 s of the bridge starting the stream
             marks that quality unsafe for good (blackmagic-api.json crashedQualities)
```

Never sent to the phone, because each broke the app on 2026-10-11/12:
- a custom server URL, or a server the app doesn't list;
- a change of stream quality;
- a settings file pushed over the network;
- stop-and-start loops.

## Only proven writes reach the phone

`blackmagic-api.json` is the manifest, per Blackmagic Camera version: the reads that work, the writes proven safe, the
lenses that switch, and the qualities that crashed.
- **The rule:** `Phone.put` refuses any write not marked safe for the running version. The only exceptions are
  streaming's own start, stop and server choice (`phone_lab.CORE_WRITES`).
- **How it is learned:** the Phone check (`phone_lab.PhoneCheck`, started from the camera page). It reads every
  documented route, then tries each candidate write once: the same value, then a small change and back. After each
  step it asks the app if it is still there and reads the phone's crash reports. It records 3 s on the phone, tries
  each lens, and tries Auto white balance. It carries on from the last run and never retries what crashed the app.
- **After an app update,** nothing is proven for the new version: the checklist asks for the Phone check, and the
  picture keeps working meanwhile.

## The checklist

`camera_doctor.checklist(facts)` is one pure function: the facts the bridge read (`StatusMixin.doctor_facts`) go in,
and the items come out. Each item has its plain words (`evidence`), the technical fact (`detail`), and, for the first
that fails, the fix and an action.

The camera page, the studio's Phone panel, its no-camera notice and Copy report all show this one list. Nothing is
guessed: a fact the bridge couldn't read is "not checked yet".

**Its `mode`** decides how the views show it (Will, 2026-10-13: switching a source "can't feel like you're causing a
bug"):

| mode | when | shown as |
|---|---|---|
| `live` | the picture arrives and nothing that stops it fails | the source and its link |
| `starting` | no first frame yet, within `STARTING_S` (`STREAM_STARTING_S` for Blackmagic's stream) | "Starting the camera", no fix, no red |
| `connect` | the first failing check is in `CONNECT_STEPS` (a step the user takes: `free`, `found`, `listed`, `app`, `link`, `other-chosen`, `other-connected`) | a guide from `_connect`: `headline`, numbered `steps`, `help` (folded), `waiting`; "Try connecting again" (Reset camera), the checks open as "Where it's up to" |
| `problem` | anything else that failed | the first failing check in red, its fix and its action |
| `off` | No camera is chosen (source `none`) and nothing plays | "No camera" and its `line`; no checks, no Reconnect or Reset. Nothing is captured, scanned or reset (`CameraBridge.no_camera` gates the capture, the monitor, the probe and the Continuity reset) |

**Where the iPhone stops** is two facts, not one:
- **`found`, "This Mac finds the iPhone":** it is on the cable (`continuity.iphone_on_usb`), or Continuity lists its
  microphone (`iphone_nearby`: "iPhoneWild Microphone" among the audio devices of the same listing). macOS lists the
  microphone whenever it finds the phone, also while it doesn't offer the camera (live 2026-10-13).
- **`listed`, "The iPhone offers its camera":** its camera is in the listing.

Each change of the first is a camera event (`nearby_said`), so a pasted report says where it stopped.

Adding a step the user takes: name it in `CONNECT_STEPS`, give `_connect` its guide for that place, and test both
(`test_camera_doctor.py`, "the guide says where the iPhone stops").

## The camera page and the Phone panel

`apps/studio/src/camera/` (TypeScript, Vitest) is one implementation:

| | |
|---|---|
| `bridge-api.ts` | the bridge's types and calls; every write sends its header; one error rule |
| `words.ts` | every word and shared rule |
| `checklist.ts` | the checklist view |
| `phone-controls.ts` | the phone's controls, built once and updated in place |
| `page.ts` | the camera page around them |

The studio's `phonepanel.ts` is the panel around the same two views. The page is built separately
(`vite.camera.config.ts` into `apps/studio/dist-camera`, by `npm run build` or `make camera-page`), so the bridge
serves it at `/camera/` with nothing else running.

## Adding a phone control

1. **Try it on a phone.** Add a probe to `phone_lab.WRITES`: its route, the body for the same value, one small change.
   The next Phone check tries just it, and the manifest records whether it is safe.
2. **The bridge:** a setter on `phone_control.Phone` and an action in `PhoneLink.act`. Read its state in
   `Phone.status` if the controls show it.
3. **The views:** the action's name in `bridge-api.ts` (`PhoneAction`), its control in `phone-controls.ts`, and any
   words in `words.ts`.
4. **Tests:**
   - the stand-in phone in `services/tests/test_camera_phone.py` answers the route, and a test drives the action;
   - `camera.test.ts` checks the control posts it;
   - `services/tests/camera_page_standin.py` answers it, so the page can be clicked through.

## What was measured (iPhone 17 Pro, Blackmagic Camera 3.5)

- **What the phone sends:** H.264 Main at 1920×1080 and 30 fps, a keyframe every second, no B-frames. The bridge
  scales it to the app's 1280×720.
- **Over Wi-Fi the frames arrive in bunches:** about 23 fps at the bridge, median gap 6 ms, p95 about 190 ms. No SRT
  buffer changes that; the phone hands frames over in batches. With the Standard quality on 2026-10-12, a frame gap
  p95 of about 50-70 ms ("slight stutter").
- **Over the cable:** a one-minute check read 29.9 fps (p95 39 ms).
- **The iPhone through Continuity Camera:** 30 fps, evenly.
- **Decoding the stream:** about 21% of one core in software. VideoToolbox broke on stream joins in this pipeline.
- **Qualities:** every profile of ours that named its resolutions HD or 4K crashed the app as its stream started.
  Blackmagic's own names (2160p, 1080p, 720p) don't, and the settings file offers one quality, Standard, in that form.
