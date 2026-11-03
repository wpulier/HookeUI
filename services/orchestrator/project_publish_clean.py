"""A publication's imported images, cleaned of personal metadata (ADR 0028, format v2 section 8; ticket 06).

Runs on the unsealed publication folder, never on the Project: the Project keeps every original byte for byte. For
each imported JPEG, PNG or TIFF (a source file's original; files Hooke wrote itself stay as they are) whose metadata
the cleaner changes, the publication holds a cleaned copy (the same
pixels, checked by decoding both where this Mac can) and leaves the original's bytes out. The original's manifest
stays, so every document naming the file is unchanged, and a `project-source-file-clean-v1` manifest says what stands
in for it. A file that cannot be cleaned keeps its bytes and is named in the review, as is a file in a format Hooke
cannot check (HEIC, CZI, ND2, DICOM, a video...).
"""
from __future__ import annotations

import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import shutil

from . import project_metadata_scrub as scrub
from .project_metadata_scan import kinds_of
from .scientific_arrays import DIRECTORY, _put
from . import scientific_files as files

MANIFEST = re.compile(r'[0-9a-f]{64}\.json')
IN_MEMORY = 512 << 20  # a JPEG or PNG is cleaned in memory up to this size; a TIFF in place at any size
HEAD = 16 << 20
# Formats that may carry personal metadata which Hooke cannot yet read: named in the review for the owner to judge.
UNCHECKED_SUFFIXES = {'.heic', '.heif', '.avif', '.czi', '.nd2', '.lif', '.dcm', '.dicom', '.mov', '.mp4', '.m4v',
                      '.vsi', '.scn', '.mrxs', '.ims', '.oib', '.oif', '.zvi', '.lsm', '.ndpi', '.qptiff', '.avi'}


def _unchecked(head: bytes, name: str) -> bool:
    return (head[4:8] == b'ftyp' or head[:10] == b'ZISRAWFILE' or head[:4] == b'\xda\xce\xbe\x0a'
            or head[128:132] == b'DICM' or Path(name).suffix.lower() in UNCHECKED_SUFFIXES)


def _assemble(folder: Path, meta: dict, out: Path, check):
    """The original's bytes from its chunks, each checked against its hash."""
    digest = hashlib.sha256()
    with out.open('wb') as stream:
        for index, key in enumerate(meta['chunks']):
            check()
            body = (folder / f'{key}.bin').read_bytes()
            expected = min(meta['chunkBytes'], meta['size'] - index * meta['chunkBytes'])
            if len(body) != expected or hashlib.sha256(body).hexdigest() != key:
                raise files.InvalidProject('A retained source chunk changed; nothing was published')
            digest.update(body)
            stream.write(body)
    if digest.hexdigest() != meta['sha256']:
        raise files.InvalidProject('A retained source file changed; nothing was published')


def _clean(original: Path, cleaned: Path, kind: str, size: int):
    """Write the cleaned copy to `cleaned`; returns label and macro images dropped, or None when nothing changes."""
    if kind == 'tiff':
        shutil.copyfile(original, cleaned)
        with cleaned.open('r+b') as stream:
            with mmap.mmap(stream.fileno(), 0) as view:
                extra, dropped = scrub.clean_tiff_in_place(view, size)
                view.flush()
            stream.seek(0, os.SEEK_END)
            stream.write(extra)
    else:
        if size > IN_MEMORY:
            raise scrub.ImageUnreadable('This image is too large for Hooke to clean here')
        cleaned.write_bytes(scrub.clean_bytes(original.read_bytes()))
        dropped = 0
    same = cleaned.stat().st_size == size and _sha(cleaned) == _sha(original)
    return None if same else dropped


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def _store(folder: Path, revision: str, meta: dict, cleaned: Path, removed: list, check) -> set:
    """Put the cleaned copy's chunks and manifest beside the original's; returns the chunk names written."""
    descriptor = os.open(folder, DIRECTORY)
    try:
        chunks, digest, size = [], hashlib.sha256(), 0
        with cleaned.open('rb') as stream:
            for block in iter(lambda: stream.read(files.CHUNK_BYTES), b''):
                check()
                chunks.append(_put(descriptor, block, '.bin'))
                digest.update(block)
                size += len(block)
        manifest = {'format': files.CLEAN_FORMAT, 'of': revision, 'originalSha256': meta['sha256'],
                    'removed': removed, 'tool': scrub.TOOL, 'name': meta['name'], 'size': size,
                    'sha256': digest.hexdigest(), 'chunkBytes': files.CHUNK_BYTES, 'chunks': chunks}
        _put(descriptor, files._encode(manifest), '.json')
    finally:
        os.close(descriptor)
    return set(chunks)


def _imported(folder: Path) -> set:
    """The manifests a document folder's source-file revisions name as their retained original."""
    found = set()
    for path in sorted((folder.parent / 'revisions').glob('*.json')):
        try:
            document = json.loads(path.read_bytes()).get('document') or {}
            if document.get('kind') in ('source-file', 'source-directory'):
                reference = (document.get('state') or {}).get('sourceFile', {}).get('reference') or {}
                if isinstance(reference.get('revision'), str):
                    found.add(reference['revision'])
        except (OSError, ValueError, AttributeError):
            continue
    return found


def clean(root: Path, scratch: Path, *, apply=True, check=lambda: None):
    """Clean the imported images of the unsealed publication at `root`. Returns (what was cleaned, findings).

    With `apply` False nothing is cleaned and only the formats Hooke cannot check are named.
    """
    cleaned, findings = [], []
    records = Path(root) / 'artifacts' / 'records'
    scratch = Path(scratch)
    scratch.mkdir(mode=0o700, exist_ok=True)
    for folder in sorted(records.glob('p*/files')) if records.is_dir() else ():
        manifests, imported = [], _imported(folder)
        for path in sorted(folder.glob('*.json')):
            if MANIFEST.fullmatch(path.name) and path.name[:-5] in imported:
                meta = json.loads(path.read_bytes())
                if type(meta) is dict and meta.get('format') == files.FORMAT and meta.get('chunks'):
                    manifests.append((path.name[:-5], meta))
        replaced, written = set(), set()
        for revision, meta in manifests:
            check()
            label = f'{str(meta["name"])[:200]} ({folder.parent.name})'
            with (folder / f'{meta["chunks"][0]}.bin').open('rb') as stream:
                head = stream.read(HEAD)
            kind = scrub.kind_of(head[:8])
            if kind is None:
                if _unchecked(head, str(meta['name'])):
                    findings.append({'path': label, 'line': 0, 'kind': 'metadata-unchecked', 'blocks': False})
                continue
            if not apply:
                continue
            original, copy = scratch / 'original', scratch / 'cleaned'
            try:
                _assemble(folder, meta, original, check)
                try:
                    dropped = _clean(original, copy, kind, meta['size'])
                except (scrub.ImageUnreadable, scrub.UnknownImageFormat):
                    findings.append({'path': label, 'line': 0, 'kind': 'metadata-not-cleaned', 'blocks': False})
                    continue
                if dropped is None:
                    continue  # nothing to remove: the original goes as it is
                same = scrub.same_pixels(original, copy, dropped)
                if same is False:
                    findings.append({'path': label, 'line': 0, 'kind': 'metadata-not-cleaned', 'blocks': False})
                    continue
                with original.open('rb') as stream:
                    removed = sorted(kinds_of(stream.read(HEAD))) or ['details']
                if dropped:
                    removed.append('slide-label')
                written |= _store(folder, revision, meta, copy, removed, check)
                replaced.add(revision)
                cleaned.append({'key': (folder, revision), 'path': label, 'removed': removed, 'bytes': meta['size'],
                                'cleanedBytes': copy.stat().st_size,
                                'pixels': 'identical' if same else 'unchanged by the edit; not decoded here'})
            finally:
                for path in (original, copy):
                    path.unlink(missing_ok=True)
        still = set()  # chunks some other manifest here still names: results, other sources, the cleaned copies
        for path in folder.glob('*.json'):
            if MANIFEST.fullmatch(path.name) and path.name[:-5] not in replaced:
                try:
                    still.update(json.loads(path.read_bytes()).get('chunks') or [])
                except (OSError, ValueError, AttributeError, TypeError):
                    continue
        for revision, meta in manifests:
            if revision in replaced:
                gone = set(meta['chunks']) - still - written
                if not gone:  # every byte of it is still carried by another file: the copy would change nothing
                    findings.append({'path': f'{str(meta["name"])[:200]} ({folder.parent.name})', 'line': 0,
                                     'kind': 'metadata-not-cleaned', 'blocks': False})
                    cleaned[:] = [c for c in cleaned if c.get('key') != (folder, revision)]
                for chunk in gone:
                    (folder / f'{chunk}.bin').unlink(missing_ok=True)
    for item in cleaned:
        item.pop('key', None)
    return cleaned, findings
