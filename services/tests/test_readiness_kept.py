"""Kept readiness facts (orchestrator/readiness.py `keep`, `invalidate`, `refresh`; L1b part 2 item 1): what is on disk
is read once and kept while nothing it depends on changes, so the event loop reads facts already read."""
import threading

import pytest

from orchestrator import readiness


@pytest.fixture
def kept():
    readiness.keep(True)
    yield
    readiness.keep(False)


def counter():
    reads = []

    def read():
        reads.append(1)
        return len(reads)

    return reads, read


def test_off_nothing_is_kept_outside_a_batch_as_before():
    readiness.keep(False)
    reads, read = counter()
    readiness._once(("location", "x"), read)
    readiness._once(("location", "x"), read)
    assert len(reads) == 2


def test_on_a_stable_fact_is_read_once_until_something_changes(kept):
    reads, read = counter()
    assert readiness._once(("location", "x"), read) == 1
    assert readiness._once(("location", "x"), read) == 1
    readiness.invalidate("a download finished")
    assert readiness._once(("location", "x"), read) == 2
    assert len(reads) == 2


def test_only_the_stable_kinds_are_kept(kept):
    reads, read = counter()
    readiness._once(("keys-file",), read)
    readiness._once(("keys-file",), read)
    assert len(reads) == 2  # not a kept kind: outside a batch it is read each time, as before
    for kind in ("installed", "pinned-code", "weights", "unmade", "not-installed"):
        assert kind in readiness.KEPT_KINDS


def test_a_refresh_reads_them_again_and_an_old_fact_is_read_where_asked(kept, monkeypatch):
    reads, read = counter()
    readiness._once(("pinned-code", "molmo2-4b"), read)
    readiness.refresh(lambda: readiness._once(("pinned-code", "molmo2-4b"), read))
    assert len(reads) == 2 and readiness._once(("pinned-code", "molmo2-4b"), read) == 2
    monkeypatch.setattr(readiness, "KEPT_S", 0.0)
    assert readiness._once(("pinned-code", "molmo2-4b"), read) == 3


def test_a_fact_read_across_an_invalidation_is_not_kept(kept):
    started, go = threading.Event(), threading.Event()
    reads = []

    def slow():
        reads.append(1)
        started.set()
        go.wait(2)
        return "before the download"

    reader = threading.Thread(target=lambda: readiness._once(("location", "y"), slow))
    reader.start()
    started.wait(2)
    readiness.invalidate("the download finished meanwhile")
    go.set()
    reader.join(2)
    assert readiness._once(("location", "y"), lambda: "after") == "after"


def test_a_click_checks_its_implementation_through_the_kept_facts(kept, monkeypatch):
    """readiness.implementation (operations.availability, every click and request): EdgeTAM's files and pinned code
    are read once, not on every click."""
    from gpu_worker import model_files
    from orchestrator import shared_python
    from orchestrator.operations import OPERATIONS

    impl = next(i for i in OPERATIONS["outline"].implementations if i.id == "edgetam")
    located, pinned = [], []
    real_location = model_files.location
    monkeypatch.setattr(model_files, "location", lambda m, environ=None: located.append(m.key) or real_location(m, environ))
    monkeypatch.setattr(shared_python, "missing_from_pinned_code", lambda m, environ=None: pinned.append(m.key) or "")
    first = readiness.implementation(impl)
    assert readiness.implementation(impl) == first
    assert len(located) <= 1 and len(pinned) <= 1


def test_keeping_is_an_option():
    assert readiness.keeping_wanted({})
    assert not readiness.keeping_wanted({"MICROMIND_READINESS_KEPT": "off"})


def test_refresh_kept_reads_each_kept_fact_again_by_its_own_read(kept):
    reads, read = counter()
    readiness._once(("installed", "cellpose"), read)
    assert readiness.refresh_kept() == 1 and len(reads) == 2
    assert readiness._once(("installed", "cellpose"), read) == 2  # the refreshed value, no read on the asking side

    def broken():
        raise OSError("gone")

    readiness._once(("weights", "w"), lambda: "made")
    readiness._kept[("weights", "w")] = (*readiness._kept[("weights", "w")][:3], broken)
    readiness.refresh_kept()
    assert ("weights", "w") not in readiness._kept  # a fact that fails to read is read again where it is asked
