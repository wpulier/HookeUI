"""The licence router (orchestrator/licences.py, ADR 0005): the commercial edition never uses a
non-commercial component, the research edition may, and the core never imports gated code."""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gpu_worker.worker import OP_COMPONENTS, licence_refusal
from orchestrator import arena, licences, tools
from orchestrator.options import CELLS, GROUPS, Settings, catalogue, resolve

SERVICES = Path(__file__).resolve().parents[1]


@pytest.fixture
def research(monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "research")


@pytest.fixture
def commercial(monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")


@pytest.mark.parametrize("value", [None, ""])
def test_the_default_edition_is_research(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MICROMIND_EDITION", raising=False)
    else:
        monkeypatch.setenv("MICROMIND_EDITION", value)
    assert licences.edition() == "research"


@pytest.mark.parametrize("value", ["anything else", "reasearch", "   "])
def test_an_invalid_nonempty_edition_still_refuses_research_components(monkeypatch, value):
    monkeypatch.setenv("MICROMIND_EDITION", value)
    assert licences.edition() == "commercial"  # fail closed
    assert licence_refusal({"op": "cells", "model": "cellpose-sam"})["licence"]


def test_the_default_catalogue_offers_research_models_but_not_unmade_weights(monkeypatch, tmp_path):
    monkeypatch.delenv("MICROMIND_EDITION", raising=False)
    monkeypatch.setenv("MICROMIND_MODELS_DIR", str(tmp_path / "models"))
    options = catalogue({})
    cells = {o["id"]: o for o in options["cells"]}
    outline = {o["id"]: o for o in options["outline"]}
    assert cells["cellpose-sam"]["available"] and cells["micro-sam-lm"]["available"]
    assert outline["micro-sam-lm"]["available"]
    assert not cells["cellpose-sam-tuned"]["available"]
    assert cells["cellpose-sam-tuned"]["why"].startswith("no fine-tuned weights")
    assert "no fine-tuned weights" in Settings().update({"cells": "cellpose-sam-tuned"})[0]


@pytest.mark.parametrize("gpu_request", [
    {"op": "cells"},
    {"op": "cells", "model": "cellpose-sam"},
    {"op": "cells", "model": "micro-sam-lm"},
    {"op": "segment", "model": "micro-sam-lm"},
])
def test_the_default_gpu_worker_permits_research_cell_models(monkeypatch, gpu_request):
    monkeypatch.delenv("MICROMIND_EDITION", raising=False)
    assert licence_refusal(gpu_request) is None


def test_every_component_is_used_and_every_use_is_a_component():
    used = {c for options in GROUPS.values() for o in options.values() for c in o.uses}
    used |= {c for op in OP_COMPONENTS.values() for c in op({})}
    from orchestrator.operations import OPERATIONS  # an operation's implementations use components (Trackastra: link)

    used |= {c for op in OPERATIONS.values() for impl in op.implementations for c in impl.uses}
    from orchestrator import capabilities  # models fixed in the code use components too

    used |= {c for r in capabilities.load()["roles"] for e in (r, r.get("fallback") or {}) for c in e.get("uses", [])}
    from orchestrator import mine  # the user's own Cellpose models, offered once they are turned on

    used.add(mine.COMPONENT)
    from env_worker import scientific_calibration
    used.add(scientific_calibration.COMPONENT)
    from gpu_worker import manifests as catalog
    from orchestrator import jobs  # a job (brainreg) and the data it reads (an atlas) are no option: their entries' own

    used |= {c for m in catalog.manifests().values() if jobs.is_job(m) or m.kind == "data" for c in m.licence}
    import json
    dependencies = json.loads((SERVICES.parent / "apps/studio/package.json").read_text())["dependencies"]
    for component, packages in licences.STUDIO_COMPONENTS.items():
        assert set(packages) <= set(dependencies), component
    used |= set(licences.STUDIO_COMPONENTS)
    assert used <= set(licences.COMPONENTS)
    assert set(licences.COMPONENTS) <= used, "a component nothing uses: remove it or use it"
    for component in licences.COMPONENTS.values():
        assert component.tier in licences.TIERS
        assert component.url.startswith("https://")


def test_an_option_takes_the_strictest_tier_of_what_it_uses():
    assert GROUPS["outline"]["edgetam"].tier == "open"
    assert GROUPS["outline"]["edgetam>sam3.1-local"].tier == "terms"
    assert CELLS["cellpose-sam"].tier == "noncommercial"
    assert GROUPS["pointing"]["box"].tier == "open"  # our own code


def test_commercial_defaults_are_all_permitted(commercial):
    settings = Settings()
    for group in GROUPS:
        option = GROUPS[group][getattr(settings, group)]
        assert licences.permitted(option.uses, "commercial"), f"{group} defaults to {option.id}"
    # Contract 8: the commercial edition's cell finder is EdgeTAM's automatic masks (open weights).
    assert settings.cells == "edgetam-automask"


def test_research_defaults_offer_cellpose(research):
    assert Settings().cells == "cellpose-sam"


def test_update_refuses_a_noncommercial_option_in_the_commercial_edition(commercial):
    settings = Settings()
    problems = settings.update({"cells": "cellpose-sam"})
    assert settings.cells == "edgetam-automask"
    assert problems and "non-commercial component policy excludes Cellpose" in problems[0]


def test_update_accepts_it_in_the_research_edition(research):
    settings = Settings()
    assert settings.update({"cells": "off"}) == []
    assert settings.update({"cells": "cellpose-sam"}) == []
    assert settings.cells == "cellpose-sam"


def test_resolve_falls_back_to_a_permitted_option_and_says_why(commercial):
    assert resolve("cells", "cellpose-sam") == ("edgetam-automask", licences.why_not(("cellpose-sam",)))
    assert resolve("outline", "edgetam") == ("edgetam", "")
    used, why = resolve("outline", "no-such-model", fallback="sam2.1-tiny")
    assert used == "sam2.1-tiny" and "unknown" in why


def test_catalogue_says_why_an_option_is_unavailable(commercial):
    cells = {o["id"]: o for o in catalogue({})["cells"]}
    assert cells["cellpose-sam"]["available"] is False
    assert "non-commercial" in cells["cellpose-sam"]["why"]
    assert cells["cellpose-sam"]["tier"] == "noncommercial"
    vision = {o["id"]: o for o in catalogue({})["vision"]}
    assert vision["gemini-3.8-flash"]["why"] == "needs GEMINI_API_KEY"
    assert catalogue({"GEMINI_API_KEY": True})["vision"][0]["available"] is True


def test_the_arena_offers_only_permitted_arms(commercial, monkeypatch):
    for arm in arena.ask_arm_ids():
        vision, pointing = arm.split("/")
        assert GROUPS["vision"][vision].permitted and GROUPS["pointing"][pointing].permitted
    # A non-commercial outline option is never a choice in the commercial edition.
    blocked = SimpleNamespace(label="Blocked", permitted=False)
    monkeypatch.setitem(GROUPS, "outline", {**GROUPS["outline"], "blocked": blocked})  # swapped, as change_choices does
    choices = arena.Arena.__new__(arena.Arena)
    choices.enabled, choices.arms, choices.current = False, {}, None
    assert "blocked" not in [c["id"] for c in choices.state()["choices"]["outline"]]


def test_the_gpu_worker_refuses_what_this_edition_may_not_run(commercial, monkeypatch):
    refusal = licence_refusal({"op": "cells"})
    assert refusal and refusal["licence"] is True and not refusal["ok"]
    assert licence_refusal({"op": "segment", "model": "edgetam"}) is None
    assert licence_refusal({"op": "segment", "model": "sam3.1-local"}) is None  # terms
    monkeypatch.setenv("MICROMIND_EDITION", "research")
    assert licence_refusal({"op": "cells"}) is None


def test_the_adapter_is_refused_in_the_commercial_edition(commercial):
    with pytest.raises(licences.NotInEdition):
        licences.adapter("cellpose-sam")


def test_outline_cells_refuses_honestly_in_the_commercial_edition(commercial):
    """The commercial edition segments cells with EdgeTAM's automatic masks; asking for Cellpose-SAM by
    name is refused before anything runs, saying why and what can (ADR 0011)."""
    from orchestrator.operations import Prompt, Runner, Unavailable

    hub = SimpleNamespace(settings=Settings(), gpu=None, gpu_heavy=None, latest_info=None)
    with pytest.raises(Unavailable) as error:
        Runner(hub).choose("cells", Prompt("field"), choice="cellpose-sam")
    assert "non-commercial" in str(error.value)
    with pytest.raises(tools.ToolError) as nothing:
        asyncio.run(tools.outline_cells(hub))  # no picture and no GPU here: said, not run
    assert "no fresh frame" in str(nothing.value) or "GPU" in str(nothing.value)


def test_no_core_module_imports_gated_code():
    # Only licences.adapter() reaches services/noncommercial/ (or a commercial arm), so the
    # core can be published without them.
    pattern = re.compile(r"^\s*(from|import)\s+(noncommercial|commercial)\b", re.M)
    offenders = []
    for root in ("orchestrator", "gpu_worker", "research_worker", "sim", "bench"):
        for path in (SERVICES / root).rglob("*.py"):
            for match in pattern.finditer(path.read_text()):
                n = path.read_text().count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(SERVICES)}:{n}")
    assert not offenders, "\n".join(offenders)


def test_noncommercial_packages_install_only_with_their_own_group():
    text = (SERVICES / "pyproject.toml").read_text()
    groups = dict(re.findall(r'^(\w+) = \[(.*?)\]', text, re.M | re.S))
    assert "cellpose" in groups["noncommercial"]
    assert all("cellpose" not in body for name, body in groups.items() if name != "noncommercial")
    assert "cellpose" not in text.split("[dependency-groups]")[0]


def test_the_licences_page_is_up_to_date():
    from bench.licences_report import OUT, render

    assert OUT.read_text() == render(), "docs/licences.md is out of date: run make licences"
