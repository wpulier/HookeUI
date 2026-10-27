"""Synthetic benchmark receipt checks; no native worker or large-data execution."""
from types import SimpleNamespace
import numpy as np
import pytest
from bench import h08_large_data as bench


def pyramid():
    return SimpleNamespace(levels=[SimpleNamespace(shape=(1, 2, 3), dtype=np.dtype('<u2'))],
                           axes=('z', 'y', 'x'), revision='accepted-fixture-revision')


def test_dataset_receipt_uses_opened_shape_and_refuses_requested_substitute():
    with pytest.raises(ValueError, match='shape'):
        bench.inspect_dataset(pyramid(), (32, 8192, 8192), 1, 1)
    facts = bench.inspect_dataset(pyramid(), (1, 2, 3), 1, 3)
    assert facts['shape'] == [1, 2, 3] and facts['logicalBytes'] == 12
    with pytest.raises(ValueError, match='4x'):
        bench.inspect_dataset(pyramid(), (1, 2, 3), 1, 4)


@pytest.mark.parametrize('samples', [[None], [1.0, None], [0.0], [float('nan')], [float('inf')]])
def test_missing_or_invalid_footprint_never_certifies_budget(samples):
    result = bench.summarize_footprints(samples, 16 << 20)
    assert result['withinBudget'] is False and result['complete'] is False


def test_footprint_maximum_is_labeled_sampled_and_requires_every_observation():
    result = bench.summarize_footprints([4.0, 9.0, 5.0], 8 << 20)
    assert result == {'sampledMaximumMiB': 9.0, 'complete': True, 'withinBudget': False}
    assert bench.summarize_footprints([4.0, 5.0], 8 << 20)['withinBudget'] is True
