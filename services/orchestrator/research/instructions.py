"""Host-supplied research guidance. Content is data and never selects executable code."""
import hashlib
import json
from ..paths import REPO


def packages():
    rows = []
    for path in sorted((REPO / 'catalog' / 'research-packages').glob('*.json')):
        raw = path.read_bytes()
        if len(raw) > 65536:
            raise ValueError('Research instruction package exceeds its bounded size')
        content = json.loads(raw)
        rows.append({'id': content['id'], 'version': content['version'],
                     'digest': hashlib.sha256(raw).hexdigest(), 'content': content})
    return rows


def pin(reference):
    if type(reference) is not dict or set(reference) != {'id', 'version', 'digest'}:
        raise ValueError('Pin the instruction package id, version and digest')
    row = next((p for p in packages() if all(p[k] == reference[k] for k in reference)), None)
    if row is None:
        raise ValueError('The instruction package changed or is unavailable; inspect it again')
    # Retain the content too: later host upgrades cannot remove the Study's guidance.
    return row
