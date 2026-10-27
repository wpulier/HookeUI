"""TEST-only retained reopen through an accepted structural DOM renderer.

Actual App/Registry/Index/Magnifier/Screen/COMMANDS and retained synthetic JPEGs.
External fixed DOM lifetime, synthetic layout and decoded picture-pane observations
only; no native source/token/property owner/paint authority or copied compatibility
ACK. Requires accepted e99e structural normalization/Change association source.
Browser loaded-pixel/error/retry acceptance remains independently required.
"""
import asyncio
import copy
import json
import time

import cv2
import numpy as np

from orchestrator.app import App
from orchestrator.brain.registry import ToolContext
from orchestrator.capture import RUN
from orchestrator.commands import COMMANDS
from orchestrator.paths import Paths
from orchestrator.workspace_observation import ClientRef
from tests.test_workspace_actions_app import CLIENT, built, saved_picture


class AcceptedReopenStudio:
    """One accepted socket reports its own fixed magnifier root and decoded panes."""
    def __init__(self, app, *, withhold=False):
        self.app, self.withhold = app, withhold
        self.client_ref = dict(CLIENT)
        self.seq, self.applied = 0, []
        self.messages, self.reports, self.replies, self.tasks = [], [], [], []
        self.loaded = {}
        self.row = {
            'id': 'magnifier', 'rootId': 'synthetic-dom:magnifier',
            'workspaceId': CLIENT['workspaceId'], 'kind': 'magnifier',
            'lifetimeGeneration': 'synthetic-fixed-root:magnifier',
            'sourceGeneration': 1, 'revision': 1,
            'open': False, 'connected': True, 'visible': False,
            'rect': None, 'z': 10, 'space': 'workspace',
            'view': {'legacy': {'schema': 'studio-surface-v1', 'scope': 'dom-observation',
                               'shows': None, 'source': None, 'beside': None,
                               'level': None, 'pictures': [], 'covers': False}},
            'propertyVersions': {}, 'propertyOwners': {}, 'controls': [],
            'load': {'state': 'unknown', 'unknown': ['native-source-unregistered']},
            'unknown': ['synthetic-structural-dom-only'],
        }

    def _loaded_picture(self, source):
        """Read the exact actual Magnifier payload; decode before declaring a pane loaded."""
        url = source['url']
        assert url.startswith('/api/picture/'), 'SETUP: this renderer supports retained Magnifier pictures only'
        data = self.app.magnifier.pictures[url.removeprefix('/api/picture/')]
        decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        assert decoded is not None and decoded.shape[0] > 0 and decoded.shape[1] > 0
        resource = source.get('resource')
        if resource is not None:
            original, media = self.app.hub.resources.picture(resource['id'], 0, resource['day'])
            assert media == 'image/jpeg' and data == original, 'actual published pane must decode retained original pixels'
        self.loaded[url] = {'bytes': data, 'shape': tuple(decoded.shape),
                            'resource': copy.deepcopy(resource)}
        return {'source': url, 'scale': source.get('displayScale', 1)}

    def _render_magnifier(self, message):
        before = copy.deepcopy(self.row)
        opened = bool(message['open'])
        primary = message['sources'][message['index']] if opened and message['sources'] else None
        beside = message.get('beside') if opened else None
        pictures = [self._loaded_picture(source) for source in (primary, beside)
                    if source and source.get('kind') == 'image']
        legacy = self.row['view']['legacy']
        legacy.update(
            source=(primary.get('url') if primary.get('kind') == 'image' else primary.get('kind')) if primary else None,
            shows=(primary.get('objectId') if primary.get('kind') == 'image' else message.get('objectId')) if primary else None,
            beside=beside.get('url') if beside else None,
            level=(1.0 if primary.get('kind') == 'image' else message.get('zoom') or 1.0) if primary else None,
            pictures=pictures)
        self.row.update(open=opened, visible=opened, rect=[10, 20, 300, 200] if opened else None)
        content = lambda facts: (facts['shows'], facts['source'], facts['beside'],
                                 tuple(picture['source'] for picture in facts['pictures']))
        if content(legacy) != content(before['view']['legacy']):
            self.row['sourceGeneration'] += 1
        if self.row != before:
            self.row['revision'] += 1

    def hear(self, message):
        self.messages.append(copy.deepcopy(message))
        if message.get('type') == 'magnifier':
            self._render_magnifier(message)
        elif message.get('type') == 'present':
            assert not message.get('commandId'), 'structural fixture must not acknowledge native commands'
            assert message['id'] == 'magnifier' and message['kind'] == 'entry' and message['verb'] == 'open'
            # The real magnifier wire already opened/selected the retained picture.
            # Do not set requested source/shows from the Change, or copy a raw ACK.
            assert self.row['open'] and self.row['view']['legacy']['pictures']
            self.applied.append(message['change'])  # exact received original after synthetic render
        elif message.get('type') != 'screen-ask':
            return
        if not self.withhold:
            task = asyncio.get_running_loop().create_task(self.send_report())
            self.tasks.append(task)

    def offer(self, message):
        """Actual accepted-client unicast transport enters the same external renderer."""
        self.hear(json.loads(message))

    async def send_report(self):
        self.seq += 1
        report = {
            'type': 'screen', 'schema': 'screen-observation-v2', 'client': dict(self.client_ref),
            'appRunId': RUN, 'seq': self.seq, 'layoutRevision': self.seq,
            'capturedMonoMs': time.monotonic() * 1000, 'visible': True,
            'complete': False, 'omitted': {'items': 1, 'sources': 0, 'targets': 0, 'edges': 0},
            'viewport': {'layoutSize': [1440, 1000], 'visualRect': [0, 0, 1440, 1000],
                         'visualScale': 1, 'documentScroll': [0, 0], 'dpr': 1,
                         'visible': True, 'documentFocused': True, 'activeSurface': 'workspace'},
            'items': [copy.deepcopy(self.row)], 'sources': [], 'targets': [], 'edges': [],
            'interaction': {'availability': 'partial', 'unknown': ['synthetic-hit-registration-unavailable']},
            'commandResults': [], 'applied': list(self.applied), 'user': [],
        }
        self.reports.append(copy.deepcopy(report))
        reply = await COMMANDS['screen'](self.app, self, report)
        self.replies.append(copy.deepcopy(reply))
        assert reply.get('ok') is True, f'SETUP: structural report refused by actual normalization: {reply}'


def accepted_reopen_app(tmp_path, *, withhold=False):
    """Trusted synthetic socket plus initial closed root; actual app paths unchanged."""
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    studio = AcceptedReopenStudio(app, withhold=withhold)
    app.accept_workspace(studio, ClientRef(CLIENT['workspaceId'], CLIENT['clientId'],
                                          CLIENT['pageEpoch'], CLIENT['connectionEpoch']))
    original = app.hub.broadcast

    def broadcast(message):
        original(message)
        studio.hear(message)

    app.hub.broadcast = broadcast
    app.hub.screen.broadcast = broadcast
    asyncio.run(studio.send_report())
    # Admission precondition: represented real fixture root on this accepted socket before Send.
    observed = app.hub.screen.observation(studio).wire()
    assert len(observed['items']) == 1 and observed['items'][0]['open'] is False
    assert observed['items'][0]['rootId'] == studio.row['rootId']
    assert observed['sources'] == observed['commandResults'] == []
    return app, studio


def _observe_changes(app, monkeypatch):
    sent, confirmations = [], []
    send, confirm = app.hub.screen.send, app.hub.screen.confirm

    def observed_send(*args, **kwargs):
        change = send(*args, **kwargs)
        sent.append(change)
        return change

    async def observed_confirm(change, check, *args, **kwargs):
        actual = await confirm(change, check, *args, **kwargs)
        confirmations.append((change, actual))
        return actual

    monkeypatch.setattr(app.hub.screen, 'send', observed_send)
    monkeypatch.setattr(app.hub.screen, 'confirm', observed_confirm)
    return sent, confirmations


async def _reopen(app, entry):
    run = app.brain.registry.start('present', {'what': entry.id, 'do': 'open'},
                                   ToolContext('chat', 1, None, asked=app.day.here()))
    await run.task
    return run


def _no_native_authority(app, studio):
    observed = app.hub.screen.observation(studio).wire()
    assert observed['sources'] == observed['targets'] == observed['edges'] == observed['commandResults'] == []
    row = observed['items'][0]
    assert row['propertyVersions'] == row['propertyOwners'] == {}
    assert row['load']['state'] == 'unknown'
    for key in ('sourceKey', 'sourceToken', 'viewDrawRef', 'payloadBinding', 'viewTransform', 'liveSample'):
        assert row.get(key) is None
    completion = app.hub.screen.completion_report(studio)['items'][0]
    assert completion.get('sourceRef') is None and completion.get('drawRef') is None


def test_old_mixed_transport_cannot_confirm_retained_reopen(tmp_path, monkeypatch):
    """Preserves the actual baseline failure; missing accepted root is not compatibility authority."""
    app, studio = built(tmp_path)
    entry, pixels = saved_picture(app)
    sent, confirmations = _observe_changes(app, monkeypatch)

    async def go():
        run = await _reopen(app, entry)
        assert run.ok is False and run.resource is None
        # Accepted origin has no represented magnifier root. Screen.send refuses
        # before returning/dispatching a Change; no foreign compatibility ACK is used.
        assert sent == [] and confirmations == []
        assert not any(message.get('type') == 'present' for message in studio.messages)
        assert app.hub.screen.observation(studio).wire()['items'] == []
        assert app.hub.resources.picture(entry.id, 0, entry.day)[0] == pixels
        assert len(app.hub.resources.results.entries(entry.day)) == 1

    asyncio.run(go())


def test_accepted_structural_renderer_confirms_original_retained_reopen(tmp_path, monkeypatch):
    """Catches failure to bind/confirm actual source-free DOM observations on the accepted origin."""
    app, studio = accepted_reopen_app(tmp_path)
    entry, pixels = saved_picture(app)
    sent, confirmations = _observe_changes(app, monkeypatch)

    async def go():
        run = await _reopen(app, entry)
        await asyncio.gather(*studio.tasks)  # renderer errors cannot be swallowed by the broadcaster
        assert run.ok is True, run.summary
        assert {key: run.resource[key] for key in ('id', 'day', 'title', 'operation')} == {
            'id': entry.id, 'day': entry.day, 'title': entry.title, 'operation': 'reopened'}
        assert run.resource['chronology'] == {
            'day': entry.day, 'kind': 'result', 'producer': 'compare', 'at': entry.at,
            'total': 1, 'earlier': 0, 'sameTime': 1,
            'earliest': [entry.id], 'latest': [entry.id],
            'earliestOmitted': 0, 'latestOmitted': 0, 'unknownTimes': 0,
            'complete': True, 'unreadable': []}
        assert run.resource['byteAssociation'] == 'not independently verified by this reopen'
        assert len(sent) == 1 and confirmations == [(sent[0], True)]
        change = sent[0]
        binding = change._structural
        assert binding is not None and binding.change is change and binding.client is studio
        assert binding.profile is app.hub.screen._profile(studio)
        assert change.before['rootId'] == binding.root == studio.row['rootId']
        assert change.before['lifetimeGeneration'] == binding.lifetime == studio.row['lifetimeGeneration']
        assert change.before['open'] is False
        after = app.hub.screen.observation(studio).wire()
        assert after['seq'] > binding.issued_seq and change.id in after['applied']
        assert studio.row['sourceGeneration'] > change.before['sourceGeneration']
        assert studio.row['revision'] > change.before['revision']
        pane = studio.row['view']['legacy']['source']
        assert studio.loaded[pane]['bytes'] == pixels
        assert studio.loaded[pane]['resource']['id'] == entry.id
        assert after['items'][0]['view']['legacy']['pictures'] == [{'source': pane, 'scale': 1}]
        assert all(reply.get('ok') is True for reply in studio.replies)
        _no_native_authority(app, studio)
        assert app.hub.resources.picture(entry.id, 0, entry.day)[0] == pixels
        assert len(app.hub.resources.results.entries(entry.day)) == 1

    asyncio.run(go())


def test_withheld_accepted_structural_report_cannot_claim_reopened(tmp_path, monkeypatch):
    """Loaded synthetic panes without a later actual accepted report do not mean confirmed."""
    app, studio = accepted_reopen_app(tmp_path, withhold=True)
    entry, pixels = saved_picture(app)
    sent, confirmations = _observe_changes(app, monkeypatch)

    async def go():
        run = await _reopen(app, entry)
        assert run.ok is False and run.resource is None
        assert len(sent) == 1 and confirmations == [(sent[0], False)]
        assert sent[0]._structural is not None and sent[0]._structural.client is studio
        assert studio.row['open'] and studio.row['view']['legacy']['pictures']
        assert studio.loaded[studio.row['view']['legacy']['source']]['bytes'] == pixels
        observed = app.hub.screen.observation(studio).wire()
        assert observed['seq'] == 1 and observed['items'][0]['open'] is False
        assert observed['applied'] == [] and len(studio.reports) == 1
        _no_native_authority(app, studio)
        assert app.hub.resources.picture(entry.id, 0, entry.day)[0] == pixels
        assert len(app.hub.resources.results.entries(entry.day)) == 1

    asyncio.run(go())
