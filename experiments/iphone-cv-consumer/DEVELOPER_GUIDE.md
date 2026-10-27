# iPhone CV Consumer Developer Guide

This folder is the complete workspace for the first live computer-vision app
that uses the iPhone camera feed. Treat it as a separate service from the camera
bridge.

## Service Boundary

```text
iPhone / macOS Continuity Camera
-> camera bridge service on http://127.0.0.1:8765
-> CV consumer service on http://127.0.0.1:8766
-> project-specific CV code and UI
```

The CV consumer must not own the camera.

Allowed in this folder:

```text
Read http://127.0.0.1:8765/iphone.mjpeg
Read http://127.0.0.1:8765/snapshot.jpg
Read http://127.0.0.1:8765/api/health
Serve CV outputs on http://127.0.0.1:8766
Add project-specific OpenCV processing
Add UI controls for CV algorithms and diagnostics
```

Not allowed in this folder:

```text
Do not open iPhoneWild Camera directly
Do not use cv2.VideoCapture for the iPhone device
Do not call browser getUserMedia
Do not run FFmpeg against AVFoundation
Do not reset ContinuityCaptureAgent
Do not modify the camera bridge lifecycle except through documented HTTP endpoints
```

The camera bridge owns connection, reconnect, stale-frame rejection, and iPhone
troubleshooting. The CV service owns decoding, processing, CV health, and
project-specific outputs.

## Folder Map

```text
experiments/iphone-cv-consumer/
  README.md                 quick start and endpoint contract
  DEVELOPER_GUIDE.md        this handoff guide
  requirements.txt          Python dependencies for this service only
  index.html                dashboard served by the CV service
  scripts/
    serve-cv.sh             shell entrypoint used by make cv
    cv_consumer_server.py   HTTP server, MJPEG reader, OpenCV processing
    cv_smoke.py             readiness check for the CV service
  output/
    .gitkeep                runtime logs/pids can live here and are ignored
```

The virtualenv lives at `experiments/iphone-cv-consumer/.venv/` and is ignored.
Run `make cv-setup` to rebuild it.

## Runtime Contract

Bridge inputs:

```text
Primary live frames: http://127.0.0.1:8765/iphone.mjpeg
Fallback/debug:      http://127.0.0.1:8765/snapshot.jpg
Bridge health:       http://127.0.0.1:8765/api/health
Bridge UI:           http://127.0.0.1:8765/camera/
```

CV outputs:

```text
Dashboard:           http://127.0.0.1:8766/
Health JSON:         http://127.0.0.1:8766/api/health
Latest raw frame:    http://127.0.0.1:8766/raw.jpg
Processed frame:     http://127.0.0.1:8766/processed.jpg
Processed stream:    http://127.0.0.1:8766/processed.mjpeg
```

Healthy `/api/health` means:

```json
{
  "ok": true,
  "state": "live",
  "bridge": {
    "ok": true,
    "state": "live",
    "mjpegUrl": "http://127.0.0.1:8765/iphone.mjpeg"
  },
  "inputMode": "mjpeg",
  "width": 1280,
  "height": 720
}
```

Important non-live states:

```text
bridge_offline   camera bridge is not reachable on port 8765
bridge_not_live  bridge is reachable but does not have fresh iPhone frames
waiting          CV service is waiting for its first decoded frame
stale            CV service had frames, but they are now too old
```

When not live, do not consume stale `/raw.jpg` or `/processed.jpg`; those
endpoints return `503` if the last frame is too old.

## Development Workflow

Use the Makefile from the repo root:

```bash
make cv-setup
make camera
make cv
```

Or start the two-service stack:

```bash
make camera-cv
```

Open both surfaces while developing:

```text
Camera/debug console: http://127.0.0.1:8765/camera/
CV app:               http://127.0.0.1:8766/
```

Validate the CV service:

```bash
make cv-health
make cv-smoke
```

Stop services:

```bash
make cv-stop
make camera-stop
```

or:

```bash
make camera-cv-stop
```

## Where To Add CV Work

Start in `scripts/cv_consumer_server.py`.

The main extension point is `_process_frame(...)`:

```text
JPEG bytes from bridge
-> cv2.imdecode(...)
-> OpenCV / NumPy processing
-> cv2.imencode(".jpg", ...)
-> /processed.jpg and /processed.mjpeg
```

For new algorithms, keep the shape:

```text
input: BGR OpenCV image
output: BGR OpenCV image
metrics: JSON-safe values added to /api/health
```

Good first additions:

```text
motion score
object contours
threshold controls
reference-frame subtraction
microscope scale calibration
measurement overlays
recording processed frames
```

If an algorithm needs UI controls, add controls in `index.html` and send updates
to new HTTP endpoints on the CV service. Do not put camera connection controls
in this app; link back to the bridge UI instead.

## Reliability Rules

- The CV app should keep running when the camera bridge is offline.
- The CV app should reconnect automatically when `/iphone.mjpeg` returns.
- `/api/health` is the first place downstream services should check readiness.
- Downstream apps should prefer `processed.mjpeg` for visual streams and
  `processed.jpg` for polling/snapshot workflows.
- If the CV app reports `bridge_offline` or `bridge_not_live`, fix the camera in
  the bridge UI first.

## Handoff Summary

Other developers can work freely inside this directory. The only shared contract
is HTTP:

```text
Input contract:  camera bridge on port 8765
Output contract: CV consumer on port 8766
```

As long as the CV service keeps that contract, the camera bridge and future CV
projects remain cleanly compartmentalized.
