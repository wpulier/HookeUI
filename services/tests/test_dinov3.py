"""DINOv3 small as an EMBED option (M3.5): never another model's numbers (no threshold, floor or calibration of
its own until benched), its own weights, registers kept out of the patch mean, its key said (HF_TOKEN), a failed
load not tried again for a while, and the inspection receipt naming its weights."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator import seen as seen_module
from orchestrator.taught import CALIBRATIONS, Match, context_line


def test_dinov3_borrows_no_threshold_floor_or_calibration():
    assert seen_module.threshold("dinov3-small") == float("inf") and seen_module.list_floor("dinov3-small") == float("inf")
    assert seen_module.threshold("dinov2-small") < 1.0  # the benched one keeps its own
    line = context_line(Match("platelets", 0.95, 4, model="dinov3-small"))
    assert "Not yet measured with this picture model (dinov3-small)" in line
    assert CALIBRATIONS["dinov2-small"] not in line


def test_seen_before_says_dinov3_has_no_threshold_by_its_name():
    from orchestrator.brain.toolbox.memory import outlines_alike
    from orchestrator.seen import Kept, Similar

    kept = Kept("s1", "o1", "guard cell", "guard cell", "likely", "t1", [1.0], "", model="dinov3-small")

    class Seen:
        on = staticmethod(lambda: True)
        earlier = staticmethod(lambda: [kept])
        thread_title = staticmethod(lambda t: "Leaf")

        async def alike(self, oid):
            return [], Similar(kept, 0.97)

    obj = SimpleNamespace(id="o2", label="stoma", kind="highlight")
    _, said = asyncio.run(outlines_alike(Seen(), obj))
    assert "DINOv3 small has no measured threshold here yet" in said and "dinov3-small" not in said


def test_the_option_loads_dinov3s_own_weights_and_keeps_registers_out_of_the_patch_mean(monkeypatch):
    torch = pytest.importorskip("torch")
    from gpu_worker import embed

    loaded = []
    monkeypatch.setattr(embed.Embedder, "__init__", lambda self, device="cpu": loaded.append(self.weights))
    assert isinstance(embed.embedder("dinov3-small"), embed.DinoV3) and loaded == [embed.DINOV3_MODEL]
    model = embed.DinoV3.__new__(embed.DinoV3)
    hidden = torch.zeros((1, 1 + 4 + 2, 3))
    hidden[0, 1:5] = 100.0  # four register tokens, far from the patches
    hidden[0, 5:] = torch.tensor([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    model.torch, model.device = torch, torch.device("cpu")
    model.model = lambda pixel_values: SimpleNamespace(last_hidden_state=hidden, pooler_output=torch.tensor([[0.0, 0.0, 1.0]]))
    model.model.config = SimpleNamespace(num_register_tokens=4)
    vec = model.vectors([np.zeros((8, 8, 3), np.uint8)], tokens="cls+mean")
    assert np.allclose(vec[0, 3:5] / vec[0, 3], [1.0, 1.0]) and abs(vec[0, 5]) < 1e-6  # the patches' mean, registers out


def test_dinov3s_key_gets_its_weights_and_a_failed_load_is_not_tried_again_at_once(monkeypatch):
    """Its key is a need to get (its gated weights), not to run: a downloaded copy runs offline without it (make
    catalog-proof, 2026-10-13: the option's own `needs` refused the copy this Mac had downloaded)."""
    from gpu_worker import embed, manifests, worker
    from orchestrator.options import EMBED

    assert not EMBED["dinov3-small"].needs and worker.EMBED_MODELS["dinov3-small"] == ("dinov3-small",)
    m = manifests.get("dinov3-small")
    assert "key:HF_TOKEN" in m.needs_to_get and not [n for n in m.needs_to_run if n.startswith("key:")]
    tries = []

    def gated(name, device="cpu"):
        tries.append(name)
        raise RuntimeError("DINOv3's weights could not be loaded; accept its licence and set HF_TOKEN")

    monkeypatch.setattr(embed, "embedder", gated)
    models = worker.Models.__new__(worker.Models)
    models._embedders, models._embed_failed, models.device = {}, {}, "cpu"
    for _ in range(3):
        with pytest.raises(RuntimeError, match="accept its licence"):
            models.embed_crops({"model": "dinov3-small", "jpegs": []})
    assert tries == ["dinov3-small"]  # one load, then the same reason at once


def test_the_inspection_receipt_names_dinov3s_weights():
    from gpu_worker import embed, worker

    facts = worker.execution_facts(SimpleNamespace(device="cpu"), {"op": "embed", "model": "dinov3-small"})
    assert facts["model"] == embed.DINOV3_MODEL and facts["optionId"] == "dinov3-small"
