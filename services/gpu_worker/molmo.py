"""Molmo 2 (4B, Ai2; Apache-2.0 code and weights): a vision-language model trained to point. Asked
"Point to the oval on the right", it answers with points (`<points coords="1 1 696 548">`, x and y
on 0-1000 of the image), which the app outlines with the click segmenter (pointing option "molmo",
orchestrator/molmo.py). It runs in its own GPU worker process, started on first use, so a click,
a tracking step or an embedding never waits behind it (orchestrator/gpu_client.py, "molmo").

The model's own code (trust_remote_code, written for transformers 4.57) is loaded as published
and made to run on the transformers this app uses (5.x) by small shims: the processor's extra
settings and the plain RoPE ("default", gone from 5.x) in `_compat()`; the attention mask, set on
the model's module (5.x changed `create_causal_mask`; ours is causal with the image block attending
both ways, as the model was trained); and its rotary frequencies, computed again after loading. Generation is our own greedy loop (5.x `generate` no longer
passes what the model's `prepare_inputs_for_generation` expects); it stops once the points are
written, since the words after them only repeat the request.

The image comes first in Molmo's prompt, so the keys and values of a view's image are kept: a
second request on the same frame only reads its own words (~0.5 s instead of ~2.5 s). `read`
prepares a frame ahead of a request (the slide settled; orchestrator/molmo.py).

Measured on this Mac (M3 Max, 38 GB; docs/bench/finding.md, "Molmo 2"): loading ~20 s plus a ~3 s
warm-up, ~12 GB of GPU memory in float16 (40-200 s to load while the Mac was short of memory and
swapping); grounding bench p50 3.6 s on a new view (seeing the image ~0.75 s, reading the prompt
~1.6 s, ~65 ms a token) and 2.0 s on a view read ahead.
"""
from __future__ import annotations

import os
import re
import sys
import time

import cv2
import numpy as np

MODEL = "allenai/Molmo2-4B"
MAX_CROPS = 4  # tiles of the image the model sees besides the whole (8 is its default; two leaf questions: the same points, ~0.6 s sooner)
MAX_NEW_TOKENS = 160  # the points come first (~5 tokens each): about 30 of them
MAX_POINTS = 60

COORDS = re.compile(r"<(?:points|tracks)[^>]*?coords=\"([0-9\t:;, .]+)")  # no closing quote needed: cut off by the word limit
GROUP = re.compile(r"(?:^|\t|:|,|;)([0-9.]+) ([0-9. ]+)")
POINT = re.compile(r"([0-9]+) ([0-9]{3,4}) ([0-9]{3,4})")


def parse_points(text: str, width: float, height: float, image: int = 1) -> list[list[float]]:
    """The points Molmo wrote for image `image` (1-based), in that image's pixels. Coordinates are
    on 0-1000 (Molmo's format: `coords="1 1 696 548 2 412 300"` is image 1, points 1 and 2); any
    outside the image are dropped, as are repeats. An answer cut off by the word limit keeps the
    points written in full."""
    points: list[list[float]] = []
    for coords in COORDS.finditer(text or ""):
        for group in GROUP.finditer(coords.group(1)):
            try:
                which = int(float(group.group(1)))
            except ValueError:
                continue
            if which != image:
                continue
            for _ix, x, y in POINT.findall(group.group(2)):
                px, py = float(x) / 1000 * width, float(y) / 1000 * height
                if 0 <= px <= width and 0 <= py <= height and [px, py] not in points:
                    points.append([px, py])
    return points[:MAX_POINTS]


def finished(text: str) -> bool:
    """The points are written: the coords attribute has closed (what follows repeats the request)."""
    at = text.find('coords="')
    return at >= 0 and text.find('"', at + len('coords="')) >= 0


_PATCHED = False


def _compat() -> None:
    """Run Molmo 2's published code (transformers 4.57) on transformers 5.x (see the module notes)."""
    global _PATCHED
    if _PATCHED:
        return
    from transformers import modeling_rope_utils, processing_utils

    original = processing_utils.ProcessorMixin.__init__

    def init(self, *args, **kwargs):
        # 5.x refuses a processor's own settings as keyword arguments; Molmo's are plain attributes.
        for key in list(kwargs):
            if key != "chat_template" and key in getattr(type(self), "optional_attributes", ()):
                setattr(self, key, kwargs.pop(key))
        original(self, *args, **kwargs)

    if not getattr(processing_utils.ProcessorMixin.__init__, "_molmo", False):
        init._molmo = True
        processing_utils.ProcessorMixin.__init__ = init
    modeling_rope_utils.ROPE_INIT_FUNCTIONS.setdefault("default", default_rope)
    _PATCHED = True


def default_rope(config, device=None, seq_len=None):
    """Plain rotary frequencies (transformers 4.x's "default"), and no attention scaling."""
    import torch

    dim = int(getattr(config, "head_dim", None) or config.hidden_size // config.num_attention_heads)
    inv = 1.0 / (config.rope_theta ** (torch.arange(0, dim, 2, dtype=torch.int64).to(device=device, dtype=torch.float) / dim))
    return inv, 1.0


def causal_mask(config=None, input_embeds=None, attention_mask=None, cache_position=None, past_key_values=None,
                position_ids=None, or_mask_function=None, **_):
    """The attention mask Molmo's code asks transformers for, as a (batch, 1, query, key) boolean:
    each token sees the ones before it, image tokens also see each other (`or_mask_function`), and
    padding is never seen. A mask already made is returned as it is."""
    import torch

    if attention_mask is not None and attention_mask.dim() == 4:
        return attention_mask
    device = input_embeds.device
    q = input_embeds.shape[1]
    past = int(cache_position[0]) if cache_position is not None else 0
    qi = (cache_position if cache_position is not None else torch.arange(q)).to(device)[:, None]
    ki = torch.arange(past + q, device=device)[None, :]
    allow = ki <= qi
    if or_mask_function is not None:
        allow = allow | or_mask_function(0, 0, qi, ki)
    allow = allow[None, None].expand(input_embeds.shape[0], 1, q, past + q)
    if attention_mask is not None:
        allow = allow & attention_mask[:, None, None, : past + q].bool().to(device)
    return allow


class Molmo:
    """Molmo 2 4B on the GPU, loaded once. `point` answers one request on one picture."""

    def __init__(self, device: str = "mps", dtype: str = "float16") -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # its pinned files only (model_files.py); never fetch
        _compat()
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        self.torch = torch
        self.device = torch.device(device)
        t0 = time.perf_counter()
        from .model_files import model_dir

        path = model_dir("molmo2-4b")  # its pinned revision, its code included (model_files.py), never fetched here
        self.processor = AutoProcessor.from_pretrained(path, trust_remote_code=True, local_files_only=True)
        model = AutoModelForImageTextToText.from_pretrained(path, trust_remote_code=True, local_files_only=True,
                                                            dtype=getattr(torch, dtype))
        sys.modules[type(model).__module__].create_causal_mask = causal_mask
        self.model = model.to(self.device).eval()
        for module in self.model.modules():
            # Its rotary frequencies are a buffer that is not saved with the weights, and 5.x makes
            # the model before loading them: computed again here, as the model's code would.
            if type(module).__name__ == "Molmo2RotaryEmbedding":
                inv, scaling = default_rope(module.config, self.device)
                module.inv_freq, module.original_inv_freq, module.attention_scaling = inv, inv, scaling
        self.eos = {self.processor.tokenizer.eos_token_id, self.model.generation_config.eos_token_id} - {None}
        self.load_ms = round((time.perf_counter() - t0) * 1000)
        self._kept: tuple | None = None  # (key, token ids, cache) of the last picture read

    def memory_gb(self) -> float:
        mps = getattr(self.torch, "mps", None)
        return round(mps.driver_allocated_memory() / 1e9, 2) if mps is not None and self.device.type == "mps" else 0.0

    def _inputs(self, image_rgb: np.ndarray, query: str, max_crops: int):
        from PIL import Image

        self.processor.image_processor.max_crops = max_crops
        messages = [{"role": "user", "content": [dict(type="text", text=query), dict(type="image", image=Image.fromarray(image_rgb))]}]
        inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                    return_tensors="pt", return_dict=True)
        return {k: v.to(self.device) for k, v in inputs.items()}

    def _prefill(self, key, inputs) -> tuple[object, object, int]:
        """The first step: the logits after the prompt, the cache, and how many prompt tokens were
        read afresh. The picture's part is taken from the kept cache when it is the same picture."""
        from transformers import DynamicCache

        ids = inputs["input_ids"]
        kept = self._kept
        if kept is not None and kept[0] == key:
            same = int(min(kept[1].shape[1], ids.shape[1]))
            diff = (kept[1][0, :same] != ids[0, :same]).nonzero()
            common = min(int(diff[0, 0]) if len(diff) else same, ids.shape[1] - 1)  # at least one token read
            image_end = int((inputs["token_type_ids"][0] != 0).nonzero().max()) + 1 if "token_type_ids" in inputs else ids.shape[1]
            if image_end <= common < ids.shape[1]:
                cache = kept[2]
                extra = cache.get_seq_length() - common
                if extra > 0:
                    cache.crop(-extra)  # back to the shared start: the picture and the words in common
                out = self.model(input_ids=ids[:, common:], attention_mask=inputs["attention_mask"],
                                 past_key_values=cache, use_cache=True)
                self._kept = (key, ids, cache)  # what the cache now holds (before the answer's words)
                return out, cache, ids.shape[1] - common
        cache = DynamicCache(config=self.model.config.text_config)
        out = self.model(**inputs, past_key_values=cache, use_cache=True)
        self._kept = (key, ids, cache)
        return out, cache, ids.shape[1]

    def read(self, key, image_rgb: np.ndarray, max_crops: int = MAX_CROPS) -> dict:
        """See a picture ahead of a request (its keys and values are kept for `point`)."""
        t0 = time.perf_counter()
        with self.torch.inference_mode():
            inputs = self._inputs(image_rgb, "Point to", max_crops)
            self._prefill(key, inputs)
        return {"ms": round((time.perf_counter() - t0) * 1000, 1)}

    def point(self, key, image_rgb: np.ndarray, query: str, max_crops: int = MAX_CROPS,
              between=lambda: None) -> dict:
        """Molmo's answer to `query` about the picture, and its points in the picture's pixels.
        `between` runs after reading the prompt and after each word (a worker serving clicks)."""
        torch = self.torch
        t0 = time.perf_counter()
        h, w = image_rgb.shape[:2]
        tokens: list[int] = []
        with torch.inference_mode():
            inputs = self._inputs(image_rgb, query, max_crops)
            prepare_ms = (time.perf_counter() - t0) * 1000
            out, cache, read = self._prefill(key, inputs)
            mask = inputs["attention_mask"]
            nxt = int(out.logits[0, -1].argmax())
            prefill_ms = (time.perf_counter() - t0) * 1000
            between()
            while nxt not in self.eos and len(tokens) < MAX_NEW_TOKENS:
                tokens.append(nxt)
                if finished(self.processor.tokenizer.decode(tokens)):
                    break
                mask = torch.cat([mask, torch.ones((1, 1), dtype=mask.dtype, device=mask.device)], 1)
                out = self.model(input_ids=torch.tensor([[nxt]], device=self.device), attention_mask=mask,
                                 past_key_values=cache, use_cache=True)
                nxt = int(out.logits[0, -1].argmax())
                between()
        text = self.processor.tokenizer.decode(tokens, skip_special_tokens=True)
        return {"points": parse_points(text, w, h), "text": text, "tokens": len(tokens), "read": read,
                "prepareMs": round(prepare_ms, 1), "prefillMs": round(prefill_ms, 1), "ms": round((time.perf_counter() - t0) * 1000, 1)}


def view_of(req: dict) -> tuple[np.ndarray, tuple[int, int]]:
    """The picture a request is about, as RGB: the frame, cut to `crop` [x, y, w, h] (the eyepiece)
    when given, and the offset of the cut in frame pixels."""
    image = cv2.imdecode(np.frombuffer(req["jpeg"], np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("an unreadable picture")
    x, y = 0, 0
    if req.get("crop"):
        x, y, cw, ch = (int(v) for v in req["crop"])
        image = image[y:y + ch, x:x + cw]
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB), (x, y)
