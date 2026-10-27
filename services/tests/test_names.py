"""One name per model (orchestrator/names.py; the user's 2026-10-11 decision Q11).

Live 2026-10-11 (typed chat): the brain shortened "SAM 3.1 (on this Mac)" to "SAM 3.1 (local)" and its own `use`
refused it; the labels the app itself showed ("EdgeTAM (on this Mac)", every cell finder's) were refused too, and
labels with commas made lists ambiguous. Every name any model is shown must mean exactly one thing.
"""
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import capabilities, names, operations, options
from orchestrator.brain.registry import ToolContext, ToolFailed
from orchestrator.brain.toolbox import Services, build, catalogue
from orchestrator.operations import OPERATIONS
from tests.fakes import Hub

GOLDEN = Path(__file__).parent / "golden"


def labels():
    """Every display name: implementations, options and the models fixed in the code."""
    out = [(f"{op.id}:{i.id}", i.label) for op in OPERATIONS.values() for i in op.implementations]
    out += [(f"option:{g}:{o.id}", o.label) for g, group in options.GROUPS.items() for o in group.values()]
    for role in capabilities.load()["roles"]:
        if role["id"].startswith("fixed:"):
            out.append((role["id"], role["label"]))
            if role.get("fallback"):
                out.append((role["id"] + ":fallback", role["fallback"]["label"]))
    return out


def test_no_display_name_has_a_comma():
    assert [(where, label) for where, label in labels() if "," in label] == []


def test_the_index_builds_and_no_key_means_two_things():
    names.index.cache_clear()
    built = names.index()  # raises ValueError when a key would mean two things in one namespace
    assert {"outline", "cells", "find-like", "looks", "option:outline", "option:vision", "fixed:find-all"} <= set(built)
    space = names.Namespace()
    space.put("SAM 3.1", names.Meaning("sam3.1-fal", "SAM 3.1 (cloud)"), "test")
    with pytest.raises(ValueError, match="would mean both"):
        space.put("sam 3.1", names.Meaning("sam3.1-local", "SAM 3.1 (this Mac)"), "test")


def test_every_name_its_id_and_its_old_labels_mean_itself():
    for op in OPERATIONS.values():
        for impl in op.implementations:
            for said in (impl.id, impl.label, *names.OLD_LABELS.get(impl.id, ())):
                assert names.resolve(op.id, said, choosable_only=False).id == impl.id, (op.id, said)
    for group, group_options in options.GROUPS.items():
        for o in group_options.values():
            for said in (o.id, o.label, *names.OLD_OPTION_LABELS.get((group, o.id), ())):
                assert names.option(group, said).id == o.id, (group, said)


def test_the_live_failure_and_the_family_default():
    local = names.resolve("outline", "SAM 3.1 (local)")
    assert local.id == "sam3.1-local" and not local.note()
    assert names.option("outline", "SAM 3.1 (local)").id == "sam3.1-local"
    family = names.resolve("outline", "SAM 3.1")
    assert family.id == "sam3.1-fal" and family.note() == ("SAM 3.1 named alone is its default, SAM 3.1 (cloud); "
                                                           "the other: SAM 3.1 (this Mac)")
    for said in ("sam 3.1 on this Mac", "SAM 3.1 locally", "sam3.1 (this Mac)", "SAM 3.1 (on this Mac)"):
        assert names.resolve("outline", said).id == "sam3.1-local", said
    for said in ("SAM 3.1 in the cloud", "SAM 3.1 (fal)", "SAM 3.1 (fal cloud)", "sam 3.1 cloud"):
        assert names.resolve("outline", said).id == "sam3.1-fal", said
    # Spoken and short forms; never a part of a name.
    assert names.resolve("outline", "sam two point one").id == "sam2.1-tiny"
    assert names.resolve("cells", "cell pose").id == "cellpose-sam"
    assert names.resolve("cells", "microsam").id == "micro-sam-lm"
    assert names.resolve("cells", "EdgeTAM").id == "edgetam-automask"  # the family's member here, said so
    assert names.resolve("outline", "SAM") is None and names.resolve("outline", "Edge") is None
    assert names.resolve("cells", "SAM 3.1") is None  # no SAM 3.1 segments every cell
    from orchestrator.brain.toolbox.choosing import unknown_name

    refused = unknown_name(OPERATIONS["outline"], "Cellpose-SAM")
    assert "(it is a choice for operation cells)" in str(refused) and refused.next == "pick one of these and try again"


def mentions(text: str):
    """Each model name said in `text`, with the brackets or where-words right after it when there are some."""
    bases = sorted({label for _, label in labels() if re.search(r"[A-Z0-9]", label)} | set(names.FAMILIES), key=len, reverse=True)
    taken = []
    for base in bases:
        for m in re.finditer(r"(?<![\w-])" + re.escape(base) + r"(?![\w-])", text):
            if any(s <= m.start() < e for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            rest = text[m.end():]
            bracket = re.match(r" \(([^()]*)\)", rest)
            place = re.match(r" (on this Mac|in the cloud|locally|on fal)\b", rest)
            yield base, (base + bracket.group(0) if bracket else base + place.group(0) if place else base), \
                (bracket.group(1) if bracket else "")


WHERE_WORDS = {names.key(w) for words in names.WHERE.values() for w in words}


def unresolved(text: str) -> list[str]:
    bad = []
    for base, said, bracket in mentions(text):
        if names.anywhere(said):
            continue
        if not bracket or names.key(bracket) in WHERE_WORDS or not names.anywhere(base):
            bad.append(said)  # a qualified name ("SAM 3.1 (local)") or a bare name that means nothing
    return bad


def test_every_name_in_what_the_models_are_told_means_one_thing():
    """Every golden prompt and wire (what the brain, the fast pass, the voice and the capabilities tool are told), every
    tool's description and arguments: each model name in them, with any place it runs said after it, resolves."""
    texts = {p.name: p.read_text() for p in GOLDEN.iterdir() if p.suffix in (".txt", ".json")}
    for tool in catalogue().tools.values():
        texts[f"tool {tool.name}"] = tool.description + " " + json.dumps(tool.params, ensure_ascii=False)
    problems = {name: unresolved(text) for name, text in texts.items()}
    assert {name: bad for name, bad in problems.items() if bad} == {}
    old_with_commas = [label for olds in (*names.OLD_LABELS.values(), *names.OLD_OPTION_LABELS.values())
                       for label in olds if "," in label]
    assert {name: [label for label in old_with_commas if label in text] for name, text in texts.items()
            if any(label in text for label in old_with_commas)} == {}
    # The scanner works: a place a model does not run is caught, an aside in brackets is not a place.
    assert unresolved("try SAM 3.1 (local), SAM 2.1 (cloud) and EdgeTAM (as before)") == ["SAM 2.1 (cloud)"]
    assert sum(1 for text in texts.values() for _ in mentions(text)) > 200


def test_every_model_line_the_capabilities_tool_says_resolves_to_its_option():
    wire = capabilities.wire(options.Settings(), {k: True for k in capabilities.KEYS.values()})
    for role in wire["roles"]:
        if role.get("fixed"):
            line = capabilities.model_line(role)
            said = line.split(": ", 1)[1].rsplit(", fixed in the code", 1)[0].split(", fixed in the code")[0]
            assert names.resolve(role["id"], said, choosable_only=False) is not None, line
            continue
        for option in role["options"]:
            if not option.get("runs"):
                continue
            line = capabilities.model_line({**role, "current": option["id"]})
            said = line.split(": ", 1)[1].rsplit(", a setting", 1)[0]
            meaning = names.option(role["id"], said)
            assert meaning is not None and meaning.id == option["id"], line


def test_the_models_menu_names_each_model_with_where_it_runs():
    """The studio's Models menu shows each option's `said` (models.ts): the one name, and where it runs beside it."""
    wire = capabilities.wire(options.Settings(), {k: True for k in capabilities.KEYS.values()})
    said = {r["id"]: [o["said"] for o in r["options"]] for r in wire["roles"] if not r.get("fixed")}
    assert said["outline"] == ["EdgeTAM (on this Mac)", "SAM 2.1 tiny (on this Mac)", "SAM 3.1 (this Mac)", "SAM 3.1 (cloud)",
                               "EdgeTAM then SAM 3.1 (cloud)", "EdgeTAM then SAM 3.1 (this Mac)",
                               "micro-sam (light microscopy) on this Mac"]
    assert said["cells"][:3] == ["Cellpose-SAM (on this Mac)", "Cellpose-SAM fine-tuned on your corrections (on this Mac)",
                                 "micro-sam (light microscopy) on this Mac"] and said["cells"][-1] == "Off"
    assert said["vision"][:2] == ["Gemini 3.8 Flash (cloud)", "Gemini 3.8 Flash (high resolution) in the cloud"]


def test_each_tool_takes_the_choosable_ids():
    """`use` takes every implementation of the operations with a choice and the options of the settings a request can
    have; `set_default` every one a default can be set to (Slice 5: every model role); `compare_models` the outline
    and cell finders. The ids are told once with their names (on `use`), never again beside another tool's argument."""
    from orchestrator.options import GROUPS

    registry = build(Services(hub=Hub(), magnifier=SimpleNamespace()))
    props = lambda tool: registry.tools[tool].params["properties"]  # noqa: E731
    every = [i.id for op in OPERATIONS.values() for i in op.implementations if names.choosable(op, i)]
    settings = [o for group, per_request in names.SETTINGS.items() if per_request for o in GROUPS[group]]
    assert props("use")["implementation"]["enum"] == list(dict.fromkeys(every + settings)) == names.choosable_ids()
    assert props("use")["operation"]["enum"] == ["outline", "cells", "find-like", "looks", "vision", "fast",
                                                 "outline_check", "research", "image", "presentation"]
    settable = [i.id for op in names.choice_operations() if op.selected_by for i in op.implementations
                if names.choosable(op, i)] + [o for group in names.SETTINGS for o in GROUPS[group]]
    assert props("set_default")["implementation"]["enum"] == list(dict.fromkeys(settable)) == names.choosable_ids(settable=True)
    assert props("set_default")["operation"]["enum"][-2:] == ["sight", "zeroshot"]  # from now on only
    assert props("compare_models")["implementations"]["items"]["enum"] == [
        i for i in names.choosable_ids() if OPERATIONS["outline"].impl(i) or OPERATIONS["cells"].impl(i)]
    description = registry.tools["use"].params["description"]
    assert all(f"{i} = " in description for i in names.choosable_ids(settable=True))
    from orchestrator.brain.menu import render

    menu = render([registry.tools[n] for n in ("use", "set_default", "compare_models")])
    assert menu.count("edgetam = EdgeTAM") == 1 and 'choices=["edgetam"' not in menu and 'each of=["edgetam"' not in menu


def test_find_takes_a_fixed_list_of_ways_and_refuses_another():
    from tests.test_finder import Agent_

    hub = Hub()
    registry = build(Services(hub=hub, agent=Agent_(hub, [])))
    assert registry.tools["find"].params["properties"]["by"]["enum"] == ["look", "cells", "concept"]
    with pytest.raises(ToolFailed) as refused:
        asyncio.run(registry.tools["find"].handler(ToolContext("chat", 1, 7, "find it"), {"what": "stoma", "by": "example"}))
    assert "by is one of look, cells, concept" in str(refused.value) and refused.value.next == "pick one of these and try again"


def test_a_presentation_model_is_named_by_its_id_or_its_name():
    from orchestrator.brain.toolbox.research import presentation_model

    assert presentation_model("Claude Opus 5.5") == "claude-opus-5-5" and presentation_model("gpt-6.1-sol") == "gpt-6.1-sol"
    assert presentation_model("") is None and presentation_model(None) is None
    with pytest.raises(ToolFailed) as refused:
        presentation_model("Claude Opus")
    assert "claude-opus-5-5 = Claude Opus 5.5" in str(refused.value)
    tool = catalogue().tools["revise_work"]
    assert tool.params["properties"]["model"]["enum"] == ["gpt-6.1-sol", "claude-opus-5-5"]


def test_available_means_its_code_is_installed_here(monkeypatch):
    """Live 2026-10-11: micro-sam and SAM 3.1 on this Mac were "available" whether or not their code was here."""
    monkeypatch.setitem(operations.INSTALLED, "sam3", lambda: "not installed here (sam3: `make sam3-setup`)")
    world = SimpleNamespace(gpu=SimpleNamespace(problem=lambda: "", ready=True), gpu_heavy=None, settings=options.Settings())
    impl = OPERATIONS["outline"].impl("sam3.1-local")
    assert operations.availability(world, impl) == "not installed here (sam3: `make sam3-setup`)"
    composite = OPERATIONS["outline"].impl("edgetam>sam3.1-local")  # its SAM 3.1 step is not here either
    assert operations.availability(world, composite) == "not installed here (sam3: `make sam3-setup`)"
    offered = {o["id"]: o for o in options.catalogue({})["outline"]}
    assert not offered["sam3.1-local"]["available"] and "make sam3-setup" in offered["sam3.1-local"]["why"]
    assert not offered["edgetam>sam3.1-local"]["available"] and offered["edgetam"]["available"]
    settings = options.Settings()
    assert settings.update({"outline": "sam3.1-local"}) == [
        "SAM 3.1 (this Mac) can't be used: not installed here (sam3: `make sam3-setup`)"] and settings.outline == "edgetam"
    monkeypatch.setitem(operations.INSTALLED, "sam3", lambda: "")
    assert operations.availability(world, impl) == ""
    # Weights that download on first use are a note, never "not installed".
    assert "first use downloads" in impl.note


def test_micro_sam_is_not_installed_without_its_own_python(monkeypatch, tmp_path):
    monkeypatch.delenv("MICROMIND_EDITION", raising=False)
    monkeypatch.setenv("MICROMIND_MICROSAM_PYTHON", str(tmp_path / "no-such-python"))
    assert "micro-sam's own Python" in operations.INSTALLED["micro-sam"]()
    offered = {o["id"]: o for o in options.catalogue({})["cells"]}
    assert not offered["micro-sam-lm"]["available"] and "its Install button on the Models page" in offered["micro-sam-lm"]["why"]
    python = tmp_path / "python"
    python.write_text("")
    monkeypatch.setenv("MICROMIND_MICROSAM_PYTHON", str(python))
    assert operations.INSTALLED["micro-sam"]() == ""


def test_a_population_says_which_model_made_it():
    from orchestrator.evidence import Population

    made = Population.__new__(Population)
    made.operation, made.implementation, made.made_by, made.producer = "cells", "cellpose-sam", {}, {}
    made.view, made.made_at = {"at": 0.0}, 0.0
    assert re.fullmatch(r"Cell outlines · Cellpose-SAM · \d\d:\d\d", made.title)
    made.implementation = "micro-sam-lm"
    assert names.resolve("cells", made.title.split(" · ")[1]).id == "micro-sam-lm"
