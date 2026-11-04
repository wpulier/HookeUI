"""Read a complete single-context cell-eval2 v1 comparison without importing its runtime."""
import math
import re


def compare(left, right, prediction_hashes):
    metrics = [('mae', 'Expression MAE', 'lower'), ('discrimination_score_l1', 'Perturbation discrimination', 'higher'),
               ('overlap_at_N', 'Differential-expression overlap', 'higher')]
    if (left['format'] != 'cell-evaluation-v1' or left['evaluator'] != 'cell-eval2' or left['version'] != '0.20.0'
            or left['protocol']['preset'] != 'v1' or left['protocol']['metrics'] != 'vcc'
            or any(left[k] != right[k] for k in ('format','evaluator','version','backend','protocol','config','primaryScoredMetrics'))
            or set(left['primaryScoredMetrics']) != {'expr_mae','pds_l1','de_wilcoxon_overlap'}):
        raise ValueError('Select complete scores from the same supported evaluator and preset')
    for key in ('reference', 'config'):
        digest = left['inputs'][key]
        if not re.fullmatch('[a-f0-9]{64}', digest) or right['inputs'][key] != digest:
            raise ValueError('Use the same reference and scoring configuration')
    values = []
    for report, prediction in zip((left, right), prediction_hashes, strict=True):
        if report['inputs']['prediction'] != prediction:
            raise ValueError('This scoring report belongs to another prediction')
        context, = report['contexts']
        targets = context['targets']
        if not targets or len(set(targets)) != len(targets) or report['protocol']['panels'] != {context['context']:targets}:
            raise ValueError('The report must cover the complete declared context')
        expected = {(key, target) for key, _, _ in metrics for target in targets}
        rows = context['metrics']
        if len(rows) != len(expected) or {(r['metric'],r['perturbation']) for r in rows if math.isfinite(r['value'])} != expected:
            raise ValueError('Each metric must cover the same complete panel')
        aggregate = {r['metric']:r['mean'] for r in context['aggregate'] if r['agg'] == 'mean' and math.isfinite(r['mean'])}
        if len(context['aggregate']) != len(metrics) or set(aggregate) != {k for k,_,_ in metrics}:
            raise ValueError('All declared aggregate metrics are required')
        values.append(aggregate)
    return [{'name':name, 'direction':direction, 'baseline':values[0][key], 'challenger':values[1][key]}
            for key,name,direction in metrics]
