"""MedSigLIP (google/medsiglip-448): how well each of a few names fits a picture of one thing, with no
examples (zero-shot), for naming a click (orchestrator/zeroshot.py, option ZEROSHOT). A SigLIP model
trained by Google on medical images (pathology, dermatology, X-rays...); its weights are gated behind
the Health AI Developer Foundations terms of use (orchestrator/licences.py, tier "terms").

Runs in the heavy GPU worker (op `zeroshot`); bench/zeroshot_bench.py measures it there. The
orchestrator sends square crops already cut around the thing (taught.crop_for, 448 px); they are
resized to 448 px whole and scaled to [-1, 1] as SigLIP was trained. Each name is put in a sentence
(`template`, "a {} in a blood smear") and embedded once (kept for the next click). The ranking is
the softmax of the model's own image-text logits over the names given: a share among those names,
not a probability that the thing is any of them.

The tokenizer is read from the model's tokenizer.json with the `tokenizers` library (it lowercases,
strips punctuation and ends with </s>, as SigLIP's sentencepiece tokenizer does), so no sentencepiece
package is needed. On the Mac's GPU the model runs in float16 (1.8 GB; the bench's rankings were the
same as in float32).
"""
from __future__ import annotations

import time

import cv2
import numpy as np

MODEL = "google/medsiglip-448"
SIDE = 448
TEXT_LENGTH = 64  # SigLIP's text model is trained on sequences padded to 64 tokens with </s> (id 1)
PAD_ID = 1


def pixels(images_bgr: list[np.ndarray]) -> np.ndarray:
    """(n, 3, 448, 448) float32 in [-1, 1]."""
    batch = []
    for image in images_bgr:
        h, w = image.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(image, (SIDE, SIDE), interpolation=cv2.INTER_AREA if max(h, w) > SIDE else cv2.INTER_CUBIC),
                           cv2.COLOR_BGR2RGB)
        batch.append(((rgb.astype(np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1))
    return np.stack(batch)


def softmax_shares(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max(axis=-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=-1, keepdims=True)


def _load(cls, **kwargs):
    """From its pinned revision on this Mac (model_files.py: no network, no token); never fetched here."""
    from .model_files import model_dir

    return cls.from_pretrained(model_dir("medsiglip"), local_files_only=True, **kwargs)


class ZeroShot:
    def __init__(self, device: str = "cpu") -> None:
        import torch
        from tokenizers import Tokenizer
        from transformers import AutoModel

        self.torch = torch
        self.device = torch.device(device)
        self.dtype = torch.float32 if self.device.type == "cpu" else torch.float16
        self.model = _load(AutoModel, dtype=self.dtype).to(self.device).eval()
        from .model_files import model_file

        self.tokenizer = Tokenizer.from_file(str(model_file("medsiglip", "tokenizer.json")))
        self.tokenizer.enable_padding(pad_id=PAD_ID, pad_token="</s>", length=TEXT_LENGTH)
        self.tokenizer.enable_truncation(TEXT_LENGTH)
        self.scale = float(self.model.logit_scale.exp().item())
        self.texts: dict[str, np.ndarray] = {}  # sentence -> its unit vector, for the next click

    def _unit(self, out) -> np.ndarray:
        out = getattr(out, "pooler_output", out)
        return self.torch.nn.functional.normalize(out.float(), dim=-1).cpu().numpy()

    def text_vectors(self, sentences: list[str]) -> np.ndarray:
        todo = [s for s in dict.fromkeys(sentences) if s not in self.texts]
        if todo:
            ids = self.torch.tensor([e.ids for e in self.tokenizer.encode_batch(todo)], device=self.device)
            with self.torch.inference_mode():
                vectors = self._unit(self.model.get_text_features(input_ids=ids))
            for sentence, vector in zip(todo, vectors):
                self.texts[sentence] = vector
        return np.stack([self.texts[s] for s in sentences])

    def image_vectors(self, images_bgr: list[np.ndarray]) -> np.ndarray:
        batch = self.torch.from_numpy(pixels(images_bgr)).to(self.device, self.dtype)
        with self.torch.inference_mode():
            return self._unit(self.model.get_image_features(pixel_values=batch))

    def rank(self, images_bgr: list[np.ndarray], names: list[str], template: str = "{}") -> list[list[tuple[str, float]]]:
        """Per picture, every name with its share, best first."""
        if not images_bgr or not names:
            return [[] for _ in images_bgr]
        text = self.text_vectors([template.format(n) for n in names])
        shares = softmax_shares(self.scale * (self.image_vectors(images_bgr) @ text.T))
        return [sorted(zip(names, (float(s) for s in row)), key=lambda pair: pair[1], reverse=True) for row in shares]

    def rank_jpegs(self, jpegs: list[bytes], names: list[str], template: str = "{}") -> dict:
        """The worker op: JPEG crops and names in, each crop's ranking out."""
        t0 = time.perf_counter()
        images = [cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) for j in jpegs]
        if any(i is None for i in images):
            raise ValueError("an unreadable crop")
        rankings = self.rank(images, list(names), template)
        return {"rankings": [[[name, round(share, 4)] for name, share in row] for row in rankings], "model": MODEL,
                "ms": round((time.perf_counter() - t0) * 1000, 1)}
