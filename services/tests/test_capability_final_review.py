"""Final review regressions: real setup ownership with deterministic thread/worker boundaries."""
import asyncio
import concurrent.futures
import threading

import pytest

from orchestrator import capability_shelf as shelf
from tests.capability_rig import Rig
from tests.test_capability_shelf import App, DOWNLOAD, EDITION, KEY_FIRST, READY, models, role
from tests.test_capability_setup_lifecycle import Queue
from tests.waiting import HANG_S, until_async


def test_disclosure_names_the_admitted_option_even_after_selection_changes(models):
    roles = role(('a', EDITION, 'mac'), ('b', KEY_FIRST, 'cloud'))
    card = shelf.card(roles, 'Cells', '', '', [], [])
    assert card['turnOn']['option'] == 'b'
    assert 'fal.ai' in card['privacy'] and card['cost'] != 'Free'
    turning = {'job': 'cells', 'option': 'b', 'label': 'B', 'model': 'sam3.1-cloud',
               'state': 'waiting', 'says': 'Add your key', 'privacy': 'Sends pictures to fal.ai (your key)',
               'cost': 'Billed by the provider'}
    roles = role(('a', DOWNLOAD, 'mac'), ('b', KEY_FIRST, 'cloud'))
    card = shelf.card(roles, 'Cells', '', '', [], [], turning)
    assert card['turnOn']['option'] == 'b' and 'fal.ai' in card['privacy']


@pytest.mark.parametrize('old_result', ['download', 'ready', 'removed', 'check'])
def test_stale_advance_cannot_enqueue_complete_or_fail_a_replacement_intent(models, monkeypatch, old_result):
    app = App([{'role': role(('b', KEY_FIRST, 'cloud'), current='b')}])
    app.downloads = Queue()
    turn = shelf.TurnOn(app)
    turn.start('cells', asked='old')
    old = turn.intents['cells']
    entered, release = threading.Event(), threading.Event()
    original = shelf._options
    def paused(r):
        if threading.current_thread().name.startswith('old-advance'):
            entered.set()
            assert release.wait(HANG_S)
        return original(r)
    monkeypatch.setattr(shelf, '_options', paused)
    roles = role(('b', DOWNLOAD if old_result == 'download' else READY, 'cloud'), current='b', proven=old_result != 'check')
    if old_result == 'removed':
        roles['options'] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='old-advance') as pool:
        pending = pool.submit(turn.advance, {'roles': [roles], 'setup': {}})
        assert entered.wait(HANG_S)
        turn.cancel('cells')
        turn.start('cells', asked='replacement')
        replacement = turn.intents['cells']
        assert replacement is not old
        release.set(); pending.result(HANG_S)
    assert turn.intents.get('cells') is replacement
    assert replacement['state'] == 'waiting' and not app.downloads.rows and not app.said


def test_app_stop_drains_setup_proof_before_stopping_dependencies_and_closes_admission(tmp_path):
    from orchestrator import commands

    app = Rig(tmp_path).app
    app.hub.settings.update({'outline': 'edgetam'})
    entered, cancelled, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = app.hub.gpu.call
    async def worker(op, **payload):
        if op == 'segment':
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
                await asyncio.sleep(.01)  # cancellation is requested before dependency cleanup has actually finished
                drained.set()
        return await original(op, **payload)
    app.hub.gpu.call = worker
    old_stop = app.hub.stop
    async def dependencies_stop():
        assert drained.is_set(), 'proof coroutine must finish cancellation before its workers stop'
        await old_stop()
    app.hub.stop = dependencies_stop
    async def run():
        app._loop = asyncio.get_running_loop()
        await commands.capability_turn_on(app, None, {'id': 'outline'})
        await asyncio.wait_for(entered.wait(), HANG_S)
        await app.stop()
        assert cancelled.is_set() and drained.is_set() and not app.turn_on.wire()
        with pytest.raises(ValueError, match='stopp|closed'):
            app.turn_on.start('outline')
    asyncio.run(run())
    assert not app.hub.checks.latest()


def test_fiji_worker_does_not_reuse_a_jvm_when_the_generation_folder_was_replaced(tmp_path, monkeypatch):
    import contextlib
    import io
    from orchestrator import fiji_bindings, fiji_generations, owned_process

    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'catalog'))
    folder = fiji_generations.fiji_dir(1)
    folder.mkdir(parents=True)
    processes, ended = [], []
    class Process:
        def __init__(self, *args, **kwargs):
            self.stdout = io.StringIO('{"ok": true}\n')
            self.stdin = io.StringIO()
            processes.append(self)
        def poll(self):
            return None if self not in ended else 0
    monkeypatch.setattr(fiji_bindings.subprocess, 'Popen', Process)
    monkeypatch.setattr(owned_process, 'watching', lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(owned_process, 'terminate', lambda process: ended.append(process))
    worker = fiji_bindings.Worker()
    first = worker._start(1)
    assert worker._start(1) is first, 'unchanged generation keeps its worker'
    folder.rename(folder.with_name('old-Fiji'))
    folder.mkdir()
    assert worker._start(1) is not first and first in ended
    worker.stop()


def test_press_cannot_admit_different_terms_than_the_card_reviewed(models):
    old = role(('a', DOWNLOAD, 'mac'), ('b', KEY_FIRST, 'cloud'))
    reviewed = shelf.plan(old)
    app = App([{'role': role(('a', EDITION, 'mac'), ('b', KEY_FIRST, 'cloud'))}])
    turn = shelf.TurnOn(app)
    with pytest.raises(ValueError, match='changed|review'):
        turn.start('cells', expected=reviewed)
    assert not turn.wire() and not app.started


def test_setup_requires_a_new_review_when_admitted_model_recipe_changes(models, monkeypatch):
    from dataclasses import replace
    from gpu_worker import manifests as catalog

    app = App([{'role': role(('b', KEY_FIRST, 'cloud'), current='b')}])
    turn = shelf.TurnOn(app)
    turn.start('cells')
    original = catalog.get
    changed = replace(original('sam3.1-cloud'), version='changed-provider-recipe',
                      files=({'path': 'new.weights', 'bytes': 2000000000, 'sha256': 'f' * 64},))
    monkeypatch.setattr(catalog, 'get', lambda key: changed if key == 'sam3.1-cloud' else original(key))
    app.states = [{'role': role(('b', DOWNLOAD, 'cloud'), current='b')}]
    turn.advance()
    assert not app.started, 'a completed key step cannot authorize changed weights or provider terms'
    assert turn.wire()['cells']['state'] == 'failed' and 'review' in turn.wire()['cells']['says'].lower()
    # A fresh explicit press, using the now-disclosed plan, admits the replacement recipe.
    turn.start('cells', expected=shelf.plan(app.states[0]['role']))
    assert app.started == ['cellpose-sam']


@pytest.mark.parametrize('failing', ['news', 'outcomes'])
def test_optional_catalog_refresh_failure_does_not_stop_setup_progress(tmp_path, failing):
    from types import SimpleNamespace
    from orchestrator.app import App as Product

    progressed = asyncio.Event()
    calls, errors = [], []
    async def broken():
        raise OSError('temporary catalog metadata read failure')
    async def fine():
        return False
    def advance():
        calls.append('advance')
        if len(calls) == 2:
            progressed.set()
    app = SimpleNamespace(hub=SimpleNamespace(clients={1}, broadcast=lambda wire: None),
                          agent=SimpleNamespace(log=errors.append), capabilities_wire=lambda: {},
                          turn_on=SimpleNamespace(intents={'cells': {}}, advance=advance),
                          offer_catalog_news=broken if failing == 'news' else fine,
                          refresh_shelf_observations=broken if failing == 'outcomes' else fine)
    async def run():
        task = asyncio.create_task(Product.carry_turn_on(app, every=.001))
        try:
            await asyncio.wait_for(progressed.wait(), HANG_S)
            assert errors and not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_failed_setup_card_discloses_current_terms_for_the_new_press(models):
    turning = {'job': 'cells', 'option': 'b', 'state': 'failed', 'says': 'Review the changed recipe',
               'privacy': 'Runs on this Mac', 'cost': 'Free'}
    card = shelf.card(role(('b', KEY_FIRST, 'cloud'), current='b'), 'Cells', '', '', [], [], turning)
    assert 'fal.ai' in card['privacy'] and card['cost'] != 'Free'


def test_changed_recipe_cancels_a_proof_waiting_for_the_check_slot(tmp_path, monkeypatch):
    from dataclasses import replace
    from gpu_worker import manifests as catalog
    from orchestrator import commands

    app = Rig(tmp_path).app
    app.hub.settings.update({'outline': 'edgetam'})
    app.hub.checks.running = {'by': 'other', 'now': 'Other check', 'done': 0, 'of': 1}
    async def run():
        app._loop = asyncio.get_running_loop()
        await commands.capability_turn_on(app, None, {'id': 'outline'})
        await asyncio.sleep(.01)
        original = catalog.get
        changed = replace(original('edgetam'), version='different-weights')
        monkeypatch.setattr(catalog, 'get', lambda key: changed if key == 'edgetam' else original(key))
        app.turn_on.advance()
        assert app.turn_on.wire()['outline']['state'] == 'failed'
        app.hub.checks.running = None
        await asyncio.sleep(.15)
        assert not app.hub.checks.latest(), 'the withdrawn recipe cannot produce a later proof after another check frees the slot'
        await app.stop()
    asyncio.run(run())


def test_disappearing_option_cancels_its_owned_download(models):
    app = App([{'role': role(('a', DOWNLOAD, 'mac'))}])
    app.downloads = Queue()
    turn = shelf.TurnOn(app)
    turn.start('cells')
    app.states = [{'role': role()}]
    turn.advance()
    assert app.downloads.rows[0]['state'] == 'cancelled'
    assert turn.wire()['cells']['state'] == 'failed'


def test_disappearing_option_cannot_prove_later_when_the_slot_frees(tmp_path, monkeypatch):
    from orchestrator import commands, options, model_checks

    performed = []
    async def probe(self, entries):
        performed.extend(entries)
        return [{'state': 'works'}]
    monkeypatch.setattr(model_checks.Checker, 'run', probe)  # functional-probe boundary; no model runs
    app = Rig(tmp_path).app
    app.hub.settings.update({'outline': 'edgetam'})
    app.hub.checks.running = {'by': 'other', 'now': 'Other check', 'done': 0, 'of': 1}
    async def run():
        app._loop = asyncio.get_running_loop()
        await commands.capability_turn_on(app, None, {'id': 'outline'})
        await asyncio.sleep(.01)
        monkeypatch.setitem(options.GROUPS, 'outline', {k: v for k, v in options.GROUPS['outline'].items() if k != 'edgetam'})
        app.turn_on.advance()
        app.hub.checks.running = None
        await asyncio.sleep(.15)
        assert not performed, 'a withdrawn option cannot resume a queued functional check'
        await app.stop()
    asyncio.run(run())


@pytest.mark.parametrize('progress', ['Checking it on the public synthetic example', 'Waiting for the current functional check'])
def test_owned_functional_check_projects_yellow_until_proven(models, progress):
    turning = {'job': 'cells', 'option': 'a', 'state': 'running', 'says': progress}
    actual = shelf.card(role(('a', READY, 'mac'), proven=False), 'Cells', '', '', [], [], turning)
    assert actual['colour'] == 'yellow' and progress.lower() in actual['word'].lower()
    completed = shelf.card(role(('a', READY, 'mac'), proven=True), 'Cells', '', '', [], [], turning)
    assert completed['colour'] == 'green', 'a current passed option still takes precedence over a retained progress tick'


def test_check_again_runs_only_the_disclosed_option_despite_an_existing_pass(tmp_path):
    from orchestrator import commands, model_checks

    app = Rig(tmp_path).app
    app.hub.settings.update({'outline': 'edgetam'})
    app.hub.checks.record(model_checks.find('outline', 'edgetam'), 'works', 'old pass', by='synthetic test')
    row = next(r for r in app.capabilities_wire()['models'] if r['id'] == 'edgetam')
    setup = next(s for s in row['setups'] if s['role'] == 'outline')
    assert setup['recheck']
    async def run():
        app._loop = asyncio.get_running_loop()
        result = await commands.capability_turn_on(app, None, {'id': 'outline', 'option': 'edgetam',
                                                    'plan': setup['plan'], 'recheck': True})
        assert result['state'] == 'running'
        await until_async(lambda: not app.turn_on.wire())
        assert not app.turn_on.wire()
        await app.stop()
    asyncio.run(run())
    assert len([1 for op, _ in app.hub.gpu.calls if op == 'segment']) == 1
