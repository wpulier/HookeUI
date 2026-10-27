"""Synthetic catalog supplier / real UI command boundary; C6 runtime acceptance is separate.

These tests supply synthetic capabilities through the real kinds module's query
seams, preserving backend registration regardless of test import order.
"""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog
from orchestrator import catalog_view
from tests.waiting import HANG_S


@pytest.fixture
def kind_supplier(monkeypatch):
    from orchestrator import env_runner, kinds
    monkeypatch.setattr(env_runner, 'in_use', lambda m, environ=None: None)
    # This synthetic supplier has neither an active runtime nor a disabled retained selection.
    monkeypatch.setattr(env_runner, 'selected', lambda m, environ=None: None)
    implementation = lambda model: getattr(model, 'implementation', None)  # noqa: E731
    # The door asks the capability's own runner whether it is proven (kinds.backends); here python-env's, read each time
    # so a test's stand-in in_use is what it sees.
    monkeypatch.setattr(kinds, 'implements', implementation)
    monkeypatch.setattr(kinds, 'door_entry', implementation)
    monkeypatch.setattr(kinds, 'read_once', nullcontext)
    monkeypatch.setattr(kinds, 'backends', lambda: {'python-env': SimpleNamespace(
        in_use=lambda m, environ=None: env_runner.in_use(m, environ))})
    monkeypatch.setattr(kinds, 'OUTPUTS', {'image.2d.rgb8': 'a picture', 'tracks.graph': 'tracks'})
    monkeypatch.setattr(kinds, 'available', lambda: [])
    monkeypatch.setattr(catalog, 'VOLUME_KIND_OUTPUTS', {'volume.3d': 'a volume'}, raising=False)
    return kinds


@pytest.mark.parametrize('input_type,output,label', [
    ('image.2d.rgb8', 'image.2d.rgb8', 'current still image'),
    ('clip.2d.rgb8', 'tracks.graph', 'recent frames'),
    ('volume.3d', 'volume.3d', 'selected volume'),
])
def test_new_kind_card_discloses_input_output_and_a_truthful_trial(kind_supplier, input_type, output, label):
    model = SimpleNamespace(id='denoise', key='mine/denoise', name='Denoise', runner='python-env', version='1', members=(),
                            inputs=(catalog.Input('source', input_type),),
                            implementation=SimpleNamespace(operation='denoise', input=input_type, output=output))
    contract = catalog_view.kind_info(model)
    assert contract['input'] == input_type and contract['output'] == output
    assert contract['gives']
    trial = catalog_view.tries(model, [])[0]
    assert label in trial['title']
    assert trial['try'] == {'command': 'capability-run', 'model': 'mine/denoise'}


def test_studio_trial_requires_the_selected_volume_and_keeps_its_original_session(tmp_path, monkeypatch, kind_supplier):
    import asyncio
    from orchestrator import commands
    from orchestrator.brain.registry import Tool, ToolFailed, schema
    from tests.capability_rig import Rig

    app = Rig(tmp_path).app
    original = catalog.get
    from orchestrator import env_runner
    model = SimpleNamespace(runner='python-env', implementation=SimpleNamespace(input='image.2d.rgb8'))
    active = SimpleNamespace(implementation=SimpleNamespace(input='volume.3d'))
    monkeypatch.setattr(env_runner, 'in_use', lambda m, environ=None: SimpleNamespace(m=active) if m is model else None)
    monkeypatch.setattr(catalog, 'get', lambda key: model if key == 'mine/volume' else original(key))
    kind_supplier.available = lambda: [{'key': 'mine/volume', 'name': 'Volume tool', 'what': 'Process a volume',
                                       'family': 'common', 'runner': 'python-env'}]
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []
    async def supplied_kind(ctx, args):
        if not args.get('volume'):
            return 'Incorrectly selected the newest volume'
        entered.set()
        await release.wait()
        seen.append((ctx.asked.session, args))
        return 'Started selected volume as job j9'
    from orchestrator.brain import toolbox
    monkeypatch.setattr(toolbox, 'add_kind_tools', lambda registry, services:
                        registry.add(Tool('run_capability', 'Synthetic C6 supplier', schema({}), supplied_kind)))
    app.brain.registry = toolbox.build(app.services)
    session = app.day.sid
    async def run():
        handler = commands.COMMANDS['capability-run']
        for bad in ({'session': session}, {'session': 'another', 'volume': 'v1'}, {'volume': 'v1'}):
            with pytest.raises(ToolFailed, match='volume|session'):
                await handler(app, None, {'model': 'mine/volume', 'input': 'volume.3d', **bad})
        assert not seen and not entered.is_set()
        pending = asyncio.create_task(handler(app, None, {'model': 'mine/volume', 'input': 'volume.3d', 'session': session, 'volume': 'v1'}))
        reached = asyncio.create_task(entered.wait())
        try:
            done, _ = await asyncio.wait({reached, pending}, timeout=HANG_S, return_when=asyncio.FIRST_COMPLETED)
            if pending in done:
                pytest.fail(f'Trial ended before dispatch: {await pending}')
            assert reached in done, 'Trial did not dispatch its selected volume'
        finally:
            reached.cancel()
        app.day.new('Another session', 'user')
        release.set()
        reply = await pending
        assert reply['ok'] and 'j9' in reply['summary']
        assert seen == [(session, {'capability': 'mine/volume', 'params': {}, 'volume': 'v1', 'by': 'user'})]
    asyncio.run(run())


def test_generic_volume_description_refuses_stale_session_and_does_not_require_orientation():
    import asyncio
    from orchestrator import commands, registrations
    from orchestrator.brain.registry import ToolFailed

    record = {'facts': {k: {'value': value} for k, value in {'series': 0, 'channel': 0, 'time': 0,
              'voxel_um': [1, 2, 3], 'orientation': None}.items()}, 'series': [{'shape': [3, 4, 5], 'axes': 'ZYX'}]}
    described = []
    def describe(vid, **facts):
        described.append((vid, facts)); return record
    regs = SimpleNamespace(session=lambda: 'session-a', describe=describe, words=lambda r: 'Stack',
                           missing=lambda r, **kw: registrations.Registrations.missing(None, r, **kw))
    app = SimpleNamespace(registrations=regs)
    async def run():
        with pytest.raises(ToolFailed, match='session'):
            await commands.volume_describe(app, None, {'id': 'v1', 'session': 'other', 'voxelUm': [1, 2, 3]})
        assert not described
        reply = await commands.volume_describe(app, None, {'id': 'v1', 'session': 'session-a',
                                                           'voxelUm': [1, 2, 3], 'registration': False})
        assert reply['ok'] and reply['missing'] == []
        assert described[0][1]['session'] == 'session-a' and described[0][1]['orientation'] is None
        assert 'orientation' in registrations.Registrations.missing(None, record)[0]
    asyncio.run(run())


def test_kind_face_and_input_describe_the_proven_active_generation_during_an_update(monkeypatch, kind_supplier):
    from orchestrator import env_runner

    old = SimpleNamespace(id='denoise', key='mine/denoise', name='Denoise', version='1', runner='python-env',
                          inputs=(catalog.Input('source', 'volume.3d'),),
                          needs_to_get=(), needs_to_run=(), kind='tool-function',
                          implementation=SimpleNamespace(operation='denoise', input='volume.3d', output='volume.3d'))
    new = SimpleNamespace(**{**vars(old), 'version': '2',
                          'inputs': (catalog.Input('source', 'image.2d.rgb8'),),
                          'implementation': SimpleNamespace(operation='denoise', input='image.2d.rgb8', output='image.2d.rgb8')})
    monkeypatch.setattr(env_runner, 'in_use', lambda m, environ=None: SimpleNamespace(m=old, proof={'ok': True}, current=False))
    info = catalog_view.kind_info(new)
    assert info['input'] == 'volume.3d' and info['activeVersion'] == '1'
    face = catalog_view.face(new, SimpleNamespace(status='ready'), [])
    assert face['state'] == 'on' and '1' in face['says'] and 'adapter' in face['says']
    monkeypatch.setattr(env_runner, 'in_use', lambda m, environ=None: None)
    face = catalog_view.face(new, SimpleNamespace(status='ready'), [])
    assert face['state'] == 'needs-setup' and face['action']['model'] == 'mine/denoise'


def test_trial_refuses_when_active_input_contract_changed_since_the_card_was_drawn(tmp_path, monkeypatch, kind_supplier):
    import asyncio
    from orchestrator import commands
    from orchestrator.brain.registry import Tool, ToolFailed, schema
    from tests.capability_rig import Rig

    app = Rig(tmp_path).app
    original = catalog.get
    model = SimpleNamespace(runner='python-env', implementation=SimpleNamespace(input='image.2d.rgb8'))
    monkeypatch.setattr(catalog, 'get', lambda key: model if key == 'mine/changed' else original(key))
    ran = []
    async def supplied_kind(ctx, args):
        ran.append(args); return 'Ran a still-image operation'
    from orchestrator.brain import toolbox
    monkeypatch.setattr(toolbox, 'add_kind_tools', lambda registry, services:
                        registry.add(Tool('run_capability', 'Synthetic C6 supplier', schema({}), supplied_kind)))
    app.brain.registry = toolbox.build(app.services)
    async def run():
        with pytest.raises(ToolFailed, match='input|changed'):
            await commands.COMMANDS['capability-run'](app, None, {'model': 'mine/changed', 'session': app.day.sid,
                                                               'input': 'volume.3d', 'volume': 'v1'})
        assert not ran
    asyncio.run(run())


def test_a_kind_says_what_it_makes_in_its_declared_words(kind_supplier):
    """The run page's Makes line and its done note come from the folder's own declaration (its output's label and
    tooltip), never from words the app writes about it; left out, nothing is added."""
    said = SimpleNamespace(id='regions', key='mine/regions', name='Regions', runner='python-env', version='1', members=(),
                           inputs=(catalog.Input('image', 'image.2d.rgb8'),),
                           outputs=(catalog.Output('regions', 'image.2d.rgb8', 'Bright regions (not cells)',
                                                   'Bright regions are not a cell count'),),
                           implementation=SimpleNamespace(operation='cells', input='image.2d.rgb8', output='image.2d.rgb8'))
    contract = catalog_view.kind_info(said)
    assert contract['made'] == 'Bright regions (not cells)'
    assert contract['madeNote'] == 'Bright regions are not a cell count'
    plain = SimpleNamespace(id='plain', key='mine/plain', name='Plain', runner='python-env', version='1', members=(),
                            inputs=(catalog.Input('image', 'image.2d.rgb8'),),
                            implementation=SimpleNamespace(operation='denoise', input='image.2d.rgb8', output='image.2d.rgb8'))
    assert not {'made', 'madeNote'} & set(catalog_view.kind_info(plain))


def test_the_bundled_bright_objects_says_it_finds_regions_not_cells():
    manifest = catalog.get('basic-bright-objects')
    assert [(o.label, o.tooltip) for o in manifest.outputs] == [('Bright regions (not cells)', 'Bright regions are not a cell count')]


def test_a_kind_carries_its_declared_settings_before_it_is_set_up():
    """S11: Gaussian Blur (Fiji) declares Sigma (radius), 0.5 to 20 px, default 2; its run page shows that before Fiji is
    set up (disabled there), from the declaration alone, never invented. One with no settings carries none."""
    blur = catalog_view.kind_info(catalog.get('fiji-gaussian-blur'))
    assert [(f['key'], f['label'], f['min'], f['max'], f['initial'], f['unit']) for f in blur['fields']] == [
        ('sigma', 'Sigma (radius)', 0.5, 20.0, 2.0, 'px')]
    assert 'fields' not in catalog_view._kind_description(catalog.get('fiji-analyze-particles'), None)
