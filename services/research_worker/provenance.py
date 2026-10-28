"""Where a picture came from, carried in the file itself (roadmap W1-6).

A microscopy contest winner was found in 2026 to contain structures added by an AI model that
coloured real footage (Nature, Gizmodo). Our researchers make illustrations, videos and labelled
photos; the studio says they are AI-made, but a saved or shared file did not. Now:

- `mark_png` burns a visible line into the picture ("AI illustration, not microscope footage"),
  drawn by the app, not asked of the image model, and writes the IPTC digital source type
  (trainedAlgorithmicMedia; compositeWithTrainedAlgorithmicMedia for labels on a real photo)
  into PNG text chunks and an XMP packet.
- `mark_video` does the same for an MP4 with FFmpeg (drawtext and metadata), keeping the
  original if FFmpeg cannot.
- `source_type` reads what a downloaded picture says about itself (XMP/IPTC digital source
  type, a C2PA manifest) before safe_fetch re-encodes it and the metadata is lost, so an
  AI-made picture is never used as a reference for what a real thing looks like.
"""
from __future__ import annotations

import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import cv2
import numpy as np

IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype/"
GENERATED = "trainedAlgorithmicMedia"  # made entirely by a model
COMPOSITE = "compositeWithTrainedAlgorithmicMedia"  # a real photo with model-made parts (labels)
AI_TYPES = (GENERATED, COMPOSITE, "algorithmicMedia", "compositeSynthetic")

ILLUSTRATION = "AI illustration, not microscope footage"
LABELLED = "Labels added by AI on a real photo"


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _xmp(source_type: str, model: str) -> str:
    return ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
            'xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
            f'Iptc4xmpExt:DigitalSourceType="{IPTC}{source_type}" xmp:CreatorTool="HookeUI ({model})"/>'
            '</rdf:RDF></x:xmpmeta><?xpacket end="r"?>')


def _label(image: np.ndarray, text: str) -> np.ndarray:
    """A dark strip along the bottom with the words, sized to the picture."""
    h, w = image.shape[:2]
    scale = max(0.4, w / 1400)
    thickness = max(1, round(scale * 1.6))
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    while tw > w - 16 and scale > 0.3:
        scale *= 0.9
        (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    pad = max(4, th // 2)
    out = image.copy()
    y0 = h - th - base - 2 * pad
    out[y0:h] = (out[y0:h].astype(np.float32) * 0.35).astype(np.uint8)
    cv2.putText(out, text, (8, h - base - pad), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), thickness, cv2.LINE_AA)
    return out


def mark_png(png: bytes, text: str, source_type: str, model: str) -> bytes:
    """The picture with `text` burnt in and its source written into the file. A picture that
    cannot be decoded is returned unchanged."""
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        return png
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    ok, encoded = cv2.imencode(".png", _label(image[..., :3] if image.shape[2] == 4 else image, text))
    if not ok:
        return png
    data = encoded.tobytes()
    chunks = (_chunk(b"tEXt", b"Description\x00" + text.encode("latin-1", "replace"))
              + _chunk(b"tEXt", b"Software\x00HookeUI")
              + _chunk(b"tEXt", b"DigitalSourceType\x00" + (IPTC + source_type).encode())
              + _chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + _xmp(source_type, model).encode()))
    end = data.rfind(b"IEND") - 4  # before the IEND chunk's length
    return data[:end] + chunks + data[end:]


def mark_video(path: Path, text: str, source_type: str, model: str) -> bool:
    """Burn `text` into an MP4 and write its source into the file's metadata, in place.
    Returns False (the video unchanged) if FFmpeg is missing or fails."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    marked = path.with_suffix(".marked.mp4")
    words = text.replace(":", r"\:").replace("'", "")
    draw = (f"drawbox=x=0:y=ih-ih/12:w=iw:h=ih/12:color=black@0.65:t=fill,"
            f"drawtext=text='{words}':fontcolor=white:fontsize=h/24:x=12:y=h-h/24-h/48")
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(path), "-vf", draw, "-c:a", "copy",
         "-metadata", f"comment={text}. DigitalSourceType: {IPTC}{source_type}",
         "-metadata", f"description={text}", "-metadata", f"encoder=HookeUI ({model})", str(marked)],
        capture_output=True, timeout=180)
    if result.returncode != 0 or not marked.exists() or marked.stat().st_size == 0:
        marked.unlink(missing_ok=True)
        return False
    marked.replace(path)
    return True


# The IPTC digital source type vocabulary (cv.iptc.org/newscodes/digitalsourcetype), longest
# first so a term is not cut short by a shorter one that begins it.
TERMS = sorted(["trainedAlgorithmicMedia", "compositeWithTrainedAlgorithmicMedia", "algorithmicMedia",
                "compositeSynthetic", "algorithmicallyEnhanced", "dataDrivenMedia", "digitalArt", "digitalCapture",
                "computationalCapture", "negativeFilm", "positiveFilm", "print", "minorHumanEdits", "humanEdits",
                "compositeCapture", "composite", "screenCapture", "virtualRecording", "softwareImage"], key=len, reverse=True)
_SOURCE = re.compile(rb"digitalsourcetype/(" + b"|".join(t.encode() for t in TERMS) + rb")", re.I)


def source_type(data: bytes) -> str | None:
    """What a downloaded picture says made it: an IPTC digital source type found in its XMP or
    text chunks ("trainedAlgorithmicMedia"...), "c2pa" when it carries a C2PA manifest but no
    readable type, or None when it says nothing. Only the file's own words; never a guess."""
    found = _SOURCE.search(data)
    if found:
        return found.group(1).decode("ascii", "replace")
    if b"c2pa" in data[:2_000_000] and (b"jumb" in data or b"caBX" in data):
        return "c2pa"
    return None


def is_ai(kind: str | None) -> bool:
    return bool(kind) and kind.lower() in {k.lower() for k in AI_TYPES}
