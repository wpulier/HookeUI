"""The scope bridge (services/scope_bridge): the user's limits, typed actions, the yes before the light and large
acquisitions, receipts, its bounded autofocus, its HTTP API and a fuzz of random commands with zero breaches. Run
against the harness's virtual OpenFlexure microscope (sim/virtual_scope.py) here; against Micro-Manager's demo
configuration through pymmcore-plus when the bridge's own environment is set up (`make scope-setup`)."""
from __future__ import annotations

import json
import math
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from scope_bridge import actions as A
from scope_bridge.backends import Recorder
from scope_bridge.backends.openflexure import OpenFlexure
from scope_bridge.bridge import ScopeBridge
from scope_bridge.fuzz import Witness, fuzz
from scope_bridge.limits import Limits
from scope_bridge.server import serve
from sim.virtual_scope import VirtualScope

SERVICES = Path(__file__).resolve().parents[1]
SCOPE_PYTHON = SERVICES / "scope_bridge" / ".venv" / "bin" / "python"


def slide(seed: int = 3, size: int = 1600) -> np.ndarray:
    rng = np.random.default_rng(seed)
    import cv2

    base = cv2.GaussianBlur(rng.uniform(0, 255, (size, size)).astype(np.float32), (0, 0), 1.2)  # fine: focus shows
    base = cv2.normalize(base, None, 30, 230, cv2.NORM_MINMAX).astype(np.uint8)
    return cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)


@pytest.fixture
def scope():
    s = VirtualScope(slide(), size=(320, 240), disc=(160.0, 120.0, 110.0), zoom=1.0, z=0.6, fps=15, noise=0.5,
                     motor_speed=1e6, motor_z_speed=1e4)
    s.serve()
    yield s
    s.close()


@pytest.fixture
def bridge(scope, tmp_path):
    backend = OpenFlexure(scope.url, um_per_step=1.0, um_per_z_step=1 / 100)  # the virtual scope's z: 100 steps a unit
    b = ScopeBridge(backend, tmp_path)
    x, y, z = backend.position()
    b.limits = Limits(x=(x - 300, x + 300), y=(y - 300, y + 300), z=(z - 5, z + 5), step_xy=200, step_z=2,
                      frames=6, seconds=30)
    return b


# --- the limits and the actions --------------------------------------------------------------------------------------
def test_limits_are_read_strictly_and_a_bad_file_is_never_looser():
    lim = Limits.from_wire({"x": [0, 10], "y": [-5, 5], "z": None, "step_xy": 3, "frames": 4})
    assert lim.x == (0.0, 10.0) and lim.z is None and lim.step_xy == 3 and lim.frames == 4
    for bad in ({"x": [10, 0]}, {"x": [0, float("nan")]}, {"step_xy": 0}, {"frames": 2.5}, {"exposure_ms": [0, 5]},
                {"x": "everywhere"}, [], {"y": [True, 3]}):
        with pytest.raises(ValueError):
            Limits.from_wire(bad)
    first = Limits.around(100.0, 200.0, 5.0)
    assert first.x == (-1900.0, 2100.0) and first.z == (-95.0, 105.0) and first.notes


@pytest.mark.parametrize("bad", [
    None, "move", [], {"kind": "teleport"}, {"kind": "move"}, {"kind": "move", "x": "3"}, {"kind": "move", "x": float("nan")},
    {"kind": "move", "x": 1, "relative": "yes"}, {"kind": "move", "x": 1, "speed": 9}, {"kind": "exposure", "ms": -1},
    {"kind": "exposure", "ms": True}, {"kind": "light", "on": 1}, {"kind": "channel", "name": ""},
    {"kind": "autofocus", "range": 5, "step": 10}, {"kind": "autofocus", "range": 900, "step": 1},
    {"kind": "acquire", "plan": "everything"}, {"kind": "acquire", "plan": {"time_plan": {"loops": 0}}},
    {"kind": "acquire", "plan": {"z_plan": {"range": 10, "step": 0}}}, {"kind": "acquire", "plan": {"grid": 3}},
    {"kind": "acquire", "plan": {"axis_order": "tpc"}}, {"kind": "snap", "x": 1},
])
def test_a_malformed_action_is_refused_with_what_was_wrong(bad):
    with pytest.raises(ValueError):
        A.parse(bad)


def test_a_plan_says_how_many_pictures_and_how_long():
    plan = A.parse({"kind": "acquire", "plan": {"channels": ["DAPI", {"config": "FITC", "exposure": 50}],
                                                "time_plan": {"interval": 10, "loops": 3}, "z_plan": {"range": 4, "step": 2},
                                                "stage_positions": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]}}).plan
    assert plan.frames == 2 * 3 * 2 * 3 and plan.z_offsets == (-2.0, 0.0, 2.0)
    assert plan.seconds(10.0) == pytest.approx(20.0)


# --- the bridge on the virtual microscope ----------------------------------------------------------------------------
def test_a_move_inside_the_limits_is_done_verified_and_receipted(bridge, scope):
    before = bridge.state()
    r = bridge.do({"kind": "move", "x": 120.0, "y": -40.0, "relative": True})
    assert r["status"] == "done" and r["verified"] is True
    assert r["after"]["x"] == pytest.approx(before["x"] + 120, abs=1) and r["before"]["x"] == before["x"]
    assert scope.position()[0] == pytest.approx(before["x"] + 120, abs=1)  # the microscope itself moved
    kept = [json.loads(line) for line in (bridge.folder / "receipts.jsonl").read_text().splitlines()]
    assert kept[-1]["id"] == r["id"] and kept[-1]["status"] == "done"


@pytest.mark.parametrize("action, why", [
    ({"kind": "move", "x": 1000.0, "relative": True}, "outside your limits"),
    ({"kind": "move", "x": 250.0, "relative": True}, "in one go; your limit is 200"),
    ({"kind": "move", "z": 3.0, "relative": True}, "focus 3.0 µm in one go"),
    ({"kind": "move", "z": -1e9}, "outside your limits"),
    ({"kind": "exposure", "ms": 5000}, None),  # this microscope sets no exposure: refused as unavailable
    ({"kind": "light", "on": False}, "light is not switched"),
    ({"kind": "channel", "name": "FITC"}, "no channel called"),
    ({"kind": "acquire", "plan": {"stage_positions": [{"x": 1e6, "y": 0}]}}, "outside your limits"),
])
def test_what_the_limits_forbid_is_refused_and_nothing_moves(bridge, scope, action, why):
    before = scope.position()
    r = bridge.do(action)
    assert r["status"] == "refused", r
    if why:
        assert why in r["why"]
    assert scope.position() == pytest.approx(before)


def test_the_light_and_a_large_acquisition_wait_for_the_users_yes(bridge, scope):
    big = {"kind": "acquire", "plan": {"time_plan": {"interval": 0, "loops": 4}, "z_plan": {"range": 4, "step": 2}}}
    asked = bridge.do(big)
    assert asked["status"] == "needs-yes" and "12 pictures" in asked["ask"] and asked["ask"].endswith("Go ahead?")
    assert not list((bridge.folder / "pictures").glob("**/*.jpg")) if (bridge.folder / "pictures").exists() else True
    assert bridge.do(big, "y999")["status"] == "needs-yes"  # a forged approval is no yes
    other = {"kind": "acquire", "plan": {"time_plan": {"interval": 0, "loops": 5}, "z_plan": {"range": 4, "step": 2}}}
    assert bridge.do(other, asked["offer"])["status"] == "needs-yes"  # nor one given for another action
    asked = bridge.do(big)
    done = bridge.do(big, asked["offer"])
    assert done["status"] == "done" and len(done["made"]) == 12 and done["verified"] is True
    assert all((bridge.folder / m).is_file() for m in done["made"])
    assert bridge.do(big, asked["offer"])["status"] == "needs-yes"  # an offer is used once


def test_autofocus_finds_the_sharpest_plane_inside_the_limits(bridge, scope):
    scope.hand(dz=0.0)
    r = bridge.do({"kind": "autofocus", "range": 2.0, "step": 0.25})
    assert r["status"] == "done", r
    # the virtual scope is sharp at z 0 and started 0.6 off; neighbouring planes near focus can tie on sensor noise
    assert abs(scope.truth()["z"]) <= 0.5 + 1e-6
    lo, hi = bridge.limits.z
    bridge.limits.z = (bridge.state()["z"] - 0.1, bridge.state()["z"] + 0.1)  # a narrow box: it never leaves it
    r = bridge.do({"kind": "autofocus", "range": 40.0, "step": 1.0})
    assert lo <= scope.position()[2] <= hi and bridge.limits.z[0] - 0.3 <= scope.position()[2] <= bridge.limits.z[1] + 0.3


def test_stop_interrupts_an_acquisition_from_another_thread(bridge, scope):
    import threading

    long = {"kind": "acquire", "plan": {"time_plan": {"interval": 0.5, "loops": 5}}}
    threading.Timer(0.6, bridge.stop).start()
    started = time.monotonic()
    r = bridge.do(long)
    assert r["status"] == "stopped" and time.monotonic() - started < 2.0
    assert "of 5 pictures" in r["why"]


def test_limits_change_only_on_the_users_own_words_and_loosening_them_asks_first(bridge):
    with pytest.raises(ValueError):
        bridge.set_limits({"step_xy": 100}, "")
    with pytest.raises(ValueError):
        bridge.set_limits({"step_xy": -1}, "make it negative")
    tighter = bridge.set_limits({"step_xy": 100}, "only 100 microns at a time")
    assert tighter["status"] == "done" and tighter["limits"]["step_xy"] == 100  # tightening: at once
    asked = bridge.set_limits({"step_xy": 400, "frames": 10_000}, "let it move 400 microns at a time")
    assert asked["status"] == "needs-yes" and "400 µm across" in asked["ask"] and "10000 pictures" in asked["ask"]
    assert bridge.limits.step_xy == 100  # nothing changed yet
    assert bridge.set_limits({"step_xy": 400, "frames": 10_000}, "yes", "y-forged")["status"] == "needs-yes"
    done = bridge.set_limits({"step_xy": 400, "frames": 10_000}, "yes", asked["offer"])
    assert done["status"] == "done" and done["limits"]["step_xy"] == 400
    assert done["limits"]["notes"][-1] == "yes"
    assert json.loads((bridge.folder / "limits.json").read_text())["step_xy"] == 400


def test_a_stop_that_comes_while_an_action_waits_its_turn_stops_it(bridge, scope):
    import threading

    started = threading.Event()
    real = bridge.backend.move_to

    def slow(x, y, z):
        started.set()
        time.sleep(0.4)
        return real(x, y, z)

    bridge.backend.move_to = slow
    first = threading.Thread(target=bridge.do, args=({"kind": "move", "x": 10.0, "relative": True},))
    first.start()
    started.wait(2)
    waiting: list = []
    second = threading.Thread(target=lambda: waiting.append(bridge.do({"kind": "move", "x": 10.0, "relative": True})))
    second.start()
    time.sleep(0.1)
    bridge.stop()  # while the second waits for the first
    first.join(5)
    second.join(5)
    assert waiting[0]["status"] == "stopped" and "before it started" in waiting[0]["why"]


def test_a_plan_position_without_z_is_acquired_at_the_focus_it_was_asked_at(bridge, scope):
    x, y, z = bridge.backend.position()
    zs: list = []
    real = bridge.backend.move_to

    def seen(mx, my, mz):
        real(mx, my, mz)
        zs.append(bridge.backend.position()[2])

    bridge.backend.move_to = seen
    r = bridge.do({"kind": "acquire", "plan": {"stage_positions": [{"x": x + 20, "y": y}],
                                               "z_plan": {"range": 2, "step": 1}}})
    assert r["status"] == "done" and len(r["made"]) == 3
    assert min(zs) >= z - 1 - 0.3 and max(zs) <= z + 1 + 0.3  # around where the focus was, never absolute 0


def test_a_web_page_cannot_ask_the_bridge(bridge):
    server = serve(bridge, 0)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        req = urllib.request.Request(url + "/api/do", data=b'{"action": {"kind": "snap"}}', method="POST",
                                     headers={"Content-Type": "text/plain", "Origin": "https://example.com"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 403
    finally:
        server.shutdown()


def test_the_http_api_acts_reads_and_never_serves_other_files(bridge, tmp_path):
    server = serve(bridge, 0)
    url = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method, path, body=None):
        req = urllib.request.Request(url + path, data=json.dumps(body).encode() if body is not None else None,
                                     method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    try:
        status, body = call("GET", "/api/state")
        state = json.loads(body)
        assert status == 200 and state["backend"]["backend"] == "openflexure" and state["limits"]["step_xy"] == 200
        status, body = call("POST", "/api/do", {"action": {"kind": "snap"}})
        snap = json.loads(body)
        assert snap["status"] == "done" and snap["made"][0].startswith("pictures/")
        status, body = call("GET", "/api/files/" + snap["made"][0])
        assert status == 200 and body[:2] == b"\xff\xd8"
        (tmp_path / "secret.txt").write_text("no")
        assert call("GET", "/api/files/secret.txt")[0] == 404
        assert call("GET", "/api/files/../../etc/passwd")[0] == 404
        assert call("PUT", "/api/limits", {"limits": {"frames": 9}})[0] == 400  # no words
        loosen = json.loads(call("PUT", "/api/limits", {"limits": {"frames": 99}, "words": "more pictures"})[1])
        assert loosen["status"] == "needs-yes" and loosen["offer"].startswith("y-")
        assert json.loads(call("POST", "/api/stop")[1])["status"] == "done"
        receipts = json.loads(call("GET", "/api/receipts?since=0")[1])["receipts"]
        assert [r["kind"] for r in receipts][-2:] == ["snap", "stop"]
    finally:
        server.shutdown()


# --- the fuzz ----------------------------------------------------------------------------------------------------------
def test_random_commands_make_zero_breaches_on_the_virtual_microscope(scope, tmp_path):
    witness = Witness(OpenFlexure(scope.url, um_per_step=1.0, um_per_z_step=1 / 100))
    b = ScopeBridge(witness, tmp_path)
    x, y, z = witness.position()
    b.limits = Limits(x=(x - 400, x + 400), y=(y - 400, y + 400), z=(z - 3, z + 3), step_xy=150, step_z=1,
                      frames=8, seconds=5)
    result = fuzz(b, witness, n=250, seed=11)
    assert result["breaches"] == [], result["breaches"][:5]
    assert result["statuses"].get("done", 0) > 30 and result["statuses"].get("refused", 0) > 30
    assert result["motorCommands"] > 20 and result["yesSaid"] >= 1


@pytest.mark.skipif(not SCOPE_PYTHON.exists(), reason="the scope bridge's own environment is not set up (make scope-setup)")
def test_random_commands_make_zero_breaches_on_micro_managers_demo():
    args = [str(SCOPE_PYTHON), "-m", "scope_bridge.fuzz", "--backend", "mm-demo", "--n", "400", "--seed", "5"]
    done = subprocess.run(args, cwd=SERVICES, capture_output=True, text=True, timeout=600)
    result = json.loads(done.stdout[done.stdout.index("{"):])
    assert done.returncode == 0 and result["breachCount"] == 0, result
    assert result["statuses"].get("done", 0) > 50


def test_the_recorder_keeps_what_the_hardware_was_told(scope):
    rec = Recorder(OpenFlexure(scope.url))
    x, y, z = rec.position()
    rec.move_to(x + 5, None, None)
    assert rec.commands == [("move_to", x + 5, None, None)]
    assert math.isclose(rec.position()[0], x + 5, abs_tol=1.0)
