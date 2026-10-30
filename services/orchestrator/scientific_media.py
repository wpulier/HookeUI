"""Transport recognition and single byte ranges for retained browser media.

This identifies an encoding from bytes, not scientific meaning or biological
content. Unsupported/active formats remain downloadable, never rendered HTML.
"""
import re


class RangeRefused(ValueError):
    pass


def media_type(header):
    if header.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if header.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if header[:6] in (b'GIF87a',b'GIF89a'):return 'image/gif'
    if header[:4]==b'RIFF' and header[8:12]==b'WEBP':return 'image/webp'
    if header[4:8]==b'ftyp' and header[8:12] in (b'isom',b'iso2',b'mp41',b'mp42',b'avc1',b'M4V ',b'qt  '):
        return 'video/quicktime' if header[8:12]==b'qt  ' else 'video/mp4'
    return None


def byte_range(value,size):
    """Return [start,end), refusing malformed, multiple or unsatisfiable ranges."""
    if value is None:return 0,size,False
    if not isinstance(value,str) or len(value)>128:raise RangeRefused()
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',value)
    if not match or size<=0:raise RangeRefused()
    first,last=match.groups()
    if not first:
        if not last or int(last)<=0:raise RangeRefused()
        return max(0,size-int(last)),size,True
    start=int(first);end=min(size,int(last)+1) if last else size
    if start>=size or end<=start:raise RangeRefused()
    return start,end,True
