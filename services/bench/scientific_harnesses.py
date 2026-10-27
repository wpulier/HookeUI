"""H00 templated harnesses: children grouped by template, and one shared parameterized check per harness template.

Every atomic child falls under exactly one template. A harness template (rules.json ``harnesses``) gives
its children their procedure, oracle and criteria and one parametrized test case each; the other
templates name what their children still need. A harness child passes only on its own executed case
of its own evidence class, and only where the runtime that case needs was present (``requires``).

    python -m bench.scientific_harnesses [--report reports/scientific-acceptance/<time>/report.json]
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from bench.scientific_inventory import EXTERNAL_CLASSES, INVENTORY

# Trusted code: each harness template's one parameterized test and the runtime it needs. None is admitted yet: a
# harness closes a child only when it compares Hooke's own result with an independent pinned-napari reference
# result (contract §2), and states the Hooke equivalent it exercises (ticket 46).
COMPARISON_GUARDS = tuple('tests/test_scientific_reference_comparison.py::' + name for name in (
    'test_the_session_holds_the_retained_volume', 'test_both_sides_ran_the_same_pinned_napari',
    'test_a_broken_behaviour_in_hookes_worker_is_caught[wrong-effect]',
    'test_a_broken_behaviour_in_hookes_worker_is_caught[silent-emitter]',
    'test_a_broken_behaviour_in_hookes_worker_is_caught[detached-viewer]'))

HARNESS_TESTS = {name: {'test': 'tests/test_scientific_reference_comparison.py::test_compare_child',
                        'class': 'actual_upstream_worker', 'prerequisites': COMPARISON_GUARDS,
                        'requires': ('NAPARI_NATIVE_HOST_PREPARATION', 'NAPARI_REFERENCE_PYTHON')}
                 for name in ('compare-event', 'compare-attribute', 'compare-enum', 'compare-contribution', 'compare-call')}
HARNESS_TESTS['compare-call']['prerequisites'] = COMPARISON_GUARDS + (
    'tests/test_scientific_reference_comparison.py::test_a_broken_behaviour_in_hookes_worker_is_caught[writer-values]',)
HARNESS_TESTS['compare-layerlist'] = {**HARNESS_TESTS['compare-call'], 'prerequisites': COMPARISON_GUARDS + tuple(
    'tests/test_scientific_reference_comparison.py::test_a_broken_behaviour_in_hookes_worker_is_caught[' + name + ']'
    for name in ('layerlist-lookup', 'layerlist-replacement'))}
# The production-path browser: the installed Studio build in a fresh profile, over a real confined worker.
HARNESS_TESTS['browser-p2'] = {'test': 'tests/test_scientific_browser_p2.py::test_p2_child', 'class': 'isolated_profile_browser',
                               'prerequisites': ('tests/test_scientific_browser_p2.py::test_both_p2_sides_ran_approved_runtimes',
                                   'tests/test_scientific_browser_p2.py::test_a_worker_that_drops_attenuation_fails_the_attenuation_child'),
                               'requires': ('NAPARI_NATIVE_HOST_PREPARATION', 'NAPARI_REFERENCE_PYTHON', 'HOOKE_BROWSER_CONFIG')}


def _children(inventory=None):
    data = inventory if inventory is not None else json.loads((INVENTORY / 'atomic-cases.json').read_text())
    return data['children']


def harness_children(inventory=None):
    return [c for c in _children(inventory) if c.get('harness') in HARNESS_TESTS]


def checks(inventory=None):
    """One registration per harness child: its own parametrized case of the template's shared test."""
    out = {}
    for child in harness_children(inventory):
        spec = HARNESS_TESTS[child['harness']]
        if child['evidence_class'] != spec['class']:
            raise ValueError(f'{child["id"]}: harness {child["harness"]} gives {spec["class"]} evidence')
        out[child['id']] = {'tests': (f'{spec["test"]}[{child["id"]}]', *spec.get('prerequisites', ())), 'class': spec['class'],
                            'requires': spec['requires']}
    return out


def template(child, suffixes=None):
    """The template a child falls under: its harness, a Hooke template suffix, or its kind and evidence class."""
    if child.get('harness'):
        return child['harness']
    if child['kind'] == 'hooke':
        suffixes = suffixes if suffixes is not None else _template_suffixes()
        return 'hooke:' + next(s for s in suffixes if child['id'].endswith('.' + s))
    if child['basis'] == 'pinned-source':
        return f'pinned:{child["kind"]}:{child["evidence_class"]}'
    return f'procedure:{child["evidence_class"]}'


def _template_suffixes():
    rules = json.loads((INVENTORY / 'rules.json').read_text())
    return sorted((t['suffix'] for t in rules['templates']), key=len, reverse=True)


def grouping(inventory=None, registered=None, report=None):
    """Per template: children, ready (procedure, oracle and criteria), executable here, external, and passing."""
    from bench.scientific_acceptance import CHECKS
    registered = CHECKS if registered is None else registered
    statuses = (report or {}).get('children', {})
    suffixes = _template_suffixes()
    rows = defaultdict(Counter)
    for child in _children(inventory):
        row = rows[template(child, suffixes)]
        ready = all(child.get(f) for f in ('procedure', 'oracle', 'criteria'))
        check = registered.get(child['id'])
        row['children'] += 1
        row['ready'] += ready
        row['external'] += child['evidence_class'] in EXTERNAL_CLASSES
        row['executable'] += bool(ready and check and check['class'] == child['evidence_class'])
        row['pass'] += statuses.get(child['id'], {}).get('status') == 'pass'
    return {name: dict(rows[name]) for name in sorted(rows, key=lambda n: (-rows[n]['children'], n))}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--report', type=Path, help='an acceptance report.json, for the passing column')
    args = parser.parse_args()
    report = json.loads(args.report.read_text()) if args.report else None
    rows = grouping(report=report)
    print(f'{"template":<58} {"children":>8} {"ready":>6} {"exec":>6} {"ext":>5} {"pass":>5}')
    for name, row in rows.items():
        print(f'{name:<58} {row["children"]:>8} {row["ready"]:>6} {row["executable"]:>6} {row["external"]:>5} {row["pass"]:>5}')
    total = Counter()
    for row in rows.values():
        total.update(row)
    print(f'{"total (" + str(len(rows)) + " templates)":<58} {total["children"]:>8} {total["ready"]:>6} '
          f'{total["executable"]:>6} {total["external"]:>5} {total["pass"]:>5}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
