"""Release-Mac checks: promotion identity and exact candidate approval, no public writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

MACOS = Path(__file__).resolve().parents[2] / "packaging/macos"


@pytest.fixture
def release(monkeypatch):
    monkeypatch.syspath_prepend(str(MACOS))
    spec = importlib.util.spec_from_file_location("mac_local_release", MACOS / "release-channel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def beta(commit="a" * 40):
    return {"channel": "beta", "bundleId": "org.hookeui.HookeUI.beta", "revision": commit,
            "notarized": True, "appcast": True}


@pytest.mark.parametrize("change", [{"revision": "b" * 40}, {"channel": "stable"},
    {"bundleId": "org.hookeui.HookeUI"}, {"notarized": False}, {"appcast": False}])
def test_stable_rejects_wrong_or_unqualified_beta(release, change):
    with pytest.raises(ValueError, match="exact source"):
        release.require_promotion("stable", "a" * 40, {**beta(), **change})


def test_promotion_requires_beta_and_same_source(release):
    with pytest.raises(ValueError):
        release.require_promotion("stable", "a" * 40, None)
    release.require_promotion("stable", "a" * 40, beta())
    release.require_promotion("beta", "a" * 40, None)
    with pytest.raises(ValueError):
        release.require_promotion("beta", "a" * 40, beta())


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_unpublished_or_withdrawn_beta_is_not_promotable(release, monkeypatch, flag):
    monkeypatch.setattr(release, "run", lambda *a, **k: json.dumps({"draft": False, "prerelease": False, flag: True}))
    with pytest.raises(ValueError, match="unpublished or withdrawn"):
        release.published_beta("0.2.2")


def candidate(tmp_path):
    data = b"exact signed candidate stand-in"
    (tmp_path / "HookeUI-Beta-0.2.2.dmg").write_bytes(data)
    intent = {"channel": "beta", "revision": "a" * 40, "version": "0.2.2", "build": 4000}
    receipt = {**intent, "bundleId": "org.hookeui.HookeUI.beta", "notarized": True, "appcast": True,
               "dmg": "HookeUI-Beta-0.2.2.dmg", "dmgSha256": hashlib.sha256(data).hexdigest()}
    return receipt, intent


@pytest.mark.parametrize("change", [{"revision": "b" * 40}, {"build": 3999}, {"version": "0.2.1"},
                                    {"channel": "stable"}, {"notarized": False}, {"appcast": False}])
def test_changed_candidate_cannot_be_published(release, tmp_path, change):
    receipt, intent = candidate(tmp_path)
    with pytest.raises(ValueError):
        release.require_candidate({**receipt, **change}, intent, receipt["dmgSha256"], tmp_path)


def test_approval_hash_checks_actual_bytes_not_just_receipt(release, tmp_path):
    receipt, intent = candidate(tmp_path)
    release.require_candidate(receipt, intent, receipt["dmgSha256"], tmp_path)
    with pytest.raises(ValueError, match="Approval hash"):
        release.require_candidate(receipt, intent, "0" * 64, tmp_path)
    (tmp_path / receipt["dmg"]).write_bytes(b"different build")
    with pytest.raises(ValueError, match="Approval hash"):
        release.require_candidate(receipt, intent, receipt["dmgSha256"], tmp_path)


def test_publish_reuses_candidate_and_rejects_intervening_release(release, monkeypatch, tmp_path):
    from argparse import Namespace
    receipt, intent = candidate(tmp_path)
    intent.update(promote_from=None, evidence="https://example.test/receipt")
    (tmp_path / "release.json").write_text(json.dumps(receipt))
    (tmp_path / "release-intent.json").write_text(json.dumps(intent))
    monkeypatch.setattr(release, "require_source", lambda channel, commit: None)
    monkeypatch.setattr(release, "next_release_build", lambda count: max(count, 1))
    calls = []
    monkeypatch.setattr(release, "run", lambda *a, **k: calls.append(a))
    args = Namespace(channel="beta", out=tmp_path, sha256=receipt["dmgSha256"])
    release.publish(args)
    assert calls == [("bash", str(MACOS / "publish.sh"), "--channel", "beta")]
    assert (tmp_path / "publication-receipt.json").exists()
    monkeypatch.setattr(release, "next_release_build", lambda count: 4001)
    calls.clear()
    with pytest.raises(ValueError, match="newer build"):
        release.publish(args)
    assert calls == []


def test_missing_published_feed_refuses_numbering(release, monkeypatch):
    monkeypatch.setattr(release, "run", lambda *a, **k: "v0.2.1")
    monkeypatch.setattr(release, "read", lambda url: None)
    monkeypatch.setattr(release, "published_receipt", lambda *a: {"appcast": True, "build": 5000, "version": "0.2.1"})
    with pytest.raises(ValueError, match="has no appcast"):
        release.next_release_build(4000)
    monkeypatch.setattr(release, "run", lambda *a, **k: "")
    assert release.next_release_build(4000) == 4000


def test_prepare_builds_native_packaging_from_selected_checkout(release, monkeypatch, tmp_path):
    from argparse import Namespace
    from contextlib import contextmanager
    source = tmp_path / "pinned-source"
    source.mkdir()
    (source / "native.swift").write_text("accepted native source")
    out = tmp_path / "candidate"
    monkeypatch.setattr(release, "require_source", lambda channel, commit: None)
    monkeypatch.setattr(release, "next_release_build", lambda count: max(count, 1))
    checked = []

    @contextmanager
    def checkout(commit):
        checked.append(commit)
        yield source

    monkeypatch.setattr(release, "source_checkout", checkout)
    calls = []

    def execute(*args, **kwargs):
        calls.append(args)
        if args[0] == "git":
            return "4000"
        assert args[1] == str(source / "packaging/macos/build.sh")
        assert kwargs["env"]["RELEASE_CHANNEL"] == "beta"
        out.mkdir()
        receipt, _ = candidate(out)
        (out / "release.json").write_text(json.dumps(receipt))
        return ""

    monkeypatch.setattr(release, "run", execute)
    args = Namespace(channel="beta", commit="a" * 40, version="0.2.2", promote_from=None,
                     evidence="https://example.test/checks", out=out)
    release.prepare(args)
    assert checked == ["a" * 40]
    assert (out / "release-intent.json").exists()
    assert not any("publish.sh" in str(arg) for call in calls for arg in call)


def test_source_checkout_preserves_native_source_at_selected_revision(release, monkeypatch, tmp_path):
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, text=True, capture_output=True).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Release test")
    git("config", "user.email", "release@example.test")
    native = repo / "native.swift"
    native.write_text("accepted native code")
    git("add", ".")
    git("commit", "-qm", "accepted")
    selected = git("rev-parse", "HEAD")
    native.write_text("unreviewed newer native code")
    monkeypatch.setattr(release, "REPO", repo)
    with release.source_checkout(selected) as checkout:
        assert (checkout / "native.swift").read_text() == "accepted native code"
    assert not checkout.exists()
    assert native.read_text() == "unreviewed newer native code"


def test_legacy_download_without_updater_still_counts_its_recorded_build(release, monkeypatch):
    monkeypatch.setattr(release, "run", lambda *a, **k: "v0.1.0-beta.20261019.1")
    monkeypatch.setattr(release, "read", lambda url: None)
    monkeypatch.setattr(release, "published_receipt", lambda *a: {
        "appcast": False, "build": 5000, "version": "0.1.0-beta.20261019.1"})
    assert release.next_release_build(4000) == 5001


def test_candidate_hash_works_with_release_macs_python_before_311(release, monkeypatch, tmp_path):
    # macOS's /usr/bin/python3 lacks hashlib.file_digest; tests use a newer venv.
    monkeypatch.delattr(release.hashlib, "file_digest", raising=False)
    receipt, intent = candidate(tmp_path)
    release.require_candidate(receipt, intent, receipt["dmgSha256"], tmp_path)


@pytest.fixture
def release_repo(release, tmp_path, monkeypatch):
    """Actual local Git remote; no network or publication credentials."""
    import subprocess
    remote = tmp_path / "remote.git"
    repo = tmp_path / "source"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, text=True,
                              capture_output=True).stdout.strip()

    git("config", "user.name", "Release test")
    git("config", "user.email", "release@example.test")
    git("remote", "add", "origin", str(remote))
    git("commit", "--allow-empty", "-qm", "stable baseline")
    baseline = git("rev-parse", "HEAD")
    git("push", "-q", "origin", "main")
    git("checkout", "-qb", "dev")
    git("commit", "--allow-empty", "-qm", "beta candidate")
    selected = git("rev-parse", "HEAD")
    git("push", "-q", "origin", "dev")
    monkeypatch.setattr(release, "REPO", repo)
    return git, remote, baseline, selected


def test_beta_source_requires_dev_and_stable_requires_promotion(release, release_repo):
    import subprocess
    git, _, baseline, selected = release_repo
    release.require_source("beta", selected)
    release.require_source("stable", baseline)
    with pytest.raises(subprocess.CalledProcessError):
        release.require_source("stable", selected)
    # Promotion preserves the Beta commit even when main has a new merge SHA.
    git("checkout", "main")
    git("merge", "--no-ff", "-m", "accepted Beta promotion", "dev")
    git("push", "-q", "origin", "main")
    release.require_source("stable", selected)
    git("commit", "--allow-empty", "-qm", "main-only change")
    main_only = git("rev-parse", "HEAD")
    git("push", "-q", "origin", "main")
    with pytest.raises(subprocess.CalledProcessError):
        release.require_source("beta", main_only)


def test_source_refreshes_remote_and_refuses_missing_branch(release, release_repo):
    import subprocess
    git, remote, baseline, selected = release_repo
    git("update-ref", "refs/remotes/origin/dev", baseline)
    # Even a narrow clone's default fetch mapping must not leave origin/dev stale.
    git("config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/main")
    release.require_source("beta", selected)
    assert git("rev-parse", "origin/dev") == selected
    subprocess.run(["git", "--git-dir", str(remote), "update-ref", "-d", "refs/heads/dev"], check=True)
    with pytest.raises(subprocess.CalledProcessError):
        release.require_source("beta", selected)
    # The retained tracking ref is not permission to release a deleted branch.
    assert git("rev-parse", "origin/dev") == selected


@pytest.mark.parametrize("channel,commit", [("preview", "a" * 40), ("beta", "HEAD"),
                                             ("stable", "a" * 39)])
def test_invalid_source_refuses_before_git(release, monkeypatch, channel, commit):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid identity must not reach Git")
    monkeypatch.setattr(release, "run", unexpected)
    with pytest.raises(ValueError):
        release.require_source(channel, commit)


@pytest.mark.parametrize("command", ["prepare", "publish"])
def test_source_refusal_blocks_build_and_upload(release, monkeypatch, tmp_path, command):
    from argparse import Namespace
    receipt, intent = candidate(tmp_path)
    intent.update(promote_from=None, evidence="https://example.test/checks")
    (tmp_path / "release.json").write_text(json.dumps(receipt))
    (tmp_path / "release-intent.json").write_text(json.dumps(intent))
    checked = []
    def refuse(channel, commit):
        checked.append((channel, commit))
        raise ValueError("source not on required branch")
    monkeypatch.setattr(release, "require_source", refuse)
    monkeypatch.setattr(release, "run", lambda *a, **kw: pytest.fail("must not build or upload"))
    args = Namespace(channel="beta", commit=intent["revision"], version="0.2.2",
                     promote_from=None, evidence=intent["evidence"], out=tmp_path,
                     sha256=receipt["dmgSha256"])
    with pytest.raises(ValueError, match="required branch"):
        getattr(release, command)(args)
    assert checked == [("beta", intent["revision"])]
    assert not (tmp_path / "publication-receipt.json").exists()
