"""Objects of 1-4 pixels still get a valid outline (L13's repro in #359: 3 of 323 blobs came back with no polygon, and the
masks panel refused the whole overlay). Their measurements come from their pixels, unchanged."""
import cv2
import numpy as np
import pytest

from gpu_worker.worker import mask_to_polygons
from orchestrator.evidence import Evidence, encode_labels, polygons_of
from orchestrator.measure import Mask
from orchestrator.population import members_from
from orchestrator.scale import filled_area

TINY = {1: [(2, 2)], 2: [(5, 2), (6, 2)], 3: [(10, 2), (11, 2), (10, 3)], 4: [(14, 2), (15, 2), (14, 3), (15, 3)],
        6: [(2, 17), (4, 17)]}  # 6: two lone pixels, one label (a finder's speckle)


def labels_with_tiny_objects():
    labels = np.zeros((24, 24), np.int32)
    for label, pixels in TINY.items():
        for x, y in pixels:
            labels[y, x] = label
    labels[10:16, 2:8] = 5  # an ordinary object, outlined as before
    return labels


def covers(polygon, pixels):
    shape = np.array(polygon, np.float32).reshape(-1, 1, 2)
    return all(cv2.pointPolygonTest(shape, (float(x), float(y)), False) >= 0 for x, y in pixels)


def test_every_object_of_a_label_map_has_a_valid_outline_over_its_own_pixels():
    members = members_from({'labels': encode_labels(labels_with_tiny_objects())}, Evidence(None, None))
    by_label = {m.instance: m for m in members.values()}
    assert sorted(by_label) == [1, 2, 3, 4, 5, 6]  # every object kept, with its identity
    for label, member in by_label.items():
        assert member.polygons, f'object {label} has no outline'
        for polygon in member.polygons:
            assert len(polygon) >= 3 and all(np.isfinite(v) for point in polygon for v in point)
        assert filled_area(member.polygons) > 0
        assert int(np.asarray(member.mask.bits).sum()) == (36 if label == 5 else len(TINY[label]))  # its pixels, as given
    for label in (1, 2, 3, 4):
        polygon, = by_label[label].polygons
        assert covers(polygon, TINY[label])
        xs, ys = zip(*TINY[label])
        points = np.array(polygon)
        assert points[:, 0].min() >= min(xs) - .5 and points[:, 0].max() <= max(xs) + .5  # no larger than its pixels
        assert points[:, 1].min() >= min(ys) - .5 and points[:, 1].max() <= max(ys) + .5
    assert covers(by_label[6].polygons[0], TINY[6][:1])  # its largest piece (a tie: the first) is outlined


def test_an_ordinary_object_keeps_exactly_the_outline_it_had():
    labels = labels_with_tiny_objects()
    members = members_from({'labels': encode_labels(labels)}, Evidence(None, None))
    big = next(m for m in members.values() if m.instance == 5)
    mask = big.mask
    assert big.polygons == mask_to_polygons(np.asarray(mask.bits, bool), offset=(mask.x0, mask.y0), min_area=4.0,
                                            scale=float(mask.scale))


@pytest.mark.parametrize('scale, origin', [(1.0, (0, 0)), (2.0, (40, 30))])
def test_a_tiny_outline_is_placed_and_scaled_as_any_outline_is(scale, origin):
    bits = np.zeros((3, 3), bool)
    bits[1, 1] = True
    polygon, = polygons_of(Mask(bits, origin[0], origin[1], scale))
    points = np.array(polygon)
    centre = (origin[0] + 1 * scale, origin[1] + 1 * scale)
    assert np.allclose(points.mean(axis=0), centre)
    assert np.isclose(filled_area([polygon]), scale * scale)  # one pixel's area, in the outline's px


def test_an_empty_mask_still_has_no_outline():
    assert polygons_of(Mask(np.zeros((3, 3), bool), 0, 0)) == []
