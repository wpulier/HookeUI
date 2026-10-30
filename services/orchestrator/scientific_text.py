"""Bounded, literal text views of retained bytes; never a scientific reader proof."""
from .scientific_projects import InvalidProject

PAGE_BYTES = 65536


def page(source, offset=0, length=PAGE_BYTES):
    if (type(offset) is not int or type(length) is not int or offset < 0
            or offset > source.size or not 1 <= length <= PAGE_BYTES):
        raise InvalidProject('Choose a retained byte range of at most 65,536 bytes')
    length = min(length, source.size - offset)
    return {'text': source.read(offset, length).decode('utf-8', errors='replace'),
            'offset': offset, 'bytes': length, 'totalBytes': source.size,
            'nextOffset': offset + length if offset + length < source.size else None,
            'encoding': 'utf-8; invalid bytes and split characters replaced'}
