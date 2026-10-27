"""Match scientific interactions to canonical workspace evidence.

The application supplies the accepted Screen connection, saved document and server-read
layout. This matcher neither starts a worker nor authorizes saved edits. The worker owner
must additionally hold its runtime/inputs, serialize calls and consume issued frames.
"""
from dataclasses import dataclass
from copy import deepcopy

from .scientific_projects import ProjectRevision
from .workspace_observation import ObservationError, RequestSnapshot
from .workspace_views import SCHEMAS


def _refuse(message):
    raise ObservationError('SCIENTIFIC_VIEW_CHANGED', message)


def _scientific_observation(item):
    value = deepcopy(item)
    value.pop('revision', None)
    value.get('view', {}).get('legacy', {}).get('document', {}).pop('focused', None)
    return value


def _visible(snapshot, item_id):
    if not isinstance(snapshot, RequestSnapshot):
        _refuse('Use the accepted workspace observation for this scientific view')
    wire = snapshot.observation.wire()
    if snapshot.report_age_ms > 1000 or not wire['viewport']['visible']:
        _refuse('Return to the scientific view and wait for a current preview')
    item = next((row for row in wire['items'] if row['id'] == item_id), None)
    if item is None or not all(item[key] for key in ('open', 'visible', 'connected')):
        _refuse('Reopen the original scientific window before interacting')
    document = item.get('view', {}).get('legacy', {}).get('document')
    if item['kind'] != 'document' or not document or document['representation'] != 'scientific-review':
        _refuse('Choose the scientific document window')
    return item, document


def _placement(layout, tab, placement, reference):
    if not isinstance(layout, dict) or layout.get('schema') not in SCHEMAS:
        _refuse('Save this window in the current workspace arrangement before opening its tools')
    matches = [row for view in layout['views'] if view['id'] == tab
               for row in view['items'] if row.get('placementId') == placement]  # a reader Tab's item may have none
    if len(matches) != 1 or matches[0]['resource'] != reference:
        _refuse('The saved window arrangement changed; reopen its exact scientific document')
    return matches[0]


@dataclass(frozen=True)
class ReviewOrigin:
    client: object
    profile: object
    project: str
    document: str
    revision: int
    digest: str
    tab: str
    placement: str
    item_id: str
    root: str
    lifetime: str | int
    source_generation: int

    @property
    def reference(self):
        return {'day': self.project, 'id': self.document, 'version': self.revision, 'picture': None}

    @classmethod
    def admit(cls, screen, client, snapshot, item_id, saved, layout):
        if not isinstance(saved, ProjectRevision):
            _refuse('Load the exact saved scientific document before opening its tools')
        owner, profile = screen._origin(snapshot)
        if owner is not client:
            _refuse('This scientific window belongs to another workspace connection')
        item, document = _visible(snapshot, item_id)
        reference = {'day': saved.session, 'id': saved.id, 'version': saved.revision, 'picture': None}
        if document['project'] != saved.session or document['resource'] != reference:
            _refuse('The scientific window no longer shows the requested saved revision')
        _placement(layout, document['tab'], document['placement'], reference)
        origin = cls(client, profile, saved.session, saved.id, saved.revision, saved.digest,
                     document['tab'], document['placement'], item_id, item['rootId'],
                     item['lifetimeGeneration'], item['sourceGeneration'])
        origin.check(screen, snapshot, layout)
        return origin

    def check(self, screen, snapshot, layout, *, review=None, frame=None, draw=None, presentation='image'):
        if presentation not in ('image', 'native-panel'):
            _refuse('Choose a declared scientific presentation')
        owner, profile = screen._origin(snapshot)
        if owner is not self.client or profile is not self.profile:
            _refuse('The original workspace connection ended; reopen this scientific view')
        item, document = _visible(snapshot, self.item_id)
        if (item['rootId'] != self.root or item['lifetimeGeneration'] != self.lifetime
                or item['sourceGeneration'] != self.source_generation
                or document['project'] != self.project or document['tab'] != self.tab
                or document['placement'] != self.placement or document['resource'] != self.reference):
            _refuse('The original scientific window or saved input changed; reopen it')
        _placement(layout, self.tab, self.placement, self.reference)
        current = screen.observation(self.client)
        fresh = screen.pin(self.client, request_id='scientific-current')
        current_item, _ = _visible(fresh, self.item_id)
        if (current is None or current.client != snapshot.observation.client
                or current.app_run_id != snapshot.observation.app_run_id
                or _scientific_observation(current_item) != _scientific_observation(item)):
            _refuse('The scientific preview changed after this action; inspect it before trying again')
        if review is None and frame is None and draw is None:
            return
        paint = item.get('scientificPanelPaint' if presentation == 'native-panel' else 'scientificPaint')
        if (not review or not isinstance(frame, dict) or not draw or paint is None
                or paint['review'] != review or paint['frame'] != frame.get('frame')
                or paint['width'] != frame.get('width') or paint['height'] != frame.get('height')
                or paint['draw'] != draw):
            _refuse('Wait for the current worker frame to appear before interacting')
        return tuple(paint['contentRect'])
