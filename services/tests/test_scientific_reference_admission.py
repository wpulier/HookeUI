"""Synthetic refusal regressions, not native-worker or browser qualification."""
import hashlib
import json
from types import SimpleNamespace
import zipfile

import pytest

from bench import scientific_inventory as inventory

from tests import scientific_napari_reference as reference


def test_missing_reference_archive_cannot_certify_loaded_sources(tmp_path, monkeypatch):
    monkeypatch.setenv('NAPARI_ARCHIVE', str(tmp_path / 'missing.zip'))
    with pytest.raises(ValueError, match='archive'):
        reference.pinned_source_check({'loaded_napari_files': {'components/dims.py': '0' * 64}})


@pytest.mark.parametrize('failure', ['archive', 'empty', 'changed', 'absent'])
def test_reference_requires_approved_archive_and_observed_sources(tmp_path, monkeypatch, failure):
    archive = tmp_path / 'reference.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('napari-main/src/napari/components/dims.py', 'approved source')
    monkeypatch.setenv('NAPARI_ARCHIVE', str(archive))
    # A small, explicitly synthetic approved artifact exercises admission without a native runtime.
    monkeypatch.setattr(inventory, 'ARCHIVE_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest())
    loaded = {'components/dims.py': hashlib.sha256(b'approved source').hexdigest()}
    if failure == 'archive': archive.write_bytes(b'unapproved archive')
    elif failure == 'empty': loaded = {}
    elif failure == 'changed': loaded['components/dims.py'] = '0' * 64
    elif failure == 'absent': loaded['unexpected.py'] = '0' * 64
    with pytest.raises(ValueError):
        reference.pinned_source_check({'loaded_napari_files': loaded})


def test_run_refuses_unverified_reference_before_returning_observations(tmp_path, monkeypatch):
    monkeypatch.setenv('NAPARI_ARCHIVE', str(tmp_path / 'missing.zip'))
    monkeypatch.setattr(reference.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps({'loaded_napari_files': {}, 'results': [{'state': 'plausible'}]})))
    with pytest.raises(ValueError):
        reference.run('/synthetic/python', [], [])


@pytest.mark.parametrize('failure', [None, 'wheel', 'generated', 'unexpected'])
def test_generated_version_requires_the_exact_approved_wheel(tmp_path, monkeypatch, failure):
    from env_worker import napari_cgl
    archive, wheel = tmp_path / 'source.zip', tmp_path / 'napari.whl'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('napari-main/src/napari/components/dims.py', 'approved source')
    with zipfile.ZipFile(wheel, 'w') as output:
        output.writestr('napari/_version.py', 'generated version')
        output.writestr('napari/unexpected.py', 'unexpected')
    monkeypatch.setattr(inventory, 'ARCHIVE_SHA256', hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(napari_cgl, 'REFERENCE_WHEEL', hashlib.sha256(wheel.read_bytes()).hexdigest())
    monkeypatch.setenv('NAPARI_ARCHIVE', str(archive))
    report = {'loaded_napari_files': {
        'components/dims.py': hashlib.sha256(b'approved source').hexdigest(),
        '_version.py': hashlib.sha256(b'generated version').hexdigest()},
        'napari_origin': {'url': wheel.as_uri(), 'archive_info': {}}}
    if failure == 'wheel': wheel.write_bytes(b'wrong wheel')
    elif failure == 'generated': report['loaded_napari_files']['_version.py'] = '0' * 64
    elif failure == 'unexpected': report['loaded_napari_files']['unexpected.py'] = hashlib.sha256(b'unexpected').hexdigest()
    if failure:
        with pytest.raises(ValueError):
            reference.pinned_source_check(report)
    else:
        admitted = reference.pinned_source_check(report)
        assert admitted['same'] == 2 and admitted['absent'] == []


def test_every_h04_comparison_requires_reference_admission_and_negative_control():
    from bench import scientific_checks_h04 as checks
    for check in checks.checks().values():
        assert checks.BROWSER + checks.OPEN in check['tests']
        assert checks.BROWSER + checks.SELF_CHECK in check['tests']
        assert {'NAPARI_ARCHIVE', 'NAPARI_REFERENCE_WHEEL'} <= set(check['requires'])


@pytest.mark.parametrize('omitted', [None, 'once-forward', 'once-restart', 'once-backward', 'loop-forward',
                                    'bounce-forward', 'loop-backward', 'loop-slow', 'once-slow', 'once-restart-slow',
                                    'defaults', 'invalid-range'])
def test_playback_requires_every_bounded_case_before_acceptance(omitted):
    from bench import scientific_acceptance as acceptance, scientific_checks_h04 as checks
    prefix = checks.BROWSER
    cases = {
        name: prefix + f'test_playback_sequence_and_draws_match_napari[{name}]'
        for name in ('once-forward', 'once-restart', 'once-backward', 'loop-forward', 'bounce-forward',
                     'loop-backward', 'loop-slow', 'once-slow', 'once-restart-slow')}
    cases['defaults'] = prefix + 'test_playback_defaults_match_napari'
    cases['invalid-range'] = prefix + 'test_playback_refuses_invalid_ranges_and_excludes_displayed_axes'
    child = {'id': 'P1.playback.qtdims-play', 'evidence_class': 'isolated_profile_browser',
             'procedure': ['Play'], 'oracle': 'pinned native playback', 'criteria': ['all declared cases']}
    results = {'junit': 'synthetic.xml', 'runtime': dict.fromkeys(checks.REQUIRES, 'synthetic'),
               'cases': [{'node': prefix + name, 'outcome': 'passed'} for name in (checks.OPEN, checks.SELF_CHECK)] +
                        [{'node': node, 'outcome': 'passed'} for name, node in cases.items() if name != omitted]}
    result = acceptance.child_status(child, results, checks.checks())
    assert result['status'] == ('pass' if omitted is None else 'fail'), result
    if omitted is not None:
        assert cases[omitted] in result['reason']


@pytest.mark.parametrize('family', ['plane', 'volume', 'projection'])
def test_h04_split_journeys_require_every_named_parameter(family):
    from itertools import combinations
    from bench import scientific_acceptance as acceptance, scientific_checks_h04 as checks
    groups = {
        'plane': ('P1.calibrated-coordinates.layer-get-value', [
            f'test_probe_known_pixels_and_calibrated_coordinates_on_every_displayed_plane[{a}-{b}]'
            for a in range(5) for b in range(5) if a != b]),
        'volume': ('P1.display-mode.dims-ndisplay', [
            'test_switch_two_and_three_displayed_dimensions_and_back', *[
                f'test_each_three_dimensional_axis_combination[{a}-{b}-{c}]'
                for a, b, c in combinations(range(5), 3)]]),
        'projection': ('P1.slab-projection.imageprojectionmode-mean', [
            f'test_slab_margins_and_every_projection_mode_show_reduced_values[{mode}]'
            for mode in ('none', 'sum', 'mean', 'max', 'min')]),
    }
    ident, names = groups[family]
    child = {'id': ident, 'evidence_class': 'isolated_profile_browser',
             'procedure': ['Exercise every declared case'], 'oracle': 'pinned native and independent fixture values',
             'criteria': ['all declared cases']}
    nodes = [checks.BROWSER + name for name in (*names, checks.OPEN, checks.SELF_CHECK)]
    results = {'junit': 'synthetic.xml', 'runtime': dict.fromkeys(checks.REQUIRES, 'synthetic'),
               'cases': [{'node': node, 'outcome': 'passed'} for node in nodes]}
    assert acceptance.child_status(child, results, checks.checks())['status'] == 'pass'
    for omitted in nodes[:len(names)]:
        partial = {**results, 'cases': [case for case in results['cases'] if case['node'] != omitted]}
        result = acceptance.child_status(child, partial, checks.checks())
        assert result['status'] == 'fail' and omitted in result['reason'], (omitted, result)
