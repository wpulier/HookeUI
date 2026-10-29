"""Named Tab actions dispatched only to the original Studio, acknowledged after its save.

The client uses the same arrangement controller as manual gestures. This broker neither
writes layouts nor redirects an absent requester to another open Studio.
"""
import asyncio
import json
import uuid

from .brain.registry import ToolFailed
from .brain.toolbox.resources import require_current
from .workspace_observation import front


_PIN_FIELDS = ("id", "rootId", "kind", "lifetimeGeneration", "sourceGeneration")


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
        if not isinstance(args, dict) or args.get('action') not in ('list', 'create', 'rename', 'switch', 'add', 'move', 'close'):
            raise ToolFailed('choose one named Tab action', next='list the Tabs first')
        if set(args) - {'action', 'tab', 'title', 'from', 'placement', 'resource'}:
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
            return reply
        except TimeoutError:
            raise ToolFailed('the original Studio did not confirm the Tab action', next='inspect its saved Tabs before retrying') from None
        finally:
            self.pending.pop(request_id, None)

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
