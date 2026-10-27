import pytest

from orchestrator.screen import Screen
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_workspace import ReviewOrigin
from orchestrator.workspace_observation import ClientRef, ObservationError
from orchestrator.workspace_views import checked_layout
from tests.test_scientific_presentation import scientific_report, CLIENT


class Rig:
    def __init__(self, tmp_path, schema=2):
        self.now = 10.0
        self.screen = Screen(clock=lambda: self.now)
        self.client = object()
        self.store = ProjectStore(tmp_path)
        self.saved = self.store.create('2026-10-16', {'kind': 'scene', 'title': 'Retained scientific work',
            'state': {}, 'resources': [], 'runtimes': []})
        self.raw = scientific_report()
        self.item = self.raw['items'][0]
        self.ref = {'day': self.saved.session, 'id': self.saved.id, 'version': self.saved.revision, 'picture': None}
        self.item['view']['legacy']['document']['resource'] = self.ref
        self.layout = checked_layout({'schema': schema, 'coordinates': 'stage', 'views': [{
            'id': 'main', 'title': 'Main', 'specimen': {'visible': True, 'rect': None}, 'notes': [],
            'items': [{'placementId': 'placement-one', 'resource': self.ref, 'title': 'Scientific work',
                       'zoom': 1, 'pan': [.5, .5]}]}]}, self.saved.session)
        self.accept()
        self.snapshot = self.pin()
        self.origin = ReviewOrigin.admit(self.screen, self.client, self.snapshot, self.item['id'], self.saved, self.layout)
        self.frame = {'frame': 'a' * 64, 'width': 32, 'height': 24}

    def accept(self, ref=CLIENT):
        self.screen.accept_client(self.client, ref, app_run_id='run-a',
            source_resolver=lambda *_: pytest.fail('must not resolve a native source'),
            capture_authority=lambda *_: pytest.fail('must not acquire camera input'))

    def pin(self):
        return self.screen.pin(self.client, request_id='scientific-action', inline=self.raw)

    def change(self):
        self.raw['seq'] += 1
        self.item['revision'] += 1

    def check(self, snapshot=None, **changes):
        return self.origin.check(self.screen, snapshot or self.snapshot, self.layout,
            **({'review': 'review-one', 'frame': self.frame, 'draw': 'draw-one'} | changes))


@pytest.mark.parametrize('schema', [2, 3, 4, 5])
def test_exact_saved_origin_matches_issued_worker_frame_without_native_source_authority(tmp_path, schema):
    rig = Rig(tmp_path, schema)
    assert rig.check() == (20, 40, 160, 120)
    assert rig.origin.digest == rig.saved.digest
    assert rig.origin.reference == rig.ref
    assert rig.screen.observation(rig.client).wire()['sources'] == []
    changed = rig.saved.document
    changed['title'] = 'Newer saved work'
    rig.store.save(rig.saved.id, rig.saved.session, changed, expected_revision=rig.saved.revision)
    assert rig.check() == (20, 40, 160, 120)
    assert rig.origin.revision == rig.saved.revision


@pytest.mark.parametrize('change', ['tab', 'placement', 'resource', 'root', 'lifetime', 'source'])
def test_window_or_resource_changes_cannot_retarget_existing_worker(tmp_path, change):
    rig = Rig(tmp_path)
    rig.change()
    document = rig.item['view']['legacy']['document']
    if change in ('tab', 'placement'): document[change] = 'elsewhere'
    elif change == 'resource': document['resource'] = {**rig.ref, 'version': 2}
    elif change == 'root':
        rig.item['rootId'] = 'another-root'
        rig.item['lifetimeGeneration'] = 'new-lifetime'
    elif change == 'lifetime': rig.item['lifetimeGeneration'] = 'new-lifetime'
    else: rig.item['sourceGeneration'] += 1
    with pytest.raises(ObservationError): rig.check(rig.pin())


@pytest.mark.parametrize('change', ['review', 'frame', 'draw', 'width', 'height', 'missing'])
def test_only_the_issued_worker_frame_and_current_draw_can_authorize_interaction(tmp_path, change):
    rig = Rig(tmp_path)
    if change == 'missing':
        rig.change()
        rig.item['scientificPaint'] = None
        with pytest.raises(ObservationError): rig.check(rig.pin())
        return
    changes = {change: 'wrong'} if change in ('review', 'draw') else {
        'frame': {**rig.frame, change: 'b' * 64 if change == 'frame' else 64}}
    with pytest.raises(ObservationError): rig.check(**changes)


@pytest.mark.parametrize('change', ['paint', 'geometry', 'hidden', 'closed', 'detached'])
def test_action_pin_cannot_outlive_a_changed_current_observation(tmp_path, change):
    rig = Rig(tmp_path)
    rig.change()
    if change == 'paint': rig.item['scientificPaint']['draw'] = 'new-draw'
    elif change == 'geometry': rig.item['scientificPaint']['contentRect'][0] += 3
    else:
        rig.item[{'hidden': 'visible', 'closed': 'open', 'detached': 'connected'}[change]] = False
        rig.item['scientificPaint'] = None
    rig.pin()
    with pytest.raises(ObservationError): rig.check()


@pytest.mark.parametrize('change', ['remove-tab', 'remove-window', 'move-tab', 'resource'])
@pytest.mark.parametrize('schema', [2, 4])
def test_latest_server_arrangement_must_still_contain_exact_origin(tmp_path, change, schema):
    rig = Rig(tmp_path, schema)
    view = rig.layout['views'][0]
    if change == 'remove-tab': rig.layout['views'] = []
    elif change == 'remove-window': view['items'] = []
    elif change == 'move-tab': view['id'] = 'moved'
    else: view['items'][0]['resource'] = {**rig.ref, 'version': 2}
    with pytest.raises(ObservationError): rig.check()


@pytest.mark.parametrize('change', ['gone', 'reaccept', 'new-epoch', 'stale', 'background'])
def test_old_or_background_connection_cannot_keep_interaction_authority(tmp_path, change):
    rig = Rig(tmp_path)
    if change == 'gone': rig.screen.gone(rig.client)
    elif change == 'reaccept': rig.accept()
    elif change == 'new-epoch': rig.accept(ClientRef('workspace-a', 'client-a', 'page-a', 'new-socket'))
    elif change == 'stale': rig.now += 2
    else:
        rig.change()
        rig.raw['viewport']['visible'] = False
        rig.pin()
    with pytest.raises(ObservationError): rig.check()


def test_another_socket_or_saved_revision_cannot_admit_the_window(tmp_path):
    rig = Rig(tmp_path)
    other = object()
    with pytest.raises(ObservationError):
        ReviewOrigin.admit(rig.screen, other, rig.snapshot, rig.item['id'], rig.saved, rig.layout)
    changed = rig.saved.document
    changed['title'] = 'New revision'
    saved = rig.store.save(rig.saved.id, rig.saved.session, changed, expected_revision=rig.saved.revision)
    with pytest.raises(ObservationError):
        ReviewOrigin.admit(rig.screen, rig.client, rig.snapshot, rig.item['id'], saved, rig.layout)


def test_revoked_preview_can_refresh_but_cannot_interact_without_a_new_draw(tmp_path):
    rig = Rig(tmp_path)
    rig.change()
    rig.item['scientificPaint'] = None
    pin = rig.pin()
    assert rig.origin.check(rig.screen, pin, rig.layout) is None
    with pytest.raises(ObservationError): rig.check(pin)
    rig.change()
    rig.item['scientificPaint'] = {'review': 'review-one', **rig.frame, 'draw': 'fresh', 'contentRect': [0, 0, 320, 240]}
    assert rig.check(rig.pin(), draw='fresh') == (0, 0, 320, 240)


def test_button_focus_loss_during_admission_keeps_exact_scientific_frame_valid(tmp_path):
    rig = Rig(tmp_path)
    rig.change()
    rig.item['view']['legacy']['document']['focused'] = False
    rig.pin()
    assert rig.check() == (20, 40, 160, 120)


@pytest.mark.parametrize('change', ['draw', 'geometry', 'state', 'resource', 'window'])
def test_focus_loss_cannot_hide_a_scientific_change_during_admission(tmp_path, change):
    rig = Rig(tmp_path)
    rig.change()
    rig.item['view']['legacy']['document']['focused'] = False
    if change == 'draw': rig.item['scientificPaint']['draw'] = 'another-draw'
    elif change == 'geometry': rig.item['scientificPaint']['contentRect'][0] += 1
    elif change == 'state': rig.item['view']['legacy']['document']['state'] = 'requested'
    elif change == 'resource': rig.item['view']['legacy']['document']['resource']['version'] += 1
    else: rig.item['rect'][0] += 1
    rig.pin()
    with pytest.raises(ObservationError): rig.check()
