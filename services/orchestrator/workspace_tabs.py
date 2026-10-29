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
ACTIONS = ('list', 'create', 'rename', 'switch', 'add', 'move', 'close', 'specimen')
SPECIMEN_WAIT_S = 4.0  # the studio's report showing the specimen as asked, after its save was acknowledged
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

    async def run(self, ctx, args):
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
        if set(args) - {'action', 'tab', 'title', 'from', 'placement', 'resource', 'specimen'}:
            raise ToolFailed('the Tab action has unsupported arguments', next='use its declared fields')
        for key in ('tab', 'title', 'from', 'placement'):
            if key in args and (not isinstance(args[key], str) or not 1 <= len(args[key]) <= 256):
                raise ToolFailed('Tab names and identities must be nonempty bounded text', next='list the Tabs first')
        action = args['action']
        if action not in ('list', 'create') and 'tab' not in args:
            raise ToolFailed('name the exact target Tab', next='list the Tabs first')
        if action in ('create', 'rename') and not str(args.get('title') or '').strip():
            raise ToolFailed('give the Tab a title', next='choose a short descriptive title')
        if action == 'move' and not {'from', 'placement'} <= set(args):
            raise ToolFailed('name the source Tab and exact placement to move', next='list its retained items first')
        if (action == 'specimen') != ('specimen' in args) or (action == 'specimen'
                                                              and args['specimen'] not in tuple(SPECIMEN)):
            raise ToolFailed('say fill, float or hide for the specimen, with the specimen action',
                             next='call tabs with action specimen, the Tab and specimen fill, float or hide')
        payload = dict(args)
        if action == 'add':
            ref = args.get('resource')
            if not isinstance(ref, dict) or set(ref) != {'id', 'version'} or not isinstance(ref['id'], str) or type(ref['version']) is not int or ref['version'] < 0:
                raise ToolFailed('choose an exact retained resource version', next='read the saved resource first')
            entry = await asyncio.to_thread(app.hub.resources.get, ref['id'], owner.session)
            current()
            if entry is None or entry.version != ref['version']:
                raise ToolFailed('the selected resource is unavailable or has changed', next='read its current saved revision')
            presentations = {'population': 'masks', 'dataset-table': 'datatable', 'analysis': 'analysis', 'graph': 'graph',
                             'result-table': 'table'}
            if entry.kind not in presentations:
                raise ToolFailed('this resource has no supported Tab insertion through this action',
                                 next='open it through its existing result control')
            payload['item'] = {'resource': {'day': owner.session, 'id': entry.id, 'version': entry.version, 'picture': None},
                               'title': entry.title, 'presentation': presentations[entry.kind], 'zoom': 1, 'pan': [.5, .5]}
            payload.pop('resource', None)
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
            if action == 'specimen':
                shown = await self._specimen_shown(screen, ctx, args['tab'], args['specimen'], current)
                reply = {**reply, 'shown': shown}
            return reply
        except TimeoutError:
            raise ToolFailed('the original Studio did not confirm the Tab action', next='inspect its saved Tabs before retrying') from None
        finally:
            self.pending.pop(request_id, None)

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
