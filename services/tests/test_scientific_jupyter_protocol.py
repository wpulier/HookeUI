"""H09: Hooke's kernel speaks the real Jupyter protocol (jupyter_client and ipykernel), launched from its kernelspec.

Opt-in. HOOKE_JUPYTER_PYTHON names a Python with jupyter_client and ipykernel (a Jupyter installation). A stand-in
Hooke answers in this process, so this checks the protocol and the forwarding, not a napari session; absence is a skip.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading

import pytest

from orchestrator import scientific_jupyter

TOKEN = 'k' * 43
CLIENT = r'''
import json, sys, time
from jupyter_client.manager import KernelManager
km = KernelManager(kernel_name='hooke-test')
km.start_kernel()
kc = km.client(); kc.start_channels(); kc.wait_for_ready(timeout=60)
def run(code, interrupt_after=None):
    msg = kc.execute(code)
    if interrupt_after is not None:
        time.sleep(interrupt_after); km.interrupt_kernel()
    reply = kc.get_shell_msg(timeout=60)
    while reply['parent_header'].get('msg_id') != msg: reply = kc.get_shell_msg(timeout=60)
    out = []
    while True:
        m = kc.get_iopub_msg(timeout=60)
        if m['parent_header'].get('msg_id') != msg: continue
        if m['msg_type'] == 'status' and m['content']['execution_state'] == 'idle': break
        if m['msg_type'] in ('stream', 'execute_result', 'error'): out.append([m['msg_type'], m['content']])
    return {'reply': reply['content'], 'iopub': out}
result = {'ran': run('print("ran"); 6 * 7'), 'failed': run('1/0'), 'interrupted': run('slow', interrupt_after=1.0)}
c = kc.complete('np.me', 5); result['complete'] = kc.get_shell_msg(timeout=30)['content']
i = kc.inspect('np.mean', 7); result['inspect'] = kc.get_shell_msg(timeout=30)['content']
kc.stop_channels(); km.shutdown_kernel(now=True)
print(json.dumps(result))
'''


class Hooke(BaseHTTPRequestHandler):
    seen, interrupted = [], threading.Event()

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        action = self.path.rsplit('/', 1)[1]
        Hooke.seen.append((action, body, self.headers.get('Authorization'), self.headers.get('Origin')))
        if self.headers.get('Authorization') != f'Bearer {TOKEN}':
            return self.answer(403, {'ok': False, 'error': 'no token', 'next': ''})
        console = {'format': 'scientific-console-v1', 'sequence': 1, 'stdout': '', 'stderr': '', 'result': '',
                   'error': '', 'truncated': False, 'privatePreview': True}
        if action == 'interrupt':
            Hooke.interrupted.set()
            return self.answer(200, {'ok': True, 'interrupt': {'cell': body['cell'], 'requested': True}})
        if action == 'assist':
            if body['mode'] == 'complete':
                return self.answer(200, {'ok': True, 'assistance': {'matches': ['mean', 'median'], 'text': ''}})
            return self.answer(200, {'ok': True, 'assistance': {'matches': [], 'text': 'mean(a, axis=None)'}})
        if body['source'] == 'slow':
            Hooke.interrupted.wait(20)
            console['error'] = 'KeyboardInterrupt: interrupted by request'
        elif body['source'] == '1/0':
            console['error'] = 'ZeroDivisionError: division by zero\nTraceback (most recent call last; locations only)'
        else:
            console.update(stdout='ran\n', result='42')
        return self.answer(200, {'ok': True, 'console': console})

    def answer(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_a_real_jupyter_client_runs_completes_inspects_and_interrupts_through_hooke(tmp_path):
    python = os.environ.get('HOOKE_JUPYTER_PYTHON')
    if not python:
        pytest.skip('requires a Python with jupyter_client and ipykernel (HOOKE_JUPYTER_PYTHON)')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Hooke)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        folder = tmp_path / 'share' / 'kernels' / 'hooke-test'
        folder.mkdir(parents=True)
        connection = folder / 'connection.json'
        connection.write_text(json.dumps({'format': scientific_jupyter.FORMAT, 'review': 'review-one', 'token': TOKEN,
                                          'url': f'http://127.0.0.1:{server.server_address[1]}'}))
        (folder / 'kernel.json').write_text(json.dumps({'argv': ['python3', str(scientific_jupyter.KERNEL), '-f',
                                                        '{connection_file}', '--hooke', str(connection)],
                                                        'display_name': 'Hooke test', 'language': 'python'}))
        environment = {key: os.environ[key] for key in ('PATH', 'TMPDIR') if key in os.environ}
        environment.update(JUPYTER_PATH=str(tmp_path / 'share'), JUPYTER_RUNTIME_DIR=str(tmp_path / 'runtime'),
                           JUPYTER_DATA_DIR=str(tmp_path / 'data'), JUPYTER_CONFIG_DIR=str(tmp_path / 'config'),
                           HOME=str(tmp_path), IPYTHONDIR=str(tmp_path / 'ipython'))
        done = subprocess.run([python, '-c', CLIENT], env=environment, capture_output=True, text=True, timeout=180)
        assert done.returncode == 0, done.stderr[-3000:]
        result = json.loads(done.stdout.strip().splitlines()[-1])
    finally:
        server.shutdown()
    ran = result['ran']
    assert ran['reply']['status'] == 'ok'
    assert ['stream', {'name': 'stdout', 'text': 'ran\n'}] in ran['iopub']
    assert any(kind == 'execute_result' and content['data'] == {'text/plain': '42'} for kind, content in ran['iopub'])
    assert result['failed']['reply']['status'] == 'error' and result['failed']['reply']['ename'] == 'ZeroDivisionError'
    assert result['interrupted']['reply']['ename'] == 'KeyboardInterrupt', (result['interrupted'], done.stderr[-4000:])
    executes = [body for action, body, _, _ in Hooke.seen if action == 'execute']
    interrupts = [body for action, body, _, _ in Hooke.seen if action == 'interrupt']
    assert [e['source'] for e in executes] == ['print("ran"); 6 * 7', '1/0', 'slow']
    assert interrupts == [{'cell': executes[2]['cell']}]  # the exact running cell, once
    assert result['complete']['matches'] == ['np.mean', 'np.median'] and result['complete']['cursor_start'] == 0
    assert result['inspect']['found'] and result['inspect']['data']['text/plain'] == 'mean(a, axis=None)'
    assert all(token == f'Bearer {TOKEN}' and origin is None for _, _, token, origin in Hooke.seen)
