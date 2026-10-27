"""Outlines that can be seen and shapes that can be shown (the outline-fit review of 2026-10-11, docs/bench/outline-fit.md):
a dark casing under every line drawn into a picture (orchestrator/drawing.py), a ring kept as a ring
(gpu_worker.mask_to_polygons), and areas that take a ring's hole off (scale.filled_area)."""
import cv2
import numpy as np
import pytest

from gpu_worker.worker import mask_to_polygons
from orchestrator.comparing import polygon_area
from orchestrator.drawing import CASING_BGR, PALETTE, contrast_colour, outline, points
from orchestrator.evidence import Mask, polygons_of
from orchestrator.scale import area_px, filled_area


def ring_mask(outer=40, inner=20):
    m = np.zeros((100, 100), np.uint8)
    cv2.circle(m, (50, 50), outer, 1, -1)
    cv2.circle(m, (50, 50), inner, 0, -1)
    return m


def test_a_ring_is_its_two_edges_and_its_area_leaves_the_hole_out():
    m = ring_mask()
    polygons = mask_to_polygons(m)
    assert len(polygons) == 2  # the outer edge, then the hole's
    outer, hole = (abs(cv2.contourArea(np.asarray(p, np.float32))) for p in polygons)
    assert abs(filled_area(polygons) - (outer - hole)) < 1e-6 and hole > 0.2 * outer
    assert area_px(polygons) == filled_area(polygons) and abs(polygon_area(polygons) - filled_area(polygons)) < 1e-6
    drawn = np.zeros_like(m)
    cv2.fillPoly(drawn, [np.asarray(p, np.int32) for p in polygons], 1)  # as every mask is rasterised again
    assert drawn[50, 50] == 0 and drawn[50, 15] == 1


def test_small_gaps_stay_filled_as_before():
    # On the outline-fit bench's 108 masks from boxes the largest gap was 1.6% of its mask: a stoma's pore, a speck.
    m = ring_mask(40, 4)  # a hole of 1% of the disc
    assert len(mask_to_polygons(m)) == 1
    two = np.zeros((100, 100), np.uint8)
    cv2.circle(two, (25, 50), 15, 1, -1)
    cv2.circle(two, (75, 50), 10, 1, -1)
    pieces = mask_to_polygons(two)
    assert len(pieces) == 2 and filled_area(pieces) > filled_area(pieces[:1])  # two pieces add up, largest first


@pytest.mark.parametrize('scale', [0.25, 2.0])
def test_evidence_polygons_keep_capture_coordinates_and_hole_order(scale):
    bits = ring_mask().astype(bool)
    origin = (-13, 29)
    expected = [[[x * scale + origin[0], y * scale + origin[1]] for x, y in polygon]
                for polygon in mask_to_polygons(bits, min_area=4)]
    assert len(expected) == 2
    assert polygons_of(Mask(bits, *origin, scale)) == expected


def test_every_outline_is_drawn_on_a_dark_casing_and_the_contrast_colour_stands_out_from_the_stain():
    magenta_stain = np.full((80, 80, 3), (150, 40, 200), np.uint8)  # BGR: a magenta-stained section
    square = [[[20, 20], [60, 20], [60, 60], [20, 60]]]
    drawn = magenta_stain.copy()
    outline(drawn, points(square), PALETTE["magenta"], 3)
    assert tuple(drawn[20, 40]) == tuple(PALETTE["magenta"])  # the line itself, its colour as the prompts say
    column = [tuple(int(v) for v in drawn[y, 40]) for y in range(12, 29)]
    line = [i for i, c in enumerate(column) if c == tuple(PALETTE["magenta"])]
    assert CASING_BGR in column[:line[0]] and CASING_BGR in column[line[-1]:]  # its casing either side, on the stain
    name, bgr = contrast_colour(magenta_stain, square)
    assert name != "magenta" and bgr == PALETTE[name]
    pale = np.full((80, 80, 3), 235, np.uint8)
    assert contrast_colour(pale, square)[0] != "white"
