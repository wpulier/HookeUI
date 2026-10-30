"""Explicit reads of accepted numeric snapshots, including closed Projects. Never evaluates settings."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .datatables import DataTableRefused, MAX_SNAPSHOT_BYTES
from .job_outputs import Folder


def snapshot_path(record: dict, session: str, kind: str) -> str:
    """Validate Project ownership from the accepted owner-relative snapshot identity.

    Journal ``day`` records the calendar date of a save, not the owning Project.
    Legacy records have no explicit session; their immutable snapshot path binds
    ownership. New records additionally declare it. Derivatives also bind source.
    """
    if not isinstance(session, str) or not session or Path(session).name != session or session in ('.', '..'):
        raise DataTableRefused('invalid saved Project identity')
    if (type(record) is not dict or type(record.get('snapshot')) is not dict
            or kind not in ('dataset-table', 'analysis', 'graph')):
        raise DataTableRefused('the saved numeric record has an unsupported shape')
    rid, version = record.get('id'), record.get('version')
    import re
    pattern = r'tb[1-9][0-9]*' if kind == 'dataset-table' else r'(?:an|gr)[1-9][0-9]*'
    if (not isinstance(rid, str) or not re.fullmatch(pattern, rid)
            or type(version) is not int or version < 1
            or record.get('session', session) != session):
        raise DataTableRefused('the saved table identity disagrees with its Project')
    relative = f'{session}/{rid}/v{version}.json'
    if record.get('snapshot', {}).get('path') != relative:
        raise DataTableRefused('the saved table path disagrees with its Project and version')
    if kind != 'dataset-table' and (type(record.get('source')) is not dict
            or record['source'].get('session') != session or type(record.get('spec')) is not dict):
        raise DataTableRefused('the saved analysis or graph source belongs to another Project')
    if not isinstance(record['snapshot'].get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', record['snapshot']['sha256']):
        raise DataTableRefused('the saved numeric data has no accepted hash')
    return relative


def read_accepted(root: Path, record: dict, session: str, kind: str, *, check=lambda: None) -> dict:
    """A journal-selected identity/hash is authority; no live store is rebound to a historical Project."""
    relative = snapshot_path(record, session, kind)
    rid, version = record['id'], record['version']
    try:
        # The same descriptor supplies bytes and their hash. Reuse the bounded
        # retained-output reader so FIFOs cannot hang opening and directory/file
        # aliases cannot redirect reads between a path check and open.
        with Folder(Path(root)) as folder:
            folder.check = check
            data = folder.read(relative, max_bytes=MAX_SNAPSHOT_BYTES)
        if hashlib.sha256(data).hexdigest() != record['snapshot']['sha256']:
            raise DataTableRefused('the saved numeric data no longer matches its accepted hash')
        snapshot = json.loads(data)
    except (OSError, ValueError) as exc:
        raise DataTableRefused(f'accepted numeric data unavailable: {exc}', next='restore its saved file') from exc
    if type(snapshot) is not dict:
        raise DataTableRefused('accepted numeric data must be an object')
    if kind == 'dataset-table':
        valid = snapshot.get('id') == rid and snapshot.get('version') == version
    else:
        valid = (snapshot.get('format') == 1 and snapshot.get('kind') == kind
                 and snapshot.get('source') == record['source'] and snapshot.get('spec') == record['spec']
                 and record['source']['session'] == session)
    if not valid:
        raise DataTableRefused('accepted numeric data disagrees with its recorded identity and settings')
    return {'record': {key: value for key, value in record.items() if key != 'snapshot'},
            'sha256': record['snapshot']['sha256'], 'snapshot': snapshot}


def page(value: dict, *, offset=0, digest=None) -> str:
    """Stable character paging shared with explicit numeric agent reads; numbers remain lossless JSON."""
    from .resources import READ_MAX
    from .untrusted import block
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    current = hashlib.sha256(text.encode()).hexdigest()
    if type(offset) is not int or not 0 <= offset <= len(text) or offset and not digest:
        raise DataTableRefused('continuation needs its original content digest and offset')
    if digest is not None and digest != current:
        raise DataTableRefused('the accepted reading changed; restart at offset 0')
    end = min(len(text), offset + READ_MAX)
    header = {'offset': offset, 'nextOffset': end if end < len(text) else None, 'totalChars': len(text), 'digest': current}
    return 'Accepted numeric result page: ' + json.dumps(header) + '\n' + block(text[offset:end], source='saved numeric result')
