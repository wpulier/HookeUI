"""Check accepted researcher metadata before sealing a portable folder copy.

No manager is started and no saved work is replayed. Historical author strings
remain evidence. This verifies existing metadata identities, not all external
research dependencies or scientific correctness.
"""
import hashlib
import json
import os
import re
import stat

from .project_roots import ProjectLocationError
from .research.findings import payload_digest
from .research.storage import MAX_RECORD, ResearchStorage
from .scientific_projects import ProjectStore

MAX_RECORDS = 100_000
MAX_BYTES = 256 << 20


def _children(folder, check):
    if not stat.S_ISDIR(folder.lstat().st_mode):
        raise ProjectLocationError('Retained researcher storage must be a regular directory')
    count = 0
    with os.scandir(folder) as children:
        for child in children:
            check()
            count += 1
            if count > MAX_RECORDS:
                raise ProjectLocationError('Research verification exceeds its directory allowance')
            yield folder / child.name


def _identity(record, session, date, rid):
    if (type(record) is not dict or record.get('session') != session
            or record.get('day') != date or record.get('rid') != rid):
        raise ProjectLocationError('Retained researcher metadata does not match its Project, date and identity')
    findings = record.get('findingRecords', [])
    if type(findings) is not list:
        raise ProjectLocationError('Retained finding history is unreadable')
    declared = record.get('findingRevisions', [])
    if type(declared) is not list or len(declared) != len(findings):
        raise ProjectLocationError('Retained finding payloads do not match their declared history')
    for number, finding in enumerate(findings, 1):
        if (type(finding) is not dict or type(finding.get('version')) is not int
                or finding['version'] != number
                or not all(key in finding for key in ('result', 'evidence', 'missingEvidenceReads', 'digest', 'at', 'complete'))):
            raise ProjectLocationError('Retained finding revision is unreadable')
        if declared[number - 1] != {key: finding[key] for key in ('version', 'digest', 'at', 'complete')}:
            raise ProjectLocationError('Retained finding payloads do not match their declared history')
        payload = {key: finding[key] for key in ('result', 'evidence', 'missingEvidenceReads')}
        if payload_digest(payload) != finding.get('digest'):
            raise ProjectLocationError('Retained finding content differs from its accepted digest')


def verify_research_records(root, *, check=lambda: None):
    folder = root.folder('artifacts') / 'research'
    if not folder.exists() and not folder.is_symlink():
        return
    records, size = 0, 0

    def read(path):
        nonlocal records, size
        check()
        records += 1
        if records > MAX_RECORDS:
            raise ProjectLocationError('Research verification exceeds its record allowance')
        data = ProjectStore._read(path, min(MAX_RECORD, MAX_BYTES - size))
        size += len(data)
        if size > MAX_BYTES:
            raise ProjectLocationError('Research verification exceeds its byte allowance')
        return data

    try:
        for date in _children(folder, check):
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date.name):
                continue  # Other research files remain copied, without inferred semantics.
            for effort in _children(date, check):
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,120}', effort.name):
                    raise ProjectLocationError('Retained researcher folder has an unsupported identity')
                if not stat.S_ISDIR(effort.lstat().st_mode):
                    raise ProjectLocationError('Retained researcher storage must be a regular directory')
                head = effort / 'session.json'
                # Reuse the existing publication marker checks before and after
                # historical verification. The outer copy also pins every member.
                ResearchStorage.read(head)
                _identity(json.loads(read(head)), root.session, date.name, effort.name)
                history = effort / 'history'
                if history.exists() or history.is_symlink():
                    for path in _children(history, check):
                        if not re.fullmatch(r'[a-f0-9]{64}\.json', path.name):
                            raise ProjectLocationError('Retained research history has an unsupported record name')
                        raw = read(path)
                        if hashlib.sha256(raw).hexdigest() != path.stem:
                            raise ProjectLocationError('Earlier researcher metadata changed from its accepted digest')
                        _identity(json.loads(raw), root.session, date.name, effort.name)
                ResearchStorage.read(head)
                check()
    except (OSError, ValueError, TypeError, RecursionError) as error:
        raise ProjectLocationError(f'Project researcher history failed verification: {error}') from error
