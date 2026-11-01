"""Explicit prediction counts and stable generated prefixes on synthetic matrices."""
import json

import numpy as np
import pytest
from scipy import sparse

from scientific_compute import engine
from scientific_compute.catalog import parameters


def panel(rows):
    return {'format': 'target-count-panel-v1', 'targets': rows}


def test_explicit_counts_and_legacy_uniform_panel():
    targets, counts, meaning = engine._prediction_panel(panel([
        {'target': 'a', 'count': 2}, {'target': 'b', 'count': 7}]), 99)
    assert list(targets) == ['a', 'b'] and counts == [2, 7]
    assert meaning == {'source': 'target-count-panel-v1', 'totalPerContext': 9,
                       'minimumPerTarget': 2, 'maximumPerTarget': 7}
    targets, counts, meaning = engine._prediction_panel(['a', 'b'], 3)
    assert list(targets) == ['a', 'b'] and counts == [3, 3]
    assert meaning['source'] == 'cells_per_target'


@pytest.mark.parametrize('value', [None, {}, {'format': 'unknown', 'targets': []},
    panel([]), panel([{'target': 'a', 'count': 1, 'extra': 'uninterpreted'}]),
    panel([{'target': 'a'}]), panel([{'target': 'a', 'count': True}]),
    panel([{'target': 'a', 'count': 1.5}]), panel([{'target': 'a', 'count': 0}]),
    panel([{'target': 'a', 'count': 10001}]), panel([{'target': '', 'count': 1}]),
    panel([{'target': 'a', 'count': 1}, {'target': 12, 'count': 2}]),
    panel([{'target': 'a', 'count': 1}, {'target': None, 'count': 2}]),
    panel([{'target': 'a', 'count': 1}, {'target': 'a', 'count': 2}])])
def test_ambiguous_or_invalid_count_panel_refuses(value):
    with pytest.raises(engine.Refused):
        engine._prediction_panel(value, 5)


def test_panel_total_respects_existing_row_allowance(monkeypatch):
    monkeypatch.setattr(engine, 'MAX_ROWS', 10)
    with pytest.raises(engine.Refused, match='observation allowance'):
        engine._prediction_panel(panel([{'target': 'a', 'count': 6}, {'target': 'b', 'count': 5}]), 1)


def test_emission_obeys_counts_and_keeps_prefixes_across_panel_order_and_blocks(tmp_path, monkeypatch):
    anndata = pytest.importorskip('anndata')
    pd = pytest.importorskip('pandas')
    controls = tmp_path / 'controls.h5ad'
    anndata.AnnData(sparse.csr_matrix([[4, 6], [6, 4], [8, 2], [2, 8]], dtype=np.float32),
        obs=pd.DataFrame({'context': ['x', 'x', 'y', 'y'], 'target_gene': ['non-targeting'] * 4},
                         index=['o1', 'o2', 'o3', 'o4']),
        var=pd.DataFrame(index=['g1', 'g2'])).write_h5ad(controls)
    model = tmp_path / 'model.npz'
    np.savez_compressed(model, format=np.array('response-model-v1'), effects=np.zeros((1, 2)),
        genes=np.array(['g1', 'g2']), targets=np.array(['a']), metadata=np.array('{}'))
    outputs = []
    for i, rows in enumerate([
            [{'target': 'a', 'count': 2}, {'target': 'b', 'count': 5}],
            [{'target': 'b', 'count': 7}, {'target': 'a', 'count': 4}]]):
        request = tmp_path / f'panel{i}.json'; request.write_text(json.dumps(panel(rows)))
        folder = tmp_path / f'out{i}'; folder.mkdir()
        monkeypatch.setattr(engine, 'BLOCK_ROWS', i + 1)
        output, metadata = engine.predict({'model': model, 'controls': controls, 'panel': request},
            parameters('response-predict', {'cells_per_target': 99, 'seed': 8}), folder, lambda: None)
        data = anndata.read_h5ad(output)
        assert metadata['shape'] == [2 * sum(r['count'] for r in rows), 2]
        assert metadata['targetCounts']['source'] == 'target-count-panel-v1'
        assert metadata['unseenTargets'] == ['b']
        assert data.obs_names.is_unique and list(data.var_names) == ['g1', 'g2']
        assert sparse.issparse(data.X)
        assert np.all(np.isfinite(data.X.data)) and np.all(data.X.data >= 0)
        assert np.all(data.X.data == np.floor(data.X.data))
        for context in ['x', 'y']:
            for row in rows:
                assert int(((data.obs.context == context) & (data.obs.target_gene == row['target'])).sum()) == row['count']
        outputs.append(data)
    first, second = outputs
    np.testing.assert_array_equal(first.X.toarray(), second[first.obs_names].X.toarray())
