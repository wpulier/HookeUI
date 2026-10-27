"""Private own-input adoption over synthetic adapter/runner boundaries, no models or installs."""
import asyncio
import hashlib
import json
import os
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator import drafts, env_runner, operations, own_trials
from tests.test_env_runner import home, contribute
from tests.test_kind_boundaries import installed_fixture
from tests.capability_rig import Rig


def image():
    rgb = np.arange(24 * 32 * 3, dtype=np.uint8).reshape(24, 32, 3)
    return cv2.imencode('.png', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))[1].tobytes()


def test_chosen_input_compares_both_and_use_requires_unchanged_installed_result(home, tmp_path, monkeypatch):
    m = contribute(home)
    installed_fixture(m)
    rig = Rig(tmp_path / 'rig')
    trial = own_trials.add_input(image(), 'find', rig.hub)
    own_trials.prepare(m.key, trial['id'])
    env_runner.register()
    assert not any(i.id == m.id for i in operations.OPERATIONS['cells'].implementations)
    seen = []
    async def candidate(run, capture, prompt, impl, params):
        seen.append(('candidate', capture.analysis_bytes))
        return {'count': 2, 'polygons': [[[1, 1], [4, 1], [4, 4]]], 'version': m.version}
    async def default(operation, capture, prompt, **kw):
        seen.append(('default', capture.analysis_bytes, kw['choice']))
        return SimpleNamespace(output={'count': 1, 'polygons': []})
    monkeypatch.setattr(env_runner, '_run', candidate)
    monkeypatch.setattr(operations.runner(rig.hub), 'run', default)
    result = asyncio.run(own_trials.compare(rig.app, trial['id'], model=m.key))
    assert seen[0][1] == seen[1][1] == image()
    assert result['candidate']['count'] == 2 and result['baseline']['count'] == 1
    assert result['inputSha256'] == hashlib.sha256(image()).hexdigest()
    assert result['candidate']['ms'] >= 0 and result['baseline']['ms'] >= 0
    assert own_trials.pending(env_runner.in_use(m).m)
    own_trials.use(m.key, trial['id'])
    env_runner.register()
    assert any(i.id == m.id for i in operations.OPERATIONS['cells'].implementations)
    # A later install cannot silently reuse this user's approval of different code.
    newer = contribute(home, version='dark-spots 0.2')
    installed_fixture(newer)
    with pytest.raises(ValueError, match='changed'):
        own_trials.use(newer.key, trial['id'])
    assert own_trials.pending(env_runner.in_use(newer).m)


def test_draft_job_and_comparison_are_bound_to_the_chosen_picture(home):
    did = drafts.create('https://github.com/maya/denoiser', image(), job='clear')
    d = drafts.folder(did)
    (d / 'env.lock').write_text('')
    m = drafts._manifest(did, d, drafts._state(did))
    assert m.implements[0].output == 'image.2d.rgb8'
    assert m.implements[0].operation != 'cells'
    state = drafts._state(did)
    state.update(state='ran', tried='synthetic')
    drafts._save_state(did, state, __import__('os').environ)
    with pytest.raises(drafts.DraftError, match='Compare'):
        drafts.promote(did)


def test_input_validation_has_no_hidden_fallback_and_preserves_bytes(home, tmp_path):
    rig = Rig(tmp_path / 'rig')
    with pytest.raises(ValueError, match='picture'):
        own_trials.add_input(b'not-an-image', 'find', rig.hub)
    with pytest.raises(ValueError, match='job'):
        own_trials.add_input(image(), 'follow', rig.hub)
    result = own_trials.add_input(image(), 'clear', rig.hub)
    assert own_trials.picture(result['id']) == image()
    assert result['job'] == 'clear'


def test_failed_current_default_does_not_unlock_use(home, tmp_path, monkeypatch):
    m = contribute(home)
    installed_fixture(m)
    rig = Rig(tmp_path / 'rig')
    trial = own_trials.add_input(image(), 'find', rig.hub)
    own_trials.prepare(m.key, trial['id'])
    async def candidate(*args):
        return {'count': 0, 'polygons': []}
    async def default(*args, **kwargs):
        raise operations.Unavailable('current default needs setup')
    monkeypatch.setattr(env_runner, '_run', candidate)
    monkeypatch.setattr(operations.runner(rig.hub), 'run', default)
    with pytest.raises(operations.Unavailable, match='needs setup'):
        asyncio.run(own_trials.compare(rig.app, trial['id'], model=m.key))
    with pytest.raises(ValueError, match='Compare'):
        own_trials.use(m.key, trial['id'])
    assert own_trials.pending(env_runner.in_use(m).m)


def test_a_default_not_set_up_here_leaves_the_candidate_to_review(home, tmp_path, monkeypatch):
    """Acceptance 2026-10-13: on a fresh install the default cell finder (Cellpose-SAM) is not downloaded, and the whole
    comparison was refused. It now shows the candidate on the chosen picture and says why the default isn't beside it;
    the default is never run, and a default that fails while it runs still refuses (above)."""
    from orchestrator import options

    m = contribute(home)
    installed_fixture(m)
    rig = Rig(tmp_path / 'rig')
    trial = own_trials.add_input(image(), 'find', rig.hub)
    own_trials.prepare(m.key, trial['id'])
    async def candidate(*args):
        return {'count': 2, 'polygons': [[[1, 1], [4, 1], [4, 4]]], 'version': m.version}
    async def default(*args, **kwargs):
        raise AssertionError('a default that cannot run here is never run')
    monkeypatch.setattr(env_runner, '_run', candidate)
    monkeypatch.setattr(operations.runner(rig.hub), 'run', default)
    monkeypatch.setattr(options, 'unavailable', lambda group, option, environ=None: 'not downloaded yet: 1.2 GB')
    result = asyncio.run(own_trials.compare(rig.app, trial['id'], model=m.key))
    assert result['candidate']['count'] == 2
    assert result['baseline']['available'] is False
    assert "can't run here yet (not downloaded yet: 1.2 GB)" in result['baseline']['says']
    own_trials.use(m.key, trial['id'])


def test_builder_brief_keeps_selected_job_and_exact_private_picture(home, tmp_path, monkeypatch):
    from orchestrator import commands
    from research_worker.prompts import deliverable_text
    rig = Rig(tmp_path / 'rig')
    selected = own_trials.add_input(image(), 'clear', rig.hub)
    briefs = []
    async def start(brief, **kwargs):
        briefs.append(brief)
        return SimpleNamespace(rid='r1', n=1)
    monkeypatch.setattr(rig.app.research, 'start', start)
    got = asyncio.run(commands.model_build(rig.app, None, {'link': 'https://github.com/lab/tool', 'trial': selected['id']}))
    ds = drafts._state(got['draft'])
    assert ds['job'] == 'clear' and ds['trial'] == selected['id']
    assert (drafts.folder(got['draft']) / 'picture.jpg').read_bytes() == image()
    assert 'image.2d.rgb8' in briefs[0] and 'cell finder' not in briefs[0]
    assert 'table.objects' in deliverable_text('adapter')


def test_picture_endpoint_is_studio_only_and_reopens_exact_input(home, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from orchestrator import server
    rig = Rig(tmp_path / 'rig')
    monkeypatch.setattr(server.App, 'build', lambda *args, **kwargs: rig.app)
    app = server.create_app('http://unused.invalid')
    # No lifespan, servers, models or providers. Requests stay inside the ASGI transport.
    client = TestClient(app, base_url='http://127.0.0.1:8770')
    assert client.post('/api/own-trials?job=find', content=image()).status_code == 403
    reply = client.post('/api/own-trials?job=find', content=image(),
                        headers={'Origin': 'http://127.0.0.1:8770', 'X-MicroscopeMind': 'own-picture'})
    assert reply.status_code == 200, reply.text
    tid = reply.json()['id']
    read = client.get(f'/api/own-trials/{tid}/input')
    assert read.content == image() and read.headers['content-type'] == 'image/png'
    assert client.get(f'/api/own-trials/{tid}/trial.json').status_code == 404


def test_set_up_for_comparison_takes_the_picture_chosen_only_then(home, tmp_path, monkeypatch):
    """Add no longer asks for a picture; Set up for comparison sends it with the turn-on, and the entry waits for its
    comparison on exactly that picture before Use it."""
    from orchestrator import commands
    m = contribute(home)
    installed_fixture(m)
    rig = Rig(tmp_path / 'rig')
    started = []
    monkeypatch.setattr(rig.app.turn_on, 'start', lambda job, asked=None: started.append(job) or {'job': job})
    monkeypatch.setattr(rig.app, 'capabilities_wire', lambda: {'type': 'capabilities'})
    assert not own_trials.pending(env_runner.in_use(m).m), 'added with no picture: nothing waits on a comparison yet'
    selected = own_trials.add_input(image(), 'find', rig.hub)
    asyncio.run(commands.model_turn_on(rig.app, None, {'model': m.key, 'trial': selected['id']}))
    assert started == [f'model:{m.key}']
    assert own_trials.pending(env_runner.in_use(m).m)
    assert [row['id'] for row in own_trials.wire()] == [selected['id']]
    with pytest.raises(commands.ToolFailed, match='choose your comparison picture'):
        asyncio.run(commands.model_turn_on(rig.app, None, {'model': m.key, 'trial': 'not-a-picture'}))


def test_an_entry_added_again_with_a_picture_keeps_what_was_accepted_for_it(home, tmp_path):
    m = contribute(home)
    installed_fixture(m)
    rig = Rig(tmp_path / 'rig')
    first = own_trials.add_input(image(), 'find', rig.hub)
    own_trials.prepare(m.key, first['id'])
    digest = __import__('orchestrator.envs', fromlist=['entry_digest']).entry_digest(env_runner.in_use(m).m)
    own_trials.marker(m.key).write_text(json.dumps({'model': m.key, 'trial': first['id'], 'adoptedDigest': digest}))
    assert not own_trials.pending(env_runner.in_use(m).m)
    own_trials.prepare(m.key, own_trials.add_input(image(), 'find', rig.hub)['id'])
    assert not own_trials.pending(env_runner.in_use(m).m), 'the same generation, accepted already, stays a choice'


@pytest.mark.parametrize("kind, waits", [("template:hf-segmentation", True), ("cellpose-file", True), ("skeleton", False)])
def test_a_model_added_without_a_picture_still_waits_for_its_comparison(home, tmp_path, monkeypatch, kind, waits):
    """Add needs no picture, but a model it can run is compared on one before it is a choice (C3): turned on from its own
    card it is installed and checked, not added as a choice; its card says to choose a picture. A skeleton runs nothing."""
    from orchestrator import catalog_view, commands, mine
    m = contribute(home)
    installed_fixture(m)
    rig = Rig(tmp_path / 'rig')
    monkeypatch.setattr(mine, 'add', lambda source, name: {'model': m.key, 'kind': kind, 'says': f'Added {m.name}.'})
    monkeypatch.setattr(rig.app, 'sync_mine', lambda: asyncio.sleep(0))
    asyncio.run(commands.model_add(rig.app, None, {'source': 'https://huggingface.co/x/y'}))
    assert own_trials.pending(env_runner.in_use(m).m) is waits
    assert own_trials.wire() == [], 'no comparison row before a picture is chosen'
    if waits:
        assert own_trials.awaiting_picture(m.key)
        assert m.id not in env_runner.register()["added"], 'not a choice before Use it'
        selected = own_trials.add_input(image(), 'find', rig.hub)
        own_trials.prepare(m.key, selected['id'])
        assert not own_trials.awaiting_picture(m.key)
        assert [row['id'] for row in own_trials.wire()] == [selected['id']]


def test_already_added_says_the_resolvers_step_then_the_comparison_then_that_it_is_a_choice(home, tmp_path, monkeypatch):
    """"Already added" reads the readiness resolver and the comparison state (Codex's cleanliness audit, P2 2): never a
    second reading of setup, and an adopted entry is said to be a choice already, not sent back to compare."""
    from orchestrator import envs, mine, readiness
    m = contribute(home)
    installed_fixture(m)
    rig = Rig(tmp_path / 'rig')
    assert mine._next_step(m, os.environ) == 'it is already a choice', 'installed and proven, no comparison owed'
    own_trials.await_comparison(m.key)
    assert mine._next_step(m, os.environ) == 'choose a picture of yours and Set up for comparison, then Use it'
    selected = own_trials.add_input(image(), 'find', rig.hub)
    own_trials.prepare(m.key, selected['id'])
    assert mine._next_step(m, os.environ) == 'compare it on your picture, then Use it'
    digest = envs.entry_digest(env_runner.in_use(m).m)
    own_trials.marker(m.key).write_text(json.dumps({'model': m.key, 'trial': selected['id'], 'adoptedDigest': digest}))
    assert mine._next_step(m, os.environ) == 'it is already a choice', 'adopted: Use it was pressed for this generation'
    for step in (readiness.Step('trust', 'Allow code from lab'), readiness.Step('key', 'Add your Google Gemini API key')):
        monkeypatch.setattr(readiness, 'model', lambda key, keys=None, environ=None, s=step: readiness.Readiness(
            'needs-setup', s.says, steps=(s,)))
        assert mine._next_step(m, os.environ) == step.says[0].lower() + step.says[1:], 'the prerequisite the card shows'


def test_guided_builder_accepts_a_job_without_a_link_and_keeps_explicit_input(home, tmp_path, monkeypatch):
    from orchestrator import commands
    rig = Rig(tmp_path / 'rig')
    selected = own_trials.add_input(image(), 'measure', rig.hub)
    calls = []
    async def start(brief, **kwargs):
        calls.append((brief, kwargs))
        return SimpleNamespace(rid='guided', n=1)
    monkeypatch.setattr(rig.app.research, 'start', start)
    result = asyncio.run(commands.model_build(rig.app, None, {'description': 'Measure the bright spots',
        'trial': selected['id'], 'session': rig.app.day.sid}))
    assert result['draft'] and len(calls) == 1
    assert 'Measure the bright spots' in calls[0][0] and 'table.objects' in calls[0][0]
    assert calls[0][1]['effort'] == 'build' and calls[0][1]['confirmed'] is True
    assert drafts._state(result['draft'])['trial'] == selected['id']
    assert (drafts.folder(result['draft']) / 'picture.jpg').read_bytes() == image()


@pytest.mark.parametrize('change', [{'trial': ''}, {'session': 'old'}, {'description': ''}])
def test_guided_builder_refuses_missing_or_stale_intent_before_creating_a_draft(home, tmp_path, monkeypatch, change):
    from orchestrator import commands
    rig = Rig(tmp_path / 'rig')
    selected = own_trials.add_input(image(), 'clear', rig.hub)
    monkeypatch.setattr(drafts, 'create', lambda *args, **kw: pytest.fail('no draft should be created'))
    request = {'description': 'Improve contrast', 'trial': selected['id'], 'session': rig.app.day.sid, **change}
    with pytest.raises(commands.ToolFailed):
        asyncio.run(commands.model_build(rig.app, None, request))


def test_builder_missing_skill_refuses_before_admission_or_draft(home, tmp_path, monkeypatch):
    from orchestrator import commands
    from research_worker import prompts
    rig = Rig(tmp_path / 'rig')
    monkeypatch.setattr(prompts, 'SKILL', tmp_path / 'missing-SKILL.md')
    monkeypatch.setattr(drafts, 'create', lambda *args, **kw: pytest.fail('no incomplete draft'))
    with pytest.raises(commands.ToolFailed, match='canonical capability skill'):
        asyncio.run(commands.model_build(rig.app, None, {'link': 'https://github.com/lab/tool'}))
