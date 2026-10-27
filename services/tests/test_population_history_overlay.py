"""Real correction/overlay reads; synthetic input and Fiji process stand-in only."""
import asyncio
import copy
import pytest

from orchestrator.artifact_overlay import read_overlay
from orchestrator.commands import handle
from tests.test_fiji_particle_journey import journey  # noqa: F401
from tests.test_particle_corrections import analyzed, saved
from tests.test_sources import Client


def test_original_contours_survive_remove_undo_remove_without_mutating_accepted_population(journey):
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        original = await read_overlay(built, result.id, result.session, 0)
        rectangle = next(m.id for m in result.live() if m.box[0] == 70)
        for change in ('remove', 'undo', 'remove'):
            caller = Client()
            await handle(built, caller, {'type': 'population-correct', 'session': result.session,
                'population': result.id, 'revision': result.revision, 'change': change,
                **({'member': rectangle} if change == 'remove' else {})})
            assert caller.replies[-1]['ok'], caller.replies
            await saved(result)
        before = copy.deepcopy(result.wire()), copy.deepcopy(result.original), copy.deepcopy(result.revisions)
        historical = await read_overlay(built, result.id, result.session, 0)
        current = await read_overlay(built, result.id, result.session, 3)
        assert historical['revision'] == 0 and historical['count'] == 3
        assert historical['members'] == original['members']
        assert historical['coverage'] == original['coverage']
        assert historical['canUndo'] is False
        assert current['revision'] == 3 and current['count'] == 2
        assert {m['id'] for m in current['members']} == {m['id'] for m in original['members']} - {rectangle}
        assert all(m['polygons'] for m in historical['members'])
        assert before == (result.wire(), result.original, result.revisions)
        from tests.standin_workers import build_app
        reopened = build_app(built.paths.home)
        replayed = await read_overlay(reopened, result.id, result.session, 0)
        assert replayed['members'] == original['members'] and replayed['revision'] == 0
    asyncio.run(go())


def test_historical_masks_refuse_unavailable_or_unretained_snapshots(journey):
    import pytest
    from orchestrator.artifact_overlay import OverlayRefused
    built, aid, _ = journey
    async def go():
        result = await analyzed(built, aid)
        rectangle = next(m.id for m in result.live() if m.box[0] == 70)
        for change in ('remove', 'undo'):
            caller = Client()
            await handle(built, caller, {'type':'population-correct','session':result.session,
                'population':result.id,'revision':result.revision,'change':change,
                **({'member':rectangle} if change=='remove' else {})})
            assert caller.replies[-1]['ok']; await saved(result)
        before = copy.deepcopy(result.wire())
        for revision in (-1, 1, 3, True, None):
            with pytest.raises(OverlayRefused): await read_overlay(built,result.id,result.session,revision)
        original = result.original
        try:
            for damaged in (None, {**original, 'members':[{'id':'missing'}]}, {**original, 'columns':{}}):
                result.original = damaged
                with pytest.raises(OverlayRefused): await read_overlay(built,result.id,result.session,0)
        finally: result.original = original
        assert result.wire() == before
        (result.path/'masks.npz').write_bytes(b'corrupted')
        with pytest.raises(OverlayRefused): await read_overlay(built,result.id,result.session,0)
    asyncio.run(go())


@pytest.mark.parametrize('change', ['correction', 'session', 'replacement', 'cancel'])
def test_historical_masks_refuse_superseded_reads(journey,monkeypatch,change):
    import threading
    import pytest
    from orchestrator.artifact_overlay import OverlayRefused
    from tests.waiting import HANG_S
    built,aid,_=journey
    async def go():
        result=await analyzed(built,aid)
        rectangle=next(m.id for m in result.live() if m.box[0]==70)
        caller=Client()
        await handle(built,caller,{'type':'population-correct','session':result.session,
            'population':result.id,'revision':0,'change':'remove','member':rectangle})
        assert caller.replies[-1]['ok'];await saved(result)
        store=built.hub.evidence
        entered,release=threading.Event(),threading.Event()
        original=store.require_retained
        def delayed(item):
            if threading.current_thread() is not threading.main_thread():
                entered.set();assert release.wait(HANG_S)
            return original(item)
        monkeypatch.setattr(store,'require_retained',delayed)
        reading=asyncio.create_task(read_overlay(built,result.id,result.session,0))
        assert await asyncio.to_thread(entered.wait,HANG_S)
        try:
            caller=Client()
            # Correction itself checks files in a thread; release only its admission, retaining overlay wait.
            monkeypatch.setattr(store,'require_retained',original)
            if change=='correction':
                await handle(built,caller,{'type':'population-correct','session':result.session,
                    'population':result.id,'revision':1,'change':'undo'})
                assert caller.replies[-1]['ok'];await saved(result)
            elif change=='session':
                await handle(built,caller,{'type':'session','action':'new','name':'Other'})
            elif change=='replacement':
                store.items[result.id]=copy.copy(result)
            else:reading.cancel()
        finally:release.set()
        with pytest.raises(asyncio.CancelledError if change=='cancel' else OverlayRefused):await reading
        if change=='correction':assert result.revision==2 and len(result.live())==3
    asyncio.run(go())
