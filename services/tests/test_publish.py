"""Publishing an entry of yours (orchestrator/publish.py, `make catalog-pr`; phase 3's E4): only an entry that pins what
it gets and is proven on this Mac can be published; the pull request is made in a worktree of its own, its whole folder
and nothing of this Mac's own state, and it is pushed only when asked. A scratch repository and remote, a stand-in gh."""
import json
import os
import shutil
import subprocess

import pytest

from gpu_worker import manifests as catalog
from orchestrator import env_runner, envs, publish
from tests.test_env_runner import ADAPTER, ENTRY, standin_uv
from tests.waiting import until

LOCK = "numpy==2.0.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "repo" / "models"
    shutil.copytree(catalog.ROOT, root)
    monkeypatch.setattr(catalog, "ROOT", root)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.delenv("MICROMIND_ENVS", raising=False)
    monkeypatch.setenv("MICROMIND_ASSETS", str(tmp_path / "empty-assets"))
    catalog.manifests.cache_clear()
    yield tmp_path
    for one in env_runner._clients.values():
        one.stop()
    env_runner._clients.clear()
    catalog.manifests.cache_clear()


def yours(home, adapter: str = ADAPTER, lock: str = LOCK, extra: str = "") -> catalog.Manifest:
    """An entry in your own folder, as a contributor writes it before publishing."""
    folder = home / "catalog" / "mine" / "models" / "dark-spots"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.toml").write_text(extra + ENTRY.format().replace('"install:dark-spots"', '"install:mine/dark-spots"'))
    (folder / "env.lock").write_text(lock)
    (folder / "adapter.py").write_text(adapter)
    m = catalog.mine().get("mine/dark-spots")
    assert m is not None, catalog.problems().get("mine/dark-spots")
    return m


def install(home, monkeypatch, m) -> None:
    if not (home / "bin" / "uv").exists():
        standin_uv(home, monkeypatch)
    queue = envs.Installs()
    queue.start(m.key)
    try:
        until(lambda: queue.wire()[0]["state"] in ("done", "failed"))
        assert queue.wire()[0]["state"] == "done", queue.wire()
    finally:
        # Never outlived by its install: one still running when this gives up is stopped and joined here, before the
        # test's folders go (the gate of 2026-10-14: a proof still running wrote into a deleted folder after its test).
        assert queue.stop(), "its install didn't stop within the queue's own bound"


def test_only_what_pins_everything_and_is_proven_here_can_be_published(home, monkeypatch):
    m = yours(home, lock="numpy==2.0.0\n", adapter=ADAPTER + "\nimport subprocess\nsubprocess.run('pip install scikit-image', shell=True)\n"
              "from urllib.request import urlopen\n")
    why = publish.problems(m)
    assert any("without a hash (numpy==2.0.0)" in w for w in why)
    assert any("adapter.py: it installs packages at run time" in w for w in why)
    assert any("adapter.py: it fetches from the network at run time" in w for w in why)
    assert any("isn't proven on this Mac" in w for w in why)
    with pytest.raises(publish.CantPublish, match="can't be published yet"):
        publish.propose("dark-spots")

    m = yours(home)
    install(home, monkeypatch, m)
    assert publish.problems(m) == []
    yours(home, adapter=ADAPTER.replace("< 128", "< 100"))  # changed since its proof: this version isn't proven
    assert any("isn't proven on this Mac for this version" in w for w in publish.problems(catalog.mine()["mine/dark-spots"]))


def test_a_file_only_on_this_mac_cant_be_published(home):
    m = yours(home)
    m = catalog.Manifest(**{**{f: getattr(m, f) for f in m.__dataclass_fields__},
                            "files": ({"path": "w.pt", "bytes": 3, "sha256": "b" * 64},)})  # added from a path
    assert any("w.pt has no URL and hash anyone else can fetch it by" in w for w in publish.problems(m))


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.parametrize("input_origin,identity_config", [("generated", "worktree"), ("generated", "conditional"),
                                                           ("private-asset", "shared")])
def test_only_the_reviewed_package_is_pushed_to_the_fork_and_pr_targets_the_official_base(home, monkeypatch, input_origin, identity_config):
    import sys

    repo, upstream, fork = contribution_repositories(home, monkeypatch)
    if identity_config == "worktree":
        # A contributor may configure identity only for this worktree. The helper's
        # temporary worktree must retain that deliberate certifier, not common config.
        _git("config", "extensions.worktreeConfig", "true", cwd=repo)
        _git("config", "--worktree", "user.name", "t", cwd=repo)
        _git("config", "--worktree", "user.email", "t@t", cwd=repo)
        _git("config", "user.name", "Unrelated identity", cwd=repo)
        _git("config", "user.email", "unrelated@example.invalid", cwd=repo)
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            monkeypatch.delenv(key, raising=False)
    if identity_config == "conditional":
        # Conditional identity is valid in the contributor checkout but resolves
        # differently in the temporary worktree, even when config is copied.
        identity = home / "contributor-identity.config"
        identity.write_text("[user]\nname = t\nemail = t@t\n")
        _git("config", "user.name", "Unrelated identity", cwd=repo)
        _git("config", "user.email", "unrelated@example.invalid", cwd=repo)
        _git("config", f"includeIf.gitdir:{repo}/.git.path", str(identity), cwd=repo)
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            monkeypatch.delenv(key, raising=False)
    gh_log = home / "gh.json"
    (home / "ghbin").mkdir()
    (home / "ghbin/gh").write_text(f"#!{sys.executable}\n" +
        "import json, sys\nfrom pathlib import Path\n" +
        f"Path({str(gh_log)!r}).write_text(json.dumps({{'args': sys.argv[1:], " +
        "'body': Path(sys.argv[sys.argv.index('--body-file') + 1]).read_text()}))\n" +
        "print('https://github.com/science/catalog/pull/7')\n")
    (home / "ghbin/gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{home / 'ghbin'}{os.pathsep}{os.environ['PATH']}")
    if input_origin == "private-asset":
        import cv2
        from orchestrator.assets import store
        from orchestrator.model_checks import FRAME
        from tests.test_env_runner import _picture

        monkeypatch.setenv("MICROMIND_ASSETS", str(home / "private-owner-images"))
        source = store().bench(FRAME)
        source.parent.mkdir(parents=True)
        source.write_bytes(cv2.imencode(".png", cv2.cvtColor(_picture(), cv2.COLOR_RGB2BGR))[1].tobytes())
    m = yours(home)
    install(home, monkeypatch, m)
    package = home / "reviewed"
    publish.propose(m.id, repo=repo, base_remote="upstream", base_branch="release", output=package)
    proposal = json.loads((package / "proposal.json").read_text())
    # Editing the source entry after preparation cannot change what is submitted.
    (catalog.entry_folder(m) / "adapter.py").write_text("raise RuntimeError('new, unreviewed content')\n")
    said = publish.propose(m.id, push=True, repo=repo, package=package, signoff=True)
    assert "Opened https://github.com/science/catalog/pull/7" in said
    branch = proposal["head"]["branch"]
    result = subprocess.run(["git", "show", f"{branch}:catalog/models/dark-spots/adapter.py"], cwd=fork,
                            capture_output=True, text=True, check=True)
    assert result.stdout == ADAPTER
    commit = subprocess.run(["git", "show", "-s", "--format=%an <%ae>%n%cn <%ce>%n%B", branch], cwd=fork,
                            capture_output=True, text=True, check=True).stdout
    assert commit.startswith("t <t@t>\nt <t@t>\n")
    assert commit.count("Signed-off-by: t <t@t>") == 1
    tree = subprocess.run(["git", "ls-tree", "-r", "--name-only", branch], cwd=fork,
                          capture_output=True, text=True, check=True).stdout.splitlines()
    assert sorted(tree) == ["README.md", *[f"catalog/models/dark-spots/{f}" for f in ("adapter.py", "env.lock", "model.toml")]]
    logged = json.loads(gh_log.read_text())
    args = logged["args"]
    assert args[args.index("--repo") + 1] == "science/catalog"
    assert args[args.index("--base") + 1] == "release"
    assert args[args.index("--head") + 1] == f"contributor:{branch}"
    expected = "generated fixture" if input_origin == "generated" else "asset image"
    assert expected in logged["body"] and "not scientific qualification" in logged["body"]
    assert "Input RGB8 SHA-256" in logged["body"]
    assert str(home) not in logged["body"] and "private-owner-images" not in logged["body"]
    assert (package / "proof-input.npy").is_file(), "the proof image stays local, absent from the complete commit tree above"
    proof = json.loads((package / "proof.json").read_text())
    assert proof["input"]["file"] == "proof-input.npy"
    assert proof["input"]["evidenceClass"] == ("generated-fixture" if input_origin == "generated" else "asset-image")
    assert not subprocess.run(["git", "branch", "--list", "catalog/*"], cwd=upstream,
                              capture_output=True, text=True, check=True).stdout.strip()
    assert len(subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout.split("worktree ")) == 2


def test_local_adapter_facts_follow_activation_and_do_not_claim_it_is_missing(home, monkeypatch):
    from orchestrator import catalog_view, readiness

    m = yours(home)
    def run_fact():
        return next(f for f in catalog_view.facts(m, {}, lambda key: False, [], os.environ) if f["kind"] == "runs")
    assert "adapter:segment" in run_fact()["says"]
    assert "Install" in run_fact()["says"] and not run_fact()["ok"]
    assert "with your permissions" in run_fact()["says"], "said before activation, when the user chooses"
    assert readiness.model(m.key, {}).status == "needs-setup"
    install(home, monkeypatch, m)
    assert run_fact()["ok"] and "with your permissions" in run_fact()["says"]
    assert readiness.model(m.key, {}).status == "ready"
    (catalog.entry_folder(m) / "adapter.py").unlink()
    # The previous proven snapshot continues working even if the new candidate lost its adapter.
    assert run_fact()["ok"] and "in use" in run_fact()["says"]
    envs.remove(m)
    assert not run_fact()["ok"] and "adapter.py is missing" in run_fact()["says"]


def contribution_repositories(home, monkeypatch):
    for key, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                       "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(key, value)
    upstream, fork, repo = home / "upstream.git", home / "fork.git", home / "contributor"
    _git("init", "-q", "--bare", "-b", "release", str(upstream), cwd=home)
    _git("init", "-q", "--bare", "-b", "main", str(fork), cwd=home)
    _git("clone", "-q", str(upstream), str(repo), cwd=home)
    _git("config", "user.name", "t", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    (repo / "README.md").write_text("Official catalog\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    _git("push", "-q", "origin", "HEAD:release", cwd=repo)
    _git("remote", "rename", "origin", "upstream", cwd=repo)
    _git("remote", "set-url", "upstream", "https://github.com/science/catalog.git", cwd=repo)
    _git("remote", "add", "origin", "git@github.com:contributor/catalog.git", cwd=repo)
    # Only fetch/push transport is a stand-in. Git's remote identity and URL rewrite queries stay real.
    import sys

    real_git = shutil.which("git")
    gitbin = home / "gitbin"
    gitbin.mkdir()
    overrides = ["-c", f"url.{upstream}.insteadOf=https://github.com/science/catalog.git",
                 "-c", f"url.{fork}.insteadOf=git@github.com:contributor/catalog.git"]
    (gitbin / "git").write_text(f"#!{sys.executable}\nimport subprocess, sys\n" +
        f"command = [{real_git!r}] + ({overrides!r} if sys.argv[1] in ('fetch', 'push') else []) + sys.argv[1:]\n" +
        "raise SystemExit(subprocess.run(command).returncode)\n")
    (gitbin / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{gitbin}{os.pathsep}{os.environ['PATH']}")
    return repo, upstream, fork


def test_preparation_is_an_inspectable_local_package_for_the_explicit_base_and_fork(home, monkeypatch):
    import hashlib

    repo, upstream, fork = contribution_repositories(home, monkeypatch)
    m = yours(home)
    install(home, monkeypatch, m)
    (catalog.entry_folder(m) / "activation.json").write_text("private local state")
    package = home / "proposal"
    said = publish.propose("dark-spots", repo=repo, base_remote="upstream", base_branch="release", output=package)
    assert str(package) in said and "Nothing has left this Mac" in said
    proposal = json.loads((package / "proposal.json").read_text())
    assert proposal["base"]["repository"] == "science/catalog"
    assert proposal["base"]["branch"] == "release"
    assert proposal["head"]["repository"] == "contributor/catalog"
    assert proposal["head"]["remote"] == "origin"
    assert sorted(p.name for p in (package / "catalog/models/dark-spots").iterdir()) == ["adapter.py", "env.lock", "model.toml"]
    proof = json.loads((package / "proof.json").read_text())
    assert proof["input"]["evidenceClass"] == "generated-fixture"
    assert hashlib.sha256((package / "proof-input.npy").read_bytes()).hexdigest() == proof["input"]["fileSha256"]
    assert "generated fixture" in (package / "body.md").read_text()
    assert "adapter.py" in (package / "proposal.patch").read_text()
    assert subprocess.run(["git", "for-each-ref", "--format=%(refname)"], cwd=fork,
                          capture_output=True, text=True, check=True).stdout == ""
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout == ""


def test_cli_prepares_locally_and_requires_that_package_for_publish(home, monkeypatch, capsys):
    repo, _, _ = contribution_repositories(home, monkeypatch)
    monkeypatch.setattr(publish, "REPO", repo)
    m = yours(home)
    install(home, monkeypatch, m)
    package = home / "cli-package"
    assert publish.main([m.id, "--base-remote", "upstream", "--base", "release", "--output", str(package)]) == 0
    assert (package / "proposal.json").is_file()
    assert publish.main([m.id, "--push"]) == 1
    assert "PACKAGE=<folder>" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["missing-remote", "unknown-repository", "missing-base", "changed-file", "changed-fork", "missing-input"])
def test_unreviewable_or_ambiguous_submission_stops_before_any_push(home, monkeypatch, failure):
    repo, upstream, fork = contribution_repositories(home, monkeypatch)
    m = yours(home)
    install(home, monkeypatch, m)
    package = home / "candidate"
    if failure == "missing-remote":
        with pytest.raises(publish.CantPublish, match="remote .* is missing"):
            publish.propose(m.id, repo=repo, base_remote="official", output=package)
    elif failure == "unknown-repository":
        _git("remote", "set-url", "origin", str(fork), cwd=repo)
        with pytest.raises(publish.CantPublish, match="GitHub HTTPS or SSH"):
            publish.propose(m.id, repo=repo, base_remote="upstream", base_branch="release", output=package)
    elif failure == "missing-base":
        with pytest.raises(publish.CantPublish, match="git fetch upstream missing"):
            publish.propose(m.id, repo=repo, base_remote="upstream", base_branch="missing", output=package)
    elif failure == "missing-input":
        source = env_runner.proof(m)["input"]
        (env_runner.proof_path(m).parent / source["file"]).unlink()
        with pytest.raises(publish.CantPublish, match="proof input is missing"):
            publish.propose(m.id, repo=repo, base_remote="upstream", base_branch="release", output=package)
    else:
        publish.propose(m.id, repo=repo, base_remote="upstream", base_branch="release", output=package)
        if failure == "changed-file":
            (package / "catalog/models/dark-spots/adapter.py").write_text("new, unreviewed code")
        else:
            _git("remote", "set-url", "origin", "https://github.com/someone-else/catalog.git", cwd=repo)
        with pytest.raises(publish.CantPublish, match="changed"):
            publish.propose(m.id, push=True, repo=repo, package=package, signoff=True)
    assert subprocess.run(["git", "for-each-ref", "--format=%(refname)"], cwd=fork,
                          capture_output=True, text=True, check=True).stdout == ""


def test_preparation_uses_the_effective_git_destination_after_url_rewrites(home, monkeypatch):
    repo, _, _ = contribution_repositories(home, monkeypatch)
    _git("remote", "set-url", "origin", "https://github.com/alias/catalog.git", cwd=repo)
    _git("config", "url.https://github.com/contributor/catalog.git.insteadOf", "https://github.com/alias/catalog.git", cwd=repo)
    m = yours(home)
    install(home, monkeypatch, m)
    package = home / "effective-destination"
    publish.propose(m.id, repo=repo, base_remote="upstream", base_branch="release", output=package)
    proposal = json.loads((package / "proposal.json").read_text())
    assert proposal["head"]["repository"] == "contributor/catalog"


def test_supported_cellpose_entry_is_publishable_only_while_its_real_readiness_is_ready(home):
    from orchestrator import downloads, mine, readiness
    from tests.test_mine import a_file, passing

    weights = a_file(home)
    key = mine.add(str(weights), "Publishable cells")["model"]
    # Contributor supplies public pinned provenance. No network request or real Cellpose inference occurs.
    manifest = catalog.entry_folder(catalog.mine()[key]) / "model.toml"
    text = manifest.read_text().replace(weights.resolve().as_uri(), "https://example.org/publishable-cells.pt")
    text = text.replace(str(weights.resolve()), "https://example.org/publishable-cells")
    manifest.write_text(text)
    m = catalog.mine()[key]
    downloads.adopt_file(m, weights)
    assert readiness.model(key, {}).status == "needs-setup"
    assert any("isn't on here yet" in reason for reason in publish.problems(m))
    assert mine.activate(key, home / "fixture.jpg", run=passing)["ok"]
    assert readiness.model(key, {}).status == "ready"
    assert publish.problems(m) == []
    mine.deactivate(key)
    assert any("isn't on here yet" in reason for reason in publish.problems(m))


def test_publishing_requires_explicit_dco_choice_before_git_or_network(home, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("no process may start before the contributor certifies")
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert publish.main(["dark-spots", "--push", "--package", str(home / "reviewed")]) == 1
    said = capsys.readouterr().out
    assert "Developer Certificate of Origin" in said
    assert "public" in said and "name" in said and "email" in said
    assert "SIGNOFF=1" in said and "--signoff" in said


@pytest.mark.parametrize("failure", ["missing-name", "missing-email", "invalid-name", "invalid-email", "author-override", "committer-override"])
def test_certification_requires_deliberately_configured_matching_identity_before_fetch(home, monkeypatch, failure):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo, _, _ = contribution_repositories(home, monkeypatch)
    if failure.startswith("missing-"):
        _git("config", "--unset", "user." + failure.removeprefix("missing-"), cwd=repo)
    elif failure.startswith("invalid-"):
        _git("config", "user." + failure.removeprefix("invalid-"), "Someone\nSigned-off-by: Other <other@example.org>", cwd=repo)
    else:
        monkeypatch.setenv("GIT_" + failure.removesuffix("-override").upper() + "_EMAIL", "other@example.org")
    run = subprocess.run
    def no_remote(command, *args, **kwargs):
        assert not any(arg in ("fetch", "push", "commit") for arg in command), command
        return run(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", no_remote)
    with pytest.raises(publish.CantPublish, match="Git identity"):
        publish.propose("dark-spots", push=True, repo=repo, package=home / "reviewed", signoff=True)


def test_changed_certification_is_refused_before_push(home, monkeypatch):
    import sys

    repo, _, fork = contribution_repositories(home, monkeypatch)
    m = yours(home)
    install(home, monkeypatch, m)
    package = home / "reviewed-certification"
    publish.propose(m.id, repo=repo, base_remote="upstream", base_branch="release", output=package)
    hook = repo / ".git/hooks/commit-msg"
    hook.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport sys\n"
                    "p = Path(sys.argv[1])\n"
                    "p.write_text(p.read_text().replace('Signed-off-by: t <t@t>', "
                    "'Signed-off-by: Other <other@example.invalid>'))\n")
    hook.chmod(0o755)
    ghbin = home / "unexpected-gh"
    ghbin.mkdir()
    (ghbin / "gh").write_text("#!/bin/sh\necho 'test refuses any GitHub call' >&2\nexit 1\n")
    (ghbin / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{ghbin}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(publish.CantPublish, match="commit no longer matches your DCO certification"):
        publish.propose(m.id, push=True, repo=repo, package=package, signoff=True)
    assert subprocess.run(["git", "for-each-ref", "--format=%(refname)"], cwd=fork,
                          capture_output=True, text=True, check=True).stdout == ""
    assert (package / "proposal.json").is_file()
