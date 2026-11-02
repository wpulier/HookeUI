"""Explicit aggregate means to response effects; no source-specific label guessing."""
import hashlib
import json
import zipfile

import numpy as np
from scipy import sparse

if __package__:
    from . import bounded_h5
    from .engine import Refused, _strings, _finite, _dense_budget, _npz, BLOCK_ROWS, MAX_DENSE_BYTES
else:
    import bounded_h5
    from engine import Refused, _strings, _finite, _dense_budget, _npz, BLOCK_ROWS, MAX_DENSE_BYTES

PROTOCOL_BYTES = 4 << 20
METADATA_BYTES = 8 << 20


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused('Scientific JSON cannot contain duplicate keys')
        result[key] = value
    return result


def protocol(path):
    if path.stat().st_size > PROTOCOL_BYTES:
        raise Refused('The aggregate protocol exceeds its metadata allowance')
    cfg = json.loads(path.read_text(), object_pairs_hook=_object)
    expected = {'format', 'scientific_unit', 'context', 'experiment', 'row_id', 'weight_column',
                'row_targets', 'control', 'gene_id', 'gene_symbol', 'selected_genes',
                'selected_targets', 'normalization_genes', 'normalization_total', 'pseudocount',
                'duplicate_symbols', 'missing_policy'}
    if type(cfg) is not dict or set(cfg) != expected:
        raise Refused('Declare the complete aggregate-profile protocol without executable additions')
    if cfg['format'] != 'aggregate-profile-config-v1' or cfg['scientific_unit'] != 'mean-raw-counts-per-filtered-cell':
        raise Refused('Only explicitly declared mean raw counts per filtered cell are supported')
    for field in ('context', 'experiment', 'row_id', 'weight_column', 'gene_id', 'gene_symbol'):
        _strings([cfg[field]], field)
    for field in ('selected_genes', 'selected_targets', 'normalization_genes'):
        if type(cfg[field]) is not list:
            raise Refused('Declare explicit ordered axes and target panels')
        _strings(cfg[field], field, unique=True)
    if cfg['duplicate_symbols'] != 'refuse' or cfg['missing_policy'] not in ('refuse', 'exclude'):
        raise Refused('Choose explicit missingness handling and refuse duplicate selected symbols')
    control = cfg['control']
    if type(control) is not dict or set(control) != {'column', 'equals', 'label'} or type(control['equals']) is not bool:
        raise Refused('Controls require an explicit boolean-column equality selector')
    _strings([control['column'], control['label']], 'Control declaration')
    if control['label'] in cfg['selected_targets']:
        raise Refused('The selected target panel must exclude controls')
    mapping = cfg['row_targets']
    if type(mapping) is not dict or not mapping:
        raise Refused('Supply an exact source-row to target mapping')
    _strings(list(mapping), 'Mapped source rows', unique=True)
    _strings(list(mapping.values()), 'Mapped targets')
    if not set(mapping.values()) <= set(cfg['selected_targets']):
        raise Refused('Mapped targets must belong to the declared panel')
    for field, low, high in [('normalization_total', 1, 1e6), ('pseudocount', 1e-6, 100)]:
        value = cfg[field]
        if type(value) not in (int, float) or not np.isfinite(value) or not low <= value <= high:
            raise Refused('Normalization parameters are outside their supported range')
    return cfg


def _digest(path, check=lambda: None):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while part := stream.read(1 << 20):
            check()
            value.update(part)
    return value.hexdigest()


def _save(output, effects, genes, contexts, targets, sizes, metadata):
    encoded = json.dumps(metadata, sort_keys=True, allow_nan=False)
    if len(encoded.encode()) > METADATA_BYTES:
        raise Refused('The retained scientific provenance exceeds its metadata allowance')
    path = output / 'effects.npz'
    np.savez_compressed(path, format=np.array('response-effects-v1'), effects=effects,
                        genes=np.array(genes), contexts=np.array(contexts), targets=np.array(targets),
                        sizes=np.array(sizes, dtype=np.int64), metadata=np.array(encoded))
    summary = {key: metadata[key] for key in ('format', 'shape', 'contexts', 'scale')}
    summary.update({'provenanceRetained': True, 'metadataSha256': hashlib.sha256(encoded.encode()).hexdigest(),
                    'missingTargets': len(metadata.get('coverage', {}).get('missingTargets', [])),
                    'missingGenes': len(metadata.get('coverage', {}).get('missingGenes', []))})
    return path, summary


def prepare(inputs, params, output, check):
    import h5py
    cfg = protocol(inputs['protocol'])
    with h5py.File(inputs['matrix'], 'r') as source:
        x = bounded_h5.matrix(source, check); nrows, nvars = x.shape
        if getattr(x, 'format', None) == 'csc':
            raise Refused('Bounded aggregate preparation supports dense or CSR matrices; CSC needs an explicitly prepared conversion')
        obs, var = source['obs'], source['var']
        rows = _strings(bounded_h5.text(obs[cfg['row_id']], nrows), 'Source rows', unique=True)
        gene_ids = _strings(bounded_h5.text(var[cfg['gene_id']], nvars), 'Source gene identities', unique=True)
        symbols = bounded_h5.text(var[cfg['gene_symbol']], nvars)
        weights = bounded_h5.numeric(obs[cfg['weight_column']], nrows)
        if weights.dtype.kind == 'b':
            raise Refused('Cell weights must be numeric counts, not booleans')
        selector = bounded_h5.numeric(obs[cfg['control']['column']], nrows)
        if selector.dtype.kind != 'b':
            raise Refused('The declared control selector must be a boolean column')
        controls = selector == cfg['control']['equals']
        row_lookup = {name: i for i, name in enumerate(rows)}
        absent_rows = sorted(set(cfg['row_targets']) - set(row_lookup))
        if absent_rows:
            raise Refused('A mapped source-row identity does not exist; no label inference is allowed')
        mapped = {row_lookup[row]: target for row, target in cfg['row_targets'].items()}
        if any(controls[i] for i in mapped):
            raise Refused('A row cannot be both a control and a mapped perturbation')
        mapped_targets = set(mapped.values()); symbol_set = set(symbols)
        targets = [target for target in cfg['selected_targets'] if target in mapped_targets]
        missing_targets = [target for target in cfg['selected_targets'] if target not in mapped_targets]
        missing_genes = [gene for gene in cfg['selected_genes'] if gene not in symbol_set]
        if (missing_targets or missing_genes) and cfg['missing_policy'] == 'refuse':
            raise Refused('The selected target/gene panel is not fully measured')
        genes = [gene for gene in cfg['selected_genes'] if gene in symbol_set]
        if not targets or not genes or not controls.any():
            raise Refused('Preparation needs measured targets, genes and selected controls')
        required = set(genes) | set(cfg['normalization_genes'])
        positions = {}
        for index, symbol in enumerate(symbols):
            if symbol in required:
                if symbol in positions:
                    raise Refused('A selected or normalization symbol has duplicate source identities')
                positions[symbol] = index
        if not required <= set(positions):
            raise Refused('The explicit normalization gene axis is not fully measured')
        source_columns = sorted(positions.values())
        column_lookup = {source_index: i for i, source_index in enumerate(source_columns)}
        gene_columns = [column_lookup[positions[g]] for g in genes]
        norm_columns = [column_lookup[positions[g]] for g in cfg['normalization_genes']]
        group_lookup = {target: i for i, target in enumerate(targets)}
        groups = np.full(nrows, -1, dtype=np.int64)
        groups[controls] = len(targets)
        for index, target in mapped.items():
            groups[index] = group_lookup[target]
        chosen = groups >= 0
        selected_weights = weights[chosen]
        if (not np.isfinite(selected_weights).all() or np.any(selected_weights <= 0)
                or np.any(selected_weights != np.floor(selected_weights))
                or selected_weights.sum(dtype=np.float64) > 2**53 - 1):
            raise Refused('Selected aggregate weights must be positive finite whole cell counts within exact range')
        _dense_budget(len(targets) + 1 + BLOCK_ROWS, len(source_columns), copies=4)
        sums = np.zeros((len(targets) + 1, len(source_columns)), dtype=np.float64)
        sizes = np.zeros(len(targets) + 1, dtype=np.int64)
        for start, stop in bounded_h5.row_ranges(x, BLOCK_ROWS, check):
            local = np.flatnonzero(chosen[start:stop])
            if not len(local):
                continue
            block = bounded_h5.row_block(x, start, stop, source_columns)
            block = block.toarray() if sparse.issparse(block) else np.asarray(block)
            block = block[local].astype(np.float64)
            _finite(block)
            if np.any(block < 0):
                raise Refused('Mean raw counts must be nonnegative')
            ids = local + start
            for row, source_row in enumerate(ids):
                group = groups[source_row]
                sums[group] += block[row] * weights[source_row]
                sizes[group] += int(weights[source_row])
        _finite(sums)
        depths = sums[:, norm_columns].sum(axis=1)
        if np.any(depths <= 0):
            raise Refused('Every selected aggregate needs positive depth on the normalization axis')
        normalized = sums[:, gene_columns] / depths[:, None] * cfg['normalization_total']
        effects = np.log2((normalized[:-1] + cfg['pseudocount']) / (normalized[-1] + cfg['pseudocount']))
        _finite(effects)
        contributions = [{'row': str(rows[i]), 'target': cfg['control']['label'] if controls[i] else mapped[i],
                          'cells': int(weights[i])} for i in np.flatnonzero(chosen)]
        normalization = {'genes': cfg['normalization_genes'], 'total': cfg['normalization_total'],
                         'pseudocount': cfg['pseudocount'], 'method': 'aggregate-count-proportions'}
        metadata = {'format': 'response-effects-v1', 'shape': list(effects.shape), 'contexts': [cfg['context']],
                    'scale': 'log2-normalized-count-ratio', 'normalization': normalization,
                    'aggregation': 'cell-weighted-mean-profiles', 'scientificUnit': cfg['scientific_unit'],
                    'coverage': {'missingTargets': missing_targets, 'missingGenes': missing_genes,
                                 'excludedRows': [str(rows[i]) for i in np.flatnonzero(~chosen)],
                                 'measuredZeroIsMissing': False},
                    'provenance': {'protocolSha256': _digest(inputs['protocol']), 'protocol': cfg,
                                   'sourceShape': list(x.shape), 'sourceContributions': contributions,
                                   'geneMapping': {g: str(gene_ids[positions[g]]) for g in sorted(required)}}}
    return _save(output, effects, genes, [cfg['context']] * len(targets), targets, sizes[:-1], metadata)


def _effects(path):
    data = _npz(path, ('format', 'effects', 'genes', 'contexts', 'targets', 'sizes', 'metadata'))
    if data['format'].item() != 'response-effects-v1':
        raise Refused('Merge requires prepared response effects')
    genes = _strings(data['genes'], 'Effect genes', unique=True)
    targets = _strings(data['targets'], 'Effect targets')
    contexts = _strings(data['contexts'], 'Effect contexts')
    if data['effects'].shape != (len(targets), len(genes)) or len(contexts) != len(targets):
        raise Refused('Response effects have inconsistent axes')
    if len(set(zip(contexts, targets))) != len(targets):
        raise Refused('Duplicate biological-context/target rows cannot be merged')
    _finite(data['effects'])
    sizes = data['sizes']
    if sizes.shape != (len(targets),) or sizes.dtype.kind not in 'iu' or np.any(sizes <= 0):
        raise Refused('Response effects require positive retained cell counts')
    encoded = data['metadata']
    if encoded.shape != () or encoded.dtype.kind not in 'US' or len(str(encoded.item()).encode()) > METADATA_BYTES:
        raise Refused('Effects have unbounded or malformed provenance metadata')
    metadata = json.loads(encoded.item(), object_pairs_hook=_object)
    if (type(metadata) is not dict or metadata.get('format') != 'response-effects-v1'
            or metadata.get('scale') != 'log2-normalized-count-ratio'
            or type(metadata.get('provenance')) is not dict or not metadata['provenance']
            or type(metadata.get('normalization')) is not dict):
        raise Refused('Merge needs explicit normalization and provenance; reprepare legacy effects')
    if metadata.get('shape') != list(data['effects'].shape) or metadata.get('contexts') != sorted(set(contexts.tolist())):
        raise Refused('Effect provenance disagrees with its scientific axes')
    norm = metadata['normalization']
    if set(norm) != {'genes', 'total', 'pseudocount', 'method'} or norm['method'] != 'aggregate-count-proportions':
        raise Refused('Effects have an unsupported normalization meaning')
    _strings(norm['genes'], 'Normalization genes', unique=True)
    for key in ('total', 'pseudocount'):
        if type(norm[key]) not in (int, float) or not np.isfinite(norm[key]) or norm[key] <= 0:
            raise Refused('Effects have invalid normalization parameters')
    return data, metadata


def merge(inputs, params, output, check):
    declared = 0
    for side in ('left', 'right'):
        with zipfile.ZipFile(inputs[side]) as archive:
            declared += sum(info.file_size for info in archive.infolist())
    if declared * 4 > MAX_DENSE_BYTES:
        raise Refused('The combined artifacts exceed the compact merge working allowance')
    left, lm = _effects(inputs['left']); right, rm = _effects(inputs['right'])
    if not np.array_equal(left['genes'], right['genes']) or lm['normalization'] != rm['normalization']:
        raise Refused('Merge requires identical ordered gene axes and normalization meaning')
    pairs = list(zip(left['contexts'], left['targets'])) + list(zip(right['contexts'], right['targets']))
    if len(set(pairs)) != len(pairs):
        raise Refused('Duplicate biological-context/target rows cannot be merged')
    _dense_budget(len(pairs), len(left['genes']), copies=4); check()
    values = np.concatenate((left['effects'], right['effects']))
    metadata = {'format': 'response-effects-v1', 'shape': list(values.shape),
                'contexts': sorted({str(c) for c, _ in pairs}), 'scale': lm['scale'],
                'normalization': lm['normalization'], 'aggregation': 'explicit-context-target-concatenation',
                'coverage': {key: sorted(set(lm.get('coverage', {}).get(key, []) + rm.get('coverage', {}).get(key, [])))
                             for key in ('missingTargets', 'missingGenes')},
                'provenance': {'sources': [{'sha256': _digest(inputs[side], check), 'metadata': meta}
                                           for side, meta in [('left', lm), ('right', rm)]]}}
    return _save(output, values, left['genes'], [c for c, _ in pairs], [t for _, t in pairs],
                 np.concatenate((left['sizes'], right['sizes'])), metadata)
