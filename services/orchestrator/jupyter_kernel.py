"""Hooke's Jupyter kernel. It runs nothing itself.

Jupyter starts this file with its own Python (which has ipykernel). Each cell, completion, help request and interrupt
is sent to the one Hooke scientific session named in its connection file, with that file's token, and runs there
through the same checks as the Studio console. Stopping or restarting this kernel in Jupyter does not change the
Hooke session; use Stop Python session in Hooke for that. This file imports nothing from Hooke.
"""
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

TIMEOUT_S = 3600


class Connection:
    def __init__(self, path):
        try:
            with open(path, encoding='utf-8') as stream:
                value = json.load(stream)
        except FileNotFoundError:
            raise SystemExit('This Hooke notebook connection ended (revoked, its session stopped, or Hooke restarted). '
                             'Choose Connect a notebook in Hooke again.') from None
        if (type(value) is not dict or value.get('format') != 'hooke-jupyter-connection-v1'
                or not str(value.get('url', '')).startswith(('http://127.0.0.1:', 'http://localhost:'))
                or type(value.get('token')) is not str):
            raise SystemExit('This is not a Hooke notebook connection file. Connect the notebook again from Hooke.')
        self.url, self.token = value['url'], value['token']

    def post(self, action, payload, timeout=TIMEOUT_S):
        """Hooke's answer as a dict; a refusal comes back as {'ok': False, 'error': ..., 'next': ...}."""
        request = urllib.request.Request(f'{self.url}/api/jupyter/{action}', data=json.dumps(payload).encode('utf-8'),
                                         method='POST', headers={'Content-Type': 'application/json',
                                                                 'Authorization': f'Bearer {self.token}'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                answer = json.loads(response.read())
            return answer if type(answer) is dict else {'ok': False, 'error': 'Hooke\'s answer could not be read.', 'next': ''}
        except urllib.error.HTTPError as error:
            try:
                answer = json.loads(error.read())
            except (ValueError, RecursionError):
                answer = None
            return answer if type(answer) is dict else {'ok': False, 'error': f'Hooke answered {error.code}', 'next': ''}
        except (ValueError, RecursionError) as error:
            return {'ok': False, 'error': f'Hooke\'s answer could not be read ({error}).', 'next': ''}
        except (urllib.error.URLError, OSError) as error:
            return {'ok': False, 'error': f'Hooke could not be reached ({error}).',
                    'next': 'Check that Hooke is open and this session is still running.'}


RETRY_S = 0.5
NOT_DELIVERED = 'Interrupt not delivered yet; sending it again.\n'


def answer_cell(connection, code, interrupted=lambda: False, notify=lambda text: sys.stderr.write(text)):
    """Run one cell in Hooke and return (reply, interrupt_delivered). Interrupts go to Hooke for this exact cell.

    An interrupt counts as delivered only when Hooke confirms it was requested. Until then it stays pending and is
    sent again on the next pass of the wait: one sent before the cell has started (answered "not requested"), one
    whose answer could not be read, and a press that arrives while an interrupt is being sent are never dropped.
    """
    cell = 'jupyter-' + uuid.uuid4().hex
    result, state = {}, {'sent': False, 'pending': False, 'pressed': False, 'last': 0.0}
    finished = threading.Event()

    def run():
        try:
            result['reply'] = connection.post('execute', {'cell': cell, 'source': code})
        except Exception as error:  # an unreadable answer is said, never mistaken for a result
            result['reply'] = {'ok': False, 'error': f'Hooke\'s answer could not be read ({error!r}).',
                               'next': 'Inspect this session\'s Python history in Hooke before running the cell again.'}
        finally:
            finished.set()

    def press():
        """A researcher's Ctrl-C or Interrupt: pending until Hooke confirms it; sent on the next pass."""
        if not state['sent']:
            state['pending'] = state['pressed'] = True
            state['last'] = 0.0

    def interrupt():
        state['last'] = time.monotonic()
        try:
            reply = connection.post('interrupt', {'cell': cell}, timeout=30)
        except KeyboardInterrupt:
            reply = None  # pressed again while sending: still pending, so it is sent again on the next pass
            state['pressed'] = True
        except Exception:  # noqa: BLE001 - an unreadable answer is not a delivery
            reply = None
        answer = reply.get('interrupt') if type(reply) is dict else None
        if type(reply) is dict and reply.get('ok') is True and type(answer) is dict and answer.get('requested') is True:
            state['sent'], state['pending'] = True, False
            return
        state['pending'] = True
        if state['pressed']:  # once per press; the pending retries that follow are silent
            state['pressed'] = False
            notify(NOT_DELIVERED)
    threading.Thread(target=run, daemon=True).start()
    # Wait on an event, never Thread.join: a KeyboardInterrupt inside join can leave the thread reported as finished
    # while it still runs, and the cell's answer would then be lost. A press only marks the interrupt pending; it
    # is sent inside the guarded loop, never from the handler, so a further press can never escape this function.
    while not finished.is_set():
        try:
            finished.wait(0.1)
            if not state['sent'] and not state['pending'] and interrupted():
                press()
            if state['pending'] and not state['sent'] and time.monotonic() - state['last'] >= RETRY_S:
                interrupt()
        except KeyboardInterrupt:
            press()
    return result.get('reply') or {'ok': False, 'error': 'Hooke gave no answer.', 'next': ''}, state['sent']


def _name(code, cursor):
    """The dotted name ending at the cursor, which is all Hooke's assistance inspects."""
    start = cursor
    while start > 0 and (code[start - 1].isalnum() or code[start - 1] in '_.'):
        start -= 1
    return code[start:cursor], start


def main(argv=None):
    from ipykernel.kernelapp import IPKernelApp
    from ipykernel.kernelbase import Kernel

    argv = list(sys.argv[1:] if argv is None else argv)
    if '--hooke' not in argv or argv.index('--hooke') + 1 >= len(argv):
        raise SystemExit('Start this kernel from the kernelspec Hooke wrote; it names the connection file.')
    at = argv.index('--hooke')
    connection = Connection(argv[at + 1])
    del argv[at:at + 2]

    class HookeKernel(Kernel):
        implementation = 'hooke'
        implementation_version = '1'
        language = 'python'
        language_version = '3'
        language_info = {'name': 'python', 'mimetype': 'text/x-python', 'file_extension': '.py'}
        banner = ('Cells run in the connected Hooke scientific session, on its saved input, and are kept in its '
                  'Python history. Restarting this kernel does not restart the Hooke session.')

        def _refused(self, reply):
            text = f"{reply.get('error', 'Hooke refused this cell.')}\n{reply.get('next', '')}".strip() + '\n'
            self.send_response(self.iopub_socket, 'stream', {'name': 'stderr', 'text': text})
            return {'status': 'error', 'execution_count': self.execution_count, 'ename': 'HookeRefused',
                    'evalue': reply.get('error', ''), 'traceback': [text]}

        def do_execute(self, code, silent, store_history=True, user_expressions=None, allow_stdin=False, **kwargs):
            reply, _ = answer_cell(connection, code, notify=lambda text: self.send_response(
                self.iopub_socket, 'stream', {'name': 'stderr', 'text': text}))
            if not reply.get('ok') or type(reply.get('console')) is not dict:
                return self._refused(reply)
            console = reply['console']
            if not silent:
                for name in ('stdout', 'stderr'):
                    if console.get(name):
                        self.send_response(self.iopub_socket, 'stream', {'name': name, 'text': console[name]})
                if console.get('result'):
                    self.send_response(self.iopub_socket, 'execute_result', {
                        'execution_count': self.execution_count, 'data': {'text/plain': console['result']}, 'metadata': {}})
                if console.get('truncated'):
                    self.send_response(self.iopub_socket, 'stream', {'name': 'stderr',
                                       'text': 'The output exceeded its allowance; Hooke kept the truncated transcript.\n'})
            if console.get('error'):
                first = console['error'].splitlines()[0] if console['error'] else 'Error'
                ename, _, evalue = first.partition(': ')
                if not silent:
                    self.send_response(self.iopub_socket, 'error', {'ename': ename, 'evalue': evalue,
                                                                    'traceback': console['error'].splitlines()})
                return {'status': 'error', 'execution_count': self.execution_count, 'ename': ename, 'evalue': evalue,
                        'traceback': console['error'].splitlines()}
            return {'status': 'ok', 'execution_count': self.execution_count, 'payload': [], 'user_expressions': {}}

        def do_complete(self, code, cursor_pos):
            cursor = len(code) if cursor_pos is None else cursor_pos
            expression, start = _name(code, cursor)
            reply = connection.post('assist', {'mode': 'complete', 'expression': expression}, timeout=30)
            found = reply.get('assistance') if reply.get('ok') else None
            matches = found.get('matches', []) if type(found) is dict else []
            prefix = expression.rpartition('.')[0]
            return {'status': 'ok', 'matches': [f'{prefix}.{m}' if prefix else m for m in matches],
                    'cursor_start': start, 'cursor_end': cursor, 'metadata': {}}

        def do_inspect(self, code, cursor_pos, detail_level=0, omit_sections=()):
            expression, _ = _name(code, len(code) if cursor_pos is None else cursor_pos)
            reply = connection.post('assist', {'mode': 'help', 'expression': expression}, timeout=30) if expression else {}
            found = reply.get('assistance') if reply.get('ok') else None
            text = found.get('text', '') if type(found) is dict else ''
            return {'status': 'ok', 'found': bool(text), 'data': {'text/plain': text} if text else {}, 'metadata': {}}

        def do_shutdown(self, restart):
            return {'status': 'ok', 'restart': restart}

    IPKernelApp.launch_instance(argv=argv, kernel_class=HookeKernel)


if __name__ == '__main__':
    main()
