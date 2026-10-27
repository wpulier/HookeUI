"""Synthetic mask/image controls for the actual simulator's display-template boundary.

Mask -> Member -> Population measurement/wire is production code. Pixels and
instance masks are external synthetic inputs; no segmentation model, camera,
registration, browser or placement success is simulated here. The original
8038 cells-then-move empty-array crash remains the measured scenario RED.
"""
from collections import OrderedDict
import copy

import numpy as np
import pytest

from orchestrator.evidence import Member, Population, polygons_of
from orchestrator.measure import Mask
from sim import harness


def display_templates(gray, members):
    helper = getattr(harness, '_population_display_templates', None)
    assert callable(helper), 'MISSING_FEATURE: simulator display templates need the actual empty-geometry boundary'
    return helper(gray, members)


def population(*, companion):
    gray = (np.arange(96 * 96, dtype=np.uint16).reshape(96, 96) % 256).astype(np.uint8)
    pieces = np.zeros((20, 20), bool)
    pieces[2, 2] = pieces[8, 12] = pieces[16, 6] = True
    fragmented = Mask(pieces, 10, 10)
    members = OrderedDict()
    # A nonzero mask with no display contour: as a population saved before tiny objects were outlined (#382) has.
    # polygons_of now outlines such a speck itself, so the empty contour is given here.
    members['c1'] = Member('c1', 1, fragmented, [], fragmented.box)
    assert fragmented.bits.sum() == 3 and polygons_of(fragmented), 'SETUP: actual nonzero mask, saved without a contour'
    if companion:
        solid = np.zeros((20, 20), bool)
        solid[2:18, 2:18] = True
        useful = Mask(solid, 30, 30)
        members['c2'] = Member('c2', 2, useful, polygons_of(useful), useful.box)
        assert members['c2'].polygons, 'SETUP: useful actual contour companion'
    pop = Population('e1', 'cells', 'synthetic-external-masks', {'operation': 'cells'},
                     7, {'epoch': 0, 'dx': 0, 'dy': 0, 'disc': None, 'width': 96, 'height': 96, 'at': 1000},
                     None, None, members)
    pop.measure_all(gray, 'synthetic-mask-measurement')
    return gray, pop


def test_empty_display_member_keeps_actual_mask_measurement_and_count_with_useful_companion():
    gray, pop = population(companion=True)
    before = copy.deepcopy(pop.wire())
    assert before['count'] == len(before['members']) == 2
    by_id = {row['id']: row for row in before['members']}
    assert by_id['c1']['polygons'] == [] and by_id['c1']['measure']['areaPx'] == 3
    assert by_id['c2']['measure']['areaPx'] == 256
    assert '2 instances segmented' in pop.count_words()
    rows = {row['id']: row for row in before['members']}
    templates = display_templates(gray, rows)
    assert set(templates) == {'c2'}, 'only a drawable contour contributes a display template'
    template, origin = templates['c2']
    # Independent known rectangle: mask pixels32..47 plus the original8px
    # padding give [24:55] in both axes. No replacement box is used for c1.
    assert origin == (24, 24)
    np.testing.assert_array_equal(template, gray[24:55, 24:55])
    assert not np.shares_memory(template, gray)
    assert pop.wire() == before and rows == {row['id']: row for row in before['members']}
    assert pop.live()[0].mask is not None and pop.live()[0].mask.bits.sum() == 3


def test_all_empty_display_geometry_returns_no_templates_without_erasing_count():
    gray, pop = population(companion=False)
    before = copy.deepcopy(pop.wire())
    assert before['count'] == 1 and before['members'][0]['measure']['areaPx'] == 3
    assert display_templates(gray, {row['id']: row for row in before['members']}) == {}
    assert pop.wire() == before and len(pop.live()) == 1
    assert '1 instances segmented' in pop.count_words()


def test_malformed_nonempty_display_geometry_still_fails():
    gray, pop = population(companion=True)
    original = copy.deepcopy(pop.wire())
    rows = {row['id']: copy.deepcopy(row) for row in original['members']}
    # Deliberately invalid external geometry is not the valid empty-polygon arm.
    rows['c2']['polygons'] = [[['not-a-coordinate', 32], [47, 32], [47, 47]]]
    helper = getattr(harness, '_population_display_templates', None)
    assert callable(helper), 'MISSING_FEATURE: simulator display templates need the actual empty-geometry boundary'
    with pytest.raises((TypeError, ValueError)):
        helper(gray, rows)
    assert pop.wire() == original and len(pop.live()) == 2
