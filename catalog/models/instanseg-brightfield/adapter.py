"""InstanSeg's brightfield nuclei model for MicroscopeMind (runner python-env): the picture in, a label map of nuclei out.
Runs only in this entry's own environment; its weights are the catalog's pinned file (MICROMIND_ENTRY_FILES)."""
import os

import numpy as np

_model = None


def segment(picture, params):
    global _model
    import torch
    from instanseg import InstanSeg

    if _model is None:
        torch.set_num_threads(2)
        weights = os.path.join(os.environ["MICROMIND_ENTRY_FILES"], "instanseg.pt")
        _model = InstanSeg(torch.jit.load(weights, map_location="cpu"), device="cpu", verbosity=0)
    labels = _model.eval_small_image(np.moveaxis(picture, -1, 0).astype(np.float32), pixel_size=params.get("pixel_size"),
                                     return_image_tensor=False, target="nuclei")
    return np.asarray(labels.cpu()).reshape(picture.shape[:2]).astype(np.int32)
