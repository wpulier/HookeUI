"""Add a capability from a GitHub link (orchestrator/link_recognise.py `from_github`; ticket 62 row 7, F043-F048 of
docs/research/2026-10-19-first-use-failure-map.md): what the link points to is told apart; a bioimage.io model in the
repository's files or in a release's zip becomes an entry run by the reviewed bioimageio template, every file pinned (the
commit, the zip's sha256, a Git LFS object by the hash its pointer states); anything else is said precisely, with what it
holds and what it lacks. A stand-in GitHub (its API, raw files, release zips read by byte ranges); no network."""
import hashlib
import io
import json
import zipfile

import numpy as np
import pytest
import yaml

from gpu_worker import manifests as catalog
from orchestrator import link_recognise, mine
from tests.test_link_recognise import mine_home, rdf, template  # noqa: F401 - the fixture is used by name

API = link_recognise.GITHUB_API
SHA = "0123456789abcdef" * 2 + "01234567"
RAW = f"https://raw.githubusercontent.com/maya/diatom-tools/{SHA}"
ASSET = "https://github.com/maya/diatom-tools/releases/download/models-v1/diatom_counter.zip"


class R:
    def __init__(self, status, content=b"", record=None, headers=None):
        content = json.dumps(record).encode() if record is not None else content  # as a server sends it
        self.status_code, self.content, self._record, self.headers = status, content, record, headers or {}

    def json(self):
        return self._record


class GitHub:
    """httpx as GitHub answers it: its API by path, raw files and release files by address; Range honoured."""

    def __init__(self, api: dict, files: dict[str, bytes]):
        self.api, self.files, self.asked = api, files, []

    def get(self, url, headers=None, **kw):
        headers = headers or {}
        self.asked.append((url, headers.get("Range", "")))
        if url.startswith(API):
            path = url[len(API):]
            return R(200, record=self.api[path]) if path in self.api else R(404)
        if url not in self.files:
            return R(404)
        data = self.files[url]
        if headers.get("Range"):
            a, b = (int(x) for x in headers["Range"].removeprefix("bytes=").split("-"))
            return R(206, data[a:b + 1])
        return R(200, data)


class EmptyGitHub(GitHub):
    """Any public repository, with nothing in it and no releases: a branch is at SHA, a commit is itself. For tests of
    what is done with a GitHub link that holds no model (tests/test_mine.py), so none of them reaches GitHub. Any other
    address is httpx's (as a test may have stood it in)."""

    def __init__(self):
        super().__init__({}, {})

    def head(self, url, **kw):
        import httpx

        return httpx.head(url, **kw)

    def get(self, url, headers=None, **kw):
        import re

        import httpx

        if not url.startswith(API):
            return httpx.get(url, **({"headers": headers} if headers else {}), **kw)
        path = url[len(API):]
        self.asked.append((url, ""))
        if re.fullmatch(r"/repos/[^/]+/[^/]+", path):
            return R(200, record={"default_branch": "main", "license": None})
        commit = re.fullmatch(r"/repos/[^/]+/[^/]+/commits/(.+)", path)
        if commit:
            return R(200, record={"sha": commit.group(1) if re.fullmatch(r"[0-9a-f]{40}", commit.group(1)) else SHA})
        if "/git/trees/" in path:
            return R(200, record={"tree": [], "truncated": False})
        if path.endswith("/releases?per_page=20"):
            return R(200, record=[])
        return R(404)


def repo_api(tree: list[str], sizes: dict[str, int] | None = None, releases: list | None = None, licence="MIT") -> dict:
    sizes = sizes or {}
    return {
        "/repos/maya/diatom-tools": {"default_branch": "main", "license": {"spdx_id": licence} if licence else None},
        "/repos/maya/diatom-tools/commits/main": {"sha": SHA},
        f"/repos/maya/diatom-tools/commits/{SHA}": {"sha": SHA},
        f"/repos/maya/diatom-tools/git/trees/{SHA}?recursive=1": {
            "tree": [{"path": p, "type": "blob", "size": sizes.get(p, 100)} for p in tree], "truncated": False},
        "/repos/maya/diatom-tools/releases?per_page=20": releases or [],
        **{f"/repos/maya/diatom-tools/releases/tags/{r['tag_name']}": r for r in releases or []},
    }


CONTENT = {"weights.pt": b"w" * 3000, "test_input_0.npy": b"a" * 2000, "test_output_0.npy": b"b" * 2000}


def description(**over) -> dict:
    d = rdf(**over)
    if "torchscript" in d["weights"]:
        d["weights"]["torchscript"]["sha256"] = hashlib.sha256(CONTENT["weights.pt"]).hexdigest()
    d["inputs"][0]["test_tensor"]["sha256"] = hashlib.sha256(CONTENT["test_input_0.npy"]).hexdigest()
    d["outputs"][0]["test_tensor"]["sha256"] = hashlib.sha256(CONTENT["test_output_0.npy"]).hexdigest()
    return d


def package(d: dict, folder: str = "diatom_counter/") -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(folder + "rdf.yaml", yaml.safe_dump(d))
        for name, data in CONTENT.items():
            z.writestr(folder + name, data)
    return out.getvalue()


def released(zipped: bytes, digest: bool = True) -> list:
    asset = {"name": "diatom_counter.zip", "size": len(zipped), "browser_download_url": ASSET}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(zipped).hexdigest()
    return [{"tag_name": "models-v1", "assets": [asset, {"name": "notes.txt", "size": 3}]}]


@pytest.mark.parametrize("link, kind, more", [
    ("https://github.com/maya/diatom-tools", "repository", {}),
    ("https://github.com/maya/diatom-tools.git", "repository", {}),
    (f"https://github.com/maya/diatom-tools/commit/{SHA}", "repository", {"ref": SHA}),
    ("https://github.com/maya/diatom-tools/tree/main", "repository", {"ref": "main"}),
    ("https://github.com/maya/diatom-tools/tree/main/models/counter", "folder", {"path": "models/counter"}),
    ("https://github.com/maya/diatom-tools/blob/main/README.md", "file", {"path": "README.md"}),
    (f"{RAW}/src/count.py", "file", {"ref": SHA, "path": "src/count.py"}),
    ("https://github.com/maya/diatom-tools/releases", "release", {"tag": ""}),
    ("https://github.com/maya/diatom-tools/releases/latest", "release", {"tag": "latest"}),
    ("https://github.com/maya/diatom-tools/releases/tag/models-v1", "release", {"tag": "models-v1"}),
    (ASSET, "asset", {"tag": "models-v1", "asset": "diatom_counter.zip"}),
    ("https://github.com/maya/diatom-tools/issues/12", "page", {"page": "issues"}),
    ("https://github.com/maya/diatom-tools/pull/3", "page", {"page": "pull"}),
    ("https://github.com/maya/diatom-tools/wiki", "page", {"page": "wiki"}),
])
def test_what_a_github_link_points_to_is_told_apart(link, kind, more):
    """F043: a README, a branch, an issue, a release and a repository are not interchangeable installable packages."""
    gh = link_recognise.github(link)
    assert gh["kind"] == kind and (gh["owner"], gh["repo"]) == ("maya", "diatom-tools")
    assert {k: gh[k] for k in more} == more


def test_an_issue_or_a_single_file_is_said_without_reading_anything():
    issue = link_recognise.recognise("https://github.com/maya/diatom-tools/issues/12", http=object())
    assert issue.kind == "unsupported" and "an issue of maya/diatom-tools" in issue.why
    assert "https://github.com/maya/diatom-tools itself" in issue.why
    readme = link_recognise.recognise("https://github.com/maya/diatom-tools/blob/main/README.md", http=object())
    assert readme.kind == "needs-code" and "a single file of maya/diatom-tools (README.md)" in readme.why


def test_a_repository_with_no_model_is_said_with_what_it_holds_and_lacks_at_its_pinned_commit():
    """F044, F046, F047: what a library, a plugin or notebooks are is said; the branch is pinned to its commit."""
    tree = ["setup.py", "README.md", "src/diatoms/__init__.py", "src/diatoms/napari.yaml", "demo.ipynb",
            "docs/tour.ipynb", ".gitmodules", "data/model_final.pth"]
    http = GitHub(repo_api(tree, licence="BSD-3-Clause"), {})
    found = link_recognise.recognise("https://github.com/maya/diatom-tools", http=http)
    assert found.kind == "needs-code" and found.name == "diatom-tools"
    assert found.source == {"url": "https://github.com/maya/diatom-tools", "git": "https://github.com/maya/diatom-tools",
                            "rev": SHA}, "the branch it was added at is pinned to its commit (F046)"
    for words in (f"at commit {SHA[:7]}", "a Python package (setup.py)", "a napari plugin", "src/diatoms/napari.yaml",
                  "2 notebooks", "model_final.pth", "no description of how to run them", "Git submodules",
                  "its licence BSD-3-Clause", "no bioimage.io model description", "an adapter has to be written"):
        assert words in found.why, (words, found.why)


def test_a_model_in_a_releases_zip_is_run_by_the_reviewed_template_pinned_by_the_zips_hash(mine_home, monkeypatch):
    zipped = package(description())
    http = GitHub(repo_api(["README.md", "setup.py"], releases=released(zipped)), {ASSET: zipped})
    found = link_recognise.recognise("https://github.com/maya/diatom-tools", http=http)
    assert found.kind == "template" and found.template == "bioimageio", found.why
    assert "release models-v1" in found.why and "pinned by its sha256" in found.why
    whole = {"sha256": hashlib.sha256(zipped).hexdigest(), "bytes": len(zipped)}
    assert {f["path"]: f["archive"] for f in found.files} == {
        "rdf.yaml": {**whole, "member": "diatom_counter/rdf.yaml"},
        "weights.pt": {**whole, "member": "diatom_counter/weights.pt"},
        "test_input_0.npy": {**whole, "member": "diatom_counter/test_input_0.npy"},
        "test_output_0.npy": {**whole, "member": "diatom_counter/test_output_0.npy"}}
    assert all(f["url"] == ASSET for f in found.files)
    assert all(rng for url, rng in http.asked if url == ASSET), "the zip is read by ranges, never downloaded whole"
    assert found.link == "https://github.com/maya/diatom-tools/releases/tag/models-v1"
    assert found.version == "github maya/diatom-tools release models-v1: diatom_counter.zip"
    # Written as an entry: the template's files byte for byte, the zip's members pinned (manifests.ARCHIVE_KEYS).
    monkeypatch.setattr(link_recognise, "_http", lambda: http)
    added = mine.add(ASSET)
    m = catalog.mine()[added["model"]]
    assert added["kind"] == "template:bioimageio" and link_recognise.reviewed_template(m) == "bioimageio"
    assert {f["path"] for f in m.files} == set(CONTENT) | {"rdf.yaml"}
    assert "from its bioimage.io package" in added["says"]
    assert not mine.state(m)["on"], "added is not on: it installs and runs only when the user turns it on (ADR 0021)"
    assert not (mine_home / "catalog" / "envs").exists(), "nothing was installed"
    assert mine.add(ASSET)["already"], "the same release file again is its entry, not a second"


def test_an_older_release_file_github_states_no_hash_for_is_read_once_and_pinned_by_what_it_is():
    zipped = package(description())
    http = GitHub(repo_api([], releases=released(zipped, digest=False)), {ASSET: zipped})
    found = link_recognise.recognise(ASSET, http=http)
    assert found.kind == "template" and found.files[0]["archive"]["sha256"] == hashlib.sha256(zipped).hexdigest()


def test_a_model_in_the_repositorys_own_files_is_pinned_at_the_commit_and_lfs_is_followed():
    """F045: weights kept in Git LFS are fetched from LFS by the hash the pointer states, never the pointer itself."""
    d = description()
    weights_sha = d["weights"]["torchscript"]["sha256"]
    pointer = f"version https://git-lfs.github.com/spec/v1\noid sha256:{weights_sha}\nsize 3000\n".encode()
    files = {f"{RAW}/models/counter/rdf.yaml": yaml.safe_dump(d).encode(),
             f"{RAW}/models/counter/weights.pt": pointer}
    tree = ["models/counter/rdf.yaml", "models/counter/weights.pt", "models/counter/test_input_0.npy",
            "models/counter/test_output_0.npy", "README.md"]
    sizes = {"models/counter/weights.pt": len(pointer), "models/counter/test_input_0.npy": 2000,
             "models/counter/test_output_0.npy": 2000}
    http = GitHub(repo_api(tree, sizes), files)
    found = link_recognise.recognise("https://github.com/maya/diatom-tools/tree/main/models/counter", http=http)
    assert found.kind == "template", found.why
    pinned = {f["path"]: f for f in found.files}
    assert pinned["weights.pt"] == {"path": "weights.pt", "bytes": 3000, "sha256": weights_sha,
                                    "url": f"https://media.githubusercontent.com/media/maya/diatom-tools/{SHA}/models/counter/weights.pt"}
    assert pinned["test_input_0.npy"]["url"] == f"{RAW}/models/counter/test_input_0.npy"
    assert pinned["rdf.yaml"]["url"] == f"{RAW}/models/counter/rdf.yaml", "at the commit, never the branch"
    assert found.source == {"git": "https://github.com/maya/diatom-tools", "rev": SHA,
                            "url": f"https://github.com/maya/diatom-tools/blob/{SHA}/models/counter/rdf.yaml"}
    # An LFS object that isn't the file the description pins, or a file missing at the commit, is said.
    files[f"{RAW}/models/counter/weights.pt"] = pointer.replace(weights_sha.encode(), b"f" * 64)
    found = link_recognise.recognise("https://github.com/maya/diatom-tools/tree/main/models/counter", http=http)
    assert found.kind == "unsupported" and "in Git LFS isn't the file" in found.why
    http.api[f"/repos/maya/diatom-tools/git/trees/{SHA}?recursive=1"]["tree"] = [
        e for e in http.api[f"/repos/maya/diatom-tools/git/trees/{SHA}?recursive=1"]["tree"] if "weights" not in e["path"]]
    found = link_recognise.recognise("https://github.com/maya/diatom-tools/tree/main/models/counter", http=http)
    assert "isn't in maya/diatom-tools at 0123456" in found.why and "a submodule or a download" in found.why


def test_model_descriptions_a_reviewed_runner_cant_run_are_each_said():
    d = description(weights={"tensorflow_saved_model_bundle": {"source": "tf.zip"}})
    zipped = package(d)
    http = GitHub(repo_api(["README.md"], releases=released(zipped)), {ASSET: zipped})
    found = link_recognise.recognise("https://github.com/maya/diatom-tools", http=http)
    assert found.kind == "unsupported" and "1 bioimage.io model description read, none" in found.why
    assert "models-v1/diatom_counter.zip: its weights need TensorFlow" in found.why


def test_github_says_why_it_could_not_be_read():
    with pytest.raises(link_recognise.CantRead, match="no public repository maya/diatom-tools"):
        link_recognise.recognise("https://github.com/maya/diatom-tools", http=GitHub({}, {}))
    api = repo_api([])
    del api["/repos/maya/diatom-tools/commits/main"]
    with pytest.raises(link_recognise.CantRead, match="no branch, tag or commit named feature"):
        link_recognise.recognise("https://github.com/maya/diatom-tools/tree/feature", http=GitHub(api, {}))

    class Limited(GitHub):
        def get(self, url, headers=None, **kw):
            return R(403, headers={"x-ratelimit-remaining": "0"})

    with pytest.raises(link_recognise.CantRead, match="hourly limit"):
        link_recognise.recognise("https://github.com/maya/diatom-tools", http=Limited({}, {}))


def test_instance_labels_carried_as_whole_float_numbers_are_read_exactly_and_nothing_else_is():
    out = np.zeros((1, 1, 8, 8), np.float32)
    out[0, 0, 1:3, 1:3], out[0, 0, 5:7, 5:7] = 7, 338
    labels = template.to_labels(out, ["b", "c", "y", "x"], (8, 8), {"channel_names": ["instance_labels"]})
    assert labels.dtype == np.int32 and set(np.unique(labels)) == {0, 7, 338}
    out[0, 0, 0, 0] = 0.5
    with pytest.raises(ValueError):
        template.to_labels(out, ["b", "c", "y", "x"], (8, 8), {"channel_names": ["instance_labels"]})


def test_instansegs_published_package_has_an_exact_reviewed_binding():
    """The supported repository of ticket 62 row 7: InstanSeg's brightfield_nuclei 0.1.1, named by its exact bytes."""
    rdf_sha = "57a28f865983f05b874dc2ad2c93570abaa53194e79aef63cfc5f9b4a88fd42a"
    weights = "0c724da169d507d8dd2e206ad3675a0b1566824eee6e20ad9a0ba916311d9dd0"
    binding = link_recognise.reviewed_zoo_output(rdf_sha, weights)
    assert binding["channels"] == ["instance_labels"] and binding["evidence"]
    assert link_recognise.reviewed_zoo_output(rdf_sha, "0" * 64) is None
    tensor = {"axes": [{"type": "batch"}, {"type": "channel", "channel_names": ["nuclei"]}]}
    assert template.resolved_meaning(tensor, rdf_sha, weights) == ["instance_labels"]


def test_an_entry_made_by_an_earlier_reviewed_version_of_a_template_is_still_its_reviewed_runner(tmp_path, monkeypatch):
    templates = tmp_path / "templates" / "runner"
    templates.mkdir(parents=True)
    for name, data in (("adapter.py", b"new"), ("env.lock", b"lock")):
        (templates / name).write_bytes(data)
    old = {"adapter.py": hashlib.sha256(b"old").hexdigest(), "env.lock": hashlib.sha256(b"lock").hexdigest()}
    (templates / "template.toml").write_text(f"earlier = [{{ version = 1, sha256 = {json.dumps(old).replace(':', ' =')} }}]\n")
    monkeypatch.setattr(link_recognise, "TEMPLATES", tmp_path / "templates")
    entry = tmp_path / "entry"
    entry.mkdir()

    class M:
        folder = str(entry)

    for adapter, runs in ((b"new", "runner"), (b"old", "runner"), (b"changed", "")):
        (entry / "adapter.py").write_bytes(adapter)
        (entry / "env.lock").write_bytes(b"lock")
        assert link_recognise.reviewed_template(M()) == runs, adapter


# --- untrusted links read safely (review of #348) ----------------------------------------------------------------------
class Redirecting(GitHub):
    """GitHub, except that some addresses answer with a redirect."""

    def __init__(self, api, files, redirects):
        super().__init__(api, files)
        self.redirects = redirects

    def get(self, url, headers=None, **kw):
        if url in self.redirects:
            self.asked.append((url, dict(headers or {})))
            return R(302, headers={"Location": self.redirects[url]})
        return super().get(url, headers=headers, **kw)

    def head(self, url, **kw):
        return self.get(url, **kw) if url in self.redirects else R(200, headers={"content-length": "3000"})


@pytest.mark.parametrize("weights_at, why", [
    ("http://127.0.0.1:8770/weights.pt", "isn't an https address"),
    ("https://192.168.1.5/weights.pt", "isn't a place models are published"),
    ("https://github.com/maya/diatom-tools/raw/main/weights.pt", "isn't an https address"),  # redirects to 127.0.0.1
])
def test_an_address_a_description_names_is_read_only_from_a_publisher_over_https(weights_at, why):
    d = description()
    d["weights"]["torchscript"]["source"] = weights_at
    zipped = package(d)
    http = Redirecting(repo_api([], releases=released(zipped)), {ASSET: zipped},
                       {"https://github.com/maya/diatom-tools/raw/main/weights.pt": "http://127.0.0.1:8770/weights.pt"})
    found = link_recognise.recognise(ASSET, http=http)
    assert found.kind == "unsupported" and why in found.why, found.why
    assert not any("127.0.0.1" in url or "192.168" in url for url, _ in http.asked), "never reached"


def test_a_redirect_to_another_host_never_carries_the_users_key():
    http = Redirecting({}, {"https://objects.githubusercontent.com/x": b"ok"},
                       {"https://api.github.com/x": "https://objects.githubusercontent.com/x"})
    assert link_recognise._get("https://api.github.com/x", http, headers={"Authorization": "Bearer canary"}) == b"ok"
    assert http.asked[0][1].get("Authorization") == "Bearer canary"
    assert http.asked[1] == ("https://objects.githubusercontent.com/x", "")  # no key, and no range
    loop = Redirecting({}, {}, {f"https://github.com/{i}": f"https://github.com/{i + 1}" for i in range(9)})
    with pytest.raises(link_recognise.CantRead, match="redirected more than 5 times"):
        link_recognise._get("https://github.com/0", loop)


@pytest.mark.parametrize("addresses, refused", [
    (["127.0.0.1"], True), (["10.1.2.3"], True), (["192.168.0.4"], True), (["169.254.169.254"], True),
    (["::1"], True), (["fd00::1"], True), (["140.82.112.3", "10.0.0.1"], True), (["140.82.112.3"], False),
])
def test_a_publishers_host_must_resolve_to_public_addresses_only(addresses, refused):
    why = link_recognise.public_host("github.com", resolve=lambda host: addresses)
    assert bool(why) == refused and (not refused or "address on this Mac or its network" in why)


def test_what_is_read_is_capped_and_one_recognition_has_a_budget():
    big = GitHub({}, {"https://github.com/big": b"x" * 2001})
    with pytest.raises(link_recognise.CantRead, match="more than the 2,000 bytes"):
        link_recognise._get("https://github.com/big", big, cap=2000)
    budget = link_recognise._Budget(seconds=60, nbytes=1000)
    held = link_recognise._BUDGET.set(budget)
    try:
        with pytest.raises(link_recognise.OverBudget, match="1000|0 MB allowance"):
            link_recognise._get("https://github.com/big", big, cap=5000)
    finally:
        link_recognise._BUDGET.reset(held)


def test_an_oversized_cover_or_description_in_a_zip_is_refused_never_read_whole(monkeypatch):
    monkeypatch.setattr(link_recognise, "COVER_MAX", 100)
    d = description(covers=["cover.png"])
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("diatom_counter/rdf.yaml", yaml.safe_dump(d))
        z.writestr("diatom_counter/cover.png", b"\x89PNG" + b"c" * 500)
        for name, data in CONTENT.items():
            z.writestr("diatom_counter/" + name, data)
    zipped = out.getvalue()
    found = link_recognise.recognise(ASSET, http=GitHub(repo_api([], releases=released(zipped)), {ASSET: zipped}))
    assert found.kind == "template" and found.cover == b"", "the model is kept; its oversized picture is not"
    huge = io.BytesIO()
    with zipfile.ZipFile(huge, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("m/rdf.yaml", "name: x\n" + "#" * 1_200_000)
    zipped = huge.getvalue()
    found = link_recognise.recognise(ASSET, http=GitHub(repo_api([], releases=released(zipped)), {ASSET: zipped}))
    assert found.kind == "unsupported" and "too big to be a model description" in found.why


@pytest.mark.parametrize("bad", [{"inputs": ["x"]}, {"weights": {"torchscript": None}}, {"inputs": [{"axes": 5}]}])
def test_one_malformed_description_among_good_ones_never_loses_the_good_one(bad):
    broken, good = package({**description(), **bad}), package(description())
    newer = "https://github.com/maya/diatom-tools/releases/download/models-v2/broken.zip"
    releases = [{"tag_name": "models-v2", "assets": [{"name": "broken.zip", "size": len(broken), "browser_download_url": newer,
                                                      "digest": "sha256:" + hashlib.sha256(broken).hexdigest()}]},
                *released(good)]
    found = link_recognise.recognise("https://github.com/maya/diatom-tools",
                                     http=GitHub(repo_api(["README.md"], releases=releases), {newer: broken, ASSET: good}))
    assert found.kind == "template" and "release models-v1" in found.why, found.why
    assert "models-v2/broken.zip (its model description is malformed where the app reads it" in found.why
    alone = link_recognise.recognise(newer, http=GitHub(repo_api([], releases=releases[:1]), {newer: broken}))
    assert alone.kind == "unsupported" and "malformed" in alone.why


def test_the_outcome_says_exactly_what_was_read():
    tree = ["setup.py", "models/counter/train.ipynb", "models/counter/model_final.pth"]
    http = GitHub(repo_api(tree), {})
    folder = link_recognise.recognise("https://github.com/maya/diatom-tools/tree/main/models/counter", http=http)
    assert folder.why == (
        "maya/diatom-tools's models/counter holds 1 notebook, 1 file that may be weights (model_final.pth) with no description "
        "of how to run them; its licence MIT. Read: the 2 files under models/counter at commit 0123456; not its releases "
        "(this link is to a folder). No bioimage.io model description (rdf.yaml or bioimageio.yaml), the one kind of "
        "GitHub source a reviewed runner runs, was in what was read. One may be in what wasn't: paste the folder or the "
        "release file that holds it; else an adapter has to be written and checked to connect it")
    http.api[f"/repos/maya/diatom-tools/git/trees/{SHA}?recursive=1"]["truncated"] = True
    partial = link_recognise.recognise("https://github.com/maya/diatom-tools", http=http)
    assert "Read: its 3 files at commit 0123456 (GitHub listed only part of them); no releases." in partial.why
    assert "was in what was read" in partial.why and "It has no" not in partial.why
    http.api[f"/repos/maya/diatom-tools/git/trees/{SHA}?recursive=1"]["truncated"] = False
    whole = link_recognise.recognise("https://github.com/maya/diatom-tools", http=http)
    assert whole.why.endswith("Read: its 3 files at commit 0123456; no releases. It has no bioimage.io model description "
                              "(rdf.yaml or bioimageio.yaml) in its files or releases, the one kind of GitHub source a "
                              "reviewed runner runs, so an adapter has to be written and checked to connect it")


def test_zips_left_unread_are_counted_and_reading_stops_at_the_first_that_runs(monkeypatch):
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("data/readme.txt", "no model")
    plain = plain.getvalue()
    urls = [f"https://github.com/maya/diatom-tools/releases/download/v{i}/data{i}.zip" for i in range(3)]
    releases = [{"tag_name": f"v{i}", "assets": [{"name": f"data{i}.zip", "size": len(plain), "browser_download_url": u,
                                                  "digest": "sha256:" + hashlib.sha256(plain).hexdigest()}]}
                for i, u in enumerate(urls)]
    monkeypatch.setattr(link_recognise, "MAX_CANDIDATES", 2)
    found = link_recognise.recognise("https://github.com/maya/diatom-tools",
                                     http=GitHub(repo_api(["README.md"], releases=releases), dict.fromkeys(urls, plain)))
    assert "2 of the 3 zip files in 3 releases" in found.why and "was in what was read" in found.why
    good = package(description())
    http = GitHub(repo_api(["README.md"], releases=[*released(good), *releases]), {ASSET: good, **dict.fromkeys(urls, plain)})
    assert link_recognise.recognise("https://github.com/maya/diatom-tools", http=http).kind == "template"
    assert not any(url in urls for url, _ in http.asked), "nothing after the one that runs is read"
