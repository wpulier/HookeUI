"""Synthetic exact timestamps verify diagnostic arithmetic, never performance."""
import json
from types import SimpleNamespace

from sim.frame_observations import retain


def test_actual_boundaries_missing_bridge_and_bounded_worst_frames():
    out = SimpleNamespace(observations=[])
    frames = [(10.1, {'id':1,'seq':8,'ba':10.,'t':10.02,'an':10.05,'sent':10.06}, b'not retained'),
              (10.3, {'id':2,'seq':9,'t':10.22,'an':10.25,'sent':10.26}, b'not retained'),
              (10.5, {'id':3,'seq':10,'ba':10.4,'t':10.42,'an':10.41,'sent':10.46}, b'not retained')]
    retain(out, 'baseline', frames, 9., 11.)
    block = out.observations[0]; stats = block['windows'][0]['phases']
    assert stats['intakeMs'] == {'count':2,'missing':1,'invalidOrder':0,'p50':20.,'p95':20.,'max':20.}
    assert stats['analysisMs']['invalidOrder'] == 1 and stats['analysisMs']['p95'] == 30.
    assert stats['publishMs']['p50'] == 10. and stats['deliveryMs']['max'] == 40.
    assert stats['appToReceiverMs']['p95'] == 80. and stats['bridgeToReceiverMs']['missing'] == 1
    assert stats['arrivalGapMs']['max'] == 200.
    missing = next(r for r in block['worstFrames'] if r['id'] == 2)
    assert missing['ba'] is None and 'intakeMs' not in missing['durationsMs']
    assert missing['seq'] == 9 and missing['windowStartedAt'] == 9. and missing['receivedAt'] == 10.3
    for i in range(30):
        retain(out, 'update', [(20+i, {'id':i+4,'seq':i+11,'t':19+i,'an':19.1+i,'sent':19.2+i}, b'')], 19+i, 21+i)
    assert len(block['windows']) == 24 and block['omittedWindows'] == 7
    assert block['windows'][0]['phase'] == 'baseline' and len(block['worstFrames']) == 16
    assert len(json.dumps(out.observations).encode()) < 64*1024
    assert 'not retained' not in json.dumps(out.observations)
