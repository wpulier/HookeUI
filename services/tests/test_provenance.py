"""Generated pictures and videos say so in the file itself; AI-made web pictures are never used
as references (research_worker/provenance.py, roadmap W1-6)."""
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from research_worker import provenance as P


def png(colour=(40, 120, 200), size=(400, 600)):
    return cv2.imencode(".png", np.full((*size, 3), colour, np.uint8))[1].tobytes()


def test_a_marked_picture_carries_its_label_and_source():
    marked = P.mark_png(png(), P.ILLUSTRATION, P.GENERATED, "gpt-image-2.5-flare")
    image = cv2.imdecode(np.frombuffer(marked, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape == (400, 600, 3)
    assert image[-5:, :, :].mean() < image[:50, :, :].mean()  # the dark label strip along the bottom
    assert b"AI illustration, not microscope footage" in marked
    assert P.source_type(marked) == P.GENERATED and P.is_ai(P.source_type(marked))
    assert b"HookeUI (gpt-image-2.5-flare)" in marked


def test_labels_on_a_real_photo_are_a_composite():
    marked = P.mark_png(png(), P.LABELLED, P.COMPOSITE, "labels drawn by the app")
    assert P.source_type(marked) == P.COMPOSITE and P.is_ai(P.COMPOSITE)


def test_a_picture_that_says_nothing_is_not_called_ai():
    assert P.source_type(png()) is None and not P.is_ai(None)
    camera = b"\xff\xd8...Iptc4xmpExt:DigitalSourceType=\"http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture\"..."
    assert P.source_type(camera) == "digitalCapture" and not P.is_ai("digitalCapture")
    assert P.source_type(b"\xff\xd8 jumb c2pa manifest ...") == "c2pa" and not P.is_ai("c2pa")


def test_an_undecodable_picture_is_returned_unchanged():
    assert P.mark_png(b"not a picture", P.ILLUSTRATION, P.GENERATED, "m") == b"not a picture"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_marked_video_carries_its_label(tmp_path):
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    if not P.mark_video(video, P.ILLUSTRATION, P.GENERATED, "veo"):
        pytest.skip("this ffmpeg cannot draw text")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "default", str(video)],
                           capture_output=True, text=True).stdout
    assert "trainedAlgorithmicMedia" in probe and "AI illustration" in probe
    frame = cv2.VideoCapture(str(video)).read()[1]
    assert frame[-5:].mean() < frame[:20].mean()  # the strip


def test_safe_fetch_refuses_an_ai_made_reference(monkeypatch):
    import asyncio

    from research_worker import safe_fetch

    async def fake_get(client, url, accept_image):
        return None, P.mark_png(png(), P.ILLUSTRATION, P.GENERATED, "someone's model"), url

    async def no_commons(client, url):
        return None

    monkeypatch.setattr(safe_fetch, "_get", fake_get)
    monkeypatch.setattr(safe_fetch, "_commons", no_commons)
    with pytest.raises(safe_fetch.FetchRefused) as refused:
        asyncio.run(safe_fetch.fetch_image("https://example.org/picture.png", client=object()))
    assert "made by an AI model" in str(refused.value)
