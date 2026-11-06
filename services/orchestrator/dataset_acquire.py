"""A published dataset into a Project: `dataset.acquire` (HOOKE-GAPS rows 1-3, VCC 2026).

On the night of 5 Nov 2026 the reference data (Nadig 2025 from GEO, Replogle 2022 from figshare+, DepMap 24Q4) was
fetched with curl, resumed by hand and checked by hand. This is the same work inside Hooke, for a person or an agent:

1. **A manifest names the dataset** (`contract/agent/dataset-manifest.schema.json`): its name, its licence, where it
   is described, and each file's address, with an optional mirror, size, md5 and sha256. A figshare article can state
   the sizes, md5s and licence itself (`figshare`), so nobody types them.
2. **Licence first.** A dataset whose stated terms are not one of `LICENCES` (open data licences, and NCBI's "no
   restrictions" for GEO) is refused before anything is fetched: the owner can download it and import the file.
   A figshare article's own licence must be the one the manifest states.
3. **Space first.** What is still to fetch, plus one copy of the largest file while it is retained, plus the spare
   every download keeps (`downloads.SPARE`), must be free, or nothing starts.
4. **Each file is fetched safely and resumably.** The address rules are the URL intake's (`scientific_url_intake`:
   https, no credentials, public addresses on every hop, no cookies, the stored bytes as they are). Bytes go to a
   private `.part` under the data home, outside the Project folder (so no export, backup or copy of the Project ever
   holds half a file), that survives a stop, a dropped connection or a restart; the next attempt
   asks for the rest (HTTP Range, with If-Range so a changed file is never spliced) and starts again where the server
   cannot resume.
5. **A mirror when the publisher's own place refuses.** A web page instead of the file (depmap.org answers automated
   clients with a verification page: it is never bypassed), a refusal, a missing file, a wrong size or wrong bytes
   moves to the file's declared mirror; the receipt says the mirror was used and why.
6. **Verified, then retained.** The whole file is hashed (sha256, and md5 when the publisher states one) and must
   match every checksum and size given. Only then is it retained, as exact bytes, in the Project (`FileIntake`), with
   its `sourceDataset` receipt: dataset, licence, where it came from (and the mirror and why), when, size, sha256 and
   md5. The `.part` is removed once the file is retained.
7. **A dataset receipt.** After the last file, a JSON receipt naming every retained file revision is retained beside
   them (`<name>.dataset.json`), so the dataset reads back as one thing.

A retry of the same request returns the files already retained without fetching them again. Nothing is parsed or
decoded: what the data means is for the readers and capabilities that read it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import urllib.parse

from . import scientific_files as files
from .downloads import SPARE
from .file_locks import fcntl
from .scientific_file_intake import FileIntake, REQUEST, WORKING_BYTES, request_identity
from .scientific_projects import Conflict, InvalidProject, _identity
from . import scientific_url_intake as web

FORMAT = 'dataset-manifest-v1'
RECEIPT_FORMAT = 'dataset-receipt-v1'
RECORD_FORMAT = 1
MAX_FILES = 64
MAX_FILE_BYTES = files.CHUNK_BYTES * files.MAX_CHUNKS
MAX_DATASET_BYTES = 256 << 30
MAX_METADATA_BYTES = 4 << 20  # a figshare article's JSON
READ_SECONDS = 60
ATTEMPTS = 3  # tries of one address when its connection drops, each resuming
RETRY_SECONDS = (2, 5, 0)
USER_AGENT = 'Hooke dataset acquisition'
SPOOL = 'dataset-acquire'  # under the data home (the door passes <home>/downloads)
TRUST = 'untrusted published data; kept as exact bytes and never parsed at acquisition'
MD5 = re.compile(r'[0-9a-f]{32}')
SHA256 = re.compile(r'[0-9a-f]{64}')
FIGSHARE_API = 'https://api.figshare.com/v2/articles/'

# Terms Hooke acquires data under: each allows reuse and redistribution with the Project (some only non-commercially,
# said in the receipt). Anything else, or nothing stated, is refused: the owner downloads it and imports the file.
LICENCES = {
    'CC-BY-4.0': 'Creative Commons Attribution 4.0: reuse and redistribution with attribution',
    'CC-BY-3.0': 'Creative Commons Attribution 3.0: reuse and redistribution with attribution',
    'CC-BY-SA-4.0': 'Creative Commons Attribution-ShareAlike 4.0: reuse with attribution, shared alike',
    'CC-BY-NC-4.0': 'Creative Commons Attribution-NonCommercial 4.0: non-commercial reuse with attribution',
    'CC0-1.0': 'Creative Commons Zero: no rights reserved',
    'ODC-BY-1.0': 'Open Data Commons Attribution: reuse with attribution',
    'ODbL-1.0': 'Open Database License: reuse with attribution, shared alike',
    'PDDL-1.0': 'Open Data Commons Public Domain Dedication',
    'NCBI-public': ('NCBI places no restrictions on the use or distribution of GEO and SRA data; submitters may '
                    'claim other rights, so cite the record and its paper'),
}
# How publishers write them (figshare's `license.name`).
PUBLISHER_NAMES = {'CC BY 4.0': 'CC-BY-4.0', 'CC BY 3.0': 'CC-BY-3.0', 'CC BY-SA 4.0': 'CC-BY-SA-4.0',
                   'CC BY-NC 4.0': 'CC-BY-NC-4.0', 'CC0': 'CC0-1.0', 'CC0 1.0': 'CC0-1.0',
                   'ODC-BY 1.0': 'ODC-BY-1.0', 'ODbL 1.0': 'ODbL-1.0', 'PDDL 1.0': 'PDDL-1.0'}

# Tests replace the transport; production connects directly through the URL intake's checked resolver.
TRANSPORT = None


class Refused(InvalidProject):
    """Acquisition refused or failed, in plain words, with the step that helps."""

    def __init__(self, says, *, next, code='dataset-refused', status=400):
        super().__init__(says)
        self.says, self.next, self.code, self.status = says, next, code, status


class _Unavailable(Exception):
    """This address did not give the file (a web page, a refusal, a wrong size or wrong bytes): try its mirror."""

    def __init__(self, says):
        super().__init__(says)
        self.says = says


class Stopped(InvalidProject):
    pass


# --- the manifest ----------------------------------------------------------------------------------------------------
def _text(value, limit, what, *, required=True):
    if value is None and not required:
        return None
    if type(value) is not str or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise Refused(f'The manifest needs {what} as one line of at most {limit} characters.', next='fix the manifest')
    return value


def _url(value, what):
    try:
        web.check_url(value)
    except web.RemoteRefused as error:
        raise Refused(f'{what}: {error}', next=error.next) from None
    return value


def _size(value, what):
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_FILE_BYTES:
        raise Refused(f'{what} must be a whole number of bytes, at most 64 GiB.', next='fix the manifest')
    return value


def check_manifest(manifest):
    """The manifest as Hooke acquires it, or a refusal naming what is wrong. Licence is checked first."""
    from .door.schema import problems
    if type(manifest) is not dict:
        raise Refused('Give the dataset as one manifest object.', next='see dataset-manifest in the agent contract')
    licence = manifest.get('licence')
    if type(licence) is not dict or type(licence.get('id')) is not str:
        raise Refused('The manifest states no licence, so Hooke will not acquire it.', code='licence-missing',
                      next='state the publisher\'s licence, or download the data yourself and import the file')
    if licence['id'] not in LICENCES:
        raise Refused(f'Hooke acquires data only under open terms ({", ".join(LICENCES)}); this dataset states '
                      f'{licence["id"][:80]!r}.', code='licence-not-admitted', status=403,
                      next='the Project owner can download it under its terms and import the file')
    found = problems(manifest, 'dataset-manifest')
    if found:
        raise Refused('The manifest does not fit dataset-manifest: ' + '; '.join(found[:4]), next='fix the manifest')
    _text(manifest['name'], 120, 'a name')
    for key in ('page',):
        if key in manifest:
            _url(manifest[key], 'The dataset page')
    if 'url' in licence:
        _url(licence['url'], 'The licence address')
    figshare = manifest.get('figshare')
    names = set()
    for i, f in enumerate(manifest['files']):
        try:
            files._name(f['name'])
        except InvalidProject:
            raise Refused(f'File {i + 1} has a name Hooke cannot keep ({f["name"][:80]!r}).', next='name it plainly') from None
        if f['name'] in names:
            raise Refused(f'{f["name"]} is named twice.', next='name each file once')
        names.add(f['name'])
        if 'url' in f:
            _url(f['url'], f['name'])
        elif 'figshareFile' not in f:
            raise Refused(f'{f["name"]} has no address.', next='give its url, or its figshareFile with the article')
        if 'figshareFile' in f and figshare is None:
            raise Refused(f'{f["name"]} names a figshare file but the manifest names no figshare article.',
                          next='add figshare: {article}')
        if 'mirror' in f:
            _url(f['mirror']['url'], f'{f["name"]}\'s mirror')
        _size(f.get('size'), f'{f["name"]}\'s size')
    return manifest


def digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# --- one safe GET, following redirects with the URL intake's checks --------------------------------------------------
def _client():
    import httpx
    return httpx.Client(transport=TRANSPORT, trust_env=False, follow_redirects=False,
                        timeout=httpx.Timeout(READ_SECONDS, connect=web.CONNECT_SECONDS),
                        headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity', 'Accept': '*/*'})


class _Answer:
    def __init__(self, response, final, redirects):
        self.response, self.final, self.redirects = response, final, redirects


def _open(client, url, headers, stack):
    """Open `url` (redirects followed, each hop checked) as a streamed response entered on `stack`."""
    current, redirects = url, 0
    while True:
        _, host, port = web.check_url(current)
        web.public(host, port)
        client.cookies.clear()  # nothing a server set is ever sent back
        response = stack.enter_context(client.stream('GET', current, headers=headers))
        peer = web._peer(response)
        if peer is not None and not web._public_ip(peer):
            raise web.RemoteRefused(f'{host} connected to a non-public address, so Hooke stopped.',
                                    next='use a public address for the data')
        if response.status_code in web.REDIRECTS and response.headers.get('location'):
            if redirects >= web.MAX_REDIRECTS:
                raise _Unavailable('it redirected more than three times')
            try:
                current = urllib.parse.urljoin(current, response.headers['location'])
            except ValueError:
                raise _Unavailable('it redirected to a malformed address') from None
            redirects += 1
            response.close()
            continue
        return _Answer(response, current, redirects)


def figshare_article(article, version=None, *, stop=lambda: False):
    """A figshare article's own record (files with size and md5, licence, DOI), fetched with the same checks."""
    import contextlib
    import httpx
    url = FIGSHARE_API + str(article) + (f'/versions/{version}' if version else '')
    body = bytearray()
    try:
        with _client() as client, contextlib.ExitStack() as stack:
            answer = _open(client, url, {}, stack)
            if answer.response.status_code != 200:
                raise Refused(f'figshare answered HTTP {answer.response.status_code} for article {article}.',
                              next='check the article number')
            for block in answer.response.iter_raw():
                if stop():
                    raise Stopped('Acquisition stopped; nothing was kept.')
                body += block
                if len(body) > MAX_METADATA_BYTES:
                    raise Refused('figshare\'s article record is unexpectedly large.', next='retry later')
    except (_Unavailable, web.RemoteRefused, httpx.HTTPError) as error:
        raise Refused(f'figshare\'s record of article {article} could not be read ({_said(error)}).',
                      next='check your connection, then retry') from None
    try:
        value = json.loads(bytes(body))
        listed = {int(f['id']): {'name': str(f['name']), 'size': int(f['size']),
                                 'md5': str(f.get('computed_md5') or f.get('supplied_md5') or ''),
                                 'url': str(f['download_url'])} for f in value['files']}
        licence = str((value.get('license') or {}).get('name') or '')
    except (ValueError, KeyError, TypeError):
        raise Refused(f'figshare\'s record of article {article} is not one Hooke understands.', next='retry later') from None
    return {'article': int(article), 'version': value.get('version'), 'doi': value.get('doi'),
            'licence': PUBLISHER_NAMES.get(licence, licence), 'files': listed}


def _said(error):
    return getattr(error, 'says', None) or str(error)[:200] or type(error).__name__


def resolve(manifest, *, stop=lambda: False, article=figshare_article):
    """Each file as it will be fetched: its address, mirror and expected size and checksums, the publisher's own where
    a figshare article states them (a manifest value that disagrees is refused)."""
    figshare = manifest.get('figshare')
    record = article(figshare['article'], figshare.get('version'), stop=stop) if figshare else None
    if record is not None and record['licence'] != manifest['licence']['id']:
        raise Refused(f'figshare states this article\'s licence as {record["licence"]!r}, not '
                      f'{manifest["licence"]["id"]!r}.', code='licence-mismatch', status=409,
                      next='state the licence the publisher gives')
    out = []
    for f in manifest['files']:
        plan = {'name': f['name'], 'url': f.get('url'), 'mirror': f.get('mirror'), 'size': f.get('size'),
                'md5': f.get('md5'), 'sha256': f.get('sha256'), 'publisher': {}}
        if 'figshareFile' in f:
            listed = record['files'].get(f['figshareFile'])
            if listed is None:
                raise Refused(f'figshare article {record["article"]} has no file {f["figshareFile"]}.',
                              next='check the file number')
            for key in ('size', 'md5'):
                if plan[key] is not None and listed[key] and plan[key] != listed[key]:
                    raise Refused(f'{f["name"]}: the manifest\'s {key} disagrees with figshare\'s.', code='checksum-mismatch',
                                  status=409, next='use the publisher\'s value')
                plan[key] = plan[key] if plan[key] is not None else (listed[key] or None)
            plan['publisher'] = {'figshare': {'article': record['article'], 'version': record['version'],
                                              'doi': record['doi'], 'file': f['figshareFile'], 'name': listed['name']}}
            if plan['url'] is None:
                plan['url'] = listed['url']
        plan['publisher'].update({k: plan[k] for k in ('size', 'md5', 'sha256') if plan[k] is not None})
        out.append(plan)
    return out


# --- the file receipt, kept in the retained document's state ---------------------------------------------------------
RECORD = ('format', 'dataset', 'licence', 'url', 'from', 'source', 'final', 'redirects', 'retrieved', 'mediaType',
          'size', 'sha256', 'md5', 'publisher', 'trust')
OPTIONAL = ('citation', 'page', 'primaryRefused', 'mirrorWhy')


def record_of(state):
    """The validated acquisition receipt of a source document, or None; a malformed one is refused."""
    value = state.get('sourceDataset')
    if value is None:
        return None
    source = state.get('sourceFile')
    if (type(value) is not dict or not set(RECORD) <= set(value) or set(value) - set(RECORD) - set(OPTIONAL)
            or value['format'] != RECORD_FORMAT or value['trust'] != TRUST or value['from'] not in ('primary', 'mirror')
            or type(value['licence']) is not dict or value['licence'].get('id') not in LICENCES
            or not all(type(value[k]) is str and value[k].startswith('https://') and len(value[k]) <= web.MAX_URL
                       for k in ('url', 'source', 'final'))
            or type(value['redirects']) is not int or type(value['publisher']) is not dict
            or type(source) is not dict or value['size'] != source.get('size') or value['sha256'] != source.get('sha256')
            or type(value['sha256']) is not str or not SHA256.fullmatch(value['sha256'])
            or not (value['md5'] is None or (type(value['md5']) is str and MD5.fullmatch(value['md5'])))
            or type(value['retrieved']) is not str or not web.RETRIEVED.fullmatch(value['retrieved'])):
        raise InvalidProject('An acquired dataset file must keep its exact acquisition receipt')
    return value


class _Retained(FileIntake):
    def __init__(self, *args, receipt, **kwargs):
        self.acquired = receipt
        super().__init__(*args, **kwargs)

    def document(self, ref):
        document = super().document(ref)
        document['state']['sourceDataset'] = dict(self.acquired)
        return document


# --- acquisition -----------------------------------------------------------------------------------------------------
class Acquisition:
    """One request to acquire one dataset into one Project, captured before any byte arrives."""

    def __init__(self, store, session, request, manifest, *, spool, producer=None, article=figshare_article):
        self.store = store.for_session(session, write=True)
        _identity(session, 'session')
        if type(request) is not str or not REQUEST.fullmatch(request):
            raise Refused('The acquisition request has an invalid identity.', next='start it again')
        self.session, self.request, self.manifest = session, request, check_manifest(manifest)
        self.producer, self.article = producer, article
        self.key = hashlib.sha256((session + '\0' + request).encode()).hexdigest()[:32]
        # Outside the Project folder: a Project's export, backup and copy never see a half-fetched file.
        self.spool = Path(spool) / self.key

    def _request(self, name):
        return hashlib.sha256((self.request + '\0' + name).encode()).hexdigest()[:40]

    def _existing(self, name):
        target, _ = request_identity(self.session, self._request(name))
        if not self.store._folder(target).exists():
            return None
        return self.store.read(target, self.session, revision=1)

    def _have(self, name):
        part = self.spool / (hashlib.sha256(name.encode()).hexdigest()[:24] + '.part')
        return part, part.with_suffix('.json')

    def run(self, stopping=lambda: False, progress=lambda got, total, says: None):
        def check():
            if stopping():
                raise Stopped('Acquisition stopped; what was fetched is kept for the next attempt.')
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.spool.mkdir(parents=True, exist_ok=True)
        lock = (self.spool / '.lock').open('a')
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused('This dataset is already being acquired by the same request.', code='acquisition-busy',
                              status=409, next='wait for that operation') from None
            result = self._run(check, progress)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
        shutil.rmtree(self.spool, ignore_errors=True)  # every file is retained: nothing is left to resume
        return result

    def _run(self, check, progress):
        plans = resolve(self.manifest, stop=lambda: bool(check()), article=self.article)
        retained, todo = {}, []
        for plan in plans:
            saved = self._existing(plan['name'])
            if saved is not None:
                acquired = record_of(saved.document['state'])
                if acquired is None or acquired['url'] != plan['url'] or any(
                        plan[k] is not None and acquired[k] != plan[k] for k in ('size', 'sha256', 'md5')):
                    raise Conflict(f'This acquisition request already retained a different {plan["name"]}; start a new request')
                retained[plan['name']] = saved
            else:
                todo.append(plan)
        total = sum(p['size'] or 0 for p in plans)
        if total > MAX_DATASET_BYTES:
            raise Refused('This dataset is larger than 256 GiB, Hooke\'s limit for one acquisition.', status=413,
                          code='too-large', next='acquire it in parts')
        remaining = sum(max(0, (p['size'] or 0) - self._resumable(p)) for p in todo)
        largest = max([p['size'] or 0 for p in todo], default=0)
        free = min(shutil.disk_usage(self.store.root).free, shutil.disk_usage(self.spool).free)
        if remaining + largest + WORKING_BYTES + SPARE > free:
            raise Refused(f'This dataset needs {_words(remaining + largest)} free while it is fetched and retained '
                          f'(plus {_words(SPARE)} kept spare); the disk has {_words(free)}.', code='disk',
                          status=507, next='free some space, then retry the same request: what was fetched is kept')
        state = {'got': sum(p['size'] or 0 for p in plans if p['name'] in retained), 'total': total, 'at': 0.0}

        def advance(n, says):
            state['got'] += n
            now = time.monotonic()
            if now - state['at'] > 1:
                state['at'] = now
                progress(state['got'], state['total'], says)
        for plan in todo:
            check()
            retained[plan['name']] = self._acquire(plan, check, advance)
        progress(state['total'], state['total'], 'Writing the dataset receipt')
        return self._receipt(plans, retained, check), [retained[p['name']] for p in plans]

    def _resumable(self, plan):
        part, meta = self._have(plan['name'])
        try:
            known = json.loads(meta.read_text())
        except (OSError, ValueError):
            return 0
        return part.stat().st_size if part.exists() and known.get('url') in (plan['url'], (plan['mirror'] or {}).get('url')) else 0

    def _acquire(self, plan, check, advance):
        places = [('primary', plan['url'], None)]
        if plan['mirror']:
            places.append(('mirror', plan['mirror']['url'], plan['mirror'].get('why')))
        refusals = []
        for where, url, why in places:
            try:
                fetched = self._fetch(plan, url, check, advance)
                sha256, md5 = self._verify(plan, fetched['part'], check)
            except _Unavailable as error:
                part, meta = self._have(plan['name'])
                advance(-(part.stat().st_size if part.exists() else 0), plan['name'])
                part.unlink(missing_ok=True)  # never spliced with another server's bytes, never kept wrong
                meta.unlink(missing_ok=True)
                refusals.append(error.says)
                continue
            receipt = {'format': RECORD_FORMAT, 'dataset': self.manifest['name'],
                       'licence': {'id': self.manifest['licence']['id'],
                                   **({'url': self.manifest['licence']['url']} if 'url' in self.manifest['licence'] else {}),
                                   'terms': LICENCES[self.manifest['licence']['id']]},
                       'url': plan['url'], 'from': where, 'source': url, 'final': unsigned(fetched['final']),
                       'redirects': fetched['redirects'], 'retrieved': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       'mediaType': fetched['mediaType'], 'size': fetched['size'], 'sha256': sha256, 'md5': md5,
                       'publisher': plan['publisher'], 'trust': TRUST}
            for key in ('citation', 'page'):
                if key in self.manifest:
                    receipt[key] = self.manifest[key]
            if where == 'mirror':
                receipt['primaryRefused'] = refusals[0]
                if why:
                    receipt['mirrorWhy'] = why
            return self._retain(plan['name'], fetched['part'], receipt, check)
        host = urllib.parse.urlsplit(plan['url']).hostname
        more = f' Its mirror did not give it either: {refusals[1]}.' if len(refusals) > 1 else ''
        raise Refused(f'{plan["name"]} could not be acquired from {host}: {refusals[0]}.{more} Nothing of it was kept.',
                      code='source-unavailable', status=502,
                      next='check the address with the data\'s publisher, or give the file a mirror')

    def _fetch(self, plan, url, check, advance):
        """The whole file into its `.part`, resuming where the server allows. Returns where it came from."""
        import contextlib
        import httpx
        part, meta = self._have(plan['name'])
        part.parent.mkdir(parents=True, exist_ok=True)
        try:
            known = json.loads(meta.read_text())
        except (OSError, ValueError):
            known = {}
        if known.get('url') != url:
            part.unlink(missing_ok=True)
            known = {'url': url}
        counted = part.stat().st_size if part.exists() else 0
        advance(counted, plan['name'])
        for attempt in range(ATTEMPTS):
            check()
            start = part.stat().st_size if part.exists() else 0
            if known.get('final') and start == known.get('size'):
                return {**known, 'part': part}  # whole already; verified next
            if plan['size'] is not None and start > plan['size']:
                part.unlink()
                start = 0
            advance(start - counted, plan['name'])
            counted = start
            headers = {}
            if start and known.get('validator'):
                headers = {'Range': f'bytes={start}-', 'If-Range': known['validator']}
            try:
                with _client() as client, contextlib.ExitStack() as stack:
                    answer = _open(client, url, headers, stack)
                    r = answer.response
                    if r.status_code == 416 and plan['size'] is not None and start == plan['size']:
                        known.update(final=answer.final, size=start, redirects=answer.redirects)
                        known.setdefault('mediaType', '')
                        meta.write_text(json.dumps(known))
                        return {**known, 'part': part}
                    if r.status_code in (401, 403, 407):
                        raise _Unavailable(f'the server refused access (HTTP {r.status_code}); Hooke sends no credentials')
                    if r.status_code in (404, 410):
                        raise _Unavailable(f'nothing is at that address (HTTP {r.status_code})')
                    if r.status_code not in (200, 206):
                        raise _Unavailable(f'the server answered HTTP {r.status_code} instead of the file')
                    encoding = r.headers.get('content-encoding', 'identity').strip().lower()
                    if encoding not in ('', 'identity'):
                        raise _Unavailable(f'the server sent it compressed ({encoding[:40]}), not as stored')
                    media = ''.join(c for c in r.headers.get('content-type', '')[:100] if 32 <= ord(c) < 127)
                    if media.lower().startswith('text/html') and not plan['name'].lower().endswith(('.html', '.htm')):
                        raise _Unavailable('it answered with a web page (a sign-in or bot verification page), not the file; '
                                           'Hooke does not get past such checks')
                    if r.status_code == 206:
                        first = _range_start(r.headers.get('content-range', ''))
                        if first != start:
                            raise _Unavailable('the server resumed at the wrong place')
                    elif start:
                        advance(-start, plan['name'])  # it cannot resume here: start this file again, safely
                        counted -= start
                        start = 0
                    stated = r.headers.get('content-length')
                    length = int(stated) if stated is not None and stated.isdigit() else None
                    whole = None if length is None else start + length
                    if whole is not None and plan['size'] is not None and whole != plan['size']:
                        raise _Unavailable(f'it offers {whole:,} bytes, not the {plan["size"]:,} the publisher states')
                    if whole is None and plan['size'] is None:
                        raise _Unavailable('the server states no size and the manifest gives none, so a truncated file '
                                           'could not be told from a whole one')
                    expected = plan['size'] if plan['size'] is not None else whole
                    if expected > MAX_FILE_BYTES:
                        raise Refused(f'{plan["name"]} is larger than 64 GiB, Hooke\'s limit for one file.', status=413,
                                      next='acquire a smaller file')
                    free = min(shutil.disk_usage(self.store.root).free, shutil.disk_usage(self.spool).free)
                    if 2 * expected - start + SPARE > free:
                        raise Refused(f'{plan["name"]} needs {_words(2 * expected - start)} free while it is fetched '
                                      f'and retained; the disk has {_words(free)}.', code='disk', status=507,
                                      next='free some space, then retry: what was fetched is kept')
                    tag = r.headers.get('etag') or ''
                    known.update(url=url, final=None, redirects=answer.redirects, mediaType=media,
                                 validator=(tag if tag and not tag.startswith('W/') else r.headers.get('last-modified')))
                    meta.write_text(json.dumps(known))
                    size = start
                    with part.open('ab' if start else 'wb') as out:
                        for block in r.iter_raw():
                            check()
                            size += len(block)
                            if size > expected:
                                raise _Unavailable(f'it sent more than the {expected:,} bytes it stated')
                            out.write(block)
                            counted += len(block)
                            advance(len(block), plan['name'])
                        out.flush()
                        os.fsync(out.fileno())
                    if size != expected:
                        raise httpx.RemoteProtocolError(f'ended after {size:,} of {expected:,} bytes')
                    known.update(final=answer.final, size=size)
                    meta.write_text(json.dumps(known))
                    return {**known, 'part': part}
            except (httpx.TransportError, httpx.RemoteProtocolError) as error:
                if attempt + 1 == ATTEMPTS:  # kept for the next attempt at the same address, never the mirror's
                    raise Refused(f'The connection to {urllib.parse.urlsplit(url).hostname} kept dropping while '
                                  f'{plan["name"]} was fetched ({type(error).__name__}).', code='connection-dropped',
                                  status=503, next='retry the same request: it carries on where it stopped') from None
                time.sleep(RETRY_SECONDS[attempt])
            except web.RemoteRefused as error:
                raise _Unavailable(str(error).rstrip('.')) from None
            except OSError as error:
                if getattr(error, 'errno', None) == 28:
                    raise Refused('The disk filled up while fetching the dataset.', code='disk', status=507,
                                  next='free some space, then retry: what was fetched is kept') from None
                raise
        raise AssertionError('unreachable')

    def _verify(self, plan, part, check):
        sha, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
        with part.open('rb') as handle:
            while block := handle.read(8 * files.CHUNK_BYTES):
                check()
                sha.update(block)
                md5.update(block)
        got = {'sha256': sha.hexdigest(), 'md5': md5.hexdigest()}
        for key in ('sha256', 'md5'):
            if plan[key] is not None and got[key] != plan[key]:
                raise _Unavailable(f'its bytes have {key} {got[key]}, not the publisher\'s {plan[key]}')
        return got['sha256'], got['md5']

    def _retain(self, name, part, receipt, check):
        intake = _Retained(self.store, self.session, self._request(name), name=name, size=receipt['size'],
                           max_bytes=MAX_FILE_BYTES, receipt=receipt, producer=self.producer)
        try:
            with part.open('rb') as body:
                while block := body.read(files.CHUNK_BYTES):
                    check()
                    intake.write(block)
            if intake.writer.digest.hexdigest() != receipt['sha256']:
                raise InvalidProject('The fetched file changed before it was retained; nothing was kept')
            saved = intake.publish(lambda: bool(check()))
        finally:
            intake.close()
        part.unlink(missing_ok=True)
        self._have(name)[1].unlink(missing_ok=True)
        return saved

    def _receipt(self, plans, retained, check):
        from .scientific_comparisons import identity
        body = {'format': RECEIPT_FORMAT, 'dataset': self.manifest['name'], 'manifestSha256': digest(self.manifest),
                'licence': {**self.manifest['licence'], 'terms': LICENCES[self.manifest['licence']['id']]},
                **{k: self.manifest[k] for k in ('citation', 'page', 'terms') if k in self.manifest},
                'files': []}
        for plan in plans:
            saved = retained[plan['name']]
            acquired = record_of(saved.document['state'])
            body['files'].append({'name': plan['name'], 'artifact': identity(saved), 'size': acquired['size'],
                                  'sha256': acquired['sha256'], 'md5': acquired['md5'], 'from': acquired['from'],
                                  'source': acquired['source'], 'retrieved': acquired['retrieved'],
                                  **({'primaryRefused': acquired['primaryRefused']} if 'primaryRefused' in acquired else {})})
        encoded = (json.dumps(body, indent=1, sort_keys=True) + '\n').encode()
        name = re.sub(r'[^A-Za-z0-9._-]+', '-', self.manifest['name']).strip('-.')[:80] or 'dataset'
        intake = FileIntake(self.store, self.session, self._request('\0receipt'), name=f'{name}.dataset.json',
                            size=len(encoded), max_bytes=len(encoded), producer=self.producer)
        try:
            intake.write(encoded)
            saved = intake.publish(lambda: bool(check()))
        finally:
            intake.close()
        return {**body, 'artifact': identity(saved)}


SIGNING = re.compile(r'(^x-amz-|signature|credential|token|^expires$|^key$|^policy$)', re.IGNORECASE)


def unsigned(url):
    """The address without a pre-signed query (S3's X-Amz-*, a signature, token or key): it expires within seconds
    and is no part of where the data is, so a receipt that may be published never keeps it."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if not any(SIGNING.search(k) for k, _ in query):
        return url
    return urllib.parse.urlunsplit(parts._replace(query=''))


def _range_start(header):
    match = re.fullmatch(r'bytes (\d+)-\d+/(\d+|\*)', header.strip())
    return int(match.group(1)) if match else -1


def _words(n):
    return f'{n / (1 << 30):.1f} GiB' if n >= 1 << 30 else f'{max(1, round(n / (1 << 20)))} MiB'
