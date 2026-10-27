"""Known real structures for tests of collection and concurrent model execution.

Generic candidate blobs are not guaranteed to have a segmentable edge. The 2026-10-13
dataset failed on tiny texture; the same saved picture's labelled oval outlined normally.
The load test also reused old coordinates on a moving recording for a minute. Hold one
labelled view, as a slide at rest: every frame still arrives and every click has a new
frame id, so the real model computes its image embedding in every measured phase.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time


@asynccontextmanager
async def held_outlines(stack, viewer):
    from .harness import default_video

    if Path(stack.video).resolve() != default_video().resolve():
        raise ValueError('outline fixtures need the labelled leaf recording; unrelated pixels have no labels')
    questions = json.loads((Path(__file__).resolve().parents[1] / 'bench/questions.json').read_text())['questions']
    targets = [{'x': row['targets'][0][0], 'y': row['targets'][0][1]} for row in questions
               if row['frame'] == 'leaf-132922-012s.jpg' and row['kind'] == 'shape']
    feed = (await stack.http.get(f'{stack.bridge_url}/feed')).json()
    # The file the bridge plays now, not only the one the stack was started with: an earlier scenario may leave another
    # file playing, and the labels are of this recording's frame 360 only. (footage-switch's leftover of 2026-10-14 was
    # another name for this same video, about 14 s in: its position, not its file, was what was wrong then.)
    playing = Path(feed.get('folder') or '') / str(feed.get('footage') or '')
    if not feed.get('footage') or playing.resolve() != default_video().resolve():
        raise ValueError(f"outline fixtures need the labelled leaf recording playing; the bridge plays {feed.get('footage')!r}")
    paused = bool((feed.get('playback') or {}).get('paused'))
    try:
        response = await stack.http.post(f'{stack.bridge_url}/feed/pause', params={'paused': 1})
        response.raise_for_status()
        if not await stack.seek(360):  # the existing hand-labelled 12-second frame, at 30 fps
            raise RuntimeError('the labelled outline view did not reach the bridge')
        seq = (await stack.sim_status())['latestBridgeFrame']
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            _, header, jpeg = viewer.latest()
            if (header and (header.get('seq') or 0) >= seq and header.get('tracked') and header.get('steady')
                    and header.get('w') == 1280 and header.get('h') == 720 and jpeg):
                yield targets
                return
            await asyncio.sleep(.1)
        raise RuntimeError('the labelled outline view did not reach the viewer as a tracked frame')
    finally:
        response = await stack.http.post(f'{stack.bridge_url}/feed/pause', params={'paused': int(paused)})
        response.raise_for_status()
