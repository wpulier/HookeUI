"""Real accepted storage; resource publication must not invent a rendered frame."""
import numpy as np
import pytest

from orchestrator import scientific_edits
from orchestrator.scientific_projects import Conflict, InvalidProject
from tests.test_scientific_edits import scene, values, view


def test_resource_publication_needs_no_view_and_preserves_exact_base(scene):
    store, saved, reference, original = scene
    owner = scientific_edits.ResourceEdits(store, saved.id, saved.session, saved.revision, reference,
                                           layer='labels')
    edit = owner.begin(max_written_bytes=12, producer={'kind':'python','cell':'recorded-cell'})
    patch = np.full((2,3), 60000, dtype='>u2')
    edit.write([0,0], patch)
    accepted = edit.commit()
    provenance = accepted.document['state']['hookeEdit']
    assert provenance['frame'] is None and provenance['view'] is None
    assert provenance['authority'] == 'accepted-resource'
    assert provenance['base']['revision'] == saved.revision
    assert provenance['input'] == reference and provenance['layer'] == 'labels'
    expected = original.copy(); expected[:2,:3] = patch
    assert np.array_equal(values(store,accepted,accepted.document['resources'][0]), expected)
    assert np.array_equal(values(store,saved,reference), original)
    assert edit.commit() == accepted
    with pytest.raises(Conflict): owner.begin(max_written_bytes=12)


def test_resource_authority_cannot_authorize_a_pointer_gesture(scene):
    store,saved,reference,_ = scene
    resource = scientific_edits.ResourceEdits(store,saved.id,saved.session,saved.revision,reference,layer='labels')
    gesture = view(scene)
    with pytest.raises(Conflict): gesture.begin(resource, max_written_bytes=12)
    frame = gesture.present(view='unpainted',layer='labels',context={})
    with pytest.raises(Conflict,match='displaying'): gesture.begin(frame,max_written_bytes=12)


def test_closed_resource_capture_and_concurrent_publication_remain_refused(scene):
    store,saved,reference,_ = scene
    owner = scientific_edits.ResourceEdits(store,saved.id,saved.session,saved.revision,reference,layer='labels')
    edit = owner.begin(max_written_bytes=12)
    winner = store.save(saved.id,saved.session,{**saved.document,'title':'winner'},expected_revision=saved.revision)
    with pytest.raises(Conflict): edit.commit()
    assert store.read(saved.id,saved.session) == winner
    owner.close()
    with pytest.raises(InvalidProject): owner.begin(max_written_bytes=12)
