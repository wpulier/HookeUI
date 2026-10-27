"""Synthetic resource-admission cases; upstream editing is verified separately."""
from collections import deque
from types import SimpleNamespace
import numpy as np
import pytest
from env_worker.scientific_label_history import retained_bytes, stroke_calls, paint_bound


def test_history_counts_retained_bases_not_just_visible_views_and_deduplicates():
    base = np.zeros(10000, dtype='u2')
    small = base[:1]
    once = retained_bytes([small])
    assert once >= base.nbytes and once < base.nbytes + 2000
    assert retained_bytes([small, small]) < once + 100
    assert retained_bytes(deque([[small], [base]])) < once + 1000


@pytest.mark.parametrize('bad', [np.array([object()], dtype=object), np.frombuffer(bytearray(20), dtype='u1'), object(), {'hidden': 1}])
def test_unknown_history_owners_are_refused(bad):
    with pytest.raises(ValueError): retained_bytes([bad])


def test_history_object_and_byte_limits_are_independent():
    with pytest.raises(ValueError, match='memory'): retained_bytes([np.ones(1000)], maximum=100)
    with pytest.raises(ValueError, match='objects'): retained_bytes([[i] for i in range(30)], objects=20)


def test_stroke_work_is_bounded_before_upstream_interpolation_allocates():
    assert stroke_calls([[0,0],[0,1],[0,4]], 4) == 5
    assert stroke_calls([[0,0]], 1) == 1
    with pytest.raises(ValueError, match='shorter'): stroke_calls([[0,0],[1e12,0]],1)
    with pytest.raises(ValueError): stroke_calls([[0,float('nan')]],1)


def test_paint_history_bound_respects_native_anisotropic_brush_extent():
    layer=SimpleNamespace(scale=(8,2,1),data=np.zeros((4,100,120),dtype='u2'),_slice_input=SimpleNamespace(order=(0,1,2)))
    assert paint_bound(layer,3,1) < paint_bound(layer,9,1)
    assert paint_bound(layer,3,10) == 10*paint_bound(layer,3,1)
    layer.scale=(8,0,1)
    with pytest.raises(ValueError): paint_bound(layer,3,1)


def test_volume_brush_admission_counts_hidden_axes_and_current_order():
    layer=SimpleNamespace(scale=(1,1,1,1),data=np.zeros((5,7,11,13),dtype='u2'),
                          _slice_input=SimpleNamespace(order=(1,0,2,3)))
    planar=paint_bound(layer,9,1)
    volume=paint_bound(layer,9,1,3)
    from env_worker.scientific_label_history import ATOM_ALLOWANCE
    assert volume-ATOM_ALLOWANCE == 5*(planar-ATOM_ALLOWANCE)
    assert paint_bound(layer,9,1,4)-ATOM_ALLOWANCE == 7*(volume-ATOM_ALLOWANCE)
