"""Official offline VCC packaging after independent, bounded count validation.

Only the admitted final-panel files are used. No login, upload, or submission API
is imported. Official prep runs in this same resource-confined worker process.
"""
from collections import Counter
import csv
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import time

import numpy as np

if __package__:
    from . import bounded_h5, engine, resource_profile
else:
    import bounded_h5, engine, resource_profile

CONTEXTS = ('D', 'E', 'F')
GENES = 18_533
TARGETS = 300
CELLS = 400
MAX_NNZ = 4_750_000_000
MAX_COUNTS = 1_000_000
METADATA = 512 << 20


def _digest(path, check):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while body := stream.read(1 << 20):
            check(); h.update(body)
    return h.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise engine.Refused('Duplicate keys in the final-panel manifest')
        result[key] = value
    return result


def _csv(path, header, count):
    if path.stat().st_size > 512 << 10:
        raise engine.Refused('Panel metadata exceeds its bounded allowance')
    rows = list(csv.reader(path.read_text(encoding='utf-8').splitlines(), strict=True))
    if (not rows or rows[0] != [header] or len(rows) != count + 1
            or any(len(row) != 1 or not row[0] or len(row[0]) > 256 for row in rows[1:])):
        raise engine.Refused('Use the exact final-panel gene and perturbation CSV files')
    values = [row[0] for row in rows[1:]]
    if len(set(values)) != count or 'non-targeting' in values:
        raise engine.Refused('Panel axes must be unique and exclude controls')
    return values


def _panel(inputs):
    path = inputs['manifest']
    if path.stat().st_size > 256 << 10:
        raise engine.Refused('Final-panel manifest exceeds its metadata allowance')
    manifest = json.loads(path.read_text(), object_pairs_hook=_unique_object)
    expected = {'season': '2026', 'partition': 'test', 'panel_id': 'vcc2026-test-1',
                'contexts': list(CONTEXTS), 'pert_col': 'target_gene', 'context_col': 'context',
                'control_label': 'non-targeting', 'n_genes': GENES,
                'n_constructs': TARGETS, 'cells_per_pert': CELLS}
    if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in expected.items()):
        raise engine.Refused('Packaging requires the frozen 2026 final D/E/F panel manifest')
    return _csv(inputs['genes'], 'gene_name', GENES), _csv(inputs['perts'], 'target_gene', TARGETS), manifest


def _plain_tree(group, depth=0, seen=None):
    """Reject links/extra allocations before the upstream whole-file decoder."""
    import h5py
    seen = set() if seen is None else seen
    if depth > 4 or len(seen) > 64:
        raise engine.Refused('Unsupported H5AD annotation structure')
    for name in group:
        if not isinstance(group.get(name, getlink=True), h5py.HardLink):
            raise engine.Refused('Packaging cannot follow external or symbolic HDF5 links')
        node = group[name]
        identity = h5py.h5o.get_info(node.id).addr
        if identity in seen:
            raise engine.Refused('Packaging cannot follow repeated HDF5 objects')
        seen.add(identity)
        if isinstance(node, h5py.Group):
            _plain_tree(node, depth + 1, seen)


def _envelope(node, rows):
    nnz = node['data'].shape[0]
    # A materialized SciPy CSR may promote narrow on-disk indices at 2**31.
    width = max(node['indices'].dtype.itemsize, 8 if nnz > 2 ** 31 - 1 else 4)
    payload = nnz * (4 + width) + (rows + 1) * max(8, node['indptr'].dtype.itemsize)
    # More conservative than upstream's 1.3x no-copy estimate; still an estimate,
    # backed by the worker's independent RSS/address-space limit.
    memory = math.ceil(payload * 1.5) + (2 << 30)
    raw = payload + METADATA
    compressed = raw + raw // 50 + (1 << 20)
    return {'storedEntries': nnz, 'csrPayloadBytes': payload, 'estimatedPeakMemoryBytes': memory,
            'maximumDecodedBytes': raw, 'estimatedTemporaryBytes': raw + 2 * compressed + METADATA,
            'estimatedMaximumFileBytes': compressed + (1 << 20)}


def validate(path, genes, targets, output, check, *, admit_prep=True):
    """Stream every count; only bounded labels and sparse blocks are decoded."""
    import h5py
    rows = len(CONTEXTS) * len(targets) * CELLS
    with h5py.File(path, 'r') as file:
        _plain_tree(file)
        if set(file) - {'X', 'obs', 'var', 'obsm', 'varm', 'obsp', 'varp', 'uns', 'layers'}:
            raise engine.Refused('Packaging refuses additional arrays instead of loading or dropping them')
        if any(key in file and len(file[key]) for key in ('obsm', 'varm', 'obsp', 'varp', 'uns', 'layers')):
            raise engine.Refused('Packaging refuses additional array annotations')
        node = file['X']
        if (not isinstance(node, h5py.Group) or node.attrs.get('encoding-type') != 'csr_matrix'
                or tuple(node.attrs.get('shape', ())) != (rows, len(genes))
                or set(node) != {'data', 'indices', 'indptr'}
                or any(not isinstance(node[k], h5py.Dataset) or len(node[k].shape) != 1 for k in node)
                or node['data'].dtype != np.dtype('float32')):
            raise engine.Refused('Packaging requires the complete final shape in CSR float32 raw counts')
        envelope = _envelope(node, rows)
        if envelope['storedEntries'] > MAX_NNZ:
            raise engine.Refused('Stored matrix entries exceed the official density limit')
        limits = resource_profile.current()
        if admit_prep and envelope['estimatedPeakMemoryBytes'] > limits['materialization_bytes']:
            raise engine.Refused('Official prep materialization exceeds the explicit worker memory allowance')
        if admit_prep and envelope['estimatedMaximumFileBytes'] > limits['file_bytes']:
            raise engine.Refused('Official prep temporary/output files exceed the worker file allowance')
        if admit_prep and shutil.disk_usage(output).free < envelope['estimatedTemporaryBytes']:
            raise engine.Refused('Reserve official prep temporary H5AD, compression and package space')
        matrix = bounded_h5.matrix(file, check)
        obs, var = file['obs'], file['var']
        oi, vi = obs.attrs.get('_index'), var.attrs.get('_index')
        if (not isinstance(oi, str) or not isinstance(vi, str)
                or set(obs) != {oi, 'context', 'target_gene'} or set(var) != {vi}):
            raise engine.Refused('Packaging requires only cell identity, context, target and gene annotations')
        axis = bounded_h5.text(var[vi], len(genes), compact=True).tolist()
        if axis != genes:
            raise engine.Refused('Use the exact ordered official gene axis before packaging; no implicit reorder')
        identities = bounded_h5.text(obs[oi], rows, compact=True).tolist()
        if len(set(identities)) != rows:
            raise engine.Refused('Cell identities must be unique')
        contexts = bounded_h5.text(obs['context'], rows, compact=True)
        labels = bounded_h5.text(obs['target_gene'], rows, compact=True)
        groups = Counter(zip(contexts, labels))
        if groups != Counter({(context, target): CELLS for context in CONTEXTS for target in targets}):
            raise engine.Refused('Every D/E/F target needs exactly 400 cells, without control or extra rows')
        hashes = [hashlib.sha256() for _ in range(3)]
        largest = 0
        for start, end in bounded_h5.row_ranges(matrix, engine.BLOCK_ROWS, check):
            block = bounded_h5.row_block(matrix, start, end, list(range(len(genes))))
            engine._finite(block, counts=True)
            if not block.has_canonical_format:
                raise engine.Refused('CSR indices must be sorted and unique before packaging')
            depths = np.asarray(block.sum(axis=1, dtype=np.float64)).ravel()
            if np.any(depths <= 0) or np.any(depths > MAX_COUNTS):
                raise engine.Refused('Each submitted cell needs positive counts within the official limit')
            largest = max(largest, int(depths.max()))
            for h, value in zip(hashes, (block.data.astype('<f4'), block.indices.astype('<i8'), np.diff(block.indptr).astype('<i8'))):
                h.update(value.tobytes())
        label_hash = hashlib.sha256()
        for context, target in zip(contexts, labels):
            label_hash.update((str(context) + '\0' + str(target) + '\n').encode())
    return {**envelope, 'shape': [rows, len(genes)], 'maximumCellCounts': largest,
            'canonicalCountHashes': [h.hexdigest() for h in hashes],
            'contextTargetRowsSha256': label_hash.hexdigest()}


def package(inputs, params, output, check):
    if params or set(inputs) != {'matrix', 'genes', 'perts', 'manifest'}:
        raise engine.Refused('Packaging takes exact admitted matrix and final-panel files without rule overrides')
    if version('vcc-cli') != '0.3.0':
        raise engine.Refused('Install the pinned official vcc-cli 0.3.0 packaging runtime')
    started = time.monotonic()
    genes, targets, manifest = _panel(inputs)
    before = validate(inputs['matrix'], genes, targets, output, check)
    validated = time.monotonic()
    input_hashes = {key: _digest(path, check) for key, path in inputs.items()}
    # Use the exact function invoked by `vcc prep`, in this monitored process.
    from vcc.prep import run_prep
    from vcc.panel import perts_fingerprint
    prep_started = time.monotonic()
    path = output / 'prediction.vcc'
    prior_tmp, prior_env = tempfile.tempdir, os.environ.get('TMPDIR')
    with tempfile.TemporaryDirectory(dir=output, prefix='official-prep-') as scratch:
        try:
            tempfile.tempdir = scratch
            os.environ['TMPDIR'] = scratch
            check()
            result = run_prep(input_path=str(inputs['matrix']), genes_path=str(inputs['genes']),
                              perts_path=str(inputs['perts']), output_path=str(path),
                              required_contexts=CONTEXTS, panel_id=manifest['panel_id'])
        finally:
            tempfile.tempdir = prior_tmp
            if prior_env is None:
                os.environ.pop('TMPDIR', None)
            else:
                os.environ['TMPDIR'] = prior_env
        packaged = time.monotonic()
        if result.dry_run or not result.verified_targets or result.nnz != before['storedEntries']:
            raise engine.Refused('Official prep did not confirm the complete validated panel')
        # Inspect and decompress the actual archive with strict bounds, never extract paths.
        import zstandard
        checked = Path(scratch) / 'independent.h5ad'
        with tarfile.open(path, 'r:') as archive:
            members = []
            for member in archive:
                if len(members) >= 2 or not member.isfile() or member.name not in {'meta.json', 'pred.h5ad.zst'}:
                    raise engine.Refused('Official package has unexpected archive members')
                members.append(member)
            if {m.name for m in members} != {'meta.json', 'pred.h5ad.zst'}:
                raise engine.Refused('Official package is incomplete')
            meta_member = archive.getmember('meta.json')
            if meta_member.size > 64 << 10:
                raise engine.Refused('Official package metadata is excessive')
            meta = json.loads(archive.extractfile(meta_member).read(), object_pairs_hook=_unique_object)
            expected = {'nnz': before['storedEntries'], 'n_obs': before['shape'][0], 'n_vars': len(genes),
                        'cli_version': '0.3.0', 'contexts': list(CONTEXTS), 'panel_id': manifest['panel_id'],
                        'perts_fingerprint': perts_fingerprint(str(inputs['perts']))}
            if not isinstance(meta, dict) or any(meta.get(key) != value for key, value in expected.items()):
                raise engine.Refused('Official package metadata differs from the admitted panel')
            if archive.getmember('pred.h5ad.zst').size > before['estimatedMaximumFileBytes']:
                raise engine.Refused('Official compressed payload exceeds its admitted size')
            total = 0
            with archive.extractfile('pred.h5ad.zst') as compressed, zstandard.ZstdDecompressor().stream_reader(compressed) as stream, checked.open('xb') as destination:
                while body := stream.read(1 << 20):
                    check(); total += len(body)
                    if total > before['maximumDecodedBytes']:
                        raise engine.Refused('Decoded official package exceeds its admitted size')
                    destination.write(body)
        after = validate(checked, genes, targets, output, check, admit_prep=False)
        for key in ('shape', 'storedEntries', 'maximumCellCounts', 'canonicalCountHashes', 'contextTargetRowsSha256'):
            if after[key] != before[key]:
                raise engine.Refused('Official packaging changed counts or context/target row meaning')
    verified = time.monotonic()
    package_hash = _digest(path, check)
    hashed = time.monotonic()
    return path, {'format': 'vcc-final-package-v1', 'officialCliVersion': '0.3.0',
                  'panelId': manifest['panel_id'], 'contexts': list(CONTEXTS),
                  'inputSha256': input_hashes, 'packageSha256': package_hash,
                  'packageBytes': path.stat().st_size, 'validation': before,
                  'independentlyCheckedPackagedCounts': True, 'packagedForSubmission': True,
                  'submitted': False, 'organizerAccepted': False, 'fullShapeResourceQualified': False,
                  'seconds': {'preflight': validated - started, 'inputHashing': prep_started - validated,
                              'officialPrep': packaged - prep_started,
                              'independentArchiveCheck': verified - packaged,
                              'packageHashing': hashed - verified, 'total': hashed - started},
                  'resourceMeaning': 'Memory/disk estimates are admission guards; worker telemetry measures actual usage. storedEntries is X entries, not filesystem file count.'}
