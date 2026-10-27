"""Which selected layers a newer revision left untouched, for one scientific session's saves.

A session's layer values come from exact accepted inputs: those of the revision it opened, then the
ones its own saves accepted. A save is placed on the newest revision only when, for every selected
layer, that revision still binds the very input the session's values came from. A worker always
stages every chunk of a layer, so the saved values never depend on any other revision's bytes, and
the producing record names the newest revision as its exact base because the input is identical
there. If any selected layer was changed or removed by another save, nothing is placed on the
newest revision: the session reports, layer by layer, what changed, and the existing conflict path
(save as a separate document) stays available. Saves of other layers stay possible: one saved layer
never strands another layer's private work.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from .scientific_projects import InvalidProject

UNCHANGED, CHANGED, REMOVED = 'unchanged-in-latest', 'changed-in-latest', 'removed-in-latest'


def inputs_of(document):
    view = document['state'].get('scientificView')
    inputs = view.get('inputs') if type(view) is dict else None
    return dict(inputs) if type(inputs) is dict else {}


def selected(fields):
    """The (layer, input) pairs a save request names, before any worker export."""
    op = fields.get('op')
    if op == 'save-resources':
        rows = fields.get('layers')
        if type(rows) is not list or any(type(row) is not dict for row in rows):
            raise InvalidProject('Choose the editable layers to save together')
        return [(row.get('layer'), row.get('input')) for row in rows]
    if op == 'save-resource':
        return [(fields.get('layer'), fields.get('input'))]
    if op == 'save-layer':
        frame = fields.get('_frame')
        layer = next((row for row in (frame or {}).get('layers', []) if row.get('id') == fields.get('layer')), None)
        if layer is None or not layer.get('editableInput'):
            raise InvalidProject('This integration has not bound that layer to an editable scientific input')
        return [(fields['layer'], layer['editableInput'])]
    raise InvalidProject('Only array saves of existing layers are placed by their inputs')


@dataclass
class Plan:
    base: object            # the revision to save on: the newest one, or the session's own on conflict
    rows: list              # per selected layer: layer, input and whether the newest revision kept it
    conflict: bool
    onto: dict | None       # set when other saves were accepted since the session last knew the head
    inputs: list
    latest: int             # the newest revision when the plan was made

    def message(self):
        changed = [row for row in self.rows if row['state'] != UNCHANGED]
        kept = [row for row in self.rows if row['state'] == UNCHANGED]
        names = ', '.join(f'{row["input"]} ({"changed" if row["state"] == CHANGED else "removed"})' for row in changed)
        return (f'A newer revision ({self.latest}) was accepted from another save and changed {names}. '
                f'Nothing was saved. {len(kept)} of your selected layers are unchanged there and can be saved on their own; '
                'the changed ones can be saved as a separate document. Your private work is still here.')


class Track:
    """The accepted inputs this session's layer values come from, and the newest revision it knows."""

    def __init__(self, saved):
        self.saved = saved
        self.bases = inputs_of(saved.document)
        self.head = saved.revision

    def plan(self, store, fields):
        pairs = selected(fields)
        latest = store.read(self.saved.id, self.saved.session)
        current = inputs_of(latest.document)
        rows = []
        for layer, input in pairs:
            ref = current.get(input)
            if ref is not None and ref == self.bases.get(input) and ref in latest.document['resources']:
                state = UNCHANGED
            else:
                state = REMOVED if ref is None else CHANGED
            rows.append({'layer': layer, 'input': input, 'state': state})
        conflict = any(row['state'] != UNCHANGED for row in rows)
        onto = None if latest.revision == self.head else {'version': latest.revision, 'since': self.head}
        return Plan(self.saved if conflict else latest, rows, conflict, onto, [input for _, input in pairs],
                    latest.revision)

    def accepted(self, receipt, inputs):
        """After an accepted save: these inputs now come from the receipt, and it is the head this session knows."""
        current = inputs_of(receipt.document)
        for input in inputs:
            if input in current:
                self.bases[input] = copy.deepcopy(current[input])
        self.head = receipt.revision
