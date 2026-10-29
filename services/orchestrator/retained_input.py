"""Stable comparison identity from retained source facts, never a transient frame alone."""
from __future__ import annotations

import hashlib
import json
import re


def comparison_input_key(capture, view: dict) -> str | None:
    image = view.get('sourceImage')
    if image is not None:
        if (not isinstance(image, dict)
                or any(not isinstance(image.get(k), str) or not image[k] for k in ('asset', 'session', 'dtype', 'axes', 'units'))
                or any(not isinstance(image.get(k), str) or not re.fullmatch(r'[0-9a-f]{64}', image[k]) for k in ('sha256', 'pixelSha256'))
                or any(type(image.get(k)) is not int or image[k] < 1 for k in ('width', 'height', 'channels'))):
            return None
        identity = {'image': image}
    else:
        if (type(capture) is not int or not capture
                or any(not isinstance(view.get(k), str) or not view[k] for k in ('boot', 'run'))
                or type(view.get('seq')) is not int):
            return None
        identity = {'frame': capture, **{k: view[k] for k in ('boot', 'seq', 'run')}}
    try:
        raw = json.dumps(identity, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    except (ValueError, TypeError):
        return None
    return 'retained-input/1:' + hashlib.sha256(raw).hexdigest()
