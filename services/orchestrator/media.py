"""What kind of picture each one is (the partner's §1e; kind (f) of the end of the effort: "a generated
illustration titled 'Real close-up'; a reference photo a tab away"). Real pixels, a photo with labels added,
a photo found on the web and a picture a model made are said and shown as what they are, wherever one is
shown or told: the result viewer's items (presenter.py), the magnifier's sources (magnifier.py) and what the
models are told is on screen (brain/context.py). One place decides it, from what was recorded when the
picture was made (a researcher's output's `generated`, a web picture's source), never from its caption.

When it is not known how a picture was made it is never called real: it is "a picture" with that said.
"""
from __future__ import annotations

CAPTURE = "capture"  # the microscope camera's own pixels (a frame, a crop, frames averaged or side by side)
RAW = "raw"  # the original imported image; its acquisition is not inferred
LABELLED = "labelled"  # a real photo of the specimen with labels added (by a picture model or drawn by the app)
REFERENCE = "reference"  # a photo found on the public web, with its recorded source/licence: not this slide
GENERATED = "generated"  # made by a model: an illustration or a video, not the specimen
DERIVED = "derived"  # computed from this slide's picture by a capability (a filter, a model enlarging it): not its pixels
LESSON = "lesson"  # a researcher's lesson page
WRITTEN = "written"  # a researcher's written result
UNKNOWN = "unknown"  # what made it is not recorded

KINDS = (RAW, CAPTURE, LABELLED, REFERENCE, GENERATED, DERIVED, LESSON, WRITTEN, UNKNOWN)

# The tab or chip that names it (the result viewer's tabs; a video made by a model has its own word).
LABELS = {RAW: "Imported image", CAPTURE: "Microscope photo", LABELLED: "Labelled photo", REFERENCE: "Reference photo",
          GENERATED: "AI illustration", DERIVED: "Processed picture", LESSON: "Lesson", WRITTEN: "What it found", UNKNOWN: "Picture"}
VIDEO_LABEL = "AI video"

# What it is, in a line shown with it (the studio) and told to the models (brain/context.py). The generated and
# labelled lines are the ones burnt into the files themselves (research_worker/provenance.py).
LINES = {RAW: "From the original imported image; acquisition is not inferred", CAPTURE: "From the microscope camera's frames of this slide",  # what the app drew on it its label says
         LABELLED: "A real photo of this slide, with labels added",
         REFERENCE: "A reference photo from the web, not this slide",
         GENERATED: "AI illustration, not microscope footage",
         DERIVED: "Made from this slide's picture by a capability, not the camera's own pixels",
         LESSON: "A lesson written by a researcher",
         WRITTEN: "What the researcher found, in its words",
         UNKNOWN: "A picture; how it was made is not recorded"}
VIDEO_LINE = "AI video, not microscope footage"
# A researcher's chart or animation of a kept table (media DERIVED, `derived` on the output): what it is, in a line.
CHART_LINE = "Drawn by the app from a saved table; its values are the table's own"
ANIMATION_LINE = "An animation the app drew from a saved table's rows, not a video of the specimen"

# How the models are told what the magnifier shows (brain/context.py's screen line).
WORDS = {RAW: "the original imported image", CAPTURE: "a picture from the camera's frames", LABELLED: "a labelled photo of this slide",
         REFERENCE: "a reference photo from the web (not this slide)", GENERATED: "an AI illustration (not the specimen)",
         DERIVED: "a picture a capability made from this slide's picture (not the camera's own pixels)",
         LESSON: "a lesson", WRITTEN: "a written result", UNKNOWN: "a picture (how it was made is not recorded)"}


def of_output(output: dict) -> str:
    """A researcher's output's kind, from what was recorded when it was made: a lesson; a video or picture a
    model made (`generated`); a chart or animation the app drew from a kept table (`derived`); a real photo with labels
    added. An image with no record is never called real."""
    kind = output.get("kind")
    if kind == "presentation":
        return WRITTEN
    if kind == "html":
        return WRITTEN if output.get("contentKind") == "report" else LESSON
    if kind == "video":
        return GENERATED  # every video is made by a model from the photo (research_worker/video.py)
    if kind == "image":
        if isinstance(output.get("derived"), dict):
            return DERIVED  # a chart or animation the app drew from a kept table (research_worker/measurement_charts.py)
        if output.get("generated") is True:
            return GENERATED
        if output.get("generated") is False:
            return LABELLED
        return UNKNOWN
    return UNKNOWN


def label(media: str, kind: str | None = None) -> str:
    """The tab's word for it: "AI illustration", "AI video", "Reference photo"..."""
    if media == GENERATED and kind == "video":
        return VIDEO_LABEL
    return LABELS.get(media, LABELS[UNKNOWN])


def noun(media: str | None, kind: str | None = None) -> str:
    """The same word inside a sentence: "AI illustration", "reference photo", "labelled photo"."""
    word = label(media or UNKNOWN, kind)
    return word[:2] + word[2:].lower() if word.startswith("AI") else word.lower()


def line(media: str, kind: str | None = None, credit: str = "") -> str:
    """What it is, in a line: "AI illustration, not microscope footage"; a reference with its credit."""
    text = VIDEO_LINE if media == GENERATED and kind == "video" else LINES.get(media, LINES[UNKNOWN])
    return f"{text} · {credit}" if credit and media == REFERENCE else text


def of_research_file(research, url: str) -> tuple[str, str]:
    """(media, credit) of a researcher's file by its URL ("/research/<day>/<rid>/<path>"), from what the
    researcher recorded: a reference it fetched (sources/), else the output with that path. Unknown otherwise."""
    parts = str(url or "").split("?")[0].split("/")
    if len(parts) < 5 or parts[1] != "research":
        return UNKNOWN, ""
    rid, path = parts[3], "/".join(parts[4:])
    session = research.get(rid) if research is not None and hasattr(research, "get") else None
    if session is None or getattr(session, "day", None) != parts[2]:
        return UNKNOWN, ""
    for ref in (getattr(session, "result", None) or {}).get("refs") or []:
        if ref.get("path") == path:
            return REFERENCE, str(ref.get("credit") or "")
    for output in getattr(session, "outputs", None) or []:
        if output.get("path") == path:
            return of_output(output), ""
    return UNKNOWN, ""
