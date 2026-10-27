"""Resolve a captured sequence only through its immutable saved finding."""
from __future__ import annotations

import hashlib
import json

from ..presentations import ResourceRef
from .policy import ResearchRefused


def resolve(manager, index, target: dict) -> ResourceRef:
    """Read-only, synchronous; callers run disk/index work off the event loop.

    Recheck immediately before admitting the prepared source: preparation must not
    race a correction of its original evidence. No generation or acquisition.
    """
    def refuse(message):
        raise ResearchRefused(message, next='reopen the retained findings and choose an available captured frame')

    if not isinstance(target, dict):
        refuse('the captured finding selection is invalid')
    day, rid = target.get('day'), target.get('rid')
    if not isinstance(day, str) or not isinstance(rid, str):
        refuse('the captured finding needs its original researcher and day')
    session = manager.get(rid)
    if session is None or session.day != day:
        session = next((s for s in manager.read_day(day, kept_only=False) if s.rid == rid), None)
    if session is None:
        refuse('the original researcher finding is unavailable')
    version = target.get('findingVersion')
    record = next((r for r in session.finding_revisions if type(version) is int and r['version'] == version), None)
    if record is None or record['digest'] != target.get('findingDigest'):
        refuse('the exact selected finding is unavailable')
    sequence, picture = target.get('sequence'), target.get('picture')
    captures = record['result'].get('captures', [])
    if type(sequence) is not int or not 0 <= sequence < len(captures):
        refuse('the captured sequence is not in this finding')
    capture = captures[sequence]
    if type(picture) is not int or not any(f.get('picture') == picture for f in capture.get('frames', [])):
        refuse('the original captured frame is unavailable')
    source = capture['source']
    document = index.document(source['id'], source['day'])
    if document is None:
        refuse('the original captured source is unavailable')
    from research_worker.captures import basis_digest
    if source.get('basisDigest') and source.get('basisVersion',1) != 1:
        refuse('the captured source basis version is not supported')
    expected = source.get('basisDigest', source['contentRevision'])
    digest = basis_digest(document) if source.get('basisDigest') else hashlib.sha256(json.dumps(
        document,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()
    if digest != expected:
        refuse('the captured source changed after these findings were saved')
    return ResourceRef.from_wire({key:source.get(key) for key in ('day','id','version')} | {'picture':picture})
