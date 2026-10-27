"""Bounded typed admission regressions; synthetic arrays, no model or provider."""
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator import admission
from orchestrator.scientific_projects import ProjectStore
from tests.test_typed_admission import installed  # noqa: F401
from tests.test_env_runner import home  # noqa: F401


def volume_member():
    return SimpleNamespace(inputs=[SimpleNamespace(id='volume', type='image.3d.zyx.float32', widget=None)],
                           outputs=[SimpleNamespace(type='table.objects')], scientific={})


@pytest.mark.parametrize('axes', [['t', 'y', 'x'], ['x', 'y', 'z']])
def test_retained_volume_requires_zyx_before_reading(tmp_path, monkeypatch, axes):
    store = ProjectStore(tmp_path / 'projects')
    first = store.create('s1', {'kind': 'scene', 'title': 'volume', 'state': {}, 'resources': [], 'runtimes': []})
    array = np.zeros((2, 3, 4), dtype='<f4')
    draft = store.array_draft(first.id, 's1', component='array.volume', shape=list(array.shape), dtype=array.dtype.str,
                              axes=axes, chunks=list(array.shape), scientific={'semantic': 'intensity'},
                              max_written_bytes=array.nbytes)
    draft.write((0, 0, 0), array)
    ref = draft.finish()
    saved = store.save(first.id, 's1', {**first.document, 'resources': [ref]}, expected_revision=1)
    source_of = ProjectStore.array_source
    reads = []

    def counted(self, *args, **kwargs):
        source = source_of(self, *args, **kwargs)
        return SimpleNamespace(shape=source.shape, dtype=source.dtype, descriptor=source.descriptor,
                               read=lambda *a: reads.append(True) or source.read(*a))
    monkeypatch.setattr(ProjectStore, 'array_source', counted)
    with pytest.raises(admission.Refused, match='axes'):
        admission.bind(volume_member(), {'volume': {'kind': 'array', 'session': 's1', 'id': saved.id,
                       'revision': saved.revision, 'component': 'array.volume'}}, store=store, session='s1')
    assert reads == []


@pytest.mark.parametrize('axes', [None, ['t', 'y', 'x'], ['x', 'y', 'z'], ['z', 'y', 'x']])
def test_volume_admission_requires_explicit_ordered_axes(axes):
    descriptor = {} if axes is None else {'axes': axes}
    resolved = {'volume': (np.zeros((2, 3, 4), dtype='<f4'), descriptor, {'input': 'volume'})}
    if axes == ['z', 'y', 'x']:
        assert admission.admit(volume_member(), resolved).typed[0].descriptor['axes'] == axes
    else:
        with pytest.raises(admission.Refused, match='axes'):
            admission.admit(volume_member(), resolved)


@pytest.mark.parametrize('failure', ['aggregate', 'header', 'decoded', 'truncated', 'axes', 'missing-axes'])
def test_headless_npy_refuses_before_loading_any_payload(installed, tmp_path, monkeypatch, failure):
    import asyncio
    import hashlib
    import io
    from orchestrator.capability_runs import ResourceHost, Refused, run_on_resource
    from tests.test_typed_admission import authorized, LABELS, INTENSITY
    sources = {'labels': authorized(tmp_path, 'labels', LABELS),
               'intensity': authorized(tmp_path, 'intensity', INTENSITY)}
    if failure == 'aggregate':
        sources = {'labels': authorized(tmp_path, 'labels', np.tile(LABELS, (3, 3))),
                   'intensity': authorized(tmp_path, 'intensity', np.tile(INTENSITY, (3, 3)))}
        monkeypatch.setattr(admission, 'MAX_BYTES', 400)  # each decoded input is 216, together 432
    elif failure in ('axes', 'missing-axes'):
        sources['intensity']['descriptor'] = {'axes': ['x', 'y']} if failure == 'axes' else {}
    else:
        from pathlib import Path
        path = Path(sources['intensity']['path'])
        if failure == 'header':
            path.write_bytes(b'\x93NUMPY\x02\x00' + (2**30).to_bytes(4, 'little'))
        elif failure == 'decoded':
            header = io.BytesIO()
            np.lib.format.write_array_header_1_0(header, {'descr': '<u2', 'fortran_order': False,
                                                        'shape': (2**32, 2**32)})
            path.write_bytes(header.getvalue())
        else:
            path.write_bytes(path.read_bytes()[:-1])
        sources['intensity']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    reads = []
    from pathlib import Path
    original_bytes = Path.read_bytes
    payload_reads = []
    def read_bytes(path):
        if str(path) in {value['path'] for value in sources.values()}:
            payload_reads.append(path)
        return original_bytes(path)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    original = np.load
    monkeypatch.setattr(np, 'load', lambda *a, **kw: reads.append(True) or original(*a, **kw))
    with pytest.raises(Refused):
        asyncio.run(run_on_resource(ResourceHost(tmp_path / 'host'), 'measure-tools#label-means', sources))
    assert reads == [] and payload_reads == []


def test_same_as_is_checked_by_shared_admission():
    member = SimpleNamespace(inputs=[SimpleNamespace(id=name, type='image.2d.gray16', widget=None)
                                    for name in ('left', 'right')],
                             outputs=[SimpleNamespace(type='table.objects')],
                             scientific={'inputs': {'left': {'same_as': [{'peer': 'right', 'fields': ['unit']}]}}})
    resolved = {name: (np.zeros((2, 3), dtype='<u2'), {'axes': ['y', 'x'], 'unit': unit}, {'input': name})
                for name, unit in (('left', 'um'), ('right', 'mm'))}
    with pytest.raises(admission.Refused, match='unit'):
        admission.admit(member, resolved)


@pytest.mark.parametrize('mutate', [False, True])
def test_headless_npy_memory_hold_covers_decode_and_releases(installed, tmp_path, monkeypatch, mutate):
    import asyncio
    from pathlib import Path
    from orchestrator import jobs
    from orchestrator.capability_runs import ResourceHost, Refused, run_on_resource
    from tests.test_typed_admission import authorized, LABELS, INTENSITY
    sources = {'labels': authorized(tmp_path, 'labels', LABELS),
               'intensity': authorized(tmp_path, 'intensity', INTENSITY)}
    original = np.load
    holds = []
    before = jobs.MEMORY.reserved_mb
    def load(*args, **kwargs):
        holds.append(jobs.MEMORY.reserved_mb - before)
        if mutate and len(holds) == 1:
            path = Path(sources['intensity']['path'])
            path.write_bytes(path.read_bytes() + b'changed')
        return original(*args, **kwargs)
    monkeypatch.setattr(np, 'load', load)
    run = run_on_resource(ResourceHost(tmp_path / 'host'), 'measure-tools#label-means', sources)
    if mutate:
        with pytest.raises(Refused, match='changed'):
            asyncio.run(run)
    else:
        assert asyncio.run(run)['result']
    assert holds and min(holds) > 0
    assert jobs.MEMORY.reserved_mb == before


@pytest.mark.parametrize('problem', ['missing-axes', 'wrong-axes', 'decoded-size'])
def test_bundle_proof_refuses_invalid_typed_reference_before_adapter(home, monkeypatch, problem):
    from tests.test_typed_admission import contribute
    from tests.test_env_runner import standin_uv
    from gpu_worker import manifests
    from orchestrator import envs, env_runner
    import io
    owner = contribute(home)
    folder = home / 'repo/models/measure-tools'
    if problem == 'decoded-size':
        header = io.BytesIO()
        np.lib.format.write_array_header_1_0(header, {'descr': '<u2', 'fortran_order': False,
                                                    'shape': (2**32, 2**32)})
        (folder / 'example-intensity.npy').write_bytes(header.getvalue())
    else:
        manifest = folder / 'model.toml'
        text = manifest.read_text()
        text = text.replace('axes = ["y", "x"], ', '' if problem == 'missing-axes' else 'axes = ["x", "y"], ')
        manifest.write_text(text)
    (folder / 'adapter.py').write_text('raise RuntimeError("the invalid proof reached adapter code")\n')
    manifests.manifests.cache_clear()
    owner = manifests.get(owner.key)
    standin_uv(home, monkeypatch)
    with pytest.raises(envs.Failed) as refused:
        envs.install(owner)
    reason = str(refused.value)
    assert ('axes' if problem != 'decoded-size' else 'bounded numeric dimensions') in reason
    assert 'the invalid proof reached adapter code' not in reason
    assert env_runner.in_use(owner) is None


def test_general_typed_scientific_port_contract_is_explicitly_unsupported(tmp_path):
    from gpu_worker import bundle_contract
    from tests.test_bundle_contract import declaration
    owner = SimpleNamespace(key='mine/imaging', id='imaging', runner='python-env', requires={'catalog': 3},
                            folder=str(tmp_path), version='1', licence=('apache-2.0',))
    raw = declaration(inputs=[{'id': 'left', 'type': 'image.2d.gray16'}, {'id': 'right', 'type': 'image.2d.gray16'}],
                      scientific={'inputs': {'left': {'same_as': [{'peer': 'right', 'fields': ['unit']}]}, 'right': {}},
                                  'outputs': {'table': {}}})
    with pytest.raises(ValueError, match='scientific ports currently require one RGB'):
        bundle_contract.parse_members([raw], owner)
