"""Hooke Cloud as a bulk store for a Project's large files (ADR 0028, format v2 section 5).

The open client of the private service's Project object routes (hooke-web `project_routes.py`): which objects it lacks,
a signed upload link for each one it lacks (the meter admits or refuses first), and signed download links. Downloads of
a public Project need no account. The objects are the version's packs of about 16 MiB (`project_remote_packs`), never
one per file. Every byte is checked against its SHA-256 here, whatever the service says.

The token is a Hooke Cloud sign-in token with the `hooke:projects` scope. It goes only to the service's own routes in
the Authorization header, never to an object link: a link is itself the permission, and may point anywhere.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from . import untrusted
from .project_remote_layers import DIGEST, ORIGIN, ChangedOutsideHooke, RemoteRefused

BATCH = 1000
MAX_OBJECT = 64 << 20


def _base(url: str) -> str:
    parsed = urlparse(str(url))
    local = parsed.hostname in ('127.0.0.1', 'localhost') and parsed.scheme == 'http'
    if not (parsed.scheme == 'https' or local) or not parsed.hostname or parsed.path not in ('', '/'):
        raise RemoteRefused('Give Hooke Cloud as its https address, for example https://app.hookeui.org')
    return f'{parsed.scheme}://{parsed.netloc}'


class _Http:
    def __init__(self, token):
        import httpx
        self.token = token
        self.client = httpx.Client(timeout=120, follow_redirects=False, trust_env=False,
                                   headers={'User-Agent': 'HookeUI'})

    def request(self, method, url, *, json=None, content=None, headers=None, auth=True, limit=None):
        headers = dict(headers or {})
        if auth and self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        if content is not None or limit is None:
            response = self.client.request(method, url, json=json, content=content, headers=headers)
            try:
                return response.status_code, response.json() if response.content else {}
            except ValueError:
                return response.status_code, {}
        with self.client.stream(method, url, headers=headers) as response:
            body = bytearray()
            for block in response.iter_bytes():
                body += block
                if len(body) > limit:
                    raise ChangedOutsideHooke('A large file from Hooke Cloud is bigger than its manifest says')
            return response.status_code, bytes(body)


class CloudBulk:
    def __init__(self, base: str, origin: str, *, token: str | None = None, http=None, visibility: str = 'private'):
        if not isinstance(origin, str) or not ORIGIN.fullmatch(origin):
            raise RemoteRefused('A Project origin is 32 hexadecimal characters')
        if visibility not in ('private', 'public'):
            raise RemoteRefused('A Hooke Cloud Project is private or public')
        self.base, self.origin, self.token, self.visibility = _base(base), origin, token, visibility
        self.http = http or _Http(token)

    def describe(self):
        return {'kind': 'hooke-cloud', 'base': f'{self.base}/api/projects/{self.origin}/objects',
                'visibility': self.visibility}

    def _link(self, url):
        """An object link the service gave: https anywhere (R2 signs its own), or this service's own address."""
        if not isinstance(url, str) or any(ord(c) < 33 for c in url):
            return False
        return url.startswith('https://') or url.startswith(self.base + '/')

    def _post(self, route, payload, *, what, auth=True):
        status, data = self.http.request('POST', f'{self.base}/api/projects/{self.origin}/{route}', json=payload,
                                         headers={'Content-Type': 'application/json'}, auth=auth)
        if status == 401:
            raise RemoteRefused('Sign in to Hooke Cloud again')
        if status >= 400 or not isinstance(data, dict) or data.get('ok') is not True:
            said = untrusted.inline(data.get('error', '') if isinstance(data, dict) else '', 200)
            raise RemoteRefused(f'Hooke Cloud refused {what} ({status}{": " + said if said else ""})')
        return data

    def missing(self, digests):
        if any(not DIGEST.fullmatch(d) for d in digests):
            raise RemoteRefused('An object name is not a SHA-256')
        lacking = []
        for start in range(0, len(digests), BATCH):
            batch = list(digests[start:start + BATCH])
            data = self._post('objects:missing', {'digests': batch}, what='checking which files it holds')
            found = data.get('missing')
            if type(found) is not list or not set(found) <= set(batch):
                raise ChangedOutsideHooke('Hooke Cloud answered about files it was not asked about')
            lacking += found
        return lacking

    def put(self, digest, path, size, *, check=lambda: None):
        if not DIGEST.fullmatch(digest) or not 0 <= size <= MAX_OBJECT:
            raise RemoteRefused('A large file is larger than one Hooke Cloud object may be')
        with open(path, 'rb') as stream:
            body = stream.read(size + 1)
        if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
            raise RemoteRefused('A large file changed while sending; nothing was sent')
        check()
        data = self._post('objects:upload-urls', {'objects': [{'sha256': digest, 'size': size}]},
                          what='storing a large file')
        entries = data.get('objects')
        if type(entries) is not list or len(entries) != 1 or entries[0].get('sha256') != digest:
            raise ChangedOutsideHooke('Hooke Cloud answered for a different file')
        entry = entries[0]
        if entry.get('present') is True:
            return False
        url, headers = entry.get('url'), entry.get('headers') or {}
        if entry.get('method') != 'PUT' or not self._link(url) or type(headers) is not dict:
            raise ChangedOutsideHooke('Hooke Cloud gave an unexpected upload link')
        status, _ = self.http.request('PUT', url, content=body, headers={str(k): str(v) for k, v in headers.items()},
                                      auth=False)
        if status == 412:  # R2's write-once condition: the object arrived meanwhile (its name is its content)
            return False
        if status >= 400:
            raise RemoteRefused(f'Hooke Cloud did not keep a large file ({status}); nothing was published')
        return True

    def get(self, digest, size, target, *, check=lambda: None):
        if not DIGEST.fullmatch(digest):
            raise RemoteRefused('An object name is not a SHA-256')
        data = self._post('objects:download-urls', {'digests': [digest]}, what='providing a large file',
                          auth=bool(self.token))
        entries = data.get('objects')
        if type(entries) is not list or len(entries) != 1 or entries[0].get('sha256') != digest:
            raise RemoteRefused('Hooke Cloud does not hold a large file of this project')
        url = entries[0].get('url')
        if not self._link(url):
            raise ChangedOutsideHooke('Hooke Cloud gave an unexpected download link')
        check()
        status, body = self.http.request('GET', url, auth=False, limit=size)
        if status != 200 or len(body) != size or hashlib.sha256(body).hexdigest() != digest:
            raise ChangedOutsideHooke('A large file from Hooke Cloud does not match its name')
        with open(target, 'xb') as stream:
            stream.write(body)
