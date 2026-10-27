"""Contract 8's evidence (orchestrator/evidence.py): saved atomically in the background, filed in the
day's journal, read back after a restart (ADR 0001); a partial save is reported, never loaded; ids
never repeat in a day; past the disk budget, or while saving fails, new analyses are refused."""
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.capture import Capture
from orchestrator.day.store import DayStore
from orchestrator.evidence import Evidence, EvidenceRefused, Member, Population, polygons_of
from orchestrator.measure import Mask
from tests.waiting import HANG_S, until


def view(n=7):
    jpeg = cv2.imencode(".jpg", np.full((120, 160, 3), 180, np.uint8))[1].tobytes()
    info = SimpleNamespace(id=n, width=160, height=120, epoch=0, dx=3.0, dy=4.0, fov=None, disc=0.0, wall_at=1000.0)
    return Capture(n, jpeg, info)


def population(store: Evidence, capture, thread="t1", n=3) -> Population:
    members = OrderedDict()
    for i in range(n):
        bits = np.zeros((20, 20), bool)
        bits[2:18, 2:10 + 3 * i] = True
        mask = Mask(bits, 10 + 30 * i, 20)
        mid = store.member_id()
        members[mid] = Member(mid, i + 1, mask, polygons_of(mask), mask.box, {"score": 0.8})
    p = Population(store.new_id(), "cells", "cellpose-sam", {"operation": "cells", "implementation": "cellpose-sam"},
                   capture.id, {"epoch": 0, "dx": 3.0, "dy": 4.0, "disc": None, "width": 160, "height": 120, "at": 1000.0},
                   None, None, members, thread=thread, day="2026-10-07", capture=capture)
    p.measure_all(None, "x1")
    return p


def saved(p: Population, timeout=60.0) -> Population:
    """Waits for the background save itself (its thread), not a fixed time: under a sim's load a save can take
    longer than any short wait, and the wait ends as soon as it is done."""
    for thread in threading.enumerate():
        if thread.name == f"evidence-{p.id}":
            thread.join(timeout)
    t0 = time.time()
    while p.status == "saving" and time.time() - t0 < timeout:
        time.sleep(0.01)
    return p


def test_saved_evidence_comes_back_after_a_restart_with_its_ids_measurements_and_links(tmp_path):
    day = DayStore(root=tmp_path / "sessions", day="2026-10-07")
    store = Evidence(SimpleNamespace(), tmp_path / "evidence", day=day)
    p = population(store, view())
    store.add(p)
    store.schedule_save(p)
    assert saved(p).status == "saved" and p.path is not None
    assert p.capture is None and all(m.mask is None for m in p.members.values())  # its pixels left memory once saved
    store.link(p, "same-object", "c1", "o9", "promotion")
    saved(p)
    restarted_day = DayStore(root=tmp_path / "sessions", day="2026-10-07")
    again = Evidence(SimpleNamespace(), tmp_path / "evidence", day=restarted_day)
    back = again.get(p.id)
    assert back is not None and back.status == "saved" and list(back.members) == list(p.members)
    assert back.value("c1", "areaPx") == p.value("c1", "areaPx") and back.links[0]["to"] == "o9"
    assert again.reload_masks(back) and np.asarray(back.members["c1"].mask.bits).sum() == 16 * 8
    assert again.new_id() == "e2" and again.member_id() == "c4"  # ids never reused within a day


def test_masks_read_back_after_a_restart_leave_memory_again_once_nothing_holds_them(tmp_path):
    """What is read back from the saved file is on disk: once nothing holds it, it leaves memory again (only masks a
    saved file holds are released)."""
    store = Evidence(SimpleNamespace(), tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    p = population(store, view())
    store.add(p)
    store.schedule_save(p)
    saved(p)
    again = Evidence(SimpleNamespace(), tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    back = again.get(p.id)
    with again.keeping_pixels(back):  # a correction that changes nothing, say
        assert again.reload_masks(back) and all(m.mask is not None for m in back.members.values())
    assert all(m.mask is None for m in back.members.values())


def test_saved_is_never_said_while_a_save_asked_for_meanwhile_is_still_being_written(tmp_path, monkeypatch):
    """Off the loop a save finishes on its writer thread: another thread asking for a save just as that writer frees
    its slot either finds it busy (and it is saved again) or starts its own, and "saved" is never said over it."""
    import threading

    store = Evidence(SimpleNamespace(), tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    p = population(store, view())
    store.add(p)
    store.schedule_save(p)
    saved(p)
    real_done, real_bytes = store._done_writing, store._masks_bytes
    gate, asker = threading.Event(), []

    def another_thread_asks_now(q):
        again = real_done(q)
        if not asker:  # the first writer frees its slot: another thread asks for a save right then
            other = threading.Thread(target=store.schedule_save, args=(q,))
            other.start()
            asker.append(other)  # only once started: the test joins it as soon as it is here
            other.join(0.2)
        return again

    def the_later_save_waits(q):
        if asker:
            gate.wait(HANG_S)  # held until the test has looked
        return real_bytes(q)

    monkeypatch.setattr(store, "_done_writing", another_thread_asks_now)
    monkeypatch.setattr(store, "_masks_bytes", the_later_save_waits)
    store.schedule_save(p)  # a link, say
    until(lambda: asker, every=0.01)
    asker[0].join(HANG_S)
    time.sleep(0.05)  # the first writer's own last steps
    try:
        assert p.status == "saving"  # the later save is still being written
    finally:
        gate.set()
    assert saved(p).status == "saved"


def test_a_damaged_save_is_reported_and_not_loaded(tmp_path):
    day = DayStore(root=tmp_path / "sessions", day="2026-10-07")
    store = Evidence(SimpleNamespace(), tmp_path / "evidence", day=day)
    p = population(store, view())
    store.add(p)
    store.schedule_save(p)
    saved(p)
    (p.path / "masks.npz").write_bytes(b"cut short")  # as if the write had stopped part way
    again = Evidence(SimpleNamespace(), tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    assert again.get(p.id) is None and "incomplete, not loaded" in again.problems[0]


def test_rejected_reopen_releases_its_table_before_the_next_entry_is_admitted(tmp_path, monkeypatch):
    import weakref

    from orchestrator.capture import Captures, Memory

    day = DayStore(root=tmp_path / 'sessions', day='2026-10-07')
    source = Evidence(SimpleNamespace(), tmp_path / 'evidence', day=day)
    large, small = population(source, view()), population(source, view(8))
    large.members['c1'].polygons = [[[float(x), float(y)] for x, y in [(0, 0), (3, 0), (3, 3), (0, 3)]]] * 2048
    for item in (large, small):
        source.add(item)
        source.schedule_save(item)
        assert saved(item).status == 'saved'

    rejected, live_at_admission = [], []
    keep_summary = Evidence._keep_summary

    def observe_summary(self, item):
        rejected.append(weakref.ref(item))  # observe ownership without substituting the real admission/refusal
        return keep_summary(self, item)

    class ObservedMemory(Memory):
        def reserve(self, cls, nbytes, why):
            if cls == 'analysis':
                live_at_admission.append(any(ref() is not None for ref in rejected))
            return super().reserve(cls, nbytes, why)

    memory = ObservedMemory({'analysis': 32, 'unsaved': 0})  # parsing fits; retained summaries honestly refuse
    hub = SimpleNamespace()
    hub.captures = Captures(hub, memory)
    monkeypatch.setattr(Evidence, '_keep_summary', observe_summary)
    reopened = Evidence(hub, tmp_path / 'evidence', day=day)
    assert not reopened.items and len(reopened.unavailable) == 2
    assert len(rejected) == 2 and all(ref() is None for ref in rejected)
    assert live_at_admission == [False, False], 'the prior refused table must die before the next parsing allowance starts'
    assert all(n == 0 for n in memory.reserved.values())


def test_nothing_is_called_saved_before_its_files_are(tmp_path):
    store = Evidence(SimpleNamespace(), tmp_path / "evidence")
    blocked = tmp_path / "evidence"
    blocked.write_text("a file where the folder should be")  # saving cannot make its folder
    p = population(store, view())
    store.add(p)
    store.schedule_save(p)
    saved(p)
    assert p.status == "failed" and p.status_why
    assert not list(tmp_path.glob("**/manifest.json"))


def test_past_the_disk_budget_or_while_saving_fails_new_analyses_are_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_EVIDENCE_GB", "0.000001")  # about 1 KB
    store = Evidence(SimpleNamespace(), tmp_path / "evidence")
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "old.bin").write_bytes(b"x" * 4096)
    with pytest.raises(EvidenceRefused, match="nothing is deleted automatically"):
        store.admit()
    monkeypatch.setenv("MICROMIND_EVIDENCE_GB", "10")
    store = Evidence(SimpleNamespace(), tmp_path / "evidence")
    for _ in range(3):
        p = population(store, view())
        p.status, p.status_why = "failed", "disk full"
        store.items[p.id] = p
    with pytest.raises(EvidenceRefused, match="saving evidence has failed 3 times"):
        store.admit()


@pytest.mark.parametrize('budget_bytes', [50, 200])
def test_admission_during_an_atomic_manifest_publish_keeps_the_disk_budget(tmp_path, monkeypatch, budget_bytes):
    """A writer can rename the temporary file after a reader sees its metadata."""
    from pathlib import Path

    root = tmp_path / 'evidence'
    root.mkdir()
    (root / 'masks.npz').write_bytes(b'x' * 60)
    temporary = root / 'manifest.json.writer.tmp'
    temporary.write_bytes(b'm' * 30)
    published = root / 'manifest.json'
    store = Evidence(SimpleNamespace(), root)
    store.disk_gb = budget_bytes / 1024 ** 3
    real_stat = Path.stat
    renamed = []

    def publish_after_metadata(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path == temporary and not renamed:
            temporary.replace(published)
            renamed.append(True)
        return result

    monkeypatch.setattr(Path, 'stat', publish_after_metadata)
    if budget_bytes < 60:
        with pytest.raises(EvidenceRefused, match='its budget'):
            store.admit()
    else:
        store.admit()
    assert renamed and published.read_bytes() == b'm' * 30
    assert (root / 'masks.npz').read_bytes() == b'x' * 60


def test_a_correction_makes_a_revision_and_revision_zero_never_changes(tmp_path):
    store = Evidence(SimpleNamespace(), None)
    p = population(store, view())
    store.add(p)
    before = p.value("c2", "areaPx")
    p.members["c2"].status = "deleted"
    store.revise(p, "c2 is two cells, not one", by="user", members=["c2"])
    assert p.revision == 1 and p.revisions[0]["members"] == ["c2"] and p.value("c2", "areaPx") is None
    assert before is not None and len(p.live()) == 2
    assert p.status == "unsaved"  # no evidence folder in this run: said, never "saved"


def test_concurrent_saves_of_one_population_end_saved_once_and_never_failed(tmp_path):
    """Scaffolding review, M1.3 item 2: outline_cells then highlight(of="@1", top=5) asks for one save and
    five link saves at once; they raced on one temporary name and could end "failed"."""
    store = Evidence(SimpleNamespace(), tmp_path / "evidence")
    p = population(store, view())
    store.add(p)
    store.schedule_save(p)
    for i in range(5):
        store.link(p, "same-object", f"c{i % 3 + 1}", f"o{i}", "promotion")
    saved(p)
    until(lambda: p.status == "saved" and not store._writing)  # the dirty re-save, if one was needed
    assert p.status == "saved" and not store._writing
    import json

    manifest = json.loads((p.path / "manifest.json").read_text())
    assert len(manifest["links"]) == 5  # the last save holds every link
    assert not list((p.path).glob("*.tmp"))


def test_a_torn_save_never_gives_its_ids_again_nor_loses_its_folder(tmp_path):
    """Scaffolding review, M1.3 item 4: read back as "incomplete", its ids were given again and the next
    population wrote over its folder's capture."""
    day = DayStore(root=tmp_path / "sessions", day="2026-10-07")
    store = Evidence(SimpleNamespace(), tmp_path / "evidence", day=day)
    p = population(store, view())
    store.add(p)
    store.schedule_save(p)
    saved(p)
    (p.path / "masks.npz").write_bytes(b"torn")
    kept = (p.path / "capture.jpg").read_bytes()
    again = Evidence(SimpleNamespace(), tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    assert again.get(p.id) is None
    assert again.new_id() != p.id and again.member_id() not in p.members
    unsaved = Evidence(SimpleNamespace(), tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    q = population(unsaved, view(9))
    assert q.id != p.id and (p.path / "capture.jpg").read_bytes() == kept


def test_ids_made_but_never_saved_are_not_given_again_after_a_restart(tmp_path):
    day = DayStore(root=tmp_path / "sessions", day="2026-10-07")
    store = Evidence(SimpleNamespace(), None, day=day)  # nothing is saved in this run
    p = population(store, view())
    store.add(p)
    again = Evidence(SimpleNamespace(), None, day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    assert again.new_id() == "e2" and again.member_id() == "c4"
