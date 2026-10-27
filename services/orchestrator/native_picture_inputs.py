"""Exact native grey planes for the existing original-picture cells runner.

PNG is lossless transport, not an implicit scientific conversion or a new producer.
The accepted native reference remains the input identity through population retention.
"""
import copy
import hashlib
import math
import time
from types import SimpleNamespace

import cv2
import numpy as np

from . import admission
from .capture import captures

MAX_PIXELS = 1 << 20
WORDS = 'Choose a saved single-plane uint8 intensity image with Y/X axes (at most one megapixel); other types need an explicit conversion.'
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'properties': {'kind': {'type': 'string', 'enum': ['array']},
                         **{k: {'type': 'string'} for k in ('session', 'id', 'component')},
                         'revision': {'type': 'integer', 'minimum': 1}},
          'required': ['kind', 'session', 'id', 'revision', 'component'], 'description': WORDS}


def supported(kind):
    return (kind.get('operation') == 'cells' and kind.get('input') == 'image.2d.rgb8'
            and kind.get('output') == 'labels.instance.2d' and len(kind.get('outputs') or ()) <= 1)


def layout(store, reference, session):
    source, descriptor, exact = admission.native_source(store, reference, session)
    if (source.dtype != '|u1' or source.axes != ('y', 'x') or len(source.shape) != 2
            or not 0 < math.prod(source.shape) <= MAX_PIXELS or descriptor.get('semantic') != 'intensity'):
        raise ValueError(WORDS)
    return source, descriptor, exact


def choices(index, session):
    result = []
    saved, _ = index.projects.scan(session)
    for owner in saved[:64]:
        for ref in owner.document['resources']:
            if not ref.get('component', '').startswith('array.'):
                continue
            reference = {'kind': 'array', 'session': session, 'id': owner.id,
                         'revision': owner.revision, 'component': ref['component']}
            try:
                layout(index.projects, reference, session)
            except (ValueError, OSError):
                continue
            result.append({'title': f"{owner.document['title']} · {ref['component'][6:]} · saved revision {owner.revision}",
                           'reference': reference, 'compatibility': {'compatible': True, 'says': WORDS}})
    return result


def resolve(hub, reference, session, *, allowance='analysis', cancelled=None):
    store, cap = hub.resources.projects, captures(hub)
    source, descriptor, exact = layout(store, reference, session)
    def check():
        if cancelled is not None and cancelled.is_set():
            raise ValueError('Opening this saved input was stopped; nothing ran.')
    check()
    height, width = source.shape
    with cap.memory.reserve(allowance, width * height * 12, 'opening exact native picture'):
        pixels = np.ascontiguousarray(source.read((slice(0, height), slice(0, width))))
        check()
        ok, encoded = cv2.imencode('.png', pixels)
        if not ok:
            raise ValueError('The saved input could not be transported losslessly.')
        png = encoded.tobytes()
        if not np.array_equal(cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED), pixels):
            raise ValueError('The picture transport changed scientific values; nothing ran.')
        check()
        if layout(store, reference, session)[2] != exact:
            raise ValueError('The selected native revision changed while opening it.')
        info = SimpleNamespace(width=width, height=height, epoch=None, dx=0., dy=0., fov=None,
                               disc=0., tracked=False, steady=True, wall_at=time.time(), seq=None, boot='')
        metadata = {'resource': copy.deepcopy(reference), 'nativeSource': exact,
                    'scientific': descriptor, 'session': session,
                    'sha256': hashlib.sha256(png).hexdigest(), 'pixelSha256': hashlib.sha256(pixels.tobytes()).hexdigest(),
                    'width': width, 'height': height, 'dtype': 'uint8', 'axes': 'YX', 'channels': 1,
                    'mediaType': 'image/png', 'units': 'pixels', 'pixelEncoding': 'row-major uint8 grayscale',
                    'orientation': 'accepted native Y/X grid; no rescaling or axis conversion'}
        capture = cap.original(info, png, {'units': 'pixels', 'frame': [width, height]}, png, metadata)
        return capture, cap.hold(capture, allowance, 'bytes', 'exact native picture')


def retained_source(store, metadata, session, pixels=None):
    """Verify the captured native identity and optional retained transport against its original values."""
    captured = metadata.get('nativeSource')
    if captured is None:
        return None
    reference = {k: captured[k] for k in ('kind', 'session', 'id', 'revision', 'component')}
    source, descriptor, exact = layout(store, reference, session)
    if exact != captured or metadata.get('resource') != reference or metadata.get('scientific') != descriptor:
        raise ValueError('The recorded native source differs from its accepted revision.')
    if pixels is not None:
        original = source.read(tuple(slice(0, n) for n in source.shape))
        if original.dtype != pixels.dtype or not np.array_equal(original, pixels):
            raise ValueError('The retained original differs from its captured native source.')
    return exact, descriptor
