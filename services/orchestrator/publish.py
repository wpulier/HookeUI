"""Prepare a contributed catalog entry and its proof for local review before explicit publishing.

Preparation reads local Git remote identities and a fetched base ref; it copies only the entry files, records an
inspectable diff, exact proof input/receipt, and PR body. The contributor reviews that private package first.
Publishing requires its path, checks its hashes and unchanged base/head routing, then pushes only catalog files to
the contributor remote and opens a PR against the explicit official repository. Proof images stay local.
This technical check does not select contributor terms, establish ownership, or qualify scientific accuracy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog

REPO = catalog.ROOT.parent.parent
BASE_BRANCH = "dev"  # the official catalog takes pull requests into dev only; main is promoted to (CONTRIBUTING.md)
FORBIDDEN = (  # what a python-env entry's own code may not do: get anything at run time
    (re.compile(r"\bpip\b[^\n]*\binstall\b"), "installs packages at run time (pin them in its lockfile)"),
    (re.compile(r"\b(?:urllib\.request|requests\.(?:get|post)|httpx\.|urlopen\()"), "fetches from the network at run time "
     "(pin what it needs as files)"),
    (re.compile(r"\btorch\.hub\.load\b"), "loads from torch.hub at run time (pin the weights as files)"),
    (re.compile(r"\bweights_only\s*=\s*False\b"), "loads a checkpoint that may run code (weights_only=False)"),
    (re.compile(r"\bfrom_pretrained\([^)]*\)") , None),  # allowed only with local_files_only: checked below
)
DCO = Path(__file__).resolve().parents[2] / "docs/contributing/DCO.txt"


def certification() -> str:
    return (DCO.read_text() + "\nYour Git name and email become public in the sign-off and commit. "
            "Read the certificate and certify only your own contribution. "
            "Choose SIGNOFF=1 (CLI: --signoff) to certify DCO 1.1 for this reviewed package.\n")


HASHED = re.compile(r"--hash=sha256:[0-9a-f]{64}")


class CantPublish(RuntimeError):
    pass


def _entry(id_: str, environ: Mapping[str, str]) -> catalog.Manifest:
    found = catalog.mine(environ)
    m = found.get(f"mine/{id_.removeprefix('mine/')}")
    if m is None:
        why = catalog.problems(environ).get(f"mine/{id_.removeprefix('mine/')}")
        raise CantPublish(f"there is no entry of yours called {id_}" + (f" that reads cleanly: {'; '.join(why)}" if why else ""))
    return m


def contribution_files(m: catalog.Manifest, environ: Mapping[str, str]) -> dict[str, bytes]:
    """Bundles use the same declared, bounded files on both distribution paths."""
    from . import envs, share
    from .publish_api import _read_file

    if m.members:
        try:
            return share.portable_files(m, environ)
        except share.CantShare as exc:
            raise CantPublish(str(exc)) from exc
    folder = catalog.entry_folder(m, environ)
    return {rel.as_posix(): _read_file(folder, rel.as_posix()) for rel in envs.entry_files(m, environ)}


def problems(m: catalog.Manifest, environ: Mapping[str, str] = os.environ, proven: bool = True) -> list[str]:
    """What stops it being published, in plain words (none: it can be). `proven=False`: all but the proof on this Mac
    (the repository's check, bench/catalog_ci.py, proves it itself)."""
    from . import licences, readiness
    from .bundle_admission import integration_problem

    out = []
    if problem := integration_problem(m):
        out.append(problem)
    folder = catalog.entry_folder(m, environ)
    from .operations import OPERATIONS

    in_the_app = bool(m.implements) and all(i.operation in OPERATIONS and OPERATIONS[i.operation].impl(i.impl)
                                            for i in m.implements)
    if not m.runner and m.kind not in ("data", "runtime") and (not in_the_app or "install:adapter" in m.needs_to_run):  # data runs nothing
        out.append("it says nothing runs it yet (a skeleton): write its adapter and give it runner python-env")
    from . import downloads

    for f in m.files:
        try:
            downloads.url_of(m, f)  # where any app's Download fetches it from: its URL, or its pinned repository's
            fetchable = bool(f.get("sha256") or f.get("git_sha1"))
        except downloads.Failed:
            fetchable = False
        if not fetchable:
            out.append(f"its file {f['path']} has no URL and hash anyone else can fetch it by (it is only on this "
                       "Mac): publish it (Hugging Face, Zenodo, a release) and pin its URL and sha256")
    for cid in m.licence:
        try:
            licences.component(cid)
        except KeyError:
            out.append(f"its licence names {cid}, which is no component the app lists and none it declares")
    if m.runner == "python-env" or m.kind == "runtime":
        from . import env_runner, envs

        portable = None
        if m.members:
            try:
                portable = contribution_files(m, environ)
            except CantPublish as exc:
                out.append(str(exc))
                portable = {}
        elif m.runner == "python-env":
            file = m.adapter.partition(":")[0].replace(".", "/") + ".py"
            if not (folder / file).is_file():
                out.append(f"its adapter {file} is not in its folder")
        lock_name = str((m.env or {}).get("lock", ""))
        lock = folder / lock_name
        lock_text = (portable[lock_name].decode(errors="replace") if portable is not None and lock_name in portable
                     else lock.read_text() if portable is None and lock.is_file() else None)
        if not m.env or lock_text is None:
            out.append("it names no lockfile of its environment beside its manifest")
        else:
            loose = [line.split()[0] for line in _requirements(lock_text) if not HASHED.search(line)]
            if loose:
                out.append(f"its lockfile pins {len(loose)} package{'s' if len(loose) != 1 else ''} without a hash "
                           f"({', '.join(loose[:3])}): make it with uv pip compile --generate-hashes")
        for rel in (map(Path, portable) if portable is not None else envs.entry_files(m, environ)):
            if rel.suffix != ".py":
                continue
            path = folder / rel
            text = portable[rel.as_posix()].decode(errors="replace") if portable is not None else path.read_text(errors="replace")
            for pattern, why in FORBIDDEN:
                for hit in pattern.finditer(text):
                    if why is None and "local_files_only=True" in hit.group(0):
                        continue
                    out.append(f"{path.relative_to(folder)}: it "
                               f"{why or 'loads with from_pretrained without local_files_only=True (it may download)'}")
                    break
        found = env_runner.in_use(m, environ) if proven and m.runner == "python-env" else None
        if proven and m.kind == "runtime" and envs.python(m, environ) is None:
            out.append("its pinned runtime is not prepared on this Mac: Install it and pass its import checks")
        if proven and m.runner == "python-env" and (found is None or not found.current):
            out.append("it isn't proven on this Mac for this version: Install it (its contract smoke check records "
                       "the actual input)")
    elif proven:
        r = readiness.model(m.key, {}, environ=environ)
        if r.status != "ready":
            out.append(f"it isn't on here yet ({r.status}): turn it on until its check passes")
    return out


def _requirements(text: str) -> list[str]:
    """A lockfile's requirements, each with its continuation lines joined."""
    out, line = [], ""
    for raw in text.splitlines():
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        line += " " + raw.strip().rstrip("\\")
        if not raw.rstrip().endswith("\\"):
            if line.strip()[:1].isalnum():
                out.append(line.strip())
            line = ""
    return out


def body(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str:
    from . import licences

    lines = [f"Adds **{m.name}** to the catalog: {m.what or m.kind}.", "",
             f"- **Version:** {m.version}", f"- **Runs with:** runner {m.runner}"
             + (f", made for its contract {m.requires.get('runner', 1)}" if m.runner else ""),
             "- **Licence:** " + "; ".join(f"{c.what} ({c.licence}, {c.tier})" for c in map(licences.component, m.licence))]
    if m.source.get("url"):
        lines.append(f"- **From:** {m.source['url']}")
    for f in m.files:
        lines.append(f"- **File:** {f['path']}, {f.get('bytes', '?')} bytes, sha256 {f.get('sha256', '')[:16]}…")
    if m.runner == "python-env":
        from . import env_runner, envs

        found = env_runner.in_use(m, environ)
        p = found.proof if found else {}
        lines.append(f"- **Environment:** Python {m.env.get('python')}, {envs.size_hint(m, environ)} packages, each by its hash")
        if m.members:
            lines.append(f"- **Bundle reference checks:** {len(p.get('cases', []))} cases for generation "
                         f"{p.get('generation', 'not recorded')} ({p.get('at', '')}); not independent qualification")
            for case in p.get('cases', []):
                lines.append(f"  - {case['member']}: {case['device']}, parameters {json.dumps(case['parameters'], sort_keys=True)}, "
                             f"tolerance {json.dumps(case['tolerance'], sort_keys=True)}")
        else:
            lines.append(f"- **Adapter contract smoke:** found {p.get('found')} on {env_runner.proof_input_says(p)} "
                         f"({p.get('picture')}) in {p.get('seconds')} s ({p.get('at', '')}); not scientific qualification")
            source = p.get("input") or {}
            lines.append(f"- **Input RGB8 SHA-256:** {source.get('pixelSha256', 'not recorded')}")
            if source.get("sourceSha256"):
                lines.append(f"- **Source file SHA-256:** {source['sourceSha256']}")
    if m.credit.get("link"):
        lines.append(f"- **Credit:** {m.credit.get('authors', '')} ({m.credit['link']})")
    lines += ["", "Made with `make catalog-pr`. Static CI checks declarations. Runtime proof and human review "
              "are required separately."]
    return "\n".join(lines)


def _git(*args: str, cwd: Path, environ: Mapping[str, str] | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env=environ, capture_output=True, text=True)
    if done.returncode:
        raise CantPublish(f"git {args[0]} failed: {(done.stderr or done.stdout).strip().splitlines()[-1:]}")
    return done.stdout


def _repository(remote: str, repo: Path, *, push: bool = False) -> str:
    """Resolve configured GitHub identity locally; never query an account or guess the official repository."""
    if remote not in _git("remote", cwd=repo).splitlines():
        raise CantPublish(f"remote {remote!r} is missing: add the official base or your contributor fork with git remote add")
    urls = _git("remote", "get-url", *(('--push',) if push else ()), "--all", remote, cwd=repo).splitlines()
    if len(urls) != 1:
        raise CantPublish(f"remote {remote!r} has multiple destinations: use one explicit GitHub repository")
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?", urls[0])
    if not match:
        raise CantPublish(f"remote {remote!r} needs one GitHub HTTPS or SSH URL (owner/repository); check git remote -v")
    return match[1]


def _destination(repo: Path, remote: str, base_remote: str, base_branch: str, branch: str) -> dict:
    _git("check-ref-format", "--branch", base_branch, cwd=repo)
    _git("check-ref-format", "--branch", branch, cwd=repo)
    base_repo, head_repo = _repository(base_remote, repo), _repository(remote, repo, push=True)
    ref = f"refs/remotes/{base_remote}/{base_branch}"
    try:
        commit = _git("rev-parse", "--verify", f"{ref}^{{commit}}", cwd=repo).strip()
    except CantPublish as exc:
        raise CantPublish(f"base {base_remote}/{base_branch} is not available locally: run git fetch {base_remote} "
                          f"{base_branch}, then prepare again") from exc
    return {"base": {"remote": base_remote, "repository": base_repo, "branch": base_branch, "commit": commit},
            "head": {"remote": remote, "repository": head_repo, "branch": branch}}


def _proof_files(m: catalog.Manifest, environ: Mapping[str, str]) -> tuple[dict, bytes]:
    """Publication needs retained provenance; older installed generations remain usable without this upgrade."""
    from . import env_runner

    p = env_runner.proof(m, environ) or {}
    source = p.get("input") or {}
    generation = env_runner.proof_path(m, environ).name.removesuffix(".proof.json")
    digest = source.get("fileSha256", "")
    permitted = {generation + ".proof-input.npy"}  # legacy receipts remain usable
    if isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
        permitted.add(f"{generation}.{digest}.proof-input.npy")
    expected = source.get("file")
    if (not isinstance(expected, str) or expected not in permitted
            or source.get("evidenceClass") not in ("generated-fixture", "asset-image", "declared-example")
            or not source.get("identity") or not source.get("pixelSha256") or not source.get("shape")):
        raise CantPublish("its proof has no retained input identity: Install again to record the input before preparation")
    try:
        data = (env_runner.proof_path(m, environ).parent / expected).read_bytes()
    except OSError as exc:
        raise CantPublish("its proof input is missing: Install again before preparation") from exc
    if hashlib.sha256(data).hexdigest() != source.get("fileSha256"):
        raise CantPublish("its proof input changed: Install again before preparation")
    return p, data


def _proof_material(m: catalog.Manifest, environ: Mapping[str, str]) -> dict[str, bytes]:
    """Local review evidence only; catalog commits never contain these files."""
    if not m.members:
        proof, data = _proof_files(m, environ)
        portable = {**proof, "input": {**proof["input"], "file": "proof-input.npy"}}
        return {"proof.json": (json.dumps(portable, indent=2) + "\n").encode(), "proof-input.npy": data}
    from . import bundle_review, env_runner

    installed = env_runner.in_use(m, environ)
    if installed is None or not installed.current:
        raise CantPublish("this bundle version has no complete reference check: run its checks before preparation")
    proof = installed.proof
    files = {"proof.json": (json.dumps(proof, indent=2) + "\n").encode()}
    total = len(files['proof.json'])
    try:
        for case in proof['cases']:
            for descriptor in case['artifacts'].values():
                name = 'proof-artifacts/' + descriptor['sha256']
                if name in files:
                    continue
                data = bundle_review.read(installed, descriptor)
                total += len(data)
                if total > bundle_review.MAX_GENERATION:
                    raise ValueError('The retained reference evidence exceeds its review-package limit')
                files[name] = data
    except (ValueError, KeyError, TypeError) as exc:
        raise CantPublish(f"cannot prepare this bundle's reference evidence: {exc}; run its checks again") from exc
    return files


def _prepare(m: catalog.Manifest, repo: Path, destination: dict, output: Path, environ: Mapping[str, str]) -> Path:
    from . import envs

    if output.exists():
        raise CantPublish(f"{output} already exists: choose a new OUTPUT so the reviewed package is preserved")
    evidence = _proof_material(m, environ) if m.runner == "python-env" else {}
    portable = contribution_files(m, environ) if m.members else None
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".catalog-proposal-", dir=output.parent) as temporary:
        staging, work = Path(temporary) / "package", Path(temporary) / "work"
        staging.mkdir(mode=0o700)
        _git("worktree", "add", "-q", "--detach", str(work), destination["base"]["commit"], cwd=repo)
        try:
            rel = Path("catalog/models") / m.id
            dest = work / rel
            if dest.exists():
                raise CantPublish(f"{rel} is in the catalog already: give your entry another id")
            if portable is None:
                envs.copy(m, dest, environ)
            else:
                for name, data in portable.items():
                    path = dest / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            shutil.copytree(dest, staging / rel)
            _git("add", "--", str(rel), cwd=work)
            (staging / "proposal.patch").write_text(_git("diff", "--cached", "--binary", "--no-ext-diff", cwd=work))
        finally:
            _git("worktree", "remove", "--force", str(work), cwd=repo)
        (staging / "body.md").write_text(body(m, environ) + "\n")
        for name, data in evidence.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        files = {str(p.relative_to(staging)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(staging.rglob("*")) if p.is_file()}
        proposal = {"format": 1, "id": m.id, "name": m.name, **destination, "files": files}
        (staging / "proposal.json").write_text(json.dumps(proposal, indent=2) + "\n")
        staging.rename(output)
    return output


def _read_package(package: Path, id_: str) -> tuple[dict, dict[str, bytes]]:
    try:
        proposal = json.loads((package / "proposal.json").read_text())
        if not isinstance(proposal, dict) or proposal.get("format") != 1 or proposal.get("id") != id_.removeprefix("mine/"):
            raise ValueError("wrong entry or package format")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", proposal["id"]):
            raise ValueError("invalid entry id")
        files = proposal["files"]
        if not isinstance(files, dict) or not {"body.md", "proposal.patch"}.issubset(files):
            raise ValueError("missing review files")
        if f"catalog/models/{proposal['id']}/model.toml" not in files:
            raise ValueError("missing catalog manifest")
        payload = {}
        for name, digest in files.items():
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or (package / rel).is_symlink():
                raise ValueError("invalid package path")
            if not (name in ("body.md", "proposal.patch", "proof.json", "proof-input.npy")
                    or re.fullmatch(r"proof-artifacts/[0-9a-f]{64}", name)
                    or name.startswith(f"catalog/models/{proposal['id']}/")):
                raise ValueError("unexpected package file")
            payload[name] = (package / rel).read_bytes()
            if hashlib.sha256(payload[name]).hexdigest() != digest:
                raise ValueError(f"{name} changed after preparation")
        if any(p.is_symlink() for p in package.rglob("*")):
            raise ValueError("package contains a symbolic link")
        actual = {str(p.relative_to(package)) for p in package.rglob("*") if p.is_file()}
        if actual != {*files, "proposal.json"}:
            raise ValueError("files were added or removed after preparation")
        if not isinstance(proposal.get("name"), str):
            raise ValueError("missing entry name")
        for key in ("base", "head"):
            if not isinstance(proposal[key], dict) or not all(isinstance(proposal[key].get(k), str)
                                                            for k in ("remote", "repository", "branch")):
                raise ValueError("missing destination")
        if not isinstance(proposal["base"].get("commit"), str):
            raise ValueError("missing base commit")
        return proposal, payload
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CantPublish(f"can't use the reviewed package: {exc}; prepare a new OUTPUT and review it") from exc


def _certifier(repo: Path) -> tuple[str, str]:
    """No guessed OS identity or silently different author/committer may certify the contribution."""
    try:
        name = _git("config", "--get", "user.name", cwd=repo).removesuffix("\n")
        email = _git("config", "--get", "user.email", cwd=repo).removesuffix("\n")
    except CantPublish as exc:
        raise CantPublish("Git identity is missing: deliberately configure your own user.name and user.email "
                          "before certifying; both become public") from exc
    if (not name.strip() or name != name.strip() or any(ord(c) < 32 or ord(c) == 127 or c in "<>" for c in name)
            or any(ord(c) < 32 or ord(c) == 127 for c in email)
            or not re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", email)):
        raise CantPublish("Git identity is invalid: configure a single-line user.name and valid user.email")
    identity = f"{name} <{email}>"
    for kind in ("AUTHOR", "COMMITTER"):
        effective = _git("var", f"GIT_{kind}_IDENT", cwd=repo).strip().rsplit(" ", 2)[0]
        if effective != identity:
            raise CantPublish("Git identity differs from the configured name/email: remove author/committer "
                              "overrides before certifying your own contribution")
    return name, email


def _submit(id_: str, package: Path, repo: Path) -> str:
    contributor_name, contributor_email = _certifier(repo)  # before any mutation or remote call
    proposal, payload = _read_package(package, id_)
    base, head = proposal["base"], proposal["head"]
    try:
        destination = _destination(repo, head["remote"], base["remote"], base["branch"], head["branch"])
        if destination != {"base": base, "head": head}:
            raise CantPublish("the base or fork changed since preparation: prepare and review a new package")
    except KeyError as exc:
        raise CantPublish("the package destination is incomplete: prepare and review a new package") from exc
    # Only this explicitly requested publishing step reaches a remote. A changed upstream needs review again.
    _git("fetch", "-q", base["remote"], base["branch"], cwd=repo)
    fetched = _git("rev-parse", "FETCH_HEAD", cwd=repo).strip()
    if fetched != base["commit"]:
        raise CantPublish("the official base advanced: prepare and review a new package against the fetched base")
    with tempfile.TemporaryDirectory(prefix="catalog-pr-") as temporary:
        work = Path(temporary) / "work"
        _git("worktree", "add", "-q", "-b", head["branch"], str(work), base["commit"], cwd=repo)
        try:
            rel = Path("catalog/models") / proposal["id"]
            if (work / rel).exists():
                raise CantPublish(f"{rel} already exists in the official base")
            for name, data in payload.items():
                if name.startswith(str(rel) + "/"):
                    dest = work / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
            body_file = Path(temporary) / "body.md"
            body_file.write_bytes(payload["body.md"])
            _git("add", "--", str(rel), cwd=work)
            # Conditional Git configuration can resolve differently in this worktree.
            # The explicit certification belongs to the identity validated in the caller.
            certifier_env = {**os.environ, "GIT_AUTHOR_NAME": contributor_name, "GIT_AUTHOR_EMAIL": contributor_email,
                             "GIT_COMMITTER_NAME": contributor_name, "GIT_COMMITTER_EMAIL": contributor_email}
            _git("commit", "-q", "--signoff", "-m", f"Catalog: {proposal['name']}", cwd=work, environ=certifier_env)
            identity = f"{contributor_name} <{contributor_email}>"
            author, committer, message = _git("show", "-s", "--format=%an <%ae>%x00%cn <%ce>%x00%B",
                                              cwd=work).split("\0", 2)
            signoffs = [line for line in message.splitlines() if line.lower().startswith("signed-off-by:")]
            if author != identity or committer != identity or signoffs != [f"Signed-off-by: {identity}"]:
                raise CantPublish("the commit no longer matches your DCO certification; nothing was pushed. "
                                  "Check Git hooks and prepare a new package before submitting again")
            _git("push", "-q", head["remote"], f"HEAD:refs/heads/{head['branch']}", cwd=work)
            qualified = f"{head['repository'].split('/')[0]}:{head['branch']}"
            done = subprocess.run(["gh", "pr", "create", "--repo", base["repository"], "--base", base["branch"],
                                   "--head", qualified, "--title", f"Catalog: {proposal['name']}",
                                   "--body-file", str(body_file)], cwd=work, capture_output=True, text=True)
            if done.returncode:
                raise CantPublish(f"pushed {head['repository']}:{head['branch']}, but gh couldn't open the pull request: "
                                  f"{done.stderr.strip()[-300:]}; the branch and local package are retained")
            return f"Opened {done.stdout.strip()} (branch {head['branch']}); retained review package {package}"
        finally:
            _git("worktree", "remove", "--force", str(work), cwd=repo)


def propose(id_: str, push: bool = False, remote: str = "origin", environ: Mapping[str, str] = os.environ,
            repo: Path | None = None, *, base_remote: str | None = None, base_branch: str = BASE_BRANCH,
            output: Path | None = None, package: Path | None = None, signoff: bool = False) -> str:
    """Prepare exact files and proof locally. Publishing is a separate explicit step over that reviewed package."""
    repo = repo or REPO
    if push:
        if package is None:
            raise CantPublish("prepare and review a local package first; pass PACKAGE=<folder> with PUSH=1")
        if not signoff:
            raise CantPublish(certification())
        return _submit(id_, package.resolve(), repo)
    if package is not None:
        raise CantPublish("PACKAGE selects an already reviewed package for PUSH=1; use OUTPUT to prepare a new one")
    m = _entry(id_, environ)
    why = problems(m, environ)
    if why:
        raise CantPublish(f"{m.name} can't be published yet:\n" + "\n".join(f"  - {w}" for w in why))
    branch = f"catalog/{m.id}-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    destination = _destination(repo, remote, base_remote or remote, base_branch, branch)
    output = (output or catalog.catalog_root(environ) / "contributions" / branch.removeprefix("catalog/")).resolve()
    _prepare(m, repo, destination, output, environ)
    return (f"Prepared {output} for local review: catalog files, proposal.patch, body.md and retained proof.\n"
            f"Base {destination['base']['repository']}:{base_branch} at {destination['base']['commit']}; "
            f"head {destination['head']['repository']}:{branch}.\n"
            "Nothing has left this Mac. Review the package, its credit and contribution terms before "
            f"make catalog-pr ID={m.id} PACKAGE=\"{output}\" PUSH=1 SIGNOFF=1.\n\n" + certification())


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Publish an entry of yours to the catalog, as a pull request.")
    parser.add_argument("id")
    parser.add_argument("--push", action="store_true", help="push the branch and open the pull request with gh")
    parser.add_argument("--signoff", action="store_true", help="certify DCO 1.1 for your contribution; "
                        "your configured Git name/email become public (read docs/contributing/DCO.txt)")
    parser.add_argument("--remote", default="origin", help="contributor head/push remote for preparation")
    parser.add_argument("--base-remote", help="official base remote (default: --remote)")
    parser.add_argument("--base", default=BASE_BRANCH, help="official base branch already fetched locally")
    parser.add_argument("--output", type=Path, help="new local review-package folder")
    parser.add_argument("--package", type=Path, help="previously prepared and reviewed package to publish")
    args = parser.parse_args(argv)
    try:
        print(propose(args.id, args.push, args.remote, base_remote=args.base_remote, base_branch=args.base,
                      output=args.output, package=args.package, signoff=args.signoff))
    except CantPublish as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
