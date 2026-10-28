"""Share with everyone with no Git and no `gh` (docs/design/2026-10-13-capabilities-for-everyone.md, C5; Codex's cross-check
at abf4afc7): the reviewed catalog's pull request made through GitHub's API with the user's own sign-in (github_signin.py).

publish.py stays the route for a checkout with Git and `gh`; this one needs only the sign-in. Its rules are publish.py's:
- **Prepare** (`prepare`) writes, on this Mac, the exact outgoing package: the entry's catalog files (only
  `catalog/models/<id>/`: proof inputs stay here), the pull request's title and body (publish.body), the commit message
  with its DCO sign-off line, the identity it will be signed as (the GitHub account's name and its verified or noreply
  email, read now), the official base (its repository, branch and commit) and every file's sha256. Nothing leaves the Mac.
  The base branch is `dev` (`publish.BASE_BRANCH`): HookeUI takes pull requests into dev only. A package prepared for
  `main` by an older app is prepared again before anything is sent; a review it already opened is still followed.
- **Submit** (`submit`) sends exactly that package and only with the user's own sign-off (`signoff=True`, the
  certification read): every file is checked against its sha256, the identity and the base are read again and must be
  what was reviewed before any new write. An exact existing review is recovered read-only even if the base advanced.
  Otherwise a changed base requires preparation and review again. Then: a fork of the catalog (made once), each file a
  blob, one tree on the base's, one signed-off commit by that identity, its branch, and the pull request.
- **Status** (`status`): recover a lost submission response and report its state (open, merged, closed), or the retained
  branch's next step. Recovery and submission serialize their local progress updates.

`http` is GitHub's REST API (`_Api`, with the sign-in's token; a test passes a controlled stand-in). The token never
enters the package, a log or a reply.
"""
from __future__ import annotations

import base64
from .file_locks import fcntl  # the standard module, or Windows' refusing stand-in
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Mapping
from urllib.parse import urlencode

from gpu_worker import manifests as catalog

from . import bounded_files
from .publish import CantPublish, body, certification, problems

API = "https://api.github.com"
OFFICIAL = "wpulier/HookeUI"  # the reviewed catalog's repository (channels.REVIEWED_URL)
FORK_WAIT_S = 60.0


class _Api:
    """GitHub's REST API with the sign-in's token (never logged)."""

    def __init__(self, token: str) -> None:
        import httpx

        self.client = httpx.Client(base_url=API, timeout=30, headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ObservationUI"})

    def call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict | list]:
        r = self.client.request(method, path, json=payload)
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {}
        return r.status_code, data


def _api(token: str | None = None):
    from . import github_signin

    token = token or github_signin.token()
    if not token:
        raise CantPublish("sign in to GitHub first (Share with everyone's Sign in)")
    return _Api(token)


def _ok(status: int, data, what: str):
    if status >= 400:
        message = data.get("message") if isinstance(data, dict) else ""
        raise CantPublish(f"GitHub refused {what} ({status}{': ' + str(message)[:160] if message else ''})")
    return data


def identity(http) -> dict:
    """The signed-in account as the sign-off names it: its name and its verified primary email, else GitHub's noreply."""
    user = _ok(*http.call("GET", "/user"), "reading your account")
    name = str(user.get("name") or "").strip()
    if not name:
        raise CantPublish("your GitHub account has no name: add it on GitHub (Settings, Public profile), since the DCO "
                          "sign-off certifies with your name")
    emails = http.call("GET", "/user/emails")
    primary = next((e.get("email") for e in (emails[1] if emails[0] < 400 and isinstance(emails[1], list) else [])
                    if e.get("primary") and e.get("verified") and not str(e.get("email", "")).endswith("@users.noreply.github.com")
                    and e.get("visibility") == "public"), None)
    email = primary or f"{user.get('id')}+{user.get('login')}@users.noreply.github.com"
    return {"login": str(user.get("login")), "name": name, "email": email}


def _official_branch() -> str:
    from . import publish

    return publish.BASE_BRANCH


def _base(http, repository: str = OFFICIAL, branch: str | None = None) -> dict:
    branch = branch or _official_branch()
    ref = _ok(*http.call("GET", f"/repos/{repository}/git/ref/heads/{branch}"), "reading the catalog's newest commit")
    return {"repository": repository, "branch": branch, "commit": ref["object"]["sha"]}


def _files(m: catalog.Manifest, environ: Mapping[str, str]) -> dict[str, bytes]:
    """The exact portable catalog content; retained proof input is never uploaded."""
    from .publish import contribution_files

    return {f"catalog/models/{m.id}/{rel}": data for rel, data in contribution_files(m, environ).items()}


def prepare(key: str, environ: Mapping[str, str] = os.environ, http=None, *, destination: str = "review") -> dict:
    """The exact outgoing package, on this Mac (see the module's docstring): {"package", "review"}."""
    if destination not in ("review", "community"):
        raise CantPublish("choose the official review or your community catalog")
    m = catalog.mine(environ).get(f"mine/{key.removeprefix('mine/')}")
    if m is None:
        raise CantPublish(f"there is no entry of yours called {key}")
    why = problems(m, environ)
    if why:
        raise CantPublish(f"{m.name} can't be published yet:\n" + "\n".join(f"  - {w}" for w in why))
    from .publish import _proof_material
    evidence = _proof_material(m, environ) if m.members else {}
    http = http or _api()
    who = identity(http)
    base = _base(http)
    status, _ = http.call("GET", f"/repos/{base['repository']}/contents/catalog/models/{m.id}?ref={base['commit']}")
    if status != 404 and destination == "review":
        raise CantPublish(f"catalog/models/{m.id} is in the catalog already: give your entry another id")
    files = _files(m, environ)
    branch = f"catalog/{m.id}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    message = f"Catalog: {m.name}\n\nSigned-off-by: {who['name']} <{who['email']}>"
    package = catalog.catalog_root(environ).resolve() / "contributions" / uuid.uuid4().hex
    package.mkdir(parents=True)
    for path, data in files.items():
        (package / path).parent.mkdir(parents=True, exist_ok=True)
        (package / path).write_bytes(data)
    for path, data in evidence.items():
        (package / path).parent.mkdir(parents=True, exist_ok=True)
        (package / path).write_bytes(data)
    (package / "body.md").write_text(body(m, environ) + "\n")
    from . import envs
    proposal = {"format": 1, "route": "api", "destination": destination, "entryDigest": envs.entry_digest(m, environ),
                "id": m.id, "name": m.name, "base": base, "branch": branch,
                "title": f"Catalog: {m.name}", "message": message, "identity": who,
                "files": {path: hashlib.sha256(data).hexdigest() for path, data in files.items()},
                "body": hashlib.sha256((package / "body.md").read_bytes()).hexdigest()}
    if evidence:
        proposal['evidence'] = {path: hashlib.sha256(data).hexdigest() for path, data in evidence.items()}
    proposal_bytes = (json.dumps(proposal, indent=2) + "\n").encode()
    (package / "proposal.json").write_bytes(proposal_bytes)
    registry = catalog.catalog_root(environ).resolve() / ".publication-packages"
    registry.mkdir(parents=True, exist_ok=True, mode=0o700)
    (registry / (package.name + ".json")).write_text(json.dumps({
        "package": str(package), "model": m.key,
        "proposal": hashlib.sha256(proposal_bytes).hexdigest()}))
    review = (f"Prepared {package} for you to read: {len(files)} files under catalog/models/{m.id}/, the pull request "
              f"\"{proposal['title']}\" to {base['repository']} (its {base['branch']} at {base['commit'][:7]}), signed off "
              f"as {who['name']} <{who['email']}> (public in the commit). Nothing has left this Mac.\n\n" + certification())
    if destination == "community":
        review = (f"Prepared {package} for your public community catalog {who['login']}/ObservationUI-community: "
                  f"{len(files)} files under catalog/models/{m.id}/. Only these files form the published snapshot; "
                  f"no weights or proof input. Signed off as {who['name']} <{who['email']}> (public). "
                  "Publishing returns a pinned community link immediately, without an official review. "
                  "Recipients must allow its code and turn it on. Nothing has left this Mac.\n\n" + certification())
    if evidence:
        review += ("\nThe reference inputs, expected results and actual results are retained here for review. "
                   "They are not in the outgoing catalog files. These checks do not establish independent qualification.\n")
    return {"package": str(package), "review": review, "identity": who, "base": base, "files": sorted(files)}


def _binding(package: Path, environ: Mapping[str, str] = os.environ) -> dict:
    """Local preparation authority lives outside the editable review package."""
    root = catalog.catalog_root(environ).resolve()
    if (not re.fullmatch(r"[0-9a-f]{32}", package.name)
            or package.parent != root / "contributions" or package.is_symlink()
            or package.parent.is_symlink()):
        raise CantPublish("that package was not prepared here: prepare and review it again")
    try:
        binding = json.loads(_read_file(root / ".publication-packages", package.name + ".json"))
        if binding["package"] != str(package):
            raise ValueError("package moved")
        return binding
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CantPublish("that package was not prepared here: prepare and review it again") from exc


def prepared(package: str | Path) -> bool:
    """Whether this is a locally registered API package, without reading a caller-selected proposal."""
    try:
        _binding(Path(package))
        return True
    except CantPublish:
        return False


def _read_file(folder: Path, name: str, maximum: int = 10 * 1024 * 1024) -> bytes:
    """Read a bounded regular file through directories held open, never following links."""
    parts = PurePosixPath(name).parts
    if (not parts or name != "/".join(parts) or any(p in (".", "..") for p in parts)
            or name.startswith("/") or "\\" in name):
        raise ValueError("unsafe package path")
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return bounded_files.read_at(directory, parts[-1], maximum)
    finally:
        os.close(directory)


def _read(package: Path, model: str | None = None, environ: Mapping[str, str] = os.environ) -> tuple[dict, dict[str, bytes], str]:
    try:
        binding = _binding(package, environ)
        if model is not None and "mine/" + model.removeprefix("mine/") != binding["model"]:
            raise ValueError("the package belongs to another model")
        raw = _read_file(package, "proposal.json", 512 * 1024)
        if hashlib.sha256(raw).hexdigest() != binding["proposal"]:
            raise ValueError("the proposal changed since you reviewed it")
        proposal = json.loads(raw)
        if (proposal.get("format") != 1 or proposal.get("route") != "api"
                or proposal["base"]["repository"] != OFFICIAL
                # main: prepared by an app before 0.5.2; readable so a review it opened is still followed
                or proposal["base"]["branch"] not in (_official_branch(), "main")
                or "mine/" + proposal["id"] != binding["model"]):
            raise ValueError("the package's identity changed")
        files = {}
        for path, digest in proposal["files"].items():
            if not path.startswith(f"catalog/models/{proposal['id']}/"):
                raise ValueError("a file is outside the prepared entry")
            data = _read_file(package, path)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"{path} changed since you reviewed it")
            files[path] = data
        # Retained scientific evidence is review material, not outgoing catalog content.
        from . import bundle_review
        evidence = proposal.get('evidence', {})
        if not isinstance(evidence, dict) or len(evidence) > bundle_review.MAX_FILES:
            raise ValueError('invalid retained evidence list')
        total = 0
        for path, digest in evidence.items():
            if path != 'proof.json' and not re.fullmatch(r'proof-artifacts/[0-9a-f]{64}', path):
                raise ValueError('invalid retained evidence path')
            data = _read_file(package, path, bundle_review.MAX_FILE)
            total += len(data)
            if total > bundle_review.MAX_GENERATION:
                raise ValueError('retained evidence exceeds its allowance')
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f'{path} changed since you reviewed it')
        expected = {*files, *evidence, "proposal.json", "body.md", "submitted.json"}
        for file in package.rglob("*"):
            if file.is_symlink() or (not file.is_dir() and file.relative_to(package).as_posix() not in expected):
                raise ValueError("the package's file list changed")
        data = _read_file(package, "body.md")
        if hashlib.sha256(data).hexdigest() != proposal["body"]:
            raise ValueError("the pull request text changed since you reviewed it")
        return proposal, files, data.decode()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CantPublish(f"can't use that package: {exc}; prepare and review it again") from exc


def submit(package: str | Path, signoff: bool, http=None, wait=time.sleep, *, model: str | None = None) -> dict:
    """Serialize retries for one approved package, retaining progress across lost responses and restarts."""
    package = Path(package)
    with _package_lock(package):
        return _submit(package, signoff, http, wait, model=model)


@contextmanager
def _package_lock(package: Path):
    """Status recovery and submission update the same retained progress serially."""
    _binding(package)
    lock = package.parent.parent / ".publication-packages" / (package.name + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        yield


def _save_progress(package: Path, progress: dict) -> None:
    binding = _binding(package)
    binding["progress"] = progress
    target = package.parent.parent / ".publication-packages" / (package.name + ".json")
    temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(binding))
    temporary.replace(target)


def _checked_identity(http, proposal: dict) -> dict:
    who = identity(http)
    if who != proposal["identity"]:
        raise CantPublish("you are signed in as someone else than the package names: prepare it again")
    return who


def _find_review(http, proposal: dict, text: str, commit: str) -> dict | None:
    base = proposal["base"]
    query = urlencode({"state": "all", "head": f"{proposal['identity']['login']}:{proposal['branch']}", "base": base["branch"]})
    existing = _ok(*http.call("GET", f"/repos/{base['repository']}/pulls?{query}"), "looking for its review")
    if not isinstance(existing, list) or len(existing) > 1:
        raise CantPublish("its review is ambiguous: inspect the prepared branch before trying again")
    if not existing:
        return None
    return _checked_review(existing[0], proposal, text, commit)


def _checked_review(pull: dict, proposal: dict, text: str, commit: str) -> dict:
    """Recovery and retained-number status accept the same exact approved review."""
    if (pull.get("head", {}).get("sha") != commit or pull.get("title") != proposal["title"]
            or pull.get("body") != text or pull.get("base", {}).get("ref") != proposal["base"]["branch"]):
        raise CantPublish("its existing review differs from this package: review it on GitHub")
    return pull


def _remote_progress(http, proposal: dict, text: str, progress: dict) -> tuple[dict | None, bool]:
    """Recover only what already exists, before applying any gate for fresh writes."""
    commit = progress.get("commit")
    if not commit:
        return None, False
    pull = None if proposal.get("destination") == "community" else _find_review(http, proposal, text, commit)
    if pull is not None:
        return pull, False  # a merged review can remain after its branch was deleted
    # Older progress used GitHub's default fork name without retaining it.
    fork = progress.get("fork") or f"{proposal['identity']['login']}/{proposal['base']['repository'].split('/')[-1]}"
    code, ref = http.call("GET", f"/repos/{fork}/git/ref/heads/{proposal['branch']}")
    if code == 404:
        return None, False
    _ok(code, ref, "reading its branch")
    if ref.get("object", {}).get("sha") != commit:
        raise CantPublish("its branch no longer names the approved commit: prepare and review it again")
    return None, True


def _remember_community(package: Path, proposal: dict, progress: dict) -> dict:
    repository, commit = progress["fork"], progress["commit"]
    acquire = {"url": f"https://github.com/{repository}", "commit": commit, "entry": proposal["id"]}
    sent = {"state": "community", "url": f"{acquire['url']}/tree/{commit}", "commit": commit, "acquire": acquire,
            "says": f"Published to your community catalog: {acquire['url']}/tree/{commit}. "
                    "It is not an official reviewed entry. Recipients allow its code and turn it on."}
    _save_progress(package, {**progress, "sent": sent})
    (package / "submitted.json").write_text(json.dumps(sent, indent=2))
    # Bind the acquisition reference to the exact producer contents, not a mutable model id.
    destination = package.parent.parent / "community-publications"
    destination.mkdir(exist_ok=True)
    target = destination / (proposal["entryDigest"] + ".json")
    temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(acquire))
    temporary.replace(target)
    return sent


def community_reference(m: catalog.Manifest) -> dict | None:
    from . import envs
    try:
        path = catalog.catalog_root() / "community-publications" / (envs.entry_digest(m) + ".json")
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _remember_review(package: Path, progress: dict, pull: dict) -> dict:
    sent = {"url": pull.get("html_url"), "number": pull.get("number"), "commit": progress["commit"], "at": progress["at"],
            "state": _review_state(pull)}
    _save_progress(package, {**progress, "sent": sent})
    (package / "submitted.json").write_text(json.dumps(sent, indent=2) + "\n")
    return sent


def _sent_result(sent: dict) -> dict:
    if sent["state"] != "open":
        return {"says": f"{sent['state'].capitalize()}: {sent['url']}", **sent}
    return {"says": f"Sent for review: {sent['url']}. Once a reviewer merges it, everyone sees it in New for you.", **sent}


def _review_state(pull: dict) -> str:
    return "merged" if pull.get("merged") or pull.get("merged_at") else str(pull.get("state") or "open")


def _submit(package: Path, signoff: bool, http=None, wait=time.sleep, *, model: str | None = None) -> dict:
    """Exactly the reviewed package, sent only with the user's sign-off (see the module's docstring)."""
    if signoff is not True:
        raise CantPublish(certification())
    package = Path(package)
    if not model:
        raise CantPublish("name the model whose package you reviewed")
    proposal, files, text = _read(package, model)
    http = http or _api()
    who = _checked_identity(http, proposal)
    progress = _binding(package).get("progress", {})
    pull, branch_exists = _remote_progress(http, proposal, text, progress)
    if proposal.get("destination") == "community" and branch_exists:
        return _remember_community(package, proposal, progress)
    if pull is not None:
        return _sent_result(_remember_review(package, progress, pull))
    if proposal["base"]["branch"] != _official_branch():
        raise CantPublish(f"this package was prepared for the catalog's {proposal['base']['branch']} branch, and reviews "
                          f"now go to {_official_branch()}: prepare and review it again")
    base = _base(http, proposal["base"]["repository"], proposal["base"]["branch"])
    if base["commit"] != proposal["base"]["commit"]:
        raise CantPublish("the catalog moved on since you prepared it: prepare and review it again")
    repo = proposal["base"]["repository"]
    community = proposal.get("destination") == "community"
    if community:
        fork_name = f"{who['login']}/ObservationUI-community"
        code, repository = http.call("GET", f"/repos/{fork_name}")
        if code == 404:
            repository = _ok(*http.call("POST", "/user/repos", {"name": "ObservationUI-community", "private": False,
                                                               "auto_init": True}), "making your public community catalog")
        else:
            _ok(code, repository, "reading your community catalog")
        if repository.get("full_name") != fork_name or repository.get("private") is True:
            raise CantPublish("the community repository is not the public publisher-owned destination you reviewed")
    else:
        fork = _ok(*http.call("POST", f"/repos/{repo}/forks", {"default_branch_only": True}), "making your copy of the catalog")
        fork_name = fork["full_name"]
        deadline = time.monotonic() + FORK_WAIT_S
        while http.call("GET", f"/repos/{fork_name}/git/commits/{base['commit']}")[0] != 200:
            if time.monotonic() > deadline:
                raise CantPublish("your copy of the catalog isn't ready on GitHub yet: send it again in a minute")
            code, _ = http.call("POST", f"/repos/{fork_name}/merge-upstream", {"branch": base["branch"]})
            if code in (404, 422):  # a copy made before dev was the catalog's default has no dev: make it at the base
                http.call("POST", f"/repos/{fork_name}/git/refs", {"ref": f"refs/heads/{base['branch']}", "sha": base["commit"]})
            wait(2.0)
    commit = progress.get("commit")
    stamp = progress.get("at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    progress = {**progress, "at": stamp, "fork": fork_name}
    if commit is None:
        tree_of = None if community else _ok(*http.call("GET", f"/repos/{fork_name}/git/commits/{base['commit']}"), "reading the base")["tree"]["sha"]
        entries = []
        for path, data in files.items():
            blob = _ok(*http.call("POST", f"/repos/{fork_name}/git/blobs",
                                  {"content": base64.b64encode(data).decode(), "encoding": "base64"}), f"sending {path}")
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = _ok(*http.call("POST", f"/repos/{fork_name}/git/trees",
                             {"tree": entries, **({} if proposal.get("destination") == "community" else {"base_tree": tree_of})}), "the files")
        # Fix the timestamp before commit creation; Git objects are content addressed, so a lost response is retryable.
        _save_progress(package, progress)
        person = {"name": who["name"], "email": who["email"], "date": stamp}
        commit = _ok(*http.call("POST", f"/repos/{fork_name}/git/commits", {
            "message": proposal["message"], "tree": tree["sha"],
            "parents": [] if proposal.get("destination") == "community" else [base["commit"]], "author": person,
            "committer": person}), "the signed-off commit")["sha"]
        progress = {**progress, "commit": commit}
        _save_progress(package, progress)
    reference = f"/repos/{fork_name}/git/ref/heads/{proposal['branch']}"
    status_code, reference_data = http.call("GET", reference)
    if status_code == 404:
        status_code, reference_data = http.call("POST", f"/repos/{fork_name}/git/refs",
                                               {"ref": f"refs/heads/{proposal['branch']}", "sha": commit})
        if status_code == 422:  # another attempt's response may have been lost
            status_code, reference_data = http.call("GET", reference)
        _ok(status_code, reference_data, "its branch")
        if reference_data.get("object", {}).get("sha", commit) != commit:
            raise CantPublish("its branch no longer names the approved commit: prepare and review it again")
    else:
        _ok(status_code, reference_data, "reading its branch")
        if reference_data.get("object", {}).get("sha") != commit:
            raise CantPublish("its branch no longer names the approved commit: prepare and review it again")
    if proposal.get("destination") == "community":
        return _remember_community(package, proposal, progress)
    pull = _find_review(http, proposal, text, commit)
    if pull is None:
        pull = _ok(*http.call("POST", f"/repos/{repo}/pulls", {
            "title": proposal["title"], "head": f"{who['login']}:{proposal['branch']}", "base": base["branch"], "body": text,
            "maintainer_can_modify": True}), "opening the pull request")
    return _sent_result(_remember_review(package, progress, pull))


def status(package: str | Path, http=None) -> dict:
    """Where its pull request stands now: open, merged or closed (kept beside the package)."""
    package = Path(package)
    with _package_lock(package):
        return _status(package, http)


def _status(package: Path, http=None) -> dict:
    proposal, _files, text = _read(package)
    progress = _binding(package).get("progress", {})
    if not progress.get("commit"):
        return {"state": "not sent"}
    http = http or _api()
    _checked_identity(http, proposal)
    if proposal.get("destination") == "community":
        _pull, exists = _remote_progress(http, proposal, text, progress)
        if exists:
            return _remember_community(package, proposal, progress)
        return {"state": "not sent", "says": "The pinned community branch is not available; publish again to finish it"}
    sent = progress.get("sent")
    if sent:
        pull = _ok(*http.call("GET", f"/repos/{proposal['base']['repository']}/pulls/{sent['number']}"), "reading its review")
        _checked_review(pull, proposal, text, progress["commit"])
    else:
        pull, branch_exists = _remote_progress(http, proposal, text, progress)
        if pull is None:
            if not branch_exists:
                return {"state": "not sent", "says": "The approved commit is retained; send again to finish its branch and review"}
            base = _base(http, proposal["base"]["repository"], proposal["base"]["branch"])
            if base["commit"] != proposal["base"]["commit"] or proposal["base"]["branch"] != _official_branch():
                return {"state": "needs review", "says": "Your approved branch exists, but the catalog moved on: prepare and review again before sending"}
            return {"state": "branch ready", "says": "Your approved branch exists; send again to open its review"}
        sent = _remember_review(package, progress, pull)
    state = _review_state(pull)
    sent = {**sent, "state": state, "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (package / "submitted.json").write_text(json.dumps(sent, indent=2) + "\n")
    return {"state": state, "url": sent.get("url"),
            "says": {"merged": "Merged: it is in the reviewed catalog, and everyone sees it in New for you",
                     "closed": "Closed without merging: its page says why", "open": "Waiting for review"}.get(state, state)}
