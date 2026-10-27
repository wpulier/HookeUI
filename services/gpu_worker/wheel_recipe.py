"""Portable, hash-pinned wheel inputs for catalog format 4.

Metadata and lock validation only: no imports from contributed packages, network,
installer, or filesystem authority. Recipients still own trust and preparation.
"""
from dataclasses import dataclass
import hashlib
import re

MAX_WHEELS = 4
MAX_BYTES = 64 << 20
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,240}\.whl')
SHA = re.compile(r'[0-9a-f]{64}')
REFERENCE = re.compile(r'hooke-wheel:([^\s\\]+)')
REQUIREMENT = re.compile(
    r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?\s*'
    r'(?:==[A-Za-z0-9_.+!-]+|@\s+(?:https://|hooke-wheel:)[^\s;]+)'
    r'(?:\s*;\s*[A-Za-z0-9_ .\'"<>=!()~-]+)?\s*')


@dataclass(frozen=True)
class Wheel:
    file: str
    sha256: str
    bytes: int

    def verify(self, data):
        if len(data) != self.bytes or hashlib.sha256(data).hexdigest() != self.sha256:
            raise ValueError(f'{self.file}: wheel bytes differ from the declared size or SHA256')


def declared(env):
    if not isinstance(env, dict):
        raise ValueError('A portable wheel recipe needs an environment table')
    if 'wheels' not in env:
        return ()
    rows = env['wheels']
    if type(rows) is not list or not 1 <= len(rows) <= MAX_WHEELS:
        raise ValueError('Declare between one and four portable wheels')
    result, names, total = [], set(), 0
    for row in rows:
        if (type(row) is not dict or set(row) != {'file', 'sha256', 'bytes'}
                or type(row['file']) is not str or not NAME.fullmatch(row['file'])
                or type(row['sha256']) is not str or not SHA.fullmatch(row['sha256'])
                or type(row['bytes']) is not int or not 0 < row['bytes'] <= MAX_BYTES):
            raise ValueError('A portable wheel needs a plain wheel filename, positive bounded bytes and SHA256')
        if row['file'].casefold() in names:
            raise ValueError('Portable wheel filenames must be distinct')
        names.add(row['file'].casefold()); total += row['bytes']
        result.append(Wheel(**row))
    if total > MAX_BYTES:
        raise ValueError('Portable wheels exceed the combined 64 MiB allowance')
    return tuple(result)


def resolve(lock, wheels, urls):
    """Validate portable requirements and replace only exact declared artifact URLs.

    uv still verifies every dependency's requirement hashes. The source lock stays
    unchanged; the caller supplies URLs to its own verified, private copies.
    """
    if not wheels:
        return lock
    expected = {wheel.file for wheel in wheels}
    if set(urls) != expected:
        raise ValueError('Every declared wheel needs one verified preparation copy')
    logical = re.sub(r'\\\r?\n\s*', ' ', lock)
    active = [line.strip() for line in logical.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    for line in active:
        requirement, *hashes = line.split('--hash=')
        if (not REQUIREMENT.fullmatch(requirement) or 'file:' in requirement.lower()
                or re.search(r'(^|\s)-', requirement) or not hashes
                or any(not re.fullmatch(r'sha256:[0-9a-f]{64}\s*', value) for value in hashes)):
            raise ValueError('Portable locks contain pinned package requirements, HTTPS artifacts and declared wheels only')
    found = [match.group(1) for line in active for match in REFERENCE.finditer(line)]
    if len(found) != len(expected) or set(found) != expected:
        raise ValueError('Portable lock must reference each declared wheel exactly once and no other local artifact')
    # Rewrite active lines only: comments may contain explanatory example URLs.
    return '\n'.join(REFERENCE.sub(lambda m: urls[m.group(1)], line) for line in active) + '\n'
