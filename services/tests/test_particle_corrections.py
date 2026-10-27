"""Studio/shared correction tools and file-backed evidence; Fiji transport is a stand-in."""
import asyncio
import copy
import json
import threading

import pytest

from orchestrator import population
from orchestrator.commands import handle
from tests.test_fiji_particle_journey import journey  # noqa: F401
from tests.test_sources import Client
from tests.waiting import HANG_S, until_async


async def saved(result):
    await until_async(lambda: result.status in ("saved", "failed"))
    assert result.status == "saved", result.status_why


async def analyzed(built, aid):
    await handle(built, Client(), {"type": "source", "open": aid})
    client = Client()
    await handle(built, client, {"type": "action", "capability": "cells",
        "args": {"compare_reference": True}, "use": {"cells": "fiji-analyze-particles"}})
    assert client.replies[-1]["ok"], client.replies[-1]
    result = population.evidence_of(built.hub).latest()
    await saved(result)
    return result


def test_original_measurements_and_producer_survive_remove_undo_remove_and_restart(journey):
    built, aid, _ = journey

    async def go():
        result = await analyzed(built, aid)
        initial = copy.deepcopy(built.hub.resources.document(result.id))
        rectangle = next(m.id for m in result.live() if m.box[0] == 70)
        survivors = set(result.members) - {rectangle}
        for change, count in [("remove", 2), ("undo", 3), ("remove", 2)]:
            client = Client()
            await handle(built, client, {"type": "tool", "name": "correct_cells",
                "args": {"of": result.id, "change": change, "members": [rectangle] if change == "remove" else []}})
            assert client.replies[-1]["ok"], client.replies[-1]
            await saved(result)
            document = built.hub.resources.document(result.id)
            assert len(document["detail"]["members"]) == count
            assert survivors <= {m["id"] for m in document["detail"]["members"]}
            assert document["detail"]["original"]["columns"] == initial["detail"]["columns"]
            assert document["detail"]["original"]["members"] == initial["detail"]["members"]
            assert document["detail"]["producer"] == initial["detail"]["producer"]
        from tests.standin_workers import build_app
        reopened = build_app(built.paths.home)
        document = reopened.hub.resources.document(result.id)
        assert document["detail"]["original"] == built.hub.resources.document(result.id)["detail"]["original"]
        assert document["entry"]["version"] == 3
        assert {m["id"] for m in document["detail"]["members"]} == survivors
        assert [r["by"] for r in document["detail"]["revisions"]] == ["user"] * 3
        assert [r["execution"] for r in document["detail"]["revisions"]] == [result.made_by["execution"]] * 3
        assert document["detail"]["revisions"][1]["undoes"] == 1

    asyncio.run(go())


def attach_studio(app, *, no_live_layer=False):
    """Existing legacy renderer transport; actual presentation handlers and saved pixels."""
    from orchestrator.screen import Screen
    from tests.test_screen import Studio

    app.hub.screen = Screen()
    class RetainedStudio(Studio):
        def hear(self, message):
            super().hear(message)
            if no_live_layer:
                for item in self.items.values():
                    if item.get("kind") == "population":
                        item["visible"] = False  # no outlines drawn without a current source/frame

    studio = RetainedStudio(app.hub.screen)
    app.hub.broadcast = app.hub.screen.broadcast = studio.hear
    population.layer_of(app.hub).broadcast = studio.hear
    studio.report()
    return studio


def test_saved_population_opens_its_own_picture_before_any_current_frame(journey):
    built, aid, _ = journey

    async def go():
        result = await analyzed(built, aid)
        from tests.standin_workers import build_app
        reopened = build_app(built.paths.home)
        studio = attach_studio(reopened, no_live_layer=True)
        assert reopened.hub.latest_info is None
        client = Client()
        await handle(reopened, client, {"type": "present", "what": result.id, "do": "open"})
        assert client.replies[-1]["ok"], client.replies[-1]
        assert reopened.magnifier.picture_now() is not None
        assert studio.items["magnifier"]["pictures"]
        assert reopened.hub.gpu_heavy.calls == []

    asyncio.run(go())


def test_original_and_current_views_read_retained_revisions_without_computation(journey):
    built, aid, _ = journey

    async def go():
        result = await analyzed(built, aid)
        attach_studio(built)
        client = Client()
        await handle(built, client, {"type": "population-correct", "session": built.day.sid,
            "population": result.id, "revision": 0, "change": "remove", "member": result.live()[2].id})
        await saved(result)
        await handle(built, client, {"type": "present", "what": result.id, "do": "open", "revision": 0})
        assert client.replies[-1]["ok"], client.replies[-1]
        original = built.magnifier.picture_now().copy()
        assert "Original revision 0" in original["label"] and "3 members" in original["label"]
        await handle(built, client, {"type": "present", "what": result.id, "do": "open", "revision": 1})
        assert client.replies[-1]["ok"], client.replies[-1]
        current = built.magnifier.picture_now()
        assert "Current revision 1" in current["label"] and "2 members" in current["label"]
        assert original["url"] != current["url"]
        assert result.revision == 1
        assert built.hub.gpu_heavy.inputs == []

    asyncio.run(go())


def test_manual_correction_pins_revision_and_retains_user_attribution(journey):
    built, aid, _ = journey

    async def go():
        result = await analyzed(built, aid)
        rectangle = next(m.id for m in result.live() if m.box[0] == 70)
        packet = {"type": "population-correct", "session": built.day.sid,
                  "population": result.id, "revision": 0, "change": "remove", "member": rectangle}
        client = Client()
        await handle(built, client, packet)
        assert client.replies[-1]["ok"], client.replies[-1]
        await saved(result)
        assert len(result.live()) == 2
        assert result.revisions[0]["asked"] == f"Remove member {rectangle} from {result.id} revision 0"
        assert result.revisions[0]["thread"] == built.conversation.here().thread
        await handle(built, client, packet)
        assert not client.replies[-1]["ok"] and "revision" in client.replies[-1]["error"]
        assert result.revision == 1
        await handle(built, client, {**packet, "revision": 1, "change": "undo"})
        assert client.replies[-1]["ok"] and len(result.live()) == 3

    asyncio.run(go())


@pytest.mark.parametrize("supersede", ["source", "session", "revision"])
def test_delayed_correction_never_changes_a_superseded_population(journey, monkeypatch, supersede):
    built, aid, _ = journey

    async def go():
        result = await analyzed(built, aid)
        member = result.live()[0].id
        started, release = threading.Event(), threading.Event()
        prepare = population.prepare_correction

        def delayed(*args):
            started.set()
            assert release.wait(HANG_S)
            return prepare(*args)

        monkeypatch.setattr(population, "prepare_correction", delayed)
        client = Client()
        packet = {"type": "population-correct", "session": built.day.sid, "population": result.id,
                  "revision": 0, "change": "remove", "member": member}
        task = asyncio.create_task(handle(built, client, packet))
        assert await asyncio.to_thread(started.wait, HANG_S)
        try:
            if supersede == "source":
                await handle(built, Client(), {"type": "source", "open": "live"})
            elif supersede == "session":
                await handle(built, Client(), {"type": "session", "action": "new", "name": "Other"})
            else:
                monkeypatch.setattr(population, "prepare_correction", prepare)
                other = Client()
                await handle(built, other, {**packet, "member": result.live()[1].id})
                assert other.replies[-1]["ok"]
        finally:
            release.set()
        await task
        assert client.replies[-1]["ok"] is False, client.replies[-1]
        assert result.members[member].status == "model"
        assert result.revision == (1 if supersede == "revision" else 0)

    asyncio.run(go())


@pytest.mark.parametrize("filename", ["masks.npz", "capture.jpg", "source.image", "manifest.json", "view", "files"])
def test_corrupt_retained_evidence_stays_visible_and_cannot_be_opened_or_corrected(journey, filename):
    built, aid, _ = journey

    async def go():
        result = await analyzed(built, aid)
        if filename in ("view", "files"):
            manifest = json.loads((result.path / "manifest.json").read_text())
            manifest[filename] = ["invalid shape"]
            (result.path / "manifest.json").write_text(json.dumps(manifest))
        else:
            (result.path / filename).write_bytes(b"corrupt")
        attach_studio(built)
        client = Client()
        await handle(built, client, {"type": "present", "what": result.id, "do": "open"})
        assert not client.replies[-1]["ok"], client.replies[-1]
        assert "unavailable" in (client.replies[-1].get("error") or client.replies[-1].get("summary") or "")
        await handle(built, client, {"type": "population-correct", "session": built.day.sid,
            "population": result.id, "revision": 0, "change": "remove", "member": result.live()[0].id})
        assert not client.replies[-1]["ok"] and result.revision == 0
        from tests.standin_workers import build_app
        reopened = build_app(built.paths.home)
        document = reopened.hub.resources.document(result.id)
        assert document is not None
        assert document["entry"]["state"] == "unavailable"
        assert document["entry"]["at"] >= result.made_at
        assert document["entry"]["thread"] == result.thread
        assert "restore" in document["entry"]["summary"].lower()
        attach_studio(reopened)
        await handle(reopened, client, {"type": "present", "what": result.id, "do": "open"})
        assert not client.replies[-1]["ok"]
        assert reopened.magnifier.picture_now() is None

    asyncio.run(go())
