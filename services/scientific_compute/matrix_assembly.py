"""Bounded raw-count row assembly; no implicit joins, relabelling or hidden data loss."""
from contextlib import ExitStack
import hashlib
import shutil
import numpy as np
from scipy import sparse

if __package__:
    from . import engine, bounded_h5
else:
    import engine, bounded_h5


def concatenate(inputs, params, output, check):
    import anndata
    import pandas as pd
    import h5py
    columns = [params['context_column'], params['target_column']]
    if columns[0] == columns[1]:
        raise engine.Refused('Context and target need distinct annotation columns')
    with ExitStack() as stack:
        sources = [stack.enter_context(h5py.File(inputs[key], 'r')) for key in ('left', 'right')]
        matrices = []; names = []; labels = []; gene_axes = []
        for file in sources:
            if 'raw' in file or any(name in file and len(file[name]) for name in ('layers', 'obsm', 'varm', 'obsp', 'varp', 'uns')):
                raise engine.Refused('Count assembly cannot discard extra array annotations')
            matrix = bounded_h5.matrix(file, check)
            if getattr(matrix, 'format', None) == 'csc':
                raise engine.Refused('Count assembly supports dense or CSR storage; CSC needs explicit conversion')
            obs, var = file['obs'], file['var']
            oi, vi = obs.attrs.get('_index'), var.attrs.get('_index')
            if (not isinstance(oi, str) or not isinstance(vi, str)
                    or set(obs.keys()) != {oi, *columns} or set(var.keys()) != {vi}):
                raise engine.Refused('Count assembly cannot discard extra annotations or guess index columns')
            names.append(engine._strings(bounded_h5.text(obs[oi], matrix.shape[0]), 'Cell identities', unique=True))
            gene_axes.append(engine._strings(bounded_h5.text(var[vi], matrix.shape[1]), 'Gene axis', unique=True).tolist())
            labels.append([bounded_h5.text(obs[column], matrix.shape[0]) for column in columns])
            matrices.append(matrix)
        genes = gene_axes[0]
        if genes != gene_axes[1]:
            raise engine.Refused('Count assembly requires identical ordered gene axes')
        count = sum(matrix.shape[0] for matrix in matrices)
        if count > engine.MAX_ROWS:
            raise engine.Refused('Count assembly exceeds the observation allowance')
        engine._dense_budget(engine.BLOCK_ROWS, len(genes), copies=4)
        if set(names[0]) & set(names[1]):
            raise engine.Refused('Duplicate cell identities cannot be silently renamed')
        worst = count * len(genes) * 8 + (count + 1) * 8
        if shutil.disk_usage(output).free < worst * 2 + (512 << 20):
            raise engine.Refused('Free space for count assembly chunks and merged output')
        parts = []; nnz = 0; largest = 0
        expected = [hashlib.sha256() for _ in range(3)]
        def observe(block, digests):
            block.sum_duplicates(); block.eliminate_zeros(); block.sort_indices()
            for digest, value in zip(digests, (block.data.astype('<f4'), block.indices.astype('<i8'), np.diff(block.indptr).astype('<i8'))):
                digest.update(value.tobytes())
        for matrix, identities, (contexts, targets) in zip(matrices, names, labels):
            for start, end in bounded_h5.row_ranges(matrix, engine.BLOCK_ROWS, check):
                block = bounded_h5.row_block(matrix, start, end, list(range(len(genes))))
                engine._finite(block, counts=True)
                block = sparse.csr_matrix(block, dtype=np.float64)
                if block.nnz and np.any(block.data > 2 ** 24):
                    raise engine.Refused('Counts cannot be represented exactly as float32 whole values')
                depth = np.asarray(block.sum(axis=1)).ravel()
                largest = max(largest, int(depth.max()))
                if largest > params['maximum_cell_counts']:
                    raise engine.Refused('A cell exceeds the declared count limit')
                block.sum_duplicates(); block.eliminate_zeros()
                if block.nnz and np.any(block.data > 2 ** 24):
                    raise engine.Refused('Summed duplicate entries exceed exact float32 counts')
                observe(block, expected); nnz += block.nnz
                if nnz > params['maximum_stored_entries']:
                    raise engine.Refused('Assembled counts exceed the declared stored-entry limit')
                end = start + block.shape[0]
                obs = pd.DataFrame({columns[0]: contexts[start:end], columns[1]: targets[start:end]}, index=identities[start:end])
                part = output / f'chunk_{len(parts)}.h5ad'
                anndata.AnnData(X=block.astype(np.float32), obs=obs, var=pd.DataFrame(index=genes)).write_h5ad(part, compression='gzip')
                parts.append(part)
        path = output / 'matrix.h5ad'
        engine._concat(parts, path, check)
        # Verify actual assembled values/identities before publication, not just planned dimensions.
        with engine._adata(path) as result:
            if result.shape != (count, len(genes)) or result.var_names.tolist() != genes:
                raise engine.Refused('Count assembly changed dimensions or gene order')
            if not np.array_equal(result.obs_names.to_numpy(dtype=str), np.concatenate(names)):
                raise engine.Refused('Count assembly changed cell identities')
            actual_labels = engine._columns(result, params)
            if any(not np.array_equal(actual_labels[i], np.concatenate([pair[i] for pair in labels])) for i in (0, 1)):
                raise engine.Refused('Count assembly changed context or target labels')
            actual_nnz = 0; actual_max = 0
            actual = [hashlib.sha256() for _ in range(3)]
            for _, block in engine._rows(result, check):
                observe(block, actual)
                actual_nnz += block.nnz
                actual_max = max(actual_max, int(np.asarray(block.sum(axis=1)).max()))
            if actual_nnz != nnz or actual_max != largest or [h.digest() for h in actual] != [h.digest() for h in expected]:
                raise engine.Refused('Count assembly changed stored counts')
        return path, {'format': 'annotated-count-assembly-v1', 'shape': [count, len(genes)],
            'storedEntries': nnz, 'maximumCellCounts': largest, 'rawCounts': True, 'dtype': 'float32',
            'geneAxisSha256': hashlib.sha256('\n'.join(genes).encode()).hexdigest(),
            'cellIdentity': 'preserved-unique', 'contextTargetLabels': 'preserved',
            'assembly': 'anndata-concat-on-disk', 'explicitZerosRemoved': True,
            'packagedForSubmission': False, 'fullShapeQualified': False}
