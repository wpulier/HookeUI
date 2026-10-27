"""Retained cohort facts through the existing read and reopen interfaces.

Synthetic local files and Studio transport; actual Results, DayStore, Index,
Registry and Screen own storage and receipts. No model or browser runs here.
Chronology concerns the selected day/kind/producing-tool cohort, not the slide
or a judgment about which artifact the user should choose.
"""
import asyncio
import hashlib
import json
import math
import threading
import time
from pathlib import Path

import pytest

from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.day import DayStore
from orchestrator.magnifier import Magnifier
from orchestrator.resources import Index, READ_MAX, Results
from orchestrator.ticket import issue, using
from tests.test_population import world  # noqa: F401
from tests.test_resources import jpeg
from tests.test_screen import setup as reporting_studio
from tests.waiting import HANG_S

DAY = "2026-10-08"
OTHER_DAY = "2026-10-09"
PREFIX = "Saved entry chronology (this cohort only): "


def noon(day_name):
    return time.mktime((*map(int, day_name.split("-")), 12, 0, 0, 0, 0, -1))


def stores(world, tmp_path, *, day_name=DAY):
    # The session is used on the date it began, so its results are filed in that date's folder.
    day = DayStore(root=tmp_path / "sessions", day=day_name, clock=lambda: noon(day_name))
    saved = Results(tmp_path / "results", day=day)
    index = Index(world, day=day, results=saved, snapshots=tmp_path / "snapshots")
    world.resources = index
    return day, saved, index


def save(day, saved, monkeypatch, at, *, tool="compare", values=None, pictures=()):
    # The producer's wall clock is injectable; ID allocation and saving are real.
    with monkeypatch.context() as clock:
        clock.setattr("orchestrator.resources.time.time", lambda: at)
        entry = saved.add(tool, {"original": True}, "Retained producer result",
                          values=values or {"count": 7}, picture_bytes=pictures,
                          source={"frame": 81, "seq": 31, "boot": "original-camera"},
                          thread=day.active_id)
    assert (saved.root / entry["day"] / f"{entry['id']}.json").is_file()  # its date folder (the session's is its id)
    assert day.journal.path.is_file()
    return entry


def facts(text):
    lines = [line[len(PREFIX):] for line in text.splitlines() if line.startswith(PREFIX)]
    assert len(lines) == 1, "the existing read must expose one factual chronology record"

    def nonfinite(value):
        raise AssertionError(f"nonfinite chronology JSON: {value}")

    return json.loads(lines[0], parse_constant=nonfinite)


def retained(text):
    return json.loads(next(line.removeprefix("retained resource: ")
                           for line in text.splitlines() if line.startswith("retained resource: ")))


def test_read_reports_actual_cohort_order_not_allocation_order_or_title(world, tmp_path, monkeypatch):
    day, saved, index = stores(world, tmp_path)
    latest = save(day, saved, monkeypatch, 900)
    selected = save(day, saved, monkeypatch, 500)
    earliest = save(day, saved, monkeypatch, 100)
    tied = save(day, saved, monkeypatch, 500)
    save(day, saved, monkeypatch, 50, tool="count")
    folder = tmp_path / "snapshots" / DAY
    folder.mkdir(parents=True)
    (folder / "snapshot-000000-f1.json").write_text(json.dumps({"wallAt": 1, "frameId": 1}))
    other = DayStore(root=tmp_path / "sessions", day=OTHER_DAY)
    save(other, Results(tmp_path / "results", day=other), monkeypatch, 10)
    # A display title is editable metadata, never a chronology clock.
    day.record_result({**selected, "title": "First and latest, 1900-01-01"})
    text = index.read(selected["id"], DAY)
    chronology = facts(text)
    assert chronology == {"day": DAY, "kind": "result", "producer": "compare", "at": 500,
                          "total": 4, "earlier": 1, "sameTime": 2,
                          "earliest": [earliest["id"]], "latest": [latest["id"]],
                          "earliestOmitted": 0, "latestOmitted": 0, "unknownTimes": 0,
                          "complete": True, "unreadable": []}
    assert selected["id"] != earliest["id"] != latest["id"] and tied["id"] != selected["id"]
    assert retained(text) == {"id": selected["id"], "day": DAY, "version": None,
                              "source": selected["source"]}
    assert '"count": 7' in text and "First and latest, 1900-01-01" in text


def test_first_and_latest_ties_have_bounded_ids_and_explicit_omissions(world, tmp_path, monkeypatch):
    day, saved, index = stores(world, tmp_path)
    earliest = [save(day, saved, monkeypatch, 100)["id"] for _ in range(8)]
    selected = save(day, saved, monkeypatch, 200)
    latest = [save(day, saved, monkeypatch, 300)["id"] for _ in range(7)]
    chronology = facts(index.read(selected["id"], DAY))
    assert chronology["total"] == 16 and chronology["earlier"] == 8 and chronology["sameTime"] == 1
    assert chronology["earliest"] == sorted(earliest)[:5] and chronology["earliestOmitted"] == 3
    assert chronology["latest"] == sorted(latest)[:5] and chronology["latestOmitted"] == 2
    assert chronology["complete"] is True and chronology["unknownTimes"] == 0


@pytest.mark.parametrize("at", [0.0, -1.0], ids=["unix-zero", "finite-before-epoch"])
def test_finite_times_are_known_without_inventing_a_positive_time_rule(world, tmp_path, monkeypatch, at):
    day, saved, index = stores(world, tmp_path)
    selected = save(day, saved, monkeypatch, at)
    latest = save(day, saved, monkeypatch, 100)
    chronology = facts(index.read(selected["id"], DAY))
    assert chronology["at"] == at and math.isfinite(chronology["at"])
    assert chronology["earlier"] == 0 and chronology["sameTime"] == 1 and chronology["total"] == 2
    assert chronology["earliest"] == [selected["id"]] and chronology["latest"] == [latest["id"]]
    assert chronology["unknownTimes"] == 0 and chronology["complete"] is True


@pytest.mark.parametrize("unknown", [None, float("nan"), float("inf"), -float("inf")],
                         ids=["missing", "nan", "positive-infinity", "negative-infinity"])
def test_unknown_peer_time_is_not_borrowed_from_id_file_mtime_or_another_result(world, tmp_path, monkeypatch, unknown):
    day, saved, index = stores(world, tmp_path)
    peer = save(day, saved, monkeypatch, 100)
    selected = save(day, saved, monkeypatch, 200)
    # A retained legacy producer record with no usable time, published through
    # the real journal owner. Its values/source/id remain the producer's.
    day.record_result({**peer, "at": unknown})
    chronology = facts(index.read(selected["id"], DAY))
    assert chronology["at"] == 200 and chronology["total"] == 2
    assert chronology["earlier"] == 0 and chronology["sameTime"] == 1
    assert chronology["earliest"] == chronology["latest"] == [selected["id"]]
    assert chronology["unknownTimes"] == 1 and chronology["complete"] is False
    assert chronology["unreadable"] == []


def test_missing_selected_time_survives_journal_reload_as_unknown(world, tmp_path, monkeypatch):
    old, saved, _ = stores(world, tmp_path)
    selected = save(old, saved, monkeypatch, 100)
    latest = save(old, saved, monkeypatch, 300)
    # Legacy JSON genuinely omitted the producing time. Append a replacement
    # record to its actual journal, without passing through today's timestamping.
    legacy = {"type": "result", **selected}
    del legacy["at"]
    with old.journal.path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(legacy) + "\n")
    current = DayStore(root=tmp_path / "sessions", day=OTHER_DAY)
    index = Index(world, day=current, results=Results(tmp_path / "results", day=current))
    text = index.read(selected["id"], DAY)
    chronology = facts(text)
    assert chronology["day"] == DAY and chronology["at"] is None
    assert chronology["total"] == 2 and chronology["unknownTimes"] == 1
    assert chronology["earlier"] is None and chronology["sameTime"] is None
    assert chronology["earliest"] == chronology["latest"] == [latest["id"]]
    assert chronology["complete"] is False and chronology["unreadable"] == []
    assert retained(text)["source"] == selected["source"]
    assert current.day == OTHER_DAY and saved.get(selected["id"], DAY)["at"] == 100


def test_unattributed_corrupt_journal_tail_qualifies_completeness_without_fabricating_a_peer(world, tmp_path, monkeypatch):
    old, saved, _ = stores(world, tmp_path)
    first = save(old, saved, monkeypatch, 100)
    selected = save(old, saved, monkeypatch, 200)
    with old.journal.path.open("a", encoding="utf-8") as stream:
        stream.write('{"unknown: torn\n')
    current = DayStore(root=tmp_path / "sessions", day=OTHER_DAY)
    index = Index(world, day=current, results=Results(tmp_path / "results", day=current))
    chronology = facts(index.read(selected["id"], DAY))
    assert chronology["total"] == 2 and chronology["earlier"] == 1 and chronology["sameTime"] == 1
    assert chronology["earliest"] == [first["id"]] and chronology["latest"] == [selected["id"]]
    assert chronology["unknownTimes"] == 0 and chronology["complete"] is False
    assert chronology["unreadable"] and len(json.dumps(chronology)) < 2000
    assert len(saved.entries(DAY)) == 2  # no kind/tool/ID can be recovered from the torn line


def test_chronology_does_not_read_every_peers_full_values_or_expand_the_existing_read_cap(world, tmp_path, monkeypatch):
    day, saved, index = stores(world, tmp_path)
    peers = [save(day, saved, monkeypatch, n, values={"body": "peer" * 20000}) for n in range(20)]
    selected = save(day, saved, monkeypatch, 100, values={"body": "chosen" * 20000})
    opened = []
    actual_open = Path.open

    def observed(path, *args, **kwargs):
        if path.parent == saved.root / DAY and path.suffix == ".json":
            opened.append(path.name)
        return actual_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observed)  # passive external filesystem observation
    text = index.read(selected["id"], DAY)
    assert opened == [f"{selected['id']}.json"]
    chronology = facts(text)
    assert chronology["total"] == 21 and chronology["earlier"] == 20
    assert len(chronology["earliest"]) == len(chronology["latest"]) == 1
    assert len(text) < READ_MAX + 2000 and len(peers) == 20
    value_text = text.split("values: ", 1)[1]
    assert len(value_text) <= READ_MAX and value_text.startswith('{"body": "chosen')
    # The bounded legacy read includes metadata and explicitly advertises full pages.
    assert len(index.read_full(selected["id"], DAY)) > len(text)
    assert not value_text.endswith('"}')  # original cap remains a cut of the full values


def test_reopen_receipt_carries_original_chronology_after_real_studio_confirmation(world, tmp_path, monkeypatch):
    day, saved, index = stores(world, tmp_path)
    original = save(day, saved, monkeypatch, 100, pictures=(jpeg(67),))
    latest = save(day, saved, monkeypatch, 300, pictures=(jpeg(123),))
    magnifier = Magnifier(world)
    _, screen, studio = reporting_studio(world)
    registry = build(Services(hub=world, day=day, magnifier=magnifier))
    asked = day.here()
    ctx = ToolContext("chat", 1, None, "Open my first saved comparison", asked=asked)

    async def go():
        request = issue(world, "turn", asked=asked)
        with using(request):
            run = registry.start("present", {"what": original["id"], "do": "open"}, ctx)
            request.track(run.task)
            request.seal()
            await run.task
        return run, request

    run, request = asyncio.run(go())
    assert run.ok, run.summary
    assert studio.applied and screen.item("magnifier")["open"] is True
    assert run.resource["id"] == original["id"] and run.resource["day"] == DAY
    assert run.resource["operation"] == "reopened"
    chronology = run.resource["chronology"]
    assert chronology["at"] == 100 and chronology["total"] == 2 and chronology["earlier"] == 0
    assert chronology["earliest"] == [original["id"]] and chronology["latest"] == [latest["id"]]
    assert chronology["complete"] is True and chronology["unknownTimes"] == 0
    assert facts(index.read(original["id"], DAY)) == chronology
    shown = magnifier.picture_now()
    assert shown["resource"]["id"] == original["id"] and shown["resource"]["day"] == DAY
    raw = magnifier.pictures[shown["url"].rsplit("/", 1)[-1]]
    assert hashlib.sha256(raw).digest() == hashlib.sha256(jpeg(67)).digest()
    assert len(saved.entries()) == 2 and ctx.asked is asked
    assert request.admission.used == 0 and ctx.inspection_slots == [] and run.images == []


@pytest.mark.parametrize("boundary", ["cancel", "midnight", "new-session", "other-thread"])
def test_delayed_public_read_obeys_original_owner_and_does_not_spend_an_image_slot(world, tmp_path, monkeypatch, boundary):
    day, saved, index = stores(world, tmp_path)
    entry = save(day, saved, monkeypatch, 100)
    registry = build(Services(hub=world, day=day))
    asked = day.here()
    ctx = ToolContext("chat", 11, None, "Read my saved comparison", asked=asked)
    path_to_hold = saved.root / DAY / f"{entry['id']}.json"
    release, entered = threading.Event(), threading.Event()
    actual_open = Path.open

    def held(path, *args, **kwargs):
        if path == path_to_hold and (not args or args[0] == "r"):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test file barrier was not released")
        return actual_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", held)

    async def go():
        request = issue(world, "turn", asked=asked)
        with using(request):
            run = registry.start("resources", {"id": entry["id"]}, ctx)
            request.track(run.task)
            request.seal()
            assert await asyncio.to_thread(entered.wait, HANG_S), "actual retained values read was not reached"
            assert run.ok is None and not run.task.done()
            if boundary == "cancel":
                request.cancel("original reader cancelled")
            elif boundary == "midnight":
                # Midnight does nothing (ADR 0017): the session goes on, and so does its read.
                day.clock = lambda: time.mktime((2026, 10, 9, 12, 0, 0, 0, 0, -1))
            elif boundary == "new-session":
                day.new()
            else:
                day.open_thread("Unrelated newer conversation")
            release.set()
            await run.task
        return run, request

    try:
        run, request = asyncio.run(go())
    finally:
        release.set()
    assert ctx.asked is asked and ctx.turn_id == 11
    assert request.admission.used == 0 and ctx.inspection_slots == [] and run.images == []
    if boundary in ("other-thread", "midnight"):
        assert run.ok is True and (boundary == "midnight" or day.active_id != asked.thread)
        assert facts(run.summary)["day"] == DAY and retained(run.summary)["source"] == entry["source"]
    else:
        assert run.ok is False and run.resource is None
        assert not any(line.startswith(PREFIX) for line in run.summary.splitlines())
    assert json.loads(path_to_hold.read_text())["values"] == {"count": 7}


def test_missing_resource_stays_a_refusal_instead_of_a_fabricated_chronology(world, tmp_path):
    day, _, _ = stores(world, tmp_path)
    registry = build(Services(hub=world, day=day))

    async def go():
        run = registry.start("resources", {"id": "d999999"}, ToolContext("chat", 1, None, asked=day.here()))
        await run.task
        return run

    run = asyncio.run(go())
    assert run.ok is False and run.resource is None and run.images == []
    assert PREFIX not in run.summary
