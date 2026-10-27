"""Exact existing artifact retrieval, including when the live view or cache changed."""
import asyncio
import hashlib

from orchestrator.brain.registry import ACTIVITY_KEYS, ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.day import DayStore
from orchestrator.magnifier import Magnifier
from orchestrator.resources import Index, Results
from tests.test_population import world  # noqa: F401
from tests.test_resources import jpeg
from tests.test_screen import setup as reporting_studio


def test_reopen_retains_result_identity_and_original_bytes_without_new_result(world, tmp_path):
    day = DayStore(root=tmp_path/'sessions')
    m = Magnifier(world)
    saved = Results(tmp_path/'results', day=day, pictures=m.pictures.get)
    original = saved.add('compare', {'a':'c41','b':'c63'}, 'Two retained candidates, unequal areas',
                         picture_bytes=[jpeg(67)], source={'captures':[{'member':'c41'},{'member':'c63'}]})
    saved.add('compare', {'a':'c41','b':'c88'}, 'Later similar comparison', picture_bytes=[jpeg(123)])
    world.resources = Index(world, day=day, results=saved)
    reporting_studio(world)
    registry = build(Services(hub=world, day=day, magnifier=m))
    async def go():
        result = registry.start('present', {'what':original['id'],'do':'open'}, ToolContext('chat',1,None))
        await result.task
        return result
    result = asyncio.run(go())
    assert result.ok, result.summary
    assert m.picture_now()['resource']['id'] == original['id']
    assert original['id'] in result.summary and 'existing' in result.summary
    assert len(saved.entries()) == 2
    raw=m.pictures[m.picture_now()['url'].rsplit('/',1)[-1]]
    assert hashlib.sha256(raw).digest() == hashlib.sha256(jpeg(67)).digest()
    assert 'c41' in world.resources.read(original['id'])
    assert 'not visually inspected' in world.resources.read(original['id'])


def test_successful_wrong_source_is_checked_against_receipt_within_three_calls():
    from tests.test_brain import make
    brain, hub, agent = make([
        {'answer':'Showing only the tall one.', 'actions':[{'tool':'magnifier_source','args':{'source':'/saved/pair'}}]},
        {'answer':'Only the tall candidate is displayed.', 'actions':[]},
    ])
    hub.settings.fast = 'off'
    m = Magnifier(hub)
    m.show_image('/saved/pair', 'Two original candidates side by side')
    brain.services = Services(hub=hub, magnifier=m)
    brain.registry = build(brain.services)
    agent.fast_model.rewrite = {'answer':'The comparison is still displayed; the single candidate was not selected.'}
    result = asyncio.run(brain.turn('Keep just the taller original in the window'))
    assert result['calls'] == 3
    assert 'not selected' in result['answer']
    assert 'Two original candidates side by side' in agent.fast_model.prompts[-1]
    assert m.picture_now()['url'] == '/saved/pair'


def test_unavailable_confirmation_never_keeps_wrong_source_completion():
    import json

    from tests.test_brain import make
    from orchestrator.ticket import current
    brain, hub, agent = make([
        {'answer':'Showing the isolated candidate.', 'actions':[{'tool':'magnifier_source','args':{'source':'/saved/pair'}}]},
        {'answer':'The candidate alone is displayed.', 'actions':[]},
    ])
    hub.settings.fast = 'off'
    m = Magnifier(hub)
    m.show_image('/saved/pair', 'Both retained candidates')
    brain.services = Services(hub=hub, magnifier=m)
    brain.registry = build(brain.services)
    original_sources = [dict(source) for source in m.sources]
    entered = []
    actual_ask = agent.fast_model.ask

    async def observe_provider(images, prompt, **kwargs):
        ticket = current()
        entry = {"ticket": ticket, "admission": ticket.admission,
                 "attempt": ticket.admission.attempts[-1], "images": tuple(images),
                 "prompt": prompt, "reply": None, "error": None}
        entered.append(entry)
        try:
            reply = await actual_ask(images, prompt, **kwargs)
        except BaseException as exc:
            entry["error"] = (type(exc), str(exc))
            raise
        entry["reply"] = reply.data
        return reply

    agent.fast_model.ask = observe_provider  # observe the genuine unavailable final, without replacing it
    result = asyncio.run(brain.turn('Leave only my second specimen in the window'))
    assert result['calls'] <= 3 and result['answer'] != 'The candidate alone is displayed.'
    assert len(agent.calls) == 2 and len(entered) == 1
    final = entered[0]
    budget = final['admission']
    assert final['ticket'].admission is budget and budget.used == result['calls'] == 3
    assert [attempt.phase for attempt in budget.attempts] == ['look', 'query', 'final']
    assert [attempt.status for attempt in budget.attempts] == ['done', 'done', 'failed']
    assert final['attempt'].request_id == budget.request_id and final['attempt'].phase == 'final'
    assert final['error'] == (asyncio.TimeoutError, 'no model for this prompt in the test')
    assert final['reply'] is None and final['images'] == ()
    assert 'Leave only my second specimen in the window' in final['prompt']
    opening = '<answer-facts source="app" untrusted="true">'
    assert final['prompt'].count(opening) == 1
    facts = json.loads(final['prompt'].split(opening, 1)[1].split('</answer-facts>', 1)[0])
    assert facts['operations'] == [{k: v for k, v in a.items() if k not in ACTIVITY_KEYS} for a in result['actions']]
    assert len(result['actions']) == 1
    receipt = result['actions'][0]
    assert receipt['tool'] == 'magnifier_source' and receipt['ok'] is True
    assert receipt['requestId'] == budget.request_id and receipt['actionId']
    assert 'Both retained candidates' in receipt['summary']
    assert 'one image pane (its image content is unchanged)' in receipt['summary']
    assert 'its other sources remain available' in receipt['summary']
    assert facts['inputs'] == {'legacyCameraCropSupplied': False, 'images': []}
    assert facts['omittedFacts'] == facts['omittedOperations'] == 0 and facts['limitations'] == []
    assert m.sources == original_sources and m.shown_beside() is None
    assert m.picture_now()['url'] == '/saved/pair' and m.picture_now()['label'] == 'Both retained candidates'
    assert result['answer'] == '' and result['answerStatus'] == 'not-published'
    assert not any(turn.role == 'assistant' for turn in brain.conversation.turns)


def test_request_with_no_action_cannot_claim_a_saved_annotation():
    from tests.test_brain import make
    brain, hub, agent = make([])
    brain.services = Services(hub=hub)
    agent.fast_model.fast = {'intent':'request','needs_look':False,'answer':'Saved your tentative note on the original.','actions':[]}
    agent.fast_model.rewrite = {'answer':'No note was saved; no annotation action ran.'}
    result = asyncio.run(brain.turn('Keep this as my provisional interpretation'))
    assert result['answer'] == 'No note was saved; no annotation action ran.'
    assert result['calls'] == 3 and result['actions'] == []


def test_identical_pixels_do_not_let_old_completion_relabel_new_source(world):
    from orchestrator.brain.registry import Tool, Registry, schema
    m=Magnifier(world)
    registry=Registry()
    releases={name:asyncio.Event() for name in ('old','new')}
    async def make_picture(ctx,args):
        m.show_picture(jpeg(89),args['label'])
        await releases[args['label']].wait()
        return args['label']
    registry.add(Tool('picture','',schema({}),make_picture))
    registry.finished.append(lambda run,ctx:m.identify_sources(run.picture_sources, {'id':run.args['label']}))
    async def go():
        old=registry.start('picture',{'label':'old'},ToolContext('chat',1,None))
        await asyncio.sleep(0)
        new=registry.start('picture',{'label':'new'},ToolContext('chat',2,None))
        await asyncio.sleep(0)
        releases['new'].set()
        await new.task
        releases['old'].set()
        await old.task
    asyncio.run(go())
    assert m.picture_now()['label']=='new' and m.picture_now()['resource']['id']=='new'
