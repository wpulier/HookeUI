"""Synthetic annotated files; exact coverage diagnostics, not biological validation."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

pd = pytest.importorskip('pandas')
ad = pytest.importorskip('anndata')

from scientific_compute import engine
from scientific_compute.catalog import parameters


def matrix(labels):
    frame = pd.DataFrame({'group': labels})
    return SimpleNamespace(obs=frame, n_obs=len(frame))


def test_inspection_counts_all_rows_and_preserves_missing_and_unused_categories(tmp_path):
    labels = pd.Categorical(['control'] * 11 + ['α', None, '', 'α'],
                            categories=['control', 'α', '', 'unused'])
    data = ad.AnnData(np.ones((15, 2), dtype=np.float32),
                     obs=pd.DataFrame({'group': labels}, index=[f'o{i}' for i in range(15)]),
                     var=pd.DataFrame(index=['a', 'b']))
    source = tmp_path / 'input.h5ad'; data.write_h5ad(source)
    out = tmp_path / 'output'; out.mkdir()
    path, result = engine.inspect({'matrix': source},
        parameters('matrix-inspect', {'annotation_column': 'group'}), out, lambda: None)
    counts = result['observationAnnotationCounts']
    assert counts['complete'] is True
    assert counts['observations'] == 15 and counts['missing'] == 1
    assert counts['distinctObservedLabels'] == 3
    assert counts['groups'] == [{'label': '', 'count': 1}, {'label': 'control', 'count': 11},
                                {'label': 'α', 'count': 2}]
    assert sum(row['count'] for row in counts['groups']) + counts['missing'] == 15
    assert json.loads(path.read_text())['observationAnnotationCounts'] == counts
    # Preview is positional; coverage is over the whole annotation, not its first ten rows.
    assert result['preview']['observationAnnotations'][0]['values'] == ['control'] * 10


def test_default_does_not_select_an_annotation_or_infer_groups():
    assert engine._annotation_counts(matrix(['a']), '', lambda: None) is None
    assert parameters('matrix-inspect', {})['annotation_column'] == ''


@pytest.mark.parametrize('column', ['absent', ' group', 'x' * 257, None, 3])
def test_column_is_literal_and_bounded(column):
    with pytest.raises(engine.Refused):
        engine._annotation_counts(matrix(['a']), column, lambda: None)


@pytest.mark.parametrize('labels', [[1, 2], [True, False], ['x' * 257]])
def test_no_coercion_or_truncation_of_group_values(labels):
    with pytest.raises(engine.Refused, match='text labels'):
        engine._annotation_counts(matrix(labels), 'group', lambda: None)


def test_cardinality_boundary_and_no_partial_report():
    data = matrix([f'label{i}' for i in range(4096)])
    result = engine._annotation_counts(data, 'group', lambda: None)
    assert result['complete'] and result['distinctObservedLabels'] == 4096
    with pytest.raises(engine.Refused, match='no partial'):
        engine._annotation_counts(matrix([f'label{i}' for i in range(4097)]), 'group', lambda: None)


def test_all_missing_is_explicit_and_no_categories_are_invented():
    result = engine._annotation_counts(matrix([None, None]), 'group', lambda: None)
    assert result['groups'] == [] and result['missing'] == 2 and result['complete']


def test_duplicate_column_and_cancellation_refuse():
    data = SimpleNamespace(obs=pd.DataFrame([['a', 'b']], columns=['group', 'group']), n_obs=1)
    with pytest.raises(engine.Refused, match='ambiguous'):
        engine._annotation_counts(data, 'group', lambda: None)
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise InterruptedError('stopped')
    with pytest.raises(InterruptedError):
        engine._annotation_counts(matrix(['a'] * 3000), 'group', check)
    assert calls == 2
