"""A scientific source from a web address: a bounded, cancellable download into the retained intake.

Web content is untrusted data. The address must be https, carry no credentials and resolve only to public
addresses, on every redirect (at most 3) and for the address actually connected to. Hooke sends no cookies,
keys, `.netrc` credentials or proxy settings (`trust_env=False`), and asks for the bytes as stored
(`Accept-Encoding: identity`): a compressed transfer is refused rather than decoded, so the SHA-256 is of the
server's own bytes. The size is bounded before (Content-Length) and during the transfer, the whole download by a
deadline, and the caller's stop is checked between chunks. Nothing is parsed or decoded here.

The bytes are spooled privately, checked against the researcher's expected SHA-256 when one is given, and only
then handed to `FileIntake`, which owns staging, the retry identity and atomic publication. A refused, failed or
stopped download keeps nothing. The receipt (`state.sourceRemote`) records the address as given, the address the
bytes came from, the redirects, when, the server's stated media type (untrusted, informational) and the exact
size and SHA-256. A retry of the same request returns the same receipt without downloading again.
"""
from __future__ import annotations

import hashlib
import ipaddress
from pathlib import Path
import re
import shutil
import socket
import tempfile
import time
import urllib.parse

from . import scientific_files as files
from .downloads import SPARE
from .scientific_file_intake import FileIntake, REQUEST, WORKING_BYTES
from .scientific_projects import Conflict, InvalidProject, _identity

MAX_BYTES = 64 << 20  # the same allowance as a selected file (scientific_file_api.MAX_UPLOAD)
MAX_REDIRECTS = 3
MAX_URL = 2048
CONNECT_SECONDS = 10
READ_SECONDS = 30
TOTAL_SECONDS = 300
CHUNK = files.CHUNK_BYTES
USER_AGENT = 'Hooke scientific source download'
REDIRECTS = (301, 302, 303, 307, 308)
FORMAT = 1
TRUST = 'untrusted web content; kept as exact bytes and never parsed at intake'
RECORD = ('format', 'url', 'final', 'redirects', 'retrieved', 'mediaType', 'size', 'sha256', 'trust')
DIGEST = re.compile(r'[0-9a-f]{64}')
RETRIEVED = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ')

# Tests replace these; production resolves through the system and connects directly.
TRANSPORT = None
RESOLVE = socket.getaddrinfo


class RemoteRefused(InvalidProject):
    """A refused download in plain words, with the step that helps."""

    def __init__(self, says, *, next, status=400):
        super().__init__(says)
        self.next, self.status = next, status


def check_url(url):
    if (type(url) is not str or not 0 < len(url) <= MAX_URL
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
        raise RemoteRefused('Give one web address of at most 2,048 characters, without spaces.',
                            next='paste the file’s address again')
    try:
        parsed = urllib.parse.urlsplit(url)
        host, port = parsed.hostname, parsed.port or 443
    except ValueError as error:
        raise RemoteRefused('This web address is malformed.', next='check the address') from error
    if parsed.scheme.lower() != 'https':
        raise RemoteRefused('Hooke downloads scientific sources only over https.',
                            next='use the https address of the file')
    if parsed.username is not None or parsed.password is not None:
        raise RemoteRefused('This address contains a user name or password. Hooke sends no credentials with a download.',
                            next='use a public address for the file, or download it yourself and import the file')
    if not host:
        raise RemoteRefused('This web address has no host.', next='check the address')
    if not host.isascii():
        # The resolver (IDNA 2003) and httpx (IDNA 2008) can name different hosts for the same characters, so the
        # host checked must be the host connected to: its ASCII (punycode) form.
        raise RemoteRefused('This web address has a non-ASCII host name.',
                            next='use the address with its ASCII (xn--) host name')
    return parsed, host, port


NAT64 = (ipaddress.ip_network('64:ff9b::/96'), ipaddress.ip_network('64:ff9b:1::/48'))
COMPATIBLE = ipaddress.ip_network('::/96')


def _public_ip(text):
    ip = ipaddress.ip_address(text.split('%', 1)[0])
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif any(ip in net for net in NAT64):  # a NAT64 gateway reaches the IPv4 address in the last 32 bits
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip.teredo is not None or ip.is_site_local or ip in COMPATIBLE:
            return False
    return ip.is_global and not ip.is_multicast


def public(host, port):
    """Every address the host resolves to must be public: no loopback, private, link-local or reserved range."""
    import concurrent.futures
    pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix='source-resolve')
    try:  # the system resolver has no timeout of its own; a stuck lookup is abandoned, never waited on
        infos = pool.submit(RESOLVE, host, port, type=socket.SOCK_STREAM).result(timeout=CONNECT_SECONDS)
    except concurrent.futures.TimeoutError as error:
        raise RemoteRefused(f'Looking up {host} took too long.', next='check your connection, then retry') from error
    except (socket.gaierror, UnicodeError, OSError) as error:
        raise RemoteRefused(f'{host} could not be found.', next='check the address and your connection, then retry') from error
    finally:
        pool.shutdown(wait=False)
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_public_ip(a) for a in addresses):
        raise RemoteRefused(f'{host} is not a public internet address, so Hooke will not download from it.',
                            next='use a public address, or download the file yourself and import the file')
    return addresses


def _peer(response):
    """The address actually connected to, where the transport reports it (it does for real sockets)."""
    stream = response.extensions.get('network_stream')
    if stream is None:
        return None
    try:
        peer = stream.get_extra_info('server_addr')
    except Exception:  # noqa: BLE001 (a transport without the information is checked by resolution alone)
        return None
    return peer[0] if isinstance(peer, tuple) and peer else None


def _name(url, given):
    if given is not None:
        return files._name(given)
    segment = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1])
    try:
        return files._name(segment[:240])
    except InvalidProject:
        return 'download'


def record_of(state):
    """The validated download receipt of a source document, or None; malformed records are refused."""
    value = state.get('sourceRemote')
    if value is None:
        return None
    source = state.get('sourceFile')
    sample = value.get('sample') if type(value) is dict else None
    if (type(value) is not dict or set(value) - {'sample'} != set(RECORD) or value['format'] != FORMAT
            or ('sample' in value and (type(sample) is not dict or set(sample) != {'capability', 'id', 'name', 'licence'}
                                       or not all(type(v) is str and len(v) <= 240 for v in sample.values())))
            or type(value['format']) is not int
            or not all(type(value[k]) is str and value[k].startswith('https://') and len(value[k]) <= MAX_URL
                       for k in ('url', 'final'))
            or type(value['retrieved']) is not str or not RETRIEVED.fullmatch(value['retrieved'])
            or type(value['mediaType']) is not str or len(value['mediaType']) > 100
            or type(value['redirects']) is not int or not 0 <= value['redirects'] <= MAX_REDIRECTS
            or type(value['retrieved']) is not str or type(value['mediaType']) is not str
            or value['trust'] != TRUST or type(source) is not dict
            or value['size'] != source.get('size') or value['sha256'] != source.get('sha256')
            or type(value['sha256']) is not str or not DIGEST.fullmatch(value['sha256'])):
        raise InvalidProject('A downloaded source must keep its exact download receipt')
    return value


INTAKE_RECORDS = {
    'sourceRemote': 'Where a source was downloaded from is written only by Hooke’s download',
    'sourceStack': 'How files were stacked is written only when Hooke imports them as a stack',
}


def guard(previous, document):
    """Only intake writes where a source came from and how it was stacked; a document save keeps both exactly."""
    state = document.get('state') if type(document) is dict else None
    if type(state) is not dict:
        return
    before = {} if previous is None else previous.document['state']
    for key, says in INTAKE_RECORDS.items():
        if state.get(key) != before.get(key):
            raise InvalidProject(says + '; a document save cannot add, change or remove it')


class _Retained(FileIntake):
    def __init__(self, *args, receipt, **kwargs):
        self.remote = receipt
        super().__init__(*args, **kwargs)

    def document(self, ref):
        document = super().document(ref)
        document['state']['sourceRemote'] = dict(self.remote)
        return document


class Download:
    """One researcher's download request, captured with its Project session before any byte arrives."""

    def __init__(self, store, session, request, url, *, sha256=None, name=None, sample=None, size=None):
        store = store.for_session(session, write=True)
        _identity(session, 'session')
        if type(request) is not str or not REQUEST.fullmatch(request):
            raise RemoteRefused('The download request has an invalid identity.', next='start the download again')
        if sha256 is not None and (type(sha256) is not str or not DIGEST.fullmatch(sha256)):
            raise RemoteRefused('The expected SHA-256 must be 64 lowercase hexadecimal characters.',
                                next='paste the digest the data’s publisher gives, or leave it empty')
        check_url(url)
        self.store, self.session, self.request, self.url, self.expected = store, session, request, url, sha256
        self.sample = sample  # a declared sample's reader and id (scientific_samples), recorded in the receipt
        self.size = size  # a declared size: the transfer may not exceed it, and must equal it
        self.name = _name(url, name)
        self.target = 'p' + hashlib.sha256((session + '\0' + request).encode()).hexdigest()[:32]
        self.stage = None

    def _existing(self):
        """A lost acknowledgement: the same request already holds this address's retained bytes."""
        if not self.store._folder(self.target).exists():
            return None
        saved = self.store.read(self.target, self.session, revision=1)
        remote = record_of(saved.document['state']) if saved.document['kind'] == 'source-file' else None
        if remote is None or remote['url'] != self.url or saved.document['title'] != self.name \
                or remote.get('sample') != self.sample \
                or (self.expected is not None and remote['sha256'] != self.expected):
            raise Conflict('This download request already belongs to another source; start the download again')
        return saved

    def run(self, stopping=lambda: False):
        if saved := self._existing():
            return saved
        self.store.root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.store.root).free < 2 * MAX_BYTES + WORKING_BYTES + SPARE:
            raise RemoteRefused('Free disk space before downloading this scientific source.', next='free some space, then retry')
        deadline = time.monotonic() + TOTAL_SECONDS
        def check():
            if stopping():
                raise RemoteRefused('Download stopped; nothing was kept.', next='start the download again when you want it')
            if time.monotonic() > deadline:
                raise RemoteRefused('The download took longer than five minutes; nothing was kept.', status=408,
                                    next='retry, or download the file yourself and import the file')
        self.stage = Path(tempfile.mkdtemp(prefix='.scientific-url-', dir=self.store.root))
        spool = self.stage / 'body'
        size, digest, final, redirects, media = self._fetch(spool, check)
        if self.expected is not None and digest != self.expected:
            raise RemoteRefused(f'The downloaded bytes have SHA-256 {digest}, not the expected {self.expected}. Nothing was kept.',
                                next='check the address and the expected digest with the data’s publisher')
        receipt = {'format': FORMAT, 'url': self.url, 'final': final, 'redirects': redirects,
                   'retrieved': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'mediaType': media,
                   'size': size, 'sha256': digest, 'trust': TRUST, **({'sample': dict(self.sample)} if self.sample else {})}
        owner = _Retained(self.store, self.session, self.request, name=self.name, size=size, max_bytes=MAX_BYTES,
                          receipt=receipt)
        try:
            with spool.open('rb') as body:
                while block := body.read(CHUNK):
                    check()
                    owner.write(block)
            if owner.writer.digest.hexdigest() != digest:
                raise InvalidProject('The spooled download changed before it was retained; nothing was kept')
            try:
                return owner.publish(lambda: stopping())
            except Conflict:
                # The same request finished first elsewhere (a duplicate in flight): its receipt is this answer.
                if saved := self._existing():
                    return saved
                raise
        finally:
            owner.close()

    def _fetch(self, spool, check):
        import httpx
        current, redirects = self.url, 0
        timeout = httpx.Timeout(READ_SECONDS, connect=CONNECT_SECONDS)
        headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity', 'Accept': '*/*'}
        with httpx.Client(transport=TRANSPORT, trust_env=False, follow_redirects=False, timeout=timeout,
                          headers=headers) as client:
            while True:
                check()
                _, host, port = check_url(current)
                public(host, port)
                client.cookies.clear()  # nothing a server set is ever sent back
                try:
                    with client.stream('GET', current) as response:
                        peer = _peer(response)
                        if peer is not None and not _public_ip(peer):
                            raise RemoteRefused(f'{host} connected to a non-public address, so Hooke stopped.',
                                                next='use a public address, or download the file yourself and import the file')
                        if response.status_code in REDIRECTS and response.headers.get('location'):
                            if redirects >= MAX_REDIRECTS:
                                raise RemoteRefused('The address redirected more than three times; nothing was kept.',
                                                    next='use the file’s direct address')
                            try:
                                current = urllib.parse.urljoin(current, response.headers['location'])
                            except ValueError as error:
                                raise RemoteRefused('The server redirected to a malformed address; nothing was kept.',
                                                    next='use the file’s direct address') from error
                            redirects += 1
                            continue
                        self._refuse_status(response.status_code)
                        encoding = response.headers.get('content-encoding', 'identity').strip().lower()
                        if encoding not in ('', 'identity'):
                            raise RemoteRefused(f'The server sent the file compressed ({encoding[:40]}). Hooke keeps exactly '
                                                'the stored bytes, so it did not decode them; nothing was kept.',
                                                next='use an address that serves the file itself, or download it and import the file')
                        stated = response.headers.get('content-length')
                        declared = None
                        if stated is not None:
                            if not stated.isdigit():
                                raise RemoteRefused('The server stated an invalid size; nothing was kept.', next='retry later')
                            declared = int(stated)
                            if self.size is not None and declared != self.size:
                                raise RemoteRefused(f'The server offers {declared:,} bytes, not the declared {self.size:,}; nothing was downloaded.',
                                                    next='the sample’s declaration and its server disagree; tell the integration’s author')
                            if declared > MAX_BYTES:
                                raise RemoteRefused(f'This file is {declared:,} bytes; a download is limited to 64 MiB. Nothing was downloaded.',
                                                    status=413, next='choose a file within this limit; larger remote intake is not yet available')
                        digest, size = hashlib.sha256(), 0
                        with spool.open('wb') as out:
                            for block in response.iter_raw():  # as received, so a stop is seen on every read
                                check()
                                size += len(block)
                                if self.size is not None and size > self.size:
                                    raise RemoteRefused(f'The download grew past the declared {self.size:,} bytes; nothing was kept.',
                                                        next='the sample’s declaration and its server disagree; tell the integration’s author')
                                if size > MAX_BYTES:
                                    raise RemoteRefused('The file grew past 64 MiB while downloading; nothing was kept.',
                                                        status=413, next='choose a file within this limit')
                                digest.update(block)
                                out.write(block)
                        if declared is not None and size != declared:
                            raise RemoteRefused(f'The download ended after {size:,} of {declared:,} bytes; nothing was kept.',
                                                next='retry the download')
                        media = ''.join(c for c in response.headers.get('content-type', '')[:100] if 32 <= ord(c) < 127)
                        return size, digest.hexdigest(), current, redirects, media
                except httpx.InvalidURL as error:
                    raise RemoteRefused('This web address cannot be requested as written.', next='check the address') from error
                except httpx.TimeoutException as error:
                    raise RemoteRefused('The server stopped answering; nothing was kept.', next='retry the download') from error
                except httpx.HTTPError as error:
                    raise RemoteRefused(f'The connection failed ({type(error).__name__}); nothing was kept.',
                                        next='check your connection, then retry') from error

    @staticmethod
    def _refuse_status(code):
        if code == 200:
            return
        if code in (401, 403, 407):
            raise RemoteRefused(f'The server refused access to this address (HTTP {code}). Hooke sends no credentials, '
                                'cookies or keys with a download.',
                                next='use a public address for the file, or download it yourself and import the file')
        if code in (404, 410):
            raise RemoteRefused(f'Nothing is at this address (HTTP {code}).', next='check the address with the data’s publisher')
        raise RemoteRefused(f'The server answered HTTP {code} instead of the file; nothing was kept.',
                            next='retry later, or check the address')

    def close(self):
        from . import scientific_file_intake as intake
        if self.stage is not None:
            shutil.rmtree(self.stage, ignore_errors=True)
            if not self.stage.exists():
                self.stage = None
        with intake._pending_lock:  # a stage that could not be removed is retried with the other intake stages
            (intake._pending.discard if self.stage is None else intake._pending.add)(self)
        return self.stage is None
