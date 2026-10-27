# The overlay contract

`overlay.json` holds the numbers the studio, the server and the simulation harness must agree
on: when a picture counts as old, when outlines fade and where they are drawn, how far the
magnifier goes, the colours a population's members are drawn in, and the order a researcher's work is
listed in. Each is written here once.

Who reads it:

- the studio (`apps/studio/src/overlay.ts`, imported by Vite at build time);
- the server (`services/orchestrator/contract.py`, loaded once at import);
- the simulation harness (`services/sim/harness.py`, through `orchestrator.contract`).

Changing a number here changes it in all three. Run the studio and Python tests and
`make sim-test` after changing one.

| Key | Meaning |
| --- | --- |
| `frameDimMs` | The studio dims a picture when no new frame has arrived for this long (ms) and draws no overlays on it. A still picture that keeps arriving is live: how long it has not changed (each frame's `still` seconds) is only logged. |
| `feedStaleS` | The server reports the feed as not live when its newest frame is this old (s). |
| `fadeStartPx`, `fadeEndPx` | Outlines are solid below the first slide speed (px/frame), gone above the second, and fade in between. |
| `speedWindow` | The fade uses the fastest of this many recent frames. |
| `inViewRadius` | An outline is drawn (and counts as in view) while its centre is within this fraction of the eyepiece radius. |
| `clickRadius` | Clicks, and the cell count's crop, reach this fraction of the eyepiece radius. |
| `magnifier.maxLevel` | The magnifier's level goes at most this many times closer than the fitted view... |
| `magnifier.minWindowPx` | ...or until its window is this many camera px across, whichever comes first. |
| `magnifier.fitPad`, `magnifier.fitMinPx` | The fitted view: the object with this padding on each side, at least this many camera px. |
| `magnifier.aspect` | The magnifier panel's shape, as `[width, height]` (4:3). |
| `magnifier.panelCssPx` | The panel's usual width (CSS px), for judging pixel enlargement. |
| `magnifier.pixelatedK` | Past this many screen px per camera px, the camera's pixels are shown as blocks ("pixel-enlarged"). |
| `kindOrder` | A researcher's work is listed lesson, video, picture (lowest first). |
| `memberColours.palette` | Each member of a population (cells) is drawn in the palette's colour its id picks (`evidence.colour_of`), the same in its rows, its close-up and its highlight. |
| `memberColours.heat`, `memberColours.unmeasured` | A layer coloured by a measurement runs through these stops, blue (low) to red (high); a member without that measurement is drawn in the grey. The server names each drawn colour (`orchestrator/colours.py`), so a request can pick "the red ones" (going live, G3). |
