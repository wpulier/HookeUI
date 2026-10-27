"""Contract 8's measurements (orchestrator/measure.py): from each member's own mask, by app code,
checked against shapes of known size."""
import math

import cv2
import numpy as np
import pytest

from orchestrator.evidence import Population, masks_from_labels
from orchestrator.measure import Mask, coverage, kulpa_perimeter, measure


def circle(r: int, pad: int = 4) -> Mask:
    size = 2 * r + 2 * pad + 1
    bits = np.zeros((size, size), np.uint8)
    cv2.circle(bits, (size // 2, size // 2), r, 1, -1)
    return Mask(bits.astype(bool), 100, 200)


@pytest.mark.parametrize("r, tolerance", [(8, 0.1), (20, 0.05), (60, 0.05)])
def test_a_circle_is_round_and_its_area_and_perimeter_are_right(r, tolerance):
    m = measure(circle(r))
    assert m["circularity"] == pytest.approx(1.0, abs=tolerance)
    assert m["areaPx"] == pytest.approx(math.pi * r * r, rel=0.06)
    assert m["perimeterPx"] == pytest.approx(2 * math.pi * r, rel=0.06)  # Kulpa's chain length, not pixel counting
    assert m["feretMaxPx"] == pytest.approx(2 * r + 1, abs=1.5) and m["elongation"] == pytest.approx(1.0, abs=0.05)
    assert m["centroid"] == pytest.approx([100 + 4 + r + 0.5, 200 + 4 + r + 0.5], abs=0.6)  # capture px


@pytest.mark.parametrize("r", [3, 5, 8, 10, 14, 20])
def test_a_small_disc_is_never_rounder_than_a_circle(r):
    # Live 2026-10-11 ("highlight the round ones"): small cells read 1.12-1.15, since the pixel count reaches half a
    # pixel past the boundary the perimeter runs along. At most 1, as ImageJ caps it.
    assert 0.9 <= measure(circle(r))["circularity"] <= 1.0


def test_roundness_orders_a_circle_above_ovals_at_every_size():
    def ellipse(a, b):
        bits = np.zeros((2 * b + 9, 2 * a + 9), np.uint8)
        cv2.ellipse(bits, (a + 4, b + 4), (a, b), 0, 0, 360, 1, -1)
        return measure(Mask(bits.astype(bool), 0, 0))["circularity"]

    for r in (6, 12, 40):
        assert ellipse(r, r) > ellipse(2 * r, r) > ellipse(3 * r, r)
    assert 0.81 <= ellipse(80, 40) <= 0.88 and 0.64 <= ellipse(90, 30) <= 0.71  # true values 0.84 and 0.66


def test_a_three_to_one_ellipse_is_three_times_longer_than_wide():
    bits = np.zeros((80, 200), np.uint8)
    cv2.ellipse(bits, (100, 40), (90, 30), 0, 0, 360, 1, -1)
    m = measure(Mask(bits.astype(bool), 0, 0))
    assert m["elongation"] == pytest.approx(3.0, abs=0.1)
    assert m["feretMinPx"] == pytest.approx(61, abs=2)


def test_holes_are_not_area_and_pieces_are_counted():
    ring = np.zeros((60, 60), np.uint8)
    cv2.circle(ring, (30, 30), 25, 1, -1)
    cv2.circle(ring, (30, 30), 10, 0, -1)
    m = measure(Mask(ring.astype(bool), 0, 0))
    assert m["holeAreaPx"] == pytest.approx(math.pi * 100, rel=0.1) and m["areaPx"] == ring.sum()
    two = np.zeros((40, 100), bool)
    two[10:30, 10:30] = True
    two[10:30, 60:80] = True
    m = measure(Mask(two, 0, 0))
    assert m["components"] == 2 and m["areaPx"] == 800
    assert m["perimeterPx"] == pytest.approx(kulpa_perimeter(two[:, :50]) * 2, rel=0.01)  # every piece's outer boundary


def test_a_mask_kept_at_half_size_is_measured_in_capture_pixels():
    full = measure(circle(20))
    half_bits = cv2.resize(circle(20).bits.astype(np.uint8), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_NEAREST)
    half = measure(Mask(half_bits.astype(bool), 100, 200, scale=2.0))
    assert half["areaPx"] == pytest.approx(full["areaPx"], rel=0.08)
    assert half["feretMaxPx"] == pytest.approx(full["feretMaxPx"], rel=0.08)


def test_small_things_are_flagged_and_the_view_edge_cuts():
    m = measure(Mask(np.ones((5, 5), bool), 10, 10))
    assert m["small"] is True
    edge = measure(circle(10), disc=(100 + 4 + 10.5, 200 + 4 + 10.5, 10.0))  # the mask reaches past 0.97 of the disc
    assert edge["edgeCut"] is True
    inside = measure(circle(10), disc=(100 + 4 + 10, 200 + 4 + 10, 400.0))
    assert inside["edgeCut"] is False


def test_overlapping_masks_are_measured_unchanged_by_the_display_map():
    """SAM's and the automatic masks can overlap: the derived label map arbitrates for display only;
    each member is measured from its own mask (contract 8)."""
    from collections import OrderedDict

    from orchestrator.evidence import Member, polygons_of

    a = np.zeros((100, 100), bool)
    a[10:60, 10:60] = True
    b = np.zeros((100, 100), bool)
    b[40:90, 40:90] = True
    members = OrderedDict()
    for mid, bits, score in (("c1", a, 0.9), ("c2", b, 0.5)):
        mask = Mask(bits, 0, 0)
        members[mid] = Member(mid, int(mid[1:]), mask, polygons_of(mask), mask.box, {"score": score})
    p = Population("e1", "find-like", "sam3.1-all", {}, 1, {"width": 100, "height": 100, "epoch": 0, "disc": None},
                   None, None, members, overlapping=True)
    p.measure_all(None)
    assert p.value("c1", "areaPx") == 2500 and p.value("c2", "areaPx") == 2500  # each its own, overlap included
    labels = p.labels()
    assert labels[50, 50] == 1  # the higher score wins where they overlap (display only)
    cov = p.columns["measure"]["coverage"]
    assert cov == pytest.approx((2500 + 2500 - 400) / 10000, abs=1e-4)  # the union, overlaps once


def test_label_maps_above_65535_keep_every_label():
    labels = np.zeros((300, 300), np.uint32)
    labels[0, 0], labels[1, 1], labels[299, 299] = 70000, 70001, 3
    got = {label for label, _ in masks_from_labels(labels)}
    assert got == {3, 70000, 70001}


def test_coverage_counts_the_eyepiece_disc_only():
    full = Mask(np.ones((100, 100), bool), 0, 0)
    assert coverage([full], (50, 50, 10), (100, 100)) == pytest.approx(1.0)
