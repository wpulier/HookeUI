# The camera bridge

The one program that opens the phone's camera. It turns the chosen live source into one feed of JPEG frames for the
app, keeps that feed alive, and says plainly why when there is no picture. Nothing else in the project opens the camera
(AGENTS.md).

`make start` runs it on port 8765, with everything else. Its camera page is <http://127.0.0.1:8765/camera/>, also
reached from the studio's header (Camera page, and the Phone panel). How it works inside is in
[ARCHITECTURE.md](ARCHITECTURE.md); the history behind the design is in [LEARNINGS.md](LEARNINGS.md).

## Connecting a camera

There are four live sources, chosen at the top of the camera page or in the studio's camera menu (the header's camera
button). A first run starts on No camera, and the choice is kept across restarts.

| | |
|---|---|
| **Blackmagic Camera app** | The free [Blackmagic Camera](https://apps.apple.com/app/blackmagic-camera/id6449580241) app (by Blackmagic Design) on the iPhone streams to this Mac. It records on the phone in full quality, its focus, exposure and colour can be held from here, and it has the phone's lenses. Over the cable when the phone is plugged in, otherwise over Wi-Fi: the bridge picks, and moves the phone by itself when that changes. |
| **iPhone** | Continuity Camera: the locked iPhone near the Mac, or plugged in. Set the target name below; the phone's focus and exposure are its own. |
| **Other camera** | Any other camera: one plugged into this Mac (a USB microscope camera, a webcam, a capture card), chosen by name from the cameras the Mac sees and captured as the iPhone is; or a camera that streams over the network, by its address (`rtsp://` for most network cameras, over TCP; `srt://`; `rtmp://`). The choice is kept. |
| **No camera** (a first run) | Pictures and videos only: open or drop them on the stage. Nothing connects and nothing searches for a camera; the app says "No camera", never that something is wrong. Choose a camera here whenever you want one. |

**Continuity Camera on your Mac:** the target must match the name macOS reports. While
the bridge is running, open **Other camera** to inspect the detected names (also listed
by `GET /api/cameras`); selecting one is not required. Then restart from your checkout
with your iPhone's exact name:

```sh
make start CAMERA_TARGET="Lab iPhone Camera"
```

Choose **iPhone** on the camera page. Keep the same `CAMERA_TARGET` on later starts,
or export it in the terminal before running `make start`. The direct bridge option is
`--target-device`. The legacy default `iPhoneWild Camera` is kept for existing local
setups; another phone needs its own name. This setting is only for Continuity Camera;
it does not configure the default Blackmagic stream or an Other camera selection.
The native capture helper retains **MicroscopeMind Camera** as its name in macOS
**Privacy & Security → Camera**.

**The Blackmagic Camera app, the first time** (about 5 minutes, once). The camera page walks through it one step at a
time, each with a "Stuck?" fold, and ticks each step as it sees it done:
1. Get the free app: a QR code opens it in the App Store.
2. Open it, allow the camera, microphone and Local Network, and turn on Settings › Network Access › HTTP Server.
3. Scan a second QR code to download ObservationUI's settings file (`MicroscopeMind.xml`, its retained compatibility filename), then in the app: Settings › Live Stream ›
   Platform › Import Stream Settings.
4. Choose Platform MicroscopeMind (ObservationUI retains the earlier platform name for existing phone settings) and Quality Standard. The picture then comes in by itself, every time the app is open.

The studio's Phone panel points to this walkthrough until it is done.

**When there is no picture,** the camera page says which of three it is, and never shows red for the first two:
- **Starting:** a camera just chosen gets a few seconds (10, or 20 for Blackmagic's stream) before anything is called
  wrong. The page says "Starting the camera".
- **Not connected yet:** a guide, not a fault. Its headline says where it stops, its steps are numbered, and it waits
  ("Waiting for the iPhone…"). For the iPhone it tells three places apart:
  - **This Mac can't find the iPhone** (no cable, nothing of it listed): "Connect your iPhone", the cable first.
  - **It finds the iPhone but not its camera** (Continuity lists the phone's microphone but no camera): lock it, close
    any app using the camera, turn Continuity Camera off and on.
  - **An app has the camera** (Blackmagic Camera open, or Desk View): close it.

  **Try connecting again** is under the steps, and "Not connecting?" folds what is rarer. The checks are open as
  "Where it's up to": what passed, a ring on the step it waits on, the rest dimmed.
- **Something broke** (a frozen picture, the wrong size, a crash, camera permission refused): the first failing check
  in red with its fix and a button where the bridge can do it itself.

Every check is one line behind "All 11 checks pass". Hovering a check shows the technical fact behind it.
- **Reconnect** asks the camera for its picture again. Try it first.
- **Reset camera** restarts the link: Blackmagic's stream from the phone, or Continuity Camera for the iPhone.
- **While a button works** it says so ("Resetting…" with a spinner), then what it did, beside it, for a few seconds.
- **Copy report** gives the checks and recent events as text, to paste to whoever is helping. The events say where the
  iPhone stopped when that changes ("macOS finds the iPhone, but doesn't offer its camera").

**The phone's controls** (Blackmagic only, on the camera page and in the Phone panel):
- **Taking:** Record on the phone, and Photo (the picture as it is now, saved on this Mac beside the recordings).
- **Lens:** the phone's lenses and the zoom within one.
- **Focus and exposure:** Hold (focus and exposure stay where they are, at 30 fps), autofocus, focus once, ISO, shutter.
- **Colour:** white balance with presets and Auto (set once from the view), and tint.
- **Camera:** frame rate and stabilization.
- **What is offered:** only what the **Phone check** proved safe on that version of Blackmagic Camera. The check runs
  from the camera page (about 10 minutes the first time, then only what is new), and again after the app updates.

**Smoothness:** beside the picture, each line of the strip is a frame arriving over the last 5 seconds. It is
*smooth* when a frame comes every 33 ms, *slight stutter* or *stutters* when some wait longer ("1 in 20 frames 35 ms
late"). Wi-Fi bunches frames more than the cable or the iPhone. **Check smoothness (1 min)** keeps a minute of it, and
**Compare** sets sources side by side.

**Test footage** replaces the camera without touching the phone: pick a recording in the studio's camera menu, or
start the bridge with `--simulate VIDEO`. See `docs/simulation.md`.

## For developers

**Looking at the camera page with no camera or phone:** `make camera-page`, then
`python services/tests/camera_page_standin.py --state live|busy|fresh|setup|connect|iphone|other|other-none|down` and open
<http://127.0.0.1:8897/camera/>. The page's code is `apps/studio/src/camera/`, shared with the studio's Phone panel.

**Adding a phone control:** see ARCHITECTURE.md, "Adding a phone control".

### Endpoints

Every write carries a header (`X-MicroscopeMind`), so another web page can't drive the camera; the route table in
`scripts/bridge_routes.py` names each one.

| | |
|---|---|
| `GET /camera/` | the camera page (its build, `apps/studio/dist-camera`; `/` and the old `/web-camera-check.html` redirect here). Nothing else of this folder is served |
| `GET /iphone.mjpeg` | the live frames (multipart JPEG); each part carries `X-Frame-Seq` and `X-Frame-At`, and the response carries `X-Bridge-Boot` |
| `GET /snapshot.jpg` | the latest fresh frame |
| `GET /api/health` | state, source (`sourceKind`, `source`, `sourceChoices`), `diagnosis` in plain words, `cadence`, the phone as last read (`phone`), recording |
| `GET /api/doctor` | the checklist, its recent events and the report (`?quiet=1`: without asking the phone more often) |
| `GET /api/cadence` | when each frame of the last five seconds arrived |
| `GET /api/preflight` | a fresh device scan, then health |
| `GET /api/source`, `POST /api/source?kind=continuity\|blackmagic\|other` | the sources and choosing one (`camera-source`; `wifi` and `usb` are old names for `blackmagic`; `other` takes `device=` or `url=`, else the one it had) |
| `GET /api/cameras` | the cameras this Mac sees, for Other camera's picker (a device scan: only when the picker asks) |
| `POST /api/restart` | Reconnect; reconnects asked at the same moment share one |
| `POST /api/restart?reset=1` | Reset camera (`camera-control`) |
| `GET /api/phone`, `POST /api/phone?do=&value=` | Blackmagic Camera's state and controls (`camera-control`): `lock`, `autofocus`, `focus-once`, `focus`, `exposure`, `iso`, `shutter`, `white-balance`, `white-balance-auto`, `tint`, `fps`, `zoom`, `lens`, `stabilization`, `record`, `start`, `stop` |
| `POST /api/photo` | the newest frame saved, exactly as it arrived, beside the recordings (`camera-control`) |
| `GET /api/phone-check`, `POST /api/phone-check` | the Phone check's progress, and starting it (`camera-control`) |
| `GET /api/blackmagic.xml`, `GET /api/settings-qr.svg` | the settings file, and a QR code for it served to the phone by the settings server (port 8767, that file only) |
| `POST /api/trial?seconds=`, `GET /api/trials` | smoothness checks (`camera-source`) |
| `GET /api/record/status`, `POST /api/record/start`, `POST /api/record/stop` | the camera recording, with its frame sidecar (`docs/datasets.md`) |
| `GET /feed`, `POST /feed`, `/feed/pause`, `/feed/seek` | test footage, a session's recording (`source=recording`, from `--datasets-dir`) or a file under a media root (`source=asset&path=`) |
| `GET /assets/library`, `GET /assets/thumb`, `POST /assets/upload` | the footage folder's recordings and pictures, their thumbnails, and adding one (`add-footage`, from a local page) |
| `/sim/*` | the simulated camera's faults and ground truth, for the scenario harness only |

Each recording has a sidecar, `X.frames.jsonl`: one line per frame with the bridge's `seq` and time. Its header
records the source.

### Make targets

```text
make start                    everything, the bridge included
make camera-source KIND=blackmagic   choose the source (continuity or blackmagic)
make camera-reconnect         Reconnect
make camera-health            the bridge's health
make camera                   the bridge alone, without the app (builds the camera page first)
make camera-page              build the camera page only
make camera-controls          what Continuity Camera lets the Mac control (nothing: focus, exposure and white balance are the phone's)
```

### Files

| | |
|---|---|
| `scripts/iphone_bridge_server.py` | the bridge: its entry point, the capture's lifecycle and the live state (the source, test footage) |
| `scripts/bridge_routes.py` | the HTTP server and its route table (which controls need which header), and the camera page |
| `scripts/bridge_monitor.py` | the capture's frame reader and the monitor that keeps it running and heals it |
| `scripts/bridge_status.py` | health, the diagnosis, the checklist's facts, smoothness, source checks, the Phone check |
| `scripts/bridge_recording.py` | the camera recording and its per-frame sidecars |
| `scripts/bridge_sources.py` | the live sources' names and the choice kept across restarts |
| `scripts/bridge_feed.py` | what the feed may play besides the camera: a session's recording, a file under a media root |
| `scripts/continuity.py` | what only the Mac can say about Continuity Camera: the device list, Desk View, USB, the agent's reset |
| `scripts/settings_server.py`, `scripts/qr.py` | the Blackmagic settings file, the small server the phone fetches it from, and its QR code |
| `scripts/bridge_common.py` | what those share: paths, FFmpeg and its stderr, and the view of the bridge's live state |
| `scripts/stream_source.py` | the SRT/RTMP stream as a source (the three device touch points, secrets redacted) |
| `scripts/phone_control.py` | Blackmagic Camera's control API, and the phone link that keeps its stream aimed at this Mac |
| `scripts/phone_lab.py`, `blackmagic-api.json` | the Phone check, and what it proved safe on each Blackmagic Camera version |
| `scripts/camera_doctor.py` | the checklist, from the facts the bridge read |
| `scripts/simulated_camera.py` | test footage as a source |
| `scripts/footage_media.py` | the footage library, its thumbnails and lengths |
| `capture-app/` | the iPhone capture app (Swift), its build and run scripts |
| `scripts/probe-camera-controls.sh` | asks AVFoundation which controls Continuity Camera exposes |
| `apps/studio/src/camera/` | the camera page and the Phone panel's checklist and controls (one implementation) |

**Tests:**
- `services/tests/test_bridge.py`: the capture, recording, footage and assets.
- `test_camera_link.py`: sources, the iPhone, and a real SRT stream into a real listener.
- `test_camera_phone.py`: a stand-in Blackmagic Camera with the real app's quirks and crashes.
- `test_camera_doctor.py`: every failure met live, the endpoints, and the page served alone.
- `apps/studio/src/camera/camera.test.ts`: the page's and panel's words, wire and controls.
