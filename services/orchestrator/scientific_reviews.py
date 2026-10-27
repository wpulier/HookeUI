from __future__ import annotations

import asyncio
import copy
from concurrent.futures import Future
from dataclasses import dataclass
import queue
from secrets import token_urlsafe, compare_digest
from hashlib import sha256
import re
import threading
import time

from . import owned_process, scientific_rebase
from .workspace_observation import ObservationError
from .scientific_history import HistoryUnavailable, ScientificHistory
from .scientific_projects import InvalidProject
from .scientific_saves import LayerSave, SaveConflict, SaveNotStarted, SaveUnavailable
from .scientific_group_save import LayersSave
from .scientific_outputs import OutputSave
from .scientific_host import ChangeRefused, ControlFailed, reporting


# An open that reaches its bound is tried once more on its own (a cold runtime's first open can pass it on a busy Mac);
# each attempt keeps the bound, and the failed attempt's step and time are kept and said.
OPEN_SECONDS = 60
OPEN_ATTEMPTS = 2
# Each startup step as the studio says it: before the worker's first report, then its own steps
# (env_worker.scientific_host.STARTUP_STEPS).
STEP_WORDS = {None: 'Starting the tool’s Python', 'code': 'Loading the tool’s code', 'viewer': 'Opening the viewer',
              'layers': 'Restoring saved layers', 'tool': 'Opening the tool’s panel'}


def timed_out(error):
    return isinstance(error, owned_process.Stopped) and str(error).endswith('timed out')


def refused(message):
    raise ObservationError('SCIENTIFIC_REVIEW_UNAVAILABLE', message)


def failure_text(error):
    return '\n'.join([f'{type(error).__name__}: {error}', *getattr(error, '__notes__', ())])[:4096]


def same_document(first, origin):
    return (origin.project, origin.document, origin.revision, origin.digest, origin.placement) == (
        first.project, first.document, first.revision, first.digest, first.placement)


def same_session(first, origin):
    return origin.client is first.client and origin.profile is first.profile and same_document(first, origin)


def recovery_digest(key):
    if key is None:
        return None
    if type(key) is not str or re.fullmatch('[A-Za-z0-9_-]{32,128}', key) is None:
        refused('The private session recovery key is invalid')
    return sha256(key.encode()).digest()


@dataclass
class _Call:
    request: dict
    result: Future


class _Owner:
    def __init__(self, origin, open_worker, store):
        self.view_preferences = None
        self.export_data_changed = False
        self.view_notice = None
        self.id, self.origin = token_urlsafe(24), origin
        self.execution = origin
        self.recovery = None
        self.detached = False
        self.completion_task = None
        self.completed = None
        self.session_notice = None
        self.edits = {}
        self.sequence = 0
        self.stop = threading.Event()
        self.started, self.ended = Future(), Future()
        self.calls = queue.Queue(maxsize=1)
        self.lock = threading.Lock()
        self.busy = False
        self.navigation = None
        self.worker = self.running_cell = None
        self.frame = None
        self.native_frame = None
        self.history = None
        self.array_save = None
        self.save_inputs = None  # the inputs of an existing-layer save awaiting confirmed acceptance
        self.released_outputs = set()
        self.observation_available = False
        self.observation_due = 0.0
        self.background_error = None
        self.opening = None  # while it opens: the attempt, its step and when each began (opening_wire)
        self.thread = threading.Thread(target=self._run, args=(open_worker, store),
                                       name='scientific-review-owner', daemon=True)

    def submit(self, fields):
        with self.lock:
            if self.stop.is_set():
                refused('This scientific review stopped; reopen it to run again')
            result = Future()
            self.calls.put_nowait(_Call(fields, result))
            return result

    def interrupt(self, cell):
        with self.lock:
            if self.stop.is_set() or self.worker is None or self.running_cell != cell:
                return False
            return self.worker.interrupt_python()

    def _observe_idle(self, worker):
        # The parent must read the protocol while pumping Qt: queued callbacks
        # can request authorized lazy regions. Never pump in an unserviced child
        # or on a second thread. UI drafts/visibility govern painting, not work.
        if (not self.observation_available or self.background_error is not None
                or time.monotonic() < self.observation_due):
            return
        self.observation_due = time.monotonic() + 1.5
        try:
            with owned_process.scope(60, self.stop.is_set):
                reply, _ = worker.request({'op': 'observe'})
                if self.frame is not None and reply.get('imageFrame') != self.frame[0]['frame']:
                    self.export_data_changed = True
        except ControlFailed as error:
            # Keep bounded text, not an exception traceback retaining arrays.
            # Pause background delivery until the observer receives the error;
            # inspection and recovery remain possible without replaying a run.
            self.export_data_changed = True
            self.background_error = failure_text(error)
            self.session_notice = ('A background plugin callback failed. Inspect private work before continuing. '
                                   + self.background_error)[:4096]

    def _starting(self, step):
        # Replaced whole, never changed in place: the app's loop reads it while this thread writes it.
        self.opening = {**self.opening, 'step': step, 'stepAt': time.monotonic()}

    def opening_wire(self):
        """What the studio shows while it opens: the step, the seconds so far, the attempt, and a failed attempt's step
        and time. None once it is open."""
        opening = self.opening
        if opening is None or self.started.done():
            return None
        now = time.monotonic()
        return {'step': opening['step'], 'says': STEP_WORDS[opening['step']],
                'seconds': round(now - opening['at'], 1), 'stepSeconds': round(now - opening['stepAt'], 1),
                'attempt': opening['attempt'], 'attempts': OPEN_ATTEMPTS, 'limitSeconds': OPEN_SECONDS,
                **({'previous': opening['previous']} if opening.get('previous') else {})}

    def _open(self, open_worker):
        """The worker, opened within OPEN_SECONDS, tried once more when the first attempt reaches the bound. Both
        failing is said with each attempt's step and time; any other failure is said at once, never retried."""
        previous = None
        for attempt in range(1, OPEN_ATTEMPTS + 1):
            at = time.monotonic()
            self.opening = {'attempt': attempt, 'at': at, 'step': None, 'stepAt': at,
                            **({'previous': previous} if previous else {})}
            opened = None
            try:
                with owned_process.scope(OPEN_SECONDS, self.stop.is_set), reporting(self._starting):
                    context = open_worker(self.execution)
                    opened = (context, *context.__enter__())
                return opened
            except owned_process.Stopped as error:
                if opened is not None:
                    return opened  # it opened as the bound passed: open, so kept (and closed by its owner)
                if not timed_out(error) or self.stop.is_set():
                    raise
                step = self.opening['step']
                previous = {'step': step, 'says': STEP_WORDS[step], 'seconds': round(time.monotonic() - at, 1)}
                if attempt == OPEN_ATTEMPTS:
                    refused(f'The tool did not open in {OPEN_SECONDS} s, twice. The first attempt stopped at '
                            f'“{self.opening["previous"]["says"]}” after {self.opening["previous"]["seconds"]:.0f} s, the '
                            f'second at “{previous["says"]}” after {previous["seconds"]:.0f} s. Your saved work is '
                            'unchanged. Close other apps or tools that are working, then open it again.')

    def _run(self, open_worker, store):
        context, entered, current, failure = None, False, None, None
        try:
            context, saved, worker = self._open(open_worker)
            entered = True
            with owned_process.scope(OPEN_SECONDS, self.stop.is_set):
                if saved.digest != self.execution.digest:
                    refused('The saved scientific input changed; reopen its exact revision')
            self.history = ScientificHistory(store, saved, self.id,
                                             tab=self.execution.tab, placement=self.execution.placement)
            from . import envs, local_workflow
            try:
                local_observation = local_workflow.observe(worker, saved, stopping=self.stop.is_set)
            except (OSError, ValueError, envs.Failed, owned_process.Stopped) as error:
                local_observation = None
                self.view_notice = f'Local tool readiness could not be observed: {error}. Your work remains available.'
            track = scientific_rebase.Track(saved)
            self.started.set_result(True)
            while not self.stop.is_set():
                try:
                    current = self.calls.get(timeout=.05)
                except queue.Empty:
                    self._observe_idle(worker)
                    continue
                fields = dict(current.request)
                navigation_id = fields.pop('_navigation', None)
                navigation = self.navigation if navigation_id is not None else None
                action_context = fields.pop('_context', {})
                cell = fields.pop('cell', None) if fields['op'] in ('python', 'python-resources') else None
                if fields['op'] in ('python-resources', 'python-assist', 'layer-inventory', 'observe', 'family-drafts', 'save-families'):
                    fields.pop('resource')  # Checked against the owner; never grants a worker a new input.
                try:
                    if fields['op'] == 'observe' and self.background_error is not None:
                        error, self.background_error = self.background_error, None
                        self.observation_due = time.monotonic() + 1.5
                        raise ControlFailed(error)
                    if fields['op'] == 'family-drafts':
                        result = ({'ok': True, 'drafts': worker.family_drafts()}, b'')
                    elif fields['op'] == 'save-families':
                        # Drafts join the newest revision; any input they name resolves to the accepted input
                        # this session's layer came from, and the worker refuses if that layer has unsaved changes.
                        from .scientific_family_saves import FamilySave
                        candidate = FamilySave(store, store.read(saved.id, saved.session), fields['request'],
                                               fields['names'], inputs=track.bases, producer=self.history.producer())
                        if self.array_save is not None and getattr(self.array_save, 'kind', None) != 'new-output':
                            self.array_save.close()  # a confirmed save this one follows
                            self.array_save = None
                        with owned_process.scope(60, self.stop.is_set):
                            candidate.capture(worker)
                        self.array_save = candidate
                        result = ({'ok': True, 'arraySave': candidate.publish(self.stop.is_set)}, b'')
                        if candidate.receipt is not None:
                            track.accepted(candidate.receipt, [])
                    elif fields['op'] in ('save-layer', 'save-resource', 'save-resources', 'save-output', 'save-outputs', 'save-tool'):
                        if fields['request'] in self.released_outputs:
                            raise InvalidProject('This output was already saved. Open its retained document instead of repeating the request')
                        # An existing layer is saved on the newest revision when that revision still binds the
                        # input its values came from; otherwise nothing is saved and each layer's state is said.
                        plan = (track.plan(store, fields)
                                if fields['op'] in ('save-layer', 'save-resource', 'save-resources') else None)
                        base = saved if plan is None else plan.base
                        candidate = (OutputSave(store, saved, fields['request'], fields['outputs'][0]['layer'], fields['outputs'][0]['meaning'], producer=self.history.producer(), selections=fields['outputs'], tool=fields['op'] == 'save-tool')
                                     if fields['op'] in ('save-outputs', 'save-tool') else
                                     OutputSave(store, saved, fields['request'], fields['layer'], fields['meaning'], producer=self.history.producer())
                                     if fields['op'] == 'save-output' else LayersSave(store, base, fields['request'], fields['layers'], producer=self.history.producer())
                                     if fields['op'] == 'save-resources' else
                                     LayerSave(store, base, fields['request'], fields.get('_frame'), fields['layer'],
                                               producer=self.history.producer(), input=fields.get('input')))
                        if self.array_save is not None and getattr(self.array_save, 'kind', None) != 'new-output':
                            self.array_save.close()  # a confirmed save, or a conflict this save replaces
                            self.array_save = None
                        if plan is not None:
                            candidate.granular, candidate.onto = plan.rows, plan.onto
                            if plan.conflict:
                                candidate._conflict()
                                self.array_save = candidate
                                raise SaveConflict(plan.message())
                        try:
                            with owned_process.scope(candidate.capture_seconds if isinstance(candidate, OutputSave) else 60, self.stop.is_set):
                                candidate.capture(worker)
                        except BaseException:
                            if candidate.failure == 'conflict':
                                self.array_save = candidate
                            else:
                                candidate.close()
                            raise
                        self.array_save = candidate
                        # Kept until acceptance is confirmed, so a retried publication records it too.
                        self.save_inputs = None if plan is None else plan.inputs
                        result = ({'ok': True, 'arraySave': candidate.publish(self.stop.is_set)}, b'')
                        if self.save_inputs is not None and candidate.receipt is not None:
                            track.accepted(candidate.receipt, self.save_inputs)
                            self.save_inputs = None
                    elif fields['op'] == 'save-release':
                        if fields['request'] in self.released_outputs:
                            result = ({'ok':True,'arraySave':None},b'')
                        elif (self.array_save is None or getattr(self.array_save,'kind',None) != 'new-output'
                              or self.array_save.identity != fields['request'] or self.array_save.receipt is None):
                            raise InvalidProject('Continue only after the new output is confirmed saved')
                        elif len(self.released_outputs) >= 64:
                            raise InvalidProject('This session reached its output allowance. Open saved Data to continue')
                        else:
                            clean = self.array_save.release()
                            self.released_outputs.add(fields['request'])
                            self.array_save=None
                            result = ({'ok':True,'arraySave':None, **({'cleanupNotice':'The output is saved. Temporary-file cleanup will retry; your private work remains available.'} if not clean else {})},b'')
                    elif fields['op'] == 'save-copy':
                        if self.array_save is None or fields['request'] != self.array_save.identity:
                            raise InvalidProject('Copy the original conflicting save without changing its identity')
                        with owned_process.scope(60, self.stop.is_set):
                            outcome = self.array_save.copy(worker, fields.get('_frame'), self.stop.is_set)
                        result = ({'ok': True, 'arraySave': outcome}, b'')
                    elif fields['op'] == 'save-retry':
                        if self.array_save is None or fields['request'] != self.array_save.identity:
                            raise InvalidProject('Retry the original array save without changing its identity')
                        result = ({'ok': True, 'arraySave': self.array_save.publish(self.stop.is_set)}, b'')
                        if self.save_inputs is not None and self.array_save.receipt is not None:
                            track.accepted(self.array_save.receipt, self.save_inputs)
                            self.save_inputs = None
                    elif fields['op'] == 'save-status':
                        result = ({'ok': True, 'arraySave': self.array_save.status() if self.array_save else None}, b'')
                    elif fields['op'] == 'history-save':
                        reference = self.history.retry_save()
                        recovered = self.history.recovered_edits()
                        result = ({'ok': True, 'history': reference, '_recovered_edits': recovered,
                                   'pythonRecovery': self.history.undispatched_cell()}, b'')
                    elif cell is not None and not self.history.begin(cell, fields.get('source')):
                        previous = self.history.lookup(cell, fields['source'])
                        if previous['state'] == 'not-dispatched':
                            raise HistoryUnavailable('This code was never run. Submit a new cell if you still want to execute it.')
                        if previous['state'] != 'completed':
                            raise HistoryUnavailable('This code may already have run. Its outcome is unconfirmed; inspect its saved history before starting a new cell.')
                        result = ({'ok': True, 'console': previous['transcript'], 'history': self.history.reference()}, b'')
                    else:
                        action_id = None
                        if fields['op'] in ('labels', 'native-panel-change') or (fields['op'] == 'change' and
                                fields['request'].get('operation') == 'controls'):
                            action_id = fields['request'] if fields['op'] == 'labels' else token_urlsafe(24)
                            if not self.history.begin_action(action_id, fields, action_context):
                                previous = self.history.action_outcome(action_id)
                                current.result.set_result(({'ok': True, **previous, 'history': self.history.reference()}, b''))
                                current = None
                                continue
                        try:
                            with owned_process.scope(60, self.stop.is_set):
                                # Executed Python may mutate view state even when its cell ends in an error.
                                # Never apply older presentation intent over that newer private work.
                                if (cell is not None or fields['op'] == 'native-panel-change') and self.view_preferences is not None:
                                    self.view_preferences = None
                                    actor = 'Python has run' if cell is not None else 'the plugin panel was used'
                                    self.view_notice = f'Remembered view was not applied because {actor}. Your current view changes are preserved; saved view settings remain available when reopening.'
                                with self.lock:
                                    self.worker, self.running_cell = worker, cell
                                try:
                                    if navigation_id is not None:
                                        if navigation is None or navigation.identity != navigation_id:
                                            raise ChangeRefused('The navigation owner changed')
                                        result = worker.navigate(fields, navigation)
                                    else:
                                        result = worker.request(fields)
                                finally:
                                    with self.lock:
                                        self.running_cell = None
                                if fields['op'] == 'render' and self.view_preferences is not None:
                                    from .scientific_host import RestoreRefused
                                    try:
                                        result = worker.request({'op': 'change', 'width': fields['width'], 'height': fields['height'],
                                            'request': {'frame': result[0]['frame']['frame'], 'operation': 'restore-view',
                                                        'value': self.view_preferences}})
                                    except RestoreRefused as error:
                                        result = ({**result[0], 'viewNotice':
                                            'Remembered view was not applied. Current layers are shown; saved view settings and private work remain. ' + str(error)}, result[1])
                                    self.view_preferences = None
                                if fields['op'] == 'render' and self.view_notice is not None:
                                    result = ({**result[0], 'viewNotice': self.view_notice}, result[1])
                                    self.view_notice = None

                        except BaseException as error:
                            if action_id is not None:
                                try:
                                    not_applied = isinstance(error, ChangeRefused)
                                    self.history.finish_action(action_id,
                                        {'state': 'not-applied' if not_applied else 'unconfirmed'}, interrupted=not not_applied)
                                except (HistoryUnavailable, ValueError) as save_error:
                                    error.add_note(str(save_error))
                            if cell is not None:
                                try:
                                    self.history.finish(cell, interrupted=True)
                                except (HistoryUnavailable, ValueError) as save_error:
                                    error.add_note(str(save_error))
                            raise
                        if action_id is not None:
                            outcome = {'edit': result[0]['edit']} if fields['op'] == 'labels' else {'state': 'completed'}
                            reference = self.history.finish_action(action_id, outcome)
                            result = ({**result[0], 'history': reference}, result[1])
                        if cell is not None:
                            reference = self.history.finish(cell, result[0].get('console'))
                            result = ({**result[0], 'history': reference}, result[1])
                except (HistoryUnavailable, SaveUnavailable, InvalidProject, ChangeRefused, ControlFailed) as error:
                    if isinstance(error, SaveNotStarted) and self.array_save is not None and self.array_save.edit is None:
                        self.array_save.close()
                        self.array_save = None
                    current.result.set_exception(error)
                else:
                    if local_observation is not None:
                        try:
                            if (fields['op'] in ('save-tool', 'save-retry') and self.array_save is not None
                                    and getattr(self.array_save, 'tool', False) and self.array_save.receipt is not None):
                                local_observation.saved_tool(self.array_save.receipt, self.history)
                            elif fields['op'] == 'render':
                                local_observation.rendered(self.history)
                        except (OSError, ValueError, envs.Failed, owned_process.Stopped) as error:
                            result = ({**result[0], 'viewNotice':
                                f'Local tool readiness evidence was not completed: {error}. Saved work remains saved.'}, result[1])
                    reply = result[0]
                    if fields['op'] in ('render', 'layer-inventory'):
                        self.observation_available = reply.get('frame', reply).get('nativePanelAvailable') is True
                    elif fields['op'] == 'native-panel-render':
                        self.observation_available = True
                    if fields['op'] in ('observe', 'render', 'native-panel-render', 'native-panel-change'):
                        self.observation_due = time.monotonic() + 1.5
                    current.result.set_result(result)
                current = None
        except BaseException as error:
            if not (isinstance(error, owned_process.Stopped) and self.stop.is_set()):
                failure = error
        finally:
            self.stop.set()
            cleanup = []
            if self.array_save is not None:
                cleanup.append(self.array_save.close)
            if entered:
                cleanup.append(lambda: context.__exit__(None, None, None))
            for release in cleanup:
                try:
                    release()
                except BaseException as error:
                    if failure is None:
                        failure = error
                    else:
                        failure.add_note(f'Scientific cleanup also failed: {error}')
            error = failure or RuntimeError('This scientific review stopped; reopen it to run again')
            if not self.started.done():
                self.started.set_exception(error)
            if current is not None and not current.result.done():
                current.result.set_exception(error)
            with self.lock:
                while not self.calls.empty():
                    self.calls.get_nowait().result.set_exception(error)
            self.ended.set_result(failure)


class FrameDelivery:
    def __init__(self, pixels, reservation, check, released):
        self.pixels = pixels
        self._reservation, self._check, self._released = reservation, check, released

    async def check(self):
        if self._reservation is None:
            refused('This scientific frame delivery ended; request its current preview')
        await self._check()

    def release(self):
        if self._reservation is not None:
            self.pixels = b''
            self._reservation.release()
            self._reservation = None
            released = self._released
            self._check = self._released = None
            released()


class ScientificReviews:
    """Call on one app loop after admitting a ReviewOrigin from the canonical Screen.

    Layout reads are server-owned and run off-loop. Every request checks the accepted
    connection, active Project and exact window again. Workers and their leases live
    on one owner thread through shutdown; cancelling an awaiting task cannot orphan it.
    """
    def __init__(self, screen, store, read_layout, active_project):
        self.screen, self.store = screen, store
        self.read_layout, self.active_project = read_layout, active_project
        self._owners = {}
        self._closing = False
        self._failures = []
        self._deliveries = 0
        self._completion_tasks = set()
        self.ended_hooks = []  # called with a session's identity when it ends (a notebook grant ends with it)

    def _ended(self, owner):
        owner.frame = None
        owner.native_frame = None
        if self._owners.pop(owner.id, None) is None:
            return
        for hook in self.ended_hooks:
            hook(owner.id)
        error = owner.ended.result()
        if error is not None:
            self._failures.append(failure_text(error))
            self._failures = self._failures[-32:]

    async def _wait(self, future):
        pending = asyncio.wrap_future(future)
        pending.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(pending)

    def _open_worker(self, origin):
        from .scientific_project_review import review
        return review(self.store, origin.document, origin.project, origin.revision)

    def _connected(self, owner):
        if self._closing or owner.stop.is_set() or self.screen._accepted.get(owner.execution.client) is not owner.execution.profile:
            refused('The original workspace connection ended; reopen this scientific review')

    async def _check(self, owner, *, snapshot=None, draw=None, interaction=False, origin=None, native=False, notebook=False, navigation=None):
        if notebook:
            # A connected notebook acts through its grant for this exact session and saved input. It never depends
            # on what the Studio shows, which tab or Project is active, or whether the Studio is in front.
            if self._closing or owner.stop.is_set():
                refused('This scientific session ended; reopen it in Hooke and connect the notebook again')
            return None
        origin = origin or owner.origin
        layout = await asyncio.to_thread(self.read_layout, origin.project)
        if self._closing or owner.stop.is_set() or self.active_project() != origin.project:
            refused('Return to the originating Project and reopen this scientific review')
        pin = snapshot or self.screen.pin(origin.client, request_id='scientific-review')
        arguments = {}
        if interaction:
            shown = navigation.frame if navigation is not None and owner.navigation is navigation else owner.native_frame if native else owner.frame
            if shown is None:
                refused('Render a fresh scientific preview before interacting')
            arguments = {'review': owner.id, 'frame': shown[0], 'draw': draw,
                         **({'presentation': 'native-panel'} if native else {})}
        return origin.check(self.screen, pin, layout, **arguments)

    async def _refresh_completion(self, owner):
        """Long work can outlive the normal screen heartbeat; ask, never invent freshness.

        Only the initiating connection's new observation counts. Admission still uses
        its original snapshot, and the ordinary origin check runs after this wait.
        No worker request is repeated to recover a completed result.
        """
        from .screen import CONFIRM_S
        self._connected(owner)
        pin = self.screen.pin(owner.origin.client, request_id='scientific-completion')
        if pin.report_age_ms <= 1000:
            return
        before = pin.observation
        self.screen.broadcast({'type': 'screen-ask'})
        deadline = asyncio.get_running_loop().time() + CONFIRM_S
        while True:
            self._connected(owner)
            current = self.screen.observation(owner.origin.client)
            if current is not before:
                return  # _check validates visibility, identity, age and arrangement.
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                refused('The scientific view did not answer. Resume its saved window to inspect completed work; do not rerun it.')
            await asyncio.sleep(min(.025, remaining))

    def _owner(self, client, identity):
        owner = self._owners.get(identity) if type(identity) is str else None
        if owner is None or owner.detached or owner.origin.client is not client:
            refused('This scientific review does not belong to this workspace connection')
        return owner

    async def open(self, origin, *, default_view=False, recovery_key=None):
        if type(default_view) is not bool:
            refused('Choose whether to start with the integration’s default view')
        if self._closing:
            refused('This application is stopping; reopen its saved scientific work after restart')
        recovery = recovery_digest(recovery_key)
        recoverable = next((row for row in self._owners.values() if recovery is not None
                            and row.recovery is not None and compare_digest(row.recovery, recovery)
                            and not row.stop.is_set()), None)
        if recoverable is not None:
            if not same_document(recoverable.execution, origin) or recoverable.execution.tab != origin.tab:
                refused('Resume the exact original scientific document and work Tab')
            if not same_session(recoverable.execution, origin):
                if not recoverable.detached:
                    refused('This scientific session is still owned by its connected workspace')
                if default_view or recoverable.busy or not recoverable.started.done():
                    refused('Private work is retained. Wait for its current action, then resume the original view')
                await self._check(recoverable, origin=origin)
                if not recoverable.detached or recoverable.busy or recoverable.stop.is_set():
                    refused('The scientific session changed while reconnecting; check it again')
                from dataclasses import replace
                recoverable.execution = replace(recoverable.execution, client=origin.client, profile=origin.profile)
                recoverable.origin = origin
                recoverable.detached = False
                recoverable.frame = recoverable.native_frame = None
                return recoverable.id
        if any(row.detached and same_document(row.execution, origin) and not row.stop.is_set()
               for row in self._owners.values()):
            refused('This private session needs its original browser recovery key. Do not rerun the analysis')
        existing = next((row for row in self._owners.values() if row.origin == origin and not row.stop.is_set()), None)
        if existing is not None:
            if default_view:
                refused('Stop the existing scientific session before starting a new default view')
            await self._check(existing)
            await self._wait(existing.started)
            await self._check(existing)
            return existing.id
        if any(same_session(row.execution, origin) and not row.stop.is_set() for row in self._owners.values()):
            refused('Resume the existing scientific session for this saved window')
        if len(self._owners) >= 4:
            refused('Stop an existing scientific review before opening another')
        owner = _Owner(origin, self._open_worker, self.store)
        owner.recovery = recovery
        loop = asyncio.get_running_loop()
        owner.ended.add_done_callback(lambda _: loop.call_soon_threadsafe(self._ended, owner))
        self._owners[owner.id] = owner
        try:
            await self._check(owner)
            if not default_view:
                from .scientific_workspace import _placement
                from env_worker.scientific_view_preferences import preferences
                layout = await asyncio.to_thread(self.read_layout, origin.project)
                row = _placement(layout, origin.tab, origin.placement, origin.reference)
                settings = row.get('display', {}).get('scientific')
                if settings is not None:
                    owner.view_preferences = preferences(settings)
            await self._check(owner)
            owner.thread.start()
            await self._wait(owner.started)
            self._connected(owner)
            return owner.id
        except BaseException as error:
            if (owner.recovery is not None and owner.thread.ident is not None
                    and (owner.detached or isinstance(error, asyncio.CancelledError))):
                raise
            owner.stop.set()
            if owner.thread.ident is not None:
                await asyncio.shield(asyncio.wrap_future(owner.ended))
            else:
                if not owner.started.done():
                    owner.started.set_exception(RuntimeError('Scientific review opening ended before its worker started; retry from its saved window'))
                if not owner.ended.done():
                    owner.ended.set_result(None)
            # Ended here, its failure kept for close(), never only by the callback ended scheduled: on Python 3.13.15
            # and later the wait above can return before that callback runs, and it then finds no owner to record.
            self._ended(owner)
            raise

    async def request(self, client, identity, request, *, snapshot=None, draw=None, notebook=False):
        owner = self._owner(client, identity)
        if type(request) is dict and request.get('op') == 'edit-status':
            from .scientific_history import CELL
            if set(request) != {'op','request'} or type(request['request']) is not str or CELL.fullmatch(request['request']) is None:
                refused('Check the original edit identity')
            await self._check(owner)
            outcome = owner.edits.get(request['request'])
            if outcome is None:
                outcome = {'request': request['request'], 'state': 'submitted'} if owner.busy else {
                    'request': request['request'], 'state': 'not-admitted', 'changed': False, 'label': None,
                    'message': 'This gesture was not admitted and changed nothing. Inspect the refreshed preview before making a new edit.'}
            if outcome['state'] == 'not-admitted' and len(owner.edits) < 128:
                owner.edits[request['request']] = copy.deepcopy(outcome)
            return {'ok': True, 'edit': copy.deepcopy(outcome)}
        if type(request) is dict and request.get('op') == 'python-interrupt':
            from .scientific_history import CELL
            if set(request) != {'op', 'cell'} or type(request['cell']) is not str or not CELL.fullmatch(request['cell']):
                refused('Interrupt needs the exact running Python cell')
            await self._check(owner, notebook=notebook)
            sent = await asyncio.to_thread(owner.interrupt, request['cell'])
            return {'ok': True, 'interrupt': {'cell': request['cell'], 'requested': sent}}
        if type(request) is dict and request.get('op') == 'navigation-latest':
            navigation = owner.navigation
            if (not owner.busy or navigation is None or notebook
                    or set(request) != {'op', 'navigation', 'sequence', 'value'}
                    or request['navigation'] != navigation.identity or draw != navigation.draw):
                refused('Return to the same active dimension navigation before replacing its target')
            await self._check(owner, snapshot=snapshot, draw=draw, interaction=True, navigation=navigation)
            if owner.navigation is not navigation or not owner.busy:
                refused('This dimension navigation ended')
            sequence = navigation.replace(request['sequence'], request['value'])
            return {'ok': True, 'navigation': {'id': navigation.identity, 'sequence': sequence, 'state': 'pending'}}
        if owner.busy:
            # Its own code, so a live view can wait for a connected notebook's cell instead of pausing.
            raise ObservationError('SCIENTIFIC_REVIEW_BUSY', 'Wait for this scientific interaction to finish before starting another')
        if type(request) is not dict or request.get('op') not in ('render', 'change', 'pick', 'inspect', 'python', 'python-resources', 'python-assist', 'history-save', 'save-layer', 'save-resource', 'save-resources', 'save-output', 'save-outputs', 'save-tool', 'save-release', 'save-status', 'save-retry', 'save-copy', 'labels', 'layer-inventory', 'observe', 'native-panel-render', 'native-panel-change', 'family-drafts', 'save-families'):
            refused('Choose a declared scientific interaction')
        if request['op'] == 'inspect':
            from env_worker.scientific_inspection import command
            command(request)
        if request['op'] == 'labels':
            from env_worker.scientific_label_wire import command
            command(request)
            if request['request'] in owner.edits:
                refused('This edit was already submitted. Check its outcome; do not replay the gesture.')
            if len(owner.edits) >= 128:
                refused('This session reached its manual edit allowance. Save changes and open the saved revision to continue.')
        if request['op'] == 'history-save' and set(request) != {'op'}:
            refused('Retry saving history without changing its contents')
        if request['op'] in ('save-layer', 'save-resource', 'save-resources', 'save-output', 'save-outputs', 'save-tool', 'save-release', 'save-retry', 'save-copy'):
            from .scientific_history import CELL
            expected = ({'op','request','resource','outputs'} if request['op'] in ('save-outputs', 'save-tool') else {'op','request','resource','layer','meaning'} if request['op'] == 'save-output' else {'op', 'request', 'resource', 'layers'} if request['op'] == 'save-resources' else
                        {'op', 'request', 'resource', 'layer', 'input'} if request['op'] == 'save-resource' else
                        {'op', 'request', 'frame', 'layer'} if request['op'] == 'save-layer' else
                        {'op', 'request', 'frame'} if request['op'] == 'save-copy' else {'op', 'request'})
            if (set(request) != expected or type(request.get('request')) is not str
                    or CELL.fullmatch(request['request']) is None):
                refused('Save these array changes with their original request identity')
        layer_saves = ('save-layer', 'save-resource', 'save-resources')
        # A confirmed save of existing layers leaves the session's other private work open: its inputs are
        # tracked, so a later save is placed by them (scientific_rebase). A granular conflict still lets
        # the unaffected layers be saved on their own. Copies and new outputs keep their explicit release.
        confirmed = (owner.array_save is not None and getattr(owner.array_save, 'kind', None) != 'new-output'
                     and owner.array_save.receipt is not None and owner.array_save.copy_receipt is None)
        regranular = (owner.array_save is not None and owner.array_save.failure == 'conflict'
                      and any(row['state'] == scientific_rebase.UNCHANGED
                              for row in getattr(owner.array_save, 'granular', None) or ())
                      and request['op'] in layer_saves)
        if (owner.array_save is not None and not confirmed and not regranular
                and request['op'] not in ('render', 'save-status', 'save-retry', 'save-copy', 'save-release', 'history-save', 'layer-inventory', 'python-assist', 'family-drafts')):
            if owner.array_save.failure == 'conflict':
                refused('A newer revision was accepted. Your edits remain in this private view; inspect the newer saved revision before continuing')
            refused('Finish saving these array changes and open the accepted revision before making further edits')
        if request['op'] in ('python', 'python-resources'):
            from env_worker.scientific_console import source
            from .scientific_history import CELL
            authority = 'resource' if request['op'] == 'python-resources' else 'frame'
            if (set(request) != {'op', authority, 'source', 'cell'} or type(request['cell']) is not str
                    or CELL.fullmatch(request['cell']) is None):
                refused('Run this Python cell with its original request identity')
            source(request['source'])
            if authority == 'resource':
                reference = request['resource']
                if (type(reference) is not dict or type(reference.get('version')) is not int
                        or reference != owner.execution.reference):
                    refused('Python inputs belong to another saved revision. Reopen the intended scientific document.')
        if request['op'] == 'python-assist':
            from env_worker.scientific_python_help import query
            if (set(request) != {'op', 'mode', 'expression', 'resource'} or type(request['resource']) is not dict
                    or type(request['resource'].get('version')) is not int
                    or request['resource'] != owner.execution.reference):
                refused('Python assistance belongs to the exact originating scientific revision')
            query(request['mode'], request['expression'])
        if request['op'] in ('layer-inventory', 'observe'):
            if (set(request) != {'op', 'resource'} or type(request['resource']) is not dict
                    or type(request['resource'].get('version')) is not int
                    or request['resource'] != owner.execution.reference):
                refused('Layer inventory belongs to the exact originating scientific revision')
        if request['op'] in ('family-drafts', 'save-families'):
            from .scientific_history import CELL
            expected = {'op', 'resource'} if request['op'] == 'family-drafts' else {'op', 'request', 'resource', 'names'}
            if (set(request) != expected or type(request['resource']) is not dict
                    or type(request['resource'].get('version')) is not int
                    or request['resource'] != owner.execution.reference):
                refused('Drafts belong to the exact originating scientific revision')
            if request['op'] == 'save-families' and (type(request['request']) is not str
                    or CELL.fullmatch(request['request']) is None or type(request['names']) is not list
                    or not 0 < len(request['names']) <= 16 or len(set(map(str, request['names']))) != len(request['names'])
                    or any(type(name) is not str for name in request['names'])):
                refused('Save the chosen drafts, each once, with their original request identity')
        if request['op'] == 'save-status' and set(request) != {'op'}:
            refused('Check the existing array save without changing its inputs')
        if request['op'] in ('save-outputs', 'save-tool'):
            from env_worker.scientific_output_wire import selections
            if (type(request['resource']) is not dict or type(request['resource'].get('version')) is not int
                    or request['resource'] != owner.execution.reference):
                refused('New outputs belong to the exact originating scientific revision')
            try: selections(request['outputs'], minimum=1 if request['op'] == 'save-tool' else 2)
            except ValueError as error: refused(str(error))
        if request['op'] == 'save-output':
            from .scientific_host import _token
            from env_worker.scientific_output_wire import declaration
            if (type(request['resource']) is not dict or type(request['resource'].get('version')) is not int
                    or request['resource'] != owner.execution.reference or not _token(request['layer'],32)):
                refused('New outputs belong to the exact originating scientific revision')
            try: declaration(request['meaning'])
            except ValueError as error: refused(str(error))
        if request['op'] in ('save-resource', 'save-resources'):
            from .scientific_host import _token
            reference = request['resource']
            if (type(reference) is not dict or type(reference.get('version')) is not int
                    or reference != owner.execution.reference):
                refused('Save needs the exact originating scientific revision and declared layer')
            layers = request['layers'] if request['op'] == 'save-resources' else [{'layer': request['layer'], 'input': request['input']}]
            from env_worker.native_regions import IDENTITY
            if (type(layers) is not list or not 1 <= len(layers) <= 64
                    or any(type(row) is not dict or set(row) != {'layer', 'input'}
                           or not _token(row['layer'], 32) or type(row['input']) is not str
                           or not IDENTITY.fullmatch(row['input']) for row in layers)):
                refused('Save needs distinct declared editable scientific layers')
        navigation = None
        if 'navigation' in request:
            from .scientific_navigation import Navigation
            action = request.get('request')
            if (notebook or set(request) != {'op', 'request', 'width', 'height', 'navigation'} or request['op'] != 'change' or type(action) is not dict
                    or set(action) != {'frame', 'operation', 'value'} or action['operation'] != 'dimensions'
                    or owner.frame is None or action['frame'] != owner.frame[0]['frame']):
                refused('Latest navigation requires the current rendered dimension controls')
            navigation = Navigation(request['navigation'], owner.frame, draw, action['value'])
        owner.busy = True
        owner.navigation = navigation
        dispatched = False
        reply_recorded = False
        try:
            native = request['op'] == 'native-panel-change'
            interaction = request['op'] not in ('render', 'native-panel-render', 'history-save', 'save-retry', 'python-resources', 'python-assist', 'save-resource', 'save-resources', 'save-output', 'save-outputs', 'save-tool', 'save-release', 'save-status', 'layer-inventory', 'observe', 'family-drafts', 'save-families')
            if request['op'] == 'save-copy':
                if owner.array_save is None or owner.array_save.failure != 'conflict':
                    refused('Choose a confirmed conflicting save before creating a separate document')
                interaction = owner.array_save.edit is None and owner.array_save.worker_frame is not None
            if notebook and (interaction or request['op'] not in ('python-resources', 'python-assist')):
                refused('A notebook may only run code, complete and get help in its session')
            await self._check(owner, snapshot=snapshot, draw=draw, interaction=interaction, native=native, notebook=notebook)
            fields = copy.deepcopy(request)
            if navigation is not None:
                fields.pop('navigation')
                fields['_navigation'] = navigation.identity
            if native:
                from env_worker import scientific_native_panel_wire
                if set(fields) != {'op', 'request'}:
                    refused('Use the declared native panel interaction')
                scientific_native_panel_wire.command(fields['request'], owner.native_frame[0])
                fields['_context'] = {'nativePanel': copy.deepcopy(owner.native_frame[0]),
                    'meaning': 'Private plugin interaction; never replay automatically'}
            if fields['op'] in ('python-resources', 'save-resource', 'save-resources', 'save-output', 'save-outputs', 'save-tool'):
                # Resource execution still revokes old pointer authority, even if
                # a silent in-place array mutation emits no napari event.
                owner.frame = None
            if fields['op'] == 'save-layer' or (fields['op'] == 'save-copy' and interaction):
                fields['_frame'] = copy.deepcopy(owner.frame[0])
            if interaction and not native:
                if fields['op'] in ('labels', 'change'):
                    fields['_context'] = {key: copy.deepcopy(owner.frame[0][key])
                        for key in ('frame', 'dimensions', 'camera', 'layers', 'width', 'height') if key in owner.frame[0]}
                    if fields['op'] == 'change' and type(fields.get('request')) is dict and fields['request'].get('operation') == 'controls':
                        fields['_context']['controls'] = copy.deepcopy(owner.frame[0].get('controls'))
                        fields['_context']['choiceMeaning'] = 'Declared labels and session-local identities; opaque Python values are not serialized or replayable'
                action = fields.get('request') if fields['op'] == 'change' else fields
                if type(action) is not dict or action.get('frame') != owner.frame[0]['frame']:
                    refused('The scientific frame changed; inspect its current preview')
                if fields['op'] != 'inspect':
                    owner.frame = None
            if fields['op'] in ('native-panel-render', 'native-panel-change'):
                if fields['op'] == 'native-panel-change':
                    owner.frame = None
                owner.native_frame = None
            elif fields['op'] in ('render', 'change', 'labels', 'python', 'python-resources',
                                   'save-layer', 'save-resource', 'save-resources', 'save-output', 'save-outputs', 'save-tool'):
                owner.native_frame = None
            if fields['op'] == 'labels':
                owner.edits[fields['request']] = {'request': fields['request'], 'state': 'submitted'}
            from .scientific_view_export import changes_data
            owner.export_data_changed = owner.export_data_changed or changes_data(fields)
            result = owner.submit(fields)
            dispatched = True
            reply, pixels = await self._wait(result)
            reply = self._record_completion(owner, fields, reply, pixels)
            reply_recorded = True
            if not notebook:
                await self._refresh_completion(owner)
            await self._check(owner, notebook=notebook)
            return copy.deepcopy(reply)
        except Exception as error:
            if isinstance(error, ControlFailed): owner.export_data_changed = True
            if dispatched and not reply_recorded and owner.detached and owner.recovery is not None:
                self._record_failure(owner, fields, error)
            if dispatched and request['op'] in ('observe', 'native-panel-render', 'native-panel-change'):
                owner.frame = owner.native_frame = None
            raise
        except asyncio.CancelledError:
            if owner.recovery is not None and dispatched:
                if not reply_recorded:
                    owner.completion_task = asyncio.create_task(self._finish_detached(owner, fields, result))
                    self._completion_tasks.add(owner.completion_task)
                    owner.completion_task.add_done_callback(self._completion_tasks.discard)
                raise
            owner.stop.set()
            await asyncio.shield(asyncio.wrap_future(owner.ended))
            self._ended(owner)
            raise
        finally:
            if owner.completion_task is None:
                owner.busy = False
                owner.navigation = None

    def _record_completion(self, owner, fields, reply, pixels):
        if fields['op'] in ('observe', 'native-panel-render') and 'imageFrame' in reply and owner.frame is not None and reply['imageFrame'] != owner.frame[0]['frame']:
            owner.export_data_changed = True  # Unsolicited plugin work is not an accepted input.
        if fields['op'] == 'history-save':
            from env_worker.scientific_label_wire import receipt
            for recovered in reply.pop('_recovered_edits', []):
                owner.edits[recovered['request']] = receipt(recovered, recovered['request'])
        if fields['op'] == 'labels':
            from env_worker.scientific_label_wire import receipt
            owner.edits[fields['request']] = receipt(reply.get('edit'), fields['request'])
        if fields['op'] not in ('render', 'native-panel-render', 'history-save', 'save-status', 'layer-inventory', 'python-assist', 'inspect', 'observe', 'family-drafts'):
            owner.sequence += 1
            owner.completed = {'sequence': owner.sequence, 'operation': fields['op'],
                'project': owner.origin.project, 'tab': owner.origin.tab, 'placement': owner.origin.placement,
                'document': owner.origin.reference, 'reply': copy.deepcopy(reply)}
        if 'frame' in reply:
            owner.frame = (copy.deepcopy(reply['frame']), pixels)
        if 'imageFrame' in reply and owner.frame is not None and reply['imageFrame'] != owner.frame[0]['frame']:
            owner.frame = None
        if 'nativePanel' in reply:
            owner.native_frame = (copy.deepcopy(reply['nativePanel']), pixels)
        if owner.array_save is not None or fields['op'] == 'layer-inventory':
            reply = {**reply, 'arraySave': owner.array_save.status() if owner.array_save else None}
        return reply

    def _record_failure(self, owner, fields, error):
        owner.session_notice = f"Earlier {fields['op']} request failed: {failure_text(error)}. Inspect the current view before another action. This request was not replayed."[:4096]

    async def _finish_detached(self, owner, fields, result):
        try:
            reply, pixels = await self._wait(result)
            self._record_completion(owner, fields, reply, pixels)
        except Exception as error:
            self._record_failure(owner, fields, error)
        finally:
            owner.frame = owner.native_frame = None
            owner.busy = False
            owner.navigation = None
            owner.completion_task = None

    def recovery_notice(self, client, identity):
        return self._owner(client, identity).session_notice

    def sessions(self, client):
        profile = self.screen._accepted.get(client)
        return [{'review': owner.id, 'document': owner.execution.reference,
                 'placement': owner.execution.placement, 'tab': owner.origin.tab,
                 'state': 'opening' if not owner.started.done() else 'running' if owner.busy else 'idle',
                 **({'opening': owner.opening_wire()} if not owner.started.done() and owner.opening else {}),
                 'history': owner.history.reference() if owner.history else None,
                 'arraySave': owner.array_save.status() if owner.array_save else None,
                 'completed': copy.deepcopy(owner.completed),
                 **({'sessionNotice': owner.session_notice} if owner.session_notice else {})}
                for owner in self._owners.values()
                if not owner.detached and owner.execution.client is client and owner.execution.profile is profile and not owner.stop.is_set()]

    async def attach(self, client, identity, origin):
        owner = self._owner(client, identity)
        if not same_session(owner.execution, origin):
            refused('Resume this review only from its exact saved document and original placement')
        if owner.busy or not owner.started.done():
            refused('Wait for this scientific operation to finish before resuming its window')
        owner.busy = True
        try:
            await self._check(owner, origin=origin)
            owner.origin = origin
            owner.frame = None
            owner.native_frame = None
            return identity
        finally:
            owner.busy = False

    async def frame(self, client, identity, frame, memory, *, native=False):
        owner = self._owner(client, identity)
        await self._check(owner)
        published = owner.native_frame if native else owner.frame
        if published is None or published[0]['frame'] != frame:
            refused('This scientific frame expired; render its current preview')
        if self._deliveries >= 8:
            refused('Wait for pending scientific previews to finish loading')
        pixels = published[1]
        reservation = memory.reserve('analysis', len(pixels), 'delivering a scientific preview')
        self._deliveries += 1

        async def check():
            await self._check(owner)
            if (owner.native_frame if native else owner.frame) is not published:
                refused('This scientific frame expired; render its current preview')

        def released():
            self._deliveries -= 1

        return FrameDelivery(pixels, reservation, check, released)

    async def stop(self, client, identity):
        owner = self._owner(client, identity)
        owner.stop.set()
        owner.frame = None
        owner.native_frame = None
        failure = await asyncio.shield(asyncio.wrap_future(owner.ended))
        if self._owners.pop(identity, None) is not None:
            for hook in self.ended_hooks:  # ended here, not only by the callback, whichever runs first
                hook(identity)
        return failure

    def gone(self, client):
        for owner in tuple(self._owners.values()):
            if owner.origin.client is client:
                owner.detached = True
                if owner.recovery is None:
                    owner.stop.set()
                owner.frame = None
                owner.native_frame = None
                if owner.ended.done():
                    self._ended(owner)

    async def close(self):
        self._closing = True
        for owner in self._owners.values():
            owner.stop.set()
            owner.frame = None
            owner.native_frame = None
        failures = self._failures[:]
        for owner in tuple(self._owners.values()):
            if owner.thread.ident is not None:
                error = await asyncio.shield(asyncio.wrap_future(owner.ended))
                if error is not None:
                    failures.append(failure_text(error))
        pending = list(self._completion_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._owners.clear()
        self._failures.clear()
        from .scientific_host import retry_cleanup
        failures.extend(failure_text(error) for error in await asyncio.to_thread(retry_cleanup))
        return failures
