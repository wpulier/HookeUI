# iPhone CV Consumer

This service is a separate live computer-vision scaffold that consumes the
existing iPhone camera bridge. It never opens `iPhoneWild Camera` directly.

For the developer handoff, extension points, and boundary rules, see
[DEVELOPER_GUIDE.md](./DEVELOPER_GUIDE.md).

```text
iPhoneWild Camera
-> camera bridge on http://127.0.0.1:8765
-> CV consumer on http://127.0.0.1:8766
```

## Start

From the project root:

```bash
make cv-setup
make camera
make cv
```

Or start the two-service stack:

```bash
make camera-cv
```

Open:

```text
Camera bridge: http://127.0.0.1:8765/camera/
CV dashboard:  http://127.0.0.1:8766/
```

## Contract

Inputs from the camera bridge:

```text
Primary live input: http://127.0.0.1:8765/iphone.mjpeg
Fallback/debug:     http://127.0.0.1:8765/snapshot.jpg
Bridge health:      http://127.0.0.1:8765/api/health
```

Outputs from the CV service:

```text
Dashboard:          http://127.0.0.1:8766/
CV health:          http://127.0.0.1:8766/api/health
Latest raw frame:   http://127.0.0.1:8766/raw.jpg
Processed frame:    http://127.0.0.1:8766/processed.jpg
Processed stream:   http://127.0.0.1:8766/processed.mjpeg
```

## Make Targets

```text
make cv-setup        Create/install the CV service virtualenv
make cv              Start the CV service on port 8766
make cv-open         Open the CV dashboard
make cv-stop         Stop only the CV service
make cv-health       Print CV service health
make cv-smoke        Verify bridge dependency and CV frame decode
make camera-cv       Start camera bridge if needed, then run CV service
make camera-cv-stop  Stop both services
```

## V1 Processing

The server decodes the bridge MJPEG stream with OpenCV, converts the frame to
grayscale, overlays Canny edges in green, and serves the processed result as
JPEG/MJPEG. This is intentionally minimal: it proves live ingestion,
processing, health, and service separation before a project-specific algorithm
is added.

## Failure Behavior

- If the camera bridge is offline, this service stays up and reports
  `bridge_offline`.
- If the bridge is reachable but not live, this service reports
  `bridge_not_live`.
- If MJPEG drops, this service retries MJPEG and attempts `/snapshot.jpg` as a
  fallback/debug frame source.
- If frames go stale, `/raw.jpg` and `/processed.jpg` return `503` instead of
  serving old frames.
