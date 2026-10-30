"""Charts and animations of a retained table, drawn by the app from the table's own values for a researcher's
make_chart and animate_measurements (charts.py's rule: never a number a model supplied).

The table is one session entry read whole at one exact revision (the index's JSON reading; its revision is the sha256 of
that text, the same revision the session tool's pages carry). `rows` points at an array inside it (under /detail/),
`value` at a number in each row (a JSON pointer from the row; "" when the row is the number itself), `label` and `group`
at text in each row. A row with no finite number is left out and counted, never imputed. Two kinds: "sorted" (one bar
per row, in the chosen order) and "histogram" (rows counted in equal bins). A third, "means", reads several tables of the
same shape (one per run of an operation: "run motion 4 times and chart the average speed") and draws one bar per table
at its mean, its spread (one standard deviation) as a whisker and every value as a dot, with n under it. `only` keeps the
rows whose value at that pointer is true ("/moving"): the rest are left out and counted. An animation builds that same chart row by
row in the chosen order and ends on the whole chart, its axes fixed from the first frame; it is an animated PNG, a
picture every browser plays, made here on this Mac: no model and no provider.
"""
from __future__ import annotations

import io
import math
import statistics
from dataclasses import dataclass, field

KINDS = ("sorted", "histogram")
MEANS = "means"  # one bar per table at its mean, with its spread (several tables: means_png)
TABLES_MAX = 8  # tables one "means" chart reads
ORDERS = ("ascending", "descending", "table")
ROWS_MAX = 20_000  # rows one chart reads
SHOWN_MAX = 500  # rows kept with the output as its values (the rest are counted)
GROUPS_MAX = 6  # groups drawn in their own colour; more are "other"
FRAMES_MAX = 48
SIZE = (760, 460)
COLOURS = ("#3567c9", "#d9762b", "#2a9a8a", "#b8478a", "#7f6a2f", "#5b6472")
OTHER = "#9aa1ab"


@dataclass
class Series:
    """The values a chart draws, in the order it draws them, with where they came from."""
    values: list[float]
    labels: list[str]
    groups: list[str]
    column: str  # the value's own key in the table (the pointer's last part)
    rows: str
    value: str
    order: str
    total: int  # rows in the table's array
    excluded: int  # rows with no finite number there
    source: dict = field(default_factory=dict)  # {"id", "revision", "day", "title", "version"}
    only: str | None = None  # the pointer to a true/false that kept a row ("/moving"), if one was given

    @property
    def n(self) -> int:
        return len(self.values)

    def stats(self) -> dict:
        if not self.values:
            return {}
        return {"n": self.n, "min": _round(min(self.values)), "median": _round(statistics.median(self.values)),
                "max": _round(max(self.values)), "mean": _round(statistics.fmean(self.values))}

    def content(self, title: str, kind: str, animated: bool) -> dict:
        """What is kept with the output: its source, how it was drawn and its values (the first SHOWN_MAX, in order)."""
        shown = [{"label": label, "value": value, **({"group": group} if group else {})}
                 for label, value, group in list(zip(self.labels, self.values, self.groups))[:SHOWN_MAX]]
        return {"title": title, "kind": "animation" if animated else "chart", "chart": kind, "source": dict(self.source),
                "rows": self.rows, "value": self.value, "column": self.column, "order": self.order,
                "total": self.total, "excluded": self.excluded, "stats": self.stats(), "values": shown,
                **({"valuesShown": SHOWN_MAX} if self.n > SHOWN_MAX else {})}


def _round(x: float) -> float:
    return float(f"{x:.4g}")


def pointer(value, path: str, *, where: str):
    """The value at a JSON pointer from `value` ("" is `value` itself)."""
    if path == "":
        return value
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 300:
        raise ValueError(f"{where} is a JSON pointer such as /area (\"\" for the row itself)")
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, TypeError, IndexError, ValueError):
            raise ValueError(f"{where} {path} is not in the table") from None
    return value


def series(document: dict, *, revision: str, rows: str, value: str, label: str | None = None, group: str | None = None,
           order: str = "ascending", only: str | None = None) -> Series:
    """The numbers at `value` in each row of the array at `rows`, in `order`. Raises ValueError, said for the model."""
    if order not in ORDERS:
        raise ValueError(f"order is one of {', '.join(ORDERS)}")
    if not isinstance(rows, str) or not rows.startswith("/detail/"):
        raise ValueError("rows points at an array in the table's detail, such as /detail/members")
    array = pointer(document, rows, where="rows")
    if not isinstance(array, list) or not array:
        raise ValueError(f"rows {rows} is not a list of rows in the table")
    if len(array) > ROWS_MAX:
        raise ValueError(f"the table has {len(array):,} rows; one chart reads at most {ROWS_MAX:,}")
    picked = []
    excluded = 0
    if only is not None and (not isinstance(only, str) or not only.startswith("/")):
        raise ValueError("only is a JSON pointer from one row to a true/false, such as /moving")
    for n, row in enumerate(array):
        if only is not None:
            try:
                kept = pointer(row, only, where="only")
            except ValueError:
                kept = None
            if kept is not True:
                excluded += 1
                continue
        try:
            number = pointer(row, value, where="value")
        except ValueError:
            number = None
        if type(number) not in (int, float) or not math.isfinite(number):
            excluded += 1
            continue
        name = _text(row, label, n)
        kind = _text(row, group, None) if group else ""
        picked.append((n, name, float(number), kind or ""))
    if not picked:
        raise ValueError(f"no row has a number at {value}" + (f" where {only} is true" if only else "")
                         + ": the table's columns are in its rows; pick one that is numeric")
    if order != "table":
        picked.sort(key=lambda p: p[2], reverse=order == "descending")
    entry = document.get("entry") if isinstance(document.get("entry"), dict) else {}
    column = value.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~") if value else rows.rsplit("/", 1)[-1]
    return Series(values=[p[2] for p in picked], labels=[p[1] for p in picked], groups=[p[3] for p in picked],
                  column=column or "value", rows=rows, value=value, order=order, total=len(array), excluded=excluded, only=only,
                  source={"id": entry.get("id"), "revision": revision, "day": entry.get("day"),
                          "title": entry.get("title"), **({"version": entry["version"]} if isinstance(entry.get("version"), int) else {})})


def _text(row, path: str | None, n: int | None) -> str:
    if path is None:
        return f"row {n + 1}" if n is not None else ""
    try:
        found = pointer(row, path, where="label")
    except ValueError:
        return f"row {n + 1}" if n is not None else ""
    return str(found)[:40] if isinstance(found, (str, int, float)) and not isinstance(found, bool) else (
        f"row {n + 1}" if n is not None else "")


# --- drawing --------------------------------------------------------------------------------------------------------
def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # an older Pillow: its one bitmap font
        return ImageFont.load_default()


# The default font draws Latin-1 only: a title's dash or curly quote would be an empty box (live 30 Oct: "Average net
# speed □ all tracked spots").
PLAIN = str.maketrans({"\u2014": "-", "\u2013": "-", "\u2212": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"',
                       "\u201d": '"', "\u2026": "...", "\u2022": "\u00b7", "\u00a0": " "})


def plain(text: str) -> str:
    """Text the chart's font can draw: common punctuation as its Latin-1 twin, anything else beyond Latin-1 as "?"."""
    return "".join(c if ord(c) < 256 else "?" for c in str(text).translate(PLAIN))


def _number(x: float) -> str:
    if x == 0:
        return "0"
    if abs(x) >= 10_000 or abs(x) < 0.01:
        return f"{x:.3g}"
    return f"{x:,.4g}" if abs(x) >= 100 else f"{x:.3g}"


def _ticks(low: float, high: float, count: int = 5) -> list[float]:
    if high <= low:
        return [low]
    step = 10 ** math.floor(math.log10((high - low) / count))
    for m in (1, 2, 2.5, 5, 10):
        if (high - low) / (step * m) <= count:
            step *= m
            break
    first = math.ceil(low / step) * step
    return [first + i * step for i in range(int((high - first) / step) + 1)]


class _Layout:
    """The axes of one chart, fixed from the whole series so an animation's frames never rescale."""

    def __init__(self, s: Series, kind: str) -> None:
        self.kind = kind
        self.left, self.top, self.right, self.bottom = 74, 58, SIZE[0] - 24, SIZE[1] - 78
        self.groups = list(dict.fromkeys(g for g in s.groups if g))
        if kind == "histogram":
            low, high = min(s.values), max(s.values)
            if high == low:
                low, high = low - 0.5, high + 0.5
            self.bins = min(30, max(5, math.ceil(math.sqrt(s.n))))
            self.edges = [low + (high - low) * i / self.bins for i in range(self.bins + 1)]
            counts = [0] * self.bins
            for v in s.values:
                counts[self._bin(v)] += 1
            self.y_low, self.y_high = 0.0, float(max(counts))
        else:
            self.y_low, self.y_high = min(0.0, min(s.values)), max(0.0, max(s.values))
            if self.y_high == self.y_low:
                self.y_high = self.y_low + 1.0

    def _bin(self, v: float) -> int:
        low, high = self.edges[0], self.edges[-1]
        return min(self.bins - 1, max(0, int((v - low) / (high - low) * self.bins)))

    def y(self, v: float) -> float:
        return self.bottom - (v - self.y_low) / (self.y_high - self.y_low) * (self.bottom - self.top)

    def colour(self, group: str) -> str:
        if not group:
            return COLOURS[0]
        i = self.groups.index(group) if group in self.groups else GROUPS_MAX
        return COLOURS[i] if i < GROUPS_MAX else OTHER

    def stack(self, group: str) -> int:
        """Its place in a histogram's stack: 0 with no group, then each drawn group, then "other"."""
        if not group:
            return 0
        i = self.groups.index(group)
        return i + 1 if i < GROUPS_MAX else GROUPS_MAX + 1


def _draw(s: Series, title: str, layout: _Layout, upto: int, caption: str):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", SIZE, "#ffffff")
    d = ImageDraw.Draw(image)
    ink, faint, rule = "#1d232b", "#5f6875", "#d5d9df"
    d.text((layout.left, 16), plain(title)[:90], fill=ink, font=_font(17))
    small = _font(11)
    for t in _ticks(layout.y_low, layout.y_high):
        y = layout.y(t)
        d.line([(layout.left, y), (layout.right, y)], fill=rule)
        d.text((layout.left - 8, y), _number(t), fill=faint, font=small, anchor="rm")
    d.line([(layout.left, layout.top), (layout.left, layout.bottom), (layout.right, layout.bottom)], fill=faint)
    width = layout.right - layout.left
    if layout.kind == "histogram":
        counts = [[0] * layout.bins for _ in range(GROUPS_MAX + 2)]
        stacks = [0] * layout.bins
        for v, g in zip(s.values[:upto], s.groups[:upto]):
            counts[layout.stack(g)][layout._bin(v)] += 1
        step = width / layout.bins
        for row, group in enumerate(["", *layout.groups[:GROUPS_MAX], *(["other"] if len(layout.groups) > GROUPS_MAX else [])]):
            row = GROUPS_MAX + 1 if group == "other" else row
            for b in range(layout.bins):
                c = counts[row][b]
                if not c:
                    continue
                y0, y1 = layout.y(stacks[b]), layout.y(stacks[b] + c)
                d.rectangle([layout.left + b * step + 1, y1, layout.left + (b + 1) * step - 1, y0], fill=layout.colour(group))
                stacks[b] += c
        for i in (0, layout.bins // 2, layout.bins):
            d.text((layout.left + i * step, layout.bottom + 6), _number(layout.edges[i]), fill=faint, font=small, anchor="ma")
        d.text((layout.left + width / 2, layout.bottom + 22), f"{s.column} (rows counted in {layout.bins} equal bins)",
               fill=faint, font=small, anchor="ma")
        d.text((14, layout.top - 16), "rows", fill=faint, font=small)
    else:
        step = width / s.n
        zero = layout.y(0.0)
        for i, (v, g) in enumerate(zip(s.values[:upto], s.groups[:upto])):
            x0, x1 = layout.left + i * step, layout.left + (i + 1) * step
            gap = 1 if step > 4 else 0
            d.rectangle([x0 + gap, min(zero, layout.y(v)), max(x0 + gap, x1 - gap), max(zero, layout.y(v))], fill=layout.colour(g))
        if s.n <= 24:
            for i, label in enumerate(s.labels):
                d.text((layout.left + (i + 0.5) * step, layout.bottom + 6), label[:8], fill=faint, font=small, anchor="ma")
        order = {"ascending": "smallest to largest", "descending": "largest to smallest", "table": "in the table's order"}[s.order]
        d.text((layout.left + width / 2, layout.bottom + 22), f"{s.n} rows, {order}", fill=faint, font=small, anchor="ma")
        d.text((14, layout.top - 16), s.column[:24], fill=faint, font=small)
    if layout.groups:
        x = layout.right
        for group in reversed(layout.groups[:GROUPS_MAX] + (["other"] if len(layout.groups) > GROUPS_MAX else [])):
            words = group[:16]
            box = d.textbbox((0, 0), words, font=small)
            x -= box[2] - box[0] + 26
            d.rectangle([x, 22, x + 10, 32], fill=layout.colour(group) if group != "other" else OTHER)
            d.text((x + 14, 27), words, fill=faint, font=small, anchor="lm")
    d.text((14, SIZE[1] - 30), caption[:130], fill=faint, font=small)
    if upto < s.n:
        d.text((layout.right, layout.top - 16), f"{upto} of {s.n}", fill=faint, font=small, anchor="ra")
    return image


def source_words(s: Series) -> str:
    """The chart's footnote: where its numbers came from, exactly, and what was left out."""
    src = s.source
    left = f"; {s.excluded} without a number left out" if s.excluded else ""
    return (f"Drawn by the app from {src.get('title') or src.get('id')} ({src.get('id')}, revision "
            f"{str(src.get('revision'))[:10]}): {s.n} of {s.total} rows{left}")


def _png(image, info: str) -> bytes:
    from PIL import PngImagePlugin

    meta = PngImagePlugin.PngInfo()
    meta.add_text("Description", info)
    out = io.BytesIO()
    image.save(out, format="PNG", pnginfo=meta, optimize=True)
    return out.getvalue()


def chart_png(s: Series, title: str, kind: str) -> bytes:
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    words = source_words(s)
    return _png(_draw(s, title, _Layout(s, kind), s.n, words), words + "; not a picture of the specimen")


# --- several tables: one bar per table at its mean, with its spread ------------------------------------------------
def spread(values: list[float]) -> dict:
    """A table's mean and spread: n, mean, its sample standard deviation (0 for one value), the standard error, the
    range and the median, rounded as the chart says them."""
    n = len(values)
    mean = statistics.fmean(values)
    sd = statistics.stdev(values) if n > 1 else 0.0
    return {"n": n, "mean": _round(mean), "sd": _round(sd), "sem": _round(sd / math.sqrt(n)) if n > 1 else 0.0,
            "median": _round(statistics.median(values)), "min": _round(min(values)), "max": _round(max(values))}


def means_content(sets: list[Series], names: list[str], title: str, unit: str = "") -> dict:
    """What is kept with a "means" chart: each table's source, its stats and its values, and across the tables the mean
    of their means and its spread (each table counted once)."""
    tables = [{"name": name, "source": dict(s.source), "total": s.total, "excluded": s.excluded, **spread(s.values),
               "values": [_round(v) for v in s.values[:SHOWN_MAX]]} for s, name in zip(sets, names)]
    across = spread([statistics.fmean(s.values) for s in sets])
    first = sets[0]
    return {"title": title, "kind": "chart", "chart": MEANS, "rows": first.rows, "value": first.value,
            "column": first.column, **({"only": first.only} if first.only else {}), **({"unit": unit} if unit else {}),
            "spread": "one standard deviation of each table's values", "tables": tables,
            "acrossTables": {"tables": len(sets), "meanOfMeans": across["mean"], "sdOfMeans": across["sd"],
                             "min": across["min"], "max": across["max"]}}


def means_png(sets: list[Series], names: list[str], title: str, unit: str = "") -> bytes:
    """One bar per table at the mean of its values, a whisker of one standard deviation either side, every value a dot
    and n under its bar; the footnote names each table it was drawn from. Drawn here (Pillow): no model, no provider."""
    from PIL import Image, ImageDraw

    if not 1 <= len(sets) <= TABLES_MAX or len(names) != len(sets):
        raise ValueError(f"a means chart reads one to {TABLES_MAX} tables, each with a name")
    stats = [spread(s.values) for s in sets]
    top_value = max(max(max(s.values), st["mean"] + st["sd"]) for s, st in zip(sets, stats))
    low_value = min(0.0, min(min(s.values) for s in sets))
    if top_value <= low_value:
        top_value = low_value + 1.0
    image = Image.new("RGB", SIZE, "#ffffff")
    d = ImageDraw.Draw(image)
    ink, faint, rule = "#1d232b", "#5f6875", "#d5d9df"
    left, top, right, bottom = 74, 58, SIZE[0] - 24, SIZE[1] - 96
    d.text((left, 16), plain(title)[:90], fill=ink, font=_font(17))
    small = _font(11)

    def y(v: float) -> float:
        return bottom - (v - low_value) / (top_value * 1.08 - low_value) * (bottom - top)

    for t in _ticks(low_value, top_value * 1.08):
        d.line([(left, y(t)), (right, y(t))], fill=rule)
        d.text((left - 8, y(t)), _number(t), fill=faint, font=small, anchor="rm")
    d.line([(left, top), (left, bottom), (right, bottom)], fill=faint)
    step = (right - left) / len(sets)
    bar = min(80.0, step * 0.55)
    for i, (s, st, name) in enumerate(zip(sets, stats, names)):
        middle = left + (i + 0.5) * step
        colour = COLOURS[i % len(COLOURS)]
        d.rectangle([middle - bar / 2, y(st["mean"]), middle + bar / 2, y(0.0 if low_value <= 0 else low_value)],
                    fill=colour)
        for k, v in enumerate(s.values[:SHOWN_MAX]):  # every value, spread a little sideways so they can be seen
            dx = ((k * 7) % 11 - 5) / 5 * bar * 0.35
            d.ellipse([middle + dx - 2, y(v) - 2, middle + dx + 2, y(v) + 2], outline=ink, fill="#ffffff")
        if st["n"] > 1:
            hi, lo = y(st["mean"] + st["sd"]), y(max(low_value, st["mean"] - st["sd"]))
            d.line([(middle, hi), (middle, lo)], fill=ink, width=2)
            d.line([(middle - 8, hi), (middle + 8, hi)], fill=ink, width=2)
            d.line([(middle - 8, lo), (middle + 8, lo)], fill=ink, width=2)
        words = f"mean {_number(st['mean'])}"  # on a white box above its whisker, so dots never hide it
        at = (middle, y(st["mean"] + st["sd"]) - 6)
        box = d.textbbox(at, words, font=small, anchor="mb")
        d.rectangle([box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2], fill="#ffffff", outline=rule)
        d.text(at, words, fill=ink, font=small, anchor="mb")
        d.text((middle, bottom + 6), plain(name)[:16], fill=ink, font=small, anchor="ma")
        d.text((middle, bottom + 20), f"n = {st['n']}" + (f", sd {_number(st['sd'])}" if st["n"] > 1 else ""),
               fill=faint, font=small, anchor="ma")
    first = sets[0]
    axis = plain(first.column + (f" ({unit})" if unit else ""))
    d.text((14, top - 16), axis[:40], fill=faint, font=small)
    d.text((left + (right - left) / 2, bottom + 38), "bar: mean · whisker: ±1 standard deviation · dots: each value"
           + (f" (only rows where {first.only[1:]} is true)" if first.only else ""), fill=faint, font=small, anchor="ma")
    words = "Drawn by the app from " + ", ".join(f"{s.source.get('id')}" for s in sets) + (
        f" ({sum(s.n for s in sets)} values" + (f"; {sum(s.excluded for s in sets)} rows left out" if any(s.excluded for s in sets)
                                               else "") + ")")
    d.text((14, SIZE[1] - 30), words[:130], fill=faint, font=small)
    return _png(image, words + "; not a picture of the specimen")


def animation_png(s: Series, title: str, kind: str, seconds: float = 6.0) -> tuple[bytes, int]:
    """An animated PNG of the chart built row by row in the series' order: (its bytes, its frames)."""
    from PIL import PngImagePlugin

    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    layout, words = _Layout(s, kind), source_words(s)
    count = max(2, min(FRAMES_MAX, s.n))
    frames = [_draw(s, title, layout, math.ceil(s.n * (k + 1) / count), words) for k in range(count)]
    seconds = min(12.0, max(2.0, float(seconds)))
    each = int(seconds * 1000 * 0.75 / (count - 1)) if count > 1 else 500
    durations = [max(40, each)] * (count - 1) + [int(seconds * 1000 * 0.25) + 600]  # the whole chart is held
    meta = PngImagePlugin.PngInfo()
    meta.add_text("Description", words + "; an animation of the table's rows, not a video of the specimen")
    out = io.BytesIO()
    frames[0].save(out, format="PNG", save_all=True, append_images=frames[1:], duration=durations, loop=0, pnginfo=meta)
    return out.getvalue(), count


def ready() -> str:
    """"" when this Mac can draw charts and play animations; else why not, in plain words."""
    try:
        from PIL import Image
    except ImportError:
        return "the app's picture library (Pillow) isn't installed here"
    try:
        out = io.BytesIO()
        Image.new("RGB", (2, 2)).save(out, format="PNG", save_all=True, append_images=[Image.new("RGB", (2, 2), "white")])
        if not getattr(Image.open(io.BytesIO(out.getvalue())), "is_animated", False):
            return "this picture library can't write animated pictures"
    except Exception as exc:  # noqa: BLE001 - its words are the reason
        return f"this picture library can't write animated pictures ({type(exc).__name__})"
    return ""
