"""The root AGENTS.md is a map, read whole by every agent session (Claude Code and Codex alike): short, and every part's
file it names exists and is named (the split of 2026-10-15; scripts/check_agents_split.py checked the move).

Claude Code's guidance is under 200 lines for a file loaded every session; Codex stops reading project instructions at
32 KiB. A part's description goes in its own file: a nested AGENTS.md beside its code, or docs/architecture/.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP = ROOT / "AGENTS.md"
SKIP = {".git", ".claude", ".scratch", ".venv", "node_modules", "__pycache__", "dist", "dist-camera"}


def named():
    """Every file the map names with a path (a backticked `dir/file.md`)."""
    return set(re.findall(r"`((?:[\w.-]+/)+[\w.-]+\.md)`", MAP.read_text()))


def parts():
    """Every part's file in the repository: each nested AGENTS.md and each docs/architecture/ page."""
    found = {str(p.relative_to(ROOT)) for p in (ROOT / "docs" / "architecture").glob("*.md")}
    for top, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP]
        if "AGENTS.md" in files and Path(top) != ROOT:
            found.add(str((Path(top) / "AGENTS.md").relative_to(ROOT)))
    return found


def test_the_map_is_short_enough_to_be_read_whole():
    text = MAP.read_text()
    assert text.count("\n") <= 200, "AGENTS.md is past 200 lines: put the description in its part's file"
    assert len(text.encode()) <= 32 * 1024, "AGENTS.md is past 32 KiB, where Codex stops reading"


def test_every_file_the_map_names_exists():
    assert sorted(n for n in named() if not (ROOT / n).is_file()) == []


def test_every_part_file_is_named_in_the_map():
    assert sorted(parts() - named()) == []


def test_private_acceptance_checkouts_are_not_application_parts(tmp_path, monkeypatch):
    import sys

    snapshot = tmp_path / '.scratch' / 'baseline'
    snapshot.mkdir(parents=True)
    (snapshot / 'AGENTS.md').write_text('Retained baseline instructions')
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', tmp_path)
    assert parts() == set()
