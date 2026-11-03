"""Find personal metadata in a publication's imported files, for the owner to judge (ADR 0028, ticket 06).

Nothing is removed here: the review names the file and what it carries (a location, a person, a home-folder path).
A publication cleans what it can first (project_publish_clean), so what is found here is what would go out. Only the first chunk of each file is read; JPEG, PNG and TIFF (including
OME-TIFF and ImageJ) headers are parsed with the standard library. Unknown formats are not guessed at.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import struct

GPS, ARTIST, DESCRIPTION, SOFTWARE, XP_AUTHOR, EXIF_IFD = 0x8825, 0x013B, 0x010E, 0x0131, 0x9C9D, 0x8769
HOME = re.compile(rb'(?:/Users|/home)/(?!Shared/)[A-Za-z0-9._\-]+/')
PERSON = re.compile(rb'(?:<Experimenter\b[^>]*(?:UserName|Email|FirstName|LastName)=|Experimenter|Operator|Author)', re.I)
MANIFEST = re.compile(r'[0-9a-f]{64}\.json')


def _tiff(body: bytes, base: int = 0):
    """Kinds of personal metadata in a TIFF structure starting at `base`."""
    kinds = set()
    if body[base:base + 2] == b'II':
        order = '<'
    elif body[base:base + 2] == b'MM':
        order = '>'
    else:
        return kinds
    try:
        (offset,) = struct.unpack_from(order + 'I', body, base + 4)
        seen = 0
        while offset and seen < 4:
            seen += 1
            (count,) = struct.unpack_from(order + 'H', body, base + offset)
            for index in range(min(count, 512)):
                tag, kind, number, value = struct.unpack_from(order + 'HHII', body, base + offset + 2 + index * 12)
                if tag == GPS:
                    kinds.add('location')
                elif tag in (ARTIST, XP_AUTHOR):
                    kinds.add('person')
                elif tag in (DESCRIPTION, SOFTWARE) and kind == 2:
                    text = body[base + value: base + value + min(number, 1 << 20)] if number > 4 else b''
                    if HOME.search(text):
                        kinds.add('home-path')
                    if PERSON.search(text):
                        kinds.add('person')
            (offset,) = struct.unpack_from(order + 'I', body, base + offset + 2 + count * 12)
    except struct.error:
        pass
    return kinds


def _jpeg(body: bytes):
    kinds, at = set(), 2
    while at + 4 <= len(body) and body[at] == 0xFF:
        marker = body[at + 1]
        if marker in (0xD9, 0xDA):
            break
        (length,) = struct.unpack_from('>H', body, at + 2)
        segment = body[at + 4: at + 2 + length]
        if marker == 0xE1 and segment.startswith(b'Exif\0\0'):
            kinds |= _tiff(segment, 6)
        elif marker == 0xE1 and b'http://ns.adobe.com/xap' in segment[:64]:
            if b'GPS' in segment:
                kinds.add('location')
            if b'dc:creator' in segment or b'Author' in segment:
                kinds.add('person')
        elif marker == 0xFE and (HOME.search(segment) or PERSON.search(segment)):
            kinds.add('person' if PERSON.search(segment) else 'home-path')
        at += 2 + length
    return kinds


def _png(body: bytes):
    kinds, at = set(), 8
    while at + 8 <= len(body):
        length, kind = struct.unpack_from('>I4s', body, at)
        data = body[at + 8: at + 8 + length]
        if kind in (b'tEXt', b'iTXt', b'zTXt'):
            key = data.split(b'\0', 1)[0]
            if key.lower() in (b'author', b'artist', b'copyright') or PERSON.search(data):
                kinds.add('person')
            if HOME.search(data):
                kinds.add('home-path')
        elif kind == b'eXIf':
            kinds |= _tiff(data)
        elif kind == b'IEND':
            break
        at += 12 + length
    return kinds


def kinds_of(head: bytes):
    if head.startswith(b'\xff\xd8'):
        return _jpeg(head)
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return _png(head)
    if head[:4] in (b'II*\0', b'MM\0*'):
        return _tiff(head)
    return set()


def scan(root: Path):
    """Findings for every imported file whose first chunk carries personal metadata. Where a publication holds a
    cleaned copy instead of the original (project_publish_clean), the copy is what is read: it is what goes out."""
    findings = []
    records = Path(root) / 'artifacts' / 'records'
    for folder in sorted(records.glob('p*/files')) if records.is_dir() else ():
        plain, cleaned = [], {}
        for manifest in sorted(folder.glob('*.json')):
            if not MANIFEST.fullmatch(manifest.name):
                continue
            try:
                meta = json.loads(manifest.read_text())
            except (OSError, ValueError):
                continue
            if type(meta) is not dict:
                continue
            if meta.get('format') == 'project-source-file-clean-v1':
                cleaned[meta.get('of')] = meta
            else:
                plain.append((manifest.name[:-5], meta))
        for revision, meta in plain:
            try:
                name, chunks = str(meta.get('name', ''))[:200], meta.get('chunks') or []
                if not chunks:
                    continue
                first = folder / f'{chunks[0]}.bin'
                if revision in cleaned:  # what goes out is the cleaned copy
                    first = folder / f'{(cleaned[revision].get("chunks") or [""])[0]}.bin'
                head = first.read_bytes()
            except (OSError, ValueError, AttributeError, TypeError):
                continue
            for kind in sorted(kinds_of(head)):
                findings.append({'path': f'{name} ({folder.parent.name})', 'line': 0,
                                 'kind': f'metadata-{kind}', 'blocks': False})
    return findings
