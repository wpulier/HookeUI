"""Descriptive statistics (orchestrator/stats.py) and the measurement table's summary (tables._stats): every number
against numpy and scipy within 1e-12, the edge cases (no values, one, all equal, NaN rows), the same input giving the
same JSON, and a table's earlier summary keys unchanged beside the new ones."""
from __future__ import annotations

import json

import numpy as np
import pytest
import scipy.stats

from orchestrator.stats import describe, finite, t_critical
from orchestrator.tables import _stats

TOL = {"rel": 1e-12, "abs": 0.0}  # relative only: "tiny" is near 1e-9, where an absolute 1e-12 would pass anything
_rng = np.random.default_rng(20261023)
SAMPLES = {
    "two": [1.0, 2.0],
    "small integers": [3, 1, 4, 1, 5, 9, 2, 6],
    "areas": [412.5, 388.0, 520.25, 301.75, 455.0],
    "normal": _rng.normal(50.0, 7.5, 200).tolist(),
    "skewed": _rng.lognormal(0.0, 1.0, 57).tolist(),
    "large offset": (1e6 + _rng.normal(0.0, 1.0, 31)).tolist(),
    "tiny": (_rng.normal(0.0, 1.0, 12) * 1e-9).tolist(),
}


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_every_number_matches_numpy_and_scipy(name):
    values = np.array(SAMPLES[name], float)
    got = describe(values)
    n = len(values)
    mean, sd = np.mean(values), np.std(values, ddof=1)
    sem = scipy.stats.sem(values)
    low, high = scipy.stats.t.interval(0.95, n - 1, loc=mean, scale=sem)
    q1, median, q3 = np.percentile(values, [25, 50, 75])
    assert got["n"] == n
    assert got["mean"] == pytest.approx(mean, **TOL)
    assert got["sd"] == pytest.approx(sd, **TOL)
    assert got["sem"] == pytest.approx(sem, **TOL)
    assert got["ci95"] == [pytest.approx(low, **TOL), pytest.approx(high, **TOL)]
    assert got["median"] == pytest.approx(median, **TOL) == pytest.approx(scipy.stats.scoreatpercentile(values, 50), **TOL)
    assert (got["q1"], got["q3"]) == (pytest.approx(q1, **TOL), pytest.approx(q3, **TOL))
    assert (got["min"], got["max"]) == (values.min(), values.max())


def test_the_t_critical_value_is_scipys():
    for df in (1, 2, 3, 9, 30, 199, 10_000):
        assert t_critical(df) == pytest.approx(scipy.stats.t.ppf(0.975, df), **TOL)
    assert t_critical(4, 0.99) == pytest.approx(scipy.stats.t.ppf(0.995, 4), **TOL)
    with pytest.raises(ValueError):
        t_critical(0)


def test_no_values_give_only_the_count():
    for empty in ([], np.array([]), [float("nan")], [float("inf"), -float("inf")]):
        assert describe(empty) == {"n": 0, "mean": None, "sd": None, "sem": None, "ci95": None,
                                   "median": None, "q1": None, "q3": None, "min": None, "max": None}


def test_one_value_has_no_spread():
    got = describe([7.25])
    assert got == {"n": 1, "mean": 7.25, "sd": None, "sem": None, "ci95": None,
                   "median": 7.25, "q1": 7.25, "q3": 7.25, "min": 7.25, "max": 7.25}


def test_all_equal_values_have_zero_spread_and_a_point_interval():
    got = describe([4.5] * 6)
    assert (got["n"], got["mean"], got["sd"], got["sem"], got["ci95"]) == (6, 4.5, 0.0, 0.0, [4.5, 4.5])
    assert (got["median"], got["q1"], got["q3"], got["min"], got["max"]) == (4.5,) * 5


def test_nan_and_infinite_rows_are_left_out_and_not_counted():
    with_gaps = [1.0, float("nan"), 2.0, float("inf"), 4.0, float("nan")]
    assert describe(with_gaps) == describe([1.0, 2.0, 4.0])
    assert describe(with_gaps)["n"] == 3
    assert finite(np.array([[1.0, np.nan], [3.0, -np.inf]])).tolist() == [1.0, 3.0]


def test_results_are_plain_json_types():
    got = describe(np.array(SAMPLES["areas"], np.float32))
    assert type(got["n"]) is int
    assert all(type(got[k]) is float for k in ("mean", "sd", "sem", "median", "q1", "q3", "min", "max"))
    assert all(type(v) is float for v in got["ci95"])
    json.dumps(got, allow_nan=False)


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_the_same_input_gives_identical_json(name):
    first = json.dumps(describe(SAMPLES[name]), allow_nan=False)
    again = json.dumps(describe(list(SAMPLES[name])), allow_nan=False)
    assert first == again == json.dumps(describe(np.array(SAMPLES[name])), allow_nan=False)


# A measurement table's columns and rows, and its summary as tables._stats gave it before D7 (captured from
# baseline dd0a85e82): text, flag, bool-in-a-number-column and empty values are not numbers, one value still has a line.
COLUMNS = [{"key": "cell", "type": "text"}, {"key": "areaPx", "type": "number"}, {"key": "circularity", "type": "number"},
           {"key": "edgeCut", "type": "bool"}, {"key": "one", "type": "number"}, {"key": "none", "type": "number"}]
ROWS = [{"cell": "c1", "areaPx": 412.5, "circularity": 0.91, "edgeCut": False, "one": 3, "none": None},
        {"cell": "c2", "areaPx": 388, "circularity": 0.84, "edgeCut": True, "none": "x"},
        {"cell": "c3", "areaPx": 520.25, "circularity": 0.77, "edgeCut": False, "none": True},
        {"cell": "c4", "areaPx": 301.75, "circularity": True, "edgeCut": False},
        {"cell": "c5", "areaPx": 455, "circularity": 0.88, "edgeCut": False}]
BEFORE = ('[{"key": "areaPx", "n": 5, "median": 412.5, "q1": 388.0, "q3": 455.0, "min": 301.75, "max": 520.25}, '
          '{"key": "circularity", "n": 4, "median": 0.86, "q1": 0.8225, "q3": 0.8875, "min": 0.77, "max": 0.91}, '
          '{"key": "one", "n": 1, "median": 3.0, "q1": 3.0, "q3": 3.0, "min": 3.0, "max": 3.0}]')
EARLIER = ("key", "n", "median", "q1", "q3", "min", "max")


def test_a_tables_earlier_summary_keys_are_unchanged_byte_for_byte():
    stats = _stats(ROWS, COLUMNS)
    assert json.dumps([{k: s[k] for k in EARLIER} for s in stats]) == BEFORE
    assert all(list(s)[:len(EARLIER)] == list(EARLIER) for s in stats)  # the new keys come after, in order


def test_a_tables_summary_adds_the_mean_sd_sem_and_interval():
    stats = {s["key"]: s for s in _stats(ROWS, COLUMNS)}
    assert all(list(s)[len(EARLIER):] == ["mean", "sd", "sem", "ci95"] for s in stats.values())
    area = describe([412.5, 388, 520.25, 301.75, 455])
    assert {k: stats["areaPx"][k] for k in ("mean", "sd", "sem", "ci95")} == {k: area[k] for k in ("mean", "sd", "sem", "ci95")}
    assert stats["circularity"]["mean"] == pytest.approx(np.mean([0.91, 0.84, 0.77, 0.88]), **TOL)
    assert (stats["one"]["mean"], stats["one"]["sd"], stats["one"]["sem"], stats["one"]["ci95"]) == (3.0, None, None, None)
    assert json.dumps(_stats(ROWS, COLUMNS), allow_nan=False) == json.dumps(_stats(ROWS, COLUMNS), allow_nan=False)


def test_table_summary_counts_the_same_finite_values_as_its_interval():
    rows = [{'x': value} for value in (1.0, float('nan'), 3.0, float('inf'), True, None)]
    got = _stats(rows, [{'key': 'x', 'type': 'number'}])
    assert got == [{'key': 'x', **{key: describe([1.0, 3.0])[key] for key in
                   ('n', 'median', 'q1', 'q3', 'min', 'max', 'mean', 'sd', 'sem', 'ci95')}}]
    json.dumps(got, allow_nan=False)
    assert _stats([{'x': float('nan')}], [{'key': 'x', 'type': 'number'}]) == []
