"""One Project version's new Git objects sent to GitHub in one pack over smart HTTP (dulwich; hooke-projects ticket 05).

The REST path costs a request per binary file and per batch of text; a pack push is one request for any number of
files (5,000 files in under a second in the U0 spike). The objects are built here: a blob for each new file, every
tree, and the commit, exactly as Git would. They are pushed to a branch of their own (`hooke-pending-<commit>`), never
to the branch people read, so the existing compare-and-swap still moves that branch and its checks still run. The
caller re-reads what GitHub then holds: a server's own report of a push is never taken as proof.

The token is used only as the push's password, never in a URL, a log or an error.
"""
from __future__ import annotations

import time

COMPONENT = 'dulwich'  # licences.COMPONENTS
PENDING = 'hooke-pending-'
MAX_PACK_BYTES = 512 << 20  # new file bytes held for one pack; a larger version goes through REST
ZERO = b'0' * 40
BASE = 'https://github.com'


class PackUnavailable(Exception):
    """The pack push could not be made; the caller sends the version through REST instead."""


def pending_ref(commit: str) -> str:
    return PENDING + commit[:12]


def build(bodies: dict, previous: dict, *, parent: str | None, message: str, author: tuple[str, str],
          when: float | None = None):
    """(commit id, the objects GitHub may lack, {path: blob id}) for one version.

    `bodies` holds each new or changed file's bytes; `previous` the parent tree's {path: blob id}, for files that are
    unchanged and already on GitHub. A blob already in `previous` is never sent again.
    """
    from dulwich.objects import Blob, Commit, Tree
    objects, wanted, have = [], {}, set(previous.values())
    for path, body in bodies.items():
        if isinstance(body, str):
            wanted[path] = body  # unchanged: its blob id
            continue
        blob = Blob.from_string(body)
        wanted[path] = blob.id.decode()
        if wanted[path] not in have:
            have.add(wanted[path])
            objects.append(blob)
    root: dict = {}
    for path, sha in wanted.items():
        folder = root
        *parts, name = path.split('/')
        for part in parts:
            folder = folder.setdefault(part, {})
        folder[name] = sha

    def tree(folder: dict) -> bytes:
        node = Tree()
        for name, value in folder.items():
            if isinstance(value, dict):
                node.add(name.encode(), 0o040000, tree(value))
            else:
                node.add(name.encode(), 0o100644, value.encode())
        objects.append(node)
        return node.id

    commit = Commit()
    commit.tree = tree(root)
    commit.parents = [parent.encode()] if parent else []
    name, email = author
    commit.author = commit.committer = f'{name} <{email}>'.encode()
    commit.author_time = commit.commit_time = int(time.time() if when is None else when)
    commit.author_timezone = commit.commit_timezone = 0
    commit.message = message.encode()
    objects.append(commit)
    return commit.id.decode(), objects, wanted


def push(token: str, repo: str, objects: list, commit: str, *, base: str = BASE, timeout: float = 300) -> str:
    """Send `objects` and point the pending branch for `commit` at it; returns that branch's name. A branch of that
    name already pointing at `commit` (an interrupted earlier push) is left as it is."""
    try:
        from dulwich.client import HttpGitClient
        from dulwich.pack import pack_objects_to_data
    except ImportError as error:
        raise PackUnavailable('Git pack push is not installed here') from error
    ref = f'refs/heads/{pending_ref(commit)}'.encode()
    new = commit.encode()

    def update_refs(refs):
        current = refs.get(ref)
        if current == new:
            return {}
        if current not in (None, ZERO):
            raise PackUnavailable('A pending branch of another version is in the way')
        return {ref: new}

    def generate(have, want, *, ofs_delta=False, progress=None):
        return pack_objects_to_data(objects, deltify=False, ofs_delta=ofs_delta)

    try:
        client = HttpGitClient(base, username='x-access-token', password=token, timeout=timeout, thin_packs=False,
                               quiet=True)
        result = client.send_pack(f'/{repo}.git', update_refs, generate)
    except PackUnavailable:
        raise
    except Exception as error:  # transport, protocol or server refusal: REST is still there
        raise PackUnavailable(f'Git pack push failed ({type(error).__name__})') from None
    status = (result.ref_status or {}).get(ref)
    if status:
        raise PackUnavailable('GitHub refused the pending branch')
    return pending_ref(commit)
