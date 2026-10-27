"""Synthetic widget boundary: export guidance, without opening Qt or a scientific worker."""
import importlib.metadata
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('candidate', ['scientific-animation', 'beta-scientific-tools/animation-movie'])
def test_animation_export_guidance_names_the_current_inspector_section(candidate, monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'examples' / candidate / 'adapter.py'
    spec = importlib.util.spec_from_file_location('animation_guidance_adapter', path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    text = {}
    callbacks = [lambda: pytest.fail('Upstream filesystem export must remain disconnected')]
    button = SimpleNamespace(
        clicked=SimpleNamespace(disconnect=callbacks.clear, connect=callbacks.append),
        setText=lambda value: text.update(label=value),
        setToolTip=lambda value: text.update(tooltip=value))
    animation = SimpleNamespace(_frames=[], set_movie_frame_index=lambda _: None)
    class Widget:
        def __init__(self, viewer):
            self.saveButton = button
            self.animation = animation
    monkeypatch.setattr(importlib.metadata, 'version', lambda _: '0.0.10')
    monkeypatch.setitem(sys.modules, 'magicgui.widgets', SimpleNamespace(Container=lambda: None))
    monkeypatch.setitem(sys.modules, 'napari_animation._qt', SimpleNamespace(AnimationWidget=Widget))
    monkeypatch.setattr(adapter, 'bind_animation_document', lambda *_: None)
    context = SimpleNamespace(restored_layers=[], viewer=SimpleNamespace(),
                              bind_recipe=lambda **_: None, bind_sequence=lambda **_: None,
                              bind_native_panel=lambda _: None)
    adapter.open_host(context)
    assert 'Export section' in text['label']
    assert 'Save tool and all layers' in text['tooltip']
    assert 'Open the saved document' in text['tooltip']
    assert 'Export saved movie in its Export section' in text['tooltip']
    assert len(callbacks) == 1
    with pytest.raises(ValueError, match='Export section'):
        callbacks[0]()
