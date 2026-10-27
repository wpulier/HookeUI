"""The three forum scenarios of ticket 59's MT5–MT7, through the shared maintenance lifecycle.

MT7 (QuPath #2211): rendered export used Z=0 instead of the displayed plane when nothing was selected. Here a fixture
renderer integration ships v1 with that defect and v2 with the fix; the exact-view export contract (view_export.py)
catches v1; the catalog's [[fixes]] notice says the defect and, until a build exists, "Fix identified — compatible build
pending"; v2 is then delivered by maintenance under the recorded policy; a recipe pinned to v1 keeps v1 (the defect stays
reproducible in its isolated version); exports v1 made are listed as affected and made again beside the originals.
A scratch upstream repository as the reviewed channel and a stand-in uv, as tests/test_maintenance.py."""
import hashlib
import importlib.util
import json

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, env_runner, view_export
from orchestrator.maintenance import discover, pins, policy, prepare, status
from tests.test_channels import _git, home, publish  # noqa: F401 - `home` is the fixture
from tests.test_env_runner import ADAPTER
from tests.test_maintenance import KEY, turned_on, version  # noqa: F401

T, Z, H, W = 2, 3, 24, 32
RENDER = '''

def render(volume, dispatch):
    """The displayed plane of a (T, Z, Y, X) volume as RGB, cropped to the region when one was chosen."""
    import numpy as np
    order, steps = dispatch["dimensions"]["order"], dispatch["dimensions"]["steps"]
    t, z = steps[order.index("t")], steps[order.index("z")]
    {defect}
    plane = volume[t, z]
    r = dispatch.get("region")
    if r is not None:
        plane = plane[r["y"]:r["y"] + r["height"], r["x"]:r["x"] + r["width"]]
    return np.repeat(plane[:, :, None], 3, axis=2).astype(np.uint8)
'''
V1 = ADAPTER + RENDER.replace("{defect}", 'if dispatch.get("region") is None:\n        z = 0  # the QuPath 0.7.0 defect')
V2 = ADAPTER + RENDER.replace("{defect}", "pass")


@pytest.fixture(autouse=True)
def every_check_looks(monkeypatch):
    monkeypatch.setattr(discover, "NEWEST_S", 0)


def volume() -> np.ndarray:
    """Different pixels at every Z and T (and across each plane), so a wrong plane can't pass for the right one."""
    base = (np.arange(H)[:, None] + np.arange(W)[None, :]) % 7
    return np.array([[base + 60 * t + 20 * z for z in range(Z)] for t in range(T)], np.uint8)


def frame(t: int, z: int, token: str = "f" * 64) -> dict:
    """What the scientific host recorded for the view shown: plane (t, z), axes t, z, y, x, y and x displayed."""
    return {"frame": token, "dimensions": {"order": ["t", "z", "y", "x"], "displayed": [2, 3], "steps": [t, z, 0, 0]},
            "camera": {"zoom": 1.0}, "layers": [{"name": "image", "visible": True}],
            "resource": {"id": "p1", "revision": 3, "digest": "d" * 64}}


def preview(dispatch: dict) -> np.ndarray:
    """The view the person saw: the displayed plane, as the host drew it."""
    steps = dispatch["dimensions"]["steps"]
    return np.repeat(volume()[steps[0], steps[1]][:, :, None], 3, axis=2)


def renderer(generation_folder, key: str):
    """The active generation's own render function (its kept copy of the entry), as the export calls it."""
    path = generation_folder / "entry" / "dark-spots" / "adapter.py"
    spec = importlib.util.spec_from_file_location(f"renderer_{key}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return lambda dispatch: module.render(volume(), dispatch)


def notices(home, text: str) -> None:
    (home / "upstream" / "catalog" / "models" / "maintenance.toml").write_text(text)
    _git("add", ".", cwd=home / "upstream")
    _git("commit", "-q", "-m", "notices", cwd=home / "upstream")


FIX = '''[[fixes]]
entry = "dark-spots"
versions = ["dark-spots 0.1"]
why = "rendered export used Z=0 instead of the displayed plane when nothing was selected"
issue = "https://github.com/qupath/qupath/pull/2211"
fixed_in = "{fixed}"
'''


# --- the contract --------------------------------------------------------------------------------------------------
def test_an_export_takes_its_view_at_dispatch_and_refuses_rather_than_assume_a_plane():
    shown = frame(1, 2)
    with pytest.raises(view_export.Refused, match="the view changed since it was shown") as e:
        view_export.capture(shown, scope="current-view", current=lambda: "e" * 64)
    assert e.value.next == "choose the view again, then export"
    incomplete = {**shown, "dimensions": {**shown["dimensions"], "steps": [1, None, 0, 0]}}
    with pytest.raises(view_export.Refused, match="no plane is assumed"):
        view_export.capture(incomplete, scope="current-view")
    with pytest.raises(view_export.Refused, match="a selected annotation isn't taken as one"):
        view_export.capture(shown, scope="selected-region")
    with pytest.raises(view_export.Refused, match="exported as native values") as e:
        view_export.capture(shown, scope="scientific-data")
    assert e.value.next == "use Export native data + metadata"
    with pytest.raises(view_export.Refused, match="no saved revision"):
        view_export.capture({**shown, "resource": {}}, scope="current-view")
    dispatch = view_export.capture(shown, scope="current-view", current=lambda: "f" * 64, size=(W, H))
    assert dispatch["dimensions"]["steps"] == [1, 2, 0, 0] and dispatch["resource"]["revision"] == 3
    assert dispatch["scopeWords"] == "Current view" and dispatch["region"] is None


def test_the_reproducer_v1_exports_the_wrong_plane_and_the_contract_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    module = {}
    exec(compile(V1, "v1", "exec"), module)
    render = lambda d: module["render"](volume(), d)  # noqa: E731
    for t, z in ((0, 2), (1, 1), (1, 2)):  # any plane but z=0 shows the defect
        dispatch = view_export.capture(frame(t, z), scope="current-view")
        record = view_export.export(dispatch, render, preview(dispatch), tmp_path / f"v1-{t}-{z}.png", entry=KEY,
                                    generation="g1", version="dark-spots 0.1")
        assert record["ok"] is False and "isn't the plane that was shown" in record["problems"][0]
    region = {"x": 4, "y": 3, "width": 10, "height": 8}
    dispatch = view_export.capture(frame(1, 2), scope="selected-region", region=region)
    record = view_export.export(dispatch, render, preview(dispatch), tmp_path / "v1-region.png", entry=KEY,
                                generation="g1", version="dark-spots 0.1")
    assert record["ok"] is True, "with a region chosen v1 took the displayed plane: why the report hid it"


def test_a_view_change_while_queued_never_changes_what_is_exported(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    module = {}
    exec(compile(V2, "v2", "exec"), module)
    dispatch = view_export.capture(frame(1, 2), scope="current-view", current=lambda: "f" * 64)
    live = frame(0, 1, token="a" * 64)  # the person moves on before the export runs
    assert live["dimensions"]["steps"] != dispatch["dimensions"]["steps"]
    record = view_export.export(dispatch, lambda d: module["render"](volume(), d), preview(dispatch),
                                tmp_path / "queued.png", entry=KEY, generation="g2", version="dark-spots 0.2")
    assert record["ok"] is True and record["dimensions"]["steps"] == [1, 2, 0, 0], "the dispatch plane, not the newest"
    kept = json.loads((tmp_path / "queued.export.json").read_text())
    assert kept["renderer"] == {"entry": KEY, "generation": "g2", "version": "dark-spots 0.2"}
    assert kept["sha256"] == hashlib.sha256((tmp_path / "queued.png").read_bytes()).hexdigest()


# --- the fix delivered through maintenance -------------------------------------------------------------------------
def test_the_fix_is_pending_then_delivered_by_maintenance_and_v1_stays_reproducible(home, monkeypatch, tmp_path):
    publish(home, KEY, version("0.1"), V1)
    channels.update()
    from tests.test_env_runner import install

    assert install(home, monkeypatch)["state"] == "done"
    m = catalog.get(KEY)
    v1 = env_runner.selected(m).folder
    pins.add(KEY, v1.name, "recipe", "saved-export-recipe")  # a saved recipe made with v1
    policy.record(m, "auto", by="you", how="screen")

    dispatch = view_export.capture(frame(1, 2), scope="current-view")
    old = view_export.export(dispatch, renderer(v1, "v1"), preview(dispatch), tmp_path / "exports" / "figure.png",
                             entry=KEY, generation=v1.name, version="dark-spots 0.1")
    assert old["ok"] is False, "the defect, reproduced in the affected version"
    original = hashlib.sha256((tmp_path / "exports" / "figure.png").read_bytes()).hexdigest()

    notices(home, FIX.format(fixed=""))  # the fix is known upstream; no distributable build yet
    channels.update()
    now = status.of(catalog.get(KEY))
    assert now["state"] == "fix-pending" and now["says"].startswith("Fix identified — compatible build pending")
    assert now["defect"]["issue"].endswith("/pull/2211") and "known defect in dark-spots 0.1" in now["says"]

    publish(home, KEY, version("0.2"), V2)  # the fixed build, then the notice naming it
    notices(home, FIX.format(fixed="dark-spots 0.2"))
    row = prepare.run(KEY)
    assert row["state"] == "applied" and env_runner.selected(catalog.get(KEY)).m.version == "dark-spots 0.2", row
    assert env_runner.generation(catalog.get(KEY), v1.name).m.version == "dark-spots 0.1", "the recipe keeps v1"

    channels.update()  # the catalog moved on by a person's Check, with the notice
    now = status.of(catalog.get(KEY))
    assert [a["file"] for a in now["affected"]] == [str(tmp_path / "exports" / "figure.png")]
    assert "1 export made by a version with a known defect can be made again" in now["says"]
    v2 = env_runner.selected(catalog.get(KEY)).folder
    again = view_export.reexport(tmp_path / "exports" / "figure.export.json", renderer(v2, "v2"), preview(dispatch),
                                 entry=KEY, generation=v2.name, version="dark-spots 0.2")
    assert again["ok"] is True and again["reexportOf"].endswith("figure.png")
    assert hashlib.sha256((tmp_path / "exports" / "figure.png").read_bytes()).hexdigest() == original, "never rewritten"


def test_new_metadata_fix_supersedes_pinned_pending_notice_without_moving_channel(home):
    import os
    from orchestrator.maintenance import revocations
    publish(home, KEY, version('0.1'), V1)
    notices(home, FIX.format(fixed=''))
    channels.update()
    m = catalog.get(KEY)
    assert revocations.fix(m, 'dark-spots 0.1')['fixedIn'] == ''
    pinned = catalog.snapshot(next(c for c in catalog.channels() if c['name'] == m.channel))
    fresh = catalog.catalog_root()/'news'/m.channel/'fresh'/revocations.FILE
    fresh.parent.mkdir(parents=True)
    fresh.write_text(FIX.format(fixed='dark-spots 0.2'))
    os.utime(fresh, (2000000000, 2000000000))
    assert revocations.fix(m, 'dark-spots 0.1')['fixedIn'] == 'dark-spots 0.2'
    assert [r['fixedIn'] for r in revocations.fixes(m)] == ['dark-spots 0.2']
    assert catalog.snapshot(next(c for c in catalog.channels() if c['name'] == m.channel)) == pinned


def test_later_explicit_channel_snapshot_supersedes_old_news(home):
    import os
    from orchestrator.maintenance import revocations
    publish(home, KEY, version('0.1'), V1)
    notices(home, FIX.format(fixed='dark-spots 0.3'))
    channels.update()
    m = catalog.get(KEY)
    pinned = catalog.snapshot(next(c for c in catalog.channels() if c['name'] == m.channel))/revocations.FILE
    stale = catalog.catalog_root()/'news'/m.channel/'older'/revocations.FILE
    stale.parent.mkdir(parents=True)
    stale.write_text(FIX.format(fixed='dark-spots 0.2'))
    os.utime(stale, (1900000000, 1900000000))
    os.utime(pinned, (1900000001, 1900000001))
    assert revocations.fix(m, 'dark-spots 0.1')['fixedIn'] == 'dark-spots 0.3'
    assert [r['fixedIn'] for r in revocations.fixes(m)] == ['dark-spots 0.3']
