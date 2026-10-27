"""Synthetic evidence-admission checks; not actual browser or napari qualification."""
from bench import scientific_acceptance as acceptance, scientific_checks_h06b as h06b


def test_border_colormap_children_require_their_explicit_browser_and_native_oracles():
    guards = (h06b.BROWSER + 'test_browser_points_border_colormap_and_limits_match_independent_values',
              h06b.PARITY + 'test_actual_points_border_colormap_and_limits_match_independent_values')
    for suffix in ('points-border-colormap', 'points-border-contrast-limits'):
        child = {'id': 'P3.points.' + suffix, 'evidence_class': 'isolated_profile_browser',
                 'procedure': 'synthetic admission', 'oracle': 'all required nodes', 'criteria': 'none omitted'}
        check = h06b.checks()[child['id']]
        assert set(guards) <= set(check['tests'])
        cases = [{'node': node, 'outcome': 'passed'} for node in check['tests']]
        results = {'cases': cases, 'runtime': {name: '/synthetic' for name in check['requires']}, 'junit': '/synthetic.xml'}
        assert acceptance.child_status(child, results, {child['id']: check})['status'] == 'pass'
        for guard in guards:
            for outcome in (None, 'failed', 'skipped'):
                changed = [row for row in cases if row['node'] != guard]
                if outcome:
                    changed.append({'node': guard, 'outcome': outcome})
                assert acceptance.child_status(child, {**results, 'cases': changed}, {child['id']: check})['status'] != 'pass'


def test_every_style_child_requires_its_own_constant_feature_runtime_case():
    for family, children in h06b.CHILDREN.items():
        guard = h06b.PARITY + f'test_constant_native_colormap_renders_and_inspects_without_losing_limits[{family}]'
        for suffix in children:
            check = h06b.checks()[f'P3.{family}.{suffix}']
            assert guard in check['tests']
