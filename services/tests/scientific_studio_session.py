"""Operate an opened scientific session in the Studio the way a person does, and read back what it shows.

Built on browser_cdp (a real Chromium, fresh profile, trusted input events) and scientific_studio_stack (a private
orchestrator and Studio). Every operation here clicks, types or presses keys; page script only finds controls and
reads text. `python()` uses the session's own Python panel (the console in the actual review worker), the API
equivalent napari users have, for setup and for reading native state with the shared reference SNAPSHOT.
"""
from __future__ import annotations

import json
import time

from tests.browser_cdp import BrowserError

BUTTON = ("[...document.querySelectorAll('button')].find(e=>e.offsetParent&&!e.closest('[hidden]')"
          "&&(e.getAttribute('aria-label')||e.textContent).trim()===%s)")
LABELLED = "[...document.querySelectorAll('%s')].find(e=>e.offsetParent&&e.getAttribute('aria-label')===%s)"


def button(label):
    return BUTTON % json.dumps(label)


def labelled(label, tag='input,select,textarea,button'):
    return LABELLED % (tag, json.dumps(label))


# A Data row opens in the Data reader on the right (ticket 66); a scientific document's viewer is its full tool, in the
# reader's ⋯ as "Open the scientific viewer".
READER_MORE = "[...document.querySelectorAll('.data-reader-side .rd-more')].find(e=>e.offsetParent)"


def menu_item(label):
    return ("[...document.querySelectorAll('.mm-drawer-menu button')].find(e=>e.offsetParent"
            "&&e.textContent.trim()===%s)") % json.dumps(label)


def result(title):
    """A saved result's row in Project Data: the row is its own button, "Open <title>" (workspace-feel step 4), and an
    input's row can carry the same words, so only result rows count."""
    return ("[...document.querySelectorAll('.study-result-card button')].find(e=>e.offsetParent&&!e.closest('[hidden]')"
            "&&(e.getAttribute('aria-label')||e.textContent).trim()===%s)") % json.dumps('Open ' + title)


class StudioSession:
    def __init__(self, page, *, timeout=180):
        self.page = page
        self.timeout = timeout

    # --- opening -------------------------------------------------------------------------------
    def open_from_project_data(self, title):
        """Project Data → the result's row (Open <title>) → the reader's ⋯ › Open the scientific viewer → Open session;
        returns seconds until the View controls exist."""
        page = self.page
        page.wait(button('Data'), 60, 'the Studio to load')
        page.click(button('Data'))
        page.wait(result(title), 30, 'the saved result in Project Data')
        page.click(result(title))
        page.wait(READER_MORE, 30, 'the Data reader')
        page.click(READER_MORE)
        page.wait(menu_item('Open the scientific viewer'), 10, "the reader's ⋯")
        page.click(menu_item('Open the scientific viewer'))
        page.wait(button('Open session'), 30, 'the saved document window')
        started = time.monotonic()
        page.click(button('Open session'))
        page.wait("!!document.querySelector('.scientific-navigation fieldset')", self.timeout, 'the opened session')
        self.tab('View')
        self.settle()
        return time.monotonic() - started

    def tab(self, name):
        self.page.click(button(name))
        time.sleep(.2)

    # --- reading -----------------------------------------------------------------------------
    def status(self):
        return self.page.eval("document.querySelector('.scientific-navigation-actions [role=status]')?.textContent||''")

    def settle(self, timeout=60):
        """Wait until the View panel accepts input (no draft, no request in flight)."""
        # Roll axes is enabled only while the view is available, nothing is in flight and nothing is unsent.
        self.page.wait(f"(()=>{{const b={button('Check current view')},r={button('Roll axes')};return b&&!b.disabled&&r&&!r.disabled&&"
                       f"![...document.querySelectorAll('.scientific-navigation button')].some(e=>e.textContent.trim()==='Discard unsent view changes'&&e.offsetParent)}})()",
                       timeout, 'a settled view')
        time.sleep(.1)

    def dims(self):
        """What the Dimensions controls show, per axis."""
        return self.page.eval("""(()=>{const root=document.querySelector('.scientific-navigation fieldset');
          const rows=[...root.querySelectorAll('label')].filter(l=>l.querySelector('input[type=range]'));
          return {display:root.querySelector('select[aria-label="Displayed dimensions"]').value,
            keyboard:root.querySelector('.scientific-keyboard-axis')?.textContent||'',
            order:[...root.querySelectorAll('details select')].map(s=>Number(s.value)),
            axes:rows.map((row,axis)=>({hidden:row.hidden,label:row.querySelector('span').textContent,
              slider:Number(row.querySelector('input[type=range]').value),max:Number(row.querySelector('input[type=range]').max),
              index:Number(row.querySelector('input[type=number]').value),
              world:row.querySelector(`input[aria-label="World coordinate axis ${axis}"]`)?.value,
              keyboard:row.dataset.keyboardAxis==='true'})),
            coordinates:[...root.querySelectorAll('details > div:last-child > p')].map(p=>p.textContent)};})()""")

    # --- operating ---------------------------------------------------------------------------
    def apply(self):
        self.page.click(button('Apply view changes'))
        self.page.wait("(()=>{const s=document.querySelector('.scientific-navigation-actions [role=status]')?.textContent||'';"
                       "return s.startsWith('View changes applied')||/refused|not|Choose|Enter/.test(s)})()", 60, 'the applied view')
        self.settle()
        return self.status()

    REFUSED = 'Wait for the current view before using a dimension action.'

    def press(self, label=None, *, key=None, modifiers=(), focus=None):
        """An immediate View action (a button, or a key on the focused preview), as a person repeats it when the
        Studio says the preview was still drawing. Returns the status after it settled."""
        for _ in range(4):
            self.settle()
            if label is not None:
                self.page.click(button(label))
            else:
                self.page.click(focus)
                self.page.key(key, modifiers)
            # Wait until the action has resolved (Check current view is disabled while a change is in flight).
            self.page.wait(f"!({button('Check current view')}).disabled", 60, 'the action to resolve')
            status = self.status()
            if status.startswith('Wait for the current scientific operation to finish'):
                # The document was busy (a preview still drawing), so nothing was sent; the panel asks for a check.
                self.page.click(button('Check current view'))
            elif status != self.REFUSED:
                self.settle()
                return self.status()
            self.settle()
            time.sleep(.5)
        raise BrowserError(f'{label or key} was refused four times: {status}')

    def set_number(self, label, value):
        """Click a number field, replace its text by typing, as a keyboard user does."""
        page = self.page
        for _ in range(3):  # retype, as a person would, if the field did not take the text
            page.wait(f"!({labelled(label)}).disabled", 60, f'{label} to accept input')
            page.click(labelled(label))
            page.select_all()
            page.key('Backspace')
            page.insert(str(value))
            if page.eval(f"({labelled(label)}).value") == str(value):
                return
        raise BrowserError(f'{label} did not take {value!r}')

    def choose(self, label, value):
        """Choose an option of a select with the keyboard: focus it, then type the option's text."""
        page = self.page
        options = page.eval(f"[...({labelled(label, 'select')}).options].map(o=>[o.value,o.textContent])")
        texts = [text for v, text in options if v == str(value)]
        if not texts:
            raise BrowserError(f'{label} has no option {value}: {options}')
        # Keyboard focus moves to the select as Tab would (a mouse click would open the platform's option menu,
        # which headless Chromium cannot show); typing the option's text then selects it (type-to-find), firing
        # the select's own input and change events. The shortest unique prefix without a space is typed: a space
        # in a focused select opens its list instead.
        word = texts[0].split(' ')[0]
        prefix = next((word[:n] for n in range(1, len(word) + 1)
                       if sum(t.lower().startswith(word[:n].lower()) for _, t in options) == 1), word)
        page.focus(labelled(label, 'select'))
        page.type(prefix)
        try:
            page.wait(f"({labelled(label, 'select')}).value === {json.dumps(str(value))}", 10, f'{label} = {value}')
        except BrowserError as error:
            state = page.eval("[...document.querySelectorAll('.scientific-navigation select')].map(s=>[s.getAttribute('aria-label'),"
                              "s.value,s.disabled,!!s.offsetParent])") + [page.eval(
                              "document.activeElement?.getAttribute('aria-label')"), prefix]
            import os
            if os.environ.get('HOOKE_DEBUG_SHOTS'):
                page.screenshot(os.path.join(os.environ['HOOKE_DEBUG_SHOTS'], f'choose-{int(time.time())}.png'))
            raise BrowserError(f'{error}: {state}') from error
        time.sleep(1.1)  # let type-to-find forget this text before another select is typed into

    def drag_slider(self, axis, value):
        """Drag a range slider's thumb to a value with the mouse and release."""
        page = self.page
        rect = page.eval(f"""(()=>{{const e={labelled(f'Slice slider axis {axis}', 'input')};e.scrollIntoView({{block:'center'}});
            const r=e.getBoundingClientRect();return [r.left,r.top,r.width,r.height,Number(e.min||0),Number(e.max),Number(e.value)]}})()""")
        left, top, width, height, low, high, current = rect
        thumb = 16  # Chromium's default range thumb width in CSS pixels
        def x_of(v):
            return left + thumb / 2 + (width - thumb) * (v - low) / (high - low)
        y = top + height / 2
        landed = current
        for _ in range(3):  # like a person, drag again from where the thumb is until it shows the value
            page.drag((x_of(landed), y), (x_of(value), y))
            landed = page.eval(f"Number(({labelled(f'Slice slider axis {axis}', 'input')}).value)")
            if landed == value:
                break
        return landed

    def python(self, code, timeout=120):
        """Run code in the session's Python panel; returns (stdout, result, error) of the new cell."""
        page = self.page
        self.tab('Python')
        # A closed earlier revision remains in the DOM. Read only the console owning the visible input.
        console = f"({labelled('Python code', 'textarea')}).parentElement"
        cells = f"({console}).querySelectorAll('[aria-label=\"Python cell history\"] > *')"
        before = page.eval(f"{cells}.length")
        page.click(labelled('Python code', 'textarea'))
        page.select_all()
        page.key('Backspace')
        page.insert(code)
        page.wait(f"!({button('Run code')}).disabled", 60, 'Run code to be available')
        status = f"({console}).querySelector(':scope > p[role=status]')"
        started = (f"{cells}.length > {before} || "
                   f"({status})?.textContent === 'Running Python…'")
        for attempt in range(6):
            # The panel is briefly unavailable while a new preview draws: a click then starts nothing, or the Studio
            # says the code was not run. It is repeated only when no cell started (a started cell disables Run code).
            page.wait(f"!({button('Run code')}).disabled && !({button('Refresh preview')}).disabled", 60,
                      'Run code to be available and no scientific operation in flight')
            page.click(button('Run code'))
            try:
                page.wait(started, 3, 'the cell to start')
                break
            except BrowserError:
                if attempt == 5:
                    raise
                time.sleep(.5)
        try:
            page.wait(f"{cells}.length > {before} && "
                      f"!({button('Run code')}).disabled", timeout, 'the Python cell')
        except BrowserError as error:
            import os
            if os.environ.get('HOOKE_DEBUG_SHOTS'):
                page.screenshot(os.path.join(os.environ['HOOKE_DEBUG_SHOTS'], f'python-{int(time.time())}.png'))
            replies = [(e['params'].get('response', {}).get('status', e['params'].get('errorText')), e['params'].get('response', {}).get('url', '')[-60:])
                       for e in page.browser.events if e.get('method') in ('Network.responseReceived', 'Network.loadingFailed')
                       and ('/api/scientific-reviews/' in e['params'].get('response', {}).get('url', '') or 'errorText' in e['params'])][-6:]
            raise BrowserError(f"{error}; last replies {replies}: {page.eval('document.querySelector(\".scientific-console [role=status], [aria-label=\\\"Python code\\\"]\")?.closest(\"section,div\")?.innerText?.slice(-600)')}") from error
        cell = page.eval(f"""(()=>{{const cells={cells};
            const c=cells[cells.length-1];return Object.fromEntries([...c.querySelectorAll('pre[aria-label]')].map(e=>[e.getAttribute('aria-label'),e.textContent]))}})()""")
        self.tab('View')
        self.settle()  # a cell redraws the preview; the View controls wait for it
        return cell

    def native_state(self, probes=()):
        """The actual worker's native state through the shared reference snapshot (via the Python panel)."""
        from tests.scientific_napari_reference import SNAPSHOT
        call = f"import json as _json\nprint('HOOKE-STATE' + _json.dumps(_hooke_snapshot(viewer, {list(map(list, probes))!r})))"
        # The snapshot source is defined once per Python session (it keeps the console's history small); a new
        # session that lacks it gets it again.
        cell = self.python(call) if getattr(self, '_snapshot_defined', False) else {'error': 'NameError'}
        if 'NameError' in cell.get('error', ''):
            cell = self.python(SNAPSHOT + '\n' + call)
            self._snapshot_defined = True
        text = cell.get('stdout', '')
        if 'HOOKE-STATE' in text:
            return json.loads(text.split('HOOKE-STATE', 1)[1].strip().splitlines()[0])
        raise BrowserError(f'The Python panel did not return the native state: {cell}')
