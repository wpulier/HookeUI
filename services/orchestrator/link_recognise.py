"""Add a capability from a link (docs/design/2026-10-13-capabilities-for-everyone.md, C3): what a pasted link is, and
whether a reviewed template runs it.

A link of a kind the app knows becomes an entry run by a **reviewed runner**: a template in `catalog/templates/<id>/` (its
adapter and its locked environment, copied byte for byte) on the model's own files, each pinned by its hash. The runner is
reviewed; **the model is not**: a TorchScript model is code its makers wrote (PyTorch's security policy treats it as
such), so the runner loads and runs it inside macOS's sandbox (sandbox.required), as any code nobody reviewed. Its hashes
and its own test say it is the model its makers published and that it runs as they ran it; neither says it is safe.

- **The BioImage Model Zoo** (`bioimageio`): a model page (`https://bioimage.io/#/?id=affable-shark`), its nickname alone,
  or its files' address. The newest version's `rdf.yaml` is read: 2D, TorchScript or ONNX weights, 1 or 3 input channels,
  preprocessing the runner knows; its weights, test tensors (its own test, run before any answer) and `rdf.yaml` pinned
  by sha256, its cover kept as the entry's picture. Weights that need the makers' own architecture code
  (`pytorch_state_dict` alone) or TensorFlow are said, never run.
- **Hugging Face** (`hf-segmentation`): a transformers segmentation model (its card's task "image-segmentation", its
  library transformers, a kind of model the runner knows, its weights as one model.safetensors, and no `auto_map`: never
  its makers' own code) is pinned at its commit: config.json, preprocessor_config.json and model.safetensors, each by its
  hash. Weights only in PyTorch's pickle format, a model that needs its makers' code, or a kind the runner doesn't know
  are said; any other Hugging Face link stays a Cellpose model file (mine.py).
- **GitHub** (`from_github`; ticket 62 row 7, F043-F048 of docs/research/2026-10-19-first-use-failure-map.md): what the
  link points to is told apart first (`github`: a repository, a folder, a file, its releases, one release's file, or a
  page such as an issue, which is no source and is said so). A branch is read at the commit it is at, and kept so. A
  bioimage.io model description in its files (pinned at that commit; a Git LFS file by the hash its pointer states) or in
  a release's zip (read by byte ranges, each file a member of the zip, the zip pinned by the sha256 GitHub states) is read
  as the zoo's are, and run by the same reviewed template when it passes. Anything else is said with what it holds (a
  Python package, a napari plugin, notebooks, weights with no description, submodules: each fact with the file that says
  it) and what it lacks; the builder (C4) or the user's coding tool writes the adapter. Reading writes nothing and runs
  nothing (ADR 0021).
- **Anything else** (a page) is said with why. What it is called is its repository's name (`link_name`), whatever
  commit, folder or file the link points to; the commit a link pins is kept in its entry's source (`link_source`).

`recognise` reads the outside world (the zoo's files, GitHub's API and files, by GET, sizes by HEAD) and writes nothing; `mine.add` writes the
entry from what it found.
"""
from __future__ import annotations

import contextvars
import hashlib
import io
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

from gpu_worker import manifests as catalog

ZOO = "https://uk1s3.embassy.ebi.ac.uk/public-datasets/bioimage.io"
ZOO_PAGE = "https://bioimage.io/#/?id="
TEMPLATES = catalog.ROOT.parent / "templates"
OPEN = {"mit", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "cc0-1.0", "cc-by-4.0", "cc-by-sa-4.0", "unlicense",
        "isc", "zlib", "mpl-2.0", "lgpl-3.0", "lgpl-2.1", "gpl-3.0", "gpl-2.0", "agpl-3.0"}
NONCOMMERCIAL = re.compile(r"\bnc\b|non-?commercial", re.I)
SMALL = 20_000_000  # files hashed at add time (the rdf, test tensors, cover); weights are pinned by the rdf's own hash
KNOWN_PRE = {"zero_mean_unit_variance", "fixed_zero_mean_unit_variance", "scale_range", "scale_linear", "clip",
             "binarize", "sigmoid", "ensure_dtype"}


class CantRead(RuntimeError):
    """The link could not be read, in plain words."""


@dataclass
class Found:
    kind: str  # "template" (a reviewed template runs it), "needs-code" (the builder, C4), "unsupported", "elsewhere"
    why: str = ""  # what it is, or why it can't run here, in plain words
    template: str = ""
    name: str = ""
    what: str = ""
    version: str = ""
    link: str = ""
    licence: str = ""
    tier: str = "noncommercial"
    makers: str = ""
    paper: str = ""
    tags: list[str] = field(default_factory=list)
    source: dict = field(default_factory=dict)
    files: list[dict] = field(default_factory=list)
    conversion: str = ""  # reviewed app output conversion, distinct from upstream inference
    cover: bytes = b""  # its picture of it working (the zoo's cover), written beside the entry
    cover_name: str = ""
    id_hint: str = ""  # what its entry's id is made from (the zoo's nickname, the repository's name)

    def wire(self) -> dict:
        size = sum(f.get("bytes", 0) for f in self.files)
        return {"kind": self.kind, "why": self.why, "template": self.template, "name": self.name, "what": self.what,
                "version": self.version, "link": self.link, "licence": self.licence, "tier": self.tier,
                "makers": self.makers, "tags": self.tags, "bytes": size, "files": len(self.files)}


def tier(licence: str) -> str:
    """Its licence's tier (licences.py): open for the common open licences, non-commercial when it says so or when
    nothing says otherwise (an unknown licence is never taken as open)."""
    words = (licence or "").strip().lower()
    if words in OPEN:
        return "open"
    return "noncommercial"


def what_words(text: str, limit: int = 12) -> str:
    words = re.sub(r"\s+", " ", text or "").strip().rstrip(".").split(" ")
    return " ".join(words[:limit])


def tags_of(tags) -> list[str]:
    out = []
    for t in tags or []:
        t = str(t).lower().strip()
        if catalog.TAG.fullmatch(t) and t not in out:
            out.append(t)
    return out[:12]


# --- the BioImage Model Zoo -------------------------------------------------------------------------------------------
def zoo_id(link: str) -> str | None:
    """The zoo's nickname a link names ("affable-shark"), else None."""
    link = link.strip()
    m = re.search(r"bioimage\.io/#/\?(?:[^#]*&)?id=([a-z0-9-]+)", link) or \
        re.search(r"public-datasets/bioimage\.io/([a-z0-9-]+)(?:/|$)", link) or \
        re.fullmatch(r"([a-z]+(?:-[a-z]+)+)", link)
    return m.group(1) if m else None


# --- reading the outside world, guarded ------------------------------------------------------------------------------
# A pasted link and the description it leads to are untrusted: every address they name is read only over https, from a
# host of the places models are published (each redirect hop checked again), from a public address (the real network
# resolves the host first and refuses loopback, private, link-local and reserved addresses), with a hard cap on what is
# read, inside one recognition's time and byte budget. Nothing here runs, installs or writes (ADR 0021).
PUBLISHERS = ("github.com", "githubusercontent.com", "huggingface.co", "hf.co", "zenodo.org", "bioimage.io",
              "embassy.ebi.ac.uk")  # each with its subdomains: api.github.com, release-assets.githubusercontent.com...
MAX_HOPS = 5
COVER_MAX = 4_000_000  # a picture of the model at work, never more
API_MAX = 16_000_000  # one GitHub API answer (a large repository's file list)
BUDGET_S = 120.0  # one recognition, all its reads
BUDGET_BYTES = 160_000_000
_BUDGET: contextvars.ContextVar["_Budget | None"] = contextvars.ContextVar("link_budget", default=None)


@dataclass
class Reply:
    status_code: int
    headers: dict
    content: bytes
    url: str = ""

    def json(self):
        import json

        try:
            return json.loads(self.content)
        except ValueError as exc:
            raise CantRead(f"{_host(self.url)} answered with something that isn't JSON") from exc


class _Budget:
    """One recognition's allowance of time and bytes, across its threads."""

    def __init__(self, seconds: float = BUDGET_S, nbytes: int = BUDGET_BYTES):
        import threading
        import time

        self.deadline, self.left, self._lock, self._clock = time.monotonic() + seconds, nbytes, threading.Lock(), time.monotonic
        self.seconds, self.nbytes = seconds, nbytes

    def check(self, more: int = 0) -> None:
        with self._lock:
            if self._clock() > self.deadline:
                raise OverBudget(f"reading the link took longer than its {self.seconds:.0f} s allowance")
            self.left -= more
            if self.left < 0:
                raise OverBudget(f"reading the link needed more than its {self.nbytes // 1_000_000} MB allowance")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def allowed(url: str) -> str:
    """Why an address may not be read ("" when it may): https only, a publisher's host, no credentials in it."""
    parts = urlsplit(url)
    host = _host(url)
    if parts.scheme != "https":
        return f"{url} isn't an https address"
    if parts.username or parts.password:
        return f"{host} was given with credentials in its address"
    if not any(host == p or host.endswith("." + p) for p in PUBLISHERS):
        return f"{host or url} isn't a place models are published ({', '.join(PUBLISHERS)})"
    return ""


def public_host(host: str, resolve=None) -> str:
    """Why a host may not be connected to ("" when it may): every address it resolves to must be a public one."""
    import ipaddress
    import socket

    resolve = resolve or (lambda h: [a[4][0] for a in socket.getaddrinfo(h, 443, type=socket.SOCK_STREAM)])
    try:
        addresses = resolve(host)
    except OSError as exc:
        return f"{host} couldn't be found ({type(exc).__name__}); check this Mac is online"
    for a in addresses or ["0.0.0.0"]:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            return f"{host} resolves to {a}, an address on this Mac or its network, which a link is never read from"
    return ""


class _Net:
    """The real network (httpx), each request streamed under its cap, to a host checked to resolve to public
    addresses only. Redirects are never followed here: `_open` checks each hop."""

    def send(self, method: str, url: str, headers: dict, timeout: float, cap: int) -> Reply:
        import httpx

        why = public_host(_host(url))
        if why:
            raise CantRead(why)
        with httpx.stream(method, url, headers=headers, timeout=timeout, follow_redirects=False) as r:
            head = {k.lower(): v for k, v in r.headers.items()}
            if method == "HEAD" or r.status_code in (301, 302, 303, 307, 308) or r.status_code >= 400:
                return Reply(r.status_code, head, b"", url)
            if int(head.get("content-length") or 0) > cap:
                raise CantRead(f"{_host(url)} would send {int(head['content-length']):,} bytes, more than the "
                               f"{cap:,} read for it")
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > cap:
                    raise CantRead(f"{_host(url)} sent more than the {cap:,} bytes read for it")
            return Reply(r.status_code, head, bytes(body), url)


def _send(http, method: str, url: str, headers: dict, timeout: float, cap: int) -> Reply:
    if isinstance(http, _Net):
        return http.send(method, url, headers, timeout, cap)
    r = (http.head if method == "HEAD" else http.get)(url, headers=headers, timeout=timeout, follow_redirects=False)
    head = {str(k).lower(): v for k, v in dict(getattr(r, "headers", None) or {}).items()}
    content = b"" if method == "HEAD" else (r.content or b"")
    if len(content) > cap:
        raise CantRead(f"{_host(url)} sent more than the {cap:,} bytes read for it")
    return Reply(r.status_code, head, content, url)


def _open(url: str, http, *, method: str = "GET", headers: dict | None = None, cap: int = SMALL,
          timeout: float = 30) -> Reply:
    """One guarded read (see above): each redirect hop allowed again, credentials never carried to another host."""
    headers = dict(headers or {})
    for _hop in range(MAX_HOPS + 1):
        why = allowed(url)
        if why:
            raise CantRead(why)
        budget = _BUDGET.get()
        if budget:
            budget.check()
        try:
            r = _send(http, method, url, headers, timeout, cap)
        except CantRead:
            raise
        except Exception as exc:  # noqa: BLE001 - offline, DNS, TLS: said as one plain reason
            raise CantRead(f"couldn't reach {_host(url)} ({type(exc).__name__}); check this Mac is online") from exc
        if budget:
            budget.check(len(r.content))
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            from urllib.parse import urljoin

            following = urljoin(url, r.headers["location"])
            if _host(following) != _host(url):
                headers.pop("Authorization", None)
            url = following
            continue
        return r
    raise CantRead(f"{_host(url)} redirected more than {MAX_HOPS} times")


def _get(url: str, http, cap: int = SMALL, headers: dict | None = None) -> bytes:
    r = _open(url, http, headers=headers, cap=cap)
    if r.status_code == 404:
        raise CantRead(f"nothing at {url}")
    if r.status_code >= 400:
        raise CantRead(f"{url} answered {r.status_code}")
    return r.content


def _size(url: str, http) -> int:
    try:
        r = _open(url, http, method="HEAD")
        return int(r.headers.get("content-length") or 0) if r.status_code < 400 else 0
    except CantRead as exc:
        if str(exc).startswith("couldn't reach"):
            return 0  # unknown until downloaded: said as 0, checked by its hash then
        raise


def _pinned(base: str, source: str, sha: str | None, http, fetched: dict[str, bytes]) -> dict:
    """One of its files: its path, bytes, sha256 and address. A small one without a stated hash is read and hashed."""
    name = Path(str(source)).name
    url = f"{base}/{name}"
    if sha:
        size = _size(url, http)
        if size:
            return {"path": name, "bytes": size, "sha256": str(sha), "url": url}
    data = fetched.get(name) or _get(url, http)  # no size from the server: read (only a small file), checked by its hash
    if sha and hashlib.sha256(data).hexdigest() != str(sha):
        raise CantRead(f"its file {name} isn't the one its rdf.yaml pins (its sha256 differs)")
    if len(data) > SMALL:
        raise CantRead(f"its file {name} states no hash and is too big to read now")
    fetched[name] = data
    return {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "url": url}


def _axes(t: dict) -> list[str]:
    axes = t.get("axes")
    if isinstance(axes, str):
        return list(axes.lower())
    return ["b" if a.get("type") == "batch" else "c" if a.get("type") == "channel" else str(a.get("id", ""))[:1].lower()
            for a in axes or []]


def _channels(t: dict) -> int:
    axes = t.get("axes")
    if isinstance(axes, str):
        shape = t.get("shape")
        if "c" not in axes:
            return 1
        i = axes.index("c")
        return int(shape[i]) if isinstance(shape, list) else int((shape or {}).get("min", [1] * len(axes))[i])
    for a in axes or []:
        if a.get("type") == "channel":
            return len(a.get("channel_names") or []) or int(a.get("size") or 1)
    return 1


def zoo_version(link: str, nick: str) -> str:
    direct = re.search(r"public-datasets/bioimage\.io/" + re.escape(nick) + r"/([^/?#]+)", link)
    query = parse_qs(urlsplit(link).fragment.partition("?")[2])
    identity = unquote((query.get("id") or [""])[0])
    version = (direct.group(1) if direct else (query.get("version") or [""])[0]
               or (identity.partition("/")[2] if identity.startswith(nick + "/") else ""))
    if version and not re.fullmatch(r"[A-Za-z0-9_.-]+", version):
        raise CantRead("the Zoo version in this link is not valid")
    return version


def output_meaning(tensor: dict) -> list[str]:
    """Only named native outputs have a reviewed conversion; channel count cannot supply semantics."""
    axes = tensor.get("axes") or []
    names = (next((a.get("channel_names") for a in axes if a.get("type") == "channel"), None)
             if isinstance(axes, list) else tensor.get("channel_names"))
    names = names or tensor.get("channel_names") or [tensor.get("id") or tensor.get("name") or ""]
    aliases = {"fg": "foreground", "bd": "boundary", "bg": "background"}
    names = [aliases.get(str(n).lower(), str(n).lower()) for n in names]
    known = ({"foreground"}, {"foreground", "boundary"}, {"background", "foreground", "boundary"}, {"instance_labels"})
    return names if len(set(names)) == len(names) and set(names) in known else []


def reviewed_zoo_output(rdf_sha256: str, weights_sha256: str) -> dict | None:
    """A reviewed semantic binding is data, keyed by both exact upstream byte identities."""
    import json
    try:
        entry = json.loads((TEMPLATES / "bioimageio" / "output-semantics.json").read_text()).get(rdf_sha256)
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) and entry.get("weightsSha256") == weights_sha256 else None


def zoo(nick: str, http, version: str = "") -> Found:
    found = Found("unsupported", link=f"{ZOO_PAGE}{nick}", template="bioimageio")
    try:
        import json

        versions = json.loads(_get(f"{ZOO}/{nick}/versions.json", http)).get("versions") or []
    except (CantRead, ValueError) as exc:
        raise CantRead(f"the BioImage Model Zoo has no model {nick!r} ({exc})") from exc
    if not versions:
        raise CantRead(f"the BioImage Model Zoo lists no version of {nick}")
    if version and version not in {str(v["v"]) for v in versions}:
        raise CantRead(f"the BioImage Model Zoo lists no version {version} of {nick}")
    v = version or str(versions[0]["v"])
    if version:
        found.link += f"/{v}"
    base = f"{ZOO}/{nick}/{v}/files"
    raw = _get(f"{base}/rdf.yaml", http)
    found.version = f"bioimage.io {nick} {v}"
    found.source = {"bioimageio": nick, "url": f"{base}/rdf.yaml"}
    fetched: dict[str, bytes] = {"rdf.yaml": raw}
    rdf_file = {"path": "rdf.yaml", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "url": f"{base}/rdf.yaml"}
    try:
        found = _description(found, raw, rdf_file, lambda source, sha: _pinned(base, source, sha, http, fetched),
                             lambda name: _get(f"{base}/{name}", http, cap=COVER_MAX))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
        found.kind, found.files, found.why = "unsupported", [], malformed(exc)
    if found.kind == "template":
        found.why = found.why.replace("A bioimage.io model", "a BioImage Model Zoo model", 1)
    return found


def _description(found: Found, raw: bytes, rdf_file: dict, pin: Callable[[str, str | None], dict],
                 cover: Callable[[str], bytes]) -> Found:
    """A bioimage.io model description (`rdf.yaml`, wherever it was found: the zoo, a GitHub folder, a release's zip) read
    against what the reviewed `bioimageio` template runs. `pin(source, sha256)` is one of its files as an entry pins it;
    `cover(name)` its picture. `found` arrives with where it is from (link, version, source)."""
    import yaml

    found.template = "bioimageio"
    try:
        rdf = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise CantRead(f"its model description isn't YAML ({str(exc).splitlines()[0]})") from exc
    if not isinstance(rdf, dict):
        raise CantRead("its model description isn't a description (not a YAML mapping)")
    found.name = found.name or str(rdf.get("name") or "")
    found.what = what_words(str(rdf.get("description") or rdf.get("name") or found.name))
    found.licence = str(rdf.get("license") or found.licence or "not stated")
    found.tier = tier(found.licence)
    found.makers = ", ".join(str(a.get("name")).split(";")[0] for a in rdf.get("authors") or []
                             if isinstance(a, dict) and a.get("name"))
    cite = [c for c in rdf.get("cite") or [] if isinstance(c, dict) and (c.get("doi") or c.get("url"))]
    if cite:
        found.paper = f"https://doi.org/{cite[0]['doi']}" if cite[0].get("doi") else str(cite[0]["url"])
    found.tags = tags_of(rdf.get("tags"))
    if rdf.get("type") != "model":
        found.why = f"it is a {rdf.get('type') or 'resource'} of the zoo, not a model"
        return found
    weights = rdf.get("weights") or {}
    kind = "torchscript" if "torchscript" in weights else "onnx" if "onnx" in weights else ""
    if not kind:
        if any(k.startswith(("tensorflow", "keras")) for k in weights):
            found.why = "its weights need TensorFlow, which HookeUI doesn't run yet"
        else:
            found.kind = "needs-code"
            found.why = ("its weights need its makers' own architecture code (pytorch_state_dict only): an adapter has to "
                         "be written and checked for it")
        return found
    inp, out = (rdf.get("inputs") or [{}])[0], (rdf.get("outputs") or [{}])[0]
    if "z" in _axes(inp):
        found.why = "it is a 3D model: the app runs 2D models from a picture"
        return found
    if _channels(inp) not in (1, 3):
        found.why = f"it takes {_channels(inp)} channels: a camera picture has 1 (grey) or 3 (colour)"
        return found
    unknown = [str(s.get("id") or s.get("name")) for s in inp.get("preprocessing") or []
               if (s.get("id") or s.get("name")) not in KNOWN_PRE]
    if unknown:
        found.why = f"its preprocessing ({', '.join(unknown)}) isn't one the app's runner knows yet"
        return found
    binding = reviewed_zoo_output(hashlib.sha256(raw).hexdigest(), weights[kind].get("sha256", "")) if not output_meaning(out) else None
    if len(rdf.get("inputs") or []) != 1 or len(rdf.get("outputs") or []) != 1 or not (output_meaning(out) or binding):
        found.kind = "needs-code"
        found.why = "its native output meaning needs a reviewed adapter; channel count does not identify objects or boundaries"
        return found
    if out.get("postprocessing"):
        found.kind = "needs-code"
        found.why = "its output postprocessing needs a reviewed adapter before the meaning of its labels is known"
        return found
    if binding:
        found.conversion = binding["conversion"]
        found.version += f"; {binding.get('version', 'ObservationUI nearest-seed v1')}"
    files = [rdf_file, pin(weights[kind]["source"], weights[kind].get("sha256"))]
    for t, legacy in ((inp, "test_inputs"), (out, "test_outputs")):
        ref = t.get("test_tensor")
        if isinstance(ref, dict) and ref.get("source"):
            files.append(pin(ref["source"], ref.get("sha256")))
        elif rdf.get(legacy):
            files.append(pin(rdf[legacy][0], None))
        else:
            found.why = "it has no test tensors: its own test can't be run before it answers"
            return found
    covers = rdf.get("covers") or []
    if covers:
        name = Path(str(covers[0].get("source") if isinstance(covers[0], dict) else covers[0])).name
        if name.lower().endswith(catalog.EXAMPLE_SUFFIXES):
            try:
                found.cover, found.cover_name = cover(name), "example" + Path(name).suffix.lower()
            except CantRead:
                pass
    found.kind, found.files = "template", files
    found.why = f"A bioimage.io model ({kind}), run by the app's reviewed bioimage.io runner on its own files"
    if binding:
        found.why += "; " + binding["conversion"]
    return found


HF = "https://huggingface.co"
HF_KINDS = {"segformer", "upernet", "dpt", "beit", "mobilevit", "mobilevitv2", "data2vec-vision", "mask2former",
            "maskformer"}  # catalog/templates/hf-segmentation/adapter.py's SEMANTIC and UNIVERSAL


def hf(repo: str, revision: str, http, token: str = "") -> Found | None:
    """A Hugging Face transformers segmentation model (see the module's docstring), or None when the repository is not
    one (it may still be a Cellpose model file: mine.py)."""
    import json

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = _open(f"{HF}/api/models/{repo}/revision/{revision}?blobs=true", http, headers=headers, cap=API_MAX)
    if r.status_code in (401, 403):
        raise CantRead(f"{repo} is gated: add your Hugging Face key and accept its licence on {HF}/{repo}, then add it again")
    if r.status_code == 404:
        raise CantRead(f"Hugging Face has no model {repo} (at {revision})")
    if r.status_code >= 400:
        raise CantRead(f"Hugging Face answered {r.status_code} for {repo}")
    info = r.json()
    if info.get("pipeline_tag") != "image-segmentation" or info.get("library_name") != "transformers":
        return None
    sha = str(info.get("sha") or "")
    card = info.get("cardData") or {}
    found = Found("unsupported", link=f"{HF}/{repo}", template="hf-segmentation", id_hint=repo.split("/")[1])
    found.name = repo.split("/")[1].replace("-", " ").replace("_", " ")
    found.what = what_words(f"Segments pictures: {found.name}")
    found.version = f"huggingface {repo}@{sha[:12]}"
    found.licence = str(card.get("license") or "not stated on its card")
    found.tier = tier(found.licence)
    found.makers = repo.split("/")[0]
    found.tags = tags_of([t for t in (info.get("tags") or []) if ":" not in str(t)][:8])
    found.source = {"hf": repo, "revision": sha}
    kind = str((info.get("config") or {}).get("model_type") or "")
    if kind not in HF_KINDS:
        found.kind = "needs-code"
        found.why = f"a {kind or 'kind of'} segmentation model the reviewed runner doesn't know yet: an adapter has to be written"
        return found
    siblings = {s["rfilename"]: s for s in info.get("siblings") or []}
    if "model.safetensors" not in siblings:
        found.why = ("its weights are split into several files, which the runner doesn't read yet"
                     if any(n.endswith(".safetensors") for n in siblings) else
                     "its weights are only in PyTorch's pickle format, which can run code when loaded: ask its makers for "
                     "safetensors")
        return found
    if not sha or "preprocessor_config.json" not in siblings or "config.json" not in siblings:
        found.why = "it has no config.json and preprocessor_config.json to load it with"
        return found
    files = []
    for name in ("config.json", "preprocessor_config.json"):
        url = f"{HF}/{repo}/resolve/{sha}/{name}"
        data = _get_with(url, http, headers)
        if len(data) > SMALL:
            raise CantRead(f"its {name} is too big to be a configuration")
        if name == "config.json":
            try:
                if json.loads(data).get("auto_map"):
                    found.kind = "needs-code"
                    found.why = "it needs its makers' own code (auto_map in its config): an adapter has to be written"
                    return found
            except ValueError as exc:
                raise CantRead(f"its config.json isn't JSON ({exc})") from exc
        files.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "url": url})
    weights = siblings["model.safetensors"]
    lfs = weights.get("lfs") or {}
    if not lfs.get("sha256"):
        found.why = "its weights state no hash on Hugging Face, so they can't be pinned"
        return found
    files.append({"path": "model.safetensors", "bytes": int(lfs.get("size") or weights.get("size") or 0),
                  "sha256": str(lfs["sha256"]), "url": f"{HF}/{repo}/resolve/{sha}/model.safetensors"})
    found.kind, found.files = "template", files
    found.why = (f"a Hugging Face {kind} segmentation model, run by the app's reviewed transformers runner on its own "
                 "files (inside the sandbox)")
    return found


def _get_with(url: str, http, headers: dict) -> bytes:
    return _get(url, http, headers=headers)


def _http():
    """The network the outside world is read through (`_Net`: guarded, streamed; a test stands in for it)."""
    return _Net()


# --- GitHub (F043-F048 of docs/research/2026-10-19-first-use-failure-map.md) -------------------------------------------
GITHUB_API = "https://api.github.com"
# A bioimage.io model description: the one kind of thing in a repository a reviewed runner runs as it is.
DESCRIPTION = re.compile(r"(?:^|/)(?:rdf|bioimageio|[^/]+\.bioimageio)\.yaml$")
LFS_POINTER = re.compile(rb"version https://git-lfs\.github\.com/spec/v1\s+oid sha256:([0-9a-f]{64})\s+size (\d+)")
PAGES = {"issues": "an issue", "pull": "a pull request", "pulls": "its pull requests", "discussions": "a discussion",
         "wiki": "its wiki", "actions": "its automated runs", "projects": "a project board", "security": "its security page",
         "pulse": "its activity page", "graphs": "its activity graphs", "compare": "a comparison of two versions",
         "milestones": "its milestones", "labels": "its labels", "tags": "its list of tags", "branches": "its branches",
         "commits": "its list of commits", "network": "its network page", "stargazers": "its stars", "forks": "its forks"}
WEIGHT_SUFFIXES = (".pt", ".pth", ".onnx", ".safetensors", ".ckpt", ".h5", ".keras", ".pb", ".bin")
MAX_CANDIDATES = 12  # model descriptions and release zips read for one link
RANGE_BLOCK = 1 << 18  # a release's zip is read by ranges: its directory and its description, never its weights


def from_github(link: str, http, token: str = "") -> Found:
    """What a GitHub link holds (see the module's docstring): the model a reviewed template runs, found in its files at a
    pinned commit or in its releases' zips; else what it is, said precisely, and what it lacks."""
    gh = github(link)
    owner, repo = gh["owner"], gh["repo"]
    home = f"https://github.com/{owner}/{repo}"
    said = Found("needs-code", link=link, name=repo, id_hint=repo, source=link_source(link))
    if gh["kind"] == "page":
        said.kind = "unsupported"
        said.why = (f"this link is {PAGES.get(gh['page'], 'a page')} of {owner}/{repo}, not its code or a model: paste "
                    f"{home} itself, a folder in it, or one of its releases' files")
        return said
    if gh["kind"] == "file" and not DESCRIPTION.search(gh["path"]):
        said.why = (f"a single file of {owner}/{repo} ({gh['path'] or 'one file'}), not a model a reviewed runner runs: "
                    f"paste {home} or the folder holding its model description, or have an adapter written for it")
        return said
    headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    info = _github_api(f"/repos/{owner}/{repo}", http, headers)
    if info is None:
        raise CantRead(f"GitHub has no public repository {owner}/{repo}: it may be private, renamed or misspelt")
    licence = str((info.get("license") or {}).get("spdx_id") or "")
    commit, tree, truncated = "", [], False
    if gh["kind"] not in ("release", "asset"):
        ref = gh["ref"] or str(info.get("default_branch") or "HEAD")
        found = _github_api(f"/repos/{owner}/{repo}/commits/{quote(ref, safe='')}", http, headers)
        if not found or not COMMIT.fullmatch(str(found.get("sha") or "")):
            raise CantRead(f"{owner}/{repo} has no branch, tag or commit named {ref}")
        commit = found["sha"]  # a branch moves (F046): everything below is read at this commit, and kept so
        said.source = {**said.source, "git": home, "rev": commit}
        said.version = f"commit {commit[:12]}"
        listing = _github_api(f"/repos/{owner}/{repo}/git/trees/{commit}?recursive=1", http, headers) or {}
        tree = [e for e in listing.get("tree") or [] if e.get("type") == "blob"]
        truncated = bool(listing.get("truncated"))
    where = gh["path"] if gh["kind"] == "file" else gh["path"].rstrip("/")
    seen = _Read(commit=commit, partial=truncated, where=where, kind=gh["kind"])
    inside = [e for e in tree if DESCRIPTION.search(e["path"])
              and (not where or e["path"] == where or e["path"].startswith(where + "/"))]
    inside.sort(key=lambda e: (e["path"].count("/"), not re.search(r"(?:^|/)(?:rdf|bioimageio)\.yaml$", e["path"]), e["path"]))
    seen.files = len([e for e in tree if not where or e["path"] == where or e["path"].startswith(where + "/")])
    seen.descriptions = len(inside)
    assets: list[tuple[dict, dict]] = []
    if gh["kind"] in ("repository", "release", "asset"):
        assets = _release_zips(gh, http, headers, seen)
    candidates = [("description", e["path"], lambda e=e: _github_folder_model(gh, info, commit, e["path"], tree, http, headers))
                  for e in inside]
    candidates += [("zip", f"{rel['tag_name']}/{a['name']}", lambda a=a, rel=rel: _release_model(gh, info, rel, a, http))
                   for rel, a in assets]
    # One at a time, in order (the folder's own, then the newest release's), until one runs: never more than
    # MAX_CANDIDATES, within the recognition's budget.
    results, chosen = [], None
    for what, label, read in candidates:
        if seen.descriptions_read + seen.zips_read >= MAX_CANDIDATES:
            break
        try:
            found = read()
        except OverBudget as exc:
            seen.stopped = str(exc)
            break
        except CantRead as exc:
            found = Found("unsupported", why=str(exc))
        except Exception as exc:  # noqa: BLE001 - a malformed description is one candidate's outcome, never the link's
            found = Found("unsupported", why=malformed(exc))
        if what == "zip":
            seen.zips_read += 1
        else:
            seen.descriptions_read += 1
        if found is None:
            continue  # a zip with no model description in it
        results.append((label, found))
        if found.kind == "template":
            chosen = found
            break
    if chosen:
        others = [f"{label} ({f.why})" for label, f in results if f is not chosen]
        if others:
            chosen.why += (f". Read before it: {len(others)} other model description{'s' if len(others) > 1 else ''}, "
                           f"not run: " + "; ".join(others[:4]) + ("…" if len(others) > 4 else ""))
        return chosen
    pinned = f"at commit {commit[:7]}" if commit else "in its releases"
    if results:
        kinds = {f.kind for _label, f in results}
        said.kind = "needs-code" if "needs-code" in kinds else "unsupported"
        listed = "; ".join(f"{label}: {f.why}" for label, f in results[:4]) + ("…" if len(results) > 4 else "")
        said.why = (f"{owner}/{repo} {pinned}: {len(results)} bioimage.io model description"
                    f"{'s' if len(results) > 1 else ''} read, none a reviewed runner can run: {listed}. "
                    f"{seen.words()}")
        return said
    said.why = _github_facts(gh, licence, tree, seen)
    return said


class OverBudget(CantRead):
    """One recognition's time or bytes ran out: what was read so far is said, never more."""


def malformed(exc: Exception) -> str:
    """A description that isn't the shape the bioimage.io spec gives, in plain words."""
    detail = str(exc).strip().splitlines()[0][:80] if str(exc).strip() else ""
    return ("its model description is malformed where the app reads it "
            f"({type(exc).__name__}{': ' + detail if detail else ''}), so it can't be checked or run")


@dataclass
class _Read:
    """What one GitHub link's recognition actually read, for its outcome's words (never claim what was not read)."""
    commit: str = ""
    partial: bool = False  # GitHub listed only part of the files
    where: str = ""
    kind: str = ""
    files: int = 0
    descriptions: int = 0
    descriptions_read: int = 0
    releases: int = 0
    releases_capped: bool = False  # only the newest RELEASES_LISTED were listed
    zips: int = 0
    zips_read: int = 0
    other_assets: int = 0
    stopped: str = ""

    def complete(self) -> bool:
        return (self.kind == "repository" and not self.partial and not self.releases_capped and not self.stopped
                and self.descriptions_read == self.descriptions and self.zips_read == self.zips)

    def words(self) -> str:
        parts = []
        if self.commit:
            scope = f"the {self.files} files under {self.where}" if self.where else f"its {self.files} files"
            parts.append(f"{scope} at commit {self.commit[:7]}" + (" (GitHub listed only part of them)" if self.partial else ""))
        if self.kind in ("folder", "file"):
            parts.append(f"not its releases (this link is to a {self.kind})")
        elif self.kind in ("repository", "release", "asset"):
            if not self.releases:
                parts.append("no releases" if self.kind == "repository" else "no release")
            else:
                newest = (f" (only the {RELEASES_LISTED} newest were listed)" if self.releases_capped else "")
                rel = f"{self.releases} release{'s' if self.releases != 1 else ''}{newest}"
                parts.append(f"{rel}, none with a zip file" if not self.zips else
                             f"{self.zips_read} of the {self.zips} zip file{'s' if self.zips != 1 else ''} in {rel}")
                if self.other_assets:
                    parts.append(f"not its {self.other_assets} other release file{'s' if self.other_assets != 1 else ''} "
                                 "(packages are read from zips)")
        if self.descriptions > self.descriptions_read:
            parts.append(f"{self.descriptions_read} of its {self.descriptions} model descriptions")
        if self.stopped:
            parts.append(f"then stopped: {self.stopped}")
        return "Read: " + "; ".join(parts) + "."


RELEASES_LISTED = 20


def _github_api(path: str, http, headers: dict) -> dict | list | None:
    """One read of GitHub's API: its JSON, None when there is nothing there (404); else CantRead, in plain words."""
    r = _open(GITHUB_API + path, http, headers=headers, cap=API_MAX)
    if r.status_code == 404:
        return None
    if r.status_code in (403, 429) and str(r.headers.get("x-ratelimit-remaining", "")) == "0":
        raise CantRead("GitHub's hourly limit for reading without signing in was reached: add the link again within the hour")
    if r.status_code >= 400:
        raise CantRead(f"GitHub answered {r.status_code} when asked about {path.split('?')[0]}")
    return r.json()


def _release_zips(gh: dict, http, headers: dict, seen: "_Read") -> list[tuple[dict, dict]]:
    """(release, asset) for each zip its link names: one asset, one release's, or the repository's newest releases'
    (`seen` records how many releases and files were listed, and what was left out)."""
    owner, repo = gh["owner"], gh["repo"]
    if gh["kind"] == "asset" or gh["tag"] not in ("", "latest"):
        one = _github_api(f"/repos/{owner}/{repo}/releases/tags/{quote(gh['tag'], safe='')}", http, headers)
        if one is None:
            raise CantRead(f"{owner}/{repo} has no release {gh['tag']}")
        releases = [one]
    elif gh["tag"] == "latest":
        one = _github_api(f"/repos/{owner}/{repo}/releases/latest", http, headers)
        releases = [one] if one else []
    else:
        releases = _github_api(f"/repos/{owner}/{repo}/releases?per_page={RELEASES_LISTED}", http, headers) or []
        seen.releases_capped = len(releases) >= RELEASES_LISTED
    named = [(rel, a) for rel in releases for a in rel.get("assets") or []
             if gh["kind"] != "asset" or a.get("name") == gh["asset"]]
    out = [(rel, a) for rel, a in named if str(a.get("name", "")).lower().endswith(".zip")]
    seen.releases, seen.zips, seen.other_assets = len(releases), len(out), len(named) - len(out)
    if gh["kind"] == "asset" and not out:
        raise CantRead(f"{owner}/{repo}'s release {gh['tag']} has no zip named {gh['asset']} (packages are read from zips)")
    return out


def _github_folder_model(gh: dict, info: dict, commit: str, path: str, tree: list[dict], http, headers: dict) -> Found:
    """A model description in the repository's own files, every file it names pinned at the commit (F045: a Git LFS
    pointer is followed to its object by the hash it states; a file missing at the commit is said)."""
    owner, repo = gh["owner"], gh["repo"]
    home = f"https://github.com/{owner}/{repo}"
    folder = posixpath.dirname(path)
    raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}"
    sizes = {e["path"]: int(e.get("size") or 0) for e in tree}
    raw = _get(f"{raw_base}/{quote(path)}", http)
    fetched: dict[str, bytes] = {}

    def pin(source: str, sha: str | None) -> dict:
        if re.match(r"https?://", str(source)):
            return _pinned(str(source).rsplit("/", 1)[0], str(source), sha, http, fetched)
        rel = posixpath.normpath(posixpath.join(folder, str(source)))
        name = Path(str(source)).name
        if rel not in sizes:
            raise CantRead(f"its {source} is named by {path} but isn't in {owner}/{repo} at {commit[:7]}: it is made or "
                           "kept elsewhere (a build step, a submodule or a download)")
        url, size = f"{raw_base}/{quote(rel)}", sizes[rel]
        if size < 1024 or not sha:
            data = _get(url, http)
            lfs = LFS_POINTER.search(data[:400])
            if lfs:  # the file is in Git LFS: its object, by the hash the pointer states
                oid, size = lfs.group(1).decode(), int(lfs.group(2))
                if sha and oid != str(sha):
                    raise CantRead(f"its {source} in Git LFS isn't the file {path} pins (its sha256 differs)")
                return {"path": name, "bytes": size, "sha256": oid,
                        "url": f"https://media.githubusercontent.com/media/{owner}/{repo}/{commit}/{quote(rel)}"}
            if sha and hashlib.sha256(data).hexdigest() != str(sha):
                raise CantRead(f"its {source} at {commit[:7]} isn't the file {path} pins (its sha256 differs)")
            return {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "url": url}
        return {"path": name, "bytes": size, "sha256": str(sha), "url": url}

    page = f"{home}/tree/{commit}/{folder}".rstrip("/")
    found = Found("unsupported", link=page, licence=str((info.get("license") or {}).get("spdx_id") or ""))
    found.version = f"github {owner}/{repo}@{commit[:12]} {path}"
    found.source = {"git": home, "rev": commit, "url": f"{home}/blob/{commit}/{path}"}
    rdf_file = {"path": "rdf.yaml", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                "url": f"{raw_base}/{quote(path)}"}
    found = _description(found, raw, rdf_file, pin,
                         lambda name: _get(f"{raw_base}/{quote(posixpath.normpath(posixpath.join(folder, name)))}", http,
                                           cap=COVER_MAX))
    found.name = f"{repo} {found.name}".replace("_", " ").strip()
    found.id_hint = f"{repo}-{found.name.split(' ', 1)[-1]}"
    if found.kind == "template":
        found.why += f", pinned at commit {commit[:7]} of {owner}/{repo}"
    return found


def _release_model(gh: dict, info: dict, release: dict, asset: dict, http) -> Found | None:
    """A model description inside one of a release's zips (a bioimage.io package), read by byte ranges; each file it
    names pinned as a member of that zip, the zip by the sha256 GitHub states for it. None: the zip holds none."""
    import zipfile

    owner, repo = gh["owner"], gh["repo"]
    url, size = str(asset.get("browser_download_url") or ""), int(asset.get("size") or 0)
    if not url or not size:
        return None
    remote = _Remote(url, size, http, asset.get("name", "its zip"))
    try:
        z = zipfile.ZipFile(remote)
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    named = sorted((n for n in z.namelist() if DESCRIPTION.search(n)), key=lambda n: (n.count("/"), len(n)))
    if not named:
        return None
    path = named[0]
    raw = _member_bytes(z, path, 1_000_000, f"its {path} in {asset['name']} is too big to be a model description")
    root = posixpath.dirname(path)
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", digest[7:]):
        whole = digest[7:]
    elif size <= 5 * SMALL:  # an older asset GitHub states no hash for: read whole once, and pinned by what it is
        whole = hashlib.sha256(_get(url, http, cap=size)).hexdigest()
    else:
        raise CantRead(f"GitHub states no hash for {asset['name']}, and it is too big to read now to pin it")
    archive = {"sha256": whole, "bytes": size}

    def member(source: str) -> str:
        name = posixpath.normpath(posixpath.join(root, str(source)))
        try:
            z.getinfo(name)
        except KeyError:
            raise CantRead(f"its {source} is named by its description but isn't in {asset['name']}") from None
        return name

    def pin(source: str, sha: str | None) -> dict:
        if re.match(r"https?://", str(source)):
            return _pinned(str(source).rsplit("/", 1)[0], str(source), sha, http, {})
        name = member(source)
        info_ = z.getinfo(name)
        if not sha:
            sha = hashlib.sha256(_member_bytes(z, name, SMALL, f"its {source} states no hash and is too big to read now")).hexdigest()
        return {"path": Path(str(source)).name, "bytes": info_.file_size, "sha256": str(sha), "url": url,
                "archive": {**archive, "member": name}}

    tag = str(release.get("tag_name") or "")
    page = f"https://github.com/{owner}/{repo}/releases/tag/{quote(tag)}"
    found = Found("unsupported", link=page, licence=str((info.get("license") or {}).get("spdx_id") or ""))
    found.version = f"github {owner}/{repo} release {tag}: {asset['name']}"
    found.source = {"git": f"https://github.com/{owner}/{repo}", "url": url}
    rdf_file = {"path": "rdf.yaml", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "url": url,
                "archive": {**archive, "member": path}}
    found = _description(found, raw, rdf_file, pin,
                         lambda name: _member_bytes(z, member(name), COVER_MAX, f"its picture {name} is too big"))
    found.name = f"{repo} {found.name}".replace("_", " ").strip()
    found.id_hint = f"{repo}-{found.name.split(' ', 1)[-1]}"
    if found.kind == "template":
        found.why += f", from {asset['name']} of {owner}/{repo}'s release {tag}, pinned by its sha256"
    return found


def _member_bytes(z, name: str, cap: int, too_big: str) -> bytes:
    """One member of a zip, read only when both its declared and its actual size are within `cap`."""
    if z.getinfo(name).file_size > cap:
        raise CantRead(too_big)
    with z.open(name) as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise CantRead(too_big)
    return data


class _Remote(io.RawIOBase):
    """A file on the web read as a seekable file, by HTTP range requests, block by block: zipfile reads a release's zip
    through it (its directory at the end and the members asked for), never the whole of it."""

    def __init__(self, url: str, size: int, http, name: str = ""):
        super().__init__()
        self.url, self.size, self.http, self.name_, self.pos, self.blocks = url, size, http, name, 0, {}

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=0):
        self.pos = max(0, offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset)
        return self.pos

    def readinto(self, buffer):
        n = min(len(buffer), self.size - self.pos)
        if n <= 0:
            return 0
        first, last = self.pos // RANGE_BLOCK, (self.pos + n - 1) // RANGE_BLOCK
        data = b"".join(self._block(i) for i in range(first, last + 1))
        start = self.pos - first * RANGE_BLOCK
        buffer[:n] = data[start:start + n]
        self.pos += n
        return n

    def _block(self, i: int) -> bytes:
        if i not in self.blocks:
            a, b = i * RANGE_BLOCK, min(self.size, (i + 1) * RANGE_BLOCK) - 1
            r = _open(self.url, self.http, headers={"Range": f"bytes={a}-{b}"}, cap=max(RANGE_BLOCK, self.size if
                      self.size <= RANGE_BLOCK * 4 else RANGE_BLOCK), timeout=60)
            if r.status_code == 200 and len(r.content) == self.size:  # no ranges here: the whole file, kept in blocks
                for j in range(0, self.size, RANGE_BLOCK):
                    self.blocks[j // RANGE_BLOCK] = r.content[j:j + RANGE_BLOCK]
            elif r.status_code == 206 and len(r.content) == b - a + 1:
                self.blocks[i] = r.content
            else:
                raise CantRead(f"couldn't read {self.name_} (GitHub answered {r.status_code})")
        return self.blocks[i]


def _github_facts(gh: dict, licence: str, tree: list[dict], seen: "_Read") -> str:
    """What a repository holds (facts, each with the file that says it), exactly what was read, and what it lacks."""
    owner, repo = gh["owner"], gh["repo"]
    paths = [e["path"] for e in tree]
    where = gh["path"].rstrip("/")
    if where:
        paths = [p for p in paths if p.startswith(where + "/")]
    root = {p.split("/")[-1] for p in paths if p.count("/") == (where.count("/") + 1 if where else 0)}
    held = []
    package = next((f for f in ("pyproject.toml", "setup.py", "setup.cfg") if f in root), "")
    if package:
        held.append(f"a Python package ({package})")
    napari = next((p for p in paths if p.endswith("napari.yaml")), "") or next(
        (".napari/" for p in paths if p.startswith(".napari/")), "")
    if napari:
        held.append(f"a napari plugin, whose widgets are made for napari's window ({napari})")
    notebooks = [p for p in paths if p.endswith(".ipynb")]
    if notebooks:
        held.append(f"{len(notebooks)} notebook{'s' if len(notebooks) > 1 else ''}")
    weights = [p for p in paths if p.lower().endswith(WEIGHT_SUFFIXES)]
    if weights:
        held.append(f"{len(weights)} file{'s' if len(weights) > 1 else ''} that may be weights "
                    f"({', '.join(Path(p).name for p in weights[:3])}{'…' if len(weights) > 3 else ''}) with no "
                    "description of how to run them")
    if ".gitmodules" in {p.split("/")[-1] for p in paths}:
        held.append("Git submodules (.gitmodules), whose code isn't in it")
    lic = (f"its licence {licence}" if licence and licence != "NOASSERTION" else
           "a licence GitHub can't name" if licence else "no licence stated")
    name = f"{owner}/{repo}'s {where}" if where else f"{owner}/{repo}"
    holds = f" holds {', '.join(held)};" if held else ":"
    if seen.complete():
        lacks = ("It has no bioimage.io model description (rdf.yaml or bioimageio.yaml) in its files or releases, the one "
                 "kind of GitHub source a reviewed runner runs, so an adapter has to be written and checked to connect it")
    else:
        lacks = ("No bioimage.io model description (rdf.yaml or bioimageio.yaml), the one kind of GitHub source a reviewed "
                 "runner runs, was in what was read. One may be in what wasn't: paste the folder or the release file "
                 "that holds it; else an adapter has to be written and checked to connect it")
    return f"{name}{holds} {lic}. {seen.words()} {lacks}"


def recognise(link: str, http: Callable | None = None, token: str = "", github_token: str = "") -> Found:
    """What a pasted link is (see the module's docstring). `http` is the guarded network (a stand-in in tests); every read
    of one recognition shares one time and byte budget."""
    http = http or _http()
    if _BUDGET.get() is None:
        held = _BUDGET.set(_Budget())
        try:
            return recognise(link, http, token, github_token)
        finally:
            _BUDGET.reset(held)
    nick = zoo_id(link)
    if nick:
        return zoo(nick, http, zoo_version(link, nick))
    if re.match(r"https?://(www\.)?huggingface\.co/", link):
        from .mine import _hf_parts

        repo, revision, _file = _hf_parts(link)
        found = hf(repo, revision, http, token=token)
        return found if found is not None else Found("elsewhere", why="a Hugging Face model: added as a Cellpose model file")
    if github(link):
        return from_github(link, http, token=github_token)
    return Found("unsupported", link=link, why="not a model page the app knows how to read")


# --- what a link is called -------------------------------------------------------------------------------------------
COMMIT = re.compile(r"[0-9a-f]{40}")
REPO_PART = re.compile(r"[A-Za-z0-9_.-]+")


def github(link: str) -> dict | None:
    """What a GitHub link points to (F043: a repository, a README, an issue and a release are different things), as
    {"owner", "repo", "kind", "ref", "path", "tag", "asset", "page"}. Its `kind`:
    - "repository": the repository, at its default branch, or at a branch or commit (`/tree/<ref>`, `/commit/<sha>`);
    - "folder": a folder in it (`/tree/<ref>/<path>`);
    - "file": one file (`/blob/<ref>/<path>`, `/raw/...`, `raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>`);
    - "release": its releases (`/releases`), the latest (`tag` "latest") or one (`/releases/tag/<tag>`);
    - "asset": one release's file (`/releases/download/<tag>/<asset>`);
    - "page": anything else of it (an issue, a pull request, its wiki...), named by `page`.
    None for any other link."""
    parts = urlsplit((link or "").strip())
    host = (parts.hostname or "").lower()
    bits = [unquote(b) for b in parts.path.split("/") if b]
    out = {"kind": "repository", "ref": "", "path": "", "tag": "", "asset": "", "page": ""}
    if host in ("github.com", "www.github.com") and len(bits) >= 2:
        owner, repo = bits[0], bits[1].removesuffix(".git")
        section = bits[2] if len(bits) >= 3 else ""
        if section in ("tree", "blob", "raw", "commit") and len(bits) >= 4:
            out.update(ref=bits[3], path="/".join(bits[4:]))
            out["kind"] = ("file" if section in ("blob", "raw") else "folder" if section == "tree" and out["path"]
                           else "repository")
        elif section == "releases":
            rest = bits[3:]
            if len(rest) >= 3 and rest[0] == "download":
                out.update(kind="asset", tag=rest[1], asset="/".join(rest[2:]))
            elif len(rest) >= 2 and rest[0] == "tag":
                out.update(kind="release", tag=rest[1])
            else:
                out.update(kind="release", tag="latest" if rest[:1] == ["latest"] else "")
        elif section:
            out.update(kind="page", page=section)
    elif host == "raw.githubusercontent.com" and len(bits) >= 3:
        owner, repo, rest = bits[0], bits[1], bits[2:]
        if rest[0] == "refs" and len(rest) >= 3 and rest[1] in ("heads", "tags"):
            rest = rest[2:]
        out.update(kind="file", ref=rest[0], path="/".join(rest[1:]))
    else:
        return None
    if not (REPO_PART.fullmatch(owner) and REPO_PART.fullmatch(repo)):
        return None
    return {"owner": owner, "repo": repo, **out}


def link_name(link: str) -> str:
    """What an entry or a draft made from a link is called: its repository's name (a GitHub or Hugging Face link, at
    whatever commit or file it points to: "napari-segment-blobs-and-things-with-membranes", never the commit's hash or
    one of its files), else the link's last part that is not a hash."""
    gh = github(link)
    if gh:
        return gh["repo"]
    hf_repo = re.match(r"https?://(?:www\.)?huggingface\.co/([^/?#]+)/([^/?#]+)", link or "")
    if hf_repo:
        return hf_repo.group(2)
    bits = [unquote(b) for b in urlsplit((link or "").strip()).path.split("/") if b]
    named = [b for b in bits if not re.fullmatch(r"[0-9a-f]{7,64}", b)]
    if named:
        return named[-1]
    return (urlsplit((link or "").strip()).hostname or "").removeprefix("www.")


def link_source(link: str) -> dict:
    """An entry's `source` for a link the app can't run yet: the link itself, and for a GitHub link its repository and,
    when the link names one, the commit it is pinned at (`rev`; a branch is not pinned)."""
    source = {"url": link}
    gh = github(link)
    if gh:
        source["git"] = f"https://github.com/{gh['owner']}/{gh['repo']}"
        if COMMIT.fullmatch(gh["ref"]):
            source["rev"] = gh["ref"]
    return source


def template_folder(template: str) -> Path:
    return TEMPLATES / template


def reviewed_template(m: catalog.Manifest) -> str:
    """The reviewed template an entry runs, when its adapter and lockfile are still byte for byte the template's, now or
    at an earlier reviewed version (`earlier` in its template.toml, by sha256); "" when one was changed: then it is code
    its owner added."""
    import tomllib

    folder = Path(m.folder or "")
    for tpl in sorted(TEMPLATES.glob("*/template.toml")) if TEMPLATES.is_dir() else ():
        d = tpl.parent
        try:
            spec = tomllib.loads(tpl.read_text())
            files = ("adapter.py", "env.lock", *spec.get("entry_files", []))
            if all((folder / f).read_bytes() == (d / f).read_bytes() for f in files):
                return d.name
            held = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in map(folder.joinpath, files) if f.is_file()}
            if any(e.get("sha256") == held for e in spec.get("earlier", [])):
                return d.name
        except (OSError, ValueError):
            continue
    return ""
