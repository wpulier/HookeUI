# MicroscopeMind Camera (the bridge's iPhone capture)

A small Mac app that captures the iPhone through Continuity Camera and hands its frames, as JPEGs, to the camera
bridge.

## Why an app

macOS wakes the iPhone's Continuity Camera only for an app with its own identity, as it does for FaceTime or Desk
View. A bare command-line capture such as FFmpeg is an anonymous client (`missing bundleID` in ContinuityCaptureAgent's
log). It only gets frames while something else keeps the phone awake, so the picture died after restarts and came
back for no clear reason.

Measured on 2026-10-11, with the same phone, side by side:

| Capture | First frame | Frame rate |
|---|---|---|
| FFmpeg | none in 40 s | — |
| This app | 1.5 s | 30.1 fps, gaps p50 33 ms, p95 40 ms |

## How it runs

- **Built by** `build.sh`. `make start` runs it, and it rebuilds only when `main.swift` or `Info.plist` change. macOS
  ties the camera permission to the app, so you allow "MicroscopeMind Camera" once in System Settings › Privacy &
  Security › Camera, or click Allow when asked.
- **Started by** `run.sh`, which the bridge launches as its iPhone capture. It opens the app through LaunchServices,
  which keeps the app's identity, with its stdout on a pipe, and copies the frames to its own stdout. Stopping
  `run.sh` stops the app, and so does a reader going away.
- **What the app does:** it captures the named device at 1280x720 and 30 fps, pinned, and encodes JPEG on the Mac's
  hardware encoder (quality 0.45: about 54 KB a frame). It exits when the iPhone disconnects.
- **Fallback:** without the app (for example, no Xcode command line tools for `swiftc`), the bridge falls back to
  FFmpeg. `MICROMIND_CAPTURE=ffmpeg` forces FFmpeg.
