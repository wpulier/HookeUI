"""Bounded post-measurement summaries of existing frame timestamps; no sampling I/O."""
import json
import math

import numpy as np

# All timestamps are host Unix wall seconds. These are observer boundaries, not
# isolated CPU timings: an-t includes waiting for the analysis executor.
CLOCK = {
    'ba': 'Bridge capture timestamp from X-Frame-At; absent remains unknown.',
    't': 'FrameSource._accept wall time after taking the newest offered JPEG.',
    'an': 'Hub._analyse_batch wall time after disc/motion/focus/quality analysis.',
    'sent': 'Hub._publish wall time before serializing and offering to Studio clients.',
    'receivedAt': 'Harness websocket receive wall time; not browser decode or paint.',
}
PHASES = {
    'intakeMs': ('ba', 't'), 'analysisMs': ('t', 'an'), 'publishMs': ('an', 'sent'),
    'deliveryMs': ('sent', 'receivedAt'), 'postAnalysisMs': ('an', 'receivedAt'),
    'appToReceiverMs': ('t', 'receivedAt'), 'bridgeToReceiverMs': ('ba', 'receivedAt'),
    'arrivalGapMs': ('previousReceivedAt', 'receivedAt'),
}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def retain(outcome, phase, frames, started, ended):
    """At most 24 window summaries + 16 worst frames, under 60 KiB per scenario.

    Worst ranks use the largest observed phase or arrival gap, so a receiver gap
    is retained even when its next frame is fresh. Missing/skewed values are counted.
    """
    block = next((o for o in outcome.observations if o.get('kind') == 'frame-timing'), None)
    if block is None:
        block = {'kind': 'frame-timing', 'clock': CLOCK, 'windows': [], 'omittedWindows': 0,
                 'worstFrames': [], 'ranking': 'largest available phase or inter-arrival gap, milliseconds'}
        outcome.observations.append(block)
    values = {key: [] for key in PHASES}
    missing = dict.fromkeys(PHASES, 0)
    invalid = dict.fromkeys(PHASES, 0)
    worst = []
    previous = None
    for received, header, _ in frames:
        row = {key: header.get(key) if number(header.get(key)) else None
               for key in ('id', 'seq', 'ba', 't', 'an', 'sent')}
        row.update(receivedAt=received, previousReceivedAt=previous,
                   boot=str(header.get('boot', ''))[:64], phase=phase[:64],
                   windowStartedAt=started, windowEndedAt=ended)
        durations = {}
        for key, (first, last) in PHASES.items():
            if not number(row.get(first)) or not number(row.get(last)):
                missing[key] += 1
            elif row[last] < row[first]:
                invalid[key] += 1
            else:
                value = (row[last] - row[first]) * 1000
                values[key].append(value)
                durations[key] = round(value, 3)
        row['durationsMs'] = durations
        row['rankMs'] = max(durations.values(), default=0)
        worst.append(row)
        previous = received
    stats = {}
    for key, samples in values.items():
        stats[key] = {'count': len(samples), 'missing': missing[key], 'invalidOrder': invalid[key],
                      'p50': round(float(np.percentile(samples, 50)), 3) if samples else None,
                      'p95': round(float(np.percentile(samples, 95)), 3) if samples else None,
                      'max': round(max(samples), 3) if samples else None}
    block['windows'].append({'phase': phase[:64], 'startedAt': started, 'endedAt': ended,
                             'frameCount': len(frames), 'phases': stats})
    block['worstFrames'] = sorted(block['worstFrames'] + worst, key=lambda r: r['rankMs'], reverse=True)[:16]
    # Preserve the first/baseline window and recent windows; omission is explicit.
    while len(block['windows']) > 24 or len(json.dumps(block, separators=(',', ':')).encode()) > 60 * 1024:
        if len(block['windows']) <= 1:
            break  # fixed scalar schema + 16 rows is below the bound
        del block['windows'][1]
        block['omittedWindows'] += 1
