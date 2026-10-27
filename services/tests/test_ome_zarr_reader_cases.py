"""Tiny shipped reader cases over real intake/SDK and synthetic upstream/viewer seams.

This checks fixture consistency and adapter interpretation, not genuine package,
confined native proof, large-data or ordinary-UI qualification.
"""
import importlib.util
import json
from types import SimpleNamespace

import numpy as np
import pytest

from gpu_worker import bundle_contract, manifests as catalog
from orchestrator import bundle_admission, reader_proof
from orchestrator.scientific_projects import ProjectStore
from env_worker.scientific_reader_probe import observe
from tests.test_ome_zarr_companion import ROOT, adapter, Level  # noqa:F401
from tests.test_scientific_directories import transfer, granted
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa:F401


def test_shipped_reference_inputs_are_small_reproducible_and_locally_eligible():
    owner, errors = catalog.read(ROOT/'model.toml')
    assert owner, errors
    assert owner.version == '7' and bundle_admission.local_reader(owner)
    assert bundle_admission.integration_problem(owner)  # No public qualification implied.
    spec = importlib.util.spec_from_file_location('ome_reference_recipe', ROOT/'generate_reference_cases.py')
    recipe = importlib.util.module_from_spec(spec); spec.loader.exec_module(recipe)
    generated = recipe.files()
    assert sum(map(len, generated.values())) < 8192
    assert all((ROOT/name).read_bytes() == body for name, body in generated.items())
    member = bundle_contract.parse_members(list(owner.members), owner)[0]
    declared = {path for case in member.reader_checks['cases'] for path in [*case['files'].values(), case['reference']]}
    assert declared == set(generated)


@pytest.mark.parametrize('case_id', ['calibrated-grey-pyramid', 'calibrated-object-labels'])
def test_reference_case_reaches_exact_shared_observation(tmp_path, adapter, monkeypatch, case_id):
    owner, errors = catalog.read(ROOT/'model.toml'); assert owner, errors
    member = bundle_contract.parse_members(list(owner.members), owner)[0]
    case = next(case for case in member.reader_checks['cases'] if case['id'] == case_id)
    files, _, expected, _ = reader_proof.inputs(ROOT, case)
    attrs = json.loads(files['.zattrs']); declared = attrs['multiscales'][0]
    decoded = []
    for dataset in declared['datasets']:
        path = dataset['path']; metadata = json.loads(files[path+'/.zarray'])
        chunk = path + '/' + '.'.join('0' for _ in metadata['shape'])
        values = np.frombuffer(files[chunk], dtype=metadata['dtype']).reshape(metadata['shape']).copy()
        decoded.append(Level(values))
    # Upstream package is a declared stand-in here; production adapter preflight,
    # transform composition, semantic choice and shared observer remain real.
    monkeypatch.setattr(adapter, 'SelectedLocation', lambda *a, **k: SimpleNamespace(root_attrs=attrs))
    monkeypatch.setattr(adapter, 'FSStore', lambda *a, **k: None)
    monkeypatch.setattr(adapter, 'Reader', lambda *a: lambda: [SimpleNamespace(data=decoded, metadata={})])
    layers, bindings = [], {}
    def add(kind, data, **settings):
        layer = SimpleNamespace(_type_string=kind, data=data, multiscale=False, rgb=False)
        layer.__dict__.update(settings); layer.ndim = len(layer.axis_labels)
        layer.data_to_world = lambda point: tuple(t+s*p for t,s,p in zip(layer.translate,layer.scale,point))
        layers.append(layer)
        return layer
    store = ProjectStore(tmp_path); saved = transfer(store,files); grants, source = granted(store,saved)
    context = SimpleNamespace(files={'source':source}, viewer=SimpleNamespace(layers=layers,
        add_image=lambda data, **kw:add('image',data,**kw), add_labels=lambda data, **kw:add('labels',data,**kw)),
        bind_view=lambda layer,key:bindings.update({id(layer):key}),
        refuse_input=lambda reason,recovery:(_ for _ in ()).throw(ValueError(reason+' '+recovery)))
    try:
        adapter.open_host(context)
        actual = observe(SimpleNamespace(viewer=context.viewer,
                                        view_bindings=SimpleNamespace(key_for=lambda layer:bindings.get(id(layer)))))
    finally:
        grants.close()
    assert actual == expected
    if case_id == 'calibrated-grey-pyramid':
        assert layers[0]._type_string == 'image' and layers[0].multiscale
        assert [level.shape for level in layers[0].data] == [(2,4,6),(1,2,3)]
    else:
        assert layers[0]._type_string == 'labels'
        assert sorted(np.unique(layers[0].data)) == [0,7,9,65537]
