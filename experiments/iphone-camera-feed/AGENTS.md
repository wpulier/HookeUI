# The camera bridge

Part of the working context the root `AGENTS.md` maps. "Above" and "below" may mean parts it lists in
other files.

- The camera bridge (`experiments/iphone-camera-feed`; for users `README.md`, for developers `ARCHITECTURE.md`; ADR
  0023) is the only thing that opens the iPhone, through its own capture app (`capture-app/`: macOS wakes the iPhone
  only for an app). Nothing else may use AVFoundation, FFmpeg capture or `cv2.VideoCapture` on the device. It runs on
  the system Python 3.9, standard library only, in modules beside `iphone_bridge_server.py` (the routes, the monitor,
  the recorder, status...); a test replaces names on the main module, so the others reach it through `self.main`.
  - **Four live sources** (`POST /api/source?kind=continuity|blackmagic|other|none`, `make camera-source KIND=`, kept in
    `~/MicroscopeMind/camera-source.json`). A first run (no saved choice) starts on No camera; a saved choice wins:
    - the iPhone through Continuity Camera. A phone listed but silent is asked again, backing off 30 s to 5 min, and
      Continuity Camera is reset only when the phone leaves the list. Byte-identical frames for 3 s are frozen, and
      get a reset at most once a minute;
    - Blackmagic: the app's SRT stream into one listener (`stream_source.py`). It picks its own link
      (`phone_control.link_for`): the cable when it shares a network, otherwise Wi-Fi. The phone moves by itself when
      the cable comes or goes. `wifi` and `usb` are old names for it;
    - Other camera: any camera this Mac sees, chosen by name (`GET /api/cameras`) and captured by the same capture app,
      or a stream address (`rtsp://` over TCP, `srt://`, `rtmp://`). Continuity Camera is never reset for it, and
      the phone's stream stops while it is chosen;
    - No camera (Will, 2026-10-15): pictures and videos only. Nothing is captured, scanned or reset
      (`CameraBridge.no_camera`); the bridge's state is `no_camera`, its diagnosis none, the checklist's mode `off`, the
      orchestrator's feed state `no_camera`, and a tool with no picture says "no camera is in use" (`frames.no_picture`),
      never a camera to fix. With nothing on stage the studio shows the empty stage; the header's control reads "No
      camera". The header's camera control only ever opens its menu (titled Camera, Open camera setup at its top, the
      Source switch labelled, what is on stage with Switch to live camera); going live is never one click on it.
  - **A stream is neither footage nor a device:** like test footage it replaces only the three device touch
    points, and nothing downstream can tell.
  - **The phone link** (`phone_control.PhoneLink`, its own thread, never on a request path) keeps Blackmagic's stream
    aimed at this Mac and started, and reads the phone's crash reports (devicectl). A quality that crashed the app as
    its stream started is never started again (`UNSAFE_QUALITIES`, `crashedQualities`); the settings file offers one,
    **Standard**.
  - **Only proven writes reach the phone** (`blackmagic-api.json`, per app version; `Phone.put`). The camera page's
    **Phone check** (`phone_lab.py`) proves them on the phone, once and again after an app update, and never retries
    what crashed the app.
  - **The camera page** (`/camera/`, built from `apps/studio/src/camera/`; the bridge serves it and its own routes,
    nothing else of its folder) and the panel of the studio header's camera control (`phonepanel.ts`) are one
    implementation:
    - **The checklist** (`/api/doctor`, `camera_doctor.py`): facts read, never guessed. The first failing one has its
      fix and a button. Reconnect, Reset camera and Copy report are always there. It is in plain words, with the
      technical facts as hovers and in the report.
    - **Not connected yet is never a fault** (Will, 2026-10-13; the checklist's `mode`, ARCHITECTURE.md "The
      checklist"): a camera just chosen is "Starting the camera"; one waiting on a step from the user is a guide with
      its headline, numbered steps, "Try connecting again" and the checks open as "Where it's up to". Red is only for
      what broke. The iPhone's guide says where it stops: not found, found without its camera (Continuity lists its
      microphone), or an app holding the camera. A button that runs says so ("Resetting…"), then what it did. No camera
      chosen is mode `off`: its headline and one calm line, no checks, Reconnect or Reset.
    - **The phone's controls:** Record on the phone, Photo (the newest frame saved on the Mac), lens, zoom, Hold,
      focus, exposure, white balance (presets and Auto, set once from the view), tint, frame rate and stabilization.
      Only what the Phone check proved is offered.
    - **First-time setup**, for someone who has never installed an app from a QR code: four steps on the camera page,
      one open at a time, each ticking itself from the checklist's facts and with a "Stuck?" fold. Get the app (a QR
      code to the App Store; "I have the app" is the one step the Mac can't see); turn on HTTP Server (and allow Local
      Network); a QR code that fetches the settings file from a small server on the channel's settings port (Stable 8767, Beta 8867); MicroscopeMind and Standard.
      "Blackmagic Camera" always means the app on the iPhone, never a Blackmagic camera.
    - **Smoothness:** said as smooth, slight stutter or stutters.
- Test footage (`docs/simulation.md`) replaces only the bridge's three device touch
  points, at startup (`--simulate VIDEO`, used by the scenario harness) or at runtime
  (`/feed`, used by the studio's camera menu). The orchestrator, the models, the agent and
  the logs must never detect or special-case it; they have no code that mentions it. The
  studio's camera menu and footage badge talk to the bridge's `/feed` directly. Ground
  truth for tests lives only on the bridge's `/sim/*` endpoints. Run `make sim-test`
  after changing the bridge, orchestrator, scene or studio drawing.
  - **A session's own recording plays the same way** (`docs/simulation.md`): the bridge reads the app's datasets
    (`--datasets-dir`, from `python -m orchestrator.paths datasets` in `scripts/studio.sh`; the sim passes its own),
    and `POST /feed?source=recording&dataset=<id>&part=<n>[&paused=1][&at=<s>]` plays a Record dataset's camera video
    (`video/part-<n>.mp4`, only inside that folder) into the feed, held at its place from its first frame when
    `paused`. `GET /feed` calls it footage (`recording:<id>/<n>`, titled by the dataset's name or start). Test footage
    takes `paused` and `at` too.
  - **The studio decides what plays; the orchestrator never knows:** it only lists a session's recordings
    (`GET /api/sessions/{sid}/recordings`, `datasets/recordings.py`, by the index's dataset rule), never which one
    is playing.
  - **The user's own recordings and pictures are the exception, and are known (ADR 0022).** The server plays them as a
    session's sources:
    - **How:** `POST /feed?source=asset&path=<absolute path>&title=…[&paused=1][&at=<s>]`, any file under a media root
      (`--media-root`, repeatable: the app's snapshots and results; the datasets, footage and explicitly configured recording folders count too). Browser MP4/WebM recordings use this same path; a link or traversal outside every allowed root still refuses;
    - **Pictures** play as one held frame at the bridge's rate, fitted and padded, never stretched;
    - **Uploads** take pictures as well as videos.

    Test footage still stands for the live camera, and nothing downstream knows it.
- **A switch says where its time went** (L1c, 2026-10-20). Each `POST /feed`, `/feed/seek` and play after a pause
  answers with `steps`, the milliseconds of each step in order, and the bridge's log says them on one line:
  - `find`: the file checked (a footage name through the library's listing);
  - `probe`: the file opened and its rate and frames read, before the running capture is touched;
  - `note`: the sidecar's line;
  - `stop`: the old capture stopped;
  - `snapshot`: the device listing;
  - `launch`: the new FFmpeg started;
  - `firstFrame`: its first frame arrived;
  - `state`: the answer built, then `total`.
- **Media and switch options** (`iphone_bridge_server.py` flags, each also set by an environment variable). Each was
  measured against the earlier way by a bridge-only bench on a quiet Mac (its numbers are in the L1c PR):
  - `--media-pace follow|one` (`MICROMIND_MEDIA_PACE`), default `follow`: thumbnails and lengths run up to eight at
    once while the bridge makes its frame rate. Below 95% of it, one at a time, as `one` always runs (never none: a camera a little under `--fps` still gets its
    lengths and thumbnails);
  - `--probe-cache on|off` (`MICROMIND_PROBE_CACHE`), default `on`: a played file's rate and frame count are kept by
    its path, inode, size and modification time;
  - `--file-stop kill|grace` (`MICROMIND_FILE_STOP`), default `kill`: a file played as the camera is killed at once
    for a switch or seek. Its reader has stopped reading, so FFmpeg never honours SIGTERM, and `grace` waits 0.2 s
    every time;
  - `--upload-priority normal|background` (`MICROMIND_UPLOAD_PRIORITY`), default `normal`: `background` copies an
    upload on a thread of its own at background priority. The upload never slowed the feed, and in the background
    it took longer.
- Every camera recording has a sidecar beside it, `X.frames.jsonl`, written by the bridge
  (`docs/datasets.md`): one line per frame with its index in the video, the bridge's frame number
  (`seq`, also sent as `X-Frame-Seq` on each `/iphone.mjpeg` part; `bootId` in `/api/health`), when
  the bridge took it (`bridgeAt`) and where it came from (`src`: camera or footage, said by the bridge
  only), plus feed switches, seeks and capture restarts. The video's own timestamps are a steady fps,
  not times; the sidecar is the clock.

The checklist's connected-reader check establishes only that a viewer is connected;
it does not establish that a frame arrived or was displayed. Frame arrival/freshness
are separate checks. Studio recovery uses the selected source's instructions; a
waiting Blackmagic/Other stream must never tell the user to lock for Continuity.

A decoder reporting an unsupported stream protocol is an installation failure, shown before
phone setup advice in health and the checklist. Reconnecting cannot fix it. Bonjour policy
refusals are retained as a Mac-side discovery issue with Local Network recovery; they are
not reported as a phone that needs opening. A fresh picture still establishes reception
when camera controls are not yet discovered.

Camera discovery keeps each Bonjour operation alive for eight seconds on its background
thread so a native Local Network prompt has time to appear (Apple TN3179 / FB16131937).
The phone snapshot includes bounded discovery stage/count/timing facts without network
identifiers. Finding a service but failing its control request is distinct from finding
no service. Reset reports partial failure when only the Mac receiver restarted; it must
not claim the phone restarted unless its control request succeeded.

Restarting the receiver and restarting the phone are separate outcomes. A phone
restart that was refused must retain its recovery error and must not log success.

Media preparation uses at most two background jobs during healthy capture, one
while capture is starting or below its cadence target, and the full existing pool
without capture. A healthy recent frame rate is not permission for an eight-job
burst; thumbnails must not displace live frames. Existing cadence limits remain.

Requested thumbnails may use the healthy capture pool; the two-job cap applies to
speculative prefetch only. Slow capture still constrains both to one. Requested
pictures must not time out behind speculative probes.

Bonjour resolution preserves the whole service instance name, including spaces and
apostrophes. A phone's last name word alone is not its advertised service identity.

The native launcher supplies `MICROMIND_BLACKMAGIC_PORT` and
`MICROMIND_CAMERA_SETTINGS_PORT` from its release channel. They select the default
listener and the settings/QR server together without forcing a source choice.
Early Beta's exact legacy default listener is rehomed to its channel on load;
explicit custom streams and the selected source are preserved.

Browser recordings may use variable frame timing. Playback uses a valid declared video duration
(container duration when the stream omits it), deriving timeline ticks from packet count;
missing/invalid duration keeps the nominal-rate fallback. This preserves the seek range and
pause/resume timeline. It does not qualify exact native frame indices for variable-rate media.
