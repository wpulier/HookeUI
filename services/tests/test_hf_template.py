"""Reviewed HF adapter's public picture-to-labels seam; synthetic processor output, no model or network."""
import contextlib
import importlib.util
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator.link_recognise import TEMPLATES


def _adapter(tmp_path, classes_json=None):
    """The template's adapter as an entry holds it: copied into a folder of its own, with its classes.json if any."""
    entry = tmp_path / "entry"
    entry.mkdir()
    (entry / "adapter.py").write_bytes((TEMPLATES / "hf-segmentation" / "adapter.py").read_bytes())
    if classes_json is not None:
        (entry / "classes.json").write_text(classes_json)
    spec = importlib.util.spec_from_file_location("hf_template_test", entry / "adapter.py")
    return spec


@pytest.mark.parametrize("names, expected", [
    ({"0": "diatom", "1": "cell"}, [[1, 1, 2], [1, 2, 2]]),
    ({"0": "diatom", "1": "background"}, [[1, 1, 0], [1, 0, 0]]),
])
def test_semantic_class_zero_is_retained_unless_declared_background(tmp_path, monkeypatch, names, expected):
    assert _segment(tmp_path, monkeypatch, names) == expected


@pytest.mark.parametrize("said, expected", [
    ('{"notThings": [1], "source": "its dataset card"}', [[1, 1, 0], [1, 0, 0]]),
    ('{"notThings": [0, 1], "source": "its dataset card"}', [[0, 0, 0], [0, 0, 0]]),
])
def test_numbered_classes_a_person_said_are_not_things_are_left_out(tmp_path, monkeypatch, said, expected):
    """Coralscapes' config names its classes by number only ("13": 13); classes.json says which aren't things."""
    assert _segment(tmp_path, monkeypatch, {"0": 0, "1": 1}, said) == expected


@pytest.mark.parametrize("said, why", [
    ('{"notThings": [13]}', "doesn't have: [13]"),
    ('{"notThings": "13"}', "can't be read"),
    ('{"notThings": [true]}', "can't be read"),
    ("not json", "can't be read"),
    ('{"names": {}}', "can't be read"),
])
def test_a_classes_file_that_cant_be_trusted_stops_the_adapter_with_why(tmp_path, monkeypatch, said, why):
    with pytest.raises(RuntimeError, match=why.replace("[", r"\[").replace("]", r"\]")):
        _segment(tmp_path, monkeypatch, {"0": 0, "1": 1}, said)


def _segment(tmp_path, monkeypatch, names, classes_json=None):
    spec = _adapter(tmp_path, classes_json)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    (tmp_path / "config.json").write_text(json.dumps({"model_type": "segformer", "id2label": names}))
    monkeypatch.setenv("MICROMIND_ENTRY_FILES", str(tmp_path))
    classes = np.array([[0, 0, 1], [0, 1, 1]], np.int32)

    class Processor:
        def __call__(self, **kwargs):
            return {}

        def post_process_semantic_segmentation(self, out, **kwargs):
            return [SimpleNamespace(cpu=lambda: SimpleNamespace(numpy=lambda: classes))]

    class Model:
        def eval(self):
            return self

        def __call__(self, **kwargs):
            return None

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda n: None,
                                                            inference_mode=contextlib.nullcontext))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoImageProcessor=SimpleNamespace(from_pretrained=lambda *a, **k: Processor()),
        AutoModelForSemanticSegmentation=SimpleNamespace(from_pretrained=lambda *a, **k: Model())))
    return adapter.segment(np.zeros((2, 3, 3), np.uint8), {}).tolist()
