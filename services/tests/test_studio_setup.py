"""Setup's command-line boundary, with operating-system tools replaced at PATH."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "studio_setup.py"


def preflight(tmp_path, *, node="v22.12.0", missing=()):
    for command in ("uv", "node", "npm", "ffmpeg", "ffprobe", "swiftc", "codesign"):
        if command in missing:
            continue
        tool = tmp_path / command
        tool.write_text("#!/bin/sh\n" + (f"echo {node}\n" if command == "node" else "exit 0\n"))
        tool.chmod(0o755)
    return subprocess.run([sys.executable, str(SCRIPT)], text=True, capture_output=True,
                          env={**os.environ, "PATH": str(tmp_path)})


def test_setup_names_missing_prerequisites_before_installing(tmp_path):
    result = preflight(tmp_path, missing=("uv", "ffprobe", "swiftc"))
    assert result.returncode == 1
    assert "uv" in result.stderr and "brew install uv" in result.stderr
    assert "ffprobe" in result.stderr and "brew install ffmpeg" in result.stderr
    assert "swiftc" in result.stderr and "xcode-select --install" in result.stderr


@pytest.mark.parametrize("node", ["v20.19.0", "v22.11.0", "v23.0.0", "v25.0.0", "bad version"])
def test_setup_rejects_node_versions_outside_both_build_and_test_contracts(tmp_path, node):
    result = preflight(tmp_path, node=node)
    assert result.returncode == 1
    assert "Node.js" in result.stderr and "22.12" in result.stderr
    assert "node@24" in result.stderr


@pytest.mark.parametrize("node", ["v22.12.0", "v22.16.0", "v24.0.0", "v26.0.0"])
def test_setup_accepts_supported_node_versions(tmp_path, node):
    result = preflight(tmp_path, node=node)
    assert result.returncode == 0, result.stderr
