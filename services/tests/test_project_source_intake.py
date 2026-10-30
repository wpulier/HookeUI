"""Actual App/Library/intake, synthetic pixels; no browser or camera qualification."""
import asyncio
import threading

from PIL import Image
import pytest

from orchestrator import commands
from orchestrator.door import actor
from orchestrator.paths import Paths
from orchestrator.project_media import MediaReference, ProjectMedia
from orchestrator.project_source_intake import retain
from tests.test_project_app_routing import build
from tests.waiting import HANG_S


@pytest.fixture
def intake(tmp_path):
    app = build(Paths.under(tmp_path / 'host'), tmp_path / 'Projects')
    sid = app.day.new('Copied images')
    original = tmp_path / 'original.png'
    Image.new('RGB', (17, 13), (4, 83, 212)).save(original)
    identity = app.hub.library.register('added:intake', original, title='Cells',
        at=1, origin=sid, group='added', optics={'umPerPx': 0.4})
    try:
        yield app, sid, identity, original
    finally:
        app.day.locations.close()  # each test joins its actual worker
        asyncio.run(app.stop())


def test_owner_intake_copies_without_stage_change_and_retry_survives_deleted_original(intake):
    app, sid, identity, original = intake
    expected = original.read_bytes()
    async def run():
        before = app.day.on_stage
        first = await commands.source_retain(app, None,
            {'asset': identity, 'session': sid, 'requestId': 'same-source-request'})
        assert first['storage'] == 'project'
        assert app.day.on_stage == before
        assert app.day.source_references[identity] == first['retained']
        original.unlink()
        second = await commands.source_retain(app, None,
            {'asset': identity, 'session': sid, 'requestId': 'same-source-request'})
        assert first == second
        metadata, saved = await asyncio.to_thread(ProjectMedia(app.hub.resources.projects).read,
                                                  MediaReference(**first['retained']))
        assert metadata['asset']['title'] == 'Cells'
        assert saved.read(0, saved.size) == expected
        record = app.day.catalogue.get(sid)
        assert record.source_references[identity] == first['retained']
        assert record.on_stage == before
    asyncio.run(run())


def test_copy_keeps_captured_project_and_agent_when_visible_project_changes(intake, monkeypatch):
    app, sid, identity, original = intake
    entered, resume = threading.Event(), threading.Event()
    original_retain = ProjectMedia.retain_library
    def delayed(self, *args, **kwargs):
        entered.set()
        assert resume.wait(HANG_S), 'Test did not release the intake worker'
        return original_retain(self, *args, **kwargs)
    monkeypatch.setattr(ProjectMedia, 'retain_library', delayed)
    async def run():
        token = actor.ACTOR.set('agent:ac_fixture')
        try:
            job = asyncio.create_task(retain(app, sid, identity, 'captured-source-request'))
            assert await asyncio.to_thread(entered.wait, HANG_S)
            other = app.day.new('Visible other Project')
            resume.set()
            result = await job
        finally:
            resume.set()
            actor.ACTOR.reset(token)
        assert app.day.sid == other and not app.day.source_references
        assert app.day.catalogue.get(sid).source_references[identity] == result['retained']
        document = app.hub.resources.projects.read(result['retained']['document'], sid)
        assert document.document['state']['sourceImport']['producer']['actor'] == 'agent:ac_fixture'
    asyncio.run(run())


def test_cancelled_intake_cannot_publish_source_binding(intake, monkeypatch):
    app, sid, identity, _ = intake
    entered, resume = threading.Event(), threading.Event()
    original_retain = ProjectMedia.retain_library
    def delayed(self, *args, **kwargs):
        entered.set()
        assert resume.wait(HANG_S), 'Test did not release the intake worker'
        return original_retain(self, *args, **kwargs)
    monkeypatch.setattr(ProjectMedia, 'retain_library', delayed)
    async def run():
        job = asyncio.create_task(retain(app, sid, identity, 'cancelled-source-request'))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            job.cancel()
            await asyncio.sleep(0)  # deliver cancellation before releasing the copy
        finally:
            resume.set()
        with pytest.raises(asyncio.CancelledError):
            await job
        assert not app.day.source_references
    asyncio.run(run())


def test_new_explicit_intake_preserves_prior_bytes_and_request_identity(intake):
    app, sid, identity, original = intake
    expected = original.read_bytes()
    async def run():
        first = await retain(app, sid, identity, 'first-material-request')
        app.day.open_source(identity, retained=first['retained'])
        Image.new('RGB', (17, 13), (99, 0, 12)).save(original)
        second = await retain(app, sid, identity, 'second-material-request')
        assert first['retained'] != second['retained']
        listed = await app.session_sources(sid)
        assert listed['onStageRetained'] == first['retained']
        assert listed['sources'][0]['retained'] == second['retained']
        _, saved = await asyncio.to_thread(ProjectMedia(app.hub.resources.projects).read,
                                           MediaReference(**first['retained']))
        assert saved.read(0, saved.size) == expected
        unrelated = app.hub.library.register('added:different', original, title='Different',
            at=2, origin=sid, group='added')
        with pytest.raises(ValueError, match='different source'):
            await retain(app, sid, unrelated, 'first-material-request')
        assert app.day.source_references[identity] == second['retained']
        journal = app.day.journal.path.read_bytes()
        assert await retain(app, sid, identity, 'first-material-request') == first
        assert app.day.source_references[identity] == second['retained']
        assert app.day.journal.path.read_bytes() == journal
        app.day.remove_source(identity)
        journal = app.day.journal.path.read_bytes()
        assert await retain(app, sid, identity, 'second-material-request') == second
        assert identity in app.day.removed_sources
        assert app.day.journal.path.read_bytes() == journal
        app.day.new('Different visible Project')
        assert await retain(app, sid, identity, 'second-material-request') == second
        restored = app.day.elsewhere(sid)
        assert identity in restored.removed_sources
        assert restored.journal.path.read_bytes() == journal
    asyncio.run(run())
