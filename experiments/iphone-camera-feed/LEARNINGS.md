# iPhone Camera Feed Learnings

History, in the order it was learned, kept as written. Much of it has been superseded since (the FFmpeg capture by the
capture app, three sources by two, Blackmagic's pinned links by its own choice, the old console by the camera page at
`/camera/`). What is true now is in [README.md](README.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## Final Working Architecture

```text
iPhone Continuity Camera
-> macOS AVFoundation device: iPhoneWild Camera
-> persistent FFmpeg capture owned by local Python server
-> latest JPEG frame cache
-> /iphone.mjpeg for browser preview
-> /snapshot.jpg and /api/health for CV/debugging
```

The current robust server can be started from the project root with:

```bash
make camera
```

Open:

```text
http://127.0.0.1:8765/web-camera-check.html
```

## What Went Wrong

### 1. Browser camera permission was a trap

The first web page used browser camera APIs. That made Chrome/Codex ask for camera permission and show a camera picker, but the picker only exposed:

```text
FaceTime HD Camera
```

Meanwhile FFmpeg could see:

```text
FaceTime HD Camera
iPhoneWild Camera
iPhoneWild Desk View Camera
```

So the iPhone was available to macOS, but not exposed to that browser/WebRTC context. The browser route could silently select the Mac webcam unless blocked.

### 2. The iPhone prompt was OS-level

When the iPhone briefly said connected, that was macOS/Continuity Camera responding to AVFoundation access from FFmpeg. It was not the same as the browser permission prompt.

The first wake script opened the iPhone camera for only a few seconds, then closed it. That is why the phone showed connected briefly and then disconnected.

### 3. Per-browser stream ownership was fragile

The first server bridge opened FFmpeg per `/iphone.mjpeg` browser connection. That worked, but it meant the camera lifecycle depended on the browser image request.

That is too fragile for computer vision work.

### 4. MJPEG image `load` events are not reliable enough

The browser requested `/iphone.mjpeg`, and the server was streaming frames, but the UI waited for an image `load` event that may not fire predictably for an endless MJPEG stream.

The UI now treats stream subscription and decoded frame metrics separately.

### 5. A cached JPEG can masquerade as a live feed

After the phone-side stream was intentionally closed, the old bridge could keep
serving the last cached JPEG while status counters kept moving. That made the
system look half-connected even though no fresh iPhone frames were arriving.

The bridge now clears the frame cache whenever a reconnect starts, refuses to
serve stale `/snapshot.jpg` frames, and only marks the preview live after fresh
frames arrive after the latest request.

## What Fixed It

### Server owns the iPhone

The browser no longer calls `getUserMedia()` and no longer asks for camera permission.

The local Python server opens exactly:

```text
iPhoneWild Camera
```

using FFmpeg. If that device is missing, the web console still starts in
recovery mode and reports `missing`; FFmpeg is not launched until a reconnect
request finds the target device.

### No FaceTime fallback

There is no camera picker in the working bridge. The only accepted source is the target AVFoundation device. `FaceTime HD Camera` cannot be selected by accident.

### Persistent capture loop

The server now starts one long-lived FFmpeg process and keeps it running:

```text
FFmpeg -> JPEG frame parser -> latest frame cache
```

The browser subscribes to the frame cache. Hiding the preview does not close the camera.

### Explicit reconnect state machine

The server monitors frame age. If FFmpeg exits or frames stop arriving, it restarts capture.

The page also has `Reconnect iPhone`, which calls:

```text
POST /api/restart
```

That endpoint rescans AVFoundation, clears the old frame, starts a fresh FFmpeg
capture request, waits for new frames, and returns the actual result:

```text
device_missing
request_sent_waiting
capture_exited
live
```

Manual reconnect now includes a hard recovery stage. When `iPhoneWild Camera`
is missing, the bridge stops stale FFmpeg, resets macOS's
`ContinuityCaptureAgent`, waits for the camera to reappear, and only then sends
the FFmpeg request. This fixed the case where the iPhone had just worked, was
intentionally disconnected, stayed plugged in, but macOS did not re-advertise
the camera until Continuity Camera was reset.

Useful endpoints:

```text
GET  /api/preflight
GET  /api/health
GET  /api/sources
POST /api/restart
POST /api/record/start
GET  /api/record/status
POST /api/record/stop
GET  /snapshot.jpg
GET  /iphone.mjpeg
```

## Reconnect Postmortem

The reconnect failure was not caused by USB, the iPhone being unlocked, or the
browser preview. It was caused by two software gaps in our bridge:

1. The browser preflight returned early when `iPhoneWild Camera` was missing.
   That meant clicking `Connect iPhone` never called `POST /api/restart`, so no
   fresh AVFoundation request was actually sent to the phone.
2. The server monitor kept trying to start FFmpeg while AVFoundation did not
   list `iPhoneWild Camera`. Those attempts could not trigger the phone because
   macOS had no iPhone camera device to open.

The fix was to make manual reconnect a hard recovery path:

```text
Connect / Reconnect
-> stop stale FFmpeg
-> clear cached frames
-> reset macOS ContinuityCaptureAgent
-> wait for iPhoneWild Camera to reappear in AVFoundation
-> start a fresh FFmpeg request
-> require fresh frames before reporting live
```

The verified recovery after an intentional disconnect was:

```text
Before: state=missing, seen=[FaceTime HD Camera, Capture screen 0]
Reconnect: ContinuityCaptureAgent reset ok
After: iPhoneWild Camera reappeared, FFmpeg started, fresh frames received
Doctor: READY FOR LIVE INPUT
```

## Connect-time Hardening (2026-10-04)

An audit before a live demo found the bridge fighting itself while the phone was slow
to answer. Observed on the real phone: a capture request can take well over 5 s to
deliver its first frame (or, with the phone not ready, none at all), and FFmpeg stuck
in AVFoundation ignores SIGTERM and needs SIGKILL.

- **Restart churn.** The monitor killed any capture without a first frame after 5 s, and
  every new `/iphone.mjpeg` subscriber called `restart()` while the bridge was not live,
  killing captures mid-connect (6 starts in 19 s; 9-11 per minute measured with the
  phone not answering). Now a new capture gets `--first-frame-timeout` (15 s) for its
  first frame; the 5 s `--stall-timeout` applies once frames have flowed; a subscriber
  only starts a capture when none is running, never restarts one (4 per minute).
- **Busy-spinning streams.** With no capture running, `wait_for_frame` returned at once
  and `/iphone.mjpeg` looped on it, a CPU per subscriber, never noticing the client left.
  It now waits on the condition, and a stream ends after `--stream-idle-timeout` (10 s)
  without frames so the client reconnects and threads do not pile up.
- **Health blocked by a dying FFmpeg.** Stopping FFmpeg (terminate, wait 2 s, kill, wait
  2 s) happened under the lock `health()` needs. The process handle is now swapped out
  under the lock and waited for outside it; a separate `lifecycle` lock serializes every
  stop and start, so two captures of the phone still cannot overlap. Frames from a
  capture that has been swapped out are discarded.
- **FFmpeg's own words.** Its stderr was discarded. The last lines of each capture are in
  `/api/health` as `ffmpegStderr`, the previous capture's end as `lastCaptureExit`
  (reason, exit code, stderr), and `lastError` now carries FFmpeg's last line when it
  fails and stays set until frames flow again. The first 40 lines per capture are logged.
  The recorder's stderr feeds `recording.error` the same way.
- **Recovery without Reconnect.** The monitor now runs from startup (also with no phone).
  While the phone is missing, or the capture has failed, it rescans the device list every
  5 s (`--rescan-interval`) and starts a capture as soon as the phone is listed. After 10 s
  missing it resets ContinuityCaptureAgent itself, at most once per 30 s, backing off to
  once per 5 minutes, and never within 30 s of any earlier reset (manual ones included).
  It stands back while a manual reconnect or feed switch runs.
- `/feed?probe=1` now updates the device state it scanned, so `devicePresent` follows at
  once (it lagged ~10 s).
- The orchestrator no longer reports `bridge_offline` on one missed health call: fresh
  frames mean live, and offline needs 3 consecutive failed health calls.

Tests: `services/tests/test_bridge.py` (fake FFmpeg processes) and the `live`, `freeze`,
`crash`, `unplug`, `unplug-gone` scenarios of `make sim-test`.

## Robust Operating Rules

- Start only one owner of `iPhoneWild Camera` at a time. The bridge server should be the owner during web/CV experiments.
- Do not run `preview.sh` at the same time as the persistent bridge unless you are intentionally testing contention.
- Use USB for serious microscope/CV work.
- Keep the iPhone powered, locked, mounted, and nearby after it has been
  unlocked once since restart.
- If the frame is black, the bridge may still be healthy; check lighting, lens cover, phone orientation, and whether the rear camera is aimed at the subject.
- If `/api/health` reports stale frames, click `Reconnect iPhone`.
- If operating from the terminal, use `make camera-reconnect`; it runs the same
  hard reconnect endpoint as the UI.
- If the server says `missing`, lock and mount the iPhone. If macOS still does
  not list it, plug it into the Mac and approve Trust/Allow prompts.
- If you need a recording, use the bridge recorder rather than opening the
  camera in a second app.
- Do not assume manual focus/exposure lock is available through Continuity
  Camera. Probe it with `probe-camera-controls.sh`.
- The phone's autofocus still runs and hunts (racks back and forth) on blank or faint
  views, and nothing about its lens reaches the bridge: FFmpeg's AVFoundation input gives
  pictures only, and macOS has no `lensPosition` for any camera (iOS only). The orchestrator
  tells a hunt from the focus knob by the eyepiece disc's size instead (the phone's lens
  breathes it by 1-2.4%, the knob never moves it; `services/orchestrator/coach.py`). The probe
  prints `focus_mode` and `adjusting_focus` too, unmeasured on the phone so far: if macOS
  reports `adjusting_focus` for Continuity Camera, the bridge could carry it as a fact.

## Build Path From Here

Use the local bridge as the stable camera ingress layer for new work:

```text
Camera ingress:
  iPhoneWild Camera -> local bridge server

Browser preview:
  /iphone.mjpeg

Computer vision snapshots:
  /snapshot.jpg

Runtime health:
  /api/health
```

For Python CV, there are two good options:

1. Read `iPhoneWild Camera` directly with OpenCV when you want lowest overhead.
2. Read `http://127.0.0.1:8765/snapshot.jpg` when you want a stable HTTP boundary shared with browser tools.

For TouchDesigner, first try `iPhoneWild Camera` directly in `Video Device In TOP`. If that is unstable or unavailable, use OBS as a router or consume the bridge stream where supported.

For future project/product IP, keep this bridge as a reusable module: strict device selection, health checks, manual reconnect, stale-frame rejection, and no browser camera fallback.

For audience reliability, do not treat Apple Continuity Camera discovery as the
only ingress. The production shape needs a ready fallback, such as Camo, OBS
Virtual Camera, or a hardware capture path. The bridge can guarantee source
truth and stale-frame failure, but it cannot force macOS to advertise an iPhone
that the OS has removed from AVFoundation.

## Feature Capability Summary

Implemented:

```text
Persistent iPhone capture
Auto-restart on stale frames
Manual reconnect
Auto-scan and auto-connect when the iPhone device reappears
Hard reconnect through macOS ContinuityCaptureAgent reset
Browser MJPEG preview
Latest-frame snapshot endpoint
Stale-frame rejection
Source manifest endpoint
Pre-show doctor
Video-only MP4 recording
```

Not exposed by current `iPhoneWild Camera` device:

```text
focus lock
focus point
exposure lock
custom exposure
exposure point
white-balance lock
```

Those controls require a different control layer, such as Camo or a custom iOS
capture app.

## 2026-10-11: listed but silent, and the second way in

The iPhone stayed in macOS's device list but never sent a frame. The bridge asked 19 times in 4 minutes and
the phone showed nothing. `killall ContinuityCaptureAgent`, run once by hand, brought the picture back at
once. What we learned:

- **A listed phone can be silent.** The bridge used to reset Continuity Camera only when the phone vanished
  from the list, so it never healed this case, and neither did Reconnect. Now a capture with no first frame
  after `--first-frame-timeout` (20 s) is healed: the capture is stopped, Continuity is reset, the bridge waits
  for the phone to be listed again, then asks again. Heals back off (30 s, doubling to 5 min) and start over at
  the first frame. Reconnect does the same once the phone has been silent for `--silent-after` (6 s).
- **15 s was a race.** One real first frame took 14.3 s, so the first-frame wait is now 20 s.
- **We fought ourselves.** The console reconnected every 3 s, two reconnects could start two captures in the
  same second, and every probe ran another FFmpeg device listing while a capture waited.
  Continuity logged `Invalid deviceControlPID`. Now probes reuse the last scan while a capture runs,
  reconnects asked at once share one, and the console never reconnects while a capture is asking.
- **"Claude is using the camera" is the bridge.** macOS credits camera use to the app that started the
  process. A studio started from the Claude desktop app shows as Claude; one started in Terminal shows as
  Terminal. Clicking the iPhone row in that menu opens Desk View, the distorted top-down view, which then
  holds the phone's camera. The bridge's diagnosis says to quit it.
- **The bridge says why.** `/api/health` has a `diagnosis`: its code, text and steps, shown by the console
  and by the studio's notice.

The second way in is the phone app stream. The free Blackmagic Camera app (3.2 or later) streams SRT over
Wi-Fi to the bridge's listener, with manual focus, exposure and white balance on the phone. Choose it under
Live Source in the camera console, or with `make camera-source KIND=stream`. The choice is kept in
`~/MicroscopeMind/camera-source.json`. Setup is in [README.md](README.md).

## 2026-10-11 (later): Blackmagic Camera, measured on the phone

- **Three sources now, in one bridge:** iPhone (Continuity), Blackmagic over Wi-Fi, Blackmagic over USB (Personal
  Hotspot). Wi-Fi and USB share one SRT listener, so switching between them never restarts the capture.
  [README.md](README.md) has the setup.
- **The app's HTTP Server is Blackmagic's camera-control REST API** (`https://<phone>:4444/control/api/v1`,
  announced over Bonjour with `capabilities=cameraControl`). It confirmed autofocus was on "Continuous": the
  hunting.
- **What works through it:** autofocus off (`{"enabled": false}`), auto-exposure off, ISO, shutter, white balance,
  frame rate (the whole `/system/format` sent back), and starting the stream.
- **What breaks or does nothing:**
  - a "Custom" server URL killed its HTTP server;
  - a server it doesn't list left the stream "Interrupted";
  - a settings file sent over the network was accepted and ignored;
  - a stop-and-start every 12 s kept the stream from connecting.

  The bridge sends none of these.
- **Over Wi-Fi the phone hands frames over in batches:** about 23 fps at the bridge, median gap 6 ms, p95 193 ms.
  No SRT buffer size changed that. Over USB: to be measured.
- **VideoToolbox decoding broke on stream joins** (-12909, grey frames), so decoding is software: about 21% of one
  core.
- **Live Stream greyed out in the app** means Remote Camera Control is on.

## 2026-10-12: why the iPhone kept going silent

The root cause, found at last: **macOS wakes the iPhone's Continuity Camera only for an app with its own identity.**
- **FFmpeg is anonymous.** ContinuityCaptureAgent logs it as "missing bundleID", and it got frames only while
  something else (FaceTime, Desk View, an earlier session) kept the phone awake. That is why the picture worked, then
  died after restarts, and came back after someone opened Desk View.
- **Resetting Continuity Camera was the wrong remedy.** It could end a session the phone had just started.
- **The proof, side by side:** FFmpeg got 0 frames in 40 s; a 60-line Swift app got its first frame in 1.5 s, then
  30.1 fps.
- **The fix:** the bridge now captures the iPhone through that app ([capture-app/](capture-app/README.md)), and a
  silent phone is asked again with no reset.
- **Launch path matters:** started directly as a child process, the app loses its identity (camera access false, 0
  frames). Started through `open`, it keeps both.
