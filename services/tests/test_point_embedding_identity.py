"""Exact pixel identity, not frame numbering, owns a model's cached embedding."""
from collections import OrderedDict
from contextlib import nullcontext
from types import SimpleNamespace
import numpy as np
from gpu_worker.worker import Models


def host():
    calls=[]
    class Batch(dict):
        def to(self,device):return self
    def processor(*,images,return_tensors):
        calls.append(images.copy())
        return Batch(pixel_values=images.copy(),original_sizes=images.shape[:2])
    model=SimpleNamespace(get_image_embeddings=lambda pixels:pixels.copy())
    result=Models.__new__(Models);result.embeddings=OrderedDict();result.device='cpu'
    result.torch=SimpleNamespace(inference_mode=nullcontext)
    result.point_model=lambda name:(processor,model)
    return result,calls


def test_identical_pixels_across_capture_ids_reuse_only_the_embedding():
    model,calls=host();pixels=np.arange(36,dtype='uint8').reshape(3,4,3)
    first=model.embed('model-a',1,pixels)
    second=model.embed('model-a',2,pixels.copy())
    assert first is second and len(calls)==1
    changed=pixels.copy();changed[0,0,0]+=1
    third=model.embed('model-a',2,changed)
    assert third is not first and len(calls)==2
    np.testing.assert_array_equal(third[0],changed)
    np.testing.assert_array_equal(first[0],pixels)


def test_shape_dtype_model_and_noncontiguous_pixel_order_are_part_of_identity():
    model,calls=host();pixels=np.arange(36,dtype='uint8').reshape(3,4,3)
    first=model.embed('model-a',1,pixels)
    strided=np.empty((3,8,3),dtype='uint8')[:,::2,:];strided[:]=pixels
    assert not strided.flags.c_contiguous
    assert model.embed('model-a',9,strided) is first
    assert model.embed('model-a',1,pixels.reshape(4,3,3)) is not first
    assert model.embed('model-a',1,pixels.astype('int8')) is not first
    assert model.embed('model-b',1,pixels) is not first
    assert model.embed('model-a',1,pixels[:,::-1]) is not first
    assert len(calls)==5


def test_cache_retains_six_unique_contents_and_recently_reused_content():
    model,calls=host()
    data=[np.full((3,4,3),i,dtype='uint8') for i in range(8)]
    first=model.embed('model-a',0,data[0])
    for i in range(1,6):model.embed('model-a',i,data[i])
    assert model.embed('model-a',99,data[0]) is first
    for i in range(6,8):model.embed('model-a',i,data[i])
    assert len(model.embeddings)==6
    assert model.embed('model-a',100,data[0]) is first
    before=len(calls);model.embed('model-a',1,data[1]);assert len(calls)==before+1
