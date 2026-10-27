"""Grouped typed admission over real retained storage; synthetic RGB pixels only."""
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator import admission, jobs
from tests.test_retained_inputs import saved  # noqa: F401


def member():
    return SimpleNamespace(inputs=[SimpleNamespace(id=name, type='image.2d.rgb8', widget=None)
                                  for name in ('left', 'right')],
                           outputs=[SimpleNamespace(type='table.objects')], scientific={})


@pytest.mark.parametrize('problem', ['missing', 'unknown', 'aggregate'])
def test_group_refusal_precedes_any_retained_pixel_decode(saved, monkeypatch, problem):
    app, _, reference, _ = saved
    references = {name: {'kind': 'result', **reference} for name in ('left', 'right')}
    if problem == 'missing':
        del references['right']
    elif problem == 'unknown':
        references['other'] = references['left']
    else:
        monkeypatch.setattr(admission, 'MAX_BYTES', 300)  # two 189-byte arrays
    decodes = []
    original = cv2.imdecode
    monkeypatch.setattr(cv2, 'imdecode', lambda *a, **kw: decodes.append(True) or original(*a, **kw))
    with pytest.raises(admission.Refused):
        admission.bind(member(), references, hub=app.hub, session=app.day.sid)
    assert decodes == []


def test_retained_decode_and_admission_hashing_share_memory_hold(saved, monkeypatch):
    app, _, reference, _ = saved
    before = jobs.MEMORY.reserved_mb
    holds = []
    decode, admit = cv2.imdecode, admission.admit
    def observed_decode(*args, **kwargs):
        holds.append(jobs.MEMORY.reserved_mb - before)
        return decode(*args, **kwargs)
    def observed_admit(*args, **kwargs):
        holds.append(jobs.MEMORY.reserved_mb - before)
        return admit(*args, **kwargs)
    monkeypatch.setattr(cv2, 'imdecode', observed_decode)
    monkeypatch.setattr(admission, 'admit', observed_admit)
    captured = admission.bind(member(), {name: {'kind': 'result', **reference} for name in ('left', 'right')},
                              hub=app.hub, session=app.day.sid)
    assert holds and min(holds) >= 3 * 2 * 7 * 9 * 3 / (1 << 20)
    assert jobs.MEMORY.reserved_mb == before
    assert all(np.array_equal(row.array, np.full((7, 9, 3), 71, np.uint8)) for row in captured.typed)
    assert all(not row.array.flags.writeable for row in captured.typed)


@pytest.mark.parametrize('problem', ['memory', 'mutated', 'wrong-shape'])
def test_retained_preflight_failure_never_decodes_and_releases_holds(saved, monkeypatch, problem):
    from contextlib import contextmanager
    from orchestrator import retained_inputs, machine
    app, row, reference, _ = saved
    before = jobs.MEMORY.reserved_mb
    if problem == 'memory':
        monkeypatch.setattr(machine, 'free_mb', lambda: 0)
    elif problem == 'mutated':
        reserve = jobs.MEMORY.reserve
        @contextmanager
        def changed_after_preflight(mb):
            with reserve(mb):
                store = app.hub.resources.results
                (store.root / row['day'] / row['pictures'][0]).write_bytes(b'changed after preflight')
                yield
        monkeypatch.setattr(jobs.MEMORY, 'reserve', changed_after_preflight)
    else:
        layout = retained_inputs.layout
        monkeypatch.setattr(retained_inputs, 'layout',
                            lambda *a: {**layout(*a), 'shape': [1, 1, 3]})
    decodes = []
    original = cv2.imdecode
    monkeypatch.setattr(cv2, 'imdecode', lambda *a, **kw: decodes.append(True) or original(*a, **kw))
    with pytest.raises(admission.Refused):
        admission.bind(member(), {name: {'kind': 'result', **reference} for name in ('left', 'right')},
                       hub=app.hub, session=app.day.sid)
    assert decodes == []
    assert jobs.MEMORY.reserved_mb == before


def test_incompatible_native_peer_prevents_retained_decode(saved, monkeypatch):
    app, _, reference, _ = saved
    declared = member()
    declared.inputs[1].type = 'image.3d.zyx.float32'
    source = SimpleNamespace(shape=(2, 3, 4), dtype='<f4')
    monkeypatch.setattr(admission, 'native_source', lambda *a: (source, {'axes': ['t', 'y', 'x']}, {}))
    decodes = []
    original = cv2.imdecode
    monkeypatch.setattr(cv2, 'imdecode', lambda *a, **kw: decodes.append(True) or original(*a, **kw))
    with pytest.raises(admission.Refused, match='axes'):
        admission.bind(declared, {'left': {'kind': 'result', **reference}, 'right': {'kind': 'array'}},
                       hub=app.hub, session=app.day.sid)
    assert decodes == []
