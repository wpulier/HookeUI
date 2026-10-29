"""Explicit response-scale interpretation, separate from stochastic count emission."""
import numpy as np

if __package__:
    from .engine import Refused, _strings
else:
    from engine import Refused, _strings


def proportions(control, exponent, genes, metadata, method):
    """Return compositional probabilities and the number of negative inverse projections.

    Inverse mode preserves the preparation's denominator and pseudocount before
    projecting invalid negative expected expression to zero and closing the whole
    composition. Closure may change zero-effect genes' absolute proportions.
    """
    if method == 'proportional-fold-v1':
        values = control * np.exp2(exponent)
        projected = 0
    elif method == 'pseudocount-inverse-v1':
        norm = metadata.get('normalization') if type(metadata) is dict else None
        if (type(metadata) is not dict or metadata.get('scale') != 'log2-normalized-count-ratio' or type(norm) is not dict
                or set(norm) != {'genes', 'total', 'pseudocount', 'method'}
                or norm['method'] != 'aggregate-count-proportions'):
            raise Refused('Inverse emission requires explicit fitted normalization; refit legacy models')
        axis = _strings(norm['genes'], 'Normalization genes', unique=True)
        if not set(axis) <= set(genes):
            raise Refused('Controls do not measure the entire fitted normalization axis')
        for key in ('total', 'pseudocount'):
            if type(norm[key]) not in (int, float) or not np.isfinite(norm[key]) or norm[key] <= 0:
                raise Refused('Invalid fitted normalization parameters')
        mass = control[np.isin(genes, axis)].sum()
        if mass <= 0:
            raise Refused('Controls have no expression on the fitted normalization axis')
        if not np.any(exponent):
            return control.copy(), 0
        base = control / mass * norm['total']
        values = (base + norm['pseudocount']) * np.exp2(exponent) - norm['pseudocount']
        # The zero-effect prior is exact before compositional closure, including unmeasured genes.
        values[exponent == 0] = base[exponent == 0]
        projected = int(np.sum(values < 0))
        values = np.maximum(values, 0)
    else:
        raise Refused('Unknown effect interpretation')
    total = values.sum()
    if not np.isfinite(values).all() or not np.isfinite(total) or total <= 0:
        raise Refused('Effects do not produce finite positive expression mass')
    return values / total, projected
