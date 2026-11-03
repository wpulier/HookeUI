"""Remove personal metadata from an image for a publication, never touching its pixels (ADR 0028, ticket 06).

Every edit is at the byte level: compressed image data is copied verbatim and nothing is decoded and encoded again,
so even lossy JPEG data stays identical. Only the publication's copy is cleaned; the Project keeps the original.

- JPEG keeps what decoding needs (JFIF header without its thumbnail, ICC profile, Adobe colour flag, tables, frames,
  scans). EXIF, XMP, IPTC, comments and anything after the image go. An EXIF orientation other than 1 is kept, alone.
- PNG keeps its critical and rendering chunks. Text, EXIF, time and unknown chunks go.
- TIFF (classic and BigTIFF, either byte order, OME-TIFF, ImageJ, Aperio SVS) keeps an allow-list of structural tags
  in every IFD. Its description is rewritten: OME-XML without people, dates, serials, free text or annotations;
  ImageJ's structural keys; Aperio's scale and magnification; tifffile's array shape. Anything else there goes, as do EXIF, GPS, XMP, IPTC,
  Photoshop, GeoTIFF, ImageJ metadata and private tags. A slide scanner's label and macro photographs, which can show
  a handwritten name, are dropped. The bytes of everything removed are zeroed; bytes the cleaner does not recognise
  are left as they were, so image data found in a way it does not follow is never touched.

Anything else is refused as `UnknownImageFormat`; a damaged file as `ImageUnreadable`.
"""
from __future__ import annotations

import json
import re
import struct
import xml.etree.ElementTree as ET

TOOL = 'hooke-metadata-clean-1'
MAX_DESCRIPTION = 8 << 20
MAX_IFDS = 100_000


class UnknownImageFormat(ValueError):
    """A format this cleaner does not know."""


class ImageUnreadable(ValueError):
    """A file of a known format whose structure could not be followed."""


def kind_of(head: bytes) -> str | None:
    if head[:2] == b'\xff\xd8':
        return 'jpeg'
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if head[:4] in (b'II*\0', b'MM\0*', b'II+\0', b'MM\0+'):
        return 'tiff'
    return None


# ---------------------------------------------------------------- JPEG

def _jpeg(b: bytes) -> bytes:
    out, i, orientation = bytearray(b'\xff\xd8'), 2, 1
    while i < len(b):
        if b[i] != 0xFF:
            raise ImageUnreadable('A JPEG marker is out of place')
        m = b[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m == 0xD9:
            out += b'\xff\xd9'
            break  # what follows the image (a second picture, a vendor trailer) goes
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            out += b[i:i + 2]
            i += 2
            continue
        (length,) = struct.unpack_from('>H', b, i + 2)
        if length < 2 or i + 2 + length > len(b):
            raise ImageUnreadable('A JPEG segment runs past the end of the file')
        segment, payload, keep = b[i:i + 2 + length], b[i + 4:i + 2 + length], False
        if m == 0xE0 and payload.startswith(b'JFIF\0') and len(payload) >= 14:
            body = payload[:12] + b'\0\0'  # the header without its thumbnail
            segment, keep = b'\xff\xe0' + struct.pack('>H', len(body) + 2) + body, True
        elif m == 0xE1 and payload.startswith(b'Exif\0\0'):
            orientation = _orientation(payload[6:]) or 1
        elif m == 0xE2 and payload.startswith(b'ICC_PROFILE\0'):
            keep = True
        elif m == 0xEE and payload.startswith(b'Adobe'):
            keep = True  # its colour transform flag decides how the image decodes
        elif m in (0xDB, 0xC4, 0xDD, 0xCC) or (0xC0 <= m <= 0xCF and m != 0xC8):
            keep = True
        elif m == 0xDA:  # a scan: its header and coded data, up to the next marker
            j = i + 2 + length
            while True:
                j = b.find(b'\xff', j)
                if j < 0 or j + 1 >= len(b):
                    raise ImageUnreadable('A JPEG scan does not end')
                if b[j + 1] == 0x00 or 0xD0 <= b[j + 1] <= 0xD7 or b[j + 1] == 0xFF:
                    j += 1 if b[j + 1] == 0xFF else 2
                    continue
                break
            out += b[i:j]
            i = j
            continue
        if keep:
            out += segment
        i += 2 + length
    else:
        raise ImageUnreadable('A JPEG has no end')
    if orientation != 1:
        exif = (b'Exif\0\0II*\0' + struct.pack('<IH', 8, 1) + struct.pack('<HHIHH', 0x0112, 3, 1, orientation, 0)
                + struct.pack('<I', 0))
        at = 2 + (2 + struct.unpack_from('>H', out, 4)[0] if out[2:4] == b'\xff\xe0' else 0)
        out[at:at] = b'\xff\xe1' + struct.pack('>H', len(exif) + 2) + exif
    return bytes(out)


def _orientation(t: bytes):
    try:
        e = '>' if t[:2] == b'MM' else '<'
        (off,) = struct.unpack_from(e + 'I', t, 4)
        (n,) = struct.unpack_from(e + 'H', t, off)
        for k in range(min(n, 512)):
            tag, kind = struct.unpack_from(e + 'HH', t, off + 2 + 12 * k)
            if tag == 0x0112 and kind == 3:
                value = struct.unpack_from(e + 'H', t, off + 2 + 12 * k + 8)[0]
                return value if 1 <= value <= 8 else None
    except struct.error:
        return None
    return None


# ---------------------------------------------------------------- PNG

PNG_KEEP = {b'IHDR', b'PLTE', b'IDAT', b'IEND', b'tRNS', b'gAMA', b'cHRM', b'sRGB', b'iCCP', b'sBIT', b'pHYs',
            b'bKGD', b'acTL', b'fcTL', b'fdAT', b'cICP', b'mDCv', b'cLLi'}


def _png(b: bytes) -> bytes:
    out, i = bytearray(b[:8]), 8
    while True:
        if i + 12 > len(b):
            raise ImageUnreadable('A PNG has no end')
        (length,) = struct.unpack_from('>I', b, i)
        kind = b[i + 4:i + 8]
        if i + 12 + length > len(b):
            raise ImageUnreadable('A PNG chunk runs past the end of the file')
        if kind in PNG_KEEP:
            out += b[i:i + 12 + length]
        i += 12 + length
        if kind == b'IEND':
            return bytes(out)


# ---------------------------------------------------------------- TIFF

SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4, 16: 8, 17: 8, 18: 8}
KEEP_TAGS = {
    254, 255, 256, 257, 258, 259, 262, 263, 266, 273, 274, 277, 278, 279, 280, 281, 282, 283, 284, 286, 287, 290,
    291, 292, 293, 296, 297, 301, 317, 318, 319, 320, 321, 322, 323, 324, 325, 330, 332, 338, 339, 340, 341, 347,
    512, 513, 514, 515, 517, 518, 519, 520, 521, 529, 530, 531, 532, 32995, 32996, 32997, 32998,
    34675,  # ICC profile
    270,    # the description, rewritten
    271, 272,  # the camera or scanner's make and model: equipment, not a person
}
IMAGEJ_KEYS = {'ImageJ', 'images', 'channels', 'slices', 'frames', 'hyperstack', 'mode', 'unit', 'spacing',
               'finterval', 'loop', 'min', 'max', 'cf', 'c0', 'c1', 'vunit', 'xorigin', 'yorigin', 'zorigin'}
APERIO_KEYS = {'AppMag', 'MPP', 'StripeWidth', 'Exposure Time', 'Exposure Scale', 'Gamma', 'Focus Offset',
               'Left', 'Top', 'LineCameraSkew', 'LineAreaXOffset', 'LineAreaYOffset', 'OriginalWidth',
               'OriginalHeight'}
OME_DROP = {'Experimenter', 'ExperimenterGroup', 'ExperimenterRef', 'ExperimenterGroupRef', 'AcquisitionDate',
            'Description', 'StructuredAnnotations', 'AnnotationRef', 'Folder', 'Rights', 'Project', 'Dataset',
            'Screen', 'Plate'}
OME_DROP_ATTRIBUTES = {'SerialNumber', 'LotNumber', 'Creator', 'UserName', 'Email', 'Institution', 'FirstName',
                       'LastName', 'MiddleName'}
PATH = re.compile(r'(^|[\s(])(/|~|[A-Za-z]:\\)')
LABEL = re.compile(r'^\s*(label|macro)\b', re.I)


def ome(xml: str) -> str:
    """OME-XML without people, dates, serials, free text or annotations; pixels, channels and sizes kept."""
    found = re.search(r'<(?:\w+:)?OME\b[^>]*\bxmlns="([^"]+)"', xml)
    if found:
        ET.register_namespace('', found.group(1))
    try:
        root = ET.fromstring(xml.encode())
    except ET.ParseError as error:
        raise ImageUnreadable('An OME description is not readable XML') from error
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag.split('}')[-1] in OME_DROP:
                parent.remove(child)
    for element in root.iter():
        for name in list(element.attrib):
            if name in OME_DROP_ATTRIBUTES:
                del element.attrib[name]
            elif PATH.search(element.attrib[name]):
                element.attrib[name] = re.sub(r'.*[/\\]', '', element.attrib[name])  # a path becomes its file name
    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding='unicode')


def imagej(text: str) -> str:
    return '\n'.join(line for line in text.splitlines() if line.split('=', 1)[0] in IMAGEJ_KEYS) + '\n'


def aperio(text: str) -> str:
    """Aperio's first line and image size, then only its scale and magnification fields."""
    parts = text.split('|')
    lines = parts[0].replace('\r\n', '\n').split('\n')
    head = '\r\n'.join(line for line in lines[:2] if not PATH.search(line))
    kept = [p for p in parts[1:] if p.split('=', 1)[0].strip() in APERIO_KEYS]
    return '|'.join([head] + kept)


def shaped(text: str) -> str | None:
    """tifffile's description of an array's shape (how a stack's pages fold into z, time and channels), alone."""
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if type(value) is not dict or not isinstance(value.get('shape'), list):
        return None
    return json.dumps({k: value[k] for k in ('shape', 'axes') if k in value}, separators=(', ', ': '))


def _description(text: str) -> str | None:
    if '<OME' in text[:4000]:
        return ome(text)
    if text.startswith('ImageJ='):
        return imagej(text)
    if text.startswith('Aperio'):
        return aperio(text)
    if text.startswith('{"shape"'):
        return shaped(text)
    return None  # free text from acquisition software


class _Tiff:
    def __init__(self, buffer):
        self.b = buffer
        if bytes(buffer[:2]) not in (b'II', b'MM'):
            raise ImageUnreadable('Not a TIFF')
        self.e = '<' if bytes(buffer[:2]) == b'II' else '>'
        magic = self.u('H', 2)
        self.big = magic == 43
        if self.big:
            self.off, self.cnt, self.entry, self.inline, self.header = 'Q', 'Q', 20, 8, 16
        else:
            self.off, self.cnt, self.entry, self.inline, self.header = 'I', 'H', 12, 4, 8
        self.first_at = 8 if self.big else 4

    def u(self, fmt, at):
        return struct.unpack_from(self.e + fmt, self.b, at)[0]

    def ifd(self, at):
        n = self.u(self.cnt, at)
        if n > 4096:
            raise ImageUnreadable('A TIFF directory is too large')
        width = struct.calcsize(self.cnt)
        entries = []
        for k in range(n):
            p = at + width + k * self.entry
            tag, kind = struct.unpack_from(self.e + 'HH', self.b, p)
            count = self.u('Q' if self.big else 'I', p + 4)
            value_at = p + (12 if self.big else 8)
            size = SIZES.get(kind, 1) * count
            entries.append({'tag': tag, 'kind': kind, 'count': count, 'size': size,
                            'at': value_at if size <= self.inline else self.u(self.off, value_at),
                            'raw': bytes(self.b[p:p + self.entry])})
        end = at + width + n * self.entry
        return entries, self.u(self.off, end), end + struct.calcsize(self.off) - at

    def values(self, entry):
        fmt = {3: 'H', 4: 'I', 13: 'I', 16: 'Q', 18: 'Q'}.get(entry['kind'])
        if fmt is None or entry['at'] + entry['size'] > len(self.b):
            raise ImageUnreadable('A TIFF offset table is out of place')
        return list(struct.unpack_from(self.e + fmt * entry['count'], self.b, entry['at']))

    def text(self, entry):
        if entry['size'] > MAX_DESCRIPTION or entry['at'] + entry['size'] > len(self.b):
            raise ImageUnreadable('A TIFF description is out of place')
        return bytes(self.b[entry['at']:entry['at'] + entry['size']]).rstrip(b'\0').decode('utf-8', 'replace')

    def chain(self, start):
        """The offsets of a chain of IFDs."""
        found, seen, at = [], set(), start
        while at:
            if at in seen or len(found) >= MAX_IFDS or at + 2 > len(self.b):
                raise ImageUnreadable('A TIFF directory chain loops or runs past the end of the file')
            seen.add(at)
            found.append(at)
            at = self.ifd(at)[1]
        return found


def _tiff(buffer, end: int) -> tuple[bytes, int]:
    """Clean the TIFF in `buffer` (bytearray or writable mmap) in place. Returns (bytes to append, label and macro
    images dropped)."""
    t = _Tiff(buffer)
    appended = bytearray()
    # Only what is removed is wiped (`dead`), never a byte still referenced (`live`): image data a reader finds in a
    # way this cleaner does not follow (a single-directory ImageJ stack's later planes) is left exactly as it was.
    live, dead = [(0, t.header)], []
    dropped = 0

    def extent(e):
        return (e['at'], e['at'] + e['size']) if e['size'] > t.inline and e['at'] + e['size'] <= len(buffer) else None

    def zap(at, depth):
        """Wipe an EXIF, GPS or Interop directory and its values."""
        try:
            entries, _, length = t.ifd(at)
        except (struct.error, ImageUnreadable):
            return
        dead.append((at, at + length))
        for e in entries:
            if extent(e):
                dead.append(extent(e))
            if depth and e['tag'] == 40965:
                zap(t.values(e)[0], depth - 1)

    def image_data(entries):
        by = {e['tag']: e for e in entries}
        for offsets, counts in ((273, 279), (324, 325), (513, 514)):
            if offsets in by and counts in by:
                starts, sizes = t.values(by[offsets]), t.values(by[counts])
                if offsets == 513:
                    starts, sizes = starts[:1], sizes[:1]
                for start, size in zip(starts, sizes):
                    if start + size > len(buffer):
                        raise ImageUnreadable('TIFF image data runs past the end of the file')
                    yield start, start + size

    def rewrite(chain, *, drop_labels):
        nonlocal dropped
        kept_chain = []
        for at in chain:
            entries, _, length = t.ifd(at)
            description = next((e for e in entries if e['tag'] == 270 and e['kind'] == 2), None)
            if drop_labels and description is not None:
                lines = t.text(description).replace('\r\n', '\n').split('\n')
                if t.text(description).startswith('Aperio') and len(lines) > 1 and LABEL.match(lines[1]):
                    dropped += 1
                    dead.append((at, at + length))
                    dead.extend(v for v in map(extent, entries) if v)
                    dead.extend(image_data(entries))
                    continue
            kept_chain.append(at)
        for index, at in enumerate(kept_chain):
            entries, _, length = t.ifd(at)
            following = kept_chain[index + 1] if index + 1 < len(kept_chain) else 0
            kept = []
            for e in entries:
                if e['tag'] not in KEEP_TAGS:
                    if extent(e):
                        dead.append(extent(e))
                    if e['tag'] in (34665, 34853) and e['kind'] in (4, 13, 16, 18):
                        zap(t.values(e)[0], 1)
                    continue
                if e['tag'] == 270:
                    if extent(e):
                        dead.append(extent(e))  # the old text; a new one written in its place is live
                    if e['kind'] != 2:
                        continue
                    new = _description(t.text(e))
                    if new is None:
                        continue
                    body = new.encode() + b'\0'
                    if len(body) <= t.inline:
                        value = body.ljust(t.inline, b'\0')
                        where = None
                    elif len(body) <= e['size']:
                        buffer[e['at']:e['at'] + len(body)] = body
                        where = e['at']
                    else:
                        if (end + len(appended)) % 2:
                            appended += b'\0'  # TIFF offsets fall on word boundaries
                        where = end + len(appended)
                        appended.extend(body + (b'\0' if len(body) % 2 else b''))
                    raw = struct.pack(t.e + 'HH', 270, 2) + struct.pack(t.e + ('Q' if t.big else 'I'), len(body))
                    raw += value if where is None else struct.pack(t.e + t.off, where)
                    e = {**e, 'size': len(body), 'at': where if where is not None else 0, 'raw': raw}
                    if where is not None and where < end:
                        live.append((where, where + len(body)))
                    kept.append(e)
                    continue
                kept.append(e)
                if e['size'] > t.inline:
                    if e['at'] + e['size'] > len(buffer):
                        raise ImageUnreadable('A TIFF value is out of place')
                    live.append((e['at'], e['at'] + e['size']))
            by = {e['tag']: e for e in kept}
            live.extend(image_data(kept))
            if 330 in by:
                for sub in t.values(by[330]):
                    rewrite(t.chain(sub), drop_labels=False)
            block = (struct.pack(t.e + t.cnt, len(kept)) + b''.join(e['raw'] for e in kept)
                     + struct.pack(t.e + t.off, following))
            buffer[at:at + length] = block + b'\0' * (length - len(block))
            live.append((at, at + len(block)))
        return kept_chain[0] if kept_chain else 0

    first = rewrite(t.chain(t.u(t.off, t.first_at)), drop_labels=True)
    struct.pack_into(t.e + t.off, buffer, t.first_at, first)
    kept = sorted(live)
    for start, stop in sorted(dead):
        start, stop = max(0, start), min(stop, end)
        for a, z in kept:  # never wipe a referenced byte, even where a hostile file makes ranges overlap
            if z <= start or a >= stop:
                continue
            if a > start:
                buffer[start:a] = b'\0' * (a - start)
            start = max(start, z)
            if start >= stop:
                break
        if start < stop:
            buffer[start:stop] = b'\0' * (stop - start)
    return bytes(appended), dropped


# ---------------------------------------------------------------- entry points

def clean_bytes(body: bytes) -> bytes:
    """A cleaned copy of a JPEG, PNG or TIFF held in memory."""
    kind = kind_of(body[:8])
    try:
        if kind == 'jpeg':
            return _jpeg(body)
        if kind == 'png':
            return _png(body)
        if kind == 'tiff':
            buffer = bytearray(body)
            extra, _ = _tiff(buffer, len(buffer))
            return bytes(buffer) + extra
    except (struct.error, IndexError, UnicodeError, RecursionError) as error:
        raise ImageUnreadable('This image’s structure could not be followed') from error
    raise UnknownImageFormat('Hooke cannot clean this format')


def clean_tiff_in_place(buffer, size: int) -> tuple[bytes, int]:
    """Clean a TIFF held in a writable buffer of `size` bytes (a file mapped in memory). Returns (bytes to append,
    label and macro images dropped)."""
    try:
        return _tiff(buffer, size)
    except (struct.error, IndexError, UnicodeError, RecursionError) as error:
        raise ImageUnreadable('This image’s structure could not be followed') from error


def same_pixels(original, cleaned, dropped: int = 0) -> bool | None:
    """Whether two image files (paths) decode to identical pixels: True, False, or None when this Mac cannot decode
    them (the copy is then trusted to the byte-level edit, which never touches image data)."""
    try:
        import numpy
    except ImportError:
        return None
    with open(original, 'rb') as stream:
        kind = kind_of(stream.read(8))
    try:
        before = _decoded(original, kind, skip_labels=bool(dropped))
    except ImportError:
        return None
    except Exception:  # noqa: BLE001 - a codec this Mac lacks, or a file only a vendor's reader opens
        return None
    try:
        after = _decoded(cleaned, kind, skip_labels=False)
    except Exception:  # noqa: BLE001 - the original decodes and the copy does not: the copy is broken
        return False
    return len(before) == len(after) and all(
        a[0] == z[0] and numpy.array_equal(a[1], z[1]) for a, z in zip(before, after))


def _decoded(path, kind, *, skip_labels):
    """[(description of the frame's layout, its pixels)] for every frame or page."""
    import numpy
    if kind == 'tiff':
        import tifffile
        with tifffile.TiffFile(path) as tiff:
            pages = [p for p in tiff.pages if not (skip_labels and _is_label(p))]
            return [((p.shape, str(p.dtype)), p.asarray()) for p in pages]
    from PIL import Image
    frames = []
    with Image.open(path) as image:
        for frame in range(getattr(image, 'n_frames', 1)):
            image.seek(frame)
            frames.append(((image.mode, image.size), numpy.asarray(image.copy())))
    return frames


def _is_label(page) -> bool:
    text = page.description or ''
    lines = text.replace('\r\n', '\n').split('\n')
    return text.startswith('Aperio') and len(lines) > 1 and bool(LABEL.match(lines[1]))
