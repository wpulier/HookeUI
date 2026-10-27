"""Bounded host diagnostics: synthetic CPU model, no downloads or accelerator work."""
import asyncio
from collections import OrderedDict
from contextlib import nullcontext
from types import SimpleNamespace

import cv2
import httpx
import numpy as np

from gpu_worker.click_diagnostics import ClickProbe
from gpu_worker.worker import Models
from orchestrator.execution import _facts
from sim.click_diagnostics import collect


def test_probe_requires_explicit_opt_in_and_execution_scope(monkeypatch):
    monkeypatch.delenv('MICROMIND_CLICK_DIAGNOSTICS', raising=False)
    assert ClickProbe.start({'_execution': True}) is None
    monkeypatch.setenv('MICROMIND_CLICK_DIAGNOSTICS', '1')
    assert ClickProbe.start({}) is None
    probe = ClickProbe.start({'_execution': True})
    probe.mark('Embed'); probe.mark('Submit')
    facts = probe.finish({})['_clickDiagnostics']
    assert _facts(facts) == facts
    assert all(value >= 0 for value in facts.values())
    assert abs(facts['probeWallMs'] - sum(facts[f'probe{phase}WallMs'] for phase in ('Embed', 'Submit', 'Readback'))) < .003


def test_real_segment_path_preserves_result_and_reports_cache_hit(monkeypatch):
    class Tensor(np.ndarray):
        def cpu(self): return self
        def float(self): return self
        def numpy(self): return np.asarray(self)
    def tensor(value): return np.asarray(value).view(Tensor)
    class Batch(dict):
        def to(self, device): return self
    class Processor:
        def __call__(self, **kwargs):
            if 'images' in kwargs:
                return Batch(pixel_values=kwargs['images'], original_sizes=tensor([[32, 32]]))
            return Batch()
        def post_process_masks(self, masks, original_sizes): return [[masks]]
    mask = tensor(np.zeros((1, 32, 32), dtype=bool)); mask[0, 4:12, 4:12] = True
    class Model:
        def get_image_embeddings(self, value): return value
        def __call__(self, **kwargs):
            return SimpleNamespace(pred_masks=mask, iou_scores=tensor([[[.9]]]))
    models = Models.__new__(Models)
    models.device = 'cpu'; models.embeddings = OrderedDict()
    models.torch = SimpleNamespace(inference_mode=nullcontext)
    models.point_model = lambda name: (Processor(), Model())
    req = dict(frame_id=1, jpeg=cv2.imencode('.jpg', np.zeros((32,32,3), dtype=np.uint8))[1].tobytes(), points=[[8,8]], _execution=True)
    monkeypatch.delenv('MICROMIND_CLICK_DIAGNOSTICS', raising=False)
    baseline = models.segment(req)
    models.embeddings.clear()
    monkeypatch.setenv('MICROMIND_CLICK_DIAGNOSTICS', '1')
    cold = models.segment(req); warm = models.segment(req)
    assert cold.pop('_clickDiagnostics')['probeEmbeddingHit'] is False
    assert warm.pop('_clickDiagnostics')['probeEmbeddingHit'] is True
    for result in (cold, warm):
        assert {k:v for k,v in result.items() if k != 'ms'} == {k:v for k,v in baseline.items() if k != 'ms'}
    mask[:] = False
    assert models.segment(req)['_clickDiagnostics']['probeEmbeddingHit'] is True


def test_receipts_keep_chronology_and_exact_identity_without_unrelated_rows():
    requests = []
    def respond(request):
        requests.append(request.url.path)
        if request.url.path == '/api/execution':
            return httpx.Response(200, json={'entries': [
                {'id':'b', 'at':12, 'kind':'click'}, {'id':'a','at':11,'kind':'click'},
                {'id':'old','at':2,'kind':'click'}, {'id':'cells','at':13,'kind':'cells'}]})
        return httpx.Response(200, json={'steps': [
            {'id':request.url.path + '.1','frameId':7,'rpcId':8,'probeWallMs':50,'secret':'not copied'},
            {'operation':'unrelated','adapterMs':30}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            observations = [{'frameId':7,'roundTripMs':100}, {'frameId':8,'roundTripMs':60}]
            result = await collect(SimpleNamespace(http=client, orch_url='http://test'), observations,10,14)
            assert result['clicks'] == observations
            assert [row['id'] for row in result['receipts']] == ['a','b']
            assert result['receipts'][0]['steps'][0]['rpcId'] == 8
            assert 'secret' not in str(result)
    asyncio.run(run())
    assert requests == ['/api/execution','/api/execution/a','/api/execution/b']
