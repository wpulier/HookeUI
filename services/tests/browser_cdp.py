"""A real Chromium in its own profile, driven over the DevTools protocol with trusted input events.

Isolated-profile browser evidence (acceptance.md §4): every run gets a new, empty user-data directory, the page is
the production Studio build served by a real orchestrator, and clicks, keys and typing are dispatched as browser
input (Input.dispatch*), the same events a person's mouse and keyboard produce. Page script is used only to read what
is on screen and where a control is; it never calls the Studio's own functions or changes its state.

HOOKE_BROWSER names the Chromium executable (for example Playwright's "Google Chrome for Testing"). Nothing here
downloads a browser.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from websockets.sync.client import connect

KEYS = {
    'ArrowLeft': (37, 'ArrowLeft'), 'ArrowRight': (39, 'ArrowRight'), 'ArrowUp': (38, 'ArrowUp'),
    'ArrowDown': (40, 'ArrowDown'), 'Home': (36, 'Home'), 'End': (35, 'End'), 'Enter': (13, 'Enter'),
    'Tab': (9, 'Tab'), 'Escape': (27, 'Escape'), 'Backspace': (8, 'Backspace'), 'Delete': (46, 'Delete'),
    ' ': (32, 'Space'),
}
MODIFIERS = {'Alt': 1, 'Control': 2, 'Meta': 4, 'Shift': 8}


class BrowserError(RuntimeError):
    pass


def executable():
    location = os.environ.get('HOOKE_BROWSER')
    return Path(location) if location and Path(location).is_file() else None


class Browser:
    """One Chromium process with a fresh profile; close() ends it and removes the profile."""

    def __init__(self, path, *, width=1440, height=900, scale=1, log=None):
        self.profile = Path(tempfile.mkdtemp(prefix='hooke-browser-profile-'))
        self.process = self.socket = None
        try:
            self.log = log
            args = [str(path), '--headless=new', '--remote-debugging-port=0', f'--user-data-dir={self.profile}',
                    '--no-first-run', '--no-default-browser-check', '--disable-extensions', '--disable-sync',
                    '--disable-background-networking', '--disable-component-update', '--password-store=basic',
                    '--use-mock-keychain', '--disable-features=Translate,MediaRouter', '--mute-audio',
                    f'--window-size={width},{height}', 'about:blank']
            # In the test's own process group, so a runner that stops the tests stops the browser too.
            self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            port_file, deadline = self.profile / 'DevToolsActivePort', time.monotonic() + 30
            while not port_file.is_file() or len(port_file.read_text().splitlines()) < 2:
                if time.monotonic() > deadline or self.process.poll() is not None:
                    raise BrowserError('Chromium did not start its DevTools endpoint')
                time.sleep(.05)
            port, path_ = port_file.read_text().splitlines()[:2]
            self.socket = connect(f'ws://127.0.0.1:{port}{path_}', max_size=64 << 20, open_timeout=10,
                                  ping_interval=None, close_timeout=1)
            # A local pipe: long reference runs leave it unread. A full receive queue can block the close
            # handshake; bound that wait before close() reaps our owned process and removes its profile.
            self._id = 0
            self.events = []
            self.width, self.height, self.scale = width, height, scale
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                error.add_note(f'Browser startup cleanup also failed: {cleanup_error}')
            raise

    def call(self, method, params=None, session=None, timeout=60):
        self._id += 1
        ident = self._id
        message = {'id': ident, 'method': method, 'params': params or {}}
        if session:
            message['sessionId'] = session
        self.socket.send(json.dumps(message))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrowserError(f'{method} did not answer within {timeout} s')
            reply = json.loads(self.socket.recv(timeout=remaining))
            if reply.get('id') == ident:
                if 'error' in reply:
                    raise BrowserError(f'{method}: {reply["error"]}')
                return reply.get('result', {})
            self.events.append(reply)

    def page(self, url):
        target = self.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        session = self.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        page = Page(self, session)
        for domain in ('Page', 'Runtime', 'Log'):
            page.call(f'{domain}.enable')
        page.viewport(self.width, self.height, self.scale)
        page.goto(url)
        return page

    def close(self):
        try:
            if self.socket is not None:
                socket, self.socket = self.socket, None
                socket.close()
        finally:
            try:
                if self.process is not None and self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=5)
            finally:
                shutil.rmtree(self.profile, ignore_errors=True)


class Page:
    def __init__(self, browser, session):
        self.browser, self.session = browser, session

    def call(self, method, params=None, timeout=60):
        return self.browser.call(method, params, self.session, timeout)

    def viewport(self, width, height, scale=1):
        self.call('Emulation.setDeviceMetricsOverride', {'width': width, 'height': height,
                                                          'deviceScaleFactor': scale, 'mobile': False})
        self.size = (width, height, scale)

    def goto(self, url):
        self.call('Page.navigate', {'url': url})
        self.wait("document.readyState === 'complete'", timeout=30)

    def eval(self, expression, timeout=60):
        """Read from the page (never used to operate a control)."""
        reply = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True,
                                               'awaitPromise': True}, timeout)
        if 'exceptionDetails' in reply:
            raise BrowserError(f'Page read failed: {reply["exceptionDetails"].get("text")} '
                               f'{reply["exceptionDetails"].get("exception", {}).get("description", "")}')
        return reply['result'].get('value')

    def wait(self, expression, timeout=60, what=None):
        deadline, value = time.monotonic() + timeout, None
        while time.monotonic() < deadline:
            value = self.eval(f'(()=>{{try{{const v=({expression});return v instanceof Node?true:v}}catch(e){{return null}}}})()')
            if value:
                return value
            time.sleep(.1)
        raise BrowserError(f'Timed out waiting for {what or expression}')

    def box(self, selector_js):
        """The visible centre of the element an expression returns, in CSS pixels."""
        rect = self.eval(f'''(()=>{{const e=({selector_js}); if(!e) return null; e.scrollIntoView({{block:'center',inline:'center'}});
            const r=e.getBoundingClientRect(); return [r.left,r.top,r.width,r.height]}})()''')
        if not rect or rect[2] <= 0 or rect[3] <= 0:
            raise BrowserError(f'No visible element for {selector_js}')
        return rect[0] + rect[2] / 2, rect[1] + rect[3] / 2

    def focus(self, selector_js):
        """Move keyboard focus to a control, as Tab traversal would; it changes no value and fires no input."""
        self.wait(f"(()=>{{const e=({selector_js});return !!e&&!e.disabled}})()", 30, f'{selector_js} to be enabled')
        if not self.eval(f"(()=>{{const e=({selector_js});if(!e||e.disabled)return false;e.scrollIntoView({{block:'center'}});"
                         "e.focus();return document.activeElement===e})()"):
            raise BrowserError(f'Could not focus {selector_js}')

    def mouse(self, kind, x, y, button='left', clicks=1, modifiers=0):
        self.call('Input.dispatchMouseEvent', {'type': kind, 'x': x, 'y': y, 'button': button,
                                               'clickCount': clicks, 'modifiers': modifiers})

    def click(self, selector_js, *, offset=None):
        x, y = self.box(selector_js)
        if offset:
            x, y = x + offset[0], y + offset[1]
        # Collapsed details can retain layout boxes/offsetParent. A trusted click
        # must reach the intended visible control, not whatever covers that box.
        state = self.eval(f"""(()=>{{const e=({selector_js}),hit=document.elementFromPoint({x}, {y});
            return {{visible:!!e?.checkVisibility(),disabled:!!e?.disabled,
                     target:!!e&&!!hit&&(e===hit||e.contains(hit))}}}})()""")
        if not state or not state['visible'] or state['disabled'] or not state['target']:
            raise BrowserError(f'Control is not a visible enabled hit target: {selector_js}; {state}')
        self.mouse('mouseMoved', x, y, button='none', clicks=0)
        self.mouse('mousePressed', x, y)
        self.mouse('mouseReleased', x, y)

    def drag(self, start, end, steps=8):
        self.mouse('mouseMoved', *start, button='none', clicks=0)
        self.mouse('mousePressed', *start)
        for n in range(1, steps + 1):
            self.mouse('mouseMoved', start[0] + (end[0] - start[0]) * n / steps,
                       start[1] + (end[1] - start[1]) * n / steps, clicks=0)
        self.mouse('mouseReleased', *end)

    def select_all(self):
        """Command/Control+A in the focused field (the editing command a real keystroke triggers)."""
        base = {'modifiers': MODIFIERS['Meta'], 'windowsVirtualKeyCode': 65, 'code': 'KeyA', 'key': 'a'}
        self.call('Input.dispatchKeyEvent', {'type': 'rawKeyDown', **base, 'commands': ['selectAll']})
        self.call('Input.dispatchKeyEvent', {'type': 'keyUp', **base})

    def key(self, name, modifiers=()):
        mask = sum(MODIFIERS[m] for m in modifiers)
        if name in KEYS:
            code, key_code = KEYS[name]
            text = ' ' if name == ' ' else ('\r' if name == 'Enter' else '')
            key = name
        elif len(name) == 1:
            code, key_code, key = ord(name.upper()), 'Key' + name.upper() if name.isalpha() else 'Digit' + name, name
            text = '' if mask & (MODIFIERS['Control'] | MODIFIERS['Meta'] | MODIFIERS['Alt']) else name
        else:
            raise BrowserError(f'Unsupported key {name}')
        base = {'modifiers': mask, 'windowsVirtualKeyCode': code, 'code': key_code, 'key': key}
        self.call('Input.dispatchKeyEvent', {'type': 'keyDown' if text else 'rawKeyDown', **base,
                                             **({'text': text, 'unmodifiedText': text} if text else {})})
        self.call('Input.dispatchKeyEvent', {'type': 'keyUp', **base})

    def type(self, text):
        for char in text:
            if char == '\n':
                self.key('Enter')
            else:
                self.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'text': char, 'unmodifiedText': char,
                                                     'key': char})
                self.call('Input.dispatchKeyEvent', {'type': 'keyUp', 'key': char})

    def insert(self, text):
        """Paste-like text entry into the focused field (Input.insertText, as an input method commits it)."""
        self.call('Input.insertText', {'text': text})

    def screenshot(self, path):
        data = self.call('Page.captureScreenshot', {'format': 'png'})['data']
        Path(path).write_bytes(base64.b64decode(data))
        return path

    def requests(self, fragment):
        """Requests the page sent whose URL contains fragment, in order: (time, url, decoded JSON body or None).

        Needs record_requests() first; reading the browser's own network log changes nothing on the page.
        """
        out = []
        for event in self.browser.events:
            if event.get('method') != 'Network.requestWillBeSent' or event.get('sessionId') != self.session:
                continue
            request = event['params']['request']
            if fragment not in request['url']:
                continue
            try:
                body = json.loads(request.get('postData') or 'null')
            except ValueError:
                body = None
            out.append((event['params'].get('timestamp'), request['url'], body, event['params'].get('requestId')))
        return out

    def response_json(self, request_id):
        """The JSON the page received for one of its requests (the browser's own copy; nothing is re-sent)."""
        reply = self.call('Network.getResponseBody', {'requestId': request_id})
        text = base64.b64decode(reply['body']).decode() if reply.get('base64Encoded') else reply['body']
        return json.loads(text)

    def record_requests(self):
        self.call('Network.enable', {'maxPostDataSize': 1 << 20})

    def drain(self):
        """Read events that arrived while nothing was called (a short no-op round trip)."""
        self.call('Runtime.evaluate', {'expression': '0'})

    def problems(self):
        """Uncaught page exceptions seen so far."""
        return [e['params'] for e in self.browser.events if e.get('method') == 'Runtime.exceptionThrown'
                and e.get('sessionId') == self.session]
