"""The menu: every tool sits in a family, the model is always shown every tool, and the MENU
option only changes the layout (brain/menu.py; docs/design/2026-10-06-capability-menu.md)."""
import asyncio

from orchestrator.brain.menu import FAMILIES, MENUS, render, voice_abilities
from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import catalogue
from orchestrator.options import MENU, OUTLINE_COLORS, Settings
from tests.test_brain import make


def test_every_tool_has_a_family_and_every_layout_shows_every_tool():
    registry = catalogue()
    assert {"find", "outline_cells", "set_style"} <= set(registry.tools)  # the catalogue lists every tool
    for tool in registry.tools.values():
        assert tool.family in FAMILIES, tool.name
    assert set(MENUS) == set(MENU)
    for menu in MENUS:
        text = registry.prompt_section(menu)
        for name in registry.tools:
            assert f'- "{name}"(' in text, (menu, name)


def test_flat_is_the_list_as_before_and_families_group_it():
    registry = catalogue()
    flat = registry.prompt_section("flat")
    assert "Scene:" not in flat and flat.splitlines()[0].startswith('- "select"(')
    grouped = registry.prompt_section("families")
    assert grouped.index("Scene: ") < grouped.index('"find"(') < grouped.index("Researchers: ")
    compact = registry.prompt_section("compact")
    assert len(compact) < len(flat)
    assert "Use it whenever the user asks to point out" in grouped and "Use it whenever" not in compact


def test_brief_says_only_the_longest_tools_briefly_and_keeps_their_arguments():
    from orchestrator.brain.menu import BRIEFS

    registry = catalogue()
    flat, brief = registry.prompt_section("flat"), registry.prompt_section("brief")
    assert len(brief) < len(flat)
    for name, text in BRIEFS.items():
        assert len(text) <= 320 and text in brief and registry.tools[name].description not in brief, name
    # Every other tool, and every argument, is as in the flat menu.
    assert registry.tools["select"].description in brief
    assert 'inside?: object id, "target N", or a label' in brief


def test_the_menu_follows_the_setting():
    brain, hub, _ = make([])
    hub.settings.menu = "families"
    assert "Scene: " in brain.registry.prompt_section()
    hub.settings.menu = "flat"
    assert "Scene: " not in brain.registry.prompt_section()


def test_the_voice_hears_the_families():
    text = voice_abilities(list(catalogue().tools.values()))
    assert text.splitlines()[0].startswith("Scene: ") and "- find and outline things" in text


def test_set_style_changes_the_outlines_and_keeps_it():
    from types import SimpleNamespace

    from orchestrator.brain.toolbox import Services, build

    _, hub, _ = make([])
    published, prefs = [], {}
    day = SimpleNamespace(set_pref=lambda k, v: prefs.__setitem__(k, v))
    registry = build(Services(hub=hub, day=day, publish_settings=lambda: published.append(hub.settings.outline_color)))

    async def go(args):
        run = registry.start("set_style", args, ToolContext("voice", 1, 7, "make the outlines yellow"))
        await run.task
        return run

    run = asyncio.run(go({"color": "yellow"}))
    assert run.ok and hub.settings.outline_color == "yellow" and published == ["yellow"]
    assert prefs == {"outlineColor": "yellow"}
    run = asyncio.run(go({"style": "edge"}))
    assert run.ok and hub.settings.style == "edge" and prefs["style"] == "edge"
    bad = asyncio.run(go({"color": "plaid"}))
    assert bad.ok is False and "pink" in bad.summary  # says what it can do


def test_outline_colour_is_a_setting():
    s = Settings()
    assert s.to_wire()["outlineColor"] == "pink" and s.to_wire()["menu"] == "flat"
    assert s.update({"outlineColor": "cyan", "menu": "compact"}) == [] and s.outline_color == "cyan"
    assert s.update({"outlineColor": "plaid"}) and s.outline_color == "cyan"
    assert set(OUTLINE_COLORS) >= {"pink", "yellow"}


def test_render_of_no_family_goes_last():
    from orchestrator.brain.registry import Tool, schema

    async def nothing(ctx, args):
        return ""

    tools = [Tool("odd", "An odd one.", schema({}), nothing), Tool("zoom", "Zoom.", schema({}), nothing, family="magnifier")]
    assert render(tools, "families").splitlines()[0].startswith("Magnifier: ")
    assert render(tools, "families").splitlines()[-1] == '- "odd"(): An odd one.'


def test_contrast_shows_them_side_by_side_and_says_what_the_model_saw(monkeypatch):
    """Synthetic retained camera bytes; real crop/composite/Magnifier/provider admission."""
    from types import SimpleNamespace
    import cv2
    import numpy as np

    from orchestrator.brain.toolbox import Services, build
    from orchestrator.magnifier import Magnifier
    from orchestrator.ticket import issue, using
    from tests.test_brain import make, obj

    _, hub, _ = make([])
    a, b = obj(hub, "stoma"), obj(hub, "guard cell")
    asked = {}

    async def ask(images, prompt, max_output_tokens=0):
        asked.update(n=len(images), prompt=prompt, images=tuple(images))
        return SimpleNamespace(data={"answer": "The left one is rounder."}, error="")

    pixels = np.full((360, 640, 3), 120, np.uint8)
    pixels[100:121, 100:121] = (50, 180, 80)
    pixels[100:121, 150:171] = (180, 70, 50)
    encoded, jpeg = cv2.imencode('.jpg', pixels)
    assert encoded
    frame = SimpleNamespace(jpeg=jpeg.tobytes())
    info = SimpleNamespace(id=7, width=640, height=360, epoch=0, dx=0., dy=0.,
                           wall_at=100., steady=True, focus=1., fov=None)
    hub.infos, hub.latest_info = {7: info}, info
    hub.source = SimpleNamespace(get=lambda fid: frame if fid == 7 else None, is_fresh=lambda: True)
    # Different actual crops, in the original a/b order.
    b.polygons = [[[150, 100], [170, 100], [150, 120]]]
    magnifier = Magnifier(hub)
    registry = build(Services(hub=hub, agent=SimpleNamespace(vision=lambda o: SimpleNamespace(ask=ask)), magnifier=magnifier))

    async def go():
        request = issue(hub, 'turn')
        try:
            with using(request):
                run = registry.start("contrast", {"a": a.id, "b": b.id}, ToolContext("chat", 1, 7, "how are these two different?"))
                await run.task
                assert request.admission.used == 1 and request.admission.attempts[-1].phase == 'query'
                return run
        finally:
            request.seal()

    run = asyncio.run(go())
    assert run.ok and "The left one is rounder." in run.summary and registry.tools["contrast"].kind == "query"
    shown = magnifier.picture_now()
    composite = magnifier.pictures[shown['url'].rsplit('/', 1)[-1]]
    assert asked["n"] == 1 and '"how are these two different?"' in asked["prompt"]
    assert composite.startswith(b"\xff\xd8") and asked['images'] == (composite,)
    assert 'left the stoma' in asked['prompt'] and 'right the guard cell' in asked['prompt']
