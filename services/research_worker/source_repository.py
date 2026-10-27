"""Read a repository at one resolved commit for a builder run; never execute its content."""
from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote, urlsplit


async def read(url: str, path: str, pins: dict, get):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    parts = parsed.path.strip('/').split('/')
    if host not in ('github.com', 'www.github.com', 'huggingface.co', 'www.huggingface.co') or len(parts) < 2:
        response, body, _ = await get(url)
        return url, body.decode(response.encoding or 'utf-8', errors='replace'), ''
    github = host.endswith('github.com')
    repo = '/'.join(parts[:2]).removesuffix('.git')
    ref = 'HEAD' if github else 'main'
    if len(parts) > 2:
        if len(parts) < 4 or parts[2] not in ('tree', 'blob', 'resolve', 'raw'):
            raise ValueError('use a repository, tree or file link with its revision')
        ref = unquote(parts[3])
        if not path and parts[2] in ('blob', 'resolve', 'raw'):
            path = '/'.join(parts[4:])
    if path and any(p in ('', '.', '..') for p in path.split('/')):
        raise ValueError('the file path must stay within the repository')
    key = f'{"github" if github else "huggingface"}/{repo}@{ref}'
    pin = pins.get(key)
    if pin is None:
        target = (f'https://api.github.com/repos/{repo}/commits/{quote(ref, safe="")}' if github else
                  f'https://huggingface.co/api/models/{repo}/revision/{quote(ref, safe="")}')
        _, body, _ = await get(target)
        metadata = json.loads(body)
        sha = str(metadata.get('sha') or '')
        if not re.fullmatch(r'[0-9a-f]{40}', sha):
            raise ValueError('the repository did not identify an immutable commit')
        pin = {'revision': sha, 'requested': url, 'files': [f['rfilename'] for f in metadata.get('siblings') or []]}
        pins[key] = pin
        # A later root-URL read from the same run remains at the revision first requested.
        pins.setdefault(f'{"github" if github else "huggingface"}/{repo}@{"HEAD" if github else "main"}', pin)
    sha = pin['revision']
    if path:
        target = (f'https://raw.githubusercontent.com/{repo}/{sha}/{quote(path, safe="/")}' if github else
                  f'https://huggingface.co/{repo}/raw/{sha}/{quote(path, safe="/")}')
        response, body, _ = await get(target)
        return target, body.decode(response.encoding or 'utf-8', errors='replace'), sha
    if github and 'listed' not in pin:
        target = f'https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=1'
        _, body, _ = await get(target)
        metadata = json.loads(body)
        pin['files'] = [f['path'] for f in metadata.get('tree') or [] if f.get('type') == 'blob']
        pin['listed'] = True
    target = f'https://github.com/{repo}/tree/{sha}' if github else f'https://huggingface.co/{repo}/tree/{sha}'
    return target, '\n'.join(pin['files'])[:20_000], sha
