"""Whole-context development diagnostics over explicit, compatible response effects.

This is not a differential-expression evaluator, a competition score or a sealed
holdout. Reference panels are measured in full, including unseen zero-effect priors.
"""
import json
import zipfile
import numpy as np

if __package__:
    from .engine import Refused, _model, _strings, _dense_budget, _finite, _json, MAX_DENSE_BYTES
    from .aggregate_profiles import _effects, _digest
else:
    from engine import Refused, _model, _strings, _dense_budget, _finite, _json, MAX_DENSE_BYTES
    from aggregate_profiles import _effects, _digest


def evaluate(inputs, params, output, check):
    declared = 0
    for name in ('model', 'reference'):
        with zipfile.ZipFile(inputs[name]) as archive:
            declared += sum(item.file_size for item in archive.infolist())
    if declared * 4 > MAX_DENSE_BYTES:
        raise Refused('The combined artifacts exceed the compact evaluation working allowance')
    model = _model(inputs['model'])
    reference, preparation = _effects(inputs['reference'])
    metadata = json.loads(model['metadata'].item())
    if (not isinstance(metadata, dict) or not metadata.get('normalization')
            or not metadata.get('trainingContexts') or not metadata.get('trainingEffectsSha256')):
        raise Refused('This model lacks explicit preparation provenance; refit from prepared aggregate effects')
    training = _strings(metadata['trainingContexts'], 'Training contexts', unique=True).tolist()
    contexts = sorted(set(reference['contexts'].tolist()))
    if set(training) & set(contexts):
        raise Refused('Training and evaluation contexts overlap; this diagnostic requires whole-context separation')
    if not np.array_equal(model['genes'], reference['genes']):
        raise Refused('Transfer evaluation requires identical ordered gene axes; no silent intersection')
    if metadata.get('scale') != preparation['scale'] or metadata['normalization'] != preparation['normalization']:
        raise Refused('Transfer evaluation requires identical normalization meaning')
    _dense_budget(*model['effects'].shape, copies=3)
    _dense_budget(*reference['effects'].shape, copies=4)
    lookup = {target:i for i,target in enumerate(model['targets'])}
    rows, missing = [], set()
    strength = params['response_strength']
    for context, target, actual in zip(reference['contexts'], reference['targets'], reference['effects']):
        check()
        actual = np.asarray(actual, dtype=np.float64)
        with np.errstate(over='raise', invalid='raise'):
            try:
                if target in lookup:
                    predicted = np.asarray(model['effects'][lookup[target]], dtype=np.float64) * strength
                else:
                    predicted = np.zeros(len(actual)); missing.add(str(target))
                error = predicted - actual
                values = [np.mean(error**2), np.mean(np.abs(error)), np.mean(actual**2), np.mean(np.abs(actual))]
            except FloatingPointError as exc:
                raise Refused('Effect magnitudes exceed finite diagnostic range') from exc
        _finite(values)
        rows.append({'context':str(context), 'target':str(target), **dict(zip(
            ('mse','mae','zeroEffectMse','zeroEffectMae'),map(float, values)))})
    def average(selected):
        result={key:float(np.mean([r[key] for r in selected])) for key in ('mse','mae','zeroEffectMse','zeroEffectMae')}
        _finite(list(result.values()))
        result['mseSkill'] = 1-result['mse']/result['zeroEffectMse'] if result['zeroEffectMse'] else None
        return result
    by_context=[{'context':context,'targets':sum(r['context']==context for r in rows),
        **average([r for r in rows if r['context']==context])} for context in contexts]
    result={'format':'response-effect-evaluation-v1','competitionScore':False,'sealedHoldout':False,
        'purpose':'Whole-context development diagnostic of normalized response effects; not cell-distribution scoring',
        'trainingContexts':training,'evaluatedContexts':contexts,'responseStrength':strength,
        'normalization':preparation['normalization'],'weighting':'equal-context-then-equal-target-and-gene',
        'coverage':{'unseenTargets':sorted(missing),'unseenTargetPrior':'zero-effect','genes':len(model['genes']),
                    'evaluatedPairs':len(rows),'panel':'all reference context-target pairs'},
        'inputs':{name:_digest(inputs[name],check) for name in ('model','reference')},
        'overall':average(by_context),'contexts':by_context,'rows':rows}
    summary={key:result[key] for key in ('format','competitionScore','sealedHoldout','overall','responseStrength','inputs')}
    summary.update(contexts=len(contexts), evaluatedPairs=len(rows), genes=len(model['genes']),
                   unseenTargets=len(missing), detailsRetained=True)
    return _json(output/'effect-evaluation.json',result), summary
