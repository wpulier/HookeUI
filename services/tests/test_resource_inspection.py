"""Retained-picture inspection uses exact pixels, within the existing answer budget."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

from orchestrator.brain.registry import ACTIVITY_KEYS, ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.resources import Index, Results
from tests.test_brain import make
from tests.test_resources import jpeg
from tests.waiting import HANG_S


def prepared(tmp_path):
    brain, hub, agent = make([])
    saved = Results(tmp_path/'results')
    entry = saved.add('compare', {}, 'Saved dark picture', picture_bytes=[jpeg(40)])
    hub.resources = Index(hub, results=saved)
    brain.registry = build(Services(hub=hub))
    return brain, hub, agent, entry


def test_inspection_delivers_saved_pixels_with_identity_not_live_view(tmp_path):
    brain, hub, agent, entry = prepared(tmp_path)
    hub.settings.fast = 'off'
    agent.replies = [{'answer':'Reading the saved picture.', 'actions':[
        {'tool':'resources','args':{'id':entry['id'],'inspect':True}}]}]
    received = []
    class Model:
        async def ask(self, images, prompt, **kwargs):
            from orchestrator.ticket import current
            data = {'answer':'The retained picture is dark.', 'actions':[]}
            ticket = current()
            received.append(SimpleNamespace(images=tuple(images), prompt=prompt,
                ticket=ticket, attempt=ticket.admission.attempts[-1], reply_data=copy.deepcopy(data)))
            return SimpleNamespace(data=data,latency_ms=1)
    agent.fast_model = Model()
    result = asyncio.run(brain.turn('What is visible in my saved comparison?'))
    assert result['calls'] == 2 and len(agent.calls) == 1
    assert len(received) == 1 and received[0].attempt.phase == 'query'
    assert received[0].reply_data == {'answer': 'The retained picture is dark.', 'actions': []}
    assert received[0].ticket.admission.used == 2
    assert [attempt.status for attempt in received[0].ticket.admission.attempts] == ['done', 'done']
    import cv2
    import numpy as np
    for sent in received:
        images, prompt = sent.images, sent.prompt
        assert len(images) == 1
        assert cv2.imdecode(np.frombuffer(images[0],np.uint8),cv2.IMREAD_COLOR).mean() < 45
        assert entry['id'] in prompt and hashlib.sha256(jpeg(40)).hexdigest() in prompt
    receipt = result['actions'][0]
    facts = decoded_answer_facts(received[0])
    assert facts['operations'] == [{k: v for k, v in receipt.items() if k not in ACTIVITY_KEYS}]
    assert facts['inputs'] == {'legacyCameraCropSupplied': False, 'images': receipt['inspected']}
    assert facts['targets'] == {'requested': 0, 'outlined': 0}
    assert facts['limitations'] == [] and facts['omittedOperations'] == facts['omittedFacts'] == 0
    assert receipt['inspected'][0]['id'] == entry['id']
    assert receipt['inspected'][0]['sha256'] == hashlib.sha256(jpeg(40)).hexdigest()
    assert result['answer'] == 'The retained picture is dark.'


def test_missing_picture_never_substitutes_live_pixels(tmp_path):
    brain, hub, agent, entry = prepared(tmp_path)
    async def go():
        run=brain.registry.start('resources', {'id':entry['id'],'inspect':True,'picture':9},ToolContext('chat',1,None))
        await run.task
        return run
    result=asyncio.run(go())
    assert result.ok is False and not result.images
    assert 'picture' in result.summary


def test_inspections_are_bounded_across_one_request(tmp_path):
    brain, hub, agent, entry = prepared(tmp_path)
    async def go():
        ctx=ToolContext('chat',1,None)
        runs=[]
        for _ in range(3):
            run=brain.registry.start('resources', {'id':entry['id'],'inspect':True},ctx)
            await run.task
            runs.append(run)
        return runs
    runs=asyncio.run(go())
    assert [r.ok for r in runs] == [True,True,False]
    assert 'two' in runs[-1].summary and not runs[-1].images


def test_retained_pixels_keep_their_memory_lease(tmp_path):
    import gc
    from orchestrator.capture import captures
    from orchestrator.inspection import inspect_picture
    _,hub,_,entry=prepared(tmp_path)
    memory=captures(hub).memory
    image=inspect_picture(hub,hub.resources,entry['id'],0)
    assert memory.reserved['analysis']==len(image.jpeg)>0
    del image
    gc.collect()
    assert memory.reserved['analysis']==0


def test_delayed_inspection_passes_pixels_and_identity_to_late_answer(tmp_path,monkeypatch):
    import threading
    from pathlib import Path
    brain,hub,agent,entry=prepared(tmp_path)
    hub.settings.fast='off'
    release=threading.Event()
    opened=threading.Event()
    original=Path.open
    def held(path,*args,**kwargs):
        if path.suffix=='.jpg' and args and args[0]=='rb':
            opened.set()
            assert release.wait(HANG_S)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',held)
    brain.registry.tools['resources'].inline_ms=10
    agent.replies=[{'answer':'Reading the original.', 'actions':[{'tool':'resources','args':{'id':entry['id'],'inspect':True}}]}]
    received=[]
    class Model:
        async def ask(self,images,prompt,**kwargs):
            received.append((images,prompt))
            return SimpleNamespace(data={'answer':'The saved picture is dark.'},latency_ms=1)
    agent.fast_model=Model()
    async def go():
        answered=asyncio.Event()
        async def report(ctx,run):
            if run.answer:answered.set()
        brain.report_late=report
        result=await brain.turn('Inspect the first saved picture')
        assert result['calls']==1 and opened.is_set()
        release.set()
        await asyncio.wait_for(answered.wait(),HANG_S)
        assert len(received)==1 and len(received[0][0])==1
        assert entry['id'] in received[0][1]
        assert entry['id'] in brain.conversation.text()
    try:asyncio.run(go())
    finally:release.set()


def test_day_change_during_saved_read_does_not_attach_new_day_pixels(tmp_path,monkeypatch):
    import threading
    from pathlib import Path
    from orchestrator.day import DayStore
    brain,hub,agent,entry=prepared(tmp_path)
    day=DayStore(root=tmp_path/'sessions')
    registry=build(Services(hub=hub,day=day))
    release,opened=threading.Event(),threading.Event()
    original=Path.open
    def held(path,*args,**kwargs):
        if path.suffix=='.jpg' and args and args[0]=='rb':
            opened.set()
            assert release.wait(HANG_S)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',held)
    async def go():
        run=registry.start('resources',{'id':entry['id'],'inspect':True},ToolContext('chat',1,None,asked=day.here()))
        assert await asyncio.to_thread(opened.wait,HANG_S)
        day.new()  # another session (ADR 0017)
        release.set()
        await run.task
        assert run.ok is False and not run.images and 'session changed' in run.summary
    try:asyncio.run(go())
    finally:release.set()


import pytest

@pytest.mark.parametrize('boundary',['cancel','day'])
@pytest.mark.parametrize('round_number',[1,2])
def test_ownership_change_while_image_model_runs_drops_stale_answer(tmp_path,boundary,round_number):
    from orchestrator.ticket import cancel_open, current, generations
    from orchestrator.brain.registry import ToolFailed
    import time
    brain,hub,agent,entry=prepared(tmp_path)
    hub.settings.fast='off'
    agent.replies=[{'answer':'Reading.', 'actions':[{'tool':'resources','args':{'id':entry['id'],'inspect':True}}]}]
    async def go():
        held,release=asyncio.Event(),asyncio.Event()
        calls=[]
        async def ask(images,prompt,**kwargs):
            ticket = current()
            sent = SimpleNamespace(images=tuple(images), prompt=prompt, ticket=ticket,
                                   attempt=ticket.admission.attempts[-1], reply_data=None)
            calls.append(sent)
            if len(calls)==round_number:
                held.set()
                await release.wait()
            # Round two needs a genuinely unavailable first interpretation;
            # a valid completed query now answers without another model call.
            data = {'answer': 'Stale saved-image answer.', 'actions': []}
            if round_number == 2 and len(calls) == 1:
                data.update(answer='', observation_scope='retained-source', input_refs=[1])
            sent.reply_data = copy.deepcopy(data)
            return SimpleNamespace(data=data,latency_ms=1)
        agent.fast_model=SimpleNamespace(ask=ask)
        task=asyncio.create_task(brain.turn('Describe my exact retained picture'))
        await asyncio.wait_for(held.wait(),HANG_S)
        owner = calls[0].ticket
        assert all(sent.ticket is owner and sent.images == calls[0].images for sent in calls)
        assert len(calls[0].images) == 1
        for sent in calls:
            facts = decoded_answer_facts(sent)
            assert facts['operations'][0]['resource']['id'] == entry['id']
            assert facts['inputs']['images'][0]['sha256'] == hashlib.sha256(jpeg(40)).hexdigest()
            assert facts['inputs']['images'][0]['inputSha256'] == hashlib.sha256(sent.images[0]).hexdigest()
            assert facts['omittedOperations'] == facts['omittedFacts'] == 0
        if round_number == 2:
            assert calls[0].reply_data == {'answer': '', 'actions': [],
                'observation_scope': 'retained-source', 'input_refs': [1]}
            assert [attempt.phase for attempt in owner.admission.attempts] == ['look', 'query', 'final']
            assert [attempt.status for attempt in owner.admission.attempts] == ['done', 'done', 'running']
        else:
            assert [attempt.phase for attempt in owner.admission.attempts] == ['look', 'query']
            assert [attempt.status for attempt in owner.admission.attempts] == ['done', 'running']
        if boundary=='cancel':cancel_open('chat',time.monotonic()+1,'never mind')
        else:generations(hub).bump('session',why='new day')
        release.set()
        with pytest.raises(ToolFailed):await task
        assert len(calls)==round_number
        assert calls[-1].reply_data == {'answer': 'Stale saved-image answer.', 'actions': []}
        assert owner.admission.used == round_number + 1
        assert all(attempt.status == 'done' for attempt in owner.admission.attempts)
        assert not any(t.role=='assistant' for t in brain.conversation.turns)
    asyncio.run(go())


def test_concurrent_retained_inputs_cannot_exceed_existing_allowance(tmp_path):
    from orchestrator.capture import captures, Refused
    from orchestrator.inspection import inspect_picture
    _,hub,_,entry=prepared(tmp_path)
    memory=captures(hub).memory
    first=inspect_picture(hub,hub.resources,entry['id'],0)
    memory.allowances['analysis']=memory.peak['analysis']
    with pytest.raises(Refused):inspect_picture(hub,hub.resources,entry['id'],0)
    assert memory.reserved['analysis']==len(first.jpeg)


@pytest.mark.parametrize('provider',['population','motion','bookmark'])
def test_retained_file_providers_obey_read_limit_before_full_allocation(tmp_path,provider,monkeypatch):
    from pathlib import Path
    from orchestrator.evidence import Evidence
    from orchestrator.motiontracks import TracksStore
    from orchestrator.bookmarks import Bookmarks
    from tests.fakes import Hub
    hub=Hub()
    folder=tmp_path/'2026-10-08'
    folder.mkdir()
    name={'population':'capture.jpg','motion':'tracks.jpg','bookmark':'b1.jpg'}[provider]
    (folder/name).write_bytes(b'x'*4096)
    reads=[]
    original=Path.open
    class File:
        def __init__(self,handle):self.handle=handle
        def __enter__(self):return self
        def __exit__(self,*args):self.handle.close()
        def read(self,n=-1):
            reads.append(n)
            return self.handle.read(n)
    def observed(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        return File(result) if path==folder/name else result
    monkeypatch.setattr(Path,'open',observed)
    if provider=='population':
        result=Evidence(hub,None).capture_jpeg(SimpleNamespace(capture=None,path=folder),max_bytes=1024)
    elif provider=='motion':
        result=TracksStore(hub,None).picture(SimpleNamespace(picture=None,path=folder),max_bytes=1024)
    else:
        result=Bookmarks(tmp_path).picture('b1','2026-10-08',max_bytes=1024)
    assert result is None and reads==[1025]


def test_fast_pass_and_look_share_the_same_two_picture_allowance(tmp_path):
    brain,hub,agent,entry=prepared(tmp_path)
    brain.services=Services(hub=hub)
    action={'tool':'resources','args':{'id':entry['id'],'inspect':True}}
    agent.replies=[{'answer':'Working with the retained images.', 'actions':[]}]
    image_calls=[]
    class Model:
        async def ask(self,images,prompt,**kwargs):
            if not images:
                return SimpleNamespace(data={'intent':'request','needs_look':True,'answer':'Reading.',
                                             'actions':[action,action]},latency_ms=1)
            image_calls.append(images)
            return SimpleNamespace(data={'answer':'Reading another.', 'actions':[action] if len(image_calls)==1 else []},latency_ms=1)
    agent.fast_model=Model()
    result=asyncio.run(brain.turn('Inspect the saved images and the current view'))
    assert [a['ok'] for a in result['actions']]==[True,True,False]
    assert 'two' in result['actions'][-1]['summary']
    assert len(image_calls)==2 and all(len(images)==2 for images in image_calls)


def test_saved_member_is_a_shared_readable_image_source_without_new_outline(world,tmp_path):
    from tests.test_annotations import setup
    from orchestrator.inspection import inspect_picture
    from tests.test_screen import setup as studio
    from orchestrator.magnifier import Magnifier
    day,population,registry=setup(world,tmp_path)
    member=population.live()[0]
    index=world.resources
    entry=index.get(member.id)
    assert entry is not None and entry.kind=='member'
    assert entry.source['population']==population.id and entry.source['member']==member.id
    assert member.id in index.read(member.id) and 'segmentation' in index.read(member.id)
    assert not index.list(kind='member')  # members stay inside their population, not a duplicate global inventory
    picture=inspect_picture(world,index,member.id,0)
    assert picture.facts['source']['member']==member.id
    assert not world.scene.objects
    magnifier=Magnifier(world)
    studio(world)
    registry=build(Services(hub=world,day=day,magnifier=magnifier))
    async def go():
        result=registry.start('present',{'what':member.id,'do':'open'},ToolContext('chat',1,None,asked=day.here()))
        await result.task
        return result
    result=asyncio.run(go())
    assert result.ok and result.resource['id']==member.id
    assert not world.scene.objects
    assert magnifier.picture_now()['resource']['id']==member.id


from tests.test_population import world  # noqa: F401


def test_no_action_request_uses_remaining_calls_to_inspect_instead_of_only_disclaiming(tmp_path):
    brain,hub,agent,entry=prepared(tmp_path)
    brain.services=Services(hub=hub)
    agent.replies=[{'answer':'The live view is unrelated.', 'actions':[]}]
    calls=[]
    class Model:
        async def ask(self,images,prompt,**kwargs):
            calls.append((images,prompt))
            if len(calls)==1:
                data={'intent':'request','needs_look':False,'answer':'I have not seen that picture.', 'actions':[]}
            elif len(calls)==2:
                data={'answer':'Inspecting the retained picture.', 'actions':[{'tool':'resources','args':{'id':entry['id'],'inspect':True}}]}
            else:
                assert images and entry['id'] in prompt
                data={'answer':'The saved picture is dark; it does not establish division.', 'actions':[]}
            return SimpleNamespace(data=data,latency_ms=1)
    agent.fast_model=Model()
    result=asyncio.run(brain.turn('Look at my saved candidate and describe the evidence'))
    assert result['calls']==3 and len(calls)==3
    assert result['actions'][0]['ok'] and result['actions'][0]['inspected']
    assert result['answer']=='The saved picture is dark; it does not establish division.'


def test_last_answer_after_completed_inspection_keeps_grounded_description(tmp_path):
    brain,hub,agent,entry=prepared(tmp_path)
    hub.settings.fast='off'
    agent.replies=[{'answer':'Reading its record.', 'actions':[{'tool':'resources','args':{'id':entry['id']}}]},
                   {'answer':'Inspecting its pixels.', 'actions':[{'tool':'resources','args':{'id':entry['id'],'inspect':True}}]}]
    calls=[]
    class Model:
        async def ask(self,images,prompt,**kwargs):
            calls.append(images)
            assert images and entry['id'] in prompt
            return SimpleNamespace(data={'answer':'The saved image is dark, without a visible division.', 'actions':[]},latency_ms=1)
    agent.fast_model=Model()
    result=asyncio.run(brain.turn('Describe the earlier saved picture'))
    assert result['calls']==3 and len(calls)==1
    assert result['answer']=='The saved image is dark, without a visible division.'


@pytest.mark.parametrize('boundary',['cancel','day'])
@pytest.mark.parametrize('round_number',[1,2])
def test_no_action_continuation_drops_cancelled_or_old_day_request(tmp_path,boundary,round_number):
    from orchestrator.ticket import cancel_open, generations
    from orchestrator.brain.registry import ToolFailed
    import time
    brain,hub,agent,entry=prepared(tmp_path)
    brain.services=Services(hub=hub)
    agent.replies=[{'answer':'The live view is unrelated.', 'actions':[]}]
    async def go():
        held,release=asyncio.Event(),asyncio.Event()
        calls=[]
        async def ask(images,prompt,**kwargs):
            calls.append(prompt)
            if len(calls)==round_number:
                held.set()
                await release.wait()
            return SimpleNamespace(data={'intent':'request','needs_look':False,'answer':'Reading the retained resource.',
                                         'actions':[] if len(calls)==1 else [{'tool':'resources','args':{'id':entry['id'],'inspect':True}}]},latency_ms=1)
        agent.fast_model=SimpleNamespace(ask=ask)
        task=asyncio.create_task(brain.turn('Inspect my saved candidate'))
        await asyncio.wait_for(held.wait(),HANG_S)
        if boundary=='cancel':cancel_open('chat',time.monotonic()+1,'never mind')
        else:generations(hub).bump('session',why='new day')
        release.set()
        with pytest.raises(ToolFailed):await task
        assert len(calls)==round_number
        assert not any(t.role=='assistant' for t in brain.conversation.turns)
    asyncio.run(go())


def test_empty_last_inspection_answer_cannot_keep_preinspection_claim(tmp_path):
    from orchestrator.ticket import current
    brain,hub,agent,entry=prepared(tmp_path)
    hub.settings.fast='off'
    agent.replies=[{'answer':'Reading its record.', 'actions':[{'tool':'resources','args':{'id':entry['id']}}]},
                   {'answer':'It is definitely dividing.', 'actions':[{'tool':'resources','args':{'id':entry['id'],'inspect':True}}]}]
    received=[]
    async def ask(images,prompt,**kwargs):
        assert images
        received.append((tuple(images),prompt,current()))
        return SimpleNamespace(data={},latency_ms=1)
    agent.fast_model=SimpleNamespace(ask=ask)
    result=asyncio.run(brain.turn('Inspect the saved candidate'))
    assert result['calls']==3
    assert 'definitely dividing' not in result['answer']
    assert len(received)==1 and len(received[0][0])==1
    images,prompt,ticket=received[0]
    assert entry['id'] in prompt and hashlib.sha256(images[0]).hexdigest() in prompt
    budget=ticket.admission
    assert budget.used==result['calls']==3 and budget.attempts[-1].status=='done'
    assert all(a['requestId']==budget.request_id for a in result['actions'])
    assert len({a['actionId'] for a in result['actions']})==2
    inspected=result['actions'][-1]['inspected'][0]
    assert inspected['id']==entry['id'] and inspected['sha256']==hashlib.sha256(jpeg(40)).hexdigest()
    assert inspected['inputSha256']==hashlib.sha256(images[0]).hexdigest()
    assert result['answer']=='' and result['answerStatus']=='not-published'
    assert not any(t.role=='assistant' for t in brain.conversation.turns)


@pytest.mark.parametrize('boundary',['cancel','day'])
def test_cancel_during_continuation_resource_read_prevents_confirmation(tmp_path,monkeypatch,boundary):
    import threading,time
    from pathlib import Path
    from orchestrator.ticket import cancel_open,generations
    from orchestrator.brain.registry import ToolFailed
    brain,hub,agent,entry=prepared(tmp_path)
    brain.services=Services(hub=hub)
    entry=hub.resources.results.add('measure',{},'Saved measurement',values={'length':12})
    agent.replies=[{'answer':'The live view is unrelated.', 'actions':[]}]
    held,release=threading.Event(),threading.Event()
    original=Path.open
    def opened(path,*args,**kwargs):
        if path.name==entry['id']+'.json':
            held.set()
            assert release.wait(HANG_S)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)
    calls=[]
    async def ask(images,prompt,**kwargs):
        calls.append(prompt)
        return SimpleNamespace(data={'intent':'request','needs_look':False,'answer':'Reading the saved measurement.',
                                     'actions':[] if len(calls)==1 else [{'tool':'resources','args':{'id':entry['id']}}]},latency_ms=1)
    agent.fast_model=SimpleNamespace(ask=ask)
    async def go():
        task=asyncio.create_task(brain.turn('Read my saved measurement'))
        assert await asyncio.to_thread(held.wait,HANG_S)
        if boundary=='cancel':cancel_open('chat',time.monotonic()+1,'never mind')
        else:generations(hub).bump('session',why='new day')
        release.set()
        with pytest.raises(ToolFailed):await task
        assert len(calls)==2
        assert not any(t.role=='assistant' for t in brain.conversation.turns)
    try:asyncio.run(go())
    finally:release.set()


def test_no_action_continuation_checks_effect_only_receipt_before_claiming_source(tmp_path):
    from orchestrator.magnifier import Magnifier
    from tests.test_screen import setup
    brain,hub,agent,entry=prepared(tmp_path)
    magnifier=Magnifier(hub)
    setup(hub)
    brain.services=Services(hub=hub,magnifier=magnifier)
    brain.registry=build(brain.services)
    agent.replies=[{'answer':'Unrelated live view.', 'actions':[]}]
    calls=[]
    async def ask(images,prompt,**kwargs):
        calls.append(prompt)
        if len(calls)==1:
            data={'intent':'request','needs_look':False,'answer':'Opening the white picture.', 'actions':[]}
        elif len(calls)==2:
            data={'answer':'The white picture is displayed.', 'actions':[{'tool':'present','args':{'what':entry['id'],'do':'open'}}]}
        else:
            assert entry['id'] in prompt and entry['title'] in prompt
            data={'answer':'The saved comparison was opened; the requested white picture was not.'}
        return SimpleNamespace(data=data,latency_ms=1)
    agent.fast_model=SimpleNamespace(ask=ask)
    result=asyncio.run(brain.turn('Show the saved white picture'))
    assert result['calls']==3 and len(calls)==3
    assert 'was not' in result['answer']
    assert magnifier.picture_now()['resource']['id']==entry['id']


def test_direct_saved_artifact_action_checks_actual_identity_before_final_answer(tmp_path):
    from orchestrator.magnifier import Magnifier
    from tests.test_screen import setup
    brain,hub,agent,entry=prepared(tmp_path)
    magnifier=Magnifier(hub)
    setup(hub)
    brain.services=Services(hub=hub,magnifier=magnifier)
    brain.registry=build(brain.services)
    agent.replies=[{'answer':'Unrelated live view.', 'actions':[]}]
    calls=[]
    async def ask(images,prompt,**kwargs):
        calls.append(prompt)
        if len(calls)==1:
            data={'intent':'request','needs_look':False,'answer':'The white picture is displayed.',
                  'actions':[{'tool':'present','args':{'what':entry['id'],'do':'open'}}]}
        else:
            assert entry['id'] in prompt and entry['title'] in prompt
            data={'answer':'The saved comparison was opened; the requested white picture was not.'}
        return SimpleNamespace(data=data,latency_ms=1)
    agent.fast_model=SimpleNamespace(ask=ask)
    result=asyncio.run(brain.turn('Show the saved white picture'))
    assert result['calls']==2 and len(calls)==2
    assert 'was not' in result['answer']
    assert magnifier.picture_now()['resource']['id']==entry['id']


def test_empty_resource_search_says_no_match_and_does_not_imply_a_saved_result(tmp_path):
    brain,hub,agent,entry=prepared(tmp_path)
    async def go():
        run=brain.registry.start('resources',{'kind':'result','query':'never-matched'},ToolContext('chat',1,None))
        await run.task
        return run
    result=asyncio.run(go())
    assert result.ok and result.summary.startswith('no measurements and pictures')
    assert not result.resource


def test_resource_order_outside_declared_schema_is_refused(tmp_path):
    brain,hub,agent,entry=prepared(tmp_path)
    async def go():
        run=brain.registry.start('resources',{'kind':'result','order':'first'},ToolContext('chat',1,None))
        await run.task
        return run
    result=asyncio.run(go())
    assert result.ok is False and 'oldest' in result.summary and 'newest' in result.summary


def test_oldest_resource_page_describes_omitted_entries_as_later(tmp_path):
    brain,hub,agent,entry=prepared(tmp_path)
    for i in range(12):hub.resources.results.add('measure',{},f'Measurement {i}')
    async def go():
        run=brain.registry.start('resources',{'kind':'result','order':'oldest'},ToolContext('chat',1,None))
        await run.task
        return run
    result=asyncio.run(go())
    assert result.ok and '(and 1 later)' in result.summary


def research_picture_fixture(tmp_path):
    """Real retained outputs and manager version events; no researcher worker is started."""
    from orchestrator.research import ResearchManager, Session

    brain, hub, _ = make([])
    day = '2026-10-08'
    root = tmp_path / 'research'
    manager = ResearchManager(hub, root=root, today=lambda: day)
    session = Session(rid='r123456', n=1, brief='Keep this picture', deliverable='image',
                      effort='standard', engine='gpt-6.1-sol', purpose='research', label='Saved image',
                      object_id=None, origin={}, day=day, status='done', root=root)
    manager.sessions[session.rid] = session
    folder = root / day / session.rid / 'outputs'
    folder.mkdir(parents=True)
    original, replacement = folder / 'original.jpg', folder / 'replacement.jpg'
    original.write_bytes(jpeg(40))
    replacement.write_bytes(jpeg(200))
    manager._event({'rid': session.rid, 'event': 'output', 'kind': 'image',
                    'path': 'outputs/original.jpg', 'caption': 'Retained picture'})
    hub.resources = Index(hub, research=manager, day=SimpleNamespace(day=day, sid=day, date=day))
    brain.registry = build(Services(hub=hub))
    return brain, hub, manager, session, original, replacement


def test_research_output_replaced_during_inspection_is_not_attached(tmp_path, monkeypatch):
    """Ordinal zero must not silently change from o1/version 1 to o2/version 2 during a read."""
    import threading
    from pathlib import Path
    from orchestrator.capture import captures

    brain, hub, manager, session, original, replacement = research_picture_fixture(tmp_path)
    opened, release = threading.Event(), threading.Event()
    real_open = Path.open

    def held(path, *args, **kwargs):
        if path == original and args and args[0] == 'rb':
            opened.set()
            if not release.wait(5):
                raise TimeoutError('research inspection test barrier was not released')
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', held)

    async def go():
        run = brain.registry.start('resources', {'id': session.rid, 'inspect': True, 'picture': 0},
                                   ToolContext('chat', 1, None))
        try:
            assert await asyncio.to_thread(opened.wait, HANG_S), 'inspection did not reach the retained file'
            manager._event({'rid': session.rid, 'event': 'output', 'kind': 'image',
                            'path': 'outputs/replacement.jpg', 'caption': 'Retained picture',
                            'replaces': 'o1', 'change': 'Use the revised picture'})
        finally:
            release.set()
            await run.task
        assert run.ok is False, 'changed researcher output was accepted as the original inspected source'
        assert not run.images and run.resource is None
        assert captures(hub).memory.reserved['analysis'] == 0
        assert session.output('o1')['version'] == 1
        assert session.output('o2')['version'] == 2
        assert session.output('o2')['replaces'] == 'o1'
        assert [o['id'] for o in session.current_outputs()] == ['o2']
        assert original.read_bytes() == jpeg(40)
        assert replacement.read_bytes() == jpeg(200)

    try:
        asyncio.run(go())
    finally:
        release.set()


def test_unchanged_research_output_inspection_still_attaches_actual_pixels(tmp_path):
    """Refusing all research images is not an acceptable fix for replacement races."""
    brain, hub, _, session, original, _ = research_picture_fixture(tmp_path)

    async def go():
        run = brain.registry.start('resources', {'id': session.rid, 'inspect': True, 'picture': 0},
                                   ToolContext('chat', 1, None))
        await run.task
        return run

    run = asyncio.run(go())
    assert run.ok is True, run.summary
    assert len(run.images) == 1
    assert run.images[0].facts['sha256'] == hashlib.sha256(jpeg(40)).hexdigest()
    assert run.images[0].facts['id'] == 'r123456'
    assert run.images[0].facts['day'] == '2026-10-08'
    assert run.resource['id'] == 'r123456'
    assert original.read_bytes() == jpeg(40)


@pytest.mark.parametrize('revised', [False, True], ids=['original-o1-v1', 'revised-o2-v2'])
def test_research_inspection_identifies_the_actual_output_version(tmp_path, revised):
    """Inspected facts bind the file's immutable output ID/version, not just its researcher ordinal."""
    from orchestrator.inspection import inspect_picture

    _, hub, manager, session, original, replacement = research_picture_fixture(tmp_path)
    if revised:
        manager._event({'rid': session.rid, 'event': 'output', 'kind': 'image',
                        'path': 'outputs/replacement.jpg', 'caption': 'Retained picture',
                        'replaces': 'o1', 'change': 'Use the revised picture'})
    image = inspect_picture(hub, hub.resources, session.rid, 0, session.day)
    expected_id, expected_version, expected_path, expected_bytes = (
        ('o2', 2, 'outputs/replacement.jpg', jpeg(200)) if revised else
        ('o1', 1, 'outputs/original.jpg', jpeg(40)))
    assert image.facts['sha256'] == hashlib.sha256(expected_bytes).hexdigest()
    assert image.facts['source']['researcher'] == 'r123456'
    assert image.facts['source']['output'] == expected_id
    assert image.facts['source']['version'] == expected_version
    assert image.facts['source']['path'] == expected_path
    # A later version never deletes or rewrites the earlier output or its bytes.
    assert session.output('o1')['version'] == 1
    assert original.read_bytes() == jpeg(40)
    assert replacement.read_bytes() == jpeg(200)


def test_research_inspection_binds_each_picture_to_its_own_output(tmp_path):
    """Selecting picture 1 must not borrow picture 0's identity or version."""
    from orchestrator.inspection import inspect_picture

    _, hub, manager, session, _, _ = research_picture_fixture(tmp_path)
    manager._event({'rid': session.rid, 'event': 'output', 'kind': 'image',
                    'path': 'outputs/replacement.jpg', 'caption': 'Retained picture'})
    image = inspect_picture(hub, hub.resources, session.rid, 1, session.day)
    assert image.facts['picture'] == 1
    assert image.facts['version'] == 1
    assert image.facts['source'] == {'researcher': 'r123456', 'output': 'o2',
                                     'version': 1, 'path': 'outputs/replacement.jpg'}
    assert image.facts['sha256'] == hashlib.sha256(jpeg(200)).hexdigest()
    assert session.output('o1')['version'] == 1


# TEST-ONLY actual-App scheduler regressions. Synthetic provider replies are not
# model quality evidence; these trace production input/acquisition/admission paths.
CURRENT_CAMERA_UNSUPPLIED = 'The current camera view was requested but is not supplied to this call.'
CURRENT_COMPARISON_CLAIM = 'I compared the saved pictures with the current camera view.'
CURRENT_PARTIAL_ANSWER = 'I inspected the saved pictures; I have not inspected the current camera view, so that comparison remains unmet.'


def decoded_answer_facts(sent):
    """Decode the full actual bounded prompt block; never call its producer."""
    opening = '<answer-facts source="app" untrusted="true">\n'
    assert sent.prompt.count(opening) == 1
    body = sent.prompt.split(opening, 1)[1].split('\n</answer-facts>', 1)[0]
    return json.loads(body)


def assert_supplied_answer_facts(sent, receipts, pictures=(), *, camera_required=False):
    facts = decoded_answer_facts(sent)
    # These successful acquisitions have no causal presentation outcome or
    # declared Goal; their compact rows retain the exact admitted identities.
    assert all('outcome' not in receipt and 'goal' not in receipt for receipt in receipts)
    assert facts == {
        'operationStatuses': [
            {**{key: receipt[key] for key in ('requestId', 'actionId', 'tool', 'ok') if key in receipt},
             'summary': receipt['summary'][:240]}
            for receipt in receipts
        ],
        'operations': receipts, 'omittedOperations': 0,
        'targets': {'requested': 0, 'outlined': 0},
        'inputs': {'legacyCameraCropSupplied': False, 'images': [picture.facts for picture in pictures]},
        'limitations': [CURRENT_CAMERA_UNSUPPLIED] if camera_required else [],
        'omittedFacts': 0,
    }


def actual_scheduler_fixture(tmp_path, monkeypatch, response, *, fast='gemini-3.5-flash'):
    from orchestrator.ticket import current
    from tests.test_workspace_request_observation import RequestRig

    rig = RequestRig(tmp_path, monkeypatch, fast=fast)
    sends, looks, started = [], [], []
    actual_look = rig.app.agent.look
    actual_start = rig.app.brain.registry.start

    async def external_provider(images, prompt, **kwargs):
        ticket = current()
        assert ticket is not None and ticket.admission is not None
        attempt = ticket.admission.attempts[-1]
        sent = SimpleNamespace(ticket=ticket, admission=ticket.admission, attempt=attempt,
                               phase=attempt.phase, images=tuple(images), prompt=prompt)
        sends.append(sent)
        try:
            data = response(sent)
        except BaseException as exc:
            sent.provider_error = exc
            raise
        sent.reply_data = copy.deepcopy(data)
        return SimpleNamespace(data=data, model='explicitly-canned-scheduler-provider', latency_ms=0.0, error=None)

    async def trace_actual_look(question, view, **kwargs):
        looks.append((current(), question, view))
        return await actual_look(question, view, **kwargs)

    def trace_actual_start(name, args, ctx, run=None):
        returned = actual_start(name, args, ctx, run=run)
        started.append((returned, ctx, current()))
        return returned

    monkeypatch.setattr(rig.provider, 'ask', external_provider)
    monkeypatch.setattr(rig.app.agent, 'look', trace_actual_look)
    monkeypatch.setattr(rig.app.brain.registry, 'start', trace_actual_start)
    return SimpleNamespace(rig=rig, sends=sends, looks=looks, started=started)


def assert_actual_scheduler_admission(traced, result):
    from orchestrator.brain.admission import MAX_CALLS

    sends = traced.sends
    assert sends and MAX_CALLS == 3
    ticket, budget = sends[0].ticket, sends[0].admission
    assert all(sent.ticket is ticket and sent.admission is budget for sent in sends)
    assert result['calls'] == budget.used == len(sends) <= MAX_CALLS
    assert len({sent.attempt.attempt_id for sent in sends}) == len(sends)
    assert [attempt.attempt_id for attempt in budget.attempts] == [sent.attempt.attempt_id for sent in sends]
    assert all(attempt.request_id == budget.request_id for attempt in budget.attempts)
    assert all(len(sent.images) <= 2 for sent in sends)
    for run, ctx, owner in traced.started:
        assert owner is ticket and ctx.admission is budget and ctx.request_id == budget.request_id
        assert run.occurrence.request_id == budget.request_id
    return ticket, budget


def test_actual_retained_acquisition_keeps_unsupplied_current_camera_obligation(tmp_path, monkeypatch):
    import json

    actions, interpretations = [], []

    def response(sent):
        if sent.phase == 'fast':
            return {'answer': 'I am inspecting the retained pictures.', 'intent': 'request',
                    'needs_look': True, 'actions': actions, 'observation_scope': 'none', 'input_refs': []}
        if sent.phase == 'look':
            return {'answer': 'The current camera view was inspected.', 'actions': [],
                    'observation_scope': 'specimen-camera', 'input_refs': ['camera']}
        interpretations.append(sent)
        if CURRENT_CAMERA_UNSUPPLIED in sent.prompt:
            answer = CURRENT_PARTIAL_ANSWER
        else:
            answer = CURRENT_COMPARISON_CLAIM  # deliberately unsupported scripted fulfillment
        return {'answer': answer, 'actions': [actions[0]] if sent.phase == 'query' else [],
                'observation_scope': 'retained-source', 'input_refs': [1, 2]}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    rig = traced.rig
    refs = [source['identity']['resource'] for source in rig.sources.values()]
    actions.extend({'tool': 'resources', 'args': {'id': ref['id'], 'inspect': True}}
                   for ref in refs)

    async def scenario():
        await rig.camera()
        await rig.ingest()
        result = await rig.send(text='Inspect the saved images and the current view')
        ticket, budget = assert_actual_scheduler_admission(traced, result)
        assert rig.app.hub.source.is_fresh(), 'BEHAVIOR: the camera is available; the missing input is not a feed outage'
        assert [sent.phase for sent in traced.sends] == ['fast', 'query', 'final'], 'MISSING_FEATURE: acquired retained pixels must be interpreted before any camera provider admission consumes the original three-call plan'
        assert not traced.looks, 'BEHAVIOR: an omitted camera look cannot be refunded or silently counted as supplied'
        assert [row['ok'] for row in result['actions']] == [True, True, False]
        assert 'two' in result['actions'][-1]['summary']
        reads = [(run, ctx) for run, ctx, _owner in traced.started if run.tool == 'resources']
        assert len(reads) == 3 and [run.ok for run, _ctx in reads] == [True, True, False]
        slots = reads[0][1].inspection_slots
        assert len(slots) == 2 and all(ctx.inspection_slots is slots for _run, ctx in reads)
        acquired = [image for run, _ctx in reads if run.ok for image in run.images]
        assert len(acquired) == 2 and not reads[-1][0].images
        assert len(interpretations) == 2 and [sent.phase for sent in interpretations] == ['query', 'final']
        for sent in interpretations:
            assert sent.images == tuple(image.jpeg for image in acquired)
            assert CURRENT_CAMERA_UNSUPPLIED in sent.prompt, 'MISSING_FEATURE: each retained-only interpretation must retain the original unsupplied current-camera obligation'
            assert 'Inspect the saved images and the current view' in sent.prompt
            for number, image in enumerate(acquired, 1):
                manifest = {'image': number, **image.facts}
                assert json.dumps(manifest, default=str) in sent.prompt  # actual image_context, not invented channel metadata
                assert image.facts['id'] == refs[number - 1]['id'] and image.facts['day'] == refs[number - 1]['day']
                assert image.facts['source']
                assert hashlib.sha256(sent.images[number - 1]).hexdigest() == image.facts['inputSha256']
        assert CURRENT_COMPARISON_CLAIM not in result['answer']
        assert result['answer'] == CURRENT_PARTIAL_ANSWER, 'MISSING_FEATURE: saved-only outcomes must retain the unmet current-view comparison'
        assert budget.used == 3 and all(attempt.status == 'done' for attempt in budget.attempts)
        assert all(sent.ticket is ticket for sent in interpretations)
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


def test_actual_ordinary_needs_look_without_retained_inputs_still_supplies_camera(tmp_path, monkeypatch):
    def response(sent):
        if sent.phase == 'fast':
            return {'answer': '', 'intent': 'question', 'needs_look': True, 'actions': [],
                    'observation_scope': 'none', 'input_refs': []}
        return {'answer': 'The supplied current camera crop is visible.', 'targets': [], 'actions': [],
                'observation_scope': 'specimen-camera', 'input_refs': ['camera']}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    rig = traced.rig
    async def scenario():
        await rig.camera()
        await rig.ingest()
        result = await rig.send(text='Describe the current view through the eyepiece.')
        ticket, budget = assert_actual_scheduler_admission(traced, result)
        fast = [sent for sent in traced.sends if sent.phase == 'fast']
        camera = [sent for sent in traced.sends if sent.phase == 'look']
        assert len(fast) == len(camera) == 1 and not fast[0].images and len(camera[0].images) == 1
        assert len(traced.looks) == 1 and traced.looks[0][0] is ticket
        info, _image, _crop = traced.looks[0][2]
        assert info.id == rig.app.hub.latest_info.id == 1 and info.seq == 1 and info.boot == 'synthetic-camera-provider'
        assert not traced.started and not result['actions']
        assert result['answer'] == 'The supplied current camera crop is visible.'
        assert budget.used == 2 and all(attempt.status == 'done' for attempt in budget.attempts)
        assert CURRENT_CAMERA_UNSUPPLIED not in camera[0].prompt
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


def test_actual_fast_off_and_failed_fast_camera_fallback_keep_spent_admission(tmp_path, monkeypatch):
    # Independent real-App scenarios; no failed provider attempt is refunded.
    for mode in ('off', 'error'):
        def response(sent):
            if sent.phase == 'fast':
                raise RuntimeError('Explicitly canned external fast-provider failure.')
            return {'answer': 'I can inspect only the supplied camera crop.', 'targets': [], 'actions': [],
                    'observation_scope': 'specimen-camera', 'input_refs': ['camera']}

        traced = actual_scheduler_fixture(tmp_path / mode, monkeypatch, response,
                                           fast='off' if mode == 'off' else 'gemini-3.5-flash')
        rig = traced.rig
        async def scenario():
            await rig.camera()
            await rig.ingest()
            result = await rig.send(text='What is in the current camera view?')
            ticket, budget = assert_actual_scheduler_admission(traced, result)
            camera = [sent for sent in traced.sends if sent.phase == 'look']
            fast = [sent for sent in traced.sends if sent.phase == 'fast']
            assert len(camera) == 1 and len(camera[0].images) == 1
            assert len(traced.looks) == 1 and traced.looks[0][0] is ticket
            info, _image, _crop = traced.looks[0][2]
            assert info.id == 1 and info.seq == 1 and info.boot == 'synthetic-camera-provider'
            assert not traced.started and not result['actions']
            assert result['answer'] == 'I can inspect only the supplied camera crop.'
            if mode == 'off':
                assert not fast and budget.used == 1 and budget.attempts[0].status == 'done'
            else:
                assert len(fast) == 1 and not fast[0].images and budget.used == 2
                assert next(attempt for attempt in budget.attempts if attempt.phase == 'fast').status == 'failed'
                assert next(attempt for attempt in budget.attempts if attempt.phase == 'look').status == 'done'
        try:
            asyncio.run(scenario())
        finally:
            rig.close()


@pytest.mark.parametrize('late_outcome', ['partial', 'failed', 'empty'])
def test_actual_deferred_retained_query_keeps_original_unsupplied_camera_obligation(tmp_path, monkeypatch, late_outcome):
    import copy
    import json
    import threading
    from pathlib import Path
    from tests.test_workspace_request_observation import OTHER

    actions, slow_record = [], {}
    delegation = 'synthetic-original-deferred-camera-delegation'

    def response(sent):
        if sent.phase == 'fast':
            return {'answer': 'I am inspecting the saved picture.', 'intent': 'request', 'needs_look': True,
                    'actions': actions, 'observation_scope': 'none', 'input_refs': []}
        if sent.phase == 'query':
            return {'answer': '', 'actions': [{'tool': 'resources', 'args': {'id': slow_record['id']}}],
                    'observation_scope': 'retained-source', 'input_refs': [1]}
        assert sent.phase == 'late', 'BEHAVIOR: the held acquired-input plan must not admit a camera or extra final send'
        if late_outcome == 'failed':
            raise RuntimeError('Explicitly canned entered late-provider failure.')
        answer = (CURRENT_PARTIAL_ANSWER if CURRENT_CAMERA_UNSUPPLIED in sent.prompt else CURRENT_COMPARISON_CLAIM)
        return {'answer': '' if late_outcome == 'empty' else answer, 'actions': [],
                'observation_scope': 'retained-source', 'input_refs': [1]}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    rig = traced.rig
    retained = rig.sources['source-a']['identity']['resource']
    actions.append({'tool': 'resources', 'args': {'id': retained['id'], 'inspect': True}})
    slow_record.update(rig.app.hub.resources.results.add('measure', {}, 'Synthetic held metadata read',
                        values={'schedulerMetadata': 'synthetic-read-was-held'}))
    metadata_path = rig.app.hub.resources.results.root / slow_record['day'] / (slow_record['id'] + '.json')
    original_metadata = metadata_path.read_bytes()
    opened, release = threading.Event(), threading.Event()
    actual_open = Path.open
    actual_turn = rig.app.brain.turn
    actual_report = rig.app.brain.report_late
    actual_show = rig.app.dialogue.show
    reported, delivered = [], []

    def held_metadata_open(path, *args, **kwargs):
        mode = kwargs.get('mode', args[0] if args else 'r')
        if path == metadata_path and mode in ('r', 'rb'):
            opened.set()
            if not release.wait(10):
                raise TimeoutError('actual metadata I/O barrier was not released')
        return actual_open(path, *args, **kwargs)

    async def trace_turn(*args, **kwargs):
        kwargs['delegation_id'] = delegation  # existing trusted-caller keyword, no fabricated turn
        return await actual_turn(*args, **kwargs)

    async def trace_report(ctx, run):
        await actual_report(ctx, run)  # actual Announcer/Dialogue persistence and publication
        reported.append((ctx, run))

    def trace_show(line, **kwargs):
        shown = actual_show(line, **kwargs)
        if shown.get('type') == 'chat-action':
            delivered.append(copy.deepcopy(shown))
        return shown

    monkeypatch.setattr(Path, 'open', held_metadata_open)
    monkeypatch.setattr(rig.app.brain, 'turn', trace_turn)
    monkeypatch.setattr(rig.app.brain, 'report_late', trace_report)
    monkeypatch.setattr(rig.app.dialogue, 'show', trace_show)

    async def scenario():
        await rig.camera()
        await rig.ingest()
        result = await rig.send(text='Inspect the saved images and the current view')
        assert await asyncio.to_thread(opened.wait, HANG_S), 'BEHAVIOR: real metadata read must reach the external filesystem barrier'
        assert [row['ok'] for row in result['actions']] == [True, None]
        assert [sent.phase for sent in traced.sends] == ['fast', 'query'] and not traced.looks
        ticket, budget = assert_actual_scheduler_admission(traced, result)
        assert budget.used == 2 and not ticket.closed
        assert len(traced.started) == 2
        acquired, initial_ctx, owner = traced.started[0]
        pending, ctx, pending_owner = traced.started[1]
        assert owner is pending_owner is ticket and initial_ctx is ctx
        assert acquired.ok is True and len(acquired.images) == 1 and pending.ok is None and not pending.images
        slots = ctx.inspection_slots
        assert len(slots) == 1 and ctx.client is rig.client and ctx.delegation_id == delegation
        assert ctx.turn_id == result['turnId'] and ctx.channel == 'chat'
        pin = ctx.request_snapshot
        original_pin = pin.wire()
        occurrence, asked = pending.occurrence, ctx.asked
        assert occurrence.request_id == budget.request_id and ctx.request_id == budget.request_id
        query = traced.sends[1]
        assert query.images == (acquired.images[0].jpeg,) and CURRENT_CAMERA_UNSUPPLIED in query.prompt
        tasks = tuple(rig.app.brain._query_answers)
        assert tasks and not reported
        assert result['answer'] == '' and result['answerStatus'] == 'not-published'
        initial_activity = [row for row in delivered if row.get('turnId') == result['turnId']]
        assert len(initial_activity) == 2
        assert [(row['requestId'], row['actionId'], row['ok'], row['summary']) for row in initial_activity] == [
            (row['requestId'], row['actionId'], row['ok'], row['summary']) for row in result['actions']]
        peer = rig.message(seq=80, ref=OTHER)
        peer['items'][0]['rect'][0] = 700
        assert (await rig.ingest(peer, rig.other))['ok'] is True
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 4)
        assert pending.ok is True and 'synthetic-read-was-held' in pending.summary and not pending.images
        assert ctx.inspection_slots is slots and len(slots) == 1  # metadata added no pixel slot
        assert ctx.client is rig.client and ctx.request_snapshot is pin and pin.wire() == original_pin
        assert ctx.delegation_id == delegation and ctx.asked == asked and pending.occurrence is occurrence
        assert [sent.phase for sent in traced.sends] == ['fast', 'query', 'late'] and budget.used == 3
        assert all(sent.ticket is ticket and sent.admission is budget for sent in traced.sends)
        assert [attempt.attempt_id for attempt in budget.attempts] == [sent.attempt.attempt_id for sent in traced.sends]
        assert [attempt.status for attempt in budget.attempts] == ['done', 'done', 'failed' if late_outcome == 'failed' else 'done']
        late = traced.sends[-1]
        image = acquired.images[0]
        assert late.images == (image.jpeg,) and len(late.images) <= 2
        assert json.dumps({'image': 1, **image.facts}, default=str) in late.prompt
        assert image.facts['id'] == retained['id'] and hashlib.sha256(late.images[0]).hexdigest() == image.facts['inputSha256']
        assert reported and delivered, 'BEHAVIOR: real late operation activity must publish on the original turn'
        reported_ctx, final_run = reported[-1]
        assert reported_ctx is ctx and final_run.occurrence is occurrence
        assert delivered[-1]['turnId'] == result['turnId'] and delivered[-1]['thread'] == asked.thread
        assert delivered[-1]['requestId'] == occurrence.request_id == budget.request_id
        assert delivered[-1]['actionId'] == occurrence.action_id
        assert delivered[-1]['ok'] is True and delivered[-1]['summary'] == pending.summary
        assert delivered[-1].get('answer', '') == final_run.answer
        assert 'synthetic-read-was-held' in late.prompt
        assert CURRENT_CAMERA_UNSUPPLIED in late.prompt, 'BEHAVIOR: every late attempt retains the original missing camera fact'
        assert metadata_path.read_bytes() == original_metadata
        if late_outcome == 'partial':
            assert CURRENT_CAMERA_UNSUPPLIED in late.prompt, 'MISSING_FEATURE: deferred retained-only synthesis must receive the original unsupplied-current-camera fact'
            assert final_run.answer == CURRENT_PARTIAL_ANSWER and CURRENT_COMPARISON_CLAIM not in final_run.answer
        else:
            assert final_run.answer == '' and not delivered[-1].get('answer')
        history = rig.app.dialogue.history()['lines']
        assert not any(row.get('role') == 'assistant' and row.get('turnId') == result['turnId'] for row in history)
        assert any(row.get('type') == 'chat-action' and row.get('actionId') == occurrence.action_id
                   and row.get('requestId') == budget.request_id and row.get('ok') is True
                   and row.get('summary') == pending.summary for row in history)
    try:
        asyncio.run(scenario())
    finally:
        release.set()
        rig.close()


@pytest.mark.parametrize('needs_camera,final_scope', [
    pytest.param(True, None,
                 id='camera-obligation-missing-scope'),
    pytest.param(True, 'workspace-pixels',
                 id='camera-obligation-invalid-channel'),
    pytest.param(False, None,
                 id='retained-only-no-camera-obligation'),
])
def test_actual_acquired_final_scope_refusal_retains_original_camera_limit(tmp_path, monkeypatch, needs_camera, final_scope):
    import json

    actions = []
    rejected_answer = 'SCRIPTED FINAL FULFILLMENT WITHOUT SUPPORTED OBSERVATION METADATA.'

    def response(sent):
        if sent.phase == 'fast':
            return {'answer': 'I am inspecting the retained source.', 'intent': 'request',
                    'needs_look': needs_camera, 'actions': actions, 'observation_scope': 'none', 'input_refs': []}
        if sent.phase == 'query':
            # No eligible answer yet: the original final refusal must run.
            return {'answer': '',
                    'actions': [], 'observation_scope': 'retained-source', 'input_refs': [1]}
        assert sent.phase == 'final', 'BEHAVIOR: actual receipt-grounded refusal must stay inside the original three sends'
        data = {'answer': rejected_answer, 'actions': []}
        if final_scope is not None:
            data.update(observation_scope=final_scope, input_refs=[1])
        return data

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    rig = traced.rig
    retained = rig.sources['source-a']['identity']['resource']
    actions.append({'tool': 'resources', 'args': {'id': retained['id'], 'inspect': True}})

    async def scenario():
        await rig.camera()
        await rig.ingest()
        expected_observation = rig.observed().wire()
        question = 'Inspect the saved images and the current view' if needs_camera else 'Inspect only this saved picture'
        result = await rig.send(text=question)
        ticket, budget = assert_actual_scheduler_admission(traced, result)
        assert [sent.phase for sent in traced.sends] == ['fast', 'query', 'final'] and not traced.looks
        assert budget.used == 3 and all(attempt.status == 'done' for attempt in budget.attempts)
        assert traced.sends[1].reply_data == {'answer': '', 'actions': [],
            'observation_scope': 'retained-source', 'input_refs': [1]}
        expected_final = {'answer': rejected_answer, 'actions': []}
        if final_scope is not None:
            expected_final.update(observation_scope=final_scope, input_refs=[1])
        assert traced.sends[2].reply_data == expected_final
        assert len(traced.started) == 1 and len(result['actions']) == 1 and result['actions'][0]['ok'] is True
        run, ctx, owner = traced.started[0]
        assert owner is ticket and run.ok is True and run.resource['id'] == retained['id']
        assert ctx.client is rig.client and ctx.admission is budget and ctx.request_id == budget.request_id
        assert ctx.turn_id == result['turnId'] and ctx.channel == 'chat'
        pin = ctx.request_snapshot
        assert pin.observation.wire() == expected_observation
        socket, request_id, accepted_pin = rig.pins[0]
        assert socket is rig.client and request_id == budget.request_id and accepted_pin is pin
        assert len(ctx.inspection_slots) == len(run.images) == 1
        image = run.images[0]
        assert image.facts['id'] == retained['id'] and image.facts['day'] == retained['day']
        for sent in traced.sends[1:]:
            assert_supplied_answer_facts(sent, [run.wire()], [image], camera_required=needs_camera)
            assert sent.images == (image.jpeg,) and len(sent.images) <= 2
            assert json.dumps({'image': 1, **image.facts}, default=str) in sent.prompt
            assert question in sent.prompt
            assert hashlib.sha256(sent.images[0]).hexdigest() == image.facts['inputSha256']
            assert (CURRENT_CAMERA_UNSUPPLIED in sent.prompt) is needs_camera
        assert result['answer'] == (rejected_answer if final_scope is None else '')
        assert result['answerStatus'] == ('model' if final_scope is None else 'not-published')
        assert pin.observation.wire() == expected_observation
        assert result['actions'][0]['requestId'] == run.occurrence.request_id == budget.request_id
        assert result['actions'][0]['actionId'] == run.occurrence.action_id
        history = rig.app.dialogue.history()['lines']
        assert any(row.get('role') == 'assistant' and row.get('turnId') == result['turnId'] for row in history) is (final_scope is None)
        activity = [row for row in history if row.get('type') == 'chat-action']
        activity += [action for row in history for action in row.get('actions', [])]
        assert any(row.get('requestId') == budget.request_id
                   and row.get('actionId') == run.occurrence.action_id and row.get('ok') is True
                   and row.get('summary') == run.summary and row.get('inspected') == result['actions'][0]['inspected']
                   for row in activity)
    try:
        asyncio.run(scenario())
    finally:
        rig.close()
