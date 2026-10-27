"""Setup's public state machine respects prerequisite targets and ownership across asynchronous queue boundaries."""
import asyncio

from orchestrator import capability_shelf as shelf
from tests.test_capability_shelf import App, DOWNLOAD, models, role
from tests.capability_rig import Rig
from tests.waiting import HANG_S, until_async


class Queue:
    """A deterministic queue port: a returned activity is the identity of one request, including adopted work."""
    def __init__(self, existing=None):
        self.rows = [existing] if existing else []
        self.stopped = []
    def wire(self):
        return list(self.rows)
    def start(self, model, by='you'):
        existing = next((r for r in self.rows if r['model'] == model), None)
        if existing:
            return existing
        row = {'model': model, 'state': 'queued', 'activity': 'q1', 'by': by}
        self.rows.append(row)
        return dict(row)
    def cancel(self, model, **expected):
        row = next(r for r in self.rows if r['model'] == model)
        if expected.get('by') is not None and expected['by'] != row['by']:
            return row
        self.stopped.append(model)
        row['state'] = 'cancelled'
        return dict(row)


def test_install_uses_the_prerequisites_target_and_stop_cancels_owned_work(models):
    needs = {'status': 'needs-setup', 'steps': [
        {'kind': 'install', 'model': 'fiji', 'says': 'Set up Fiji before proving this binding'}]}
    app = App([{'role': role(('a', needs, 'mac'))}])
    app.installs = Queue()
    turn = shelf.TurnOn(app)
    turn.start('cells')
    assert [r['model'] for r in app.installs.wire()] == ['fiji']
    assert turn.cancel('cells')
    assert app.installs.stopped == ['fiji'] and not turn.wire()


def test_stop_does_not_cancel_a_download_already_started_by_someone_else(models):
    app = App([{'role': role(('a', DOWNLOAD, 'mac'))}])
    existing = {'model': 'cellpose-sam', 'state': 'downloading', 'activity': 'other', 'by': 'another request'}
    app.downloads = Queue(existing)
    turn = shelf.TurnOn(app)
    turn.start('cells')
    assert turn.cancel('cells') and app.downloads.stopped == []
    assert app.downloads.wire()[0]['state'] == 'downloading'


def test_stop_cancels_the_download_started_by_this_setup(models):
    app = App([{'role': role(('a', DOWNLOAD, 'mac'))}])
    app.downloads = Queue()
    turn = shelf.TurnOn(app)
    turn.start('cells')
    assert turn.cancel('cells') and app.downloads.stopped == ['cellpose-sam']


def test_setup_completion_files_in_the_original_session(tmp_path):
    app = Rig(tmp_path).app
    original = app.day.here()
    app.day.new('Another specimen')
    async def finish():
        app._loop = asyncio.get_running_loop()
        app.turned_on('cells', 'Find every cell is on', asked=original)
        await asyncio.sleep(.02)
    asyncio.run(finish())
    def lines(store):
        return [line for thread in store.threads.values() for line in thread.lines
                if line.get('producer', {}).get('kind') == 'turn-on']
    assert not lines(app.day)
    assert len(lines(app.day.elsewhere(original.session))) == 1


def test_one_press_proves_only_its_selected_option_with_a_public_input_and_files_the_origin(tmp_path, monkeypatch):
    from orchestrator import commands
    from orchestrator.model_checks import FRAME

    monkeypatch.setenv('MICROMIND_ASSETS', str(tmp_path / 'empty-public-assets'))
    rig = Rig(tmp_path)
    app = rig.app
    app.hub.settings.update({'outline': 'edgetam'})
    original = app.day.here()
    async def run():
        app._loop = asyncio.get_running_loop()
        result = await commands.capability_turn_on(app, None, {'id': 'outline'})
        assert result['state'] == 'running', 'downloaded alone does not complete setup'
        app.day.new('A different specimen')

        def settled():
            app.turn_on.advance()
            return not app.turn_on.wire()

        await until_async(settled)
        assert not app.turn_on.wire(), app.turn_on.wire()
        await asyncio.sleep(.02)
    asyncio.run(run())
    checks = app.hub.checks.latest()
    assert list(checks) == ['outline:edgetam'] and checks['outline:edgetam']['state'] == 'works'
    segments = [p for op, p in app.hub.gpu.calls if op == 'segment']
    assert len(segments) == 1 and segments[0]['box'] == [15.0, 15.0, 35.0, 35.0]
    assert not (tmp_path / 'empty-public-assets' / 'bench' / FRAME).exists()
    notices = [line for t in app.day.elsewhere(original.session).threads.values() for line in t.lines
               if line.get('producer', {}).get('kind') == 'turn-on']
    assert len(notices) == 1
    assert not [line for t in app.day.threads.values() for line in t.lines if line.get('producer', {}).get('kind') == 'turn-on']


def test_stop_cancels_the_owned_functional_check_without_a_late_success(tmp_path):
    from orchestrator import commands

    app = Rig(tmp_path).app
    app.hub.settings.update({'outline': 'edgetam'})
    entered, cancelled = asyncio.Event(), asyncio.Event()
    original_call = app.hub.gpu.call
    async def worker(op, **payload):
        if op == 'segment':
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return await original_call(op, **payload)
    app.hub.gpu.call = worker  # external worker boundary, no model or process
    async def run():
        app._loop = asyncio.get_running_loop()
        await commands.capability_turn_on(app, None, {'id': 'outline'})
        await asyncio.wait_for(entered.wait(), HANG_S)
        await commands.capability_turn_on_cancel(app, None, {'id': 'outline', 'attempt': app.turn_on.wire()['outline']['attempt']})
        await asyncio.wait_for(cancelled.wait(), HANG_S)
        await asyncio.sleep(.02)
        assert not app.turn_on.wire() and app.hub.checks.running is None
    asyncio.run(run())
    assert not app.hub.checks.latest()
    assert not [line for t in app.day.threads.values() for line in t.lines if line.get('producer', {}).get('kind') == 'turn-on']


def test_failed_functional_check_can_be_retried_by_one_press(tmp_path):
    from orchestrator import commands, model_checks

    app = Rig(tmp_path).app
    app.hub.settings.update({'outline': 'edgetam'})
    app.hub.checks.record(model_checks.find('outline', 'edgetam'), 'broken', 'old worker failed', by='synthetic test')
    async def run():
        app._loop = asyncio.get_running_loop()
        await commands.capability_turn_on(app, None, {'id': 'outline'})
        await until_async(lambda: not app.turn_on.wire() or app.turn_on.wire()['outline']['state'] == 'failed')
        assert not app.turn_on.wire(), app.turn_on.wire()
    asyncio.run(run())
    assert app.hub.checks.latest()['outline:edgetam']['state'] == 'works'


def test_a_newly_registered_option_gets_its_functional_check_without_restarting(tmp_path, monkeypatch):
    from dataclasses import replace
    from orchestrator import model_checks, options
    from orchestrator.operations import OPERATIONS

    app = Rig(tmp_path).app
    model_checks.entries()  # normal startup has already read the old catalog
    group = dict(options.GROUPS['outline'])
    group['new-edge'] = replace(group['edgetam'], id='new-edge', label='New Edge')
    monkeypatch.setitem(options.GROUPS, 'outline', group)
    op = OPERATIONS['outline']
    monkeypatch.setitem(OPERATIONS, 'outline', replace(op, implementations=(*op.implementations, replace(op.impl('edgetam'), id='new-edge', option='new-edge'))))
    monkeypatch.setattr(model_checks, '_entries', model_checks._entries)  # restore cache after this temporary registration
    app.catalog_changed()  # same notification a completed install/registration sends
    entry = model_checks.find('outline', 'new-edge')
    assert entry is not None and entry.probe == ('outline', 'new-edge')


def test_finishing_setup_does_not_overwrite_a_choice_made_while_it_waited(models):
    from tests.test_capability_shelf import READY, EDITION, KEY_FIRST

    off = {'role': role(('e', EDITION, 'mac'), ('a', DOWNLOAD, 'mac'), current='e')}
    later = {'role': role(('b', KEY_FIRST, 'cloud'), ('a', READY, 'mac'), current='b')}
    app = App([off, later])
    turn = shelf.TurnOn(app)
    turn.start('cells')
    app.settings['cells'] = 'b'  # a separate, later user action
    turn.advance()
    assert not turn.wire() and app.settings['cells'] == 'b'
    assert len(app.said) == 1 and 'is used' not in app.said[0]


def test_two_simultaneous_presses_retain_one_setup_owner(models):
    import concurrent.futures
    import threading

    app = App([{'role': role(('a', DOWNLOAD, 'mac'))}])
    barrier = threading.Barrier(2)
    original_wire = app.capabilities_wire
    def wire(counts=True):
        result = original_wire(counts=counts)
        side = barrier.wait(timeout=2)  # both passed start's initial lock check
        if side == 0:
            assert app.downloads.entered.wait(timeout=2)
        return result
    app.capabilities_wire = wire
    class QueueWhileStarting(Queue):
        def __init__(self):
            super().__init__()
            self.entered, self.release = threading.Event(), threading.Event()
        def start(self, model, by='you'):
            if self.entered.is_set():
                row = super().start(model, by)
                self.release.set()
                return row
            row = super().start(model, by)
            self.entered.set()
            self.release.wait(timeout=.2)  # first owned queue reply is delayed, as an actual queue callback may be
            return row
    app.downloads = QueueWhileStarting()
    turn = shelf.TurnOn(app)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(turn.start, 'cells', asked='first')
        second = pool.submit(turn.start, 'cells', asked='second')
        first.result(timeout=HANG_S); second.result(timeout=HANG_S)
    assert not app.downloads.stopped, 'a duplicate press cannot cancel the first admitted setup'
    assert turn.cancel('cells')
    assert app.downloads.stopped == ['cellpose-sam'], 'the retained owner must own the queue row it cancels'
