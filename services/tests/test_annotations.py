"""User observations stay attached to retained evidence, not a newly guessed live object."""
import asyncio
from pathlib import Path

import pytest

from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.day import DayStore
from orchestrator.evidence import Evidence
from orchestrator.resources import Index
from orchestrator.ticket import issue, using
from tests.test_population import cells, wait_saved, world  # noqa: F401


def setup(world, tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    world.evidence = Evidence(world, tmp_path / 'evidence', day=day)
    population, _ = cells(world)
    wait_saved(population)
    world.resources = Index(world, day=day)
    registry = build(Services(hub=world, day=day))
    return day, population, registry


def invoke(registry, day, args, *, asked=None):
    async def go():
        r = registry.start('annotate_resource', args, ToolContext('chat', 1, None, 'Keep my tentative observation',
                                                                asked=asked or day.here()))
        if r.task is not None:
            await r.task
        return r
    return asyncio.run(go())


def test_tentative_member_note_and_display_name_survive_restart_without_renaming_cells(world, tmp_path):
    day, population, registry = setup(world, tmp_path)
    member = population.live()[0]
    result = invoke(registry, day, {'target': member.id, 'title': 'Candidate Delta', 'text': 'It might be dividing.'})
    assert result.ok, result.summary
    [entry] = world.resources.list(kind='annotation')
    assert entry.source['population'] == population.id and entry.source['member'] == member.id
    assert entry.source['revision'] == 0 and entry.source['day'] == day.day
    assert 'tentative' in entry.summary.lower() and 'It might be dividing.' in world.resources.read(entry.id)
    assert not world.scene.objects
    before = population.wire()
    result = invoke(registry, day, {'id': entry.id, 'title': 'Candidate Amber', 'text': 'Could be two overlapping cells.'})
    assert result.ok and entry.id in result.summary
    restarted = DayStore(root=day.root, day=day.day)
    read = Index(world, day=restarted).read(entry.id)
    assert 'Candidate Amber' in read and 'Could be two overlapping cells.' in read
    assert 'It might be dividing.' in read  # revision history remains accessible
    assert population.wire() == before
    assert Index(world, day=restarted).get(entry.id).source == entry.source


@pytest.mark.parametrize('boundary', ['cancel', 'session'])
def test_stale_annotation_cannot_land_in_another_session_or_after_cancellation(world, tmp_path, boundary):
    day, population, registry = setup(world, tmp_path)
    asked = day.here()
    request = issue(world, 'turn', asked=asked)
    try:
        if boundary == 'cancel':
            request.cancel('never mind')
        else:
            day.new()  # another session opened before it was saved (ADR 0017)
        with using(request):
            result = invoke(registry, day, {'target': population.live()[0].id, 'text': 'possibly splitting'}, asked=asked)
        assert not result.ok and not world.resources.list(kind='annotation')
    finally:
        request.close()


def test_failed_journal_write_is_not_reported_as_a_saved_note(world, tmp_path, monkeypatch):
    day, population, registry = setup(world, tmp_path)
    import os
    original = os.fdopen
    failed = []
    inode = day.journal.path.stat().st_ino

    class FullDisk:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def tell(self):
            return self.stream.tell()
        def write(self, value):
            failed.append(value)
            raise OSError('disk full')

    def fail_journal(fd, *args, **kw):
        stream = original(fd, *args, **kw)
        return FullDisk(stream) if os.fstat(fd).st_ino == inode else stream

    monkeypatch.setattr(os, 'fdopen', fail_journal)
    result = invoke(registry, day, {'target': population.live()[0].id, 'text': 'possibly splitting'})
    assert failed and not day.annotations
    assert not result.ok and not world.resources.list(kind='annotation')


def test_annotation_refuses_unresolved_target_instead_of_using_selected_live_outline(world, tmp_path):
    day, population, registry = setup(world, tmp_path)
    result = invoke(registry, day, {'target': 'the longer one', 'text': 'a possible division'})
    assert not result.ok and not world.resources.list(kind='annotation')


def test_population_note_requires_persisted_source(world, tmp_path):
    day = DayStore(root=tmp_path/'sessions')
    world.evidence = Evidence(world, None, day=day)
    population, _ = cells(world)
    world.resources = Index(world, day=day)
    registry = build(Services(hub=world,day=day))
    assert population.status == 'unsaved'
    result = invoke(registry,day,{'target':population.id,'text':'A tentative possibility'})
    assert result.ok is False and 'not saved' in result.summary
    assert not day.annotations


def test_existing_note_requires_explicit_edit_or_separate_creation(world,tmp_path):
    day,population,registry=setup(world,tmp_path)
    target=population.live()[0].id
    first=invoke(registry,day,{'target':target,'title':'Candidate Delta','text':'Possibly dividing.'})
    aid=first.resource['id']
    ambiguous=invoke(registry,day,{'target':target,'text':'Too blurred to tell.'})
    assert not ambiguous.ok and aid in ambiguous.summary
    assert len(day.annotations)==1 and len(day.annotations[aid])==1
    edited=invoke(registry,day,{'id':aid,'text':'Too blurred to tell.'})
    assert edited.ok and edited.resource['version']==2
    assert day.annotations[aid][-1]['title']=='Candidate Delta'
    assert 'Possibly dividing.' in world.resources.read(aid)
    separate=invoke(registry,day,{'target':target,'separate':True,'text':'A separate observation.'})
    assert separate.ok and separate.resource['id']!=aid and len(day.annotations)==2
    assert 'new note' in separate.summary and 'no existing note was edited' in separate.summary
