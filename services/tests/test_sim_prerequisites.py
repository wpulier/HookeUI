"""Importing simulation helpers needs no private inputs; running the stack still does."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


def probe(manifest: Path, code: str):
    setup = f"""
from pathlib import Path
from orchestrator import assets
assets.MANIFEST = Path({str(manifest)!r})
import subprocess
def no_server(*args, **kwargs):
    raise AssertionError('a prerequisite check started a process')
subprocess.Popen = no_server
"""
    return subprocess.run([sys.executable, "-c", setup + code], capture_output=True, text=True, timeout=15)


def test_simulation_helpers_import_without_private_recordings(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"backup": {}, "real": [], "bench": []}))
    result = probe(manifest, """
from sim.harness import Outcome, Viewer, SCENARIOS, overlay_opacity
assert not Outcome('synthetic').checks
assert overlay_opacity([0]) == 1.0
assert Viewer and SCENARIOS
""")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mode", ["help", "unlisted", "missing", "explicit"])
def test_simulation_cli_explains_missing_inputs_before_starting_any_process(tmp_path, mode):
    manifest = tmp_path / "manifest.json"
    data = {"backup": {}, "real": [], "bench": []}
    if mode == "missing":
        data["real"] = [{"id": "2026-06-14-leaf-epidermis-3m43s", "file": "footage/real/absent.mp4"}]
    manifest.write_text(json.dumps(data))
    args = ["--help"] if mode == "help" else ["--video", str(tmp_path / "absent.mp4")] if mode == "explicit" else []
    result = probe(manifest, f"""
import runpy, socket, sys
socket.socket.connect = no_server
sys.argv = ['sim.harness', *{args!r}]
runpy.run_module('sim.harness', run_name='__main__')
""")
    if mode == "help":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "--video" in result.stdout
    else:
        assert result.returncode == 2, result.stdout + result.stderr
        assert "Simulation prerequisite:" in result.stderr
        assert ("not listed" if mode == "unlisted" else "missing") in result.stderr
        assert "Traceback" not in result.stderr


def test_external_private_manifest_resolves_input_without_changing_public_manifest(tmp_path):
    public = tmp_path / 'public.json'
    public.write_text(json.dumps({'backup': {}, 'real': []}))
    private = tmp_path / 'private.json'
    private.write_text(json.dumps({'backup': {}, 'real': [
        {'id': '2026-06-14-leaf-epidermis-3m43s', 'file': 'footage/private-fixture-missing.mp4'}
    ]}))
    before = public.read_bytes()
    result = probe(public, f"""
import runpy, sys
sys.argv = ['sim.harness', '--asset-manifest', {str(private)!r}]
runpy.run_module('sim.harness', run_name='__main__')
""")
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'Simulation prerequisite: recording is missing:' in result.stderr
    assert 'private-fixture-missing.mp4' in result.stderr
    assert public.read_bytes() == before


def test_missing_external_manifest_is_explained_before_any_process_starts(tmp_path):
    public = tmp_path / 'public.json'
    public.write_text(json.dumps({'backup': {}, 'real': []}))
    result = probe(public, f"""
import runpy, sys
sys.argv = ['sim.harness', '--asset-manifest', {str(tmp_path / 'absent.json')!r}]
runpy.run_module('sim.harness', run_name='__main__')
""")
    assert result.returncode == 2
    assert 'Simulation prerequisite: asset manifest is missing:' in result.stderr
    assert 'Traceback' not in result.stderr
