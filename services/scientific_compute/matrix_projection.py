"""Explicit raw-cell projection; immutable sources, no inferred target mapping."""
import hashlib
import json
import shutil

import numpy as np
from scipy import sparse

if __package__:
    from . import engine, bounded_h5
    from .aggregate_profiles import _object, _digest
else:
    import engine, bounded_h5
    from aggregate_profiles import _object, _digest


def protocol(path):
    if path.stat().st_size > 4 << 20:
        raise engine.Refused('Projection protocol exceeds its metadata allowance')
    cfg = json.loads(path.read_text(), object_pairs_hook=_object)
    fields = {'format', 'context', 'experiment', 'observation_id', 'target_column',
              'target_mapping', 'control_label', 'selected_targets', 'gene_id',
              'gene_symbol', 'selected_genes', 'cells_per_group', 'seed'}
    if type(cfg) is not dict or set(cfg) != fields or cfg['format'] != 'raw-count-projection-v1':
        raise engine.Refused('Declare the complete version-1 raw-count projection protocol')
    for key in ('context', 'experiment', 'observation_id', 'target_column', 'control_label', 'gene_id', 'gene_symbol'):
        engine._strings([cfg[key]], key)
    for key in ('selected_targets', 'selected_genes'):
        if type(cfg[key]) is not list:
            raise engine.Refused('Projection axes must be explicit ordered lists')
        engine._strings(cfg[key], key, unique=True)
    if len(cfg['selected_targets']) > 4096 or len(cfg['selected_genes']) > engine.MAX_GENES:
        raise engine.Refused('Projection panel exceeds its bounded axis allowance')
    if cfg['control_label'] in cfg['selected_targets']:
        raise engine.Refused('Controls must be distinct from the selected perturbations')
    mapping = cfg['target_mapping']
    if type(mapping) is not dict or not mapping:
        raise engine.Refused('Supply exact source annotation to target mappings')
    engine._strings(list(mapping), 'Source target annotations', unique=True)
    engine._strings(list(mapping.values()), 'Mapped targets')
    if set(mapping.values()) != {*cfg['selected_targets'], cfg['control_label']}:
        raise engine.Refused('Mappings must cover exactly the declared targets and control label')
    if type(cfg['cells_per_group']) is not int or not 0 <= cfg['cells_per_group'] <= engine.MAX_ROWS:
        raise engine.Refused('Cells per group must be zero (all) or a bounded positive count')
    if type(cfg['seed']) is not int or not 0 <= cfg['seed'] < 2**32:
        raise engine.Refused('Projection seed must be an unsigned 32-bit integer')
    return cfg


def project(inputs, params, output, check):
    import anndata as ad
    import h5py
    import pandas as pd
    cfg = protocol(inputs['protocol'])
    with h5py.File(inputs['matrix'], 'r') as source:
        matrix = bounded_h5.matrix(source, check)
        if getattr(matrix, 'format', None) == 'csc':
            raise engine.Refused('Projection supports dense or CSR input; CSC requires explicit conversion')
        nobs, nvars = matrix.shape
        obs, var = source['obs'], source['var']
        identities = engine._strings(bounded_h5.text(obs[cfg['observation_id']], nobs), 'Observation identities', unique=True)
        annotations = bounded_h5.text(obs[cfg['target_column']], nobs)
        gene_ids = engine._strings(bounded_h5.text(var[cfg['gene_id']], nvars), 'Source gene identities', unique=True)
        symbols = bounded_h5.text(var[cfg['gene_symbol']], nvars)
        positions = {}
        required = set(cfg['selected_genes'])
        for i, symbol in enumerate(symbols):
            if symbol in required:
                if symbol in positions:
                    raise engine.Refused('Selected gene symbols are ambiguous; no silent merge')
                positions[symbol] = i
        if set(positions) != required:
            raise engine.Refused('Every selected gene must exist; no missing gene is filled with zeros')
        missing = set(cfg['target_mapping']) - set(annotations)
        if missing:
            raise engine.Refused('A mapped source annotation is absent; correct the explicit protocol')
        groups = [*cfg['selected_targets'], cfg['control_label']]
        group_ids = {name: i for i, name in enumerate(groups)}
        labels = np.array([group_ids[cfg['target_mapping'][a]] if a in cfg['target_mapping'] else -1 for a in annotations])
        selected = np.zeros(nobs, dtype=bool)
        counts = []
        for i, name in enumerate(groups):
            check()
            candidates = np.flatnonzero(labels == i)
            size = cfg['cells_per_group'] or len(candidates)
            if not size or len(candidates) < size:
                raise engine.Refused('Every declared group needs the requested number of cells')
            if cfg['cells_per_group']:
                # Key by immutable cell identity, not source order or expression values.
                candidates = sorted(candidates, key=lambda row: (hashlib.sha256(json.dumps(
                    [cfg['seed'], cfg['context'], name, str(identities[row])], ensure_ascii=False,
                    separators=(',', ':')).encode()).digest(), str(identities[row])))[:size]
            selected[candidates] = True
            counts.append({'target': name, 'available': int(np.sum(labels == i)), 'selected': size})
        selected_count = int(selected.sum())
        columns = sorted(positions.values())
        order = [columns.index(positions[g]) for g in cfg['selected_genes']]
        engine._dense_budget(engine.BLOCK_ROWS, len(columns), copies=4)
        worst = selected_count * len(columns) * 8 + (selected_count + 1) * 8
        if shutil.disk_usage(output).free < worst * 2 + (512 << 20):
            raise engine.Refused('Free space for sparse projection chunks and assembled output')
        parts = []; nnz = 0
        expected_counts = hashlib.sha256()
        def observe(block, digest):
            # Per-row encoding keeps the witness independent of chunk boundaries.
            for row in range(block.shape[0]):
                a, b = block.indptr[row:row+2]
                digest.update(np.array([b-a], dtype='<i8').tobytes())
                digest.update(block.indices[a:b].astype('<i8').tobytes())
                digest.update(block.data[a:b].astype('<f4').tobytes())
        for start in range(0, nobs, engine.BLOCK_ROWS):
            check(); stop = min(start + engine.BLOCK_ROWS, nobs)
            chosen = np.flatnonzero(selected[start:stop])
            if not len(chosen):
                continue
            block = bounded_h5.row_block(matrix, start, stop, columns)
            block = sparse.csr_matrix(block)[chosen][:, order]
            engine._finite(block, counts=True)
            block = block.astype(np.float64)
            block.sum_duplicates(); block.eliminate_zeros(); block.sort_indices()
            engine._finite(block, counts=True)
            if block.nnz and np.any(block.data > 2**24):
                raise engine.Refused('Selected counts cannot be represented exactly as float32')
            nnz += block.nnz
            if nnz > params['maximum_stored_entries']:
                raise engine.Refused('Projection exceeds the declared stored-entry limit')
            block = block.astype(np.float32); observe(block, expected_counts)
            ids = chosen + start
            metadata = pd.DataFrame({'context': cfg['context'], 'target_gene': [groups[j] for j in labels[ids]],
                                     'source_annotation': annotations[ids]}, index=identities[ids])
            genes = pd.DataFrame({'source_gene_id': [gene_ids[positions[g]] for g in cfg['selected_genes']]}, index=cfg['selected_genes'])
            part = output / f'part_{len(parts)}.h5ad'
            ad.AnnData(X=block, obs=metadata, var=genes).write_h5ad(part, compression='gzip')
            parts.append(part)
        path = output / 'projected-counts.h5ad'
        engine._concat(parts, path, check)
        provenance = {'format': cfg['format'], 'protocol': cfg,
                      'protocolSha256': _digest(inputs['protocol'], check),
                      'sourceSha256': _digest(inputs['matrix'], check), 'sourceShape': list(matrix.shape),
                      'groups': counts, 'excludedUnmappedObservations': int(np.sum(labels < 0)),
                      'excludedBySampling': int(np.sum(labels >= 0)) - selected_count,
                      'sampling': 'all' if not cfg['cells_per_group'] else 'sha256-cell-identity-v1',
                      'omittedArrays': [k for k in ('raw', 'layers', 'obsm', 'varm', 'obsp', 'varp') if k in source],
                      'annotationProjection': 'only declared identity/target and gene identity fields; original source retained'}
    # Keep the recipe in the portable result, beyond its owning operation receipt.
    with h5py.File(path, 'r+') as saved:
        ad.io.write_elem(saved, 'uns', {'projection': json.dumps(provenance, sort_keys=True)})
    with engine._adata(path) as result:
        if (result.shape != (selected_count, len(columns)) or result.var_names.tolist() != cfg['selected_genes']
                or result.obs_names.tolist() != identities[selected].tolist()
                or result.obs['target_gene'].tolist() != [groups[j] for j in labels[selected]]
                or result.obs['context'].tolist() != [cfg['context']] * selected_count
                or result.obs['source_annotation'].tolist() != annotations[selected].tolist()
                or result.var['source_gene_id'].tolist() != [gene_ids[positions[g]] for g in cfg['selected_genes']]):
            raise engine.Refused('Projection assembly changed selected axes or annotations')
        actual_counts = hashlib.sha256()
        for _, block in engine._rows(result, check):
            block.sum_duplicates(); block.eliminate_zeros(); block.sort_indices(); observe(block, actual_counts)
        if actual_counts.digest() != expected_counts.digest():
            raise engine.Refused('Projection assembly changed selected count values')
    return path, {'format': 'raw-count-projection-result-v1', 'shape': [selected_count, len(columns)],
                  'storedEntries': nnz, 'rawCounts': True, 'sourceCellIdentity': 'preserved',
                  'groups': counts, 'sampling': provenance['sampling'],
                  'excludedUnmappedObservations': provenance['excludedUnmappedObservations'],
                  'excludedBySampling': provenance['excludedBySampling'],
                  'protocolSha256': provenance['protocolSha256'], 'sourceSha256': provenance['sourceSha256'],
                  'provenanceRetained': True, 'biologicalAccuracyEstablished': False}
