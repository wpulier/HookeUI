"""Which of a list of targets a dataset holds, read from its observation annotations alone (HOOKE-GAPS row 6).

On the VCC night this was a scratch script: of the contest's 300 targets, how many had been perturbed in Jurkat,
HepG2 and the K562 essential screen (none), and how strong the K562 knockdowns were (their Mann-Whitney counts).
Here the matrix's obs is read with h5py alone (`h5ad_read.py`; X is never opened, so a 10 GB screen answers in
seconds) and compared with a target list from a CSV:

- every distinct label of the declared obs column (or of the row names, or one '_'-separated token of them, as a
  pseudobulk names its rows), apart from the control label, and how many rows carry each;
- the listed targets found in it and those not found (exact text; nothing is normalised or inferred);
- optionally, a numeric obs column summarised over the rows of each found target (n, mean, median, min, max) and
  over all non-control rows.

A label is what the dataset's makers wrote: it does not establish what was perturbed or how well.
"""
import csv
import json
import math

import numpy as np

import h5py

from h5ad_read import Refused, column, frame, index

MAX_LISTED = 4096


def _targets(path, name):
    with open(path, newline='', encoding='utf-8-sig') as handle:
        rows = csv.DictReader(handle)
        if rows.fieldnames is None or name not in rows.fieldnames:
            raise Refused(f'The target list is a CSV with a {name!r} column')
        found = []
        for row in rows:
            value = (row.get(name) or '').strip()
            if value and value not in found:
                found.append(value)
    if not found:
        raise Refused('The target list names no targets')
    if len(found) > 65536:
        raise Refused('The target list names more than 65,536 targets')
    return found


def _token(name, token):
    if token == 0:
        return name
    parts = name.split('_')
    return parts[token - 1] if len(parts) >= token else None


def _stats(values):
    values = values[np.isfinite(values)]
    if not len(values):
        return {'n': 0}
    return {'n': int(len(values)), 'mean': float(values.mean()), 'median': float(np.median(values)),
            'min': float(values.min()), 'max': float(values.max())}


def run(inputs, params, output, check):
    label, control = params['column'], params['control_label']
    with h5py.File(inputs['matrix'], 'r') as f:
        obs = frame(f, 'obs')
        if label:
            labels = column(obs, label)
            if labels.dtype != object:
                raise Refused(f'obs[{label!r}] holds numbers, not target labels')
        else:  # a pseudobulk names its rows by target (Replogle 2022: <n>_<gene>_<guides>_<id>)
            token = params['name_token']
            names = index(obs)
            labels = np.asarray([_token(str(n), token) for n in names], dtype=object)
        values = None
        if params['value_column']:
            numbers = column(obs, params['value_column'])
            if numbers.dtype == object:
                raise Refused(f'obs[{params["value_column"]!r}] holds text, not numbers')
            values = np.asarray(numbers, dtype=np.float64)
    check()
    counts = {}
    for value in labels:
        if value is not None and value != control:
            counts[value] = counts.get(value, 0) + 1
    controls = int(sum(1 for v in labels if v == control))
    targets = _targets(inputs['targets'], params['targets_column'])
    found = [t for t in targets if t in counts]
    missing = [t for t in targets if t not in counts]
    result = {'format': 'target-overlap-v1', 'column': label or f'row names (token {params["name_token"]})',
              'controlLabel': control,
              'observations': int(len(labels)), 'controlObservations': controls,
              'missingLabels': int(sum(1 for v in labels if v is None)),
              'distinctTargets': len(counts), 'targetsListed': len(targets),
              'found': len(found), 'notFound': len(missing),
              'foundTargets': [{'target': t, 'observations': counts[t]} for t in found[:MAX_LISTED]],
              'notFoundTargets': missing[:MAX_LISTED],
              'truncated': len(found) > MAX_LISTED or len(missing) > MAX_LISTED,
              'meaning': ('Exact text labels of the declared observation column compared with the listed targets; a '
                          'label is what the dataset\'s makers recorded and does not establish what was perturbed.')}
    if values is not None:
        rows_of = {}
        for i, value in enumerate(labels):
            if value is not None and value != control:
                rows_of.setdefault(value, []).append(i)
        result['valueColumn'] = params['value_column']
        result['valuesOverTargets'] = _stats(values[[i for rows in rows_of.values() for i in rows]])
        result['valuesOverFound'] = _stats(values[[i for t in found for i in rows_of[t]]]) if found else {'n': 0}
        result['valuesByFoundTarget'] = {t: _stats(values[rows_of[t]]) for t in found[:MAX_LISTED]}
    for value in result.get('valuesByFoundTarget', {}).values():
        for key, number in value.items():
            if isinstance(number, float) and not math.isfinite(number):
                raise Refused('A value summary is not finite')
    path = output / 'overlap.json'
    path.write_text(json.dumps(result, sort_keys=True, allow_nan=False))
    return path, {k: result[k] for k in ('format', 'observations', 'distinctTargets', 'targetsListed', 'found', 'notFound')}
