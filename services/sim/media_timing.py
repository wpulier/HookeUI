"""Choose a continuous recording interval before measuring live media contention.

The simulated camera loops a finite recording. Its decoder can pause at that
boundary; that is a different event from library/thumbnail/upload contention.
Never choose an interval from measured latency or retry a slow sample.
"""
import math
import time


WINDOW_SECONDS = 30.  # 6 s baseline + 8 s media, preparation and request overhead.


async def prepare(stack, out):
    status = await stack.sim_status()
    total, fps = status.get('totalFrames', 0), status.get('fps', 0)
    qualified = (isinstance(total, int) and total > 0 and isinstance(fps, (int, float))
                 and math.isfinite(fps) and fps > 0 and total / fps > WINDOW_SECONDS)
    if not out.check('recording supports a continuous media timing window', qualified,
                     f'{total} frames, {fps} fps; need more than {WINDOW_SECONDS:g} s'):
        return None
    target = stack.start_frame % total
    if (total - target) / fps <= WINDOW_SECONDS:
        target = 0
    if not out.check('media timing starts at its declared source position', await stack.seek(target),
                     f'frame {target}'):
        return None
    status = await stack.sim_status()
    source = status.get('latestSourceFrame')
    world = status.get('worldFrame')
    if not out.check('observed source has room before its loop boundary',
                     status.get('totalFrames') == total and status.get('fps') == fps
                     and isinstance(source, int) and 0 <= source < total
                     and isinstance(world, int) and 0 <= world < total
                     and (total - max(source, world)) / fps > WINDOW_SECONDS,
                     f'source frame {source}, clock frame {world} of {total}'):
        return None
    return dict(started=time.monotonic(), sourceFrame=source, worldFrame=world, totalFrames=total, fps=fps)


def finish(window, out):
    elapsed = time.monotonic() - window['started']
    remaining = (window['totalFrames'] - max(window['sourceFrame'], window['worldFrame'])) / window['fps']
    out.observations.append(dict(kind='media-source-window', **window, elapsedS=elapsed,
                                 secondsToLoopAtStart=remaining))
    out.check('measured media window stayed before the recording loop', elapsed < remaining,
              f'{elapsed:.2f} s measured; loop after {remaining:.2f} s')
