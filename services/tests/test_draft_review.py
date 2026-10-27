"""A draft is admitted through its review (ticket 62 L13, orchestrator/drafts.py): the user reads exactly what ran (its
adapter, packages, pinned files with their origin, what it imports, every try with its error and the static checks a
contribution gets), and Use it takes back that review's digest. A draft pinning several files becomes one of yours with all
of them (live 2026-10-20: the napari-segment-blobs draft pinned its upstream source and licence, and Use it failed with
"LICENSE is missing", said only as "something went wrong in the app", leaving a half-made entry)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog
from orchestrator import downloads, drafts
from tests.test_drafts import ADAPTER, bench, macos  # noqa: F401 - the shared bench fixture

FIELDS = {"name": "Blob labels", "what": "Outlines every blob", "licence": "BSD-3-Clause", "tier": "open"}
SOURCE = "https://raw.githubusercontent.com/maya/blobs/" + "f" * 40


def _pin(did, path, body):
    class Stream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self, n):
            yield body

    return drafts.pin(did, f"{SOURCE}/{path}", http=SimpleNamespace(stream=lambda *a, **k: Stream()))


def _ran(did, adapter=ADAPTER):
    drafts.write(did, adapter, "numpy==2.0.0", FIELDS)
    out = drafts.attempt(did)
    assert out["ok"], out


@macos
def test_review_shows_exactly_what_ran_and_use_it_adopts_every_pinned_file(bench):
    did = drafts.create("https://github.com/maya/blobs")
    drafts.write(did, "def segment(picture, params):\n    raise ValueError('its source is missing')\n", "numpy==2.0.0", FIELDS)
    assert not drafts.attempt(did)["ok"]
    _pin(did, "__init__.py", b"def blobs(image):\n    return image\n")
    _pin(did, "LICENSE", b"BSD 3-Clause License\n")
    _ran(did)
    seen = drafts.review(did)
    assert seen["adapter"] == ADAPTER and seen["packages"] == ["numpy==2.0.0"] and seen["requirements"] == ["numpy==2.0.0"]
    assert [f["path"] for f in seen["files"]] == ["__init__.py", "LICENSE"]
    assert all(f["url"].startswith(SOURCE) and len(f["sha256"]) == 64 for f in seen["files"])
    assert seen["imports"] == ["cv2", "numpy"]
    assert [t["ok"] for t in seen["tries"]] == [False, True] and "its source is missing" in seen["tries"][0]["error"], \
        "a failed try is kept in the review"
    assert not any(c["blocks"] for c in seen["checks"]), seen["checks"]
    assert any("2 pinned files" in f for f in seen["facts"])
    assert seen["scanned"] == ["adapter.py", "requirements.in", "env.lock", "__init__.py (pinned)"]
    assert seen["byHash"] == ["LICENSE"] and any("LICENSE was checked by hash only" in f for f in seen["facts"]), \
        "the review says exactly what its checks read (L13 review: 'nothing found' overclaimed)"
    row = next(r for r in drafts.wire() if r["id"] == did)
    assert row["review"]["digest"] == seen["digest"]
    used = drafts.promote(did, reviewed=seen["digest"])
    m = catalog.mine()[used["model"]]
    published = downloads.model_files.revision_dir(downloads.model_files.owner(m))
    assert {"LICENSE", "__init__.py"} <= {p.name for p in published.iterdir()}
    assert drafts._state(did)["reviewed"]["digest"] == seen["digest"]
    assert "reviewed by you" in used["says"]


@macos
def test_use_it_takes_only_the_digest_of_what_was_reviewed(bench):
    did = drafts.create("https://github.com/maya/blobs")
    _ran(did)
    with pytest.raises(drafts.DraftError, match="review what it runs"):
        drafts.promote(did)
    old = drafts.review(did)["digest"]
    _ran(did, ADAPTER + "\n# changed after its review\n")
    with pytest.raises(drafts.DraftError, match="changed since you read it"):
        drafts.promote(did, reviewed=old)
    assert drafts.promote(did, reviewed=drafts.review(did)["digest"])["model"]


@macos
def test_a_failed_use_it_says_why_and_leaves_nothing_half_made(bench, monkeypatch):
    did = drafts.create("https://github.com/maya/blobs")
    _pin(did, "__init__.py", b"def blobs(image):\n    return image\n")
    _ran(did)
    digest = drafts.review(did)["digest"]

    def refused(*a, **k):
        raise downloads.Failed("a downloaded file didn't match what the catalog pins, so nothing was used: disk full")

    monkeypatch.setattr(downloads, "publish", refused)
    with pytest.raises(drafts.DraftError, match="nothing was added: .*disk full"):
        drafts.promote(did, reviewed=digest)
    assert not any(Path(catalog.mine_root()).glob("*")), "no half-made entry of yours"
    assert drafts._state(did)["state"] == "ran", "the draft and its tries are kept"
    monkeypatch.undo()
    monkeypatch.setenv("MICROMIND_CATALOG", str(bench / "catalog"))
    assert drafts.promote(did, reviewed=digest)["model"] == f"mine/{did}"


@macos
def test_a_check_that_blocks_a_contribution_blocks_use_it(bench):
    did = drafts.create("https://github.com/maya/blobs")
    _ran(did, ADAPTER + "\ndef _later():\n    import httpx\n    return httpx.get('https://example.org/weights')\n")
    seen = drafts.review(did)
    blocking = [c["says"] for c in seen["checks"] if c["blocks"]]
    assert blocking and "network" in blocking[0], seen["checks"]
    with pytest.raises(drafts.DraftError, match="its review found"):
        drafts.promote(did, reviewed=seen["digest"])


@macos
def test_the_wire_never_waits_on_a_use_it_in_progress(bench):
    """The capabilities wire is drawn on the event loop; Use it holds the drafts' lock while it checks its files' hashes
    (up to gigabytes of weights), so the wire's review never waits for it."""
    import threading
    import time

    did = drafts.create("https://github.com/maya/blobs")
    _ran(did)
    held, release = threading.Event(), threading.Event()

    def use_it_in_progress():
        with drafts._LOCK:
            held.set()
            release.wait(5)

    threading.Thread(target=use_it_in_progress, daemon=True).start()
    held.wait(2)
    t0 = time.monotonic()
    row = next(r for r in drafts.wire() if r["id"] == did)
    release.set()
    assert time.monotonic() - t0 < 1 and row["review"]["busy"] and "right now" in row["review"]["unavailable"]


@macos
def test_pinned_code_is_scanned_and_its_findings_are_notes_for_the_reviewer(bench):
    did = drafts.create("https://github.com/maya/blobs")
    _pin(did, "__init__.py", b"import httpx\ndef fetch():\n    return httpx.get('https://example.org')\n")
    _ran(did)
    seen = drafts.review(did)
    notes = [c["says"] for c in seen["checks"] if not c["blocks"]]
    assert any("its pinned __init__.py fetches from the network" in n for n in notes), seen["checks"]
    assert not any(c["blocks"] for c in seen["checks"]), "the makers' code is read by the reviewer, not refused outright"


@macos
def test_a_todo_in_the_ais_own_comment_is_for_the_reviewer_not_a_block(bench):
    """Ticket 62 L13 review: any "TODO" in an AI-written adapter blocked Use it with no way out. A folder or workspace
    candidate with a TODO stays unfinished (contribute.preflight); a draft's review blocks only a scaffold placeholder."""
    from orchestrator import contribute

    did = drafts.create("https://github.com/maya/blobs")
    _ran(did, ADAPTER + "\n# TODO: handle 3D stacks later\n")
    seen = drafts.review(did)
    assert not any(c["blocks"] for c in seen["checks"]), seen["checks"]
    assert drafts.promote(did, reviewed=seen["digest"])["model"]
    other = drafts.create("https://github.com/maya/other-blobs")
    _ran(other, ADAPTER + f"\n# {contribute.PLACEHOLDERS[0]}\n")
    assert any("still says TODO" in c["says"] and c["blocks"] for c in drafts.review(other)["checks"])
