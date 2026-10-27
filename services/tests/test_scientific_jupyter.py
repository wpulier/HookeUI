"""H09 slice 1: an authorized local Jupyter connection that can only do what the session's console does.

Component tests with a stand-in session manager and the real HTTP routes; no napari kernel or Jupyter frontend.
"""
import asyncio
import json
import os
import stat
import threading
import time

import pytest
from fastapi.testclient import TestClient

from orchestrator import jupyter_kernel, scientific_jupyter
from orchestrator.paths import Paths
from orchestrator.scientific_jupyter import JupyterGrants, JupyterRefused
from orchestrator.server import create_app

REFERENCE = {'id': 'p' + 'a' * 32, 'day': '2026-10-21', 'version': 3, 'picture': None}


class Owner:
    def __init__(self, client):
        self.origin = type('Origin', (), {'client': client})()
        self.execution = type('Execution', (), {'reference': dict(REFERENCE)})()
        self.detached = False
        self.stop = threading.Event()


class Manager:
    def __init__(self):
        self.client = object()
        self._owners = {'review-one': Owner(self.client)}
        self.calls = []

    def _owner(self, client, identity):
        owner = self._owners.get(identity)
        if owner is None or owner.origin.client is not client:
            raise ValueError('This scientific review does not belong to this workspace connection')
        return owner

    async def request(self, client, identity, request, **kwargs):
        self.calls.append((client, identity, request))
        return {'ok': True, 'console': {'format': 'scientific-console-v1', 'sequence': 1, 'stdout': 'ran\n', 'stderr': '',
                                        'result': '42', 'error': '', 'truncated': False, 'privatePreview': True}}


def granted(tmp_path):
    manager = Manager()
    grants = JupyterGrants(manager, tmp_path / 'jupyter')
    issued = grants.issue(manager.client, 'review-one', 'http://127.0.0.1:8770/')
    connection = json.loads((tmp_path / 'jupyter' / 'connections' / f"{issued['name']}.json").read_text())
    return manager, grants, issued, connection


def test_a_grant_writes_a_private_connection_and_a_kernelspec_and_never_returns_the_token(tmp_path):
    manager, grants, issued, connection = granted(tmp_path)
    folder = tmp_path / 'jupyter' / issued['name']
    secret = tmp_path / 'jupyter' / 'connections' / f"{issued['name']}.json"
    assert stat.S_IMODE(os.stat(secret).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tmp_path / 'jupyter').st_mode) == 0o700
    assert sorted(p.name for p in folder.iterdir()) == ['kernel.json']  # installing copies no token
    assert connection['token'] not in json.dumps(issued) and connection['url'] == 'http://127.0.0.1:8770'
    spec = json.loads((folder / 'kernel.json').read_text())
    assert spec['argv'][:2] == ['python3', str(scientific_jupyter.KERNEL)] and spec['argv'][-2:] == ['--hooke', str(secret)]
    assert issued['display'] == spec['display_name']
    assert issued['install'] == f'jupyter kernelspec install --user "{folder}"'
    assert grants.resolve(connection['token']) == (manager.client, 'review-one', REFERENCE)


def test_notebook_actions_become_exactly_the_consoles_requests(tmp_path):
    _, grants, _, _ = granted(tmp_path)
    cell = 'jupyter-' + 'b' * 32
    assert grants.request('execute', {'cell': cell, 'source': 'x = 1'}, REFERENCE) == {
        'op': 'python-resources', 'resource': REFERENCE, 'source': 'x = 1', 'cell': cell}
    assert grants.request('interrupt', {'cell': cell}, REFERENCE) == {'op': 'python-interrupt', 'cell': cell}
    assert grants.request('assist', {'mode': 'complete', 'expression': 'np.me'}, REFERENCE) == {
        'op': 'python-assist', 'mode': 'complete', 'expression': 'np.me', 'resource': REFERENCE}
    for action, fields in (('execute', {'cell': 'short', 'source': 'x'}), ('execute', {'cell': cell}),
                           ('render', {}), ('assist', {'mode': 'complete'})):
        with pytest.raises(JupyterRefused):
            grants.request(action, fields, REFERENCE)


@pytest.mark.parametrize('ending', ['revoked', 'stopped', 'restarted', 'expired', 'reissued'])
def test_a_connection_ends_with_its_session_and_its_files_go(tmp_path, ending, monkeypatch):
    manager, grants, issued, connection = granted(tmp_path)
    folder = tmp_path / 'jupyter' / issued['name']
    if ending == 'revoked':
        assert grants.revoke(manager.client, 'review-one')
    elif ending == 'stopped':
        manager._owners['review-one'].stop.set()
    elif ending == 'restarted':
        manager._owners['review-one'] = Owner(manager.client)
    elif ending == 'expired':
        monkeypatch.setattr(scientific_jupyter.time, 'time', lambda: time.monotonic() + 10 ** 10)
    else:
        grants.issue(manager.client, 'review-one', 'http://127.0.0.1:8770')
    with pytest.raises(JupyterRefused):
        grants.resolve(connection['token'])
    if ending != 'reissued':
        assert not folder.exists() and not (tmp_path / 'jupyter' / 'connections' / f"{issued['name']}.json").exists()


def test_other_tokens_other_clients_and_an_app_restart_get_nothing(tmp_path):
    manager, grants, issued, connection = granted(tmp_path)
    for token in ('', 'x' * 15, 'y' * 43, connection['token'] + 'z'):
        with pytest.raises(JupyterRefused):
            grants.resolve(token)
    with pytest.raises(ValueError):
        grants.issue(object(), 'review-one', 'http://127.0.0.1:8770')
    JupyterGrants(manager, tmp_path / 'jupyter')  # what the app does when it starts again
    assert not (tmp_path / 'jupyter').exists()


def test_the_number_of_connections_is_bounded(tmp_path):
    manager = Manager()
    grants = JupyterGrants(manager, tmp_path / 'jupyter')
    for n in range(scientific_jupyter.MAX_GRANTS):
        manager._owners[f'review-{n}'] = Owner(manager.client)
        grants.issue(manager.client, f'review-{n}', 'http://127.0.0.1:8770')
    manager._owners['review-extra'] = Owner(manager.client)
    with pytest.raises(JupyterRefused, match='At most'):
        grants.issue(manager.client, 'review-extra', 'http://127.0.0.1:8770')


def test_the_kernel_route_refuses_browsers_and_missing_tokens_and_forwards_a_valid_cell(tmp_path):
    app = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
    built = app.state.app
    manager = Manager()
    notebooks = built.scientific_notebooks
    notebooks.manager = manager
    issued = notebooks.issue(manager.client, 'review-one', 'http://127.0.0.1:8770')
    token = json.loads((built.paths.home / 'jupyter' / 'connections' / f"{issued['name']}.json").read_text())['token']
    real = built.scientific_reviews.request
    built.scientific_reviews.request = manager.request
    try:
        client = TestClient(app, base_url='http://127.0.0.1:8770')
        cell = {'cell': 'jupyter-' + 'c' * 32, 'source': 'print("ran"); 42'}
        bearer = {'Authorization': f'Bearer {token}'}
        for headers in ({**bearer, 'Origin': 'http://127.0.0.1:8770'}, {**bearer, 'Sec-Fetch-Site': 'same-origin'}, {},
                        {'Authorization': f'Basic {token}'}):
            assert client.post('/api/jupyter/execute', json=cell, headers=headers).status_code == 403
        refused = client.post('/api/jupyter/execute', json=cell, headers={'Authorization': 'Bearer ' + 'n' * 43})
        assert refused.status_code == 403 and refused.json()['code'] == 'NOTEBOOK_CONNECTION'
        assert manager.calls == []
        answer = client.post('/api/jupyter/execute', json=cell, headers=bearer)
        assert answer.status_code == 200 and answer.json()['console']['result'] == '42'
        assert manager.calls == [(manager.client, 'review-one', {'op': 'python-resources', 'resource': REFERENCE,
                                                                 'source': cell['source'], 'cell': cell['cell']})]
        assert client.post('/api/jupyter/execute', json={**cell, 'extra': 1}, headers=bearer).status_code == 409
    finally:
        built.scientific_reviews.request = real


class Recorder:
    def __init__(self, delay=0.0, refusals=0):
        self.posts, self.delay, self.refusals = [], delay, refusals

    def post(self, action, payload, timeout=None):
        self.posts.append((action, payload))
        if action == 'execute':
            time.sleep(self.delay)
            return {'ok': True, 'console': {}}
        refused = self.refusals > 0
        self.refusals -= 1
        return {'ok': True, 'interrupt': {'cell': payload['cell'], 'requested': not refused}}


def test_the_kernel_sends_each_cell_once_and_interrupts_that_exact_cell():
    connection = Recorder(delay=0.4)
    asked = threading.Event()
    threading.Timer(0.15, asked.set).start()
    reply, sent = jupyter_kernel.answer_cell(connection, 'while True: pass', interrupted=asked.is_set)
    assert reply['ok'] and sent
    (first, run), (second, stop) = connection.posts
    assert (first, second) == ('execute', 'interrupt') and run['cell'] == stop['cell'] and run['cell'].startswith('jupyter-')
    assert jupyter_kernel._name('y = viewer.layers', 17) == ('viewer.layers', 4)
    assert jupyter_kernel._name('np.me', 5) == ('np.me', 0)


def test_the_kernel_only_talks_to_this_computer(tmp_path):
    path = tmp_path / 'connection.json'
    path.write_text(json.dumps({'format': 'hooke-jupyter-connection-v1', 'url': 'https://example.org', 'token': 't' * 43}))
    with pytest.raises(SystemExit, match='not a Hooke notebook connection'):
        jupyter_kernel.Connection(path)
    with pytest.raises(SystemExit, match='connection ended'):
        jupyter_kernel.Connection(tmp_path / 'revoked.json')
    path.write_text(json.dumps({'format': 'hooke-jupyter-connection-v1', 'url': 'http://127.0.0.1:9', 'token': 't' * 43}))
    answer = jupyter_kernel.Connection(path).post('execute', {'cell': 'jupyter-' + 'd' * 32, 'source': 'x'}, timeout=2)
    assert answer['ok'] is False and 'could not be reached' in answer['error']


def test_a_notebook_never_depends_on_what_the_studio_shows_but_still_ends_with_its_session():
    from orchestrator.scientific_reviews import ScientificReviews

    class Screen:
        def pin(self, *args, **kwargs):
            raise AssertionError('a notebook request must not consult the Studio screen')
    manager = ScientificReviews(Screen(), None, lambda project: (_ for _ in ()).throw(AssertionError('no layout')),
                                lambda: 'another-project')
    owner = Owner(object())
    assert asyncio.run(manager._check(owner, notebook=True)) is None  # Studio elsewhere, another Project active
    owner.stop.set()
    with pytest.raises(ObservationError_like()):
        asyncio.run(manager._check(owner, notebook=True))
    manager2 = ScientificReviews(Screen(), None, lambda project: None, lambda: 'p')
    studio = Owner(object())
    studio.origin.project = 'p'
    with pytest.raises(AssertionError, match='must not consult'):
        asyncio.run(manager2._check(studio))  # the Studio's own requests still check the screen


def ObservationError_like():
    from orchestrator.workspace_observation import ObservationError
    return (ObservationError, ValueError)


def test_a_notebook_may_only_run_code_and_ask_for_assistance(monkeypatch):
    from orchestrator.scientific_reviews import ScientificReviews
    manager = ScientificReviews(None, None, lambda project: None, lambda: 'p')
    owner = Owner(object())
    owner.id, owner.busy, owner.array_save, owner.edits, owner.completion_task = 'review-one', False, None, {}, None
    manager._owners['review-one'] = owner
    for request in ({'op': 'render', 'width': 10, 'height': 10}, {'op': 'save-retry', 'request': 'x' * 20},
                    {'op': 'labels', 'request': 'x' * 20}):
        with pytest.raises(ObservationError_like()):
            asyncio.run(manager.request(owner.origin.client, 'review-one', request, notebook=True))


def test_a_session_ending_removes_its_notebook_connection_at_once(tmp_path):
    manager, grants, issued, connection = granted(tmp_path)
    grants.forget('review-one')
    assert not (tmp_path / 'jupyter' / issued['name']).exists()
    assert not (tmp_path / 'jupyter' / 'connections' / f"{issued['name']}.json").exists()
    with pytest.raises(JupyterRefused):
        grants.resolve(connection['token'])
    from orchestrator.scientific_reviews import ScientificReviews
    reviews = ScientificReviews(None, None, None, None)
    ended = []
    reviews.ended_hooks.append(ended.append)
    owner = Owner(object())
    owner.id, owner.frame, owner.native_frame = 'review-two', None, None
    owner.ended = type('Done', (), {'result': lambda self: None})()
    reviews._owners['review-two'] = owner
    reviews._ended(owner)
    assert ended == ['review-two']


def test_an_interrupt_refused_before_the_cell_starts_is_sent_again_not_dropped():
    connection = Recorder(delay=1.6, refusals=1)
    asked, notes = threading.Event(), []
    threading.Timer(0.1, asked.set).start()
    reply, delivered = jupyter_kernel.answer_cell(connection, 'while True: pass', interrupted=asked.is_set,
                                                  notify=notes.append)
    interrupts = [payload for action, payload in connection.posts if action == 'interrupt']
    assert delivered and len(interrupts) == 2 and interrupts[0] == interrupts[1]
    assert notes == [jupyter_kernel.NOT_DELIVERED]


def test_a_press_while_an_interrupt_is_being_sent_is_sent_again_and_delivered():
    """Review round 2: one real SIGINT; a second press lands while that interrupt is being sent. It is not swallowed:
    the interrupt stays pending, the note is said, and it is sent again on the next pass and delivered."""
    import os, signal
    class Slow(Recorder):
        def post(self, action, payload, timeout=None):
            if action == 'interrupt' and not any(a == 'interrupt' for a, _ in self.posts):
                self.posts.append((action, payload))
                raise KeyboardInterrupt  # the researcher pressed again while it was being sent
            return super().post(action, payload, timeout)
    connection, notes = Slow(delay=1.5), []
    threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
    reply, delivered = jupyter_kernel.answer_cell(connection, 'while True: pass', notify=notes.append)
    interrupts = [payload for action, payload in connection.posts if action == 'interrupt']
    assert reply['ok'] and delivered and len(interrupts) == 2 and interrupts[0] == interrupts[1]
    assert notes == [jupyter_kernel.NOT_DELIVERED]


@pytest.mark.parametrize('answer', ['not json', '[1, 2]', 'null', '{"ok": true, "interrupt": "yes"}'])
def test_an_unreadable_interrupt_answer_is_not_a_delivery_and_is_sent_again(answer):
    """Review round 2: a malformed reply (bad JSON, a non-dict, a wrong shape) never escapes; it is retried."""
    import json as _json
    class Garbled(Recorder):
        def post(self, action, payload, timeout=None):
            if action == 'interrupt' and not any(a == 'interrupt' for a, _ in self.posts):
                self.posts.append((action, payload))
                try:
                    return _json.loads(answer)
                except ValueError as error:
                    raise ValueError(f'unreadable: {error}') from error
            return super().post(action, payload, timeout)
    connection, notes, asked = Garbled(delay=1.5), [], threading.Event()
    threading.Timer(0.1, asked.set).start()
    reply, delivered = jupyter_kernel.answer_cell(connection, 'while True: pass', interrupted=asked.is_set, notify=notes.append)
    assert reply['ok'] and delivered and [a for a, _ in connection.posts].count('interrupt') == 2
    assert notes == [jupyter_kernel.NOT_DELIVERED]


def test_a_malformed_answer_from_hooke_is_said_not_raised(monkeypatch):
    import io, urllib.error
    connection = object.__new__(jupyter_kernel.Connection)
    connection.url, connection.token = 'http://127.0.0.1:1', 't'
    for body in (b'not json', b'[1]'):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *exc): return False
        monkeypatch.setattr(jupyter_kernel.urllib.request, 'urlopen', lambda request, timeout, body=body: Response(body))
        answer = connection.post('interrupt', {'cell': 'x'})
        assert answer['ok'] is False and 'could not be read' in answer['error']
        def refused(request, timeout, body=body):
            raise urllib.error.HTTPError('u', 409, 'x', {}, io.BytesIO(body))
        monkeypatch.setattr(jupyter_kernel.urllib.request, 'urlopen', refused)
        assert connection.post('interrupt', {'cell': 'x'}) == {'ok': False, 'error': 'Hooke answered 409', 'next': ''}


def test_stopping_a_session_runs_its_ended_hooks_itself():
    from concurrent.futures import Future
    from orchestrator.scientific_reviews import ScientificReviews
    reviews = ScientificReviews(None, None, None, None)
    ended = []
    reviews.ended_hooks.append(ended.append)
    owner = Owner(object())
    owner.id, owner.frame, owner.native_frame = 'review-three', None, None
    owner.ended = Future()
    owner.ended.set_result(None)
    reviews._owners['review-three'] = owner
    assert asyncio.run(reviews.stop(owner.origin.client, 'review-three')) is None
    assert ended == ['review-three']
