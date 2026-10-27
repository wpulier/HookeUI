"""Portable returns become fresh, untrusted candidates before comparison.

The source package never supplies an activation identity. Its portable contents are
validated first, then copied with a new identity and retained source provenance.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import tomllib
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog

from . import capability_workspace, kinds, own_trials, share


@dataclass(frozen=True)
class PreparedImport:
    path: Path
    lineage: dict[str, str]


def _toml(value) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and math.isfinite(value):
        return repr(value)
    if isinstance(value, list):
        return '[' + ', '.join(_toml(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{ ' + ', '.join(_toml(key) + ' = ' + _toml(item) for key, item in value.items()) + ' }'
    raise share.CantShare('The capability manifest contains an unsupported value; export it again from its authoring workspace')


def _checked_manifest(files: dict[str, bytes], id_: str, directory: Path) -> catalog.Manifest:
    folder = directory / id_
    folder.mkdir()
    for name, data in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    manifest, problems = catalog.read(folder / catalog.MANIFEST)
    if manifest is None:
        raise share.CantShare('This capability cannot be imported: ' + '; '.join(problems))
    if manifest.members:
        from gpu_worker import bundle_contract
        for member in bundle_contract.parse_members(list(manifest.members), manifest):
            if member.bridge == 'scientific-host-v1':
                # Metadata admission only. Its explicit preparation and confined
                # review route remain separate from executable operation proof.
                continue
            view = bundle_contract.view(manifest, member)
            why = kinds.takes_why(view)
            if why or kinds.implements(view) is None or not member.reference_cases:
                raise share.CantShare(f"{member.name} needs an integration before it can run: "
                                      + (why or "a supported picture operation with pinned reference checks is required"))
        return manifest
    if why := kinds.takes_why(manifest):
        raise share.CantShare('This needs an integration before it can run: ' + why
                             + '. Ask its author for a capability that accepts one 2D picture.')
    supported = {output for _, output in own_trials.JOBS.values()}
    if not any(item.input == 'image.2d.rgb8' and item.output in supported for item in manifest.implements):
        declared = ', '.join(f'{item.input} to {item.output}' for item in manifest.implements) or 'no picture result'
        raise share.CantShare(
            f'This capability declares {declared}. Guided import cannot compare this input and result yet. '
            'Ask its author for a separate 2D picture capability that returns cell labels, a table, text or an image, '
            'or wait for comparison support. Nothing has been installed.')
    return manifest


def prepare(path: Path, directory: Path, environ: Mapping[str, str] = os.environ) -> PreparedImport:
    """Validate and re-identify an upload without adding a channel or executing code."""
    shared, old_id, files = share._read(path)
    capability_workspace._owned_references(files[catalog.MANIFEST])
    if shared['format'] == share.FIJI_FORMAT:
        from . import fiji_sandbox
        if not fiji_sandbox.supported():
            raise share.CantShare('This computer cannot safely run shared Fiji capabilities: the OS sandbox is unavailable')
    source = shared.get('from') or old_id
    if (not isinstance(source, str) or len(source) > 256
            or not re.fullmatch(r'(?:[a-z0-9_-]+/)?[a-z0-9_-]+', source)):
        raise share.CantShare('The package has an invalid source identity; export it again from its authoring workspace')
    by = shared.get('by', '')
    if not isinstance(by, str) or len(by.encode()) > 1024:
        raise share.CantShare('The package attribution is too long; shorten it before exporting again')
    with tempfile.TemporaryDirectory(prefix='validate-', dir=directory) as temporary:
        manifest = _checked_manifest(files, old_id, Path(temporary))
        content = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        lineage = {'id': source, 'version': manifest.version, 'sha256': digest}
        fields = tomllib.loads(files[catalog.MANIFEST].decode())
        candidate_id = 'cap-' + uuid.uuid4().hex
        fields['id'] = candidate_id
        if manifest.contract or manifest.members:
            fields['derived_from'] = lineage
        for kind in ('needs_to_get', 'needs_to_run'):
            fields[kind] = [
                'install:' + candidate_id if step.startswith('install:') and step.removeprefix('install:').split('/')[-1] == old_id else step
                for step in fields.get(kind, [])
            ]
        if manifest.component:
            fields['licence'] = [candidate_id if name == old_id else name for name in fields['licence']]
        changed = '\n'.join(_toml(key) + ' = ' + _toml(value) for key, value in fields.items()) + '\n'
        candidate = Path(temporary) / candidate_id
        candidate.mkdir()
        for name, data in files.items():
            (candidate / name).parent.mkdir(parents=True, exist_ok=True)
            (candidate / name).write_bytes(changed.encode() if name == catalog.MANIFEST else data)
        copied, problems = catalog.read(candidate / catalog.MANIFEST)
        if copied is None:
            raise share.CantShare('The new candidate is incompatible: ' + '; '.join(problems))
        portable = share.pack(copied, by=by)
        result = directory / (candidate_id + share.SUFFIX)
        result.write_bytes(portable)
        # Legacy manifests keep their declared contract; provenance also travels in package metadata.
        with zipfile.ZipFile(result) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        metadata = json.loads(contents['SHARED.json'])
        metadata['imported_from'] = lineage
        if manifest.derived_from:
            metadata['source_lineage'] = manifest.derived_from
        metadata['from'] = source
        contents['SHARED.json'] = json.dumps(metadata).encode()
        with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in contents.items():
                archive.writestr(name, data)
        share._read(result)
        return PreparedImport(result, lineage)


def publish(prepared: PreparedImport, *, trial: str = '', environ: Mapping[str, str] = os.environ) -> dict:
    """Add only a community candidate and its comparison gate. No proof or activation."""
    if trial:
        own_trials.state(trial, environ)
    shared, id_, files = share._read(prepared.path)
    capability_workspace._owned_references(files[catalog.MANIFEST])
    with tempfile.TemporaryDirectory(prefix='validate-', dir=prepared.path.parent) as temporary:
        manifest = _checked_manifest(files, id_, Path(temporary))
    if trial and manifest.members:
        raise share.CantShare("This package contains multiple tools. Review each tool’s reference results after setup instead of attaching a single-tool comparison.")
    channel = share.channel_name(id_, files, shared['format'])
    from .bundle_admission import scientific as is_scientific
    scientific = is_scientific(manifest)
    own_trials.await_comparison(f'{channel}/{id_}', environ,
                               review_kind='scientific-review' if scientific else 'bundle-reference' if manifest.members else None)
    opened = share.open_file(prepared.path, environ)
    if trial:
        own_trials.prepare(opened['model'], trial, environ)
    return {**opened, 'name': shared.get('name') or id_, 'lineage': prepared.lineage, 'comparisonRequired': True,
            'says': ('The candidate was read. Allow its code, then prepare dependencies for private review. Its workflow remains unqualified.'
                     if scientific else 'The package was read. Review its permissions and checks here before enabling it.')}
