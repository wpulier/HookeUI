"""Named Tab actions dispatched only to the original Studio, acknowledged after its save.

The client uses the same arrangement controller as manual gestures. This broker neither
writes layouts nor redirects an absent requester to another open Studio.
"""
import asyncio
import json
import uuid

from .brain.registry import ToolFailed
from .brain.toolbox.resources import require_current
from .workspace_observation import SPECIMEN_ASKED as SPECIMEN, front


_PIN_FIELDS = ("id", "rootId", "kind", "lifetimeGeneration", "sourceGeneration")
ACTIONS = ('list', 'create', 'rename', 'switch', 'add', 'move', 'close', 'specimen', 'arrange', 'only', 'reader')
SPECIMEN_WAIT_S = 4.0  # the studio's report showing the specimen as asked, after its save was acknowledged
PLACED_WAIT_S = 4.0  # the studio's report showing a Tab arranged (beside) or holding only what was kept (only)
ADD_PRESENTATIONS = {'population': 'masks', 'dataset-table': 'datatable', 'analysis': 'analysis', 'graph': 'graph',
                     'result-table': 'table'}
ASK_EVERY_S = 0.5  # a studio in the background reports only when asked (main.ts screen-ask)


def specimen_of(wire, tab: str) -> dict | None:
    """The specimen of the open Tab `tab` (its id or exact title) as an observation's Tab strip reports it, or None."""
    item = next((row for row in (wire or {}).get("items") or () if row.get("id") == "workspace-tabs"), None)
    workspace = ((item or {}).get("view") or {}).get("legacy", {}).get("workspace") or {}
    return next((t.get("specimen") for t in workspace.get("tabs") or () if tab in (t.get("id"), t.get("title"))), None)


def _workspace_pin(observation, session):
    """Pin the original observed Tab strip, not a guessed server/UI generation."""
    item = next((row for row in observation.wire()["items"] if row["id"] == "workspace-tabs"), None)
    if (item is None or not item["open"] or not item["connected"]
            or item.get("view", {}).get("legacy", {}).get("workspace", {}).get("project") != session):
        raise ToolFailed("the original Project Tab strip is unavailable", next="open the workspace and ask again")
    return {"client": observation.client.wire(), **{key: item[key] for key in _PIN_FIELDS}}


class WorkspaceTabs:
    def __init__(self, app):
        self.app = app
        self.pending = {}

    async def run(self, ctx, args, *, verify=True):
        from .brain.toolbox.present import _original_screen
        from .screen import screen_of
        app = self.app
        require_current(ctx, app.day)
        if ctx.client is None or ctx.client not in app.hub.clients or ctx.asked is None or ctx.asked.session != app.day.sid:
            raise ToolFailed('the originating Studio is not open for this Project', next='open the Project and ask again')
        screen = screen_of(app.hub)
        if not _original_screen(screen, ctx, target="workspace-tabs"):
            raise ToolFailed('the request has no original workspace view', next='ask again from the Studio')
        owner = app.hub.datatables.capture(ctx.asked.session)
        pin = _workspace_pin(ctx.request_snapshot.observation, owner.session)

        def current():
            owner.check(app)
            require_current(ctx, app.day)
            _original_screen(screen, ctx, target="workspace-tabs")
            if _workspace_pin(screen.observation(ctx.client), owner.session) != pin:
                raise ToolFailed("the original workspace opening changed", next="inspect the current Tabs and ask again")

        current()
        if not isinstance(args, dict) or args.get('action') not in ACTIONS:
            raise ToolFailed('choose one named Tab action', next='list the Tabs first')
        if set(args) - {'action', 'tab', 'title', 'from', 'placement', 'resource', 'specimen', 'kind', 'keep',
                        'presentation'}:
            raise ToolFailed('the Tab action has unsupported arguments', next='use its declared fields')
        for key in ('tab', 'title', 'from', 'placement', 'kind'):
            if key in args and (not isinstance(args[key], str) or not 1 <= len(args[key]) <= 256):
                raise ToolFailed('Tab names and identities must be nonempty bounded text', next='list the Tabs first')
        action = args['action']
        if action == 'only' and isinstance(args.get('keep'), str):
            args = {**args, 'keep': [args['keep']]}  # one id as a string (the brain sent it so, 29 Oct): that one stays
        if action == 'only' and 'keep' not in args:
            # Nothing named to keep: "close everything on this Tab" (will-workspace, 29 Oct: refused, so the brain closed
            # each window by hand). Every window there closes; Main's specimen floats small, as it always stays.
            args = {**args, 'keep': []}
        if action == 'only' and (not isinstance(args.get('keep'), list) or not 0 <= len(args['keep']) <= 8
                                 or not all(isinstance(k, str) and 1 <= len(k) <= 256 for k in args['keep'])):
            raise ToolFailed('keep is up to 8 window or placement ids from the screen or tabs list, or none to close '
                             'everything on the Tab', next='list the Tabs to see what each holds')
        if action == 'only':
            # A window id as the screen shows it (document:<placement>) is its placement on the Tab.
            args = {**args, 'keep': [k.removeprefix('document:') for k in args['keep']]}
        if action in ('arrange', 'only') and 'tab' in args:
            # "This Tab", or a Tab by its title as the screen shows it: its id from the latest report's Tab strip.
            from .placement import strip as tab_strip

            observed = screen_of(app.hub).observation(ctx.client)
            tabs = tab_strip(observed.wire() if observed is not None else None).get('tabs') or ()
            if not any(t.get('id') == args['tab'] for t in tabs):
                named = [t for t in tabs if " ".join(str(t.get('title') or '').split()).casefold()
                         == " ".join(args['tab'].split()).casefold()]
                if len(named) == 1:
                    args = {**args, 'tab': named[0]['id']}
        if action not in ('list', 'create', 'reader') and 'tab' not in args:
            raise ToolFailed('name the exact target Tab', next='list the Tabs first')
        if action in ('create', 'rename') and not str(args.get('title') or '').strip():
            raise ToolFailed('give the Tab a title', next='choose a short descriptive title')
        if action == 'move' and not {'from', 'placement'} <= set(args):
            raise ToolFailed('name the source Tab and exact placement to move', next='list its retained items first')
        if action == 'only' and 'specimen' in args:
            # only puts the specimen away itself (hidden; Main's floats small): the hint is what it does already
            # (will-workspace, 29 Oct: only with specimen hide was refused, and nothing was closed).
            args = {k: v for k, v in args.items() if k != 'specimen'}
        if (action == 'specimen') != ('specimen' in args) or (action == 'specimen'
                                                              and args['specimen'] not in tuple(SPECIMEN)):
            raise ToolFailed('say fill, float or hide for the specimen, with the specimen action',
                             next='call tabs with action specimen, the Tab and specimen fill, float or hide')
        payload = dict(args)
        if action == 'reader':
            # Open in new Tab, as a person's (WorkspaceViews.openReaderTab): one saved item in a Tab of its own, the
            # Data reader full width; an item that has one already goes to it (documents.py opens a document so).
            ref = args.get('resource')
            if (not isinstance(ref, dict) or set(ref) - {'id', 'version'} or not isinstance(ref.get('id'), str)
                    or not (ref.get('version') is None or type(ref['version']) is int)):
                raise ToolFailed('choose an exact saved item', next='read the saved resource first')
            entry = await asyncio.to_thread(app.hub.resources.get, ref['id'], owner.session)
            current()
            if entry is None or entry.version != ref.get('version'):
                raise ToolFailed('the selected item is unavailable or has changed', next='read its current saved revision')
            title = " ".join(str(args.get('title') or getattr(entry, 'shown', '') or entry.title).split())[:80] or entry.id
            payload = {'action': 'reader', 'item': {'resource': {'day': owner.session, 'id': entry.id,
                                                                 'version': entry.version}, 'title': title}}
        if action == 'add':
            ref = args.get('resource')
            # Named by its id alone ("d1", or {id} with no version): the version saved now, read here. Hooke resolves
            # that itself (will-workspace, 29 Oct: "put that photo in it" was refused for want of a version, and the
            # voice said "Hooke needs an exact saved version chosen"); an explicit version that is not the saved one
            # is still refused.
            if isinstance(ref, str):
                ref = {'id': ref}
            named = isinstance(ref, dict) and set(ref) <= {'id', 'version'} and isinstance(ref.get('id'), str)
            entry = await asyncio.to_thread(app.hub.resources.get, ref['id'], owner.session) if named else None
            current()
            if entry is None:
                said = str(ref.get('id'))[:60] if named else 'that item'
                raise ToolFailed(f"{said} is not in this Project's Data", next='name it by its id from the screen, the Tabs or Data',
                                 argument=True)
            # A photo, a video, a document or a researcher's work has no version to name (will-workspace and
            # will-research, 29 Oct: refused "not saved in this Project", and the voice told the user to save it first).
            if ref.get('version') is not None and ref['version'] != entry.version:
                raise ToolFailed('the selected resource is unavailable or has changed', next='read its current saved revision')
            presentation = ADD_PRESENTATIONS.get(entry.kind)
            if args.get('presentation') == 'table' and entry.kind == 'population':
                presentation = 'table'  # a population's measurements table, the window its masks' Measurements opens
            if presentation is None:
                # A photo, a video or a document is placed, not added: present do=place puts it where it is asked, on
                # the Tab in front (will-workspace, 29 Oct: the brain tried tabs add for a reference photo, was refused
                # without where to go, and the user was told about saved versions).
                raise ToolFailed(f'a {entry.kind} is not added to a Tab this way',
                                 next=f'present with do place, what {entry.id}, tab and placement only, beside or window '
                                      'puts it in that Tab')
            payload['item'] = {'resource': {'day': owner.session, 'id': entry.id, 'version': entry.version, 'picture': None},
                               'title': entry.title, 'presentation': presentation, 'zoom': 1, 'pan': [.5, .5]}
            payload.pop('resource', None)
            payload.pop('presentation', None)
        current()
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = (ctx.client, owner, future, pin, current)
        try:
            ctx.client.offer(json.dumps({'type': 'workspace-tabs', 'id': request_id, 'session': owner.session, 'workspacePin': pin, 'action': payload}))
            reply = await asyncio.wait_for(future, 10)
            current()
            if not reply.get('ok'):
                raise ToolFailed(str(reply.get('error') or 'the Tab action was not confirmed'), next='check the workspace before retrying')
            if action == 'list':
                # What is in front, as the studio's latest report shows it (workspace_observation.front): the Tab and its
                # title, the window highest on it, and whether the studio itself is in view. Observed, never inferred.
                observed = screen.observation(ctx.client)
                if observed is not None:
                    reply = {**reply, 'front': front(observed.wire())}
                reply = await self._kinds(reply, owner.session, args.get('kind'))
            if action == 'only':
                await self._close_panels(screen, ctx, args)
            if action in ('arrange', 'only') and verify:
                placed = await self._placed(screen, ctx, args, current)
                reply = {**reply, 'shown': placed}
            if action == 'specimen':
                shown = await self._specimen_shown(screen, ctx, args['tab'], args['specimen'], current)
                reply = {**reply, 'shown': shown}
            return reply
        except TimeoutError:
            raise ToolFailed('the original Studio did not confirm the Tab action', next='inspect its saved Tabs before retrying') from None
        finally:
            self.pending.pop(request_id, None)

    async def _kinds(self, reply: dict, session: str, kind: str | None) -> dict:
        """Each listed item's resource kind as the index keeps it (resources.py), and with `kind` only the Tabs holding
        that kind (a table, outlines, a video...): "which Tab has the measurements?" answered from what each Tab holds,
        never from a title."""
        resources = getattr(self.app.hub, 'resources', None)
        ids = {i['resource']['id'] for t in reply.get('tabs') or () for i in t.get('items') or ()
               if isinstance(i.get('resource'), dict) and isinstance(i['resource'].get('id'), str)}

        def look():
            found = {}
            for rid in list(ids)[:96]:
                try:
                    entry = resources.get(rid, session) if resources is not None else None
                except Exception:  # one unreadable entry leaves only its own kind unsaid (rule 4)
                    entry = None
                if entry is not None:
                    found[rid] = entry.kind
            return found

        kinds = await asyncio.to_thread(look) if ids else {}
        tabs = []
        for tab in reply.get('tabs') or ():
            items = [{**i, **({'resourceKind': kinds[i['resource']['id']]}
                              if isinstance(i.get('resource'), dict) and i['resource'].get('id') in kinds else {})}
                     for i in tab.get('items') or ()]
            tabs.append({**tab, 'items': items})
        reply = {**reply, 'tabs': tabs}
        if kind:
            def holds(tab):
                return ((tab.get('holds') or {}).get(kind) or any(i.get('kind') == kind for i in tab.get('items') or ())
                        or any(i.get('kind') == kind for i in tab.get('shows') or ()))
            matches = [t for t in tabs if holds(t)]
            reply = {**reply, 'tabs': matches, 'kind': kind,
                     'holding': [t.get('title') for t in matches] if matches else f"no Tab holds a {kind}"}
        return reply

    async def _close_panels(self, screen, ctx, args: dict) -> None:
        """Only, for what is not one of the Tab's own windows: the magnifier and the Data reader open on the Tab in
        front are closed too (each its own close, as `present` closes it), so "close everything else" leaves nothing
        else. The Tab's windows were closed by the studio through their own ×."""
        from .brain.toolbox.present import act
        from .placement import identities, strip, windows_on

        observed = screen.observation(ctx.client)
        wire = observed.wire() if observed is not None else None
        tab = strip(wire).get('tab')
        named = next((t for t in strip(wire).get('tabs') or () if args['tab'] in (t.get('id'), t.get('title'))), None)
        if not tab or (named is not None and named.get('id') != tab):
            return
        keep = set(args.get('keep') or ())
        services = getattr(getattr(self.app, 'brain', None), 'services', None)
        for item in windows_on(wire, tab):
            iid = str(item.get('id') or '')
            if (iid == 'magnifier' or iid.startswith('document:reader-')) and not identities(item) & keep and services:
                try:
                    await act(services, {'do': 'close', 'what': iid}, ctx=ctx)
                except ToolFailed:
                    pass  # said by the report check that follows: what is still open there

    async def _placed(self, screen, ctx, args: dict, current) -> dict:
        """Done only once the studio's report shows the Tab as asked: arranged with its windows side by side (beside)
        or holding only what was kept, the specimen hidden or, on Main, floating (only). Otherwise said with what the
        report shows."""
        from .placement import beside_shown, only_shown, strip, windows_on

        loop = asyncio.get_running_loop()
        deadline, ask, why = loop.time() + PLACED_WAIT_S, loop.time(), ''
        while True:
            current()
            observed = screen.observation(ctx.client)
            wire = observed.wire() if observed is not None else None
            tab = strip(wire).get('tab')
            named = next((t for t in strip(wire).get('tabs') or () if args['tab'] in (t.get('id'), t.get('title'))),
                         None) or ({'id': args['tab']} if not strip(wire).get('tabs') else None)  # an older Tab strip
            home = (named or {}).get('id') == 'main' or (named or {}).get('title') == 'Main'
            if tab and named is not None and named.get('id') == tab:
                if args['action'] == 'only':
                    ok, why = only_shown(wire, tab, args['keep'], home=home)
                else:
                    shown = windows_on(wire, tab)
                    ok, why = beside_shown(wire, tab, [n for w in shown for n in (w.get('id'),)]) if shown else (True, '')
                if ok:
                    return {'tab': tab, 'windows': [w.get('id') for w in windows_on(wire, tab)][:12]}
            else:
                why = 'another Tab is in front'
            if loop.time() >= deadline:
                break
            if loop.time() - ask >= ASK_EVERY_S:
                ctx.client.offer(json.dumps({'type': 'screen-ask'}))
                ask = loop.time()
            await asyncio.sleep(0.05)
        raise ToolFailed(f"the change was saved, but the studio does not show it on that Tab yet: {why}",
                         next='list the Tabs to see what each holds now')

    async def _specimen_shown(self, screen, ctx, tab: str, asked: str, current) -> dict:
        """Done only once the studio's report shows the specimen as asked on that Tab (ADR 0006: done means the screen
        shows it), its save already acknowledged; otherwise said with what the report shows."""
        loop = asyncio.get_running_loop()
        deadline, ask, seen = loop.time() + SPECIMEN_WAIT_S, loop.time(), None
        while True:
            current()
            observed = screen.observation(ctx.client)
            seen = specimen_of(observed.wire() if observed is not None else None, tab)
            if seen is not None and seen.get('state') == SPECIMEN[asked]:
                return {'tab': tab, 'specimen': seen}
            if loop.time() >= deadline:
                break
            if loop.time() - ask >= ASK_EVERY_S:
                ctx.client.offer(json.dumps({'type': 'screen-ask'}))
                ask = loop.time()
            await asyncio.sleep(0.05)
        said = f"its report shows it {seen['state']}" if seen else "its report does not say the specimen of that Tab"
        raise ToolFailed(f"the specimen change was saved, but the studio does not show it on that Tab yet: {said}",
                         next='list the Tabs to see the specimen as it is now')

    def reply(self, client, message):
        found = self.pending.get(message.get('id'))
        if found is None or found[0] is not client or found[2].done():
            return False
        _client, owner, future, pin, current = found
        if message.get('workspacePin') != pin:
            return False
        try:
            current()
        except (ToolFailed, ValueError) as exc:
            future.set_exception(exc)
            return False
        result = message.get('result')
        if message.get('session') != owner.session or not isinstance(result, dict):
            return False
        if len(json.dumps(result, allow_nan=False).encode()) > 64 * 1024:
            return False
        future.set_result(result)
        return True
