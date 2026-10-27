"""Synthetic comparison invariants, not upstream runtime or scientific validation."""
import copy

import numpy as np
import pytest

from orchestrator import reference_checks as checks


ZERO = {"atol": 0, "rtol": 0}


def compare(actual, expected, tolerance=None, output_type="image.2d.rgb8"):
    return checks.compare(actual, expected, ZERO if tolerance is None else tolerance, output_type=output_type)


def picture(array):
    return {"picture": array, "shape": list(array.shape)}


def table():
    return {"columns": [{"name": "object"}, {"name": "frame"}, {"name": "area", "unit": "um2"},
                        {"name": "class"}], "rows": [[1, 0, 10.0, "cell"], [1, 1, 20.0, "cell"]]}


def table_tolerance():
    return {"atol": 0.1, "rtol": 0.001, "numeric_columns": ["area"], "identity_columns": ["frame", "object"]}


def test_array_numeric_tolerance_and_metrics():
    reference = picture(np.array([1.0, 2.0, 3.0], dtype=np.float32))
    actual = picture(np.array([1.05, 2.0, 3.0], dtype=np.float32))
    assert not compare(actual, reference)["ok"]
    result = compare(actual, reference, {"atol": 0.1, "rtol": 0})
    assert result["ok"] and result["metrics"]["elements"] == 3
    assert result["metrics"]["max_absolute_error"] == pytest.approx(0.05)
    assert np.array_equal(reference["picture"], np.array([1.0, 2.0, 3.0], np.float32))


@pytest.mark.parametrize("changed", [np.ones((2, 3), np.uint8), np.ones(6, np.int8)])
def test_array_shape_and_dtype_are_exact(changed):
    assert not compare(picture(changed), picture(np.ones(6, np.uint8)))["ok"]


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_arrays_never_match(value):
    p = picture(np.array([value]))
    assert not compare(p, p)["ok"]


@pytest.mark.parametrize("tolerance", [{}, {"atol": 0}, {"atol": -1, "rtol": 0},
                                       {"atol": True, "rtol": 0}, {"atol": "1", "rtol": 0},
                                       {"atol": float("nan"), "rtol": 0},
                                       {"atol": 0, "rtol": float("inf")},
                                       {"atol": 0, "rtol": 0, "equal_nan": True}])
def test_tolerance_is_explicit_finite_and_bounded(tolerance):
    p = picture(np.ones(1))
    assert not compare(p, p, tolerance)["ok"]


def test_overflow_in_bound_cannot_pass():
    p = picture(np.array([1e308]))
    assert not compare(p, p, {"atol": 0, "rtol": 1e308})["ok"]


def test_large_integers_do_not_round_equal():
    a = picture(np.array([2**64 - 1], dtype=np.uint64))
    b = picture(np.array([2**64 - 2], dtype=np.uint64))
    result = compare(a, b)
    assert not result["ok"] and result["metrics"]["max_absolute_error"] == 1
    assert compare(a, b, {"atol": 1, "rtol": 0})["ok"]


def test_noncontiguous_arrays_compare_in_bounded_chunks(monkeypatch):
    monkeypatch.setattr(checks, "_CHUNK", 3)
    a = np.arange(40, dtype=np.uint8).reshape(5, 8).T
    assert compare(picture(a), picture(a.copy()))["ok"]


def test_array_size_and_metadata_bounds(monkeypatch):
    monkeypatch.setattr(checks, "MAX_ELEMENTS", 5)
    p = picture(np.ones(6))
    assert not compare(p, p)["ok"]
    p = {"picture": np.ones(1), "shape": [True]}
    assert not compare(p, p)["ok"]


def test_no_array_subclass_or_object_code_runs():
    class Malicious(np.ndarray):
        def __array_ufunc__(self, *args, **kwargs):
            raise AssertionError("subclass must not execute")
    p = {"picture": np.ones(1).view(Malicious)}
    assert not compare(p, p)["ok"]
    p = picture(np.array([object()], dtype=object))
    assert not compare(p, p)["ok"]


def test_table_numeric_tolerance_preserves_schema_and_row_identity():
    expected = table()
    actual = copy.deepcopy(expected)
    actual["rows"][0][2] += 0.05
    result = compare(actual, expected, table_tolerance(), "table.objects")
    assert result["ok"] and result["metrics"]["rows"] == 2
    actual["rows"][0][2] += 1
    assert not compare(actual, expected, table_tolerance(), "table.objects")["ok"]


@pytest.mark.parametrize("mutation", ["row_order", "id", "id_type", "schema", "column_order", "missing",
                                      "duplicate_identity", "class", "null_numeric", "nan_numeric"])
def test_table_mismatches_fail(mutation):
    expected = table()
    actual = copy.deepcopy(expected)
    if mutation == "row_order":
        actual["rows"].reverse()
    elif mutation == "id":
        actual["rows"][0][0] = 2
    elif mutation == "id_type":
        actual["rows"][0][0] = True
    elif mutation == "schema":
        actual["columns"][2]["unit"] = "px2"
    elif mutation == "column_order":
        actual["columns"].reverse()
    elif mutation == "missing":
        actual.pop("rows")
    elif mutation == "duplicate_identity":
        expected["rows"][1][:2] = expected["rows"][0][:2]
        actual = copy.deepcopy(expected)
    elif mutation == "class":
        actual["rows"][0][3] = "artifact"
    elif mutation == "null_numeric":
        actual["rows"][0][2] = None
    elif mutation == "nan_numeric":
        actual["rows"][0][2] = float("nan")
    assert not compare(actual, expected, table_tolerance(), "table.objects")["ok"]


@pytest.mark.parametrize("field,value", [("identity_columns", []), ("identity_columns", ["absent"]),
                                         ("identity_columns", ["area"]), ("numeric_columns", ["area", "area"]),
                                         ("numeric_columns", "area")])
def test_table_roles_are_declared_and_unambiguous(field, value):
    tolerance = table_tolerance()
    tolerance[field] = value
    assert not compare(table(), table(), tolerance, "table.objects")["ok"]


def test_empty_table_can_match_but_does_not_imply_operation_coverage():
    expected = table()
    expected["rows"] = []
    result = compare(expected, expected, table_tolerance(), "table.objects")
    assert result["ok"] and result["metrics"]["rows"] == 0


def test_numeric_column_does_not_round_large_integer_to_float():
    expected = table()
    actual = table()
    expected["rows"][0][2] = 2**63 + 1
    actual["rows"][0][2] = float(2**63)
    assert not compare(actual, expected, table_tolerance(), "table.objects")["ok"]


def test_points_preserve_order_and_count():
    expected = {"points": [[1.0, 2.0], [3.0, 4.0]]}
    actual = {"points": [[1.01, 2.0], [3.0, 4.0]]}
    assert compare(actual, expected, {"atol": 0.02, "rtol": 0}, "points.2d")["ok"]
    actual["points"].reverse()
    assert not compare(actual, expected, ZERO, "points.2d")["ok"]
    assert not compare({"points": [[1, 2]]}, expected, ZERO, "points.2d")["ok"]


@pytest.mark.parametrize("a,b", [({"text": "cells"}, {"text": "Cells"}),
                                  ({"text": 1}, {"text": 1}),
                                  ({}, {}),
                                  ({"text": "x", "extra": 1}, {"text": "x"})])
def test_text_requires_complete_exact_strings(a, b):
    assert not compare(a, b, {}, "text")["ok"]


def test_exact_text():
    assert compare({"text": "µm\n1"}, {"text": "µm\n1"}, {}, "text")["ok"]
    assert not compare({"text": "x"}, {"text": "x"}, ZERO, "text")["ok"]


@pytest.mark.parametrize("a,b", [(True, 1), (1, 1.0), ({"a": 1}, {"a": 1, "b": None}),
                                  ([1, 2], [2, 1]), ({"parent": 1}, {"parent": 2})])
def test_structured_data_exact_types_and_relationships(a, b):
    assert not compare({"data": a}, {"data": b}, {}, "embeddings.features")["ok"]


def test_tracking_division_edges_are_not_discarded():
    expected = {"tracks": [{"id": 1, "children": [2, 3], "points": [[0, 1, 2]]}]}
    assert compare(expected, expected, {}, "tracks.graph")["ok"]
    actual = copy.deepcopy(expected)
    actual["tracks"][0]["children"].pop()
    assert not compare(actual, expected, {}, "tracks.graph")["ok"]


def test_json_bounds_cycles_and_nonjson_are_refused(monkeypatch):
    cyclic = []
    cyclic.append(cyclic)
    for value in (cyclic, object(), {1: "x"}, float("nan"), 2**100):
        result = compare({"data": value}, {"data": value}, {}, "custom.data")
        assert not result["ok"] and result["metrics"] == {}
    monkeypatch.setattr(checks, "MAX_JSON_BYTES", 30)
    value = {"data": "a" * 31}
    assert not compare(value, value, {}, "custom.data")["ok"]


def test_json_depth_and_nodes_are_bounded(monkeypatch):
    value = {"data": [[[[]]]]}
    monkeypatch.setattr(checks, "MAX_DEPTH", 2)
    assert not compare(value, value, {}, "custom.data")["ok"]
    monkeypatch.setattr(checks, "MAX_DEPTH", 32)
    monkeypatch.setattr(checks, "MAX_NODES", 3)
    value = {"data": [1, 2, 3]}
    assert not compare(value, value, {}, "custom.data")["ok"]


def test_data_dict_subclass_never_executes():
    class Malicious(dict):
        def items(self):
            raise AssertionError("must not execute")
    assert not compare({"data": Malicious()}, {"data": {}}, {}, "custom.data")["ok"]
