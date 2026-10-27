"""A picture of one thing as a vector, for matching the user's own examples (orchestrator/taught.py,
option TAUGHT) and outlines named earlier today (orchestrator/seen.py, option SEEN). Which model
is option EMBED (orchestrator/options.py); every vector says which model made it, and vectors of
two models are never compared.

  dinov2-small  DINOv2 small (Apache-2.0): 384 numbers, the class token after the final layer
                norm. 13 ms a crop on the heavy worker (docs/bench/taught.md).
  bioclip-2     BioCLIP 2 (MIT; imageomics/bioclip-2, a CLIP ViT-L/14 trained on the Tree of Life):
                768 numbers, the image tower's projected embedding (docs/bench/seen.md).
  dinov3-small  DINOv3 ViT-S/16 (Meta's DINOv3 licence; gated weights on Hugging Face, accepted there first):
                384 numbers, the class token after the final layer norm (M3: an implementation only, not
                yet benched on our cells; seen.py and taught.py claim no calibration for it).

Runs in the heavy GPU worker (op `embed`, {model}); bench/taught_bench.py and bench/seen_bench.py
measure it there and in process. The orchestrator sends square crops already cut around the thing
(taught.crop_for); they are resized to 224 px whole (no centre crop, which would cut the edges of
the thing off) and normalised as each model was trained. Vectors are scaled to length 1, so a dot
product is the cosine similarity.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

DEFAULT = "dinov2-small"
MODELS = ("dinov2-small", "bioclip-2", "dinov3-small")
MODEL = "facebook/dinov2-small"  # DINOv2's Hugging Face id
DINOV3_MODEL = "facebook/dinov3-vits16-pretrain-lvd1689m"  # DINOv3 ViT-S/16's Hugging Face id (gated)
BIOCLIP = "hf-hub:imageomics/bioclip-2"
SIDE = 224
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)


def pixels(images_bgr: list[np.ndarray], mean: np.ndarray = MEAN, std: np.ndarray = STD) -> np.ndarray:
    """(n, 3, 224, 224) float32, normalised."""
    batch = []
    for image in images_bgr:
        rgb = cv2.cvtColor(cv2.resize(image, (SIDE, SIDE), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        batch.append(((rgb.astype(np.float32) / 255.0 - mean) / std).transpose(2, 0, 1))
    return np.stack(batch)


def decode(jpegs: list[bytes]) -> list[np.ndarray]:
    images = [cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) for j in jpegs]
    if any(i is None for i in images):
        raise ValueError("an unreadable crop")
    return images


def reply(model, jpegs: list[bytes]) -> dict:
    """The worker op: JPEG crops in, unit vectors (lists of floats) out, with the model that made them."""
    t0 = time.perf_counter()
    vectors = model.vectors(decode(jpegs))
    return {"vectors": [[round(float(v), 6) for v in row] for row in vectors], "dim": model.dim, "model": model.name,
            "ms": round((time.perf_counter() - t0) * 1000, 1)}


class Embedder:
    """DINOv2 small."""

    name = "dinov2-small"
    dim = 384
    weights = MODEL

    def __init__(self, device: str = "cpu") -> None:
        import torch
        from transformers import AutoModel

        self.torch = torch
        self.device = torch.device(device)
        from .model_files import model_dir

        path = model_dir(self.name)  # its pinned revision (model_files.py), never fetched here
        self.model = AutoModel.from_pretrained(path, local_files_only=True).to(self.device).eval()

    def vectors(self, images_bgr: list[np.ndarray], tokens: str = "cls") -> np.ndarray:
        """(n, 384) unit vectors. tokens "cls": the class token; "cls+mean": it and the mean of the
        patch tokens, joined (768; measured in the bench, not the default)."""
        if not images_bgr:
            return np.zeros((0, self.dim), np.float32)
        batch = self.torch.from_numpy(pixels(images_bgr)).to(self.device)
        with self.torch.inference_mode():
            out = self.model(pixel_values=batch)
        hidden = out.last_hidden_state
        cls = out.pooler_output
        start = 1 + int(getattr(self.model.config, "num_register_tokens", 0) or 0)  # DINOv3's registers are not patches
        vec = self.torch.cat([cls, hidden[:, start:].mean(1)], 1) if tokens == "cls+mean" else cls
        vec = self.torch.nn.functional.normalize(vec.float(), dim=1)
        return vec.cpu().numpy()

    def embed_jpegs(self, jpegs: list[bytes]) -> dict:
        return reply(self, jpegs)


class BioClip:
    """BioCLIP 2 (open_clip): the image tower for crops; the text tower for names (benches only)."""

    name = "bioclip-2"
    dim = 768

    def __init__(self, device: str = "cpu") -> None:
        import open_clip
        import torch

        self.torch = torch
        self.device = torch.device(device)
        from .model_files import model_dir

        local = f"local-dir:{model_dir(self.name)}"  # its pinned revision (model_files.py), never fetched here
        model, _, _ = open_clip.create_model_and_transforms(local)
        self.model = model.to(self.device).eval()
        self.tokenizer = open_clip.get_tokenizer(local)

    def vectors(self, images_bgr: list[np.ndarray]) -> np.ndarray:
        """(n, 768) unit vectors."""
        if not images_bgr:
            return np.zeros((0, self.dim), np.float32)
        batch = self.torch.from_numpy(pixels(images_bgr, CLIP_MEAN, CLIP_STD)).to(self.device)
        with self.torch.inference_mode():
            vec = self.model.encode_image(batch)
        return self.torch.nn.functional.normalize(vec.float(), dim=1).cpu().numpy()

    def text_vectors(self, texts: list[str]) -> np.ndarray:
        """(n, 768) unit vectors of names or prompts, for zero-shot matching."""
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        with self.torch.inference_mode():
            vec = self.model.encode_text(self.tokenizer(texts).to(self.device))
        return self.torch.nn.functional.normalize(vec.float(), dim=1).cpu().numpy()

    def embed_jpegs(self, jpegs: list[bytes]) -> dict:
        return reply(self, jpegs)


class DinoV3(Embedder):
    """DINOv3 ViT-S/16: the same class token as DINOv2 small, its normalisation (ImageNet's) and size."""

    name = "dinov3-small"
    dim = 384
    weights = DINOV3_MODEL

    def __init__(self, device: str = "cpu") -> None:
        super().__init__(device)  # not downloaded: model_files.NotDownloaded says so (its licence is a step of the download)


def embedder(model: str = DEFAULT, device: str = "cpu"):
    """The embedding model by its option id (EMBED)."""
    if model == "bioclip-2":
        return BioClip(device)
    if model == "dinov2-small":
        return Embedder(device)
    if model == "dinov3-small":
        return DinoV3(device)
    raise ValueError(f"unknown embedding model {model!r}")
