"""Point/box decoding reuses the image already encoded for that frame.

Real upstream preprocessing checks coordinate/label equivalence; a deterministic model
keeps this regression independent of downloaded weights and accelerator availability.
"""
from collections import OrderedDict
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import torch
from transformers import Sam2ImageProcessor, Sam2Processor

from gpu_worker.worker import Models


@pytest.mark.parametrize('shape', [(91, 173), (173, 91)])
@pytest.mark.parametrize('prompt', [
    {'points': [[13, 27], [51, 63]], 'labels': [1, 0]},
    {'box': [9, 11, 69, 71]},
    {'points': [[13, 27]], 'box': [9, 11, 69, 71]},
])
def test_prompts_preserve_coordinates_without_reprocessing_cached_pixels(shape, prompt):
    processor = Sam2Processor(Sam2ImageProcessor(size={'height': 64, 'width': 64}))
    rgb = np.random.default_rng(14).integers(0, 256, (*shape, 3), dtype=np.uint8)
    jpeg = cv2.imencode('.jpg', rgb)[1].tobytes()
    image = cv2.cvtColor(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
    kwargs = {}
    if 'points' in prompt:
        kwargs.update(input_points=[[prompt['points']]], input_labels=[[prompt.get('labels', [1])]])
    if 'box' in prompt:
        kwargs['input_boxes'] = [[prompt['box']]]
    expected = processor(images=image, return_tensors='pt', **kwargs)
    encoded, model_calls = [], []

    class CountingProcessor:
        def __call__(self, **kwargs):
            if kwargs.get('images') is not None:
                encoded.append(kwargs['images'].shape)
            return processor(**kwargs)

        post_process_masks = processor.post_process_masks

    class Model:
        def get_image_embeddings(self, pixels):
            return pixels

        def __call__(self, **call):
            model_calls.append(call)
            for key in ('input_points', 'input_labels', 'input_boxes'):
                if key in expected:
                    assert torch.equal(call[key], expected[key])
                else:
                    assert call[key] is None
            assert call['multimask_output'] is True
            masks = torch.full((1, 1, 3, 64, 64), -10.)
            masks[:, :, :, 10:20, 10:20] = 10.
            return SimpleNamespace(pred_masks=masks, iou_scores=torch.tensor([[[.9, .8, .7]]]))

    models = Models.__new__(Models)
    models.torch, models.device = torch, torch.device('cpu')
    models.embeddings = OrderedDict()
    models.point_model = lambda _: (CountingProcessor(), Model())
    request = dict(frame_id=1, jpeg=jpeg, candidates=True, **prompt)
    first = models.segment(request)
    repeated = models.segment(request)
    next_frame = models.segment({**request, 'frame_id': 2})
    assert len(encoded) == 1, 'identical pixels are encoded once; each capture still receives its own prompt decoding'
    assert len(model_calls) == 3
    assert model_calls[0]['image_embeddings'] is model_calls[1]['image_embeddings']
    assert model_calls[2]['image_embeddings'] is model_calls[0]['image_embeddings']
    assert {k: v for k, v in first.items() if k != 'ms'} == {k: v for k, v in next_frame.items() if k != 'ms'}
    for result in (first, repeated, next_frame):
        assert result['area'] > 0 and result['polygons'] and len(result['candidates']) == 3
    assert {k: v for k, v in first.items() if k != 'ms'} == {k: v for k, v in repeated.items() if k != 'ms'}
