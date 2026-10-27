"""Real Project storage with synthetic transport; upstream worker cases are separate."""
import copy
import pytest

from env_worker.scientific_tool_document import snapshot, InvalidDocument, FORMAT
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_saves import SaveUnavailable
from tests.test_scientific_outputs import attempt, Worker
from tests.test_scientific_edits import scene, values


class ToolWorker(Worker):
    def __init__(self):
        super().__init__()
        self.state = {'format': FORMAT, 'schema': 'test.sequence', 'version': 1,
                      'state': {'keyframes': [{'steps': 15}], 'title': 'Saved sequence'}}
    def request(self, request):
        assert request == {'op': 'layer-inventory'}
        return {'inventory': {'layers': [{'id': 'a'*32}]}}, b''
    def describe_output(self, layer, *, tool=False):
        assert tool
        return super().describe_output(layer)
    def stage_outputs(self, rows, *, tool=False):
        assert tool and len(rows) == 1
        item = rows[0]
        state = copy.deepcopy(self.state)
        self.stage_output(item['layer'], item['metadata'], item['meaning'], item['grants']['output'])
        return {'document': state, 'bindings': {'specimen': item['names']['output']}}


@pytest.mark.parametrize('after', [False, True])
def test_tool_save_keeps_state_and_arrays_together_across_lost_acknowledgement(scene, monkeypatch, after):
    import orchestrator.scientific_outputs as module
    from orchestrator.scientific_project_review import _resolve
    store, saved, ordinary, _ = attempt(scene)
    worker = ToolWorker()
    save = OutputSave(store, saved, ordinary.identity, ordinary.layer, ordinary.meaning, tool=True)
    before = {row.id for row in store.list(saved.session)}
    expected = copy.deepcopy(worker.state)
    expected_data = worker.data.copy()
    save.capture(worker)
    assert {row.id for row in store.list(saved.session)} == before
    rename = module.os.rename
    def fail(source, target):
        if after: rename(source, target)
        raise OSError('Controlled publication acknowledgement failure')
    with monkeypatch.context() as patch:
        patch.setattr(module.os, 'rename', fail)
        with pytest.raises(SaveUnavailable): save.publish()
    worker.state['state']['title'] = 'Later private work'
    worker.data[:] = 0
    outcome = save.publish()
    assert outcome['tool'] is True and worker.calls == 1
    accepted = store.read(save.target, saved.session)
    assert accepted.document['title'] == saved.document['title']
    spec = accepted.document['state']['scientificView']
    assert spec['tool'] == {'document': expected, 'bindings': {'specimen': 'output'}}
    assert values(store, accepted, spec['inputs']['output']).tobytes() == expected_data.tobytes()
    assert store.read(saved.id, saved.session) == saved
    resolved, _, _ = _resolve(store, accepted.id, accepted.session, 1)
    assert resolved == accepted
    save.close()
    assert store.read(accepted.id, accepted.session) == accepted


@pytest.mark.parametrize('bindings', [{}, {'specimen': 'missing'}, {'a': 'output', 'b': 'output'}, {None: 'output'}])
def test_tool_bindings_cannot_omit_or_substitute_saved_layers(bindings):
    with pytest.raises(InvalidDocument):
        snapshot({'document': ToolWorker().state, 'bindings': bindings}, [{'input': 'output'}])


@pytest.mark.parametrize('source_units', [
    ['micrometer', 'micrometer'], ['um', 'µm'], ['micrometers', 'μm'],
])
@pytest.mark.parametrize('change', [None, 'unit_scale', 'affine'])
def test_bound_tool_copy_preserves_vocabulary_and_coordinate_identity(scene, source_units, change):
    from env_worker.scientific_output_wire import calibration
    store, saved, ordinary, _ = attempt(scene)
    worker = ToolWorker()
    meaning = {'semantic':'class-labels', 'axes':['z','x']}
    coordinates = calibration(worker.info, meaning['axes'], {'session':saved.session,'id':saved.id,'revision':1})
    coordinates['units'] = source_units
    metadata = {'semantic':'class-labels','calibration':coordinates,
                'class_vocabulary':[{'id':i,'name':f'Class {i}'} for i in range(35)],
                'sources':list(saved.document['resources'])}
    draft = store.array_draft(saved.id, saved.session, component='array.known', shape=worker.data.shape,
        dtype=worker.data.dtype.str, axes=meaning['axes'], chunks=worker.data.shape, scientific=metadata,
        max_written_bytes=worker.data.nbytes)
    draft.write([0,0], worker.data); reference=draft.finish()
    body=saved.document;body['resources'].append(reference)
    body['state']['scientificView']['inputs']={'known':reference}
    saved=store.save(saved.id,saved.session,body,expected_revision=saved.revision)
    original_data = worker.data.tobytes()
    if change == 'unit_scale': worker.info['geometry']['units'][0] = 'millimeter'
    if change == 'affine': worker.info['geometry']['basis'][0][0] += 1
    worker.request=lambda _: ({'inventory':{'layers':[{'id':'a'*32,'editableInput':'known'}]}},b'')
    save=OutputSave(store,saved,ordinary.identity,ordinary.layer,meaning,tool=True)
    try:
        if change is not None:
            from orchestrator.scientific_saves import SaveNotStarted
            before = {row.id for row in store.list(saved.session)}
            with pytest.raises(SaveNotStarted, match='changed input calibration'):
                save.capture(worker)
            assert worker.calls == 0
            assert {row.id for row in store.list(saved.session)} == before
            assert store.read(saved.id, saved.session) == saved
            return
        save.capture(worker);save.publish()
        accepted=store.read(save.target,saved.session)
        spec=accepted.document['state']['scientificView']
        source=store.array_source(accepted.id,accepted.session,1,spec['inputs']['output'])
        for key,value in metadata.items():assert source.descriptor[key]==value
        assert values(store, accepted, spec['inputs']['output']).tobytes() == original_data
        from orchestrator.scientific_project_review import _resolve
        reopened, _, _ = _resolve(store, accepted.id, accepted.session, 1)
        assert reopened == accepted
        assert accepted.document['state']['coordinateSpace']==coordinates
        assert store.read(saved.id,saved.session)==saved
    finally:save.close()
