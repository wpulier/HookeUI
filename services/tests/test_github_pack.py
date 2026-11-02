"""Publishing to GitHub with one Git pack (hooke-projects ticket 05): an in-memory GitHub that takes packs, and the real
dulwich client against a real smart-HTTP server on this Mac. No network, Git install or account."""
import json
import threading

import pytest
from dulwich.objects import Blob, Commit, Tree

from orchestrator.github_pack import PENDING, PackUnavailable, build, pending_ref, push
from orchestrator.github_rest import Client
from orchestrator.project_publish import publish
from orchestrator.project_remote import RemoteFile
from orchestrator.project_remote_github import GitHubRecords, create_repository, git_blob_id, github_remote
from orchestrator.project_remote_layers import RemoteRefused
from orchestrator.project_repository import ProjectRepository
from orchestrator.project_start_from import start_from
from tests.test_project_publish import SIGNOFF, elsewhere, published
from tests.test_project_remote_github import GitHub, anonymous, signed_in

TOKEN = 'ghu_' + 'x' * 36


class PackGitHub(GitHub):
    """GitHub that also takes a pack push, the way receive-pack does: objects stored, then one branch set."""

    def __init__(self, *, lie=False):
        super().__init__()
        self.packs, self.lie = [], lie

    def call(self, method, path, payload=None):
        if path == '/user' and method == 'GET':
            self.calls.append((method, path))
            return 200, {'login': 'ada', 'id': 7}
        bare = path.split('?')[0]
        if '/git/matching-refs/heads/' in bare and method == 'GET':
            self.calls.append((method, path))
            full, prefix = bare.removeprefix('/repos/').split('/git/matching-refs/heads/')
            refs = self.repos[full]['refs']
            return 200, [{'ref': f'refs/heads/{b}'} for b in refs if b.startswith(prefix)]
        if '/git/refs/heads/' in bare and method == 'DELETE':
            self.calls.append((method, path))
            full, branch = bare.removeprefix('/repos/').split('/git/refs/heads/')
            return (204, {}) if self.repos[full]['refs'].pop(branch, None) else (422, {'message': 'Reference does not exist'})
        return super().call(method, path, payload)

    def push_pack(self, repo, objects, commit):
        self.packs.append(sorted(type(o).__name__ for o in objects))
        if self.lie:
            return pending_ref(commit)  # says it took the push, holds nothing
        held = self.repos[repo]
        trees = {o.id.decode(): o for o in objects if isinstance(o, Tree)}
        for o in objects:
            if isinstance(o, Blob):
                held['blobs'][o.id.decode()] = o.data

        def flat(tree, prefix=''):
            out = {}
            for item in trees[tree].items():
                name, sha = item.path.decode(), item.sha.decode()
                if item.mode == 0o040000:
                    out.update(flat(sha, prefix + name + '/'))
                else:
                    out[prefix + name] = (sha, len(held['blobs'][sha]))
            return out

        made = next(o for o in objects if isinstance(o, Commit))
        assert made.id.decode() == commit
        held['trees'][made.tree.decode()] = flat(made.tree.decode())
        held['commits'][commit] = {'message': made.message.decode(), 'tree': made.tree.decode(),
                                   'parents': [p.decode() for p in made.parents]}
        held['refs'][pending_ref(commit)] = commit
        return pending_ref(commit)


def test_a_publication_goes_in_one_pack_and_anyone_can_start_from_it(tmp_path):
    root, _, _, _, _, source, _, review = published(tmp_path)
    hub = PackGitHub()
    full, placeholder = create_repository(signed_in(hub), 'cells-study', private=False, description='Cells')
    hub.calls.clear()
    remote = github_remote(signed_in(hub), full, placeholder=placeholder)
    receipt = publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote,
                      replaces=placeholder)
    held = hub.repos[full]
    assert len(hub.packs) == 1 and held['refs']['main'] == receipt['commit']
    assert not [b for b in held['refs'] if b.startswith(PENDING)]  # the pending branch is gone
    posts = [c for c in hub.calls if c[0] == 'POST' and '/git/' in c[1]]
    assert posts == []  # no file, tree or commit went through REST
    working, *_ = start_from(github_remote(anonymous(hub), full), elsewhere(tmp_path)[3], staging=tmp_path / 'Fetch')
    assert ProjectRepository(working).read(source.id, 1).digest == source.digest


def test_a_second_version_sends_only_what_changed():
    hub = PackGitHub()
    full, placeholder = create_repository(signed_in(hub), 'batches', private=True, description='')
    records = GitHubRecords(signed_in(hub), full, placeholder=placeholder)
    files = [RemoteFile(f'project/history/n{i:04}.json', 20, (lambda i=i: json.dumps({'n': i}).encode().ljust(20)))
             for i in range(1000)]
    first = records.write_commit(files, parent=None, message='one')
    records.advance(placeholder, first)
    changed = files[:999] + [RemoteFile('project/history/n0999.json', 20, lambda: b'{"n": "changed"}   ')]
    second = records.write_commit(changed, parent=first, message='two')
    records.advance(first, second)
    assert hub.packs[0].count('Blob') == 1000
    assert hub.packs[1].count('Blob') == 1  # the one changed file; trees and the commit beside it
    assert hub.repos[full]['refs']['main'] == second
    assert hub.repos[full]['commits'][second]['parents'] == [first]


def test_a_push_github_does_not_hold_is_never_published():
    hub = PackGitHub(lie=True)
    full, placeholder = create_repository(signed_in(hub), 'liar', private=True, description='')
    records = GitHubRecords(signed_in(hub), full, placeholder=placeholder)
    with pytest.raises(RemoteRefused, match='does not hold'):
        records.write_commit([RemoteFile('README.md', 3, lambda: b'hi\n')], parent=None, message='one')
    assert hub.repos[full]['refs']['main'] == placeholder


def test_without_a_pack_the_version_goes_through_rest():
    class Refusing(PackGitHub):
        def push_pack(self, repo, objects, commit):
            self.packs.append('refused')
            raise PackUnavailable('Git pack push failed (HangupException)')
    hub = Refusing()
    full, placeholder = create_repository(signed_in(hub), 'fallback', private=True, description='')
    records = GitHubRecords(signed_in(hub), full, placeholder=placeholder)
    commit = records.write_commit([RemoteFile('README.md', 3, lambda: b'hi\n')], parent=None, message='one')
    records.advance(placeholder, commit)
    assert hub.packs == ['refused'] and ('POST', f'/repos/{full}/git/commits') in hub.calls
    assert hub.repos[full]['refs']['main'] == commit


def test_a_client_with_no_sign_in_never_tries_a_pack():
    assert Client(anonymous=True).packs is False
    assert Client(TOKEN).packs is True
    with pytest.raises(PackUnavailable, match='sign-in'):
        Client(anonymous=True).push_pack('ada/cells', [], 'a' * 40)


def test_built_objects_are_the_git_objects_git_would_make():
    body = b'{"cells": 12}\n'
    commit, objects, wanted = build({'project/results/count.json': body, 'README.md': git_blob_id(b'old\n')},
                                    {'README.md': git_blob_id(b'old\n')}, parent='b' * 40, message='Publish\n',
                                    author=('ada', '7+ada@users.noreply.github.com'), when=1_800_000_000)
    assert wanted == {'project/results/count.json': git_blob_id(body), 'README.md': git_blob_id(b'old\n')}
    blobs = [o for o in objects if isinstance(o, Blob)]
    assert [b.data for b in blobs] == [body]  # the unchanged README is not sent again
    made = objects[-1]
    assert made.id.decode() == commit and made.parents == [b'b' * 40]
    assert made.author == b'ada <7+ada@users.noreply.github.com>' and made.author_time == 1_800_000_000


@pytest.fixture
def smart_http(tmp_path):
    """A bare repository served over Git smart HTTP on this Mac, by dulwich's own server."""
    from dulwich.repo import Repo
    from dulwich.server import DictBackend
    from dulwich.web import make_server, make_wsgi_chain
    bare = Repo.init_bare(str(tmp_path / 'cells.git'), mkdir=True)
    server = make_server('127.0.0.1', 0, make_wsgi_chain(DictBackend({'/ada/cells.git': bare})))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield bare, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()


def test_the_real_client_pushes_a_pack_over_smart_http_and_then_only_the_change(smart_http):
    bare, base = smart_http
    bodies = {f'project/n{i:04}.json': json.dumps({'n': i}).encode() for i in range(5000)}
    commit, objects, wanted = build(bodies, {}, parent=None, message='one', author=('ada', 'ada@example.org'))
    assert push(TOKEN, 'ada/cells', objects, commit, base=base) == pending_ref(commit)
    # Read back from the repository itself, never from the push's own report.
    assert bare.refs[f'refs/heads/{pending_ref(commit)}'.encode()] == commit.encode()
    assert bare[bare[commit.encode()].tree].items()
    second, more, _ = build({**{p: sha for p, sha in wanted.items()}, 'project/n0001.json': b'{"n": "changed"}'},
                            wanted, parent=commit, message='two', author=('ada', 'ada@example.org'))
    assert sum(isinstance(o, Blob) for o in more) == 1
    push(TOKEN, 'ada/cells', more, second, base=base)
    assert bare[second.encode()].parents == [commit.encode()]
    assert bare[bare[bare[second.encode()].tree][b'project'][1]][b'n0001.json'][1] == git_blob_id(b'{"n": "changed"}').encode()


def test_a_pending_branch_of_another_version_is_never_overwritten(smart_http):
    bare, base = smart_http
    first, objects, _ = build({'a.txt': b'a'}, {}, parent=None, message='one', author=('ada', 'ada@example.org'))
    push(TOKEN, 'ada/cells', objects, first, base=base)
    bare.refs[f'refs/heads/{pending_ref("f" * 40)}'.encode()] = first.encode()
    with pytest.raises(PackUnavailable, match='in the way'):
        push(TOKEN, 'ada/cells', objects, 'f' * 40, base=base)
    assert push(TOKEN, 'ada/cells', objects, first, base=base) == pending_ref(first)  # already there: nothing to do


def test_a_failed_push_never_says_the_token(tmp_path):
    with pytest.raises(PackUnavailable) as caught:
        push(TOKEN, 'ada/cells', [], 'a' * 40, base='http://127.0.0.1:9', timeout=2)
    assert TOKEN not in str(caught.value) and caught.value.__cause__ is None
