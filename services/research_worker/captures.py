"""Original capture identities from complete resource reads, without acquiring pixels."""
from __future__ import annotations

import copy
import hashlib
import json



def basis_digest(document: dict) -> str:
    """Scientific capture facts exclude layer placement, display words and read badges."""
    entry = document.get('entry', {})
    detail = document.get('detail', {})
    basis = {'entry':{key:entry.get(key) for key in ('day','id','kind','version','source','pictures')},
             'detail':{key:detail.get(key) for key in ('values','columns','members')}}
    return hashlib.sha256(json.dumps(basis,ensure_ascii=False,sort_keys=True,
        separators=(',', ':'),allow_nan=False).encode()).hexdigest()


def retain(documents: dict) -> list[dict]:
    retained = []
    for (entry_id, revision), document in documents.items():
        entry = document.get('entry', {})
        if entry.get('kind') not in ('clip', 'tracks', 'population'):
            continue
        pictures = entry.get('pictures') or []
        values = document.get('detail', {}).get('values') or {}
        facts = values.get('frames') if entry['kind'] == 'clip' else [entry.get('source', {})]
        facts = facts if isinstance(facts, list) else []
        frames = []
        for picture in range(len(pictures)):
            original = facts[picture] if picture < len(facts) else {}
            frames.append({'picture': picture, **{key:copy.deepcopy(original[key]) for key in
                ('frame', 'seq', 'boot', 'bootId', 'at', 'sha256') if key in original}})
        retained.append({'title':entry.get('title') or entry_id,'kind':entry['kind'],
            'state':entry.get('state') or 'unknown','summary':entry.get('summary') or '',
            'at':entry.get('at'), 'source':{'day':entry['day'],'id':entry_id,
                'version':entry.get('version'),'contentRevision':revision,'basisVersion':1,'basisDigest':basis_digest(document)}, 'frames':frames})
    return retained
