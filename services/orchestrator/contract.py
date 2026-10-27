"""The numbers the studio, the server and the simulation harness share, read once from
contract/overlay.json (the studio imports the same file: apps/studio/src/overlay.ts).

No imports from the rest of the app, so anything (versions, the sim harness) can use it.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[2] / "contract" / "overlay.json"
OVERLAY: dict = json.loads(PATH.read_text())

FRAME_DIM_MS = float(OVERLAY["frameDimMs"])  # the studio dims a frozen picture
FEED_STALE_S = float(OVERLAY["feedStaleS"])  # the server reports the feed not live
FADE_START_PX = float(OVERLAY["fadeStartPx"])  # outlines fade from this slide speed (px/frame)...
FADE_END_PX = float(OVERLAY["fadeEndPx"])  # ...and are gone at this one
SPEED_WINDOW = int(OVERLAY["speedWindow"])  # the fastest of this many recent frames counts
IN_VIEW_RADIUS = float(OVERLAY["inViewRadius"])  # x the eyepiece radius: drawn, and in view
CLICK_RADIUS = float(OVERLAY["clickRadius"])  # x the eyepiece radius: clicks and the cells crop
SPEECH_LINE_GAP_S = float(OVERLAY["speechLineGapS"])  # a spoken chat line grows until this long passes without a piece

_m = OVERLAY["magnifier"]
MAGNIFIER_MAX_LEVEL = float(_m["maxLevel"])
MAGNIFIER_MIN_WINDOW_PX = float(_m["minWindowPx"])
MAGNIFIER_PIXELATED_K = float(_m["pixelatedK"])
MAGNIFIER_FIT_PAD = float(_m["fitPad"])
MAGNIFIER_FIT_MIN_PX = float(_m["fitMinPx"])
MAGNIFIER_PANEL_CSS_PX = float(_m["panelCssPx"])
MAGNIFIER_ASPECT = _m["aspect"][0] / _m["aspect"][1]  # [width, height]

KIND_ORDER: dict[str, int] = {k: int(v) for k, v in OVERLAY["kindOrder"].items()}

_c = OVERLAY["memberColours"]  # the colours the studio draws a population's members in (population.ts)
MEMBER_PALETTE: list[str] = list(_c["palette"])  # by id (evidence.colour_of)
MEMBER_HEAT: list[tuple[int, int, int]] = [tuple(int(v) for v in stop) for stop in _c["heat"]]  # by a measurement
MEMBER_UNMEASURED: str = str(_c["unmeasured"])  # a member without that measurement
