"""The colours a population's members are drawn in, as the studio draws them, and their plain names (going live,
G3: asked "can you highlight all the red ones?", the app asked the vision model, which sees the camera's picture
and never the outlines the app draws, and was told nothing of how the cells' layer was coloured).

- `drawn(member_id, style)` is the studio's `memberLook` colour (apps/studio/src/population.ts): the member's own
  palette colour by its id, one colour for all, a measured column's ramp, or a category's palette colour (the look
  groups, K1; grey for a member without that measurement or category); None when the style hides it. The palette,
  the ramp and the grey are contract/overlay.json's `memberColours`, read by both; tests/test_going_live.py and
  population.test.ts check the same cases. Whether a member is drawn NOW (verified in place, the layer not faded
  out) is the layer's (population.drawn_colours).
- `name_of(colour)` is a plain name: each palette colour its own (`PALETTE_NAMES`, the names `present` colours by
  where it has them: brain/toolbox/present.py COLOURS, so no two palette colours are merged into one name), any
  other by hue bands (red, orange, yellow, green, teal, cyan, blue, purple, magenta, pink; grey, white or black
  when barely coloured).
- `words(style)` says the colouring in a few words, for the models (population.evidence_lines).

Facts, not verdicts: code says which colour each member is drawn in and what that colour is called; what the
user's words mean is the model's call (a colour nothing is drawn in is answered with the colours that are)."""
from __future__ import annotations

import math
import re

from .contract import MEMBER_HEAT, MEMBER_PALETTE, MEMBER_UNMEASURED

PALETTE = list(MEMBER_PALETTE)
PALETTE_BGR = [(int(c[5:7], 16), int(c[3:5], 16), int(c[1:3], 16)) for c in PALETTE]  # for the app's own pictures
# Each palette colour its own name (the review of G3, SF1: three were all "pink"), the names `present` colours by
# where the colour is one of those; a colour not listed here is named by its hue.
PALETTE_NAMES = {"#f979e8": "pink", "#22c55e": "green", "#38c3f7": "cyan", "#e74c3c": "red", "#ffa500": "orange",
                 "#ff69b4": "hot pink", "#00ced1": "teal", "#ffdc64": "yellow", "#1e90ff": "blue", "#f08080": "coral",
                 "#ff1493": "magenta", "#9acd32": "lime"}
# Hue bands (degrees, upper bounds) and their plain names; light reds and magentas are pink.
BANDS = ((15, "red"), (42, "orange"), (70, "yellow"), (160, "green"), (185, "teal"), (200, "cyan"), (255, "blue"),
         (290, "purple"), (345, "magenta"), (360, "red"))
PINK_LIGHT = 0.68  # HSL lightness from which a red or magenta is called pink
GREY_CHROMA = 0.15  # below this spread of the channels (0-1) a colour is grey, white or black


def _round(v: float) -> int:
    return math.floor(v + 0.5)  # JavaScript's Math.round (Python's round takes halves to even)


def heat(t) -> str:
    """The studio's `heat`: a colour along the measured range (0 low, 1 high), blue through teal and yellow to red."""
    try:
        t = float(t)
    except (TypeError, ValueError):
        t = 0.0
    x = max(0.0, min(1.0, t if math.isfinite(t) else 0.0)) * (len(MEMBER_HEAT) - 1)
    i = min(len(MEMBER_HEAT) - 2, math.floor(x))
    f = x - i
    a, b = MEMBER_HEAT[i], MEMBER_HEAT[i + 1]
    c = [_round(v + (b[k] - v) * f) for k, v in enumerate(a)]
    return f"rgb({c[0]}, {c[1]}, {c[2]})"


def own(member_id: str) -> str:
    """A member's own colour, from its id (contract 10): the same in its rows, its close-up and its highlight."""
    from .evidence import colour_of

    return PALETTE[colour_of(member_id) % len(PALETTE)]


def drawn(member_id: str, style: dict | None) -> str | None:
    """The colour the studio draws member `member_id` in under the layer's `style`, written as the studio writes
    it ("#rrggbb" or "rgb(r, g, b)"); None when the style hides it (a selection kept, the rest hidden). A member
    faded by a selection is still drawn, faintly, in its colour."""
    s = style or {}
    emphasis = s.get("emphasis") or None
    if emphasis and emphasis.get("rest") == "hide" and member_id not in (emphasis.get("members") or []):
        return None
    if s.get("colour") == "uniform" and s.get("value"):
        return str(s["value"])
    if s.get("colour") == "column":
        values = s.get("values")
        return heat(values[member_id]) if values and member_id in values else MEMBER_UNMEASURED
    if s.get("colour") == "category":  # the studio's categoryColour: the palette in the legend's order
        values = s.get("values")
        return PALETTE[int(values[member_id]) % len(PALETTE)] if values and member_id in values else MEMBER_UNMEASURED
    return own(member_id)


def rgb_of(colour) -> tuple[int, int, int] | None:
    text = str(colour or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{6}", text):
        return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
    m = re.fullmatch(r"rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", text)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def name_of(colour) -> str:
    """The plain name of a colour the studio draws ("#e74c3c" is red), by hue; a colour given by name stays it."""
    rgb = rgb_of(colour)
    if rgb is None:
        return " ".join(str(colour or "").strip().lower().split())
    named = PALETTE_NAMES.get("#%02x%02x%02x" % rgb)
    if named:
        return named
    r, g, b = (v / 255 for v in rgb)
    hi, lo = max(r, g, b), min(r, g, b)
    light, chroma = (hi + lo) / 2, hi - lo
    if chroma < GREY_CHROMA:
        return "white" if light >= 0.9 else "black" if light <= 0.1 else "grey"
    if hi == r:
        hue = (60 * (g - b) / chroma) % 360
    elif hi == g:
        hue = 60 * ((b - r) / chroma + 2)
    else:
        hue = 60 * ((r - g) / chroma + 4)
    name = next(n for top, n in BANDS if hue < top)
    return "pink" if name in ("red", "magenta") and light >= PINK_LIGHT else name


def bgr(colour) -> tuple[int, int, int]:
    """A colour the studio draws, as OpenCV's BGR (the app's own pictures: a captured view, a snapshot)."""
    rgb = rgb_of(colour) or rgb_of(MEMBER_UNMEASURED)
    return rgb[2], rgb[1], rgb[0]


def asked(word) -> str:
    """A colour as a request gives it, as a plain name: a name ("Red", "gray"), or a drawn colour's code."""
    name = name_of(word)
    return "grey" if name == "gray" else name


def words(style: dict | None) -> str:
    """The layer's colouring in a few words: "each in its own colour", "all yellow", "coloured by size, blue
    (low) to red (high)", or "coloured by look groups (DINOv2 small), one colour each"."""
    from .screen import COLUMN_WORDS

    s = style or {}
    if s.get("colour") == "uniform" and s.get("value"):
        return f"all {name_of(s['value'])}"
    if s.get("colour") == "column" and s.get("column"):
        return f"coloured by {COLUMN_WORDS.get(s['column'], s['column'])}, blue (low) to red (high)"
    if s.get("colour") == "category":
        label = (s.get("legend") or {}).get("label") or COLUMN_WORDS.get(s.get("column"), s.get("column") or "category")
        return f"coloured by {label}, one colour each"
    return "each in its own colour"
