#!/usr/bin/env python3
"""Local scientific acceptance progress; external service planning is private.
Historical counters never qualify a different source candidate.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services'))
from bench import scientific_acceptance as runner

LAUNCH = ROOT / 'docs/testing/launch-checklist.md'
LIFECYCLE = ROOT / 'docs/testing/maintenance-checklist.md'
INVENTORY = ROOT / 'services/bench/specification/atomic-cases.json'
REPORTS = ROOT / 'reports/scientific-acceptance'
WEIGHTS = {'parity': 0.85, 'launch': 0.05, 'maintenance': 0.05, 'qualification': 0.05}
MAINTENANCE_SLICES = [f'MT{n}' for n in range(8)]


def qualification(group):
    return group == 'Q' or group.startswith(('Q.', 'P8.'))


def launch_rows(text):
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) == 6 and cells[0].isdigit():
            rows.append({'row': int(cells[0]), 'need': cells[1], 'evidence': cells[5]})
    return rows


def launch():
    rows = launch_rows(LAUNCH.read_text())
    done = [r for r in rows if r['evidence'].lower().startswith('demonstrated')]
    return {'done': len(done), 'total': len(rows), 'unit': 'launch checks Demonstrated on the signed candidate',
            'left': [f"{r['row']}. {r['need']}" for r in rows if r not in done]}


def work_state(text):
    match = re.search(r'^\**Work state:\**\s*(\w+)', text, re.M)
    return match.group(1).lower() if match else 'unknown'


def maintenance():
    text = LIFECYCLE.read_text()
    done = [s for s in MAINTENANCE_SLICES
            if re.search(rf'^\d{{4}}-\d{{2}}-\d{{2}}: {s} Demonstrated\b', text, re.M)]
    return {'done': len(done), 'total': len(MAINTENANCE_SLICES), 'unit': 'MT slices merged and Demonstrated',
            'left': [s for s in MAINTENANCE_SLICES if s not in done]}


def newest_report():
    found = sorted(REPORTS.glob('*/report.json'), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def commit_of(run):
    if isinstance(run, dict):
        if isinstance(run.get('commit'), str):
            return run['commit']
        for value in run.values():
            found = commit_of(value)
            if found:
                return found
    return None


def acceptance(path):
    """Use the runner's sealed execution and current inventory, never imported pass counters."""
    inventory = runner.load_inventory()
    reported = {}
    results = None
    note = 'no retained report: run make scientific-acceptance on this source'
    if path:
        path = Path(path)
        reported = json.loads(path.read_text())
        try:
            if path.name != 'report.json':
                raise ValueError("expected the acceptance runner's report.json")
            results = runner.reuse(path.parent)
            note = results.get('stale') or 'current locally sealed execution; external qualification remains separate'
        except (runner.InvalidAcceptance, OSError, ValueError, KeyError) as error:
            note = str(error)
    # Recompute all children/groups from trusted mappings and sealed cases. Report counters
    # are outside the run's seal and may be stale, partial or edited independently of it.
    report = runner.acceptance_report(inventory, results)
    source = {'report': str(path) if path else None, 'commit': commit_of(reported.get('run')),
              'current': results is not None and not results.get('stale'), 'note': note,
              'completion': report['completion'], 'completion_blockers': report['completion_blockers'],
              'workflow_groups': {g: info['status'] for g, info in report['groups'].items()
                                  if g.startswith('W.')}}

    def track(keep, unit):
        mine = [c['status'] for c in report['children'].values() if keep(c['group'])]
        mine_groups = [info['status'] for g, info in report['groups'].items() if keep(g)]
        old = [c['status'] for c in reported.get('children', {}).values() if keep(c['group'])]
        return {'done': mine.count('pass'), 'total': len(mine), 'unit': unit,
                'reported_done': old.count('pass'), 'reported_total': len(old),
                'groups_done': mine_groups.count('pass'), 'groups_total': len(mine_groups),
                'states': dict(Counter(mine)), 'source': source}

    return (track(lambda g: not qualification(g), 'current acceptance children passing'),
            track(qualification, 'current Q and P8 children with real evidence'))


def measure(report_path=None):
    parity, qualified = acceptance(report_path)
    tracks = {'parity': parity, 'launch': launch(),
              'maintenance': maintenance(), 'qualification': qualified}
    for t in tracks.values():
        t['fraction'] = t['done'] / t['total'] if t['total'] else 0.0
    overall = sum(WEIGHTS[name] * t['fraction'] for name, t in tracks.items())
    source = parity['source']
    blockers = list(source['completion_blockers'])
    if not source['current']:
        blockers.append(source.get('note', 'No current scientific execution evidence'))
    blockers.extend(f'{name}: {t["done"]}/{t["total"]} recorded units' for name, t in tracks.items()
                    if not t['total'] or t['done'] != t['total'])
    workflows = source['workflow_groups']
    return {'overall': overall, 'weights': WEIGHTS, 'tracks': tracks,
            'completion': source['completion'] and not blockers,
            'completion_blockers': blockers,
            'core_workflow': {'launch_done': tracks['launch']['done'], 'launch_total': tracks['launch']['total'],
                              'workflow_groups_done': sum(s == 'pass' for s in workflows.values()),
                              'workflow_groups_total': len(workflows), 'workflow_groups': workflows}}



def text(result):
    lines = [f"Recorded weighted evidence: {result['overall'] * 100:.1f}% (not a completion certificate)", '']
    for name, t in result['tracks'].items():
        extra = f"; groups {t['groups_done']}/{t['groups_total']}" if 'groups_total' in t else ''
        lines.append(f"{name:14} {t['done']:>5}/{t['total']:<5} {t['fraction'] * 100:5.1f}%  "
                     f"weight {result['weights'][name]:.2f}  {t['unit']}{extra}")
    source = result['tracks']['parity']['source']
    lines += ['', f"Acceptance report: {source.get('report') or source.get('note')}"
              + (f" (commit {source['commit'][:8]})" if source.get('commit') else '')]
    lines += [f"Scientific evidence current: {bool(source['current'])}. {source['note']}",
              f"Historical report parity counters: {result['tracks']['parity']['reported_done']}/"
              f"{result['tracks']['parity']['reported_total']} (not current credit)",
              f"Core workflow: {result['core_workflow']['launch_done']}/{result['core_workflow']['launch_total']} launch rows; "
              f"{result['core_workflow']['workflow_groups_done']}/{result['core_workflow']['workflow_groups_total']} W groups",
              f"Full completion: {result['completion']}", 'Completion blockers:']
    lines.extend(f'- {item}' for item in result['completion_blockers'])
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--acceptance', help='a retained scientific-acceptance report.json (default: the newest here)')
    parser.add_argument('--json', action='store_true', help='print the measurement as JSON')
    parser.add_argument('--require-complete', action='store_true', help='exit nonzero unless full completion is evidenced')
    args = parser.parse_args(argv)
    report = args.acceptance or newest_report()
    result = measure(report)
    print(json.dumps(result, indent=1) if args.json else text(result))
    return 1 if args.require_complete and not result['completion'] else 0


if __name__ == '__main__':
    sys.exit(main())
