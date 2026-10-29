"""Synthetic app-wiring regressions; TEST-ONLY, authored on 8698dd5b.

Run through App.build's Registry/Runner/COMMANDS/Screen and actual retained stores. No
server/lifespan, camera, worker, model, private recovery fixture or browser is started.
The legacy Studio is a synthetic transport/renderer, not browser acceptance evidence.

MISSING_FEATURE assertions name absent UUID/typed-output/command-fence app seams.
BEHAVIOR assertions follow those gates. The reconciled browser wire uses pageEpoch;
the server's completion adapter alone maps it to its legacy clientEpoch projection.
There are no xfails/skips or
fake production action handlers. This slice does not replace the 75-case/180-minute
campaign or reproduce user/evaluator wording in a product prompt.
"""
import asyncio
import copy
import hashlib
import json
import threading
import time
import weakref
from types import SimpleNamespace
from uuid import UUID

import cv2
import numpy as np
import pytest

from orchestrator.app import App
from orchestrator.brain.actions import ActionRunner
from orchestrator.brain.registry import ToolContext
from orchestrator.capture import Capture, RUN
from orchestrator.commands import COMMANDS
from orchestrator.paths import Paths
from orchestrator.research import Session
from orchestrator.scene import SceneObject
from orchestrator.ticket import generations, issue, using
from orchestrator.workspace_observation import ClientRef, ObservationError
from tests.test_evidence import population
from tests.test_screen import Studio
from tests.waiting import HANG_S


WORKSPACE = 'synthetic-workspace'
CLIENT = {'workspaceId': WORKSPACE, 'clientId': 'synthetic-client',
          'pageEpoch': 'synthetic-page', 'connectionEpoch': 'synthetic-socket'}
DISPLAY_KIND = {'live': 'magnifier', 'saved-image': 'image', 'composite': 'comparison',
                'video': 'video', 'lesson': 'lesson', 'written': 'written'}
CONTROL_PROPERTY = {'move': 'geometry', 'resize': 'geometry', 'zoom': 'zoom',
                    'pan': 'pan', 'front': 'front', 'close': 'open'}
STUDIOS = {}  # weak test-fixture handles, not another production observation registry


def jpeg(value=70, width=160, height=120):
    pixels = np.full((height, width, 3), value, np.uint8)
    pixels[10:30, 10:45] = [value, 220, 30]
    pixels[65:85, 90:125] = [210, value, 110]
    ok, encoded = cv2.imencode('.jpg', pixels)
    assert ok
    return encoded.tobytes()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def address(entry, picture=0, **extra):
    """The approved public present source address; explicit null legacy version."""
    return {'day': entry.day, 'id': entry.id, 'version': entry.version,
            'picture': picture, **extra}


class AppStudio(Studio):
    """Only replaces transport/render observations, never the action executor.

    Legacy controls use the existing test renderer. UUID commands need their actual
    causal fence and full target descriptors on the agreed command wire. Descriptors
    project actual PresentationState fields; the fixture never reads a newer state
    to manufacture acknowledgment of an older command.
    Independent browser measurements remain a later acceptance class.
    """
    def __init__(self, app):
        super().__init__(app.hub.screen)
        self.app, self.messages = app, []
        self.seq = 0
        self.draw = True
        self.load_state_override = None
        self.bad_payload = False
        self.source_override = None  # explicit negative renderer input; never repaired to desired source
        self.command_results = []
        self.observation_tasks = []
        self.rendered_commands = {}
        self.stack = []
        # Legacy positive producers use a separate unaccepted compatibility
        # transport. Its own items/reports cannot acknowledge UUID commands or
        # downgrade this explicitly accepted workspace's canonical report.
        self.legacy = Studio(app.hub.screen)
        self.client_ref = dict(CLIENT)
        self.workspace_id = WORKSPACE
        self.client_id = CLIENT['clientId']
        self.page_epoch = CLIENT['pageEpoch']
        self.connection_epoch = CLIENT['connectionEpoch']
        self.presentation_area = [0, 0, 1440, 1000]  # independently measured external CSS viewport

    def offer(self, message):
        """Synthetic websocket delivery, preserving the public outbound wire."""
        self.hear(json.loads(message))

    def hear(self, message):
        self.messages.append(copy.deepcopy(message))
        if message.get('commandId'):
            # Report asynchronously, through the actual Screen command seam. No
            # action is executed or state written here; only the renderer is fake.
            task = asyncio.get_running_loop().create_task(self.observe_command(message))
            self.observation_tasks.append(task)
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        elif message.get('type') == 'screen-ask' and self.rendered_commands:
            task = asyncio.get_running_loop().create_task(self.send_report())
            self.observation_tasks.append(task)
        else:
            self.legacy.hear(message)  # legacy reports belong to their unaccepted transport

    async def observe_command(self, message):
        assert message.get('schema') == 'presentation-command-v2', 'MISSING_FEATURE: generic command needs the canonical renderer schema'
        fence = message.get('fence')
        assert isinstance(fence, dict), 'MISSING_FEATURE: UUID command must expose its causal fence'
        assert fence['client'] == self.client_ref and 'clientEpoch' not in fence['client']
        assert fence['appRunId'] == RUN
        for field in ('commandId', 'requestId', 'actionId'):
            assert isinstance(fence[field], str) and fence[field]
            assert message[field] == fence[field]
        assert isinstance(fence['afterReportSeq'], int) and fence['afterReportSeq'] <= self.seq
        assert fence['issuedMono'] <= fence['deadlineMono']
        descriptors = message.get('instances')
        assert isinstance(descriptors, list), 'MISSING_FEATURE: command must serialize its committed target descriptors'
        assert 1 <= len(descriptors) <= 8
        states = {state['id']: state for state in descriptors}
        assert len(states) == len(descriptors) and set(states) == set(fence['targets'])
        assert message['targets'] == list(states)
        if message['commandId'] in self.rendered_commands:
            assert self.rendered_commands[message['commandId']] == message
            self.keep_results([{**result, 'reportSeq': self.seq + 1}
                               for result in self.command_results
                               if result['commandId'] == message['commandId']])
            await self.send_report()  # repeat observation, never reapply a relative operation
            return
        self.rendered_commands[message['commandId']] = copy.deepcopy(message)
        results = []
        for iid, lease in fence['targets'].items():
            state = states[iid]
            assert state['workspaceId'] == WORKSPACE
            assert state['lifetimeGeneration'] == lease['lifetimeGeneration']
            assert state['sourceGeneration'] == lease['sourceGeneration']
            assert state['revision'] == lease['committedRevision']
            versions, owners = state['propertyVersions'], state['propertyOwners']
            assert lease['committedPropertyVersions']
            for prop, version in lease['committedPropertyVersions'].items():
                assert versions[prop] == version and owners[prop] == lease['owner']
            assert state['sourceRef'] == lease['sourceRef']
            assert state['delivery']['sourceToken'] == lease['sourceToken']
            assert isinstance(state['delivery']['sourceToken'], str) and state['delivery']['sourceToken']
            wanted = state['sourceRef']['identity']['resource']
            wanted_payload = self.retained_payload(wanted)
            assert wanted_payload is not None
            expected_payload = lease['payloadBinding']
            assert expected_payload['servedDigest'] == digest(wanted_payload[0])
            if 'originalDigest' in expected_payload:
                assert expected_payload['originalDigest'] == digest(wanted_payload[0])
            if 'transform' in expected_payload:
                assert expected_payload['transform'] == [1, 0, 0, 1, 0, 0]
            ref = copy.deepcopy(self.source_override or state['sourceRef'])
            resource = ref['identity'].get('resource')
            assert resource, 'this synthetic renderer only decodes retained resource fixtures'
            found = self.retained_payload(resource)
            assert found is not None, 'retained payload must actually be readable'
            original = found[0]
            if ref.get('digest'):
                assert digest(original) == ref['digest'], 'actual retained file disagrees with source descriptor'
            data = jpeg(240) if self.bad_payload else original
            decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            assert decoded is not None
            h, w = decoded.shape[:2]
            geometry, view = state['geometryIntent'], state['view']
            pos, size = geometry['position'], geometry['size'] or (360, 280)
            rect = [*pos, *size]
            relation = geometry['relation']
            if relation:
                # Synthetic CSS layout only. The real FloatingWindow/fit and
                # browser measurement campaign must test this separately.
                if relation.get('kind') == 'beside' and relation.get('reference') in self.items:
                    anchor = self.items[relation['reference']]['rect']
                    gap = relation.get('gapPx', 12)
                    rect[0] = (anchor[0] - gap - rect[2] if relation.get('side') == 'left'
                               else anchor[0] + anchor[2] + gap)
                    rect[1] = anchor[1]
            opened = state['open']
            phase = 'closed' if not opened else ('drawn' if self.draw else 'loaded')
            load_state = 'empty' if not opened else phase  # closed is a command phase, not a load state
            if opened and self.load_state_override is not None:
                load_state = self.load_state_override
                phase = 'error' if load_state == 'error' else load_state
            drawn = opened and self.draw and load_state == 'drawn'
            paint = f'synthetic-paint:{self.seq + 1}:{iid}' if drawn else None
            token = state['delivery']['sourceToken']
            payload = {'decodedToken': token, 'servedDigest': digest(data),
                       'originalDigest': digest(original), 'transform': [1, 0, 0, 1, 0, 0]}
            controls = {name: {'id': f'{name}:{iid}', 'role': 'button', 'editable': False,
                               'enabled': opened, 'present': opened, 'targetId': iid,
                               'stateRevision': versions[prop]}
                        for name, prop in CONTROL_PROPERTY.items()}
            controls['move']['role'] = 'drag-handle'
            controls['resize']['role'] = 'resize-handle'
            controls['zoom'].update(role='button', observedValue=view['zoom'])
            if iid not in self.stack:
                self.stack.append(iid)
            if message['op'] == 'front':
                self.stack.remove(iid)
                self.stack.append(iid)
            controls['front']['observedValue'] = self.stack.index(iid) + 1
            controls.pop('pan')
            for axis, value in zip(('x', 'y'), view['pan']):
                controls['pan-' + axis] = {'id': f'pan-{axis}:{iid}', 'role': 'slider', 'editable': False,
                    'enabled': opened, 'present': opened, 'targetId': iid,
                    'stateRevision': versions['pan'], 'observedValue': value}
            content = [rect[0], rect[1] + 30, rect[2], rect[3] - 30]
            scale = min(content[2] / w, content[3] / h) * view['zoom']
            tx = content[2] / 2 - view['pan'][0] * w * scale
            ty = content[3] / 2 - view['pan'][1] * h * scale
            self.items[iid] = {
                'id': iid, 'rootId': 'synthetic-root:' + iid, 'workspaceId': WORKSPACE,
                'kind': DISPLAY_KIND[ref['kind']], 'sourceRef': ref, 'sourceKey': ref['key'],
                'sourceToken': token, 'sourceGeneration': state['sourceGeneration'],
                'lifetimeGeneration': state['lifetimeGeneration'], 'revision': state['revision'],
                'propertyVersions': dict(versions), 'propertyOwners': dict(owners),
                'open': opened, 'visible': opened, 'connected': opened, 'staged': False,
                'rect': rect, 'contentRect': content, 'clipRect': content, 'mode': 'floating',
                'phase': phase, 'drawRef': paint, 'viewDrawRef': 'view-' + paint if paint else None,
                'load': {'state': load_state, 'sourceKey': ref['key'], 'sourceGeneration': state['sourceGeneration'],
                         'naturalSize': [w, h], 'drawToken': paint, 'token': token},
                'payloadBinding': payload, 'controls': controls,
                'zoom': view['zoom'], 'pan': list(view['pan']), 'view': copy.deepcopy(view),
                'viewTransform': {'sourceToContent': [scale, 0, 0, scale, tx, ty],
                                  'contentOrigin': content[:2],
                                  'sourceToWorkspace': [scale, 0, 0, scale, tx + content[0], ty + content[1]],
                                  'originalSize': [w, h], 'transformRevision': state['revision']},
                'z': self.stack.index(iid) + 1,
            }
            results.append({field: fence[field] for field in ('commandId', 'requestId', 'actionId', 'appRunId')})
            results[-1].update(presentationId=iid, phase=phase, reportSeq=self.seq + 1,
                               lifetimeGeneration=state['lifetimeGeneration'], sourceGeneration=state['sourceGeneration'],
                               committedRevision=state['revision'], sourceToken=token, sourceKey=ref['key'],
                               committedPropertyVersions={prop: versions[prop] for prop in lease['committedPropertyVersions']},
                               committedPropertyOwners={prop: owners[prop] for prop in lease['committedPropertyVersions']},
                               propertyVersions=dict(versions), propertyOwners=dict(owners),
                               drawRef=paint, viewDrawRef='view-' + paint if paint else None, absent=not opened)
            if load_state == 'error':
                self.items[iid]['load']['errorCode'] = 'MEDIA_DECODE_FAILED'
                results[-1]['error'] = {'code': 'MEDIA_DECODE_FAILED', 'reason': 'synthetic renderer decode failure'}
        self.keep_results(results)
        for iid in self.stack:
            if iid in self.items:
                self.items[iid]['z'] = self.stack.index(iid) + 1
        await self.send_report()

    def keep_results(self, results):
        """One current result per command/target; satisfy completion's stricter 40 bound."""
        replaced = {(result['commandId'], result['presentationId']) for result in results}
        self.command_results = [result for result in self.command_results
                                if (result['commandId'], result['presentationId']) not in replaced]
        self.command_results = (self.command_results + results)[-40:]

    def retained_payload(self, resource):
        """Read actual synthetic retained bytes; an output ID never becomes current n.

        This is a renderer fixture, not proof of production HTTP token delivery.
        The later browser campaign must independently intercept that delivery.
        """
        oid = resource.get('outputId')
        if oid:
            session = self.app.research.get(resource['id'])
            output = session.output(oid)
            assert output['version'] == resource['version']
            path = session.root / resource['day'] / session.rid / output['path']
            return path.read_bytes(), 'image/jpeg'
        return self.app.hub.resources.picture(resource['id'], resource.get('picture', 0), resource['day'])

    def store(self):
        store = getattr(self.app.hub, 'presentations', None)
        assert store is not None, 'MISSING_FEATURE: App.build must wire its real UUID presentation registry'
        return store

    async def send_report(self, user=()):
        self.seq += 1
        assert 'clientEpoch' not in self.client_ref  # completion's alias belongs to the server adapter
        report = {'type': 'screen', 'schema': 'screen-observation-v2', 'client': dict(self.client_ref),
                  'appRunId': RUN, 'seq': self.seq, 'layoutRevision': self.seq,
                  'capturedMonoMs': time.monotonic() * 1000,
                  'visible': self.visible, 'complete': True, 'omitted': {},
                  'viewport': {'layoutSize': self.presentation_area[2:], 'visualRect': list(self.presentation_area),
                               # Synthetic CSS stage fills this fixture's viewport;
                               # this measurement is independent of any command intent.
                               'presentationArea': list(self.presentation_area),
                               'visualScale': 1, 'documentScroll': [0, 0], 'dpr': 1,
                               'visible': self.visible, 'documentFocused': True, 'activeSurface': 'workspace'},
                  'items': [], 'sources': [], 'targets': [], 'edges': [],
                  'interaction': {'availability': 'observed', 'unknown': []},
                  'commandResults': list(self.command_results),
                  'applied': list(self.applied), 'user': list(user)}
        sources = {}
        for item in self.items.values():
            if not item.get('rootId', '').startswith('synthetic-root:'):
                continue  # legacy items have their own compatibility report
            wire_item = copy.deepcopy(item)
            wire_item['controls'] = list(wire_item['controls'].values())
            report['items'].append(wire_item)
            sources[item['sourceRef']['key']] = item['sourceRef']
        report['sources'] = list(sources.values())
        report['targets'] = [{'id': 'target:' + item['id'], 'type': 'presentation',
                              'presentationId': item['id'], 'sourceKey': item['sourceKey'],
                              'basis': 'registered-window'} for item in report['items']]
        report['edges'] = [{'type': 'shows', 'from': item['id'], 'to': item['sourceKey']}
                          for item in report['items']]
        await COMMANDS['screen'](self.app, self, report)


def built(tmp_path):
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    studio = AppStudio(app)
    ref = ClientRef(CLIENT['workspaceId'], CLIENT['clientId'], CLIENT['pageEpoch'], CLIENT['connectionEpoch'])
    app.accept_workspace(studio, ref)  # trusted synthetic driver, before any report
    original = app.hub.broadcast

    def broadcast(message):
        original(message)
        studio.hear(message)

    app.hub.broadcast = broadcast
    app.hub.screen.broadcast = broadcast
    asyncio.run(studio.send_report())  # accepted socket's initial canonical v2 observation
    STUDIOS[id(app)] = weakref.ref(studio)
    return app, studio


def workspace_api(app):
    assert callable(getattr(app, 'accept_workspace', None)), 'MISSING_FEATURE: trusted App workspace acceptance'
    assert callable(getattr(app, 'workspace_context', None)), 'MISSING_FEATURE: accepted socket context routing'


def test_app_workspace_context_preserves_accepted_origin_and_original_request_without_report(tmp_path):
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    workspace_api(app)
    studio = AppStudio(app)
    ref = ClientRef(WORKSPACE, 'synthetic-client', 'synthetic-page', 'synthetic-socket')
    app.accept_workspace(studio, ref)
    # Transport fields are deliberately different after trusted acceptance.
    studio.client_ref = {'workspaceId': 'spoofed', 'clientId': 'spoofed',
                         'pageEpoch': 'spoofed', 'connectionEpoch': 'spoofed'}
    studio.workspace_id = studio.client_id = studio.page_epoch = studio.connection_epoch = 'spoofed'
    original = app.workspace_context(studio, request_id='original-request', owner='original-action')
    later = app.workspace_context(studio, request_id='later-request', owner='later-action')
    assert (original.workspace_id, original.client_id, original.client_epoch, original.connection_epoch) == (
        WORKSPACE, 'synthetic-client', 'synthetic-page', 'synthetic-socket')
    assert (original.request_id, original.owner) == ('original-request', 'original-action')
    assert (later.request_id, later.owner) == ('later-request', 'later-action')
    assert original is not later and original.request_id == 'original-request'


@pytest.mark.parametrize('sender', ['spoofed-socket', 'client-wire-dict'])
def test_app_workspace_context_refuses_unaccepted_origin_despite_matching_client_attributes(tmp_path, sender):
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    workspace_api(app)
    accepted = AppStudio(app)
    app.accept_workspace(accepted, ClientRef(WORKSPACE, 'synthetic-client', 'synthetic-page', 'synthetic-socket'))
    unaccepted = AppStudio(app) if sender == 'spoofed-socket' else dict(CLIENT)
    with pytest.raises(ObservationError) as refused:
        app.workspace_context(unaccepted, request_id='unaccepted-request', owner='unaccepted-action')
    assert refused.value.code == 'CLIENT_MISMATCH'
    assert app.workspace_context(accepted, request_id='valid-request', owner='valid-action').client_id == 'synthetic-client'


def test_app_workspace_context_refuses_socket_after_existing_screen_disconnect(tmp_path):
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    workspace_api(app)
    studio = AppStudio(app)
    app.accept_workspace(studio, ClientRef(WORKSPACE, 'synthetic-client', 'synthetic-page', 'synthetic-socket'))
    original = app.workspace_context(studio, request_id='before-disconnect', owner='original-action')
    app.hub.screen.gone(studio)  # actual server disconnect seam, no fake binding mutation
    with pytest.raises(ObservationError) as refused:
        app.workspace_context(studio, request_id='after-disconnect', owner='late-action')
    assert refused.value.code == 'CLIENT_MISMATCH'
    assert original.request_id == 'before-disconnect' and original.connection_epoch == 'synthetic-socket'


def test_app_v2_report_cannot_register_spoofed_peer_but_accepted_socket_can_report(tmp_path):
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    workspace_api(app)
    accepted, peer = AppStudio(app), AppStudio(app)  # same raw IDs, distinct transports
    app.accept_workspace(accepted, ClientRef(WORKSPACE, 'synthetic-client', 'synthetic-page', 'synthetic-socket'))
    report = {'type': 'screen', 'schema': 'screen-observation-v2', 'client': dict(CLIENT),
              'appRunId': RUN, 'seq': 1, 'layoutRevision': 1, 'capturedMonoMs': 0,
              'visible': True, 'complete': True, 'omitted': {},
              'viewport': {'layoutSize': [1440, 1000], 'visualRect': [0, 0, 1440, 1000],
                           'visualScale': 1, 'documentScroll': [0, 0], 'dpr': 1,
                           'visible': True, 'documentFocused': True, 'activeSurface': 'workspace'},
              'items': [], 'sources': [], 'targets': [], 'edges': [],
              'interaction': {'availability': 'observed', 'unknown': []}, 'commandResults': []}

    async def go():
        refused = await COMMANDS['screen'](app, peer, copy.deepcopy(report))
        assert refused['ok'] is False and refused['code'] == 'CLIENT_MISMATCH'
        assert app.hub.screen.observation(accepted) is None
        valid = await COMMANDS['screen'](app, accepted, copy.deepcopy(report))
        assert valid['ok'] is True
        assert app.hub.screen.observation(accepted).wire()['client'] == CLIENT
        with pytest.raises(ObservationError) as unknown:
            app.workspace_context(peer, request_id='peer-after-report', owner='peer-action')
        assert unknown.value.code == 'CLIENT_MISMATCH'

    asyncio.run(go())


def test_app_workspace_reacceptance_retires_old_core_context_without_changing_page(tmp_path):
    from orchestrator.presentations import PresentationAction, PresentationError, SourceRef

    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    workspace_api(app)
    studio = AppStudio(app)
    app.accept_workspace(studio, ClientRef(WORKSPACE, 'synthetic-client', 'synthetic-page', 'synthetic-socket'))
    old = app.workspace_context(studio, request_id='old-request', owner='old-action')
    app.accept_workspace(studio, ClientRef(WORKSPACE, 'synthetic-client', 'synthetic-page', 'new-socket'))
    fresh = app.workspace_context(studio, request_id='fresh-request', owner='fresh-action')
    assert fresh.client_epoch == old.client_epoch == 'synthetic-page'
    assert fresh.connection_epoch == 'new-socket' and old.connection_epoch == 'synthetic-socket'
    store = studio.store()
    source = SourceRef.from_wire({'key': 'unprepared-synthetic-source', 'kind': 'saved-image',
                                 'identity': {'resource': {'day': '2026-10-08', 'id': 'd1', 'version': 0, 'picture': 0}},
                                 'availability': 'retained', 'media': 'raw', 'provenance': {}})
    with pytest.raises(PresentationError) as refused:
        store.admit(PresentationAction('open', source=source), old)
    assert refused.value.code == 'client_epoch_changed'  # origin guard precedes source resolution
    assert store.list(WORKSPACE) == ()


def saved_picture(app, value=70):
    data = jpeg(value)
    record = app.hub.resources.results.add('compare', {}, 'Synthetic retained picture', picture_bytes=[data])
    entry = app.hub.resources.get(record['id'], record['day'])
    assert entry is not None and app.hub.resources.picture(entry.id, 0, entry.day)[0] == data
    return entry, data


async def plan(app, actions, *, ctx=None, ticket=None):
    studio = STUDIOS[id(app)]()
    assert studio is not None
    await studio.send_report()  # actual initiating-client seam, before freezing target-at-ask
    ctx = ctx or ToolContext('chat', 1, None, 'Arrange retained views', asked=app.day.here())
    # Socket-bound identity is input, not a test-created action executor. I3 owns
    # the final ToolContext field adapter; keep the real registry and runner.
    ctx.client = studio  # the accepted originating socket, not an untrusted ID dictionary
    ctx.workspace_id = WORKSPACE
    if ctx.when_asked is None:
        ctx.when_asked = app.brain.board.when_asked()
    late = []

    async def on_late(run, context):
        late.append(run)

    runner = ActionRunner(app.brain.registry, app.hub, lambda: None, on_late, board=app.brain.board)
    with using(ticket or issue(app.hub, 'turn', asked=app.day.here(), channel='chat')):
        runs = await runner.run(actions, ctx)
        if runs:
            try:
                await asyncio.wait_for(runs[0].sequence, HANG_S)
            finally:
                await asyncio.gather(*(task for task in studio.observation_tasks if task.done()))
    return runs, ctx


def succeeded(run):
    assert run.ok is True, f'MISSING_FEATURE or behavioral refusal at registered {run.tool}: {run.summary}; {run.error}'


def controlled_state(state):
    """All PresentationState controls/fences; only its volatile observation is excluded."""
    return (state.id, state.workspace_id, state.source, state.revision, state.source_generation,
            state.lifetime_generation, state.open, state.view, state.geometry_intent,
            dict(state.owners), dict(state.property_versions), state.staged, state.title)


def output_window(ctx, step=1, output=0):
    from orchestrator.brain.registry import ToolOutput

    outputs = getattr(ctx.steps, 'outputs', None)
    assert outputs is not None, 'MISSING_FEATURE: actual Runner steps need typed shared outputs'
    ref = outputs[step][output]
    assert isinstance(ref, ToolOutput), 'window output must retain the approved immutable ToolOutput type'
    ref = ref.wire()  # inspect a defensive projection, never the shared immutable output
    assert isinstance(ref, dict) and ref.get('instanceId'), 'window output must be an explicit instance reference'
    iid = ref['instanceId']
    UUID(iid.removeprefix('w-'))
    return iid


async def open_window(app, entry, **extra):
    runs, ctx = await plan(app, [('present', {'do': 'open', 'source': address(entry, **extra)})])
    succeeded(runs[0])
    return output_window(ctx), runs[0], ctx


def test_positive_control_built_registry_opens_existing_retained_artifact(tmp_path):
    from tests.test_retained_reopen_structural_renderer import accepted_reopen_app
    app, _ = accepted_reopen_app(tmp_path)
    entry, pixels = saved_picture(app)

    async def go():
        run = app.brain.registry.start('present', {'what': entry.id, 'do': 'open'},
                                       ToolContext('chat', 1, None, asked=app.day.here()))
        await run.task
        succeeded(run)
        assert run.resource['id'] == entry.id and run.resource['operation'] == 'reopened'
        assert app.hub.resources.picture(entry.id, 0, entry.day)[0] == pixels
        assert len(app.hub.resources.results.entries(entry.day)) == 1
    asyncio.run(go())


def test_uuid_open_has_independent_instances_even_for_equal_source_pixels(tmp_path):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        a, _, _ = await open_window(app, entry)
        b, _, _ = await open_window(app, entry)
        assert a != b, 'BEHAVIOR: equal source bytes must not merge presentation instances'
        sa, sb = studio.store().get(WORKSPACE, a), studio.store().get(WORKSPACE, b)
        assert sa.source.same_identity(sb.source)
        assert studio.items[a]['rootId'] != studio.items[b]['rootId']
        assert all(studio.items[i]['controls']['close']['targetId'] == i for i in (a, b))
    asyncio.run(go())


@pytest.mark.parametrize('verb,values', [
    ('move', {'position': {'x': 75.5, 'y': 100.25}}),
    ('resize', {'size': {'width': 480, 'height': 360}}),
    ('zoom', {'zoom': {'factor': 2}}),
    ('pan', {'center': [0.25, 0.75]}),
    ('front', {}), ('close', {}),
], ids=['move', 'resize', 'zoom', 'pan', 'front', 'close'])
def test_uuid_action_changes_only_its_named_instance(tmp_path, verb, values):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        a, _, _ = await open_window(app, entry)
        b, _, _ = await open_window(app, entry)
        untouched = studio.store().get(WORKSPACE, b)
        before = studio.store().get(WORKSPACE, a)
        runs, _ = await plan(app, [('present', {'do': verb, 'what': {'instanceId': a}, **values})])
        succeeded(runs[0])
        after = studio.store().get(WORKSPACE, a)
        assert controlled_state(studio.store().get(WORKSPACE, b)) == controlled_state(untouched)
        assert after.source.same_identity(before.source)
        if verb == 'move': assert after.geometry_intent.position == (75.5, 100.25)
        if verb == 'resize': assert after.geometry_intent.size == (480, 360)
        if verb == 'zoom': assert after.view.zoom == 2
        if verb == 'pan': assert after.view.pan == (0.25, 0.75)
        if verb in ('zoom', 'pan', 'front'):
            assert runs[0].outcome['status'] == 'confirmed'
        if verb == 'close': assert not after.open and untouched.open
        assert after.revision > before.revision
    asyncio.run(go())


def test_duplicate_and_ordered_output_dependency_use_real_runner(tmp_path):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        runs, ctx = await plan(app, [
            ('present', {'do': 'open', 'source': address(entry)}),
            ('present', {'do': 'duplicate', 'what': {'step': 1, 'output': 0}}),
            ('present', {'do': 'zoom', 'what': {'step': 2, 'output': 0}, 'zoom': {'factor': 2}}),
        ])
        for run in runs: succeeded(run)
        a, b = output_window(ctx, 1), output_window(ctx, 2)
        assert a != b and studio.store().get(WORKSPACE, a).view.zoom == 1
        assert studio.store().get(WORKSPACE, b).view.zoom == 2
    asyncio.run(go())


@pytest.mark.parametrize('verb', ['arrange', 'beside'])
def test_arrangement_is_between_independent_windows_with_fixed_reference(tmp_path, verb):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        a, _, _ = await open_window(app, entry)
        b, _, _ = await open_window(app, entry)
        before = studio.store().get(WORKSPACE, b)
        args = {'do': verb, 'targets': [{'instanceId': a}],
                'relation': {'kind': 'beside', 'reference': {'instanceId': b}, 'gapPx': 12, 'align': 'top'}}
        runs, _ = await plan(app, [('present', args)])
        succeeded(runs[0])
        assert controlled_state(studio.store().get(WORKSPACE, b)) == controlled_state(before)
        ra, rb = studio.items[a]['rect'], studio.items[b]['rect']
        assert abs(ra[0] - (rb[0] + rb[2] + 12)) <= 2 or abs(rb[0] - (ra[0] + ra[2] + 12)) <= 2
        assert studio.items[a]['rootId'] != studio.items[b]['rootId']
    asyncio.run(go())


def test_a_picture_retained_between_two_opens_is_one_source_for_both_windows(tmp_path):
    """The first window opens while the picture's file is still being written (temporary), the second once it is
    retained. They are one source: the studio reports one descriptor per source key, so two would leave the first
    window's row describing another source (the macos-15 runner's beside refusals) or fail the real studio's report."""
    app, studio = built(tmp_path)
    results = app.hub.resources.results
    release, write = threading.Event(), results._write

    def held(day, name, data, *, durable=False, session=None):
        if threading.current_thread() is not threading.main_thread():
            assert release.wait(HANG_S)
        return write(day, name, data, durable=durable, session=session)

    results._write = held  # the background picture write, held until the first window is open
    record = results.add('compare', {}, 'Synthetic retained picture', picture_bytes=[jpeg()])
    entry = app.hub.resources.get(record['id'], record['day'])

    async def go():
        a, _, _ = await open_window(app, entry)
        assert studio.store().get(WORKSPACE, a).source.availability == 'temporary'
        release.set()
        for name in record['pictures']:
            await asyncio.to_thread(results.wait_for_picture, record['day'], name)
        b, _, _ = await open_window(app, entry)
        assert studio.store().get(WORKSPACE, b).source == studio.store().get(WORKSPACE, a).source
        args = {'do': 'beside', 'targets': [{'instanceId': a}],
                'relation': {'kind': 'beside', 'reference': {'instanceId': b}, 'gapPx': 12, 'align': 'top'}}
        runs, _ = await plan(app, [('present', args)])
        succeeded(runs[0])
        ra, rb = studio.items[a]['rect'], studio.items[b]['rect']
        assert abs(ra[0] - (rb[0] + rb[2] + 12)) <= 2 or abs(rb[0] - (ra[0] + ra[2] + 12)) <= 2
    try:
        asyncio.run(go())
    finally:
        release.set()


def test_intentional_identical_relative_steps_apply_twice(tmp_path):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        iid, _, _ = await open_window(app, entry)
        action = ('present', {'do': 'zoom', 'what': {'instanceId': iid}, 'zoom': {'factor': 2}})
        runs, _ = await plan(app, [action, action])
        for run in runs: succeeded(run)
        assert studio.store().get(WORKSPACE, iid).view.zoom == 4
        outcomes = [run.wire().get('outcome') for run in runs]
        assert all(outcomes), 'MISSING_FEATURE: serializable operation receipts must survive ToolRun.wire'
        assert outcomes[0]['actionId'] != outcomes[1]['actionId']
    asyncio.run(go())


@pytest.mark.parametrize('verb', ['open', 'zoom'])
def test_delivery_replay_does_not_allocate_or_multiply_a_relative_action(tmp_path, verb):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        await studio.send_report()
        if verb == 'zoom':
            iid, _, _ = await open_window(app, entry)
            args = {'do': 'zoom', 'what': {'instanceId': iid}, 'zoom': {'factor': 2}}
        else:
            args = {'do': 'open', 'source': address(entry)}
        message = {'type': 'present', 'requestId': 'synthetic-manual-operation',
                   'actionId': 'synthetic-action-1', 'client': dict(CLIENT), **args}
        first = await COMMANDS['present'](app, studio, message)
        states = studio.store().list(WORKSPACE)
        assert len([s for s in states if s.open]) == 1
        second = await COMMANDS['present'](app, studio, copy.deepcopy(message))
        assert second == first
        assert tuple(map(controlled_state, studio.store().list(WORKSPACE))) == tuple(map(controlled_state, states))
        if verb == 'zoom':
            assert studio.store().get(WORKSPACE, iid).view.zoom == 2
    asyncio.run(go())


@pytest.mark.parametrize('ref', [{'step': 2, 'output': 0}, {'step': 1, 'output': 9}])
def test_invalid_step_dependency_refuses_without_touching_independent_open(tmp_path, ref):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        runs, ctx = await plan(app, [
            ('present', {'do': 'open', 'source': address(entry)}),
            ('present', {'do': 'zoom', 'what': ref, 'zoom': {'factor': 2}}),
            ('present', {'do': 'resize', 'what': {'step': 1, 'output': 0}, 'size': {'width': 400, 'height': 300}}),
        ])
        succeeded(runs[0])
        assert runs[1].ok is False
        succeeded(runs[2])
        state = studio.store().get(WORKSPACE, output_window(ctx))
        assert state.view.zoom == 1 and state.geometry_intent.size == (400, 300)
    asyncio.run(go())


@pytest.mark.parametrize('boundary', ['cancel', 'day'])
def test_canceled_or_old_day_request_cannot_open_a_uuid(tmp_path, boundary):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        if boundary == 'cancel': ticket.cancel('synthetic cancellation')
        else: generations(app.hub).bump('session', why='synthetic new day')
        runs, _ = await plan(app, [('present', {'do': 'open', 'source': address(entry)})], ticket=ticket)
        assert runs[0].ok is False
        assert not [s for s in studio.store().list(WORKSPACE) if s.open]
        assert app.hub.resources.picture(entry.id, 0, entry.day) is not None
    asyncio.run(go())


@pytest.mark.parametrize('changed_target', [True, False], ids=['target-user-zoom', 'independent-sibling-zoom'])
def test_final_source_replacement_guard_preserves_manual_change_and_independent_sibling(tmp_path, monkeypatch, changed_target):
    app, studio = built(tmp_path)
    original, _ = saved_picture(app, 40)
    replacement, _ = saved_picture(app, 190)
    reached, release = threading.Event(), threading.Event()

    async def go():
        target, _, _ = await open_window(app, original)
        sibling, _, _ = await open_window(app, original)
        read = app.hub.resources.picture

        def paused(eid, n=0, day=None, *args, **kwargs):
            if eid == replacement.id:
                reached.set()
                if not release.wait(5):
                    raise TimeoutError('synthetic retained-source preparation barrier not released')
            return read(eid, n, day, *args, **kwargs)

        monkeypatch.setattr(app.hub.resources, 'picture', paused)
        ctx = ToolContext('chat', 2, None, 'Replace the retained source', asked=app.day.here())
        ctx.client, ctx.workspace_id = studio, WORKSPACE
        ctx.when_asked = app.brain.board.when_asked()
        with using(issue(app.hub, 'turn', asked=app.day.here(), channel='chat')):
            run = app.brain.registry.start('present', {'do': 'open', 'source': address(replacement),
                                                      'instance': {'instanceId': target}}, ctx)
            try:
                assert await asyncio.to_thread(reached.wait, HANG_S), 'MISSING_FEATURE: replacement must prepare actual retained source'
                edited = target if changed_target else sibling
                await COMMANDS['present'](app, studio, {'type': 'present', 'requestId': 'synthetic-user-zoom',
                    'actionId': 'synthetic-user-action', 'client': dict(CLIENT), 'do': 'zoom',
                    'what': {'instanceId': edited}, 'zoom': {'level': 2}})
            finally:
                release.set()
                await asyncio.wait_for(run.task, 8)
        state = studio.store().get(WORKSPACE, target)
        if changed_target:
            assert run.ok is False, 'BEHAVIOR: target touched during preparation must fence stale replacement'
            assert state.source.identity['resource']['id'] == original.id
            assert state.view.zoom == 2
        else:
            succeeded(run)
            assert state.source.identity['resource']['id'] == replacement.id
            assert studio.store().get(WORKSPACE, sibling).view.zoom == 2
    try:
        asyncio.run(go())
    finally:
        release.set()


@pytest.mark.parametrize('bad', ['undrawn', 'wrong-payload', 'hidden'])
def test_registered_open_does_not_confirm_unobserved_or_wrong_content(tmp_path, bad, monkeypatch):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    # A studio in the background paints nothing (workspace-windows.ts paint needs the page in view), so 'hidden'
    # is a window applied there and not drawn yet: waiting to be seen, then said sent and not confirmed (live
    # 2026-10-11: answered "could not ... the window was hidden" while it was in the studio).
    studio.draw = bad not in ('undrawn', 'hidden')
    studio.bad_payload = bad == 'wrong-payload'
    studio.visible = bad != 'hidden'
    from orchestrator.brain.toolbox import present as present_module
    monkeypatch.setattr(present_module, 'BACKGROUND_WAIT_S', 1.0)

    async def go():
        runs, _ = await plan(app, [('present', {'do': 'open', 'source': address(entry)})])
        outcome = runs[0].wire().get('outcome')
        assert outcome, 'MISSING_FEATURE: refusal must retain its actual causal outcome'
        assert outcome['status'] != 'confirmed'
        assert outcome.get('failure', {}).get('code') != 'TARGET_GONE', 'observation loss is not specimen absence'
        if bad == 'hidden':
            assert outcome['failure']['code'] == 'STUDIO_IN_BACKGROUND' and runs[0].ok is True
            assert 'not confirmed' in runs[0].summary and 'could not' not in runs[0].summary.lower()
    asyncio.run(go())


def comparison_fixture(app):
    from orchestrator.population import captured_picture

    pixels = jpeg()
    info = SimpleNamespace(id=7, width=160, height=120, epoch=0, dx=3.0, dy=4.0,
                           fov=None, disc=0.0, wall_at=1000.0, seq=11, boot='synthetic-boot')
    p = population(app.hub.evidence, Capture(7, pixels, info), thread=app.day.active_id, n=2)
    p.day = app.day.day
    app.hub.evidence.add(p)
    ids = list(p.members)
    originals = [captured_picture(app.hub, p, [mid], outlines=False) for mid in ids]
    assert all(originals)
    return p, ids, originals


def test_actual_compare_producer_retains_exact_ordered_component_pixels(tmp_path):
    app, _ = built(tmp_path)
    p, ids, originals = comparison_fixture(app)

    async def go():
        runs, _ = await plan(app, [('compare', {'a': ids[0], 'b': ids[1]})])
        succeeded(runs[0])
        resource = runs[0].resource
        entry = app.hub.resources.get(resource['id'], resource['day'])
        components = entry.source.get('components')
        assert components and len(components) == 2, 'MISSING_FEATURE: compare must retain clean constituents, not just captures/composite'
        for mid, original, component in zip(ids, originals, components):
            assert component['provenance']['member'] == mid
            assert component['provenance']['population'] == p.id
            assert component['provenance']['revision'] == 0
            child = component['identity']['resource']
            assert app.hub.resources.picture(child['id'], child['picture'], child['day'])[0] == original
            assert component['digest'] == digest(original)
        # Correcting current evidence cannot rewrite the retained comparison's originals.
        app.hub.evidence.revise(p, 'synthetic correction', 'user', [ids[0]])
        assert app.hub.resources.get(entry.id, entry.day).source['components'] == components
    asyncio.run(go())


def test_retained_comparison_splits_into_exact_useful_independent_children(tmp_path):
    app, studio = built(tmp_path)
    p, ids, originals = comparison_fixture(app)

    async def go():
        compared, _ = await plan(app, [('compare', {'a': ids[0], 'b': ids[1]})])
        succeeded(compared[0])
        entry = app.hub.resources.get(compared[0].resource['id'], compared[0].resource['day'])
        app.hub.evidence.revise(p, 'synthetic later correction', 'user', [ids[0]])
        parent, _, _ = await open_window(app, entry)
        result_count = len(app.hub.resources.results.entries(entry.day))
        runs, ctx = await plan(app, [('present', {'do': 'split', 'what': {'instanceId': parent}})])
        succeeded(runs[0])
        children = [output_window(ctx, 1, n) for n in range(2)]
        assert len(set(children)) == 2 and parent not in children
        assert not studio.store().get(WORKSPACE, parent).open
        assert len(app.hub.resources.results.entries(entry.day)) == result_count, 'split must not manufacture a new compare artifact'
        for child, original in zip(children, originals):
            item = studio.items[child]
            assert item['payloadBinding']['servedDigest'] == digest(original)
            assert item['contentRect'][2] >= 240 and item['contentRect'][3] >= 180
            assert item['controls']['close']['targetId'] == child
        assert studio.items[children[0]]['rootId'] != studio.items[children[1]]['rootId']
    asyncio.run(go())


def research_fixture(app):
    manager = app.research
    session = Session(rid='r654321', n=1, brief='Synthetic pictures', deliverable='image', effort='standard',
                      engine='gpt-6.1-sol', purpose='research', label='Synthetic saved pictures', object_id=None,
                      origin={}, day=app.day.day, status='done', root=manager.root)
    manager.sessions[session.rid] = session
    folder = manager.root / session.day / session.rid / 'outputs'
    folder.mkdir(parents=True)
    for name, value in [('first.jpg', 40), ('second.jpg', 180), ('revised.jpg', 220)]:
        (folder / name).write_bytes(jpeg(value))
    for name in ('first.jpg', 'second.jpg'):
        manager._event({'rid': session.rid, 'event': 'output', 'kind': 'image',
                        'path': 'outputs/' + name, 'caption': 'Synthetic retained output'})
    return manager, session


def test_positive_control_actual_registry_inspects_each_research_output_identity(tmp_path):
    app, _ = built(tmp_path)
    _, session = research_fixture(app)

    async def go():
        for n, oid, value in [(0, 'o1', 40), (1, 'o2', 180)]:
            run = app.brain.registry.start('resources', {'id': session.rid, 'day': session.day,
                                                       'inspect': True, 'picture': n}, ToolContext('chat', 1, None))
            await run.task
            succeeded(run)
            assert run.images[0].facts['source']['output'] == oid
            assert run.images[0].facts['source']['version'] == 1
            assert run.images[0].facts['sha256'] == digest(jpeg(value))
    asyncio.run(go())


def test_open_exact_previous_research_output_does_not_substitute_current_ordinal(tmp_path):
    app, studio = built(tmp_path)
    manager, session = research_fixture(app)
    entry = app.hub.resources.get(session.rid, session.day)
    original = address(entry, 1, outputId='o2', version=1)
    manager._event({'rid': session.rid, 'event': 'output', 'kind': 'image', 'path': 'outputs/revised.jpg',
                    'caption': 'Revised synthetic output', 'replaces': 'o2', 'change': 'Synthetic replacement'})

    async def go():
        runs, ctx = await plan(app, [('present', {'do': 'open', 'source': original})])
        succeeded(runs[0])
        item = studio.items[output_window(ctx)]
        assert item['payloadBinding']['servedDigest'] == digest(jpeg(180))
        ref = item['sourceRef']
        assert ref['identity']['resource']['outputId'] == 'o2'
        assert ref['identity']['resource']['version'] == 1
        assert session.output('o2')['path'] == 'outputs/second.jpg'
    asyncio.run(go())


def test_successful_highlight_retains_its_actual_output_on_the_toolrun(tmp_path):
    """Supplemental user regression: prior result identity, not an invented Sight ID.

    Existing outline is a synthetic source fixture. The highlight is the real built
    handler; no vision/segmentation result is substituted for its execution.
    """
    app, _ = built(tmp_path)
    obj = SceneObject('o47', 'highlight', [[[10, 10], [30, 10], [30, 30], [10, 30]]],
                      7, 0, (0, 0), 'user', label='synthetic target')
    app.hub.scene.add(obj)
    app.brain.sight.view = SimpleNamespace(frame_id=7, targets=[], objects=[])

    async def go():
        runs, ctx = await plan(app, [('highlight', {'things': [obj.id], 'keep': True})])
        succeeded(runs[0])
        assert obj.id in ctx.objects
        wire = runs[0].wire(full=True)
        outputs = wire.get('outputs')
        assert outputs, 'MISSING_FEATURE: successful highlight must keep durable actual outputs on its originating ToolRun'
        assert any(output.get('id') == obj.id or output.get('outlineId') == obj.id for output in outputs)
        assert obj.id in ctx.steps.produced[1], 'BEHAVIOR: actual output must compose by occurrence'
        # Log text, a title, and a stale running-look label are not a substitute.
        app.brain.sight.view = None
        assert runs[0].wire(full=True)['outputs'] == outputs
        app.hub.scene.select(None)
        assert runs[0].wire(full=True)['outputs'] == outputs
    asyncio.run(go())


def test_unsupported_operation_on_valid_window_reports_capability_boundary(tmp_path):
    """A valid retained source/target cannot be blamed for an unsupported verb."""
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        iid, _, _ = await open_window(app, entry)
        before = studio.store().get(WORKSPACE, iid)
        runs, _ = await plan(app, [('present', {'do': 'unsupported-synthetic-operation',
                                              'what': {'instanceId': iid}})])
        assert runs[0].ok is False
        outcome = runs[0].wire().get('outcome')
        assert outcome, 'MISSING_FEATURE: unsupported capability must have a durable causal receipt'
        assert outcome['failure']['code'] == 'CAPABILITY_UNSUPPORTED'
        assert controlled_state(studio.store().get(WORKSPACE, iid)) == controlled_state(before)
        assert app.hub.resources.picture(entry.id, 0, entry.day) is not None
    asyncio.run(go())


@pytest.mark.parametrize('source_loss', [False, True], ids=['unsupported-split', 'missing-original'])
def test_split_refusal_distinguishes_capability_from_source_loss(tmp_path, monkeypatch, source_loss):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        missing = None
        if source_loss:
            _, ids, _ = comparison_fixture(app)
            compared, _ = await plan(app, [('compare', {'a': ids[0], 'b': ids[1]})])
            succeeded(compared[0])
            resource = compared[0].resource
            composite = app.hub.resources.get(resource['id'], resource['day'])
            components = composite.source.get('components')
            assert components, 'MISSING_FEATURE: source-loss control requires actual retained components'
            missing = components[0]['identity']['resource']
            selected = composite
        else:
            selected = entry
        parent, _, _ = await open_window(app, selected)
        if source_loss:
            # Inject only an I/O failure for one retained child. The real source
            # resolver/action/commit remain in use and other originals stay readable.
            picture = app.hub.resources.picture

            def unavailable(eid, n=0, day=None, *args, **kwargs):
                if eid == missing['id'] and n == missing['picture'] and day == missing['day']:
                    return None
                return picture(eid, n, day, *args, **kwargs)

            monkeypatch.setattr(app.hub.resources, 'picture', unavailable)
        runs, _ = await plan(app, [('present', {'do': 'split', 'what': {'instanceId': parent}})])
        assert runs[0].ok is False
        outcome = runs[0].wire().get('outcome')
        assert outcome, 'MISSING_FEATURE: distinct refusal boundary must be serializable'
        code = outcome['failure']['code']
        assert code in ({'SOURCE_UNAVAILABLE', 'COMPONENT_UNAVAILABLE'} if source_loss else
                        {'CAPABILITY_UNSUPPORTED', 'COMPONENT_UNAVAILABLE'})
        assert code != 'TARGET_GONE', 'a missing capability/original is not a missing specimen target'
        assert studio.store().get(WORKSPACE, parent).open
    asyncio.run(go())


# NA-S1..S4 / ACT-CONTRACT-053-C1: synthetic renderer regressions through
# the actual accepted App, Registry/Runner, native stores and Screen ingress.
# These are no-model tests, not actual browser paint or physical timing evidence.
def _native_command(studio, op=None):
    commands = [message for message in studio.messages
                if message.get('schema') == 'presentation-command-v2'
                and (op is None or message['op'] == op)]
    assert commands, 'the real adapter must emit its causal native command'
    return commands[-1]


async def _comparison_parent(app):
    _, members, originals = comparison_fixture(app)
    made, _ = await plan(app, [('compare', {'a': members[0], 'b': members[1]})])
    succeeded(made[0])
    resource = made[0].resource
    entry = app.hub.resources.get(resource['id'], resource['day'])
    parent, _, _ = await open_window(app, entry)
    return parent, originals


def _faithful_absence(studio):
    """A physically closed synthetic root is omitted, with its causal result kept.

    Only the real fixture renderer's already disconnected/closed roots qualify;
    this does not turn a desired close or missing acknowledgement into absence.
    """
    for iid, item in list(studio.items.items()):
        if (item.get('open') is False and item.get('connected') is False
                and item.get('visible') is False and item.get('load', {}).get('state') == 'empty'):
            del studio.items[iid]


def _decode_failure(studio, command, iid):
    item = studio.items[iid]
    item.update(phase='error', drawRef=None)
    item['load'].update(state='error', drawToken=None, errorCode='MEDIA_DECODE_FAILED')
    result = next(row for row in studio.command_results
                  if row['commandId'] == command['commandId'] and row['presentationId'] == iid)
    result.update(phase='error', drawRef=None,
                  error={'code': 'MEDIA_DECODE_FAILED', 'reason': 'synthetic child decode failure'})


def _causal_receipt(value, command_id, status=None):
    """Find retained real receipts without imposing an aggregate outcome layout."""
    found = []
    def visit(node):
        if isinstance(node, dict):
            if (node.get('commandId') == command_id and isinstance(node.get('status'), str)
                    and (status is None or node['status'] == status)):
                found.append(node)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(value)
    assert found, f'the actual outcome lost its {status or "terminal"} causal receipt for {command_id}'
    return found[-1]


class _ObservationClock:
    """Advance existing monotonic seams; never alter a fence or receipt.

    The real asyncio clock continues to advance after the offset, so pending
    sleeps remain live. Screen's server receive clock shares this exact domain.
    """
    def __init__(self, monkeypatch, app):
        self.real = time.monotonic
        self.offset = 0.0
        monkeypatch.setattr(time, 'monotonic', self.now)
        monkeypatch.setattr(app.hub.screen, '_observation_clock', self.now)

    def now(self):
        return self.real() + self.offset

    def after(self, command, seconds):
        target = command['fence']['issuedMono'] + seconds
        self.offset = max(self.offset, target - self.real())


@pytest.mark.parametrize('delay', [0.01, 4.25], ids=['before-deadline', 'after-deadline'])
def test_native_command_and_renderer_receipt_use_one_server_clock(tmp_path, monkeypatch, delay):
    """Injected server receive time governs both issued fences and comparison.

    Async scheduling remains real; the external renderer supplies real receipts.
    This separates elapsed-time policy from the speed of a test host.
    """
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    start = time.monotonic()
    now = [start]
    monkeypatch.setattr(app.hub.screen, '_observation_clock', lambda: now[0])
    observe = studio.observe_command
    messages = []

    async def delivered(message):
        messages.append(message)
        assert message['fence']['issuedMono'] == start
        assert message['fence']['deadlineMono'] == start + 4.0
        now[0] += delay
        await observe(message)

    monkeypatch.setattr(studio, 'observe_command', delivered)

    async def go():
        await studio.send_report()
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        try:
            with using(ticket):
                runs, _ = await plan(app, [('present', {'do': 'open', 'source': address(entry)})], ticket=ticket)
                assert runs[0].ok is (delay < 4), runs[0].summary
                assert messages
                if delay > 4:
                    assert runs[0].outcome['status'] == 'timed_out'
        finally:
            ticket.close()
    asyncio.run(go())


async def _adopt_rendered_child(app, studio, iid):
    """Trusted synthetic user fixture: real core/apply, native mapper and Screen.

    The tested split still uses the actual Registry/Runner. This independent
    user touch uses the frozen core/apply seam because Root owns manual routing;
    it creates no successful ToolRun or manufactured confirmation.
    """
    from uuid import uuid4
    from orchestrator.apply import apply, WORKSPACE as WORKSPACE_DESTINATION
    from orchestrator.brain.registry import ActionOccurrence
    from orchestrator.presentation_wire import command
    from orchestrator.presentations import PresentationAction
    from orchestrator.screen import CONFIRM_S

    ticket = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
    occurrence = ActionOccurrence(ticket.admission.request_id, 'synthetic-user/' + str(uuid4()))
    ctx = app.workspace_context(studio, request_id=occurrence.request_id, owner=occurrence.action_id)
    core = studio.store()
    before = core.get(WORKSPACE, iid)
    lease = app.services.prepared_sources.borrow(studio.items[iid]['sourceToken'], ctx)
    try:
        with using(ticket):
            admitted = core.admit(PresentationAction('zoom', instance=iid, values={'level': 1.75}), ctx)
            applied = await apply(app.hub, lambda _place: core.commit(admitted), scope='session',
                                  destination=WORKSPACE_DESTINATION)
            assert applied.ok, 'the real accepted user touch must commit before fault injection'
            issued = time.monotonic()
            wire = command(applied.value, op='zoom', states=(core.get(WORKSPACE, iid),),
                deliveries={iid: lease}, before={iid: before}, occurrence=occurrence,
                after_report_seq=app.hub.screen.completion_report(studio)['seq'],
                issued_mono=issued, deadline_mono=issued + CONFIRM_S)
            await studio.observe_command(wire)  # real synthetic renderer, actual accepted ingress
            assert app.hub.screen.observation(studio).wire()['seq'] == studio.seq
            return controlled_state(core.get(WORKSPACE, iid))
    finally:
        lease.release()
        ticket.close()


@pytest.mark.parametrize('adopt', [False, True], ids=['untouched-children', 'user-adopted-child'])
def test_failed_committed_split_cleans_only_untouched_children_and_preserves_sibling(tmp_path, monkeypatch, adopt):
    app, studio = built(tmp_path)
    sibling_entry, _ = saved_picture(app, 110)

    async def go():
        parent, _ = await _comparison_parent(app)
        sibling, _, _ = await open_window(app, sibling_entry)
        parent_before = controlled_state(studio.store().get(WORKSPACE, parent))
        sibling_before = controlled_state(studio.store().get(WORKSPACE, sibling))
        send = studio.send_report
        fault = {}

        async def report(user=()):
            wire = _native_command(studio)
            if wire['op'] == 'split' and not fault:
                children = wire['targets']
                fault.update(command=copy.deepcopy(wire), children=list(children),
                             tokens={iid: studio.items[iid]['sourceToken'] for iid in children})
                if adopt:
                    # Publish a genuine partial draw before the independent user
                    # touch; the second child has not decoded yet.
                    waiting = studio.items[children[1]]
                    waiting.update(phase='loading', drawRef=None)
                    waiting['load'].update(state='loading', drawToken=None)
                    pending = next(row for row in studio.command_results
                        if row['commandId'] == wire['commandId'] and row['presentationId'] == children[1])
                    pending.update(phase='loading', drawRef=None)
                    await send(user)
                    assert app.hub.screen.observation(studio).wire()['seq'] == studio.seq
                    fault['adopted_state'] = await _adopt_rendered_child(app, studio, children[0])
                _decode_failure(studio, wire, children[1])
                fault['actual_failure'] = copy.deepcopy(studio.items[children[1]]['load'])
            _faithful_absence(studio)
            await send(user)
            assert app.hub.screen.observation(studio).wire()['seq'] == studio.seq

        monkeypatch.setattr(studio, 'send_report', report)
        runs, _ = await plan(app, [('present', {'do': 'split', 'what': {'instanceId': parent}})])
        run = runs[0]
        assert fault and fault['actual_failure']['state'] == 'error', 'draw failure must follow a real committed split'
        assert run.ok is False and run.wire()['outcome']['status'] != 'confirmed'
        assert controlled_state(studio.store().get(WORKSPACE, parent)) == parent_before
        assert controlled_state(studio.store().get(WORKSPACE, sibling)) == sibling_before
        children = fault['children']
        untouched = children[1:] if adopt else children
        if adopt:
            assert controlled_state(studio.store().get(WORKSPACE, children[0])) == fault['adopted_state']
            assert studio.items[children[0]]['open'] and studio.items[children[0]]['view']['zoom'] == 1.75
        metadata = app.workspace_context(studio, request_id='synthetic-cleanup-check', owner='server:metadata')
        from orchestrator.presentation_delivery import DeliveryUnavailable
        for iid in untouched:
            assert not studio.store().get(WORKSPACE, iid).open, 'untouched operation-owned child leaked after failed split'
            actual = studio.items.get(iid)
            assert actual is None or (actual['open'] is False and actual['connected'] is False)
            with pytest.raises(DeliveryUnavailable):
                app.services.prepared_sources.borrow(fault['tokens'][iid], metadata)
        cleanup = [message for message in studio.messages if message.get('schema') == 'presentation-command-v2'
                   and message['op'] == 'close' and set(message['targets']) & set(children)]
        assert cleanup, 'cleanup must have its own causal renderer commands'
        assert set().union(*(set(message['targets']) for message in cleanup)) == set(untouched)
        for message in cleanup:
            proof = _causal_receipt(run.wire()['outcome'], message['commandId'], 'confirmed')
            assert proof['requestId'] == run.occurrence.request_id
            assert set(proof['targets']) == set(message['targets'])
        if adopt:
            lease = app.services.prepared_sources.borrow(fault['tokens'][children[0]], metadata)
            lease.release()  # adopted holder, unlike cleaned children, still owns its original
    asyncio.run(go())


@pytest.mark.parametrize('control', ['move', 'resize'])
@pytest.mark.parametrize('fault', ['missing', 'disabled', 'mistargeted'])
def test_split_requires_each_child_registered_geometry_control_before_parent_retirement(tmp_path, monkeypatch, control, fault):
    app, studio = built(tmp_path)

    async def go():
        parent, _ = await _comparison_parent(app)
        before = controlled_state(studio.store().get(WORKSPACE, parent))
        send, injected = studio.send_report, {}

        async def report(user=()):
            wire = _native_command(studio)
            if wire['op'] == 'split' and not injected:
                iid = wire['targets'][1]
                widget = studio.items[iid]['controls'][control]
                if fault == 'missing':
                    del studio.items[iid]['controls'][control]
                elif fault == 'disabled':
                    widget['enabled'] = False
                else:
                    widget['targetId'] = parent  # known root, wrong independent geometry target
                injected.update(child=iid, command=copy.deepcopy(wire))
            _faithful_absence(studio)
            await send(user)
            assert app.hub.screen.observation(studio).wire()['seq'] == studio.seq

        monkeypatch.setattr(studio, 'send_report', report)
        runs, _ = await plan(app, [('present', {'do': 'split', 'what': {'instanceId': parent}})])
        assert injected, 'the negative control must belong to an actually committed/drawn child'
        assert runs[0].ok is False, 'missing independent geometry capability must prevent retirement'
        outcome = runs[0].wire()['outcome']
        assert outcome['status'] != 'confirmed'
        assert (outcome.get('failure') or outcome.get('error'))['code'] == 'CONTROL_UNAVAILABLE'
        assert controlled_state(studio.store().get(WORKSPACE, parent)) == before
        assert studio.items[parent]['open']
        assert not any(message.get('op') == 'close' and parent in message.get('targets', [])
                       for message in studio.messages), 'parent must never be retired after unusable child controls'
    asyncio.run(go())


@pytest.mark.parametrize('failure', ['failed', 'timed-out', 'canceled'])
def test_split_retains_confirmed_child_receipt_when_separate_retirement_is_unconfirmed(tmp_path, monkeypatch, failure):
    app, studio = built(tmp_path)

    async def go():
        parent, originals = await _comparison_parent(app)
        observe = studio.observe_command
        clock = _ObservationClock(monkeypatch, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        retirement = {}

        async def render(wire):
            if wire['op'] == 'close' and wire['targets'] == [parent] and not retirement:
                retirement.update(command=copy.deepcopy(wire),
                                  child_report=copy.deepcopy(app.hub.screen.completion_report(studio)))
                if failure == 'failed':
                    item = studio.items[parent]  # actual old root stays open; close was not applied
                    row = {key: wire[key] for key in ('requestId', 'actionId', 'commandId', 'appRunId')}
                    row.update(presentationId=parent, reportSeq=studio.seq + 1, phase='error',
                        lifetimeGeneration=item['lifetimeGeneration'], sourceGeneration=item['sourceGeneration'],
                        committedRevision=wire['fence']['targets'][parent]['committedRevision'],
                        committedPropertyVersions=dict(wire['fence']['targets'][parent]['committedPropertyVersions']),
                        sourceToken=item['sourceToken'], sourceKey=item['sourceKey'],
                        propertyVersions=dict(item['propertyVersions']), propertyOwners=dict(item['propertyOwners']),
                        drawRef=None, absent=False,
                        error={'code': 'RENDER_FAILED', 'reason': 'synthetic parent close did not apply'})
                    studio.keep_results([row])
                    await studio.send_report()
                else:
                    if failure == 'canceled':
                        ticket.cancel('synthetic cancellation after children drew')
                    clock.after(wire, 4.25)  # no actual close or acknowledgement exists
                return
            await observe(wire)

        monkeypatch.setattr(studio, 'observe_command', render)
        runs, _ = await plan(app, [('present', {'do': 'split', 'what': {'instanceId': parent}})], ticket=ticket)
        run = runs[0]
        assert retirement, 'all children must really confirm before the distinct parent close is attempted'
        split = _native_command(studio, 'split')
        close = retirement['command']
        assert close['commandId'] != split['commandId']
        assert run.ok is False, 'an unconfirmed retirement cannot finish the whole split'
        outcome = run.wire()['outcome']
        assert outcome['status'] != 'confirmed', 'confirmed children alone do not prove whole-goal completion'
        children = _causal_receipt(outcome, split['commandId'], 'confirmed')
        removal = _causal_receipt(outcome, close['commandId'])
        assert removal['status'] != 'confirmed'
        assert children['requestId'] == removal['requestId'] == run.occurrence.request_id
        assert children['actionId'] == removal['actionId'] == run.occurrence.action_id
        assert set(children['targets']) == set(split['targets'])
        assert children['report']['seq'] == retirement['child_report']['seq']
        rows = {row['id']: row for row in children['observed']}
        for iid, pixels in zip(split['targets'], originals):
            assert rows[iid]['phase'] == 'drawn' and rows[iid]['drawRef']
            assert rows[iid]['payloadBinding']['servedDigest'] == digest(pixels)
            assert rows[iid]['sourceToken'] == split['fence']['targets'][iid]['sourceToken']
        error = removal.get('failure') or removal.get('error')
        assert error and error['code'] in ({'TARGET_REVISED', 'TOPOLOGY_MISMATCH', 'RENDER_FAILED'} if failure == 'failed'
            else {'REQUEST_CANCELED'} if failure == 'canceled' else {'CONFIRM_TIMEOUT', 'REPORT_STALE'})
        # No successful closure receipt or whole-goal state is manufactured by
        # retaining already confirmed source/draw facts.
        assert studio.items[parent]['open']
    asyncio.run(go())


@pytest.mark.parametrize('mode', ['within-four-seconds', 'after-four-seconds', 'wrong-origin', 'canceled-request'])
def test_native_confirmation_uses_four_second_origin_and_request_policy(tmp_path, monkeypatch, mode):
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        from orchestrator.screen import CONFIRM_S
        clock = _ObservationClock(monkeypatch, app)
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        ingress = COMMANDS['screen']
        captured = {}
        peer = None
        if mode == 'wrong-origin':
            peer = AppStudio(app)
            ref = ClientRef('synthetic-peer-workspace', 'synthetic-peer', 'peer-page', 'peer-socket')
            peer.client_ref = ref.wire()
            app.accept_workspace(peer, ref)

        async def delayed_ingress(owner, client, message):
            commands = [wire for wire in studio.messages if wire.get('schema') == 'presentation-command-v2']
            wire = commands[-1] if commands else None
            if owner is app and client is studio and wire is not None and not captured:
                captured['command'] = copy.deepcopy(wire)
                clock.after(wire, 4.25 if mode == 'after-four-seconds' else 3.0)
                if mode == 'canceled-request':
                    ticket.cancel('synthetic cancellation before faithful draw report')
                if mode == 'wrong-origin':
                    captured['transport'] = await ingress(owner, peer, message)
                    captured['origin_report'] = copy.deepcopy(app.hub.screen.completion_report(studio))
                    clock.after(wire, 4.25)  # no originating-client observation arrived
                    return captured['transport']
                reply = await ingress(owner, client, message)
                captured['transport'] = reply
                captured['origin_report'] = copy.deepcopy(app.hub.screen.completion_report(studio))
                return reply
            return await ingress(owner, client, message)

        monkeypatch.setitem(COMMANDS, 'screen', delayed_ingress)
        runs, _ = await plan(app, [('present', {'do': 'open', 'source': address(entry)})], ticket=ticket)
        run = runs[0]
        assert captured, 'the held report must belong to an actual committed native command'
        wire = captured['command']
        assert CONFIRM_S == 4.0
        assert ticket.admission.used == 0, 'synthetic action observations must not create model calls'
        outcome = run.wire()['outcome']
        if mode == 'within-four-seconds':
            assert wire['fence']['deadlineMono'] - wire['fence']['issuedMono'] == pytest.approx(CONFIRM_S)
            assert captured['transport']['ok'] is True
            assert captured['origin_report']['receivedAtMono'] - wire['fence']['issuedMono'] == pytest.approx(3.0, abs=0.1)
            succeeded(run)
            proof = _causal_receipt(outcome, wire['commandId'], 'confirmed')
            assert proof['report']['seq'] == captured['origin_report']['seq']
            assert proof['requestId'] == wire['requestId'] and proof['actionId'] == wire['actionId']
            assert set(proof['targets']) == set(wire['targets'])
            item = proof['observed'][0]
            assert item['phase'] == 'drawn' and item['drawRef']
            assert item['sourceToken'] == wire['fence']['targets'][item['id']]['sourceToken']
            assert item['payloadBinding']['servedDigest'] == digest(jpeg())
        else:
            assert run.ok is False and outcome['status'] != 'confirmed'
            if mode == 'after-four-seconds':
                assert captured['transport']['ok'] is True
                assert captured['origin_report']['receivedAtMono'] > wire['fence']['issuedMono'] + CONFIRM_S
                proof = _causal_receipt(outcome, wire['commandId'], 'timed_out')
                assert proof['report']['seq'] == captured['origin_report']['seq'], 'late draw remains dated history'
                assert (proof.get('failure') or proof.get('error'))['code'] == 'CONFIRM_TIMEOUT'
            elif mode == 'wrong-origin':
                assert captured['transport']['ok'] is False and captured['transport']['code'] == 'CLIENT_MISMATCH'
                assert captured['origin_report']['seq'] <= wire['fence']['afterReportSeq']
                assert app.hub.screen.observation(peer) is None
            else:
                assert captured['transport']['ok'] is True
                assert (outcome.get('failure') or outcome.get('error'))['code'] == 'REQUEST_CANCELED'
    asyncio.run(go())


@pytest.mark.parametrize('adopt', [False, True], ids=['untouched-children', 'user-adopted-child'])
def test_canceled_committed_split_compensates_only_untouched_children_on_original_request(tmp_path, monkeypatch, adopt):
    """Actual Ticket.cancel before qualification; synthetic child delivery only."""
    app, studio = built(tmp_path)
    sibling_entry, _ = saved_picture(app, 110)

    async def go():
        parent, _ = await _comparison_parent(app)
        sibling, _, _ = await open_window(app, sibling_entry)
        parent_before = controlled_state(studio.store().get(WORKSPACE, parent))
        sibling_before = controlled_state(studio.store().get(WORKSPACE, sibling))
        ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
        admission, issued = ticket.admission, dict(ticket.issued)
        send, fault = studio.send_report, {}
        reason = 'synthetic original split cancellation before child qualification'

        async def report(user=()):
            wire = _native_command(studio)
            if wire['op'] == 'split' and not fault:
                children = wire['targets']
                assert wire['requestId'] == admission.request_id
                assert wire['client'] == studio.client_ref
                fault.update(command=copy.deepcopy(wire), children=list(children),
                             tokens={iid: studio.items[iid]['sourceToken'] for iid in children})
                # Both children come from an actual committed native split. Hold
                # one before the first canonical report, so no complete child
                # qualification or parent retirement can precede cancellation.
                assert all(studio.store().get(WORKSPACE, iid).open
                           and studio.store().get(WORKSPACE, iid).staged for iid in children)
                waiting = studio.items[children[1]]
                waiting.update(phase='loading', drawRef=None)
                waiting['load'].update(state='loading', drawToken=None)
                pending = next(row for row in studio.command_results
                    if row['commandId'] == wire['commandId'] and row['presentationId'] == children[1])
                pending.update(phase='loading', drawRef=None)
                await send(user)
                held = app.hub.screen.completion_report(studio)
                assert held['seq'] == studio.seq
                assert next(row for row in held['items'] if row['id'] == children[1])['phase'] == 'loading'
                assert studio.store().get(WORKSPACE, parent).open
                if adopt:
                    fault['adopted_state'] = await _adopt_rendered_child(app, studio, children[0])
                ticket.cancel(reason)  # the real original request; no replacement admission
                fault['canceled_at_seq'] = studio.seq
            _faithful_absence(studio)
            await send(user)
            assert app.hub.screen.observation(studio).wire()['seq'] == studio.seq

        monkeypatch.setattr(studio, 'send_report', report)
        runs, _ = await plan(app, [('present', {'do': 'split', 'what': {'instanceId': parent}})], ticket=ticket)
        run = runs[0]
        assert fault and ticket.cancelled == reason
        assert ticket.admission is admission and ticket.issued == issued and admission.used == 0
        assert run.occurrence.request_id == admission.request_id
        outcome = run.wire()['outcome']
        assert run.ok is False and outcome['status'] != 'confirmed'
        assert (outcome.get('failure') or outcome.get('error'))['code'] == 'REQUEST_CANCELED'
        cancellation = _causal_receipt(outcome, fault['command']['commandId'])
        assert cancellation['status'] != 'confirmed'
        assert (cancellation.get('failure') or cancellation.get('error'))['code'] == 'REQUEST_CANCELED'
        assert controlled_state(studio.store().get(WORKSPACE, parent)) == parent_before
        assert controlled_state(studio.store().get(WORKSPACE, sibling)) == sibling_before
        assert studio.items[parent]['open'] and studio.items[sibling]['open']
        children = fault['children']
        untouched = children[1:] if adopt else children
        metadata = app.workspace_context(studio, request_id=run.occurrence.request_id, owner=run.occurrence.action_id)
        from orchestrator.presentation_delivery import DeliveryUnavailable
        for iid in untouched:
            assert not studio.store().get(WORKSPACE, iid).open, 'canceled split leaked an untouched provisional child'
            actual = studio.items.get(iid)
            assert actual is None or (actual['open'] is False and actual['connected'] is False)
            with pytest.raises(DeliveryUnavailable):
                app.services.prepared_sources.borrow(fault['tokens'][iid], metadata)
        cleanup = [wire for wire in studio.messages if wire.get('schema') == 'presentation-command-v2'
                   and wire['op'] == 'close' and set(wire['targets']) & set(children)]
        assert cleanup, 'rightful compensation must send actual causal close commands after cancellation'
        assert set().union(*(set(wire['targets']) for wire in cleanup)) == set(untouched)
        assert not any(wire.get('op') == 'close' and parent in wire.get('targets', []) for wire in studio.messages)
        for wire in cleanup:
            proof = _causal_receipt(outcome, wire['commandId'], 'confirmed')
            assert wire['requestId'] == proof['requestId'] == admission.request_id
            assert wire['actionId'] == proof['actionId'] == run.occurrence.action_id
            assert wire['client'] == fault['command']['client'] == studio.client_ref
            assert wire['fence']['afterReportSeq'] >= fault['canceled_at_seq']
            assert set(proof['targets']) == set(wire['targets'])
        if adopt:
            assert controlled_state(studio.store().get(WORKSPACE, children[0])) == fault['adopted_state']
            assert studio.items[children[0]]['open'] and studio.items[children[0]]['view']['zoom'] == 1.75
            lease = app.services.prepared_sources.borrow(fault['tokens'][children[0]], metadata)
            lease.release()
    asyncio.run(go())


@pytest.mark.parametrize('anchor_x,side,expected,width', [
    (1000, None, 'left', 1440), (20, None, 'right', 1440), (1000, 'left', 'left', 1440),
    (1000, 'right', None, 1440), (20, 'left', None, 1440), (540, None, 'right', 1440),
    (340, None, None, 700),
])
def test_arrange_omitted_side_uses_available_space_without_resizing_or_moving_anchor(tmp_path, monkeypatch, anchor_x, side, expected, width):
    import json
    app, studio = built(tmp_path)
    studio.presentation_area = [0, 0, width, 1000]
    # This exercises geometry, not renderer latency. Keep the server observation
    # domain deterministic; the separate before/after-deadline cases test policy.
    observed_at = time.monotonic()
    monkeypatch.setattr(app.hub.screen, '_observation_clock', lambda: observed_at)
    entry, _ = saved_picture(app)
    async def go():
        target, _, _ = await open_window(app, entry)
        anchor, _, _ = await open_window(app, entry)
        moved, _ = await plan(app, [('present', {'do': 'move', 'instance': anchor,
                                               'position': {'x': anchor_x, 'y': 60}})])
        succeeded(moved[0])
        before_anchor = controlled_state(studio.store().get(WORKSPACE, anchor))
        before_target = controlled_state(studio.store().get(WORKSPACE, target))
        before_rect = list(studio.items[target]['rect'])
        relation = {'kind': 'beside', 'reference': {'instanceId': anchor}, 'gapPx': 12}
        if side is not None:
            relation['side'] = side
        runs, _ = await plan(app, [('present', {'do': 'arrange', 'targets': [{'instanceId': target}],
                                               'relation': relation})])
        assert controlled_state(studio.store().get(WORKSPACE, anchor)) == before_anchor
        if expected is None:
            assert runs[0].ok is False and 'cannot fit' in runs[0].summary
            assert controlled_state(studio.store().get(WORKSPACE, target)) == before_target
            return
        succeeded(runs[0])
        actual = studio.items[target]['rect']
        assert actual[2:] == before_rect[2:]
        assert actual[1] == 60
        anchor_rect = studio.items[anchor]['rect']
        assert actual[0] == (anchor_rect[0] - 12 - actual[2] if expected == 'left'
                              else anchor_rect[0] + anchor_rect[2] + 12)
        retained = json.loads(studio.store().get(WORKSPACE, target).geometry_intent.relation_json)
        assert retained['side'] == expected
        assert runs[0].outcome['status'] == 'confirmed'
    asyncio.run(go())
