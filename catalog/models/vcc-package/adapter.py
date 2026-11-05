"""Package a complete final prediction with the official `vcc prep` (vcc-cli 0.3.0), with a measured memory forecast.

1. Preflight reads the entry's structure and labels (bounded), and forecasts official prep's peak memory
   from its stored entries, using the ratios measured on this kind of Mac for three full VCC 2026 entries
   (about 2e9 entries each: peak RSS 10.4-12.0 GB, peak memory footprint 32.0-32.2 GB). The forecast is
   admitted against the worker's RSS ceiling and the computer's physical memory, before anything loads.
2. The official `vcc.prep.run_prep` (the function `vcc prep` runs) packages it, its temporary files in
   this run's own folder.
3. The package is decompressed (bounded) and its counts, gene axis and context/target rows are compared
   with the input's, hash for hash. Nothing is uploaded or submitted.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import time

import numpy as np

ROWS_PER_BLOCK = 4096
MAX_CELL_TOTAL = 1e6
# Measured: payload = stored entries x 8 bytes (float32 + int32) + row pointers; vcc prep 0.3.0 peaks at
# about 2.02x payload (memory footprint) and 0.75x payload (resident). Rounded up, plus 1 GiB of overhead.
FOOTPRINT_PER_PAYLOAD, RSS_PER_PAYLOAD = 2.10, 0.80
MEASURED = [{'entry': 'm1', 'storedEntries': 1992361520, 'peakRssBytes': 11968741376, 'peakFootprintBytes': 32072850472},
            {'entry': 'c2', 'storedEntries': 1989390598, 'peakRssBytes': 11452612608, 'peakFootprintBytes': 32012131296},
            {'entry': 'c3', 'storedEntries': 1990799782, 'peakRssBytes': 11014340608, 'peakFootprintBytes': 32034790416}]


class Refused(ValueError):
    pass


def _csv(path, column):
    import pandas as pd
    frame = pd.read_csv(path)
    if column not in frame or frame[column].isna().any():
        raise Refused(f'The CSV needs a complete {column} column')
    return frame[column].astype(str).tolist()


def _digest(path, check):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        while part := stream.read(1 << 24):
            check()
            h.update(part)
    return h.hexdigest()


def _text(node):
    """An AnnData string or categorical column, decoded."""
    import h5py
    if isinstance(node, h5py.Group) and node.attrs.get('encoding-type') == 'nullable-string-array':
        if node['mask'][:].any():
            raise Refused('Names and labels must not be missing')
        node = node['values']
    elif isinstance(node, h5py.Group):
        categories = np.array([c.decode() if isinstance(c, bytes) else str(c) for c in node['categories'][:]])
        return categories[node['codes'][:]]
    return np.array([c.decode() if isinstance(c, bytes) else str(c) for c in node[:]])


def _structure(file, genes, rows):
    node = file['X']
    if (node.attrs.get('encoding-type') != 'csr_matrix' or tuple(node.attrs.get('shape', ())) != (rows, len(genes))
            or set(node) != {'data', 'indices', 'indptr'} or node['data'].dtype != np.dtype('float32')):
        raise Refused(f'Packaging needs the complete final shape {(rows, len(genes))} as CSR float32 raw counts')
    var = file['var']
    if _text(var[var.attrs['_index']]).tolist() != genes:
        raise Refused('Use the exact ordered official gene axis; nothing is reordered')
    obs = file['obs']
    return node, _text(obs['context']), _text(obs['target_gene'])


def _count_hashes(node, check):
    """Content hashes of the counts that do not depend on index width; checks counts and cell totals."""
    hashes = [hashlib.sha256() for _ in range(3)]
    indptr = node['indptr'][:].astype(np.int64)
    largest = 0
    for start in range(0, len(indptr) - 1, ROWS_PER_BLOCK):
        check()
        stop = min(start + ROWS_PER_BLOCK, len(indptr) - 1)
        a, b = int(indptr[start]), int(indptr[stop])
        data = node['data'][a:b]
        if not np.isfinite(data).all() or (data < 0).any() or (data != np.rint(data)).any():
            raise Refused('Counts must be finite, nonnegative whole numbers')
        lengths = np.diff(indptr[start:stop + 1])
        totals = np.add.reduceat(data.astype(np.float64), (indptr[start:stop] - a)) if b > a else np.zeros(stop - start)
        totals[lengths == 0] = 0
        if (totals <= 0).any() or (totals > MAX_CELL_TOTAL).any():
            raise Refused('Each cell needs positive counts within the official 1e6 limit')
        largest = max(largest, int(totals.max()))
        for h, value in zip(hashes, (data.astype('<f4'), node['indices'][a:b].astype('<i8'), lengths.astype('<i8'))):
            h.update(value.tobytes())
    return {'storedEntries': int(indptr[-1]), 'maximumCellCounts': largest,
            'countHashes': [h.hexdigest() for h in hashes]}


def _labels_hash(contexts, labels):
    h = hashlib.sha256()
    for context, target in zip(contexts, labels):
        h.update((context + '\0' + target + '\n').encode())
    return h.hexdigest()


def _limits():
    """The worker's limits, as the folder worker configured them (its resource profile is already loaded)."""
    import sys
    profile = sys.modules.get('resource_profile')
    return profile.current() if profile is not None else {'memory_bytes': 24 << 30}


def forecast(stored_entries, rows):
    payload = stored_entries * 8 + (rows + 1) * 8
    return {'csrPayloadBytes': payload,
            'peakFootprintBytes': math.ceil(payload * FOOTPRINT_PER_PAYLOAD) + (1 << 30),
            'peakRssBytes': math.ceil(payload * RSS_PER_PAYLOAD) + (1 << 30),
            'temporaryDiskBytes': payload * 2 + (2 << 30),
            'basis': 'ratios measured for vcc prep 0.3.0 on three full VCC 2026 entries on this Mac',
            'measured': MEASURED}


def run(inputs, params, output, check):
    import h5py
    from importlib.metadata import version
    if version('vcc-cli') != '0.3.0':
        raise Refused('Install the pinned official vcc-cli 0.3.0 packaging runtime')
    started = time.monotonic()
    manifest = json.loads(Path(inputs['manifest']).read_text())
    contexts, per = list(manifest['contexts']), int(manifest['cells_per_pert'])
    genes, targets = _csv(inputs['genes'], 'gene_name'), _csv(inputs['perts'], 'target_gene')
    if manifest.get('n_genes') != len(genes) or manifest.get('n_constructs') != len(targets):
        raise Refused('The manifest does not describe these gene and target files')
    rows = len(contexts) * len(targets) * per
    with h5py.File(inputs['matrix'], 'r') as file:
        node, ctx, lab = _structure(file, genes, rows)
        expected_rows = (np.repeat(contexts, len(targets) * per), np.tile(np.repeat(targets, per), len(contexts)))
        if not ((ctx == expected_rows[0]).all() and (lab == expected_rows[1]).all()):
            raise Refused(f'Every context x target needs exactly {per} cells, in panel order, without controls')
        stored = int(node['indptr'][-1])
        plan = forecast(stored, rows)
        limits = _limits()
        if plan['peakRssBytes'] > limits['memory_bytes']:
            raise Refused(f"Official prep would reach about {plan['peakRssBytes'] / 2**30:.1f} GiB resident, over the worker's "
                          f"{limits['memory_bytes'] / 2**30:.0f} GiB ceiling")
        physical = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
        if plan['peakFootprintBytes'] > physical:
            raise Refused(f"Official prep would need about {plan['peakFootprintBytes'] / 2**30:.1f} GiB of memory; "
                          f"this computer has {physical / 2**30:.1f} GiB")
        if shutil.disk_usage(output).free < plan['temporaryDiskBytes']:
            raise Refused(f"Reserve about {plan['temporaryDiskBytes'] / 2**30:.0f} GiB of disk for official prep")
        before = _count_hashes(node, check)
    labels = _labels_hash(ctx, lab)
    preflighted = time.monotonic()
    from vcc.prep import run_prep
    from vcc.panel import perts_fingerprint
    path = output / 'prediction.vcc'
    prior_tmp, prior_env = tempfile.tempdir, os.environ.get('TMPDIR')
    with tempfile.TemporaryDirectory(dir=output, prefix='official-prep-') as scratch:
        try:
            tempfile.tempdir = scratch
            os.environ['TMPDIR'] = scratch
            check()
            result = run_prep(input_path=str(inputs['matrix']), genes_path=str(inputs['genes']),
                              perts_path=str(inputs['perts']), output_path=str(path),
                              required_contexts=tuple(contexts), panel_id=manifest['panel_id'])
        finally:
            tempfile.tempdir = prior_tmp
            if prior_env is None:
                os.environ.pop('TMPDIR', None)
            else:
                os.environ['TMPDIR'] = prior_env
        packaged = time.monotonic()
        if result.dry_run or not result.verified_targets or result.nnz != before['storedEntries']:
            raise Refused('Official prep did not confirm the complete panel')
        import zstandard
        checked = Path(scratch) / 'independent.h5ad'
        with tarfile.open(path, 'r:') as archive:
            names = sorted(m.name for m in archive.getmembers())
            if names != ['meta.json', 'pred.h5ad.zst'] or not all(m.isfile() for m in archive.getmembers()):
                raise Refused(f'The official package holds {names}')
            meta = json.loads(archive.extractfile('meta.json').read(64 << 10))
            wanted = {'nnz': before['storedEntries'], 'n_obs': rows, 'n_vars': len(genes), 'cli_version': '0.3.0',
                      'contexts': contexts, 'panel_id': manifest['panel_id'],
                      'perts_fingerprint': perts_fingerprint(str(inputs['perts']))}
            if any(meta.get(k) != v for k, v in wanted.items()):
                raise Refused('The official package metadata differs from the admitted panel')
            budget, total = plan['csrPayloadBytes'] + (1 << 30), 0
            with archive.extractfile('pred.h5ad.zst') as compressed, \
                    zstandard.ZstdDecompressor().stream_reader(compressed) as stream, checked.open('xb') as out:
                while body := stream.read(1 << 22):
                    check()
                    total += len(body)
                    if total > budget:
                        raise Refused('The decoded package exceeds its admitted size')
                    out.write(body)
        with h5py.File(checked, 'r') as file:
            node, ctx2, lab2 = _structure(file, genes, rows)
            after = _count_hashes(node, check)
        if after != before or _labels_hash(ctx2, lab2) != labels:
            raise Refused('Official packaging changed counts or context/target rows')
    verified = time.monotonic()
    return path, {'format': 'vcc-package-v1', 'officialCliVersion': '0.3.0', 'panelId': manifest['panel_id'],
                  'contexts': contexts, 'packageSha256': _digest(path, check), 'packageBytes': path.stat().st_size,
                  'inputSha256': _digest(inputs['matrix'], check), 'counts': before, 'contextTargetRowsSha256': labels,
                  'forecast': plan, 'independentlyCheckedPackagedCounts': True, 'submitted': False,
                  'seconds': {'preflight': preflighted - started, 'officialPrep': packaged - preflighted,
                              'independentCheck': verified - packaged},
                  'meaning': 'packaged by the official vcc prep and checked; nothing uploaded. The forecast is an admission guard; workerUsage measures this run.'}
