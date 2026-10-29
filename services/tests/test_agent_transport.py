"""Actual Swift HTTP transport against synthetic local servers; no live credential claim."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import platform
import shutil
import subprocess
import threading

import pytest
from orchestrator.paths import REPO

pytestmark = pytest.mark.skipif(platform.system() != 'Darwin' or not shutil.which('swiftc'),
                               reason='requires the macOS Swift transport')

@pytest.fixture(scope='module')
def transport_program(tmp_path_factory):
    executable = tmp_path_factory.mktemp('agent-transport')/'probe'
    subprocess.run(['swiftc', '-swift-version', '5',
                    str(REPO/'packaging/macos/HookeUI/AgentShared.swift'),
                    str(REPO/'packaging/macos/tests/agent-transport/main.swift'),
                    '-o', str(executable)], check=True, capture_output=True, timeout=300)
    return executable

@contextmanager
def server(reply):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            length = int(self.headers.get('Content-Length', '0'))
            seen.append((self.path, self.headers.get('Authorization'), self.rfile.read(length), self.headers.get('X-Hooke-Studio')))
            status, location = reply(self.path)
            self.send_response(status)
            if location: self.send_header('Location', location)
            self.send_header('Content-Length', '0')
            self.end_headers()
        do_POST = do_GET
        def log_message(self, *args): pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}', seen
    finally:
        http.shutdown(); http.server_close(); thread.join(5)

@pytest.mark.parametrize('mode', ['data', 'download'])
@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_redirect_never_replays_credentials_or_body(transport_program, mode, status):
    with server(lambda _: (200, None)) as (outside, other_seen):
        with server(lambda _: (status, outside+'/outside-door')) as (base, seen):
            result = subprocess.run([str(transport_program), mode, base+'/api/agent/v1/resource'],
                                    check=True, capture_output=True, text=True, timeout=20)
            assert result.stdout.strip() == str(status)
            assert len(seen) == 1 and seen[0][1] == 'Bearer synthetic-test-credential'
            assert seen[0][3] == 'synthetic-owner-window'
            assert other_seen == [], 'redirect contacted another process'

@pytest.mark.parametrize('mode', ['data', 'download'])
def test_same_origin_redirect_refused_and_direct_success_preserved(transport_program, mode):
    with server(lambda path: (302, '/private') if path == '/redirect' else (200, None)) as (base, seen):
        refused = subprocess.run([str(transport_program), mode, base+'/redirect'],
                                 check=True, capture_output=True, text=True, timeout=20)
        assert refused.stdout.strip() == '302'
        assert [request[0] for request in seen] == ['/redirect']
        accepted = subprocess.run([str(transport_program), mode, base+'/direct'],
                                  check=True, capture_output=True, text=True, timeout=20)
        assert accepted.stdout.strip() == '200'
        assert [request[0] for request in seen] == ['/redirect', '/direct']
