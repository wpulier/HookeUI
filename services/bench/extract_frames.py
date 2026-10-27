"""Re-create the benchmark frames in bench/frames/ from the original recordings.

Frames are derived from the June 14 recordings, which (like all captures) are kept out of
Git, so the frames are regenerated rather than committed. Names encode the recording and
the timestamp: leaf-<recording suffix>-<seconds>s.jpg
"""
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[2]
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)

FRAMES = store().bench("frames")
WANTED = [("132922", 12), ("132922", 45), ("132922", 90), ("132922", 140), ("132922", 200), ("131443", 8)]


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    for suffix, seconds in WANTED:
        # The frames keep the original capture's time in their names (leaf-132922-012s.jpg).
        recording = {"132922": "2026-06-14-leaf-epidermis-3m43s", "131443": "2026-06-14-leaf-epidermis-23s"}[suffix]
        cap = cv2.VideoCapture(str(store().real_file(recording)))
        cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, image = cap.read()
        cap.release()
        if not ok:
            raise SystemExit(f"could not read {suffix} at {seconds}s")
        out = FRAMES / f"leaf-{suffix}-{seconds:03d}s.jpg"
        cv2.imwrite(str(out), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print(out.name)


if __name__ == "__main__":
    main()
