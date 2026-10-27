"""Synthetic returned-before-finished callbacks; real thread/snapshot and output transport guards."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from env_worker.native_edits import OUTPUT_FORMAT
from tests.test_scientific_output_coordinates import session, proof
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa: F401
from tests.waiting import until, HANG_S


def outputs():
    host, image, first = session()
    accepted = image.data.copy(); reads = []
    def read(bounds, *, view=False):
        assert view
        part = accepted[tuple(slice(*pair) for pair in bounds)].copy()
        reads.append(part.nbytes)
        return part
    host.editable.inputs['labels'].read = read
    layers = []
    for i, role in enumerate(('flows', 'cellprob', 'outlines', 'masks')):
        layer = first if i == 0 else SimpleNamespace(**vars(first))
        layer.name = role; layer._type_string = 'image' if i < 2 else 'labels'
        layer.rgb = i == 0
        layer.data = np.stack([accepted]*3, axis=-1) if i == 0 else accepted.copy()
        layer.data_to_world = lambda point: tuple(float(v) for v in point)
        if i: host.viewer.layers.append(layer)
        layers.append(layer)
    host._layers = [(str(i+1)*32, layer) for i, layer in enumerate(layers)]
    return host, image, layers, accepted, reads


def declare(host, image, layers):
    for layer in layers:
        host.bind_view_output(version=2, layer=layer, key=layer.name, coordinates_from=image,
            meaning={'semantic':'intensity' if layer._type_string == 'image' else 'instance-labels', 'axes':['y','x']})


def rows(host, layers):
    result = []
    for (key, layer) in host._layers:
        info = host.describe_output(key)
        axes = ['y','x','color'] if layer.rgb else ['y','x']
        result.append({'layer':key, 'metadata':info,
            'meaning':{'semantic':'intensity' if layer._type_string == 'image' else 'instance-labels','axes':['y','x']},
            'components':[{'name':'output','edit':{'format':OUTPUT_FORMAT, 'token':key,
                'shape':list(layer.data.shape),'dtype':layer.data.dtype.str,'axes':axes,'chunks':list(layer.data.shape)}}]})
    return result


def test_four_returned_outputs_keep_identity_but_publish_only_after_worker_finishes():
    host, image, layers, accepted, reads = outputs()
    returned, release = threading.Event(), threading.Event()
    def worker():
        returned.set(); release.wait(HANG_S)
    thread = threading.Thread(target=worker); thread.start()
    try:
        until(returned.is_set)
        declare(host, image, layers)
        assert [host.view_bindings.key_for(layer) for layer in layers] == ['flows','cellprob','outlines','masks']
        assert reads == []  # registering declarations neither reads source values nor claims completion
        for key, _ in host._layers:
            with pytest.raises(ValueError, match='Stop background Python work'):
                host.describe_output(key)
            with pytest.raises(ValueError, match='Stop background Python work'):
                host.stage_output(key, {}, {}, {}, lambda *_:pytest.fail('Transferred while worker active'))
    finally:
        release.set(); thread.join(HANG_S)
    assert not thread.is_alive()
    selections = rows(host, layers)
    assert all(row['metadata']['coordinatesFrom']['sha256'] == proof(accepted) for row in selections)
    received = {}
    def exchange(header, body):
        received.setdefault(header['token'], bytearray()).extend(body)
        return {'ok':True, 'kind':OUTPUT_FORMAT, 'index':header['index'], 'bytes':len(body)}, b''
    host.stage_outputs(selections, exchange, snapshot='memory')
    assert received == {key:layer.data.tobytes() for key, layer in host._layers}
    assert reads == [accepted.nbytes]  # immutable input proof shared across the four declarations


@pytest.mark.parametrize('when', ['before-bind','after-bind'])
def test_dirty_input_cannot_become_baseline_at_first_stable_save(when):
    host, image, layers, _, _ = outputs()
    if when == 'before-bind': image.data[0,0] = 999
    declare(host, image, layers)
    if when == 'after-bind': image.data[0,0] = 999
    for key, _ in host._layers:
        with pytest.raises(ValueError, match='input.*changed|accepted input'):
            host.describe_output(key)


def test_accepted_source_proof_uses_bounded_revocable_native_grant():
    from env_worker import native_regions
    from orchestrator.native_regions import NativeRegionInputs, Source
    host, image, layers, accepted, _ = outputs()
    source = host.editable.inputs['labels']
    reads = []
    def read(region):
        reads.append(region); return accepted[region]
    descriptor = {'reference':source.reference, 'semantic':'intensity',
        'dtype':accepted.dtype.name,'shape':list(accepted.shape),'axes':['y','x']}
    grant = NativeRegionInputs({'labels':Source(accepted.shape,accepted.dtype.str,('y','x'),descriptor,read)})
    source.read = native_regions.bind(grant.specifications(),grant.answer)['labels'].read
    declare(host, image, layers)
    grant.close()
    with pytest.raises(ValueError, match='closed'):
        host.describe_output('1'*32)
    assert reads == []


def test_four_outputs_share_one_bounded_accepted_proof_through_native_transport():
    from env_worker import native_regions
    from orchestrator.native_regions import NativeRegionInputs, Source
    host, image, layers, _, _ = outputs()
    accepted = np.arange(600_000,dtype='>u2').reshape(600,1000)
    source = host.editable.inputs['labels']; source.shape = accepted.shape
    image.data = accepted.copy()
    for layer in layers:
        layer.data = np.stack([accepted]*3,axis=-1) if layer.rgb else accepted.copy()
    reads = []
    def read(region):
        part = accepted[region]; reads.append(part.nbytes); return part
    descriptor = {'reference':source.reference, 'semantic':'intensity',
        'dtype':accepted.dtype.name,'shape':list(accepted.shape),'axes':['y','x']}
    grant = NativeRegionInputs({'labels':Source(accepted.shape,accepted.dtype.str,('y','x'),descriptor,read)})
    source.read = native_regions.bind(grant.specifications(),grant.answer)['labels'].read
    try:
        declare(host,image,layers)
        proofs = [host.describe_output(key)['coordinatesFrom']['sha256'] for key,_ in host._layers]
        assert len(set(proofs)) == 1
        assert len(reads) > 1 and max(reads) <= 1 << 20 and sum(reads) == accepted.nbytes
        image.data[0,0] = 999
        with pytest.raises(ValueError, match='input.*changed'):
            host.describe_output('1'*32)
        assert sum(reads) == accepted.nbytes  # cache cannot replace the fresh mutable-layer check
    finally:
        grant.close()
