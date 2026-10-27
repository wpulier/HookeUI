"""Share with everyone with no Git and no gh (orchestrator/publish_api.py; C5): the exact package is prepared on this Mac,
read, and sent through GitHub's API only on the user's sign-off, as exactly that: its files by their sha256, one commit
signed off by the signed-in account, the pull request to the official base; a changed file, a moved base or another
account sends nothing. A controlled stand-in for GitHub's API (no network, no token)."""
import base64
import json

import pytest

from orchestrator import publish, publish_api
from tests.test_publish import home, yours  # noqa: F401 - a scratch catalog and an entry of yours, proven

BASE = "b" * 40


class GitHub:
    """GitHub's REST API as the publisher uses it, answering from what it was sent (every call kept)."""

    def __init__(self, name="Maya Okafor", exists=False):
        self.calls, self.name, self.exists = [], name, exists
        self.blobs, self.base = {}, BASE
        self.fork_ready = False

    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/user":
            return 200, {"login": "maya", "id": 7, "name": self.name}
        if path == "/user/emails":
            return 200, [{"email": "maya@lab.example", "primary": True, "verified": True, "visibility": "public"}]
        if path.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": self.base}}
        if "/contents/catalog/models/" in path:
            return (200, {}) if self.exists else (404, {"message": "Not Found"})
        if path.endswith("/forks"):
            return 202, {"full_name": "maya/ObservationUI"}
        if path.endswith(f"/git/commits/{BASE}"):
            ready, self.fork_ready = self.fork_ready, True  # ready on the second look, as a new fork is
            return (200, {"tree": {"sha": "t" * 40}}) if ready else (404, {})
        if path.endswith("/merge-upstream"):
            return 200, {}
        if path.endswith("/git/blobs"):
            sha = f"blob{len(self.blobs)}"
            self.blobs[sha] = base64.b64decode(payload["content"])
            return 201, {"sha": sha}
        if path.endswith("/git/trees"):
            return 201, {"sha": "tree1"}
        if path.endswith("/git/commits"):
            return 201, {"sha": "c" * 40}
        if path.endswith("/git/refs"):
            return 201, {"ref": payload["ref"]}
        if method == "GET" and "/pulls?" in path:
            return 200, []
        if path.endswith("/pulls"):
            self.pull = {"html_url": "https://github.com/wpulier/HookeUI/pull/42", "number": 42,
                         "title": payload["title"], "body": payload["body"], "state": "open",
                         "head": {"sha": "c" * 40}, "base": {"ref": payload["base"]}}
            return 201, self.pull
        if path.endswith("/pulls/42"):
            return 200, {**self.pull, "state": "closed", "merged": True}
        return 404, {"message": f"no route {path}"}


def proven(home, monkeypatch):
    m = yours(home)
    # Synthetic retained receipt: this suite tests publishing, not installation/proof.
    from orchestrator import envs
    folder = envs.folder(m)
    kept = folder / "entry" / m.id
    kept.mkdir(parents=True)
    envs.copy(m, kept)
    (folder / "bin").mkdir()
    (folder / "bin/python").write_text("never executed")
    (folder / "receipt.json").write_text('{"at": "2000-01-01", "synthetic": true}')
    folder.with_name(folder.name + ".proof.json").write_text('{"ok": true, "synthetic": true}')
    return m


def test_the_exact_package_is_prepared_here_and_sent_only_as_it_was_reviewed(home, monkeypatch):
    proven(home, monkeypatch)
    api = GitHub()
    made = publish_api.prepare("dark-spots", http=api)
    assert not any(method == "POST" for method, _p, _b in api.calls), "preparing sends nothing"
    assert made["identity"] == {"login": "maya", "name": "Maya Okafor", "email": "maya@lab.example"}
    assert all(f.startswith("catalog/models/dark-spots/") for f in made["files"]), "only its catalog files, no proof input"
    assert "Maya Okafor <maya@lab.example>" in made["review"] and "Nothing has left this Mac" in made["review"]
    with pytest.raises(publish.CantPublish, match="DCO"):
        publish_api.submit(made["package"], signoff=False, http=api, model="dark-spots")
    assert not any(method == "POST" for method, _p, _b in api.calls), "no sign-off, nothing sent"

    sent = publish_api.submit(made["package"], signoff=True, http=api, model="dark-spots", wait=lambda s: None)
    assert sent["url"].endswith("/pull/42") and sent["number"] == 42
    commit = next(b for method, p, b in api.calls if method == "POST" and p.endswith("/git/commits"))
    assert commit["parents"] == [BASE] and commit["author"]["name"] == "Maya Okafor"
    assert commit["message"].endswith("Signed-off-by: Maya Okafor <maya@lab.example>")
    pull = next(b for method, p, b in api.calls if method == "POST" and p.endswith("/pulls"))
    assert pull["head"].startswith("maya:catalog/dark-spots-") and pull["base"] == "main"
    package = made["package"]
    proposal = json.loads((__import__("pathlib").Path(package) / "proposal.json").read_text())
    assert sorted(api.blobs.values()) == sorted((__import__("pathlib").Path(package) / f).read_bytes()
                                                for f in proposal["files"]), "exactly the reviewed files' bytes"
    assert publish_api.status(package, http=api)["state"] == "merged"


@pytest.mark.parametrize("change, words", [
    ("file", "changed since you reviewed it"), ("base", "moved on"), ("account", "someone else"),
])
def test_a_changed_package_a_moved_base_or_another_account_sends_nothing(home, monkeypatch, change, words):
    proven(home, monkeypatch)
    api = GitHub()
    made = publish_api.prepare("dark-spots", http=api)
    if change == "file":
        target = next((__import__("pathlib").Path(made["package"]) / f) for f in made["files"] if f.endswith("adapter.py"))
        target.write_text(target.read_text() + "\n# changed after review\n")
    elif change == "base":
        api.base = "d" * 40
    else:
        api.name = "Someone Else"
    with pytest.raises(publish.CantPublish, match=words):
        publish_api.submit(made["package"], signoff=True, http=api, model="dark-spots", wait=lambda s: None)
    assert not any(method == "POST" for method, _p, _b in api.calls), "nothing was sent"


def test_an_account_with_no_name_or_an_entry_already_there_is_refused_before_anything(home, monkeypatch):
    proven(home, monkeypatch)
    with pytest.raises(publish.CantPublish, match="has no name"):
        publish_api.prepare("dark-spots", http=GitHub(name=""))
    with pytest.raises(publish.CantPublish, match="already"):
        publish_api.prepare("dark-spots", http=GitHub(exists=True))


def test_altered_proposal_metadata_is_refused_before_any_write(home, monkeypatch):
    from pathlib import Path

    proven(home, monkeypatch)
    api = GitHub()
    made = publish_api.prepare("dark-spots", http=api)
    proposal = Path(made["package"]) / "proposal.json"
    changed = json.loads(proposal.read_text())
    changed["base"]["repository"] = "other/unapproved"
    changed["message"] = "Changed signoff"
    proposal.write_text(json.dumps(changed))
    with pytest.raises(publish.CantPublish, match="review"):
        publish_api.submit(made["package"], signoff=True, http=api, model="dark-spots", wait=lambda s: None)
    assert not any(method == "POST" for method, _p, _b in api.calls)


class RecoverableGitHub(GitHub):
    """A branch/PR exists even when its creation response never reaches the caller."""
    def __init__(self, lose):
        super().__init__()
        self.refs, self.pulls, self.lose = {}, [], lose

    def call(self, method, path, payload=None):
        if method == "GET" and "/git/ref/heads/catalog/" in path:
            self.calls.append((method, path, payload))
            sha = self.refs.get(path.split("/git/ref/heads/")[1])
            return (200, {"object": {"sha": sha}}) if sha else (404, {})
        if method == "GET" and "/pulls?" in path:
            self.calls.append((method, path, payload))
            return 200, list(self.pulls)
        if method == "GET" and path.endswith("/pulls/42") and self.pulls:
            self.calls.append((method, path, payload))
            return 200, self.pulls[0]
        if method == "POST" and path.endswith("/git/refs"):
            self.calls.append((method, path, payload))
            branch = payload["ref"].removeprefix("refs/heads/")
            if branch in self.refs:
                return 422, {"message": "Reference already exists"}
            self.refs[branch] = payload["sha"]
            if self.lose == "branch":
                self.lose = None
                raise OSError("response lost after branch creation")
            return 201, {"ref": payload["ref"]}
        if method == "POST" and path.endswith("/pulls"):
            self.calls.append((method, path, payload))
            if self.pulls:
                return 422, {"message": "Pull request already exists"}
            pull = {"html_url": "https://github.com/wpulier/HookeUI/pull/42", "number": 42,
                    "title": payload["title"], "body": payload["body"], "state": "open",
                    "head": {"sha": "c" * 40}, "base": {"ref": "main"}}
            self.pulls.append(pull)
            if self.lose == "pull":
                self.lose = None
                raise OSError("response lost after PR creation")
            return 201, pull
        return super().call(method, path, payload)


@pytest.mark.parametrize("lose", ["branch", "pull"])
def test_resubmitting_after_a_lost_write_response_recovers_the_same_submission(home, monkeypatch, lose):
    proven(home, monkeypatch)
    api = RecoverableGitHub(lose)
    made = publish_api.prepare("dark-spots", http=api)
    with pytest.raises(OSError, match="response lost"):
        publish_api.submit(made["package"], signoff=True, http=api, wait=lambda _: None, model="dark-spots")
    result = publish_api.submit(made["package"], signoff=True, http=api, wait=lambda _: None, model="dark-spots")
    again = publish_api.submit(made["package"], signoff=True, http=api, wait=lambda _: None, model="dark-spots")
    assert result["number"] == again["number"] == 42
    assert len(api.refs) == len(api.pulls) == 1
    assert sum(method == "POST" and path.endswith("/pulls") for method, path, _ in api.calls) == 1


@pytest.mark.parametrize("first", ["submit", "status"])
@pytest.mark.parametrize("state", ["open", "closed", "merged"])
def test_lost_review_response_is_recovered_read_only_after_the_official_base_advances(home, monkeypatch, first, state):
    proven(home, monkeypatch)
    api = RecoverableGitHub("pull")
    made = publish_api.prepare("dark-spots", http=api)
    with pytest.raises(OSError, match="response lost"):
        publish_api.submit(made["package"], signoff=True, http=api, wait=lambda _: None, model="dark-spots")
    if state != "open":
        api.pulls[0]["state"] = "closed"
    if state == "merged":
        # GitHub's list response reports merged_at; its source branch may already be deleted.
        api.pulls[0]["merged_at"] = "2026-10-13T00:00:00Z"
        api.refs.clear()
    api.base = "d" * 40
    boundary = len(api.calls)
    if first == "status":
        recovered = publish_api.status(made["package"], http=api)
    else:
        recovered = publish_api.submit(made["package"], signoff=True, http=api, model="dark-spots")
    assert recovered["state"] == state and recovered["url"].endswith("/pull/42")
    checked = publish_api.status(made["package"], http=api)
    assert checked["url"] == recovered["url"] and checked["state"] == state
    assert publish_api.submit(made["package"], signoff=True, http=api, model="dark-spots")["number"] == 42
    assert all(method == "GET" for method, _path, _body in api.calls[boundary:])
    assert len(api.pulls) == 1 and len(api.refs) == (0 if state == "merged" else 1)


def test_a_recovered_branch_requires_fresh_review_before_new_writes_on_a_moved_base(home, monkeypatch):
    proven(home, monkeypatch)
    api = RecoverableGitHub("branch")
    made = publish_api.prepare("dark-spots", http=api)
    with pytest.raises(OSError, match="response lost"):
        publish_api.submit(made["package"], signoff=True, http=api, wait=lambda _: None, model="dark-spots")
    api.base = "d" * 40
    boundary = len(api.calls)
    status = publish_api.status(made["package"], http=api)
    assert status["state"] == "needs review" and "branch" in status["says"]
    with pytest.raises(publish.CantPublish, match="moved on"):
        publish_api.submit(made["package"], signoff=True, http=api, model="dark-spots")
    assert all(method == "GET" for method, _path, _body in api.calls[boundary:])
    assert any("/git/ref/heads/catalog/" in path for _method, path, _body in api.calls[boundary:])
    assert len(api.refs) == 1 and not api.pulls


@pytest.mark.parametrize("changed", ["account", "file", "review", "branch"])
def test_recovery_refuses_altered_identity_or_content_before_any_new_write(home, monkeypatch, changed):
    from pathlib import Path

    proven(home, monkeypatch)
    api = RecoverableGitHub("branch" if changed == "branch" else "pull")
    made = publish_api.prepare("dark-spots", http=api)
    with pytest.raises(OSError, match="response lost"):
        publish_api.submit(made["package"], signoff=True, http=api, wait=lambda _: None, model="dark-spots")
    if changed == "account":
        api.name = "Someone Else"
    elif changed == "file":
        (Path(made["package"]) / "body.md").write_text("Changed after approval")
    elif changed == "review":
        api.pulls[0]["body"] = "Different public review"
    else:
        api.refs[next(iter(api.refs))] = "e" * 40
    boundary = len(api.calls)
    for action in (
        lambda: publish_api.submit(made["package"], signoff=True, http=api, model="dark-spots"),
        lambda: publish_api.status(made["package"], http=api),
    ):
        with pytest.raises(publish.CantPublish, match="someone else|changed|differs|approved commit"):
            action()
    assert all(method == "GET" for method, _path, _body in api.calls[boundary:])


@pytest.mark.parametrize("retained_by", ["submit", "recover"])
@pytest.mark.parametrize("changed", ["head", "title", "body", "base"])
def test_retained_status_refuses_a_review_changed_after_submission_or_recovery(home, monkeypatch, retained_by, changed):
    from pathlib import Path

    proven(home, monkeypatch)
    api = RecoverableGitHub("pull" if retained_by == "recover" else None)
    made = publish_api.prepare("dark-spots", http=api)
    if retained_by == "recover":
        with pytest.raises(OSError, match="response lost"):
            publish_api.submit(made["package"], signoff=True, http=api, wait=lambda _: None, model="dark-spots")
    else:
        publish_api.submit(made["package"], signoff=True, http=api, wait=lambda _: None, model="dark-spots")
    assert publish_api.status(made["package"], http=api)["state"] == "open"
    saved = Path(made["package"]) / "submitted.json"
    retained = saved.read_bytes()
    if changed == "head":
        api.pulls[0]["head"]["sha"] = "e" * 40
    elif changed == "base":
        api.pulls[0]["base"]["ref"] = "another-branch"
    else:
        api.pulls[0][changed] = "Changed after the approved review was retained"
    boundary = len(api.calls)
    with pytest.raises(publish.CantPublish, match="existing review differs"):
        publish_api.status(made["package"], http=api)
    assert saved.read_bytes() == retained, "a refused response must not overwrite the accepted status"
    assert all(method == "GET" for method, _path, _body in api.calls[boundary:])


@pytest.mark.parametrize("change", ["title", "branch", "base", "identity", "message", "files", "body", "added", "absolute", "traversal", "symlink", "directory-link", "package-link", "wrong-model", "unregistered"])
def test_material_changes_and_paths_never_leave_the_machine(home, monkeypatch, tmp_path, change):
    from pathlib import Path
    import shutil

    proven(home, monkeypatch)
    api = GitHub()
    made = publish_api.prepare("dark-spots", http=api)
    package = Path(made["package"])
    model = "dark-spots"
    if change in {"title", "branch", "base", "identity", "message", "files", "absolute", "traversal"}:
        file = package / "proposal.json"
        data = json.loads(file.read_text())
        if change == "absolute":
            data["files"] = {str(tmp_path / "private.txt"): "a" * 64}
        elif change == "traversal":
            data["files"] = {"catalog/models/dark-spots/../../../../private.txt": "a" * 64}
        else:
            data[change] = "changed"
        file.write_text(json.dumps(data))
    elif change == "body":
        (package / "body.md").write_text("different body")
    elif change == "added":
        (package / "catalog/models/dark-spots/extra.py").write_text("unreviewed")
    elif change == "symlink":
        target = package / "catalog/models/dark-spots/adapter.py"
        external = tmp_path / "outside.py"
        target.rename(external)
        target.symlink_to(external)  # same bytes is still a link, not the approved file
    elif change == "directory-link":
        target = package / "catalog/models/dark-spots"
        external = tmp_path / "outside-folder"
        target.rename(external)
        target.symlink_to(external, target_is_directory=True)
    elif change == "package-link":
        external = tmp_path / "outside-package"
        package.rename(external)
        package.symlink_to(external, target_is_directory=True)
    elif change == "wrong-model":
        model = "another-model"
    else:
        external = tmp_path / package.name
        shutil.copytree(package, external)
        package = external
    with pytest.raises(publish.CantPublish, match="review|prepare|model"):
        publish_api.submit(package, signoff=True, http=api, wait=lambda _: None, model=model)
    assert not any(method != "GET" for method, _path, _body in api.calls)


def test_command_binds_the_prepared_package_to_the_requested_model(home, monkeypatch):
    import asyncio
    from orchestrator.commands import model_publish_submit
    from orchestrator.brain.registry import ToolFailed

    proven(home, monkeypatch)
    api = GitHub()
    made = publish_api.prepare("dark-spots", http=api)
    with pytest.raises(ToolFailed, match="another model"):
        asyncio.run(model_publish_submit(None, None, {"model": "mine/another-model", "package": made["package"], "signoff": True}))
    assert not any(method != "GET" for method, _path, _body in api.calls)
