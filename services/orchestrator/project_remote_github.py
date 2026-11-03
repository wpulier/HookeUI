"""A GitHub repository as a Project remote, with no Git installed (ADR 0028, format v2).

Records: one commit per push. Signed in, the new objects go in one Git pack over smart HTTP (github_pack) to a pending
branch, and the published branch then moves by compare-and-swap as before. When a pack cannot be sent, the Git data
API is used: changed text files travel inline in batched tree requests (13 requests for 5,000 files in the U0 spike,
against 5,000 for one blob each) and binary record files become blobs. Unchanged files are reused from the parent
tree either way. Every blob read back is checked against its Git blob id, and every
project file again against the snapshot manifest by `project_remote.fetch`.

Anyone may read a public repository with no account: the commit and its listing come from two API calls, and file
contents from GitHub's raw file host, so a fetch does not exhaust the 60-request anonymous allowance.

Large files: for a GitHub-only publication they are assets of one release (`hooke-data`), named by SHA-256 and
downloadable with no account. Larger projects use another bulk store (format v2, section 5).
"""
from __future__ import annotations

import base64
import hashlib
import re
import time
from urllib.parse import quote

from .github_rest import Client, GitHubRefused
from .project_remote import Diverged, Remote, RemoteFile
from .project_remote_layers import DIGEST, MAX_ENTRIES, ChangedOutsideHooke, RemoteRefused, sha256

SHA1 = re.compile(r'[0-9a-f]{40}')
REPO = re.compile(r'[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}')
BATCH_FILES = 400
BATCH_BYTES = 900 << 10
RELEASE_TAG = 'hooke-data'
MAX_RELEASE_OBJECTS = 900
MAX_RELEASE_BYTES = 2 << 30
TOPIC = 'hooke-project'


def git_blob_id(body: bytes) -> str:
    return hashlib.sha1(b'blob %d\x00' % len(body) + body).hexdigest()


def repository(value: str) -> str:
    """owner/name from 'owner/name' or a github.com URL."""
    value = str(value).strip()
    for prefix in ('https://github.com/', 'http://github.com/', 'github.com/'):
        if value.startswith(prefix):
            value = value[len(prefix):]
    value = value.removesuffix('.git').strip('/')
    if not REPO.fullmatch(value) or '..' in value:
        raise RemoteRefused('Give a GitHub repository as owner/name or its github.com address')
    return value


def _text(body: bytes):
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        return None
    return text if '\x00' not in text and text.encode('utf-8') == body else None


class GitHubRecords:
    def __init__(self, client: Client, repo: str, *, branch='main', placeholder=None):
        self.client, self.repo, self.branch = client, repository(repo), branch
        self.placeholder = placeholder  # the commit GitHub made when Hooke created the repository
        self._trees = {}
        self._pending = set()  # pack-push branches to remove once the version is published or refused

    def describe(self):
        return {'kind': 'github', 'location': f'https://github.com/{self.repo}', 'branch': self.branch}

    def head(self):
        status, data = self.client.ok('GET', f'/repos/{self.repo}/git/ref/heads/{quote(self.branch)}',
                                      what='reading the project version', allow=(404, 409))
        if status in (404, 409):
            return None
        sha = (data.get('object') or {}).get('sha') if isinstance(data, dict) else None
        if not isinstance(sha, str) or not SHA1.fullmatch(sha):
            raise ChangedOutsideHooke('GitHub returned an unexpected branch')
        return sha

    def _tree(self, tree_sha):
        if tree_sha in self._trees:
            return self._trees[tree_sha]
        _, data = self.client.ok('GET', f'/repos/{self.repo}/git/trees/{tree_sha}?recursive=1',
                                 what='listing the project files')
        if not isinstance(data, dict) or data.get('truncated') is not False or type(data.get('tree')) is not list:
            raise RemoteRefused('This project has more files than GitHub lists in one read; use another remote')
        entries = {}
        for item in data['tree']:
            kind, mode = item.get('type'), item.get('mode')
            if kind == 'tree':
                continue
            if kind != 'blob' or mode not in ('100644', '100755'):
                raise ChangedOutsideHooke('This repository holds a link or submodule; Hooke never follows one')
            if not SHA1.fullmatch(str(item.get('sha'))) or type(item.get('size')) is not int:
                raise ChangedOutsideHooke('GitHub listed an invalid file')
            entries[item['path']] = (item['sha'], item['size'])
        if len(entries) > MAX_ENTRIES + 16:
            raise RemoteRefused('This project has more files than Hooke can hold')
        self._trees[tree_sha] = entries
        return entries

    def _commit(self, commit):
        if not isinstance(commit, str) or not SHA1.fullmatch(commit):
            raise RemoteRefused('Name a GitHub version by its full commit id')
        _, data = self.client.ok('GET', f'/repos/{self.repo}/git/commits/{commit}', what='reading the project version')
        tree = (data.get('tree') or {}).get('sha') if isinstance(data, dict) else None
        parents = [p.get('sha') for p in data.get('parents', [])] if isinstance(data, dict) else []
        if not isinstance(tree, str) or not SHA1.fullmatch(tree) or len(parents) > 1:
            raise ChangedOutsideHooke('This GitHub version is not a Hooke version')
        return data.get('message', ''), tree, (parents[0] if parents else None)

    def read_commit(self, commit):
        message, tree, parent = self._commit(commit)
        entries = [{'path': path, 'ref': f'{sha}:{commit}:{path}', 'bytes': size}
                   for path, (sha, size) in sorted(self._tree(tree).items())]
        return {'commit': commit, 'parent': parent, 'message': message, 'entries': entries}

    def read_blob(self, ref, max_bytes):
        sha, commit, path = str(ref).split(':', 2)
        if not SHA1.fullmatch(sha) or not SHA1.fullmatch(commit):
            raise ChangedOutsideHooke('An invalid GitHub file reference')
        if self.client.anonymous:
            body = self.client.download(f'https://raw.githubusercontent.com/{self.repo}/{commit}/'
                                        + quote(path, safe='/'), max_bytes)
        else:
            _, data = self.client.ok('GET', f'/repos/{self.repo}/git/blobs/{sha}', what='reading a project file')
            if not isinstance(data, dict) or type(data.get('size')) is not int or data['size'] > max_bytes:
                raise ChangedOutsideHooke('A project file on GitHub is larger than it may be')
            try:
                body = base64.b64decode(data.get('content', ''), validate=False)
            except ValueError as error:
                raise ChangedOutsideHooke('GitHub returned an unreadable file') from error
        if git_blob_id(body) != sha:
            raise ChangedOutsideHooke('A project file on GitHub does not match its id')
        return body

    def write_commit(self, files: list[RemoteFile], *, parent, message, check=lambda: None):
        previous = {}
        base = None
        if parent is not None:
            _, base, _ = self._commit(parent)
            previous = {path: sha for path, (sha, _) in self._tree(base).items()}
        wanted, changed = {}, {}
        for item in files:
            check()
            body = item.read()
            if item.sha256 is not None and sha256(body) != item.sha256:
                raise RemoteRefused(f'{item.path} changed while sending; nothing was sent')
            wanted[item.path] = git_blob_id(body)
            if previous.get(item.path) != wanted[item.path]:
                changed[item.path] = body
        commit = self._pack(changed, wanted, previous, parent=parent, message=message, check=check)
        if commit is not None:
            return commit
        tree = self._rest_tree(changed, wanted, previous, base, check=check)
        payload = {'message': message, 'tree': tree, 'parents': [parent] if parent else []}
        _, data = self.client.ok('POST', f'/repos/{self.repo}/git/commits', payload, what='recording the version')
        commit = data.get('sha')
        if not isinstance(commit, str) or not SHA1.fullmatch(commit):
            raise RemoteRefused('GitHub did not record the version')
        return commit

    def _author(self):
        """The signed-in account as GitHub names it in commits it makes itself (its no-reply address)."""
        status, data = self.client.ok('GET', '/user', what='reading your GitHub account', allow=(403, 404))
        login, number = (data.get('login'), data.get('id')) if status == 200 and isinstance(data, dict) else (None, None)
        if isinstance(login, str) and re.fullmatch(r'[A-Za-z0-9-]{1,39}', login) and type(number) is int:
            return login, f'{number}+{login}@users.noreply.github.com'
        return 'HookeUI', 'hookeui@users.noreply.github.com'

    def _pack(self, changed, wanted, previous, *, parent, message, check):
        """The version as one pack push (github_pack), checked by reading back what GitHub holds; None when a pack
        cannot be sent, and REST is used instead."""
        from .github_pack import MAX_PACK_BYTES, PackUnavailable, build
        if not getattr(self.client, 'packs', False) or sum(len(b) for b in changed.values()) > MAX_PACK_BYTES:
            return None
        try:
            commit, objects, built = build({path: changed.get(path, sha) for path, sha in wanted.items()}, previous,
                                           parent=parent, message=message, author=self._author())
            check()
            branch = self.client.push_pack(self.repo, objects, commit)
        except PackUnavailable:
            return None
        self._pending.add(branch)
        # What a server says of its own push is never proof: GitHub must now hold this commit and exactly these files.
        status, data = self.client.ok('GET', f'/repos/{self.repo}/git/ref/heads/{quote(branch)}',
                                      what='checking the sent version', allow=(404,))
        if built != wanted or status != 200 or (data.get('object') or {}).get('sha') != commit:
            raise RemoteRefused('GitHub does not hold the version that was sent; nothing was published')
        message_back, tree, parent_back = self._commit(commit)
        stored = {path: sha for path, (sha, _) in self._tree(tree).items()}
        if parent_back != parent or message_back != message or stored != wanted:
            raise RemoteRefused('GitHub holds different files than were sent; nothing was published')
        return commit

    def _rest_tree(self, changed, wanted, previous, base, *, check):
        """The version's tree through the Git data API: text inline in batched trees, binary files as blobs."""
        changes = []
        for path, body in changed.items():
            check()
            text = _text(body)
            if text is None:
                _, data = self.client.ok('POST', f'/repos/{self.repo}/git/blobs',
                                         {'content': base64.b64encode(body).decode(), 'encoding': 'base64'},
                                         what='sending a project file')
                if data.get('sha') != wanted[path]:
                    raise RemoteRefused('GitHub stored a project file differently; nothing was published')
                changes.append(({'path': path, 'mode': '100644', 'type': 'blob', 'sha': wanted[path]}, 64))
            else:
                changes.append(({'path': path, 'mode': '100644', 'type': 'blob', 'content': text}, len(body)))
        for path in sorted(set(previous) - set(wanted)):
            changes.append(({'path': path, 'mode': '100644', 'type': 'blob', 'sha': None}, 64))
        tree = base
        batch, size = [], 0
        def send():
            nonlocal tree, batch, size
            payload = {'tree': [entry for entry, _ in batch]}
            if tree is not None:
                payload['base_tree'] = tree
            _, data = self.client.ok('POST', f'/repos/{self.repo}/git/trees', payload, what='sending project files')
            tree = data.get('sha')
            if not isinstance(tree, str) or not SHA1.fullmatch(tree):
                raise RemoteRefused('GitHub did not accept the project files')
            batch, size = [], 0
        for change, weight in changes:
            check()
            if batch and (len(batch) >= BATCH_FILES or size + weight > BATCH_BYTES):
                send()
            batch.append((change, weight))
            size += weight
        if batch or tree is None:
            send()
        # Every file GitHub now holds must be exactly what was sent.
        stored = {path: sha for path, (sha, _) in self._tree(tree).items()}
        if stored != wanted:
            raise RemoteRefused('GitHub holds different files than were sent; nothing was published')
        return tree

    def advance(self, old, new):
        try:
            self._advance(old, new)
        finally:
            self._clean()

    def _clean(self):
        """Remove the pending branches pack pushes left (this one's, and any an interrupted publication left)."""
        if not self._pending:
            return
        from .github_pack import PENDING
        status, data = self.client.ok('GET', f'/repos/{self.repo}/git/matching-refs/heads/{PENDING}',
                                      what='tidying the sent version', allow=(404, 409))
        names = {str(r.get('ref', '')).removeprefix('refs/heads/') for r in data if isinstance(r, dict)} \
            if status == 200 and isinstance(data, list) else set()
        for branch in sorted((names | self._pending)):
            if branch.startswith(PENDING) and re.fullmatch(r'[0-9a-z-]{1,60}', branch):
                self.client.ok('DELETE', f'/repos/{self.repo}/git/refs/heads/{quote(branch)}',
                               what='tidying the sent version', allow=(404, 422))
        self._pending.clear()

    def _advance(self, old, new):
        if self.head() != old:
            raise Diverged('This project changed in two places: GitHub has a version this Mac did not send. '
                           'Nothing was changed.')
        if old is None:
            status, _ = self.client.ok('POST', f'/repos/{self.repo}/git/refs',
                                       {'ref': f'refs/heads/{self.branch}', 'sha': new},
                                       what='publishing the version', allow=(422,))
        else:
            status, _ = self.client.ok('PATCH', f'/repos/{self.repo}/git/refs/heads/{quote(self.branch)}',
                                       {'sha': new, 'force': old == self.placeholder},
                                       what='publishing the version', allow=(422,))
        if status == 422 or self.head() != new:
            raise Diverged('This project changed in two places: another version arrived first. Nothing was changed.')


class GitHubReleaseBulk:
    """Large files as assets of the `hooke-data` release: uploaded signed in, downloaded with no account."""

    def __init__(self, client: Client, repo: str, *, private=False):
        self.client, self.repo, self.private = client, repository(repo), bool(private)
        self._release = None

    def describe(self):
        return {'kind': 'github-release', 'base': f'https://github.com/{self.repo}/releases/download/{RELEASE_TAG}/',
                'visibility': 'private' if self.private else 'public'}

    def _assets(self, create=False):
        if self._release is None:
            status, data = self.client.ok('GET', f'/repos/{self.repo}/releases/tags/{RELEASE_TAG}',
                                          what='reading the project data', allow=(404,))
            if status == 404:
                if not create:
                    return None, {}
                _, data = self.client.ok('POST', f'/repos/{self.repo}/releases',
                                         {'tag_name': RELEASE_TAG, 'name': 'Hooke project data',
                                          'body': 'Large files of this Hooke project, named by SHA-256. '
                                                  'Hooke verifies each one before use.'},
                                         what='creating the project data release')
            self._release = data
        assets = {a.get('name'): a for a in self._release.get('assets', []) if isinstance(a, dict)}
        return self._release, assets

    def missing(self, digests):
        _, assets = self._assets()
        if len(set(digests) | set(assets)) > MAX_RELEASE_OBJECTS:
            raise RemoteRefused('This project has too many large files for GitHub alone; '
                                'publish its data to Hooke Cloud or your own storage')
        return [d for d in digests if d not in assets]

    def put(self, digest, path, size, *, check=lambda: None):
        if not DIGEST.fullmatch(digest):
            raise RemoteRefused('An object name is not a SHA-256')
        release, assets = self._assets(create=True)
        if digest in assets:
            return False
        if sum(a.get('size', 0) for a in assets.values()) + size > MAX_RELEASE_BYTES:
            raise RemoteRefused('This project’s data is too large for GitHub alone; '
                                'publish it to Hooke Cloud or your own storage')
        body = open(path, 'rb').read()
        if len(body) != size or sha256(body) != digest:
            raise RemoteRefused('A large file changed while sending; nothing was published')
        check()
        upload = str(release.get('upload_url', '')).split('{', 1)[0]
        if not upload.startswith('https://uploads.github.com/'):
            raise RemoteRefused('GitHub returned an unexpected upload address')
        status, data = self.client.upload(f'{upload}?name={digest}', body)
        if status >= 400 or data.get('name') != digest or data.get('size') != size:
            raise GitHubRefused('a large project file', status, data if isinstance(data, dict) else {})
        assets[digest] = data
        self._release.setdefault('assets', []).append(data)
        return True

    def get(self, digest, size, target, *, check=lambda: None):
        if not DIGEST.fullmatch(digest):
            raise RemoteRefused('An object name is not a SHA-256')
        check()
        if self.private:  # a private repository's assets come through the API, signed in
            _, assets = self._assets()
            asset = assets.get(digest)
            if not isinstance(asset, dict) or type(asset.get('id')) is not int:
                raise RemoteRefused('A large file of this version is not in its store')
            body = self.client.asset(self.repo, asset['id'], size)
        else:
            body = self.client.download(f'https://github.com/{self.repo}/releases/download/{RELEASE_TAG}/{digest}',
                                        size)
        if len(body) != size or sha256(body) != digest:
            raise ChangedOutsideHooke('A large file on GitHub does not match its name')
        with open(target, 'xb') as stream:
            stream.write(body)


def github_remote(client: Client, repo: str, *, placeholder=None, bulk=None, private=False) -> Remote:
    """`private`: a private repository (a backup), whose large files are read signed in."""
    return Remote(GitHubRecords(client, repo, placeholder=placeholder),
                  bulk or GitHubReleaseBulk(client, repo, private=private))


def create_repository(client: Client, name: str, *, private: bool, description: str, wait=time.sleep):
    """Make a new repository under the signed-in account. Returns (owner/name, its placeholder commit)."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name) or name in ('.', '..'):
        raise RemoteRefused('Choose a repository name of letters, digits, dashes, dots or underscores')
    _, data = client.ok('POST', '/user/repos', {'name': name, 'private': bool(private),
                                                'description': ' '.join(str(description).split())[:300],
                                                'auto_init': True, 'has_wiki': False, 'has_projects': False},
                        what='creating the repository', allow=())
    repo = repository(data.get('full_name', ''))
    records = GitHubRecords(client, repo)
    for _ in range(10):
        placeholder = records.head()
        if placeholder:
            break
        wait(1)
    else:
        raise RemoteRefused('GitHub created the repository but has not prepared it yet; try again in a moment')
    if not private:
        client.ok('PUT', f'/repos/{repo}/topics', {'names': [TOPIC]}, what='tagging the repository')
    return repo, placeholder
