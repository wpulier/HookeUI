"""The three homes of everything saved (orchestrator/saves.py, docs/saves.md): moving the saves out of the
checkout never deletes and switches only once every file is there; later writes by older code are
brought along, pruned logs are not; pruning lists before it removes, and only the dated performance logs."""
import json
import os
import time
from datetime import date

import pytest

from orchestrator import saves
from orchestrator.paths import MARKER, Paths


@pytest.fixture()
def old(tmp_path, monkeypatch):
    """A checkout's services/ and recordings/ with some of everything."""
    services, repo = tmp_path / "repo" / "services", tmp_path / "repo"
    monkeypatch.setattr(saves, "SERVICES", services)
    monkeypatch.setattr(saves, "REPO", repo)
    files = {
        "sessions/2026-10-06/journal.jsonl": "{}\n",
        "research/2026-10-06/r1/session.json": "{}",
        "snapshots/2026-10-06/snapshot-1.png": "png",
        "logs/interactions.jsonl": "{}\n",
        "logs/conversation.jsonl": "{}\n",
        "logs/votes.jsonl": '{"winner": "A"}\n',
        "logs/corrections.jsonl": "{}\n",
        "logs/crops/2026-09-01/a.jpg": "old crop",
        "logs/crops/2026-10-06/b.jpg": "new crop",
        "logs/saved/20260901-120000/interactions.jsonl": "old",
        "logs/orchestrator.log": "log",
        "capabilities/verdicts.jsonl": "{}\n",
    }
    for name, text in files.items():
        (services / name).parent.mkdir(parents=True, exist_ok=True)
        (services / name).write_text(text)
    (repo / "recordings").mkdir(parents=True)
    (repo / "recordings" / "camera.mp4").write_bytes(b"video")
    return services


def test_moving_lists_first_then_links_every_file_and_keeps_the_originals(old, tmp_path):
    root = tmp_path / "MicroscopeMind"
    root.mkdir()
    said: list[str] = []
    assert saves.move(root, apply=False, out=said.append) == 0
    assert not (root / "assets").exists() and not (root / MARKER).exists()  # listed, nothing changed
    assert saves.move(root, apply=True, out=said.append) == 0
    new = Paths.saves(root)
    assert (new.sessions / "2026-10-06" / "journal.jsonl").read_text() == "{}\n"
    assert (new.logs / "crops" / "2026-10-06" / "b.jpg").exists() and (new.logs / "orchestrator.log").exists()
    assert json.loads(new.votes_log.read_text()) == {"winner": "A"}  # judgments are kept apart
    assert (new.votes_log.parent / "corrections.jsonl").exists() and new.verdicts.exists()
    assert (new.recordings / "camera.mp4").read_bytes() == b"video"
    assert os.stat(new.recordings / "camera.mp4").st_ino == os.stat(old.parent / "recordings" / "camera.mp4").st_ino
    assert (old / "sessions" / "2026-10-06" / "journal.jsonl").exists()  # the originals stay
    assert (root / MARKER).exists()
    assert saves.move(root, apply=True, out=said.append) == 0  # again: nothing changes


def test_a_different_file_already_there_is_never_overwritten_and_nothing_switches(old, tmp_path):
    root = tmp_path / "MicroscopeMind"
    clash = Paths.saves(root).interaction_log
    clash.parent.mkdir(parents=True)
    clash.write_text("someone else's\n")
    said: list[str] = []
    assert saves.move(root, apply=True, out=said.append) == 1
    assert clash.read_text() == "someone else's\n" and not (root / MARKER).exists()
    assert any("conflict" in line for line in said)


def test_after_the_move_what_older_code_wrote_comes_along_and_what_was_pruned_does_not(old, tmp_path):
    root = tmp_path / "MicroscopeMind"
    saves.move(root, apply=True, out=lambda line: None)
    new = Paths.saves(root)
    (new.logs / "crops" / "2026-09-01" / "a.jpg").unlink()  # pruned in the new home
    later = old / "sessions" / "2026-10-07" / "journal.jsonl"  # written by an older checkout after the move
    later.parent.mkdir(parents=True)
    later.write_text("{}\n")
    marker = json.loads((root / MARKER).read_text())
    os.utime(later, (marker["movedAtEpoch"] + 5, marker["movedAtEpoch"] + 5))
    saves.sync(root, out=lambda line: None)
    assert (new.sessions / "2026-10-07" / "journal.jsonl").exists()
    assert not (new.logs / "crops" / "2026-09-01" / "a.jpg").exists()


def test_pruning_lists_first_and_removes_only_old_dated_performance_logs(old, tmp_path):
    paths = Paths._layout(old, recordings=old.parent / "recordings", keys_file=tmp_path / "k")
    said: list[str] = []
    listed = saves.prune(paths, days=14, apply=False, today=date(2026, 10, 6), out=said.append)
    assert sorted(p.name for p in listed) == ["2026-09-01", "20260901-120000"]
    assert all(p.exists() for p in listed)
    saves.prune(paths, days=14, apply=True, today=date(2026, 10, 6), out=said.append)
    assert not any(p.exists() for p in listed)
    assert (old / "logs" / "crops" / "2026-10-06" / "b.jpg").exists() and (old / "logs" / "votes.jsonl").exists()
    assert (old / "sessions" / "2026-10-06" / "journal.jsonl").exists()


def test_a_trashed_dataset_is_moved_whole_and_one_collecting_is_refused(tmp_path):
    from orchestrator.datasets import manifest, store

    root = tmp_path / "datasets"
    for dataset_id, status in (("ds-20261006-012139", "complete"), ("ds-20261006-200000", "collecting")):
        (root / dataset_id).mkdir(parents=True)
        manifest.write(root / dataset_id, {"id": dataset_id, "status": status})
    target = store.trash(root, "ds-20261006-012139", clock=lambda: time.mktime((2026, 10, 7, 9, 0, 0, 0, 0, -1)))
    assert target.parent == root / ".trash" and (target / "dataset.json").exists()
    assert [f.name for f, _ in manifest.all_in(root)] == ["ds-20261006-200000"]  # the trash is not listed
    with pytest.raises(ValueError):
        store.trash(root, "ds-20261006-200000")


def test_the_scripts_ask_where_the_saves_are_instead_of_assuming_services():
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    for script in ("scripts/save-logs.sh", "scripts/demo.sh"):
        code = "\n".join(line for line in (repo / script).read_text().splitlines() if not line.lstrip().startswith("#"))
        for place in ("services/logs", "services/research", "services/sessions", "$ROOT/recordings/"):
            assert place not in code, (script, place)


def test_pruning_keeps_new_and_legacy_research_traces(tmp_path):
    paths = Paths.under(tmp_path)
    traces = [paths.research / 'transcripts' / '2026-01-01' / 'r1.jsonl',
              paths.logs / 'transcripts' / '2026-01-01' / 'r2.jsonl']
    for path in traces:
        path.parent.mkdir(parents=True)
        path.write_text('{"round":1,"reply":"retained evidence"}\n')
    crop = paths.logs / 'crops' / '2026-01-01' / 'image.jpg'
    crop.parent.mkdir(parents=True)
    crop.write_bytes(b'disposable')
    removed = saves.prune(paths, days=14, apply=True, today=date(2026, 10, 29), out=lambda _: None)
    assert removed == [crop.parent]
    assert not crop.exists()
    assert all('retained evidence' in path.read_text() for path in traces)
