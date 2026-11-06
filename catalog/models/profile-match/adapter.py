"""Which reference profiles a sample's pseudobulk expression most resembles: a hypothesis, never an identity.

On the VCC night (5 Nov 2026) the hidden contexts' cell lines were guessed by a scratch script: the Spearman
correlation of each context's control (non-targeting) pseudobulk with DepMap 24Q4's cell-line expression (context D
was closest to MOLM-13, E to U-87 MG; `LINE-IDENTITY-HYPOTHESIS.md`). This is that method as a capability:

- the sample's selected rows (those whose `cell_column` label is `cell_label`, or every row) are summed gene by gene
  from its raw counts (`h5ad_read.column_sums`: X read a block of rows at a time);
- the reference is a CSV with one profile per row: an id in its first column, one column per gene. A gene header
  like DepMap's "TP53 (7157)" is matched by its symbol before the parenthesis;
- genes named once in both, with a value in every reference row and at least `min_counts` in the pseudobulk, are
  compared; each profile's Spearman correlation (ties averaged) with the pseudobulk ranks it;
- the profiles are named from a names CSV (DepMap's Model.csv: ModelID to CellLineName).

The answer is labelled a hypothesis: rank correlation of a pseudobulk with bulk line profiles measures similarity
across platforms, normalisations and conditions, and does not prove what the sample is.
"""
import csv
import json
import math
import re

import h5py
import numpy as np

from h5ad_read import Refused, column, frame, index, column_sums

MAX_PROFILES = 20000
MAX_GENES = 100000
MEANING = ('A hypothesis: profiles ranked by Spearman correlation with the sample\'s pseudobulk over the genes both '
           'name. Similarity across platforms and conditions does not establish identity; confirm it another way '
           '(STR profile, known markers, the provider\'s record).')


def _symbol(header, strip):
    header = header.strip()
    return re.sub(r'\s*\([^()]*\)\s*$', '', header) if strip else header


def _reference(path, strip, check):
    with open(path, newline='', encoding='utf-8-sig') as handle:
        rows = csv.reader(handle)
        header = next(rows, None)
        if not header or len(header) < 3:
            raise Refused('The reference is a CSV: an id column, then one column per gene')
        genes = [_symbol(h, strip) for h in header[1:]]
        if len(genes) > MAX_GENES:
            raise Refused('The reference has more than 100,000 gene columns')
        ids, values = [], []
        for n, row in enumerate(rows):
            if n % 64 == 0:
                check()
            if not row:
                continue
            if len(row) != len(header):
                raise Refused(f'Reference row {n + 2} has {len(row)} fields, not {len(header)}')
            ids.append(row[0])
            values.append(np.array([float(v) if v not in ('', 'NA', 'nan', 'NaN') else np.nan for v in row[1:]]))
            if len(ids) > MAX_PROFILES:
                raise Refused('The reference has more than 20,000 profiles')
    if not ids:
        raise Refused('The reference has no profiles')
    return ids, genes, np.vstack(values)


def _names(path, id_column, label_column):
    with open(path, newline='', encoding='utf-8-sig') as handle:
        rows = csv.DictReader(handle)
        if rows.fieldnames is None or id_column not in rows.fieldnames or label_column not in rows.fieldnames:
            raise Refused(f'The names CSV needs {id_column!r} and {label_column!r} columns')
        return {row[id_column]: row[label_column] for row in rows if row.get(id_column)}


def _ranks(matrix):
    from scipy.stats import rankdata
    return rankdata(matrix, axis=-1, method='average')


def run(inputs, params, output, check):
    with h5py.File(inputs['sample'], 'r') as f:
        obs, var = frame(f, 'obs'), frame(f, 'var')
        rows = len(index(obs))
        if params['cell_column']:
            labels = column(obs, params['cell_column'])
            selected = np.asarray([v == params['cell_label'] for v in labels], dtype=bool)
            if not selected.any():
                raise Refused(f'No row of the sample has {params["cell_column"]} = {params["cell_label"]!r}')
        else:
            selected = np.ones(rows, dtype=bool)
        sample_genes = column(var, params['gene_column']) if params['gene_column'] else index(var)
        totals = column_sums(f, selected, check)
    if len(sample_genes) != len(totals):
        raise Refused('The sample\'s gene names do not match its matrix')
    if np.any(totals < 0) or not np.all(np.isfinite(totals)):
        raise Refused('The sample\'s counts must be finite and not negative')
    ids, genes, reference = _reference(inputs['reference'], params['strip_parenthesised_ids'], check)
    names = _names(inputs['names'], params['names_id_column'], params['names_label_column'])
    check()

    def once(values):
        seen = {}
        for i, v in enumerate(values):
            if v is not None and str(v):
                seen[str(v)] = -1 if str(v) in seen else i
        return {k: i for k, i in seen.items() if i >= 0}
    in_sample, in_reference = once(sample_genes), once(genes)
    complete = np.all(np.isfinite(reference), axis=0)
    shared = sorted(g for g in in_sample.keys() & in_reference.keys()
                    if complete[in_reference[g]] and totals[in_sample[g]] >= params['min_counts'])
    if len(shared) < params['min_genes']:
        raise Refused(f'Only {len(shared)} genes are shared with a value everywhere; at least {params["min_genes"]} are needed')
    sample = totals[[in_sample[g] for g in shared]]
    library = float(totals.sum())
    if library <= 0:
        raise Refused('The selected rows hold no counts')
    sample = np.log2(sample / library * 1e6 + 1)  # log2(CPM + 1); Spearman depends only on its order
    profiles = reference[:, [in_reference[g] for g in shared]]
    ranked_sample, ranked = _ranks(sample), _ranks(profiles)
    a = ranked_sample - ranked_sample.mean()
    b = ranked - ranked.mean(axis=1, keepdims=True)
    denominator = np.sqrt((a * a).sum() * (b * b).sum(axis=1))
    rho = np.divide(b @ a, denominator, out=np.full(len(ids), np.nan), where=denominator > 0)
    order = sorted(range(len(ids)), key=lambda i: (-(rho[i] if math.isfinite(rho[i]) else -2), ids[i]))
    top = [{'rank': n + 1, 'id': ids[i], 'name': names.get(ids[i]), 'spearman': float(rho[i])}
           for n, i in enumerate(order[:params['top']]) if math.isfinite(rho[i])]
    result = {'format': 'profile-match-v1', 'hypothesis': True, 'meaning': MEANING,
              'method': 'Spearman rank correlation (ties averaged) of log2(CPM+1) pseudobulk with each reference row',
              'selectedRows': int(selected.sum()), 'rows': int(rows),
              'selection': ({'column': params['cell_column'], 'label': params['cell_label']} if params['cell_column']
                            else 'every row'),
              'pseudobulkCounts': library, 'genesCompared': len(shared), 'referenceProfiles': len(ids),
              'referenceGenes': len(genes), 'top': top,
              'margin': (top[0]['spearman'] - top[1]['spearman']) if len(top) > 1 else None}
    path = output / 'profile-match.json'
    path.write_text(json.dumps(result, sort_keys=True, allow_nan=False))
    return path, {'format': result['format'], 'hypothesis': True, 'genesCompared': len(shared),
                  'best': top[0] if top else None}
