"""Bounded UTF-8 source bodies; control headers and transcript limits stay small."""
import hashlib
import re

from .scientific_console import SOURCE_BYTES, source


def encode(request):
    header = dict(request)
    payload = source(header.pop('source')).encode('utf-8')
    header.update(sourceBytes=len(payload), sourceSha256=hashlib.sha256(payload).hexdigest())
    admit(header, len(payload))
    return header, payload


STAGE_OPS = ('stage-layer', 'stage-resource', 'stage-resources', 'stage-families')
# Base chunk digest prefixes for incremental staging: 16 bytes for each chunk of a component's grid.
BASE_BYTES = 65536 * 16
PAYLOAD_BYTES = max(SOURCE_BYTES, BASE_BYTES)


def _base(header, size):
    base = header['baseDigests']
    if (header.get('op') not in STAGE_OPS or type(base) is not dict or set(base) != {'bytes', 'sha256', 'layers'}
            or type(base['bytes']) is not int or not 16 <= base['bytes'] <= BASE_BYTES or base['bytes'] % 16
            or size != base['bytes'] or type(base['sha256']) is not str
            or re.fullmatch('[a-f0-9]{64}', base['sha256']) is None or type(base['layers']) is not list
            or not 1 <= len(base['layers']) <= 64
            or any(type(n) is not int or n < 0 for n in base['layers']) or sum(base['layers']) * 16 != size):
        raise ValueError('Base chunk digests must match their bounded admitted staging request')


def admit(header, size):
    if type(header) is not dict:
        raise ValueError('Scientific requests must be objects')
    if 'baseDigests' in header:
        _base(header, size)
        return
    encoded = 'sourceBytes' in header or 'sourceSha256' in header
    if not encoded:
        if size:
            raise ValueError('Only an admitted Python source request can carry a body')
        return
    expected = {'op', 'sourceBytes', 'sourceSha256', 'interruptToken'}
    if header.get('op') == 'python':
        expected.add('frame')
    elif header.get('op') != 'python-resources':
        raise ValueError('Only Python execution accepts a source body')
    if (set(header) != expected or type(header['sourceBytes']) is not int
            or not 1 <= header['sourceBytes'] <= SOURCE_BYTES or size != header['sourceBytes']
            or type(header['sourceSha256']) is not str
            or re.fullmatch('[a-f0-9]{64}', header['sourceSha256']) is None
            or type(header['interruptToken']) is not str
            or re.fullmatch('[a-f0-9]{32}', header['interruptToken']) is None
            or ('frame' in header and (type(header['frame']) is not str
                or re.fullmatch('[a-f0-9]{64}', header['frame']) is None))):
        raise ValueError('Python source must match its bounded admitted request')


def decode(header, payload):
    admit(header, len(payload))
    if 'baseDigests' in header:
        if hashlib.sha256(payload).hexdigest() != header['baseDigests']['sha256']:
            raise ValueError('Base chunk digests changed during transfer; nothing was staged')
        counts = header['baseDigests']['layers']
        starts = [sum(counts[:i]) * 16 for i in range(len(counts) + 1)]
        result = {key: value for key, value in header.items() if key != 'baseDigests'}
        result['_base'] = [payload[a:b] or None for a, b in zip(starts, starts[1:])]
        return result
    if 'sourceBytes' not in header:
        return header
    if hashlib.sha256(payload).hexdigest() != header['sourceSha256']:
        raise ValueError('Python source changed during transfer; nothing ran')
    result = dict(header)
    del result['sourceBytes'], result['sourceSha256']
    result['source'] = source(payload.decode('utf-8', errors='strict'))
    return result
