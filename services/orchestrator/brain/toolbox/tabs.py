"""Original-Studio Tab actions use the same named arrangement gestures as the person. A Tab is an arrangement inside
this Project (CONTEXT.md "Tab"), never a new Project (threads.py new_project)."""
import json

from ..registry import Tool, schema


def add_tab_tools(registry, services):
    if services.workspace_tabs is None:
        return

    async def tabs(ctx, args):
        return json.dumps(await services.workspace_tabs(ctx, args), ensure_ascii=False)

    registry.add(Tool('tabs', 'List the Tabs inside this Project (a Tab arranges this Project’s windows; a new Project is '
        'new_project), their exact retained placements and what is in front, or explicitly create, rename, switch, add a '
        'retained resource, move a placement, or close a named Tab. Closing hides an arrangement and never deletes Data. '
        'Each Tab also holds the specimen, the live view: it fills the Tab behind its windows, floats as a small '
        'window (a new Tab opens with it so), or is hidden there; list and the screen say which. It counts in what a '
        'Tab shows: name it when it is there, and float or hide it too for only one thing open in a Tab or everything '
        'else closed (action specimen with specimen fill, float or hide; Main’s floats, never hides; done once the '
        'screen shows it). '
        'Mutations require the user’s request. Always name tab by its exact ID from list; move also needs from and '
        'placement. No implicit active Tab or other Studio fallback. Completion is acknowledged by the originating Studio '
        'after its arrangement save; visibility is reported separately. A measurements table in a Tab (new or not) is '
        'show_measurements with tab: one action, confirmed on screen.',
        schema({'action': {'type': 'string', 'enum': ['list', 'create', 'rename', 'switch', 'add', 'move', 'close',
                                                      'specimen']},
                'specimen': {'type': 'string', 'enum': ['fill', 'float', 'hide']},
                'tab': {'type': 'string'}, 'from': {'type': 'string'}, 'placement': {'type': 'string'},
                'title': {'type': 'string', 'maxLength': 80},
                'resource': {'type': 'object', 'properties': {'id': {'type': 'string'}, 'version': {'type': 'integer'}},
                             'required': ['id', 'version'], 'additionalProperties': False}}, required=['action']),
        tabs, kind='query', inline_ms=1500, family='display', retained_input=True))
