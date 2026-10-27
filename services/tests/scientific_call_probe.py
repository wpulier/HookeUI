"""Bounded actual P7 diagnostic, not a mapped acceptance run. Requires the coordinated native slot.

From services: python -m tests.scientific_call_probe --preparation /private/prepared.json
--python /prepared/bin/python --root /private/new-probe --symbol Layer.save

The canonical complete comparison run and all mapped prerequisites remain required.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path

from tests import test_scientific_reference_comparison as comparison
from tests.scientific_comparison_recipes import CALLS


def _persist(path, receipt):
    """Replace one private receipt atomically; a failed write keeps the prior stage."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.diagnostic-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(receipt, stream, sort_keys=True, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run(prepared, python, symbol, root):
    child = next(c for c in comparison.CHILDREN
                 if c['harness'] == 'compare-call' and c['source']['symbol'] == symbol)
    case = comparison._cases([child])[0]
    receipt = {'evidence_class': 'actual_upstream_worker_diagnostic', 'qualified': False,
               'mapped_completion': False, 'symbol': symbol, 'status': 'running',
               'stage': 'preparation', 'positive': {}, 'negative': {}}
    path = root / 'diagnostic.json'
    # A programmatic retry must also preserve its earlier attempt, as the CLI does.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        json.dump(receipt, stream, sort_keys=True, indent=2)
        stream.write('\n')
    def stage(name):
        receipt['stage'] = name
        _persist(path, receipt)
    restore = None
    try:
        restore = comparison._prepared_environment(prepared)
        pair = receipt['positive']
        stage('positive-hooke')
        pair['hooke'] = comparison._hooke(prepared, root / 'positive', [case])
        stage('positive-reference')
        pair['reference'] = comparison._reference(python, [case], pair['hooke']['scene'])
        stage('positive-admission')
        comparison.test_the_session_holds_the_retained_volume(pair)
        comparison.test_both_sides_ran_the_same_pinned_napari(pair)
        stage('positive-comparison')
        comparison.test_compare_child(child['id'], pair)
        template, broken_symbol, broken = comparison.BREAKS['writer-values']
        negative_case = {'id': 'writer-values', 'template': template, 'symbol': broken_symbol,
                         'path': next(c['source']['path'] for c in comparison.CHILDREN
                                      if c['harness'] == template and c['source']['symbol'] == broken_symbol)}
        negative = receipt['negative']
        stage('negative-hooke')
        negative['hooke'] = comparison._hooke(prepared, root / 'negative', [negative_case], broken)
        stage('negative-reference')
        negative['reference'] = comparison._reference(python, [negative_case], negative['hooke']['scene'])
        stage('negative-admission')
        comparison.test_both_sides_ran_the_same_pinned_napari(negative)
        stage('negative-comparison')
        comparison.test_a_broken_behaviour_in_hookes_worker_is_caught(
            'writer-values', {'negatives': {'writer-values': negative}})
        stage('environment-restore')
        release, restore = restore, None
        release()
        receipt['status'] = 'passed'
        stage('complete')
        return receipt
    except BaseException as error:
        receipt['status'] = 'failed'
        receipt['error'] = {'type': type(error).__name__, 'message': str(error)[:4000]}
        try:
            _persist(path, receipt)
        except BaseException as persistence_error:
            error.add_note(f'Diagnostic receipt could not be updated: {persistence_error}')
        raise
    finally:
        if restore is not None:
            restore()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preparation', type=Path, required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--symbol', choices=sorted(CALLS), default='Layer.save')
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False, mode=0o700)
    result = run(json.loads(args.preparation.read_text()), args.python, args.symbol, args.root)
    print(json.dumps({k: v for k, v in result.items() if k not in ('positive', 'negative')}))


if __name__ == '__main__':
    main()
