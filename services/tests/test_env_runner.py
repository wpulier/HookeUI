"""Entries that bring their own adapter (runner "python-env", orchestrator/env_runner.py; phase 3's E4): a contributed
entry, its folder alone (manifest, lockfile, adapter.py), becomes a choice with no change to the app's code, once
Install has made its environment and proven its adapter by running it; it runs only in that environment; one whose
proof fails, or that is taken away, is no choice. A stand-in uv (tests/test_envs.fake_uv) in a temporary catalog."""
import asyncio
import dataclasses
import os
import shutil
import sys
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import env_runner, envs, licences, names, operations, options, readiness
from tests.test_envs import fake_uv

ADAPTER = '''
import numpy as np

def segment(picture, params):
    """Dark things on a light field: each connected dark region a label."""
    import cv2
    dark = (picture.mean(axis=2) < 128).astype(np.uint8)
    n, labels = cv2.connectedComponents(dark)
    return labels
'''
WRONG = '''
import numpy as np

def segment(picture, params):
    return np.zeros((3, 3), np.int32)
'''
ENTRY = '''id = "dark-spots"
name = "Dark spots"
runs = "this-mac"
version = "dark-spots 0.1"
licence = ["dark-spots"]
kind = "model"
what = "Outlines every dark spot on a light field"
runner = "python-env"
source = {{ url = "https://example.org/dark-spots" }}
adapter = "adapter:segment"
env = {{ python = "3.13.5", lock = "env.lock", imports = ["numpy"] }}
component = {{ what = "Dark spots, a contributed example", licence = "MIT", url = "https://example.org/dark-spots", tier = "open" }}
credit = {{ authors = "A contributor", link = "https://example.org/dark-spots" }}
needs_to_run = ["install:dark-spots"]
host = "own"
documented_mb = 50
load_limit_s = 30

[[implements]]
operation = "cells"
impl = "dark-spots"
input = "image.2d.rgb8"
output = "labels.instance.2d"
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "repo" / "models"
    shutil.copytree(catalog.ROOT, root)
    monkeypatch.setattr(catalog, "ROOT", root)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.delenv("MICROMIND_ENVS", raising=False)
    catalog.manifests.cache_clear()
    before = (dict(options.GROUPS["cells"]), operations.OPERATIONS["cells"], dict(operations.ADAPTERS))
    from orchestrator.assets import store
    from orchestrator.model_checks import FRAME

    monkeypatch.setenv("MICROMIND_ASSETS", str(tmp_path / "assets"))
    frame = store().bench(FRAME)
    frame.parent.mkdir(parents=True, exist_ok=True)
    frame.write_bytes(cv2.imencode(".png", cv2.cvtColor(_picture(), cv2.COLOR_RGB2BGR))[1].tobytes())
    yield tmp_path
    options.GROUPS["cells"].clear()
    options.GROUPS["cells"].update(before[0])
    operations.OPERATIONS["cells"] = before[1]
    operations.ADAPTERS.clear()
    operations.ADAPTERS.update(before[2])
    names.index.cache_clear()  # restored choices must not leave a contributed test model in later tool schemas
    for one in env_runner._clients.values():
        one.stop()
    env_runner._clients.clear()
    catalog.manifests.cache_clear()


def _picture() -> np.ndarray:
    picture = np.full((96, 96, 3), 220, np.uint8)
    for (x, y, r) in ((25, 25, 9), (70, 30, 7), (50, 70, 11)):
        cv2.circle(picture, (x, y), r, (30, 30, 30), -1)
    return picture


def contribute(home, adapter: str = ADAPTER, version: str = "dark-spots 0.1", extra: str = "") -> catalog.Manifest:
    """What a contributor adds: one folder (manifest, lockfile, adapter.py), and nothing else. Again: a new version."""
    folder = home / "repo" / "models" / "dark-spots"
    folder.mkdir(exist_ok=True)
    (folder / "model.toml").write_text(extra + ENTRY.format().replace('"dark-spots 0.1"', f'"{version}"'))
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(adapter)
    catalog.manifests.cache_clear()
    m = catalog.get("dark-spots")
    assert m is not None or extra, catalog.problems().get("dark-spots")
    return m


def standin_uv(home, monkeypatch):
    """test_envs' stand-in uv, its environment's Python a wrapper of the app's own (a bare link loses its packages)."""
    log = fake_uv(home, monkeypatch)
    script = home / "bin" / "uv"
    text = script.read_text().replace(f'ln -s {sys.executable} "${{@: -1}}/bin/python"',
                                      f'printf \'#!/bin/bash\\nexec {sys.executable} "$@"\\n\' > "${{@: -1}}/bin/python"; '
                                      f'chmod +x "${{@: -1}}/bin/python"')
    script.write_text(text)
    return log


def install(home, monkeypatch) -> dict:
    if not (home / "bin" / "uv").exists():
        standin_uv(home, monkeypatch)
    queue = envs.Installs()
    queue.start("dark-spots")
    deadline = time.monotonic() + 30  # completion wait, not an installation performance assertion
    while time.monotonic() < deadline:
        if queue.wire()[0]["state"] in ("done", "failed"):
            break
        time.sleep(0.02)
    return queue.wire()[0]


def test_a_contributed_folder_becomes_a_choice_once_installed_and_proven(home, monkeypatch):
    m = contribute(home)
    assert readiness.model("dark-spots", {}).steps[0].says == "Install Dark spots"
    assert env_runner.register()["added"] == [], "no choice before it is installed and proven"
    row = install(home, monkeypatch)
    assert row["state"] == "done", row
    p = env_runner.proof(m)
    assert p["ok"] and p["found"] == 3 and p["picture"] == "96x96"
    import hashlib
    from orchestrator.assets import store
    from orchestrator.model_checks import FRAME

    assert p["input"]["evidenceClass"] == "asset-image"
    assert p["input"]["identity"] == f"bench/{FRAME}"
    assert p["input"]["sourceSha256"] == hashlib.sha256(store().bench(FRAME).read_bytes()).hexdigest()
    kept = np.load(env_runner.proof_path(m).parent / p["input"]["file"], allow_pickle=False)
    np.testing.assert_array_equal(kept, _picture())
    assert env_runner.register()["added"] == ["dark-spots"]
    assert options.GROUPS["cells"]["dark-spots"].label == "Dark spots"
    assert licences.component("dark-spots").tier == "open" and "dark-spots" not in licences.COMPONENTS, "its declared licence"
    assert list(readiness.model("dark-spots", {}).steps) == []
    impl = operations.OPERATIONS["cells"].impl("dark-spots")
    _, jpeg = cv2.imencode(".jpg", cv2.cvtColor(_picture(), cv2.COLOR_RGB2BGR))
    out = asyncio.run(operations.ADAPTERS[("cells", "dark-spots")](None, SimpleNamespace(jpeg=jpeg.tobytes()), None,
                                                                     impl, {}))
    assert len(out["polygons"]) == 3 and out["version"] == "dark-spots 0.1"


def test_a_contributed_choice_has_one_readiness_on_every_surface(home, monkeypatch):
    """F-L8-2: an AI-made capability was Ready on the Models page and offered in Tools (its own functional proof passed),
    while its role's setup asked for "its functional check on the public synthetic example", which no check of the app's
    covers: pressed, it said "Needs you: no functional check adapter yet" under Ready. Its own proof is its check, on
    the role's option, the card, its setup and the shelf; checking it again reruns that proof."""
    from orchestrator import capabilities, capability_shelf, catalog_view, model_checks

    contribute(home)
    assert install(home, monkeypatch)["state"] == "done"
    assert env_runner.register()["added"] == ["dark-spots"]
    monkeypatch.setattr(model_checks, "_entries", None)
    assert model_checks.find("cells", "dark-spots") is None, "the app has no check of its own for a contributed choice"
    settings = options.Settings()
    settings.update({"cells": "dark-spots"})
    wire = capabilities.wire(settings, {})
    role = next(r for r in wire["roles"] if r["id"] == "cells")
    option = next(o for o in role["options"] if o["id"] == "dark-spots")
    assert option["readiness"]["status"] == "ready" and option["check"]["state"] == "works"

    row = next(r for r in catalog_view.rows(wire["roles"], {}, {}) if r["id"] == "dark-spots")
    assert row["group"] == "ready" and row["face"]["state"] == "on"
    setup = next(s for s in row["setups"] if s["role"] == "cells")
    assert setup["ready"] and setup["recheck"], "its setup is done, as its card says"
    assert [s["kind"] for s in setup["plan"]["steps"]] == ["install"], "checking it again reruns its own proof"
    assert setup["plan"]["steps"][0]["recheck"] is True
    assert not any("public synthetic example" in s.get("says", "") for s in setup["plan"]["steps"])

    card = capability_shelf.card(role, "Cells", "", "", [], [])
    assert card["colour"] == "green" and card["using"] == option["said"]


def test_an_adapter_that_gives_the_wrong_thing_is_never_a_choice(home, monkeypatch):
    m = contribute(home, WRONG)
    row = install(home, monkeypatch)
    assert row["state"] == "failed" and "its label map is (3, 3), not the picture's (96, 96)" in row["says"]
    assert not env_runner.proof(m)["ok"] and env_runner.register()["added"] == []
    assert env_runner.proof(m)["input"]["evidenceClass"] == "asset-image", "failed checks retain their input too"
    step = readiness.model("dark-spots", {}).steps[0]
    assert step.says == "Install Dark spots again" and "failed its proof" in step.how


def test_taken_away_it_is_no_choice_and_a_setting_that_chose_it_goes_back(home, monkeypatch):
    m = contribute(home)
    install(home, monkeypatch)
    env_runner.register()
    settings = options.Settings()
    settings.update({"cells": "dark-spots"})
    assert settings.cells == "dark-spots"
    envs.remove(m)
    changed = env_runner.register(settings=settings)
    assert changed["removed"] == ["dark-spots"] and changed["reset"] and settings.cells == options.Settings().cells
    assert "dark-spots" not in options.GROUPS["cells"]


def test_its_adapter_never_sees_the_apps_code(home, monkeypatch):
    peek = ADAPTER + '''
def peek(picture, params):
    import importlib.util
    if importlib.util.find_spec("orchestrator") is not None:
        raise RuntimeError("the app's code is importable here")
    return segment(picture, params)
'''
    m = contribute(home, peek)
    standin_uv(home, monkeypatch)
    envs.build(m)
    one = env_runner.Client(env_runner.Installed(dataclasses.replace(m, adapter="adapter:peek"), envs.folder(m), {}, True))
    try:
        assert len(one.run(_picture())["polygons"]) == 3
    finally:
        one.stop()


def run_it(m) -> dict:
    impl = operations.OPERATIONS["cells"].impl(m.id)
    _, jpeg = cv2.imencode(".jpg", cv2.cvtColor(_picture(), cv2.COLOR_RGB2BGR))
    return asyncio.run(operations.ADAPTERS[("cells", m.id)](None, SimpleNamespace(jpeg=jpeg.tobytes()), None, impl, {}))


def test_a_new_version_installs_beside_the_one_in_use(home, monkeypatch):
    """The review's correction 4: an update never replaces what works. v1 keeps running (its own copy of the entry)
    while the catalog has v2; v2 is used only once its Install made it and its proof passed; a v2 that fails its proof
    leaves v1 in use."""
    from orchestrator import catalog_view

    contribute(home)
    install(home, monkeypatch)
    env_runner.register()
    assert run_it(catalog.get("dark-spots"))["version"] == "dark-spots 0.1"
    v2 = contribute(home, WRONG, version="dark-spots 0.2")  # the catalog brings v2, a broken one
    found = env_runner.in_use(v2)
    assert found is not None and not found.current and found.m.version == "dark-spots 0.1"
    assert list(readiness.model("dark-spots", {}).steps) == [], "the one installed runs: nothing to do"
    facts = catalog_view.facts(v2, {}, lambda k: False, [], os.environ)
    assert any(f.get("install") == "dark-spots" and f["says"].startswith("Update available: dark-spots 0.2") for f in facts)
    out = run_it(v2)
    assert len(out["polygons"]) == 3 and out["version"] == "dark-spots 0.1", "v1's own adapter, not the catalog's v2"
    row = install(home, monkeypatch)
    assert row["state"] == "failed" and env_runner.in_use(v2).m.version == "dark-spots 0.1", "a broken v2 never replaces v1"
    v3 = contribute(home, ADAPTER.replace("< 128", "< 129"), version="dark-spots 0.3")
    assert install(home, monkeypatch)["state"] == "done"
    assert env_runner.in_use(v3).m.version == "dark-spots 0.1", "proof alone does not promote an update"
    env_runner.choose(v3, None)
    found = env_runner.in_use(v3)
    assert found.current and found.m.version == "dark-spots 0.3"
    env_runner.register()
    assert run_it(v3)["version"] == "dark-spots 0.3"
    kept = {r["version"]: env_runner._proof_of(d)["ok"] for d, r in envs.generations(v3)}
    assert kept == {"dark-spots 0.1": True, "dark-spots 0.2": False, "dark-spots 0.3": True}, "each kept, none written over"


def test_a_bad_update_goes_back_in_one_press_and_stays_back(home, monkeypatch):
    """C7: an update whose proof passed but that the user rejects goes back to the version before in one press (the
    card's Go back, command model-version); a restart keeps that choice; the worker of the version left is not used again;
    Use the newest undoes it."""
    from orchestrator import catalog_view

    contribute(home)
    install(home, monkeypatch)
    env_runner.register()
    v2 = contribute(home, ADAPTER.replace("< 128", "< 129"), version="dark-spots 0.2")
    assert install(home, monkeypatch)["state"] == "done"
    env_runner.choose(v2, None)
    assert env_runner.in_use(v2).m.version == "dark-spots 0.2"
    kept = env_runner.versions(v2)
    assert [v["version"] for v in kept] == ["dark-spots 0.2", "dark-spots 0.1"] and kept[0]["inUse"]
    assert run_it(v2)["version"] == "dark-spots 0.2"
    newest = env_runner._clients["dark-spots"]
    assert newest._process is not None and newest._process.poll() is None, "the newest version's worker runs"
    older = kept[1]["generation"]
    assert env_runner.choose(v2, older).m.version == "dark-spots 0.1"
    assert run_it(v2)["version"] == "dark-spots 0.1", "the version gone back to runs"
    assert newest._process is None, "the newer one's worker was stopped, never used again"
    catalog.manifests.cache_clear()  # as after a restart: the choice is kept beside its environments
    v2 = catalog.get("dark-spots")
    assert env_runner.in_use(v2).m.version == "dark-spots 0.1" and env_runner.chosen(v2) == older
    row = next(r for r in catalog_view.rows([], {}, {}) if r["id"] == "dark-spots")
    assert [v["chosen"] for v in row["versions"]] == [False, True]
    assert "dark-spots 0.1 stays in use" in env_runner.update_waiting(v2)
    assert env_runner.choose(v2, None).m.version == "dark-spots 0.2" and env_runner.chosen(v2) == envs.folder(v2).name
    with pytest.raises(env_runner.Failed, match="no longer here"):
        env_runner.choose(v2, "not-a-generation")


def test_a_failed_update_is_said_on_its_card_and_an_installed_newest_offers_no_install(home, monkeypatch):
    """C7 Window B: a failed update's reason is a fact on its card (finding 1), with the one in use kept; and once the
    user went back from a newest version that is installed and proven, its card offers no Install for it (finding 4)."""
    from orchestrator import catalog_view

    contribute(home)
    install(home, monkeypatch)
    v2 = contribute(home, WRONG, version="dark-spots 0.2")
    assert install(home, monkeypatch)["state"] == "failed"
    facts = catalog_view.facts(v2, {}, lambda k: False, [], os.environ)
    failed = [f for f in facts if f["says"].startswith("Update dark-spots 0.2 failed its check: ")]
    assert len(failed) == 1 and failed[0]["ok"] is False, facts
    assert "label map" in failed[0]["says"] and failed[0]["says"].endswith("dark-spots 0.1 stays in use")
    assert env_runner.in_use(v2).m.version == "dark-spots 0.1"
    assert any(f.get("install") == "dark-spots" for f in facts), "Install can still try it again"

    v3 = contribute(home, ADAPTER.replace("< 128", "< 129"), version="dark-spots 0.3")
    assert install(home, monkeypatch)["state"] == "done"
    older = next(v["generation"] for v in env_runner.versions(v3) if v["version"] == "dark-spots 0.1")
    env_runner.choose(v3, older)
    facts = catalog_view.facts(v3, {}, lambda k: False, [], os.environ)
    back = next(f for f in facts if "dark-spots 0.1 stays in use" in f["says"])
    assert "install" not in back, "the newest is installed: Use the newest switches to it, no Install"
    assert not any(f["says"].startswith("Update dark-spots") for f in facts)


def test_going_back_retires_the_left_versions_worker_at_once(home, monkeypatch):
    """C7 Window B, finding 3: Go back to A (and Use the newest) re-registers the catalog, and that retires the worker of
    the generation left there and then, through the App's retire, not at the entry's next run."""
    contribute(home)
    install(home, monkeypatch)
    env_runner.register()
    v2 = contribute(home, ADAPTER.replace("< 128", "< 129"), version="dark-spots 0.2")
    assert install(home, monkeypatch)["state"] == "done"
    env_runner.choose(v2, None)
    env_runner.register()
    assert run_it(v2)["version"] == "dark-spots 0.2"
    newest = env_runner._clients["dark-spots"]
    process = newest._process
    assert process is not None and process.poll() is None
    older = env_runner.versions(v2)[1]["generation"]
    env_runner.choose(v2, older)
    retired = []
    env_runner.register(retire=lambda c: (retired.append(c), c.stop()))
    assert retired == [newest] and process.poll() is not None, "B's worker ended at Go back, before any run"
    assert "dark-spots" not in env_runner._clients
    assert run_it(v2)["version"] == "dark-spots 0.1"
    back = env_runner._clients["dark-spots"]
    env_runner.register(retire=retired.append)
    assert retired == [newest], "the generation in use keeps its worker"
    env_runner.choose(v2, None)
    env_runner.register(retire=lambda c: (retired.append(c), c.stop()))
    assert retired == [newest, back], "Use the newest retires the version gone back to"
    env_runner.stop()


def test_an_entry_made_for_another_version_of_the_app_is_listed_with_why(home, monkeypatch):
    from orchestrator import catalog_view

    future = catalog.CATALOG_FORMAT + 1
    contribute(home, extra=f'requires = {{ catalog = {future} }}\nshiny_new_key = "future format"\n')
    assert catalog.get("dark-spots") is None
    row = next(p for p in catalog_view.problems() if p["entry"] == "dark-spots")
    assert row["incompatible"] and "needs a newer HookeUI" in row["problems"][0] and f"format {future}" in row["problems"][0]
    contribute(home, extra='requires = { runner = 1 }\n')
    assert catalog.get("dark-spots") is not None
    monkeypatch.setitem(catalog.RUNNER_CONTRACTS, "python-env", (2, 2))
    catalog.manifests.cache_clear()
    row = next(p for p in catalog_view.problems() if p["entry"] == "dark-spots")
    assert row["incompatible"] and "made for an older python-env runner (version 1)" in row["problems"][0]
    monkeypatch.setitem(catalog.RUNNER_CONTRACTS, "python-env", (1, 1))
    contribute(home, extra='requires = { runner = "1" }\n')
    row = next(p for p in catalog_view.problems() if p["entry"] == "dark-spots")
    assert "incompatible" not in row and "`requires` takes catalog and runner" in row["problems"][0]


@pytest.mark.parametrize("format", ["png", "jpeg-exif"])
def test_ordinary_original_picture_reaches_contributed_adapter_with_its_encoded_pixels(home, monkeypatch, format):
    """Real source → population → operation; only the proved installation and adapter process are stand-ins."""
    import struct

    from orchestrator import population
    from orchestrator.commands import handle
    from tests.test_original_picture_analysis import prepare
    from tests.test_sources import Client

    m = contribute(home)
    installed = env_runner.Installed(m, home / "unused-env", {"ok": True, "found": 1}, True)
    installed.folder.mkdir()  # synthetic installed runtime exists, while the adapter remains a stand-in
    monkeypatch.setattr(env_runner, "in_use", lambda entry, environ=os.environ: installed if entry.key == m.key else None)
    seen = []

    class Adapter:
        def run(self, picture, params=None):
            seen.append(picture.copy())
            return {"polygons": [[[15, 5], [35, 5], [35, 25], [15, 25]]]}

    monkeypatch.setattr(env_runner, "client", lambda entry: Adapter())
    built, _, _, _ = prepare(home, monkeypatch)
    pixels = np.full((64, 96, 3), (220, 201, 183), np.uint8)
    pixels[5:25, 15:35] = (40, 51, 62)
    extension = ".jpg" if format == "jpeg-exif" else ".png"
    encoded = cv2.imencode(extension, pixels)[1].tobytes()
    if format == "jpeg-exif":
        exif = b"Exif\0\0II" + struct.pack("<HIH", 42, 8, 1) + struct.pack("<HHII", 274, 3, 1, 6) + b"\0" * 4
        encoded = encoded[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + encoded[2:]
    image = home / f"original{extension}"
    image.write_bytes(encoded)
    aid = built.hub.library.register("imported:adapter-input", image, title="Original", at=time.time(), origin=None)
    env_runner.register()

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        return await population.segment(built.hub, choice=m.id)

    result = asyncio.run(go())
    expected = cv2.cvtColor(cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGR2RGB)
    assert (result.view["height"], result.view["width"]) == (64, 96)
    assert len(seen) == 1 and seen[0].shape == (64, 96, 3)
    np.testing.assert_array_equal(seen[0], expected)


@pytest.mark.parametrize("asset", ["absent", "empty"])
def test_generated_proof_retains_exact_input_and_says_generated_everywhere(home, monkeypatch, asset):
    import hashlib
    from orchestrator import catalog_view, publish

    monkeypatch.setenv("MICROMIND_ASSETS", str(home / "empty-assets"))
    if asset == "empty":
        from orchestrator.assets import store
        from orchestrator.model_checks import FRAME

        path = store().bench(FRAME)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
    m = contribute(home)
    row = install(home, monkeypatch)
    assert row["state"] == "done", row
    p = env_runner.proof(m)
    assert p["input"]["evidenceClass"] == "generated-fixture"
    assert p["input"]["identity"] == "three-discs-rgb8-v1"
    assert p["input"]["shape"] == [128, 128, 3]
    kept = np.load(env_runner.proof_path(m).parent / p["input"]["file"], allow_pickle=False)
    assert kept.dtype == np.uint8 and kept.shape == (128, 128, 3)
    assert tuple(kept[30, 30]) == (40, 40, 40) and tuple(kept[0, 0]) == (220, 220, 220)
    assert hashlib.sha256(kept.tobytes()).hexdigest() == p["input"]["pixelSha256"]
    env_runner.register()
    facts = catalog_view.facts(m, {}, lambda key: False, [], os.environ)
    for text in (row["says"], options.GROUPS["cells"]["dark-spots"].note,
                 " ".join(f["says"] for f in facts if f["kind"] == "check"), publish.body(m)):
        assert "generated" in text.lower() and "real picture" not in text.lower(), text
    assert "not scientific qualification" in publish.body(m)


def test_copied_entry_keeps_operational_generation_but_policy_changes_do_not(home):
    m = contribute(home)
    copied = home / "snapshot" / m.id
    envs.copy(m, copied)
    kept, problems = catalog.read(copied / catalog.MANIFEST, m.namespace, m.channel)
    assert not problems
    assert envs.generation(kept) == envs.generation(m), "discovery paths are not executable inputs"
    changed = dataclasses.replace(kept, needs_to_run=(*kept.needs_to_run, "licence:gate"))
    assert envs.generation(changed) != envs.generation(m), "runtime rights/requirements still change the generation"


SLOW = ADAPTER + '''

def slow(picture, params):
    """Says where it is (its process and group), then works far past any test's wait."""
    import os, pathlib, time
    pathlib.Path(PIDFILE).write_text(f"{os.getpid()} {os.getpgid(0)}")
    time.sleep(120)
    return segment(picture, params)
'''


def test_the_users_stop_of_a_run_ends_its_adapters_process_group(home, monkeypatch):
    """Issue 40 (capability safety and recovery): the user's Stop of the only request of a contributed adapter's run
    ends its work, which has begun: its process and its process group are gone once the Stop is answered. A waiter
    that only went away would leave begun work running (contract 4: test_operations)."""
    from orchestrator import user_stop
    from orchestrator.capture import Capture
    from orchestrator.operations import Prompt, Runner

    pidfile = home / "adapter.pid"
    m = contribute(home, SLOW.replace("PIDFILE", repr(str(pidfile))))
    assert install(home, monkeypatch)["state"] == "done"
    env_runner.register()
    installed = env_runner.in_use(m)
    slow = env_runner.Installed(dataclasses.replace(installed.m, adapter="adapter:slow"), installed.folder,
                                installed.proof, installed.current)
    monkeypatch.setattr(env_runner, "in_use", lambda entry, environ=os.environ: slow if entry.key == m.key else None)
    for one in env_runner._clients.values():  # its proof's client, if kept, ran the adapter's own segment
        one.stop()
    env_runner._clients.clear()
    _, jpeg = cv2.imencode(".png", cv2.cvtColor(_picture(), cv2.COLOR_RGB2BGR))
    capture = Capture(1, jpeg.tobytes(), SimpleNamespace(id=1, width=96, height=96, epoch=0, dx=0.0, dy=0.0))
    # Its worker class is the analysis worker's (the adapter runs in its own process, never on that worker).
    worker = SimpleNamespace(ready=True, problem=lambda: "")
    hub = SimpleNamespace(gpu=worker, gpu_heavy=worker, settings=options.Settings(), latest_info=None)

    async def go():
        runner = Runner(hub)
        with user_stop.stoppable() as stop:
            task = asyncio.ensure_future(runner.run("cells", capture, Prompt("field"), choice=m.id,
                                                    ticket=SimpleNamespace(id="ra", choices={})))
        deadline = time.monotonic() + 30  # the adapter's start, not a performance assertion
        while not pidfile.exists() and time.monotonic() < deadline and not task.done():
            await asyncio.sleep(0.02)
        assert pidfile.exists(), task.exception() if task.done() else "the adapter never began"
        await asyncio.sleep(0.05)
        pid, group = (int(x) for x in pidfile.read_text().split())
        os.kill(pid, 0)  # it runs
        stop.requested = True
        task.cancel()
        t0 = time.monotonic()
        with pytest.raises(asyncio.CancelledError):
            await task
        took = time.monotonic() - t0
        # Gone when the Stop is answered, not later: the whole owned group, the adapter's process with it.
        with pytest.raises(ProcessLookupError):
            os.killpg(group, 0)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        return took

    assert asyncio.run(go()) < 10


def test_install_signature_changes_on_external_proof_and_choice(tmp_path, monkeypatch):
    from orchestrator import envs, paths
    monkeypatch.setattr(paths, 'envs', lambda environ: tmp_path)
    monkeypatch.setattr(paths, 'tools', lambda environ: tmp_path / 'tools')
    before = envs.signature({})
    folder = tmp_path / 'example' / 'generation'
    folder.mkdir(parents=True)
    receipt = folder / 'receipt.json'
    receipt.write_text('{"proved": true}')
    installed = envs.signature({})
    assert installed != before
    (folder.parent / 'generation.proof.json').write_text('{"passed":true}')
    proven = envs.signature({})
    assert proven != installed
    (folder.parent / 'chosen.json').write_text('{"generation":"generation"}')
    assert envs.signature({}) != installed
    receipt.unlink()
    assert envs.signature({}) != installed


@pytest.mark.parametrize("parameterized_only", [False, True])
def test_default_proof_uses_declared_default_example_from_immutable_snapshot(home, monkeypatch, parameterized_only):
    from pathlib import Path
    adapter = '''import numpy as np
def segment(picture, params):
    if min(picture.shape[:2]) < params["minimum"]:
        return {"unsupported": "needs a picture at least 256 pixels wide"}
    return picture
'''
    folder = home / "repo" / "models" / "dark-spots"
    folder.mkdir(exist_ok=True)
    picture = np.full((256, 256, 3), 220, np.uint8)
    picture[30:80, 40:90] = 30
    cv2.imwrite(str(folder / 'example-large.png'), picture)
    contribute(home, adapter)
    manifest = folder / 'model.toml'
    example = 'tests = [{input = "example-large.png", outcome = "result"' + (
        ', params = {minimum = 64}' if parameterized_only else '') + '}]\n'
    manifest.write_text('requires = { catalog = 2 }\n' + example +
                        manifest.read_text().split('[[implements]]')[0] +
                        '\n[[inputs]]\nid = "image"\ntype = "image.2d.rgb8"\n[[inputs]]\nid = "minimum"\ntype = "int"\ndefault = 256\n[[outputs]]\nid = "result"\ntype = "image.2d.rgb8"\n')
    catalog.manifests.cache_clear()
    m = catalog.get('dark-spots')
    assert m is not None, catalog.problems()
    result = install(home, monkeypatch)
    if parameterized_only:
        assert result['state'] == 'failed', result
        assert not env_runner.in_use(m), 'An override cannot establish default readiness'
        return
    assert result['state'] == 'done', result
    receipt = env_runner.proof(m)
    assert receipt['input']['evidenceClass'] == 'declared-example'
    assert receipt['input']['identity'] == 'example-large.png'
    assert receipt['input']['shape'] == [256, 256, 3]
    assert receipt['parameters'] == {'minimum': 256}
    assert 'declared example' in env_runner.proof_input_says(receipt)
    from orchestrator import publish
    assert publish._proof_files(m, os.environ)[0]['input'] == receipt['input']
    generation = env_runner.in_use(m).folder
    (Path(m.folder) / 'example-large.png').unlink()
    saved, source = env_runner.proof_picture(m, generation, os.environ)
    assert saved.shape == (256, 256, 3) and source['identity'] == 'example-large.png'


def test_rechecking_retains_original_proof_input_and_both_formats_can_be_packaged(home, monkeypatch):
    from orchestrator import publish
    m = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.proof(m)
    root = env_runner.proof_path(m).parent
    original_bytes = (root / original['input']['file']).read_bytes()
    monkeypatch.setattr(env_runner, 'representative', lambda environ: (
        np.full((64, 64, 3), 120, np.uint8), {'evidenceClass': 'generated-fixture', 'identity': 'another-fixture'}))
    newer = env_runner.prove(m)
    assert newer['ok'], newer
    assert newer['input']['file'] != original['input']['file']
    assert (root / original['input']['file']).read_bytes() == original_bytes
    assert publish._proof_files(m, os.environ)[0]['input'] == newer['input']
    # Retained legacy names remain readable: no forced reinstallation merely to package an existing proof.
    old_name = envs.folder(m).name + '.proof-input.npy'
    (root / old_name).write_bytes(original_bytes)
    original['input']['file'] = old_name
    env_runner.proof_path(m).write_text(__import__('json').dumps(original))
    assert publish._proof_files(m, os.environ)[1] == original_bytes


def test_failed_proof_input_write_cleans_only_its_partial_file(home, monkeypatch):
    m = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    env_runner.preserve_active(m)
    parent = envs.root(m)
    original = {p.name: p.read_bytes() for p in parent.glob('*.proof-input.npy')}
    before = set(parent.iterdir())
    def no_space(stream, picture, **kwargs):
        stream.write(b'partial array')
        raise OSError('controlled disk-full failure')
    monkeypatch.setattr(np, 'save', no_space)
    with pytest.raises(OSError, match='controlled disk-full'):
        env_runner.prove(m)
    assert set(parent.iterdir()) == before
    assert {p.name: p.read_bytes() for p in parent.glob('*.proof-input.npy')} == original
    assert env_runner.in_use(m) is not None


def test_a_contributed_cells_entry_and_one_of_yours_registered_at_once_both_stay(home, monkeypatch):
    """A contributed entry that implements Cells (kinds.register) and one of yours (mine.register, in App.sync_mine's
    thread) each change the Cells operation from what it was: neither may build on an old one and lose the other's
    change (review of #413, 2026-10-21). The contributed entry registers, whole, while yours is being built."""
    import threading

    from orchestrator import mine
    from tests.test_mine import a_file, passing

    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(home / "old"))  # this Mac's caches never count here
    contribute(home)
    assert install(home, monkeypatch)["state"] == "done"
    key = mine.add(str(a_file(home)), "My cells")["model"]
    mine.activate(key, home / "frame.jpg", run=passing)
    actual, ran = catalog.short_revision, []

    def short_revision(m):  # read while mine.register builds your implementation of Cells
        if not ran:
            ran.append(threading.Thread(target=env_runner.register))  # the loop's catalog_changed, or a capability run
            ran[0].start()
            ran[0].join()
        return actual(m)

    monkeypatch.setattr(catalog, "short_revision", short_revision)
    mine.register()
    assert ran, "the other writer ran while this one was building"
    ids = {i.id for i in operations.OPERATIONS["cells"].implementations}
    assert {"dark-spots", "mine-my-cells"} <= ids, ids
    assert ("cells", "dark-spots") in operations.ADAPTERS and ("cells", "mine-my-cells") in operations.ADAPTERS
