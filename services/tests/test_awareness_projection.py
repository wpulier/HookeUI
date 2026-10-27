"""What the agent's awareness reads of the capability catalogue (brain/context._awareness): the same rows the
Capabilities page draws, made by the same resolver on every read, without building the Models page's cards (its
setup, downloads, rows, news, shelf, drafts). The profile of 6b70e352 found 160 complete wires in two voice rows.
The real app over stand-in workers (tests/capability_rig.py)."""
import pytest

from orchestrator import catalog_view
from orchestrator.brain import context
from tests.capability_rig import Rig


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path / "rig")


def test_the_status_is_exactly_the_capability_rows_of_the_full_wire(rig):
    full = rig.app.capabilities_wire(counts=False)
    status = rig.app.capability_status()
    assert set(status) == {"capabilities"}, "only what awareness reads"
    assert status["capabilities"] == full["capabilities"]


def test_it_is_made_fresh_on_every_read(rig, monkeypatch):
    # load_keys applies keys to the environment. setenv first, so the monkeypatch records each variable's original state
    # (absent here: conftest pops provider keys) and puts it back after; delenv alone records nothing for an absent one.
    from orchestrator.keys import APPLIED

    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", APPLIED):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    before = rig.app.capability_status()["capabilities"]
    assert any(r.get("why") == "needs GEMINI_API_KEY" for r in before)
    rig.app.paths.keys_file.write_text("GEMINI_API_KEY=test-value-not-real\nOPENAI_API_KEY=test-value-not-real\n")
    after = rig.app.capability_status()["capabilities"]
    assert after != before, "a key added is seen at the next read, never a remembered answer"
    assert not any(r.get("why") == "needs GEMINI_API_KEY" for r in after)
    assert after == rig.app.capabilities_wire(counts=False)["capabilities"]


def test_awareness_reads_it_without_building_the_models_page(rig, monkeypatch):
    expected = context._awareness(rig.brain)["capabilities"]
    built = []
    real = catalog_view.rows
    monkeypatch.setattr(catalog_view, "rows", lambda *a, **k: built.append(1) or real(*a, **k))
    facts = context._awareness(rig.brain)
    assert facts["capabilities"] == expected and built == [], "the same facts, and no Models page made for them"
    assert rig.brain.services.capabilities()["models"], "the models and capabilities tools still read the whole page"
    assert built, "which is the full wire"
