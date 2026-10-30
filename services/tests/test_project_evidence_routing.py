"""Real saved populations in isolated Projects; synthetic masks and image bytes."""
import copy
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.day.store import SessionStore
from orchestrator.evidence import Evidence
from orchestrator.project_evidence import resolve
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectRegistry, ProjectRoot, ProjectLocationError
from orchestrator.resources import Index
from tests.test_evidence import population, view, saved


def clock():
    # Match the declared capture date regardless of the runner's current day/timezone.
    return time.mktime((2026, 10, 29, 12, 0, 0, 0, 0, -1))


@pytest.fixture
def project(tmp_path):
    legacy = tmp_path / 'host/sessions'
    SessionStore(legacy, clock=clock, session='2026-10-29')
    locations = ProjectLocations(legacy, ProjectRegistry(tmp_path/'host'), tmp_path/'Projects')
    day = SessionStore(legacy, clock=clock, session='2026-10-29', locations=locations)
    day.new('Microscopy')
    hub = SimpleNamespace()
    evidence = Evidence(hub, tmp_path/'host/evidence', day=day)
    hub.evidence = evidence
    try:
        yield day, locations, evidence, hub
    finally:
        locations.close()


def make(evidence, day):
    p = population(evidence, view())
    p.session = day.sid
    evidence.add(p)
    evidence.schedule_save(p)
    assert saved(p).status == 'saved', p.status_why
    return p


def test_moved_project_preserves_each_accepted_population_save(project, tmp_path):
    day, locations, evidence, hub = project
    p = make(evidence, day)
    owner = day.sid
    original = copy.deepcopy(day.evidence[p.id])
    original_bytes = {f.name: f.read_bytes() for f in p.path.iterdir()}
    evidence.link(p, 'same-object', 'c1', 'o9', 'review')
    assert saved(p).status == 'saved', p.status_why
    linked = copy.deepcopy(day.evidence[p.id])
    with evidence.keeping_pixels(p):
        evidence.reload_masks(p)
        evidence.revise(p, 'mark', 'human', ['c1'])
        assert saved(p).status == 'saved', p.status_why
    latest = copy.deepcopy(day.evidence[p.id])
    assert len({r['path'] for r in (original, linked, latest)}) == 3
    assert all(f.read_bytes() == original_bytes[f.name] for f in resolve(evidence,owner,original).iterdir())
    assert not evidence.root.exists()
    root = locations.project(owner)
    day.new('Another Project')
    index = Index(hub, day=day)
    retained = index.captured_manifest(p.id, owner)
    assert retained and retained[0]['revision'] == 1
    assert index.picture(p.id, day=owner)[0] == original_bytes['capture.jpg']
    locations.close()
    moved = tmp_path/'Moved microscopy'; root.path.rename(moved)
    fresh = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
        reopened = SessionStore(fresh.legacy, session=owner, locations=fresh)
        reader = Evidence(SimpleNamespace(), evidence.root, day=reopened)
        back = reader.get(p.id)
        assert back and back.revision == 1 and back.links[0]['to'] == 'o9'
        assert reader.reload_masks(back)
        for record in (original, linked, latest):
            folder = resolve(reader, owner, record)
            assert folder.is_relative_to(moved)
            assert hashlib.sha256((folder/'manifest.json').read_bytes()).hexdigest() == record['manifestSha256']
        assert json.loads((resolve(reader,owner,original)/'manifest.json').read_bytes())['revision'] == 0
    finally:
        fresh.close()


@pytest.mark.parametrize('change', ['closed', 'missing', 'replaced', 'symlink'])
def test_late_save_refuses_changed_destination_without_erasing_accepted_bytes(project, tmp_path, change):
    day, locations, evidence, _ = project
    p = make(evidence, day)
    accepted = copy.deepcopy(day.evidence[p.id])
    root = evidence.root_for()
    old = (p.path/'manifest.json').read_bytes()
    if change == 'closed':
        locations.close()
    else:
        root.rename(root.with_name('preserved'))
        if change == 'replaced': root.mkdir()
        if change == 'symlink': root.symlink_to(tmp_path, target_is_directory=True)
    evidence.schedule_save(p)
    assert saved(p).status == 'failed'
    assert day.evidence[p.id] == accepted
    path = p.path if change == 'closed' else root.with_name('preserved') / p.path.relative_to(root)
    assert (path/'manifest.json').read_bytes() == old
    assert not evidence.root.exists()


def test_portable_evidence_refuses_different_origin_or_modified_manifest(project):
    day, _, evidence, _ = project
    p = make(evidence, day)
    record = day.evidence[p.id]
    with pytest.raises(ProjectLocationError, match='origin'):
        resolve(evidence, day.sid, {**record, 'path': record['path'].replace(record['path'][8:40], 'a'*32)})
    (p.path/'manifest.json').write_text('{}')
    with pytest.raises(ProjectLocationError, match='accepted revision'):
        resolve(evidence, day.sid, record)
    evidence.read_back()
    assert p.id in evidence.unavailable


def test_second_host_can_read_evidence_but_cannot_publish(project):
    day, locations, evidence, _ = project
    p = make(evidence, day)
    other = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        host = SessionStore(other.legacy, session='2026-10-29', locations=other)
        reader = Evidence(SimpleNamespace(), evidence.root, day=host)
        assert resolve(reader, day.sid, day.evidence[p.id]) == p.path
        with pytest.raises(ProjectLocationError, match='read-only'):
            reader.root_for(day.sid, write=True)
    finally:
        other.close()


def test_directory_sync_failure_leaves_prior_acceptance_readable(project, monkeypatch):
    from orchestrator.scientific_projects import ProjectStore
    day, _, evidence, _ = project
    p = make(evidence, day)
    original = copy.deepcopy(day.evidence[p.id])
    def fail(path): raise OSError('injected syncing failure')
    monkeypatch.setattr(ProjectStore, '_sync_directory', fail)
    evidence.schedule_save(p)
    assert saved(p).status == 'failed'
    assert day.evidence[p.id] == original
    assert resolve(evidence, day.sid, original).exists()


def test_link_cannot_bless_changed_mask_bytes(project):
    import numpy as np
    day, _, evidence, _ = project
    p = make(evidence, day)
    accepted = copy.deepcopy(day.evidence[p.id])
    with np.load(p.path/'masks.npz') as old:
        arrays = {k: old[k] for k in old.files}
    arrays['c1.bits'] = np.zeros_like(arrays['c1.bits'])
    np.savez_compressed(p.path/'masks.npz', **arrays)
    evidence.link(p, 'same-object', 'c1', 'o9', 'review')
    assert saved(p).status == 'failed'
    assert 'Previous evidence bytes changed' in p.status_why
    assert day.evidence[p.id] == accepted


def test_workspace_reconciles_retained_population_by_project_origin(project):
    from orchestrator import workspace_views as w
    from tests.test_workspace_recovery import migrated
    day, _, evidence, _ = project
    w.save(day, migrated(day), 0)
    origin = {'session':day.sid,'tab':'main','placementId':'pending-evidence','run':'research-run'}
    w.admit(day, origin)
    w.link_execution(day, origin, 'measured-execution')
    p = population(evidence, view())
    p.session = day.sid
    p.made_by = {'execution':'measured-execution'}
    evidence.add(p); evidence.schedule_save(p)
    assert saved(p).status == 'saved', p.status_why
    w.reconcile(day)
    rows = w.read(day, schema=2)['completions']
    assert len(rows) == 1
    assert rows[0]['item']['resource']['id'] == p.id
    assert rows[0]['item']['presentation'] == 'masks'
    w.reconcile(day)
    assert len(w.read(day, schema=2)['completions']) == 1


@pytest.mark.parametrize('replacement', ['link', 'fifo'])
def test_manifest_replacement_refuses_resolution_and_publication_without_blocking(project, tmp_path, replacement):
    import os
    from orchestrator.project_evidence import reference
    day, _, evidence, _ = project
    p = make(evidence, day)
    manifest = p.path/'manifest.json'
    original = manifest.read_bytes()
    target = tmp_path/'elsewhere.json'; target.write_bytes(original)
    manifest.unlink()
    if replacement == 'link': manifest.symlink_to(target)
    else: os.mkfifo(manifest)
    with pytest.raises((OSError, ValueError)):
        resolve(evidence, day.sid, day.evidence[p.id])
    with pytest.raises(ProjectLocationError, match='unchanged'):
        reference(evidence, p, p.path)


@pytest.mark.parametrize('regenerate', [False, True])
def test_reopened_optional_enrichment_can_be_recovered_without_accepting_changed_bytes(project, regenerate):
    import numpy as np
    day, _, evidence, _ = project
    p = make(evidence, day)
    p.columns['looks:test'] = {'columns': {}, 'values': {}}
    p.vectors['looks:test'] = {'ids': ['c1'], 'array': np.array([[1., 2.]])}
    evidence.schedule_save(p)
    assert saved(p).status == 'saved', p.status_why
    old = copy.deepcopy(day.evidence[p.id])
    # A valid but altered archive must not be carried over as recovered enrichment.
    np.savez_compressed(p.path/'vectors.npz', **{'looks:test.ids': np.array(['c1']), 'looks:test.v': np.array([[99.,99.]])})
    reopened = Evidence(SimpleNamespace(), evidence.root, day=day)
    q = reopened.get(p.id)
    assert q and 'looks:test' not in q.columns
    assert any('looks were not read back' in problem for problem in reopened.problems)
    if regenerate:
        q.vectors['looks:new'] = {'ids': ['c1'], 'array': np.array([[3., 4.]])}
    reopened.link(q, 'same-object', 'c1', 'o9', 'review')
    assert saved(q).status == 'saved', q.status_why
    manifest = json.loads((q.path/'manifest.json').read_bytes())
    if regenerate:
        with np.load(q.path/'vectors.npz') as values:
            assert set(values.files) == {'looks:new.ids', 'looks:new.v'}
            assert values['looks:new.v'].tolist() == [[3.,4.]]
    else:
        assert 'vectors.npz' not in manifest['files']
    assert old['path'] != day.evidence[p.id]['path']


def test_restored_optional_vectors_clear_prior_recovery_state(project):
    import numpy as np
    day, _, evidence, _ = project
    p = make(evidence, day)
    p.columns['looks:test'] = {'columns': {}, 'values': {}}
    p.vectors['looks:test'] = {'ids': ['c1'], 'array': np.array([[1., 2.]])}
    evidence.schedule_save(p)
    assert saved(p).status == 'saved'
    path = p.path/'vectors.npz'
    original = path.read_bytes()
    path.write_bytes(b'damaged')
    evidence.reload()
    assert 'looks:test' not in evidence.get(p.id).columns
    path.write_bytes(original)
    evidence.reload()
    q = evidence.get(p.id)
    assert 'looks:test' in q.columns
    evidence.link(q, 'same-object', 'c1', 'o9', 'review')
    assert saved(q).status == 'saved', q.status_why
    with np.load(q.path/'vectors.npz') as vectors:
        assert vectors['looks:test.v'].tolist() == [[1.,2.]]
