"""The one door, through every actor (ADR 0024): one capability folder in the contract (catalog format 2), with a
parameter, installed and proven, then run by hand from its Actions row (its form made from its parameter), from Try on
its card, by the agent and by a researcher, each with the parameter's value it was given reaching its adapter. No app
code names it. The real app over stand-in workers (tests/capability_rig.py)."""
import asyncio
import json

import pytest

from gpu_worker import manifests as catalog
from orchestrator import kinds, operations
from tests.test_env_runner import home, standin_uv  # noqa: F401 - a scratch catalog with the bench frame
from tests.test_kind_acceptance import install

FOLDER = '''requires = { catalog = 2 }
id = "band-measure"
name = "Band measure"
runs = "this-mac"
version = "1"
licence = ["band-measure"]
what = "Measures the brightness of horizontal bands of the view"
runner = "python-env"
source = { url = "https://example.org/band-measure" }
adapter = "adapter:run"
env = { python = "3.13.5", lock = "env.lock", imports = ["numpy"] }
component = { what = "Band measure, a contributed example", licence = "MIT", url = "https://example.org/band-measure", tier = "open" }
needs_to_run = ["install:band-measure"]
category = "Measure"

[[inputs]]
id = "image"
type = "image.2d.rgb8"

[[inputs]]
id = "bands"
type = "int"
default = 4
min = 1
max = 16
label = "Bands"
tooltip = "How many horizontal bands"

[[outputs]]
id = "table"
type = "table.objects"
'''
ADAPTER = '''
import numpy as np

def run(picture, params):
    bands = np.array_split(picture.mean(axis=2), params["bands"], axis=0)
    return {"columns": [{"name": "Band"}, {"name": "Brightness", "unit": "0-255", "definition": "mean grey of the band"}],
            "rows": [[i + 1, float(b.mean())] for i, b in enumerate(bands)]}
'''


@pytest.fixture
def rig(home, monkeypatch, tmp_path, request):
    from tests.capability_rig import Rig

    standin_uv(home, monkeypatch)
    folder = home / "repo" / "models" / "band-measure"
    folder.mkdir(parents=True)
    (folder / "model.toml").write_text(FOLDER.replace('id = "bands"', 'id = "bands"\nagent = false') if getattr(request, "param", "") == "human-only" else FOLDER)
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(ADAPTER)
    catalog.manifests.cache_clear()
    assert catalog.get("band-measure") is not None, catalog.problems().get("band-measure")
    assert install("band-measure")["state"] == "done"
    made = Rig(tmp_path / "rig")
    made.app.catalog_changed()
    yield made
    for op in list(kinds.registered()):
        operations.OPERATIONS.pop(op, None)
    kinds.registered().clear()


def rows_of(rig) -> int:
    results = rig.hub.resources.results
    entry = next(e for e in reversed(results.entries()) if e.get("tool") == "run_capability")
    return len(results.values(entry["id"])["rows"])


def newest(rig) -> dict:
    return next(e for e in reversed(rig.hub.resources.results.entries()) if e.get("tool") == "run_capability")


def test_one_folder_reaches_every_actor_with_its_parameter(rig):
    from orchestrator.capabilities import contributed
    from orchestrator.commands import handle
    from tests.test_sources import Client

    # The list: one row per actor, its form made from its declared parameter.
    rows = {r["id"]: r for r in contributed(set(rig.brain.registry.tools))}
    agent = rows["catalog:band-measure"]
    assert agent["available"] and agent["family"] == "measuring" and agent["how"]["tool"] == "run_capability"
    assert agent["fields"] == [{"key": "bands", "label": "Bands", "kind": "number", "required": False,
                                "hint": "How many horizontal bands", "choices": [], "said": {}, "numeric": True,
                                "initial": 4, "min": 1, "max": 16, "advanced": False}]
    assert {"catalog:band-measure:you", "catalog:band-measure:researcher"} <= set(rows)
    # What runs it is said, never "the app's own code" (live 2026-10-15).
    assert agent["runsOn"] == "Band measure: its makers' code, in an environment of its own on this Mac"

    # The agent, with a value the user asked for.
    runs = asyncio.run(rig.turn([("run_capability", {"capability": "band-measure", "params": {"bands": 6},
                                                     "by": "user"})], "measure six bands"))
    run = next(r for r in runs if r.tool == "run_capability")
    assert run.ok, run.summary
    assert rows_of(rig) == 6

    # By hand: its Actions row, the form's value.
    client = Client()

    async def by_hand(msg):
        await handle(rig.app, client, msg)
        return client.replies[-1]

    reply = asyncio.run(by_hand({"type": "action", "requestId": "a1", "capability": "catalog:band-measure",
                                 "args": {"bands": 3}}))
    assert reply.get("ok") is not False, reply
    assert rows_of(rig) == 3
    # Run by the user's own hand: their choice (live 2026-10-15, every hand run was recorded "brain-chosen"), titled by
    # the capability, said in plain words with its table (never the agent's wrapped rows), and on the Measurements page.
    entry = newest(rig)
    assert entry["madeBy"][0]["origin"] == "user-chosen" and entry["title"].startswith("Band measure · ")
    assert reply["said"] == "Band measure made a table of 3 rows." and reply["table"] == entry["id"], reply
    assert reply["tableIdentity"] == {"session": rig.app.day.sid, "revision": 0}
    listed = {e.id: e.wire() for e in rig.hub.resources.list("result")}
    assert listed[entry["id"]].get("table") is True
    # ...and a value its parameter doesn't take is refused with why, nothing run.
    reply = asyncio.run(by_hand({"type": "action", "requestId": "a2", "capability": "catalog:band-measure",
                                 "args": {"bands": 99}}))
    assert reply.get("ok") is False and "Bands is at most 16" in json.dumps(reply), reply

    # Try on its card.
    reply = asyncio.run(by_hand({"type": "capability-run", "requestId": "t1", "model": "band-measure",
                                 "session": rig.app.day.sid, "input": "image.2d.rgb8", "params": {"bands": 2}}))
    assert reply.get("ok") is not False, reply
    assert rows_of(rig) == 2
    assert reply["said"] == "Band measure made a table of 2 rows." and newest(rig)["madeBy"][0]["origin"] == "user-chosen"

    # A researcher: told it is on, runs it through the same door, its entry made for it.
    from orchestrator.research.hands import Hands
    from tests.test_research_hands import session

    hands = Hands(rig.hub)
    s = session(sitting=rig.app.day.sid)  # a researcher of the session open
    reply = asyncio.run(hands.run(s, "capability", "band brightness", capability="band-measure", params={"bands": 5}))
    assert reply["ok"], reply
    assert "Band measure" in reply["words"] and rows_of(rig) == 5
    made = newest(rig)  # what made it is kept with it, as the agent's run keeps it
    assert made["madeBy"][0]["implementation"] == "band-measure" and made["title"].startswith("Band measure, for ")
    refused = asyncio.run(hands.run(s, "capability", "x", capability="nothing-here"))
    assert not refused["ok"] and "band-measure" in refused["next"]


def test_a_researcher_is_offered_what_is_on(rig):
    from orchestrator.research.manager import _door
    from research_worker.researcher import Researcher

    door = _door()
    assert "band-measure" in [c["key"] for c in door["capabilities"]] and "cellpose-sam" in door["cellFinders"]
    r = Researcher.__new__(Researcher)
    r.spec = {"snapshot": door}
    r.effort = type("E", (), {"name": "quick"})()
    from research_worker.work import SCHEMAS

    schema = r.run_schema(SCHEMAS["run"])
    props = schema["parameters"]["properties"]
    assert "capability" in props["operation"]["enum"] and "band-measure" in props["capability"]["enum"]
    assert "bands (a whole number, 1-16, default 4)" in schema["description"]


def test_no_app_code_names_a_capability_added_as_a_folder():
    """The door test: nothing of the app's own code knows the folders that came through it."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for path in [*root.glob("orchestrator/**/*.py"), *root.glob("research_worker/*.py"), *root.glob("env_worker/*.py")]:
        text = path.read_text()
        assert "band-measure" not in text and "dark-spots" not in text, path


def test_each_actor_reads_the_catalog_once_per_run_and_never_on_the_event_loop(rig, monkeypatch):
    """Reading what is on here reads every entry's installed generation, receipts and proof from disk (kinds.available):
    the agent's run_capability, the action command (the Actions panel) and a researcher's run each read it once, in a
    thread, and find its manifest in that reading (kinds.manifest_of) rather than scanning again."""
    import threading

    from orchestrator.commands import handle, run_tool
    from orchestrator.research.hands import Hands
    from tests.test_research_hands import session
    from tests.test_sources import Client

    reads = []  # (what, on the loop's thread)
    real_available, real_scan = kinds._available, kinds._scan

    def available(environ=None, *a, **k):
        reads.append(("available", threading.current_thread() is threading.main_thread()))
        return real_available(environ, *a, **k) if environ is not None else real_available(*a, **k)

    def scan(kind, environ=None, *a, **k):
        reads.append(("scan", threading.current_thread() is threading.main_thread()))
        return real_scan(kind, environ, *a, **k) if environ is not None else real_scan(kind, *a, **k)

    monkeypatch.setattr(kinds, "_available", available)
    monkeypatch.setattr(kinds, "_scan", scan)

    def once(run) -> None:
        reads.clear()
        run()
        assert reads == [("available", False)], reads

    once(lambda: asyncio.run(run_tool(rig.app, "run_capability", {"capability": "band-measure", "params": {"bands": 2}})))
    assert rows_of(rig) == 2
    client = Client()
    once(lambda: asyncio.run(handle(rig.app, client, {"type": "action", "requestId": "a1",
                                                      "capability": "catalog:band-measure", "args": {"bands": 3}})))
    assert client.replies[-1].get("ok") is not False and rows_of(rig) == 3
    hands = Hands(rig.hub)
    s = session(sitting=rig.app.day.sid)
    once(lambda: asyncio.run(hands.run(s, "capability", "bands", capability="band-measure", params={"bands": 5})))
    assert rows_of(rig) == 5


@pytest.mark.parametrize("rig", ["human-only"], indirect=True)
def test_user_only_settings_trust_studio_context_not_actor_label(rig):
    from orchestrator.commands import handle
    from orchestrator.research.hands import Hands
    from tests.test_research_hands import session
    from tests.test_sources import Client

    runs = asyncio.run(rig.turn([("run_capability", {"capability": "band-measure", "params": {"bands": 6},
                                                     "by": "user"})], "measure six bands"))
    assert not next(r for r in runs if r.tool == "run_capability").ok
    reply = asyncio.run(Hands(rig.hub).run(session(sitting=rig.app.day.sid), "capability", "brightness",
                                         capability="band-measure", params={"bands": 6}))
    assert not reply["ok"]
    client = Client()
    for command in [
        {"type": "action", "capability": "catalog:band-measure", "args": {"bands": 3}},
        {"type": "capability-run", "model": "band-measure", "input": "image.2d.rgb8",
         "session": rig.app.day.sid, "params": {"bands": 3}},
    ]:
        asyncio.run(handle(rig.app, client, {**command, "requestId": "human-setting"}))
        assert client.replies[-1].get("ok") is not False, client.replies[-1]
        assert rows_of(rig) == 3


def test_try_and_actions_keep_the_originating_studio_for_result_presentation(rig):
    from orchestrator.brain.registry import Tool
    from orchestrator.commands import handle
    from tests.test_sources import Client

    client = Client()
    async def show_here(ctx, args):
        return 'shown in requesting studio' if ctx.client is client else 'no requesting studio'
    rig.brain.registry.add(Tool('run_capability', 'Present the retained result', {}, show_here))
    for msg in [
        {"type": "action", "capability": "catalog:band-measure", "args": {}},
        {"type": "capability-run", "model": "band-measure", "input": "image.2d.rgb8", "session": rig.app.day.sid},
    ]:
        asyncio.run(handle(rig.app, client, {**msg, 'requestId': 'presentation'}))
        assert client.replies[-1].get('summary') == 'shown in requesting studio', client.replies[-1]


def test_studio_table_navigation_is_left_to_the_session_checked_reply(rig, monkeypatch):
    from unittest.mock import AsyncMock
    from orchestrator.commands import handle
    from tests.test_sources import Client

    opened = AsyncMock(return_value={'shown': True})
    monkeypatch.setattr(rig.app.services, 'open_page', opened)
    client = Client()
    asyncio.run(handle(rig.app, client, {'type': 'capability-run', 'requestId': 'table-navigation',
        'model': 'band-measure', 'session': rig.app.day.sid, 'input': 'image.2d.rgb8'}))
    reply = client.replies[-1]
    assert reply['ok'] and reply['tableIdentity']['session'] == rig.app.day.sid
    opened.assert_not_called()
