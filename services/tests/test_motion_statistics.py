"""Measured motion means survive retention and bounded readers.

Synthetic timed paths/JPEGs are external inputs. Actual movement measurement, the
registered tool/research operation, tracks store, index and reader cap stay real.
No model arithmetic, injected measurement result or changed producer is used.
"""
import asyncio
import copy
import json

import numpy as np
import pytest

from orchestrator.day import DayStore
from orchestrator.motiontracks import TracksStore, keep_measured, tracks_of
from orchestrator.movement import ClipFrame, Track, measure, words
from orchestrator.research.hands import Hands
from orchestrator.resources import Index
from research_worker.session_index import capped
from tests.test_motion_tracks import MotionHub, moving_slide, tool, wait_saved
from tests.test_movement import clip
from tests.test_research_samples import DAY, session


def statistics(item):
    """Absence is a behavior assertion after the actual measurement, not an API error."""
    value = item.summary.get("speedStatistics")
    assert isinstance(value, dict), "the complete measured tracks have no arithmetic speed statistics"
    assert value["method"] == "arithmetic mean of per-track rates"
    assert value["unit"] == item.unit
    assert value["basis"]  # the eligible tracked spots must not be presented as certified cells
    return value


@pytest.mark.parametrize("scale,unit,net,path,moving", [
    (None, "px/s", "37.5", "37.8333", "55"),
    (0.5, "µm/s", "18.75", "18.9167", "27.5"),
])
def test_motion_words_expose_arithmetic_means_for_both_explicit_populations(scale, unit, net, path, moving):
    """Catches median substitution, time/point weighting and net/path or subset confusion."""
    gray = np.zeros((8, 8), np.uint8)
    frames = [ClipFrame(0.0, gray, 0, 0), ClipFrame(3.0, gray, 0, 0)]
    # Independently worked rates: bent path net 5/2=2.5, path (3+4)/2=3.5;
    # the other rates are 10/1=10 and 300/3=100. Different lifetimes are intentional.
    measured = measure(frames, [
        Track([0, 1, 2], [(0, 0), (3, 0), (3, 4)]),
        Track([0, 1], [(0, 0), (10, 0)]),
        Track([0, 3], [(0, 0), (300, 0)]),
    ], scale)
    assert len(measured.counted) == 3 and len(measured.moving) == 2
    assert measured.counted[0]["net"] == (2.5 if scale is None else 1.25)
    assert measured.counted[0]["path"] == (3.5 if scale is None else 1.75)
    assert measured.unit == unit
    text = words(measured)
    assert "arithmetic mean" in text, text
    assert "all 3 counted tracks" in text.lower(), text
    assert "2 moving tracks" in text.lower(), text
    assert f"net speed {net} {unit}" in text, text
    assert f"path speed {path} {unit}" in text, text
    assert f"net speed {moving} {unit}" in text, text
    assert f"path speed {moving} {unit}" in text, text


def test_registered_motion_means_are_retained_once_with_original_source_and_read_back(tmp_path):
    """Catches omitted summaries, moving-only averages or save/source loss."""
    day = DayStore(root=tmp_path / "sessions", day=DAY)
    hub = MotionHub(moving_slide(), disc=False, base=(500.0, -300.0))
    hub.execution_day = day
    store = hub.tracks_store = TracksStore(hub, tmp_path / "evidence", day=day)
    run = tool(hub, {"show": False})
    assert run.ok, run.summary
    item = store.latest()
    assert item is not None and item.how == "measured"
    assert item.summary["counted"] == 9 and item.summary["moving"] == 5
    assert wait_saved(item) == "saved"
    original_source, original_execution = copy.deepcopy(item.view), item.made_by["execution"]
    stats = copy.deepcopy(statistics(item))
    assert stats["allCounted"]["count"] == 9 and stats["moving"]["count"] == 5
    # The five external dot rates are 60, 40, sqrt(1800), sqrt(2125), sqrt(1250).
    # Four stationary dots also belong to the counted population. JPEG/spot rounding
    # warrants the same physical-rate tolerance as the original movement fixtures.
    assert stats["allCounted"]["netMean"] == pytest.approx(24.8755, abs=3)
    assert stats["moving"]["netMean"] == pytest.approx(44.7759, abs=4)
    assert stats["allCounted"]["pathMean"] >= stats["allCounted"]["netMean"]
    assert stats["moving"]["pathMean"] >= stats["moving"]["netMean"]
    assert "arithmetic mean" in run.summary and "arithmetic mean" in item.words()
    assert "all 9 counted tracks" in run.summary.lower()
    manifest = json.loads((item.path / "manifest.json").read_text())
    assert manifest["summary"]["speedStatistics"] == stats
    assert manifest["view"] == original_source and manifest["execution"] == original_execution
    again = DayStore(root=tmp_path / "sessions", day=DAY)
    loaded = TracksStore(hub, tmp_path / "evidence", day=again).get(item.id)
    assert loaded is not None and loaded.status == "saved"
    assert statistics(loaded) == stats and loaded.view == original_source
    assert loaded.made_by["execution"] == original_execution


def test_concurrent_requests_share_one_motion_aggregate_and_original_execution():
    """Catches a second projection/item for the same actual in-flight measurement."""
    hub = MotionHub(moving_slide(), disc=False)
    frames = [(hub.source.get(fid), info) for fid, info in hub.infos.items()]

    async def both():
        return await asyncio.gather(keep_measured(hub, frames, None, "", 4),
                                    keep_measured(hub, frames, None, "", 4))

    (first, reused_a, done_a), (second, reused_b, done_b) = asyncio.run(both())
    assert first is second and done_a.execution is done_b.execution
    assert sorted([reused_a, reused_b]) == [False, True]
    assert len(tracks_of(hub).items) == 1
    assert first.summary["counted"] == 9 and first.summary["moving"] == 5
    stats = statistics(first)
    assert stats["allCounted"]["count"] == 9 and stats["moving"]["count"] == 5


def test_empty_measured_population_has_null_means_not_zero_speed():
    """Catches empty=stationary substitution and a fabricated denominator."""
    hub = MotionHub(clip([], [], seconds=1.6, noise=0), disc=False)
    run = tool(hub, {"seconds": 1.5, "show": False})
    assert run.ok, run.summary
    item = tracks_of(hub).latest()
    assert item.summary["counted"] == 0 and not item.rows
    stats = statistics(item)
    assert stats["allCounted"] == {"count": 0, "netMean": None, "pathMean": None}
    assert stats["moving"] == {"count": 0, "netMean": None, "pathMean": None}
    assert "nothing was followed" in run.summary


def test_stationary_measured_population_has_known_zero_mean_but_no_moving_mean():
    """A useful zero-versus-unknown control, using actual tracked stationary JPEG spots."""
    hub = MotionHub(clip([], [(300, 200), (500, 500)], seconds=1.6, noise=0), disc=False)
    run = tool(hub, {"seconds": 1.5, "show": False})
    assert run.ok, run.summary
    item = tracks_of(hub).latest()
    assert item.summary["counted"] == 2 and item.summary["moving"] == 0
    stats = statistics(item)
    assert stats["allCounted"] == {"count": 2, "netMean": 0.0, "pathMean": 0.0}
    assert stats["moving"] == {"count": 0, "netMean": None, "pathMean": None}


def test_research_means_cover_all_tracks_and_survive_bounded_read_without_rendered_subset_arithmetic():
    """Catches incomplete reader rows as population and loss of aggregates behind long row text."""
    # The detector's inner-view crop excludes some configured stationary dots.
    # Count the actual eligible retained tracks, not the original drawing commands.
    # One independently known 60 px/s mover is on a clear lower row.
    still = [(55 + 55 * x, 30 + 28 * y) for y in range(21) for x in range(20)]
    hub = MotionHub(clip([(100, 650, 60, 0)], still, seconds=1.6, fps=15, noise=0), disc=False)
    research = session(day=DAY)
    research.origin = {"thread": "t2"}
    reply = asyncio.run(Hands(hub).run(research, "motion", "compare the actual average speed", seconds=1.5))
    assert reply["ok"], reply
    store = tracks_of(hub)
    item = store.get(reply["id"])
    assert item is not None and item.made_for == "Researcher 2" and item.thread == "t2"
    complete = tuple(item.rows)
    count = len(complete)
    moving = [row for row in complete if row["values"]["moving"]]
    assert count > 200 and item.summary["counted"] == count
    assert item.summary["moving"] == 1 and len(moving) == 1 and store.latest() is None
    assert moving[0]["values"]["net"] == pytest.approx(60, abs=3)
    assert not [message for message in hub.sent if "tracks" in str(message.get("type", ""))]
    assert research.ran[-1]["id"] == item.id and research.ran[-1]["counted"] == count
    wire = store.wire(item)
    assert wire["count"] == count and wire["left"] == max(0, count - 400)
    assert len(wire["tracks"]) == min(count, 400)
    index = Index(hub)
    entry = index.get(item.id)
    assert entry is not None and entry.source["frame"] == item.capture_id
    assert len(entry.values["tracks"]) == count
    # These are the actual index projections consumed by the resource HTTP route.
    # No live HTTP server or synthetic measurement reply is needed for this cap seam.
    payload = {"entry": entry.wire(), "detail": {"text": index.read(item.id), "values": entry.values}}
    reading, was_cut = capped(payload)
    assert was_cut and len(reading) == 12000  # preserve the original bounded-input contract
    assert complete[0]["id"] in reading and complete[-1]["id"] not in reading
    # Independent consumer-side oracle over every original retained row, before
    # either reader or renderer truncation; source means use unrounded measurements.
    all_net = sum(row["values"]["net"] for row in complete) / count
    all_path = sum(row["values"]["path"] for row in complete) / count
    stats = statistics(item)
    assert stats["allCounted"]["count"] == count and stats["moving"]["count"] == 1
    assert stats["allCounted"]["netMean"] == pytest.approx(all_net, abs=0.0001)
    assert stats["allCounted"]["pathMean"] == pytest.approx(all_path, abs=0.0001)
    assert stats["allCounted"]["netMean"] == pytest.approx(60 / count, abs=3 / count)
    assert stats["moving"]["netMean"] == pytest.approx(60, abs=3)
    assert stats["moving"]["pathMean"] == pytest.approx(moving[0]["values"]["path"], abs=0.0001)
    assert stats["allCounted"]["netMean"] < stats["moving"]["netMean"] / 200
    assert entry.values["summary"]["speedStatistics"] == stats
    # Means/counts must be factual early text, not inferred by the model from retained rows.
    for text in (reply["words"], reading):
        assert "arithmetic mean" in text
        assert f"all {count} counted tracks" in text.lower()
        assert "1 moving track" in text.lower()
        assert f"net speed {stats['allCounted']['netMean']:g} px/s" in text
        assert f"path speed {stats['moving']['pathMean']:g} px/s" in text
