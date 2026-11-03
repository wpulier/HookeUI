"""A version's large files laid end to end in packs of about 16 MiB (format v2 section 4; hooke-projects ticket 07).

An object store charges for every object read as well as for the bytes: serving 1 TB a thousand times a month costs
about $355 on R2 as 1 MiB objects and about $33 as 16 MiB ones (spike S5). So the bulk members of a version are not
sent one object each. Each distinct content goes, once, into a pack of `PACK_BYTES`, and one content may run on from
one pack into the next. A pack is an object named by the SHA-256 of its bytes, like any other.

`hooke-bulk.json` lists the packs. Each lists its pieces in order as `[content sha256, offset in that content, bytes]`,
and the pieces fill the pack exactly. The next version reuses every pack of its parent whose contents it still holds
in full. Only new contents go into new packs, so a second push sends only what is new, and a version's packs never
hold a file that version does not.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import os
from pathlib import Path

from .project_remote_layers import DIGEST, MAX_ENTRIES, ChangedOutsideHooke, RemoteRefused

PACK_BYTES = 16 << 20
MAX_PACK_BYTES = 64 << 20  # what a reader accepts from any writer: Hooke Cloud's own limit for one object
BLOCK = 1 << 20


def _bad(why='The bulk manifest’s packs do not hold exactly this version’s large files'):
    return ChangedOutsideHooke(why)


def parse(packs, sizes: dict) -> list:
    """`packs` checked against `sizes` ({content sha256: bytes}): every piece names one of those contents, the pieces
    fill each pack exactly, and together they cover every non-empty content once."""
    if type(packs) is not list or len(packs) > MAX_ENTRIES:
        raise _bad()
    pieces, names, count = defaultdict(list), set(), 0
    for pack in packs:
        if (type(pack) is not dict or set(pack) != {'sha256', 'bytes', 'contents'}
                or not isinstance(pack['sha256'], str) or not DIGEST.fullmatch(pack['sha256'])
                or pack['sha256'] in names or type(pack['bytes']) is not int
                or not 0 < pack['bytes'] <= MAX_PACK_BYTES or type(pack['contents']) is not list or not pack['contents']):
            raise _bad()
        names.add(pack['sha256'])
        filled = 0
        for piece in pack['contents']:
            count += 1
            if count > len(sizes) + len(packs):
                raise _bad()
            if type(piece) is not list or len(piece) != 3:
                raise _bad()
            content, offset, length = piece
            if (not isinstance(content, str) or content not in sizes or type(offset) is not int
                    or type(length) is not int or offset < 0 or length <= 0):
                raise _bad()
            pieces[content].append((offset, length))
            filled += length
        if filled != pack['bytes']:
            raise _bad()
    for content, size in sizes.items():
        at = 0
        for offset, length in sorted(pieces[content]):
            if offset != at:
                raise _bad()
            at += length
        if at != size:
            raise _bad()
    return packs


def plan(sizes: dict, previous: list, present: set):
    """(the parent's packs this version reuses, the new packs as lists of pieces).

    `sizes` is {content: bytes} in the order the contents are packed; `previous` the parent's checked packs, and
    `present` those of them the store still holds. A parent pack is reused only when every content it holds is in this
    version and every piece of those contents is in packs reused with it.
    """
    kept = {p['sha256']: p for p in previous
            if p['sha256'] in present and all(c in sizes for c, _, _ in p['contents'])}
    while True:
        covered = defaultdict(int)
        for pack in kept.values():
            for content, _, length in pack['contents']:
                covered[content] += length
        partial = [name for name, pack in kept.items()
                   if any(covered[c] != sizes[c] for c, _, _ in pack['contents'])]
        if not partial:
            break
        for name in partial:
            del kept[name]
    reused = [p for p in previous if p['sha256'] in kept]
    held = {c for p in reused for c, _, _ in p['contents']}
    fresh, current, filled = [], [], 0
    for content, size in sizes.items():
        if content in held or size == 0:
            continue
        offset = 0
        while offset < size:
            length = min(PACK_BYTES - filled, size - offset)
            current.append([content, offset, length])
            offset += length
            filled += length
            if filled == PACK_BYTES:
                fresh.append(current)
                current, filled = [], 0
    if current:
        fresh.append(current)
    return reused, fresh


class Packer:
    """Writes new packs from a sealed snapshot's files, checking each content's SHA-256 as its last piece is read."""

    def __init__(self, sizes: dict, sources: dict, scratch: Path, *, check=lambda: None):
        self.sizes, self.sources, self.scratch, self.check = sizes, sources, Path(scratch), check
        self.hashes = {}

    def write(self, pieces: list):
        """(its sha256, the file holding it, its bytes) for one new pack."""
        path = self.scratch / 'pack'
        digest, total = hashlib.sha256(), 0
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as out:
            for content, offset, length in pieces:
                running = self.hashes.setdefault(content, hashlib.sha256())
                with open(self.sources[content], 'rb') as source:
                    source.seek(offset)
                    left = length
                    while left:
                        self.check()
                        block = source.read(min(BLOCK, left))
                        if not block:
                            raise RemoteRefused('A snapshot file changed after sealing; nothing was sent')
                        running.update(block)
                        digest.update(block)
                        out.write(block)
                        left -= len(block)
                total += length
                if offset + length == self.sizes[content] and running.hexdigest() != content:
                    raise RemoteRefused('A snapshot file changed after sealing; nothing was sent')
        return digest.hexdigest(), path, total


def unpack(pack: dict, local: Path, targets: dict, *, check=lambda: None) -> None:
    """Write each piece of a fetched pack at its offset into every file holding its content."""
    with open(local, 'rb') as stream:
        for content, offset, length in pack['contents']:
            fds = [os.open(t, os.O_WRONLY | os.O_NOFOLLOW) for t in targets[content]]
            try:
                done = 0
                while done < length:
                    check()
                    block = stream.read(min(BLOCK, length - done))
                    if not block:
                        raise _bad('A pack from the store is shorter than its manifest says')
                    for fd in fds:
                        _pwrite(fd, block, offset + done)
                    done += len(block)
            finally:
                for fd in fds:
                    os.close(fd)


def _pwrite(fd: int, block: bytes, at: int) -> None:
    view = memoryview(block)
    while view:
        written = os.pwrite(fd, view, at)
        view, at = view[written:], at + written
