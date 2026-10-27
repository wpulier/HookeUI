"""Synthetic Zoo metadata and arrays only; no model, weights or provider execution."""
import json

import numpy as np
import pytest

from orchestrator import link_recognise
from tests.test_link_recognise import ZOO, rdf, template, zoo, mine_home


def test_an_explicit_zoo_version_is_never_replaced_by_latest():
    http = zoo(rdf())
    http.files[f'{ZOO}/diatom-counter/versions.json'] = json.dumps({'versions': [{'v': '9.0'}, {'v': '1.0'}]}).encode()
    found = link_recognise.recognise(f'{ZOO}/diatom-counter/1.0/files/rdf.yaml', http=http)
    assert found.kind == 'template' and found.version.endswith('1.0')
    assert all('/1.0/' in f['url'] for f in found.files)


@pytest.mark.parametrize('names', [['distance', 'affinity'], ['class_a', 'class_b', 'class_c'], ['intensity']])
def test_unknown_zoo_output_semantics_require_an_adapter(names):
    description = rdf()
    description['outputs'][0]['axes'][1]['channel_names'] = names
    found = link_recognise.recognise('diatom-counter', http=zoo(description))
    assert found.kind == 'needs-code' and 'meaning' in found.why


def test_permuted_boundary_and_foreground_follow_the_declared_names():
    output = np.zeros((1, 2, 8, 8), np.float32)
    output[0, 1, 1:7, 1:7] = 1
    tensor = rdf()['outputs'][0]
    tensor['axes'][1]['channel_names'] = ['boundary', 'foreground']
    labels = template.to_labels(output, ['b', 'c', 'y', 'x'], (8, 8), tensor)
    assert labels.max() == 1 and labels[3, 3] == 1


def test_unnamed_float_output_is_never_guessed_from_its_channel_count():
    with pytest.raises(ValueError, match='meaning'):
        template.to_labels(np.ones((1, 2, 8, 8), np.float32), ['b', 'c', 'y', 'x'], (8, 8))


def test_reviewed_unnamed_output_binding_requires_both_exact_pins():
    bindings = json.loads((link_recognise.TEMPLATES / 'bioimageio' / 'output-semantics.json').read_text())
    rdf_hash, binding = next(iter(bindings.items()))
    weight_hash = binding['weightsSha256']
    assert link_recognise.reviewed_zoo_output(rdf_hash, weight_hash)['channels'] == ['foreground', 'boundary']
    assert link_recognise.reviewed_zoo_output('0' * 64, weight_hash) is None
    assert link_recognise.reviewed_zoo_output(rdf_hash, '0' * 64) is None
    tensor = {'axes': [{'type': 'channel', 'channel_names': ['channel0', 'channel1']}]}
    native = json.dumps(tensor, sort_keys=True)
    assert template.resolved_meaning(tensor, rdf_hash, weight_hash) == ['foreground', 'boundary']
    for rh, wh in [('0' * 64, weight_hash), (rdf_hash, '0' * 64)]:
        with pytest.raises(ValueError, match='meaning'):
            template.resolved_meaning(tensor, rh, wh)
    assert json.dumps(tensor, sort_keys=True) == native, 'the RDF stays unchanged'
    assert 'nearest-seed' in binding['conversion'] and 'watershed' in binding['conversion']


def test_semantics_data_is_part_of_the_reviewed_template_entry(mine_home, monkeypatch):
    from gpu_worker import manifests as catalog
    from orchestrator import mine
    monkeypatch.setattr(link_recognise, '_http', lambda: zoo(rdf()))
    added = mine.add('diatom-counter')
    m = catalog.mine()[added['model']]
    path = catalog.entry_folder(m) / 'output-semantics.json'
    assert path.read_bytes() == (link_recognise.TEMPLATES / 'bioimageio' / path.name).read_bytes()
    assert link_recognise.reviewed_template(m) == 'bioimageio'
    path.write_text('{}')
    assert link_recognise.reviewed_template(m) == ''


def test_exact_synthetic_binding_reaches_add_without_rewriting_unnamed_rdf(mine_home, monkeypatch):
    import hashlib
    import shutil
    from gpu_worker import manifests as catalog
    from orchestrator import mine
    description = rdf()
    description['outputs'][0]['axes'][1]['channel_names'] = ['channel0', 'channel1']
    http = zoo(description)
    original = http.files[f'{ZOO}/diatom-counter/1.0/files/rdf.yaml']
    templates = mine_home / 'templates'
    shutil.copytree(link_recognise.TEMPLATES / 'bioimageio', templates / 'bioimageio')
    binding = {'weightsSha256': description['weights']['torchscript']['sha256'], 'channels': ['foreground', 'boundary'],
               'conversion': "Synthetic test of ObservationUI nearest-seed conversion; not upstream watershed"}
    (templates / 'bioimageio' / 'output-semantics.json').write_text(json.dumps({hashlib.sha256(original).hexdigest(): binding}))
    monkeypatch.setattr(link_recognise, 'TEMPLATES', templates)
    monkeypatch.setattr(link_recognise, '_http', lambda: http)
    added = mine.add('diatom-counter')
    assert added['kind'] == 'template:bioimageio'
    m = catalog.mine()[added['model']]
    assert m.pins['output_conversion'] == binding['conversion']
    assert 'ObservationUI nearest-seed v1' in m.version
    assert next(f['sha256'] for f in m.files if f['path'] == 'rdf.yaml') == hashlib.sha256(original).hexdigest()
    assert http.files[f'{ZOO}/diatom-counter/1.0/files/rdf.yaml'] == original
    # Same RDF with a different reviewed weight pin remains unsupported.
    binding['weightsSha256'] = 'd' * 64
    (templates / 'bioimageio' / 'output-semantics.json').write_text(json.dumps({hashlib.sha256(original).hexdigest(): binding}))
    assert link_recognise.recognise('diatom-counter', http=http).kind == 'needs-code'
