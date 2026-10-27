"""The Mac app's PATH guards (packaging/macos/HookeUI/main.swift, makeBin): on a Mac without the developer tools,
running one of macOS's /usr/bin stubs for them opens the "install the command line developer tools" dialog. On a
clean macOS 26.6.2 VM (2026-10-19) uv's first sync ran the unguarded install_name_tool and the dialog opened."""
import hashlib
import re
import sys
from pathlib import Path

import pytest

MAIN = Path(__file__).resolve().parents[2] / "packaging/macos/HookeUI/main.swift"


def guarded() -> set[str]:
    found = re.search(r"let developerToolStubs = \[(.*?)\]", MAIN.read_text(), re.S)
    assert found, "main.swift no longer lists developerToolStubs"
    return set(re.findall(r'"([^"]+)"', found.group(1)))


def test_the_guards_cover_what_the_first_launch_runs_and_the_earlier_list():
    names = guarded()
    assert "install_name_tool" in names  # uv patches its Python with it on every first launch
    assert {"git", "xcrun", "swift", "swiftc", "clang", "make", "python3", "pip3"} <= names
    assert "for tool in developerToolStubs" in MAIN.read_text()


@pytest.mark.skipif(sys.platform != "darwin", reason="reads this Mac's /usr/bin")
def test_every_developer_tool_stub_on_this_mac_is_guarded():
    stub = Path("/usr/bin/git").read_bytes()
    digest = hashlib.sha256(stub).digest()
    stubs = set()
    for path in Path("/usr/bin").iterdir():
        try:
            if path.is_file() and path.stat().st_size == len(stub) and hashlib.sha256(path.read_bytes()).digest() == digest:
                stubs.add(path.name)
        except OSError:
            continue  # unreadable system programs (sudo) are not stubs
    assert "git" in stubs
    assert stubs <= guarded(), f"unguarded developer-tool stubs: {sorted(stubs - guarded())}"
