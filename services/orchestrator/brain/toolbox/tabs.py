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
        'new_project), what each holds and what is in front, or explicitly create, rename, switch, add a retained '
        'resource, move a placement, arrange, keep only one thing, or close a named Tab. Each item has a kind (table, '
        'outlines, tracks, image, reference, document, graph, video, web-page, magnifier, note); list with kind=table '
        'gives only the Tabs holding a table: "which Tab has the measurements?" is answered from kinds, never titles. '
        'Researcher Tabs list what they show. Placing on a Tab: '
        'arrange puts its windows and the live view side by side (beside); only keeps just `keep` (window or placement '
        'ids) open there, closing the rest and hiding the specimen (Main’s floats small), and with no keep closes '
        'everything on the Tab ("close everything on this Tab"); both done once the screen shows it. Closing hides an '
        'arrangement and never deletes Data. '
        'Each Tab also holds the specimen, the live view: it fills the Tab behind its windows, floats as a small '
        'window (a new Tab opens with it so), or is hidden there; list and the screen say which. It counts in what a '
        'Tab shows: name it when it is there, and float or hide it too for only one thing open in a Tab or everything '
        'else closed (action specimen with specimen fill, float or hide; Main’s floats, never hides; done once the '
        'screen shows it). '
        'Mutations require the user’s request. Always name tab by its exact ID from list; move also needs from and '
        'placement. No implicit active Tab or other Studio fallback. Completion is acknowledged by the originating Studio '
        'after its arrangement save; visibility is reported separately. A measurements table in a Tab (new or not) is '
        'show_measurements with tab: one action, confirmed on screen. Anything else put in a Tab (a photo, a '
        'researcher\'s picture, a window open in another Tab), as its main item or beside, is present do=place with '
        'tab: one action, wherever it is now. '
        'reopen brings back a closed Tab (list shows it with open false) as it was kept, its arrangement and items '
        'with it: how a close is undone. '
        'compose makes one new Tab holding saved items (items: up to 8, by id; a population with presentation table '
        'shows its measurements), its windows side by side: one action instead of create, add and arrange. '
        'show false prepares quietly, for looking or getting ready while the user keeps their own view: the Tab is '
        'made, filled, arranged or its specimen set without coming to the front, and the reply says prepared, never '
        'shown; switch to it when the user should see it, and it opens already arranged. Leave show out when the user '
        'asked to see it.',
        schema({'action': {'type': 'string', 'enum': ['list', 'create', 'rename', 'switch', 'add', 'move', 'close',
                                                      'reopen', 'specimen', 'arrange', 'only', 'compose']},
                'show': {'type': 'boolean',
                         'description': 'false: prepare it quietly, not brought to the front (the user sees it when it '
                                        'is switched to); leave out to show it'},
                'items': {'type': 'array', 'maxItems': 8,
                          'items': {'type': 'object', 'properties': {
                              'id': {'type': 'string'}, 'version': {'type': 'integer'},
                              'presentation': {'type': 'string', 'enum': ['table']}},
                              'required': ['id'], 'additionalProperties': False},
                          'description': 'compose: the saved items the new Tab holds'},
                'arrange': {'type': 'boolean', 'description': 'compose: false leaves its windows where they open'},
                'kind': {'type': 'string', 'enum': ['table', 'outlines', 'tracks', 'image', 'reference', 'document',
                                                    'graph', 'video', 'web-page', 'magnifier', 'note'],
                         'description': 'list: only the Tabs holding this kind'},
                'keep': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 8,
                         'description': 'only: the window or placement ids that stay open; none to close everything'},
                'presentation': {'type': 'string', 'enum': ['table'],
                                 'description': 'add a population as its measurements table instead of its outlines'},
                'specimen': {'type': 'string', 'enum': ['fill', 'float', 'hide']},
                'tab': {'type': 'string'}, 'from': {'type': 'string'}, 'placement': {'type': 'string'},
                'title': {'type': 'string', 'maxLength': 80},
                'resource': {'type': 'object', 'properties': {'id': {'type': 'string'}, 'version': {'type': 'integer'}},
                             'required': ['id'], 'additionalProperties': False,
                             'description': 'add: the saved item by its id (its saved version unless one is named)'}},
               required=['action']),
        tabs, kind='query', inline_ms=1500, family='display', retained_input=True))
