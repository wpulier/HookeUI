"""An imported zip archive's members, each read and hashed: what `project.import` records (HOOKE-GAPS row 4).

On the VCC night the organizer's zips were checked against the extracted data with `unzip -p | shasum` by hand. Now
importing a .zip reads every member once, without extracting anything to disk: its size, its CRC (the archive's own
check, which a damaged member fails, and then nothing is imported) and its sha256. The list is kept in the retained
source's `sourceArchive` record, written only here (`scientific_url_intake.INTAKE_RECORDS`), so a later reader can see
which retained file in the Project has the same bytes as which member. `expected` (name -> sha256, e.g. from the
organizer's own manifest) is checked too: a missing or different member refuses the import.

Bounded: at most `MAX_MEMBERS` members are listed and 1 TiB read; past that the archive is still imported and its
record says it was not verified, and why. An encrypted member is listed with no hash. Nothing in a member is parsed.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile

from .scientific_projects import InvalidProject

FORMAT = 1
MAX_MEMBERS = 2048
MAX_NAME = 512
MAX_UNCOMPRESSED = 1 << 40
MAX_RECORD_BYTES = 1 << 20
CHUNK = 1 << 20
SHA256 = re.compile(r'[0-9a-f]{64}')
MEMBER = ('name', 'size', 'crc32', 'sha256')


def is_zip(name, handle):
    """A .zip by its name or its first bytes (a local file header or an empty archive's end record)."""
    at = handle.tell()
    try:
        handle.seek(0)
        head = handle.read(4)
    finally:
        handle.seek(at)
    return name.lower().endswith('.zip') or head in (b'PK\x03\x04', b'PK\x05\x06')


def members(handle, check=lambda: None, expected=None):
    """The archive's record (see the docstring). Raises InvalidProject when it is damaged or `expected` disagrees."""
    if expected is not None and (type(expected) is not dict or not all(
            type(k) is str and type(v) is str and SHA256.fullmatch(v) for k, v in expected.items())):
        raise InvalidProject('Expected archive members are {name: sha256}')
    handle.seek(0)
    try:
        archive = zipfile.ZipFile(handle)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError) as error:
        raise InvalidProject(f'This .zip cannot be read as an archive ({error}); nothing was imported') from None
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        record = {'format': FORMAT, 'kind': 'zip', 'memberCount': len(infos), 'verified': True,
                  'uncompressedBytes': sum(i.file_size for i in infos)}
        if len(infos) > MAX_MEMBERS:
            record.update(verified=False, why=f'{len(infos)} members; Hooke reads and lists at most {MAX_MEMBERS}')
        elif record['uncompressedBytes'] > MAX_UNCOMPRESSED:
            record.update(verified=False, why='more than 1 TiB uncompressed; Hooke reads at most 1 TiB')
        if not record['verified']:
            if expected:
                raise InvalidProject(f'The archive is too large to check against the expected members: {record["why"]}')
            return record
        listed = []
        for info in infos:
            if len(info.filename) > MAX_NAME:
                raise InvalidProject(f'An archive member has a name longer than {MAX_NAME} characters; nothing was imported')
            row = {'name': info.filename, 'size': info.file_size, 'crc32': f'{info.CRC:08x}', 'sha256': None}
            if info.flag_bits & 0x1:
                row['encrypted'] = True
                record['verified'] = False
                record['why'] = 'an encrypted member cannot be read, so it has no hash'
                listed.append(row)
                continue
            digest, size = hashlib.sha256(), 0
            try:
                with archive.open(info) as body:
                    while block := body.read(CHUNK):
                        check()
                        size += len(block)
                        if size > info.file_size:
                            raise zipfile.BadZipFile('it is longer than its header says')
                        digest.update(block)
            except (zipfile.BadZipFile, EOFError, NotImplementedError, OSError, ValueError) as error:
                raise InvalidProject(f'Archive member {info.filename} fails its check ({error}); the archive is '
                                     'damaged or uses an unsupported method, so nothing was imported') from None
            if size != info.file_size:
                raise InvalidProject(f'Archive member {info.filename} is {size} bytes, not the {info.file_size} its '
                                     'header says; nothing was imported')
            row['sha256'] = digest.hexdigest()
            listed.append(row)
        record['members'] = listed
    for name, want in (expected or {}).items():
        row = next((r for r in listed if r['name'] == name), None)
        if row is None:
            raise InvalidProject(f'The archive has no member {name}; nothing was imported')
        if row['sha256'] != want:
            raise InvalidProject(f'Archive member {name} has sha256 {row["sha256"]}, not the expected {want}; '
                                 'nothing was imported')
    if expected:
        record['expectedMatched'] = len(expected)
    if len(json.dumps(record).encode()) > MAX_RECORD_BYTES:
        record = {k: v for k, v in record.items() if k != 'members'}
        record.update(verified=False, why='the member list is too long to keep with the source')
    return record


def record_of(state):
    """The validated archive record of a source document, or None; a malformed one is refused."""
    value = state.get('sourceArchive')
    if value is None:
        return None
    ok = (type(value) is dict and value.get('format') == FORMAT and value.get('kind') == 'zip'
          and type(value.get('verified')) is bool and type(value.get('memberCount')) is int
          and type(value.get('uncompressedBytes')) is int
          and set(value) <= {'format', 'kind', 'memberCount', 'verified', 'uncompressedBytes', 'members', 'why',
                             'expectedMatched'})
    if ok and 'members' in value:
        rows = value['members']
        ok = type(rows) is list and len(rows) == value['memberCount'] and all(
            type(r) is dict and set(MEMBER) <= set(r) <= {*MEMBER, 'encrypted'} and type(r['name']) is str
            and type(r['size']) is int and (r['sha256'] is None or (type(r['sha256']) is str and SHA256.fullmatch(r['sha256'])))
            for r in rows)
    if not ok:
        raise InvalidProject('An imported archive must keep its exact member record')
    return value

