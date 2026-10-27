"""Built-in declaration discovery; synthetic Fiji proof/transport, no JVM or model process."""
import asyncio
import json

import cv2
import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import commands, fiji_bindings, fiji_generations, kinds, operations
from tests.test_native_picture_inputs import input_at
from tests.standin_workers import build_app
from tests.test_sources import Client


@pytest.fixture
def declared(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path/'catalog'))
    folder = fiji_generations.root()/'1'; (folder/'Fiji').mkdir(parents=True)
    receipt = {'n': 1, 'base': {'version': 'synthetic Fiji', 'sha256': 'a'*64}, 'files': [], 'sites': [],
               'proofs': {fiji_bindings.BINDING: {**fiji_bindings.proof_identity(), 'passed': True}}}
    (folder/'receipt.json').write_text(json.dumps(receipt))
    (folder.parent/'current').write_text('1')
    return folder, receipt


def test_catalog_contract_discovers_existing_adapter_once_without_replacing_it(declared):
    m = catalog.get('fiji-analyze-particles')
    assert m.contract and m.data_inputs[0].type == 'image.2d.rgb8'
    assert m.outputs[0].type == 'labels.instance.2d' and not m.params and not m.fiji
    prior = operations.ADAPTERS['cells', m.id]
    kinds.register()
    rows = [row for row in kinds.available() if row['key'] == m.key]
    assert len(rows) == 1 and rows[0]['runnable'], rows
    assert rows[0]['params'] == []
    assert operations.ADAPTERS['cells', m.id] is prior
    assert kinds.manifest_of(rows[0]) == m
    # Same declaration cannot let a contributed package borrow the app binding.
    from dataclasses import replace
    assert catalog._fiji_problems(replace(m, namespace='mine'))


def test_missing_generation_proof_is_not_runnable_and_shared_command_refuses(declared, tmp_path):
    folder, receipt = declared
    receipt['proofs'] = {}; (folder/'receipt.json').write_text(json.dumps(receipt))
    built = build_app(tmp_path/'home'); owner, _, reference = input_at(built)
    row = kinds.find('fiji-analyze-particles')
    assert row and not row['runnable']
    async def run():
        client = Client()
        await commands.handle(built, client, {'type': 'action', 'capability': 'catalog:fiji-analyze-particles',
                                             'args': {}, 'resource': reference})
        assert client.replies[-1]['ok'] is False
        assert 'prov' in str(client.replies[-1]).lower()
    asyncio.run(run())
    from orchestrator.evidence import evidence_of
    assert not evidence_of(built.hub).items


@pytest.mark.parametrize('use_gpu', [True, False], ids=['stand-in-gpu', 'no-gpu'])
def test_existing_fiji_adapter_receives_exact_native_values_and_fixed_settings(declared, tmp_path, monkeypatch, use_gpu):
    if use_gpu:
        built = build_app(tmp_path/'home')
    else:
        from orchestrator.app import App
        from orchestrator.paths import Paths
        built = App.build('http://127.0.0.1:1', Paths.under(tmp_path/'home'), use_gpu=False)
    owner, pixels, reference = input_at(built)
    impl = operations.OPERATIONS['cells'].impl('fiji-analyze-particles')
    assert impl.worker == 'analysis'
    assert operations.availability(built.hub, impl) == ''
    received = []
    def analyze(path, *, n):
        from orchestrator.evidence import encode_labels
        received.append((cv2.imread(str(path), cv2.IMREAD_UNCHANGED), n))
        # Empty is a valid synthetic transport result, not a real ImageJ execution.
        return {'width': 40, 'height': 32, 'generation': n, 'particles': [],
                'labels': encode_labels(np.zeros((32,40), np.uint16)), 'maskContract': fiji_bindings.MASK_CONTRACT,
                'runtime': {}, 'threshold': 80}
    monkeypatch.setattr(fiji_bindings, 'analyze_particles', analyze)
    async def run():
        client = Client()
        await commands.handle(built, client, {'type': 'action', 'capability': 'catalog:fiji-analyze-particles',
                                             'args': {}, 'resource': reference})
        assert client.replies[-1]['ok'], client.replies[-1]
        from orchestrator.evidence import evidence_of
        item = next(iter(evidence_of(built.hub).items.values()))
        assert item.session == owner.session
        assert item.producer['capturedNativeInput']['nativeSource']['revisionDigest'] == owner.digest
        assert item.producer['settings'] == {'conversion': '8-bit', 'thresholdMethod': 'Default', 'minimumSizePx2': 20.0}
        assert received[0][1] == 1 and np.array_equal(received[0][0], pixels)
        await commands.handle(built, client, {'type': 'action', 'capability': 'catalog:fiji-analyze-particles',
                                             'args': {'threshold': 2}, 'resource': reference})
        assert client.replies[-1]['ok'] is False and len(received) == 1
    asyncio.run(run())
