"""Share a capability as a file, with no account (docs/design/2026-10-13-capabilities-for-everyone.md, C5).

**Sharing** (`export`): a Python adapter (format 1), a proven Fiji function (format 2), or a bundle (format 3) becomes one small file,
`<id>.observationui.zip`: its manifest, its adapter, its lockfile and its picture, laid out as a catalog
(`catalog/models/<id>/…`), with `SHARED.json` saying who shared it, when, and its proof here. Never its weights: each is
fetched from where its manifest pins it, checked by its hash, so an entry whose weights are only on this Mac (a file
added from a path) is refused with what to do. The file goes any way people send files: AirDrop, email, a forum post.

**Opening one** (`open_file`, through Add your own: dropped on the Models page, or its path pasted) makes it a **community
catalog of its own**: its validated contents are saved as an immutable, content-addressed channel snapshot, without
Git or other developer tools. Previously imported Git-backed shares remain readable. Everything the channels already guarantee holds: it is "community, not
reviewed here", nothing of its code runs until the user allows code from it (its Allow names who shared it), and then
only after its own Install and proof. Removing the channel takes it away. Only what a catalog entry may hold is read from
the file (`ALLOWED`); anything else, a link, a path out of its folder, or a file too big refuses the whole file.
"""
from __future__ import annotations

import functools
import hashlib
import io
import json
import os
import re
import struct
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog
from gpu_worker import wheel_recipe

from . import bounded_files
from .bundle_admission import integration_problem

SUFFIX = ".observationui.zip"
FORMAT = 1
FIJI_FORMAT = 2
BUNDLE_FORMAT = 3
WHEEL_FORMAT = 4
BUNDLE_FORMATS = (BUNDLE_FORMAT, WHEEL_FORMAT)
RUNNERS = {FORMAT: "python-env", FIJI_FORMAT: "fiji", BUNDLE_FORMAT: "python-env", WHEEL_FORMAT: "python-env"}
ALLOWED = {catalog.MANIFEST, "adapter.py", "env.lock", "requirements.in", "README.md"}
ATTRIBUTION = {name + suffix for name in ("license", "licence", "notice", "copying") for suffix in ("", ".txt", ".md", ".rst")}
MAX_FILE = 2 * 1024 * 1024  # each file in a share (weights never travel in one)
MAX_TOTAL = 10 * 1024 * 1024
MAX_ENTRIES = 256
MAX_ARCHIVE = MAX_TOTAL + wheel_recipe.MAX_BYTES + 1024 * 1024


def _format(m):
    return (FIJI_FORMAT if m.runner == 'fiji' else WHEEL_FORMAT if m.requires.get('catalog') == 4
            else BUNDLE_FORMAT if m.members else FORMAT)


def declared_wheels(raw, format_):
    """Verified declarations granting only named wheel files a larger byte bound."""
    try:
        manifest = tomllib.loads(raw.decode('utf-8'))
        wheels = wheel_recipe.declared(manifest.get('env') or {})
        if wheels and (format_ != WHEEL_FORMAT or manifest.get('requires', {}).get('catalog') != 4):
            raise ValueError('Portable wheels require share and catalog format 4')
        return wheels
    except (ValueError, UnicodeError, TypeError, AttributeError) as error:
        raise CantShare(str(error)) from error


def _verify_wheels(files, wheels, lock_name):
    if not wheels:
        return
    try:
        for wheel in wheels:
            wheel.verify(files[wheel.file])
        wheel_recipe.resolve(files[lock_name].decode('utf-8'), wheels,
                             {wheel.file: 'verified-artifact' for wheel in wheels})
    except (KeyError, ValueError, UnicodeError) as error:
        raise CantShare(f'Invalid portable wheel recipe: {error}') from error


class CantShare(RuntimeError):
    """What could not be shared or opened, in plain words."""


def root(environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.catalog_root(environ) / "shared"


def _allowed(name: str) -> bool:
    if name in ALLOWED or name.lower() in ATTRIBUTION or (name.startswith("example") and name.lower().endswith(catalog.EXAMPLE_SUFFIXES)):
        return True
    # Reviewed templates declare their adapter's sibling data. Preserve those exact names through both directions;
    # arbitrary local state, subdirectories and weights still do not belong in a portable entry. Any reviewed
    # template's declared name is admitted for any entry: it is a data file name beside an adapter that is code anyway.
    from .link_recognise import TEMPLATES

    return name in _template_files(Path(TEMPLATES)) and Path(name).name == name and not name.startswith(".")


def _declared_files(raw: bytes, format_: int, id_: str) -> frozenset[str] | None:
    try:
        manifest = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise CantShare("the shared manifest is not valid TOML") from exc
    if manifest.get("id") != id_ or manifest.get("runner") != RUNNERS[format_]:
        raise CantShare("the share format, runner and manifest identity do not agree")
    requires = manifest.get("requires", {})
    bundled = (isinstance(requires, dict) and requires.get("catalog") in (3, 4)) or "members" in manifest
    if bundled != (format_ in BUNDLE_FORMATS):
        raise CantShare("bundle declarations require share format 3")
    if isinstance(requires, dict) and requires.get('catalog') == 4 and format_ != WHEEL_FORMAT:
        raise CantShare('Catalog format 4 requires share format 4')
    wheels = declared_wheels(raw, format_)
    if format_ in BUNDLE_FORMATS:
        from types import SimpleNamespace
        from gpu_worker.bundle_contract import parse_members

        try:
            members = parse_members(manifest.get("members"), SimpleNamespace(
                key=id_, runner=manifest.get("runner"), requires=requires))
        except (ValueError, TypeError) as exc:
            raise CantShare(f"invalid bundle declarations: {exc}") from exc
        env = manifest.get("env")
        lock = env.get("lock") if isinstance(env, dict) else None
        if not isinstance(lock, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.lock", lock):
            raise CantShare("a shared bundle needs its declared lockfile beside its manifest")
        names = {catalog.MANIFEST, lock}
        names.update(wheel.file for wheel in wheels)
        for member in members:
            names.add(member.adapter.partition(":")[0].replace(".", "/") + ".py")
            names.update(test["input"] for test in member.tests)
            for case in member.reader_checks.get('cases', []):
                names.update(case['files'].values())
                names.add(case['reference'])
            for case in member.reference_cases:
                names.update((case["input"], case["reference"]))
        example = manifest.get("example")
        if example:
            if (not isinstance(example, str) or not re.fullmatch(r"example[A-Za-z0-9_.-]*", example)
                    or not example.lower().endswith(catalog.EXAMPLE_SUFFIXES)):
                raise CantShare("a bundle example must be a picture beside its manifest")
            names.add(example)
        return frozenset(names)
    if format_ == FORMAT:
        return None
    fiji = manifest.get("fiji")
    if not isinstance(fiji, dict) or not isinstance(fiji.get("command"), str) or not fiji["command"]:
        raise CantShare("a Fiji share must declare its reference command")
    names = {catalog.MANIFEST}
    script = fiji.get("script")
    if script is not None:
        if not isinstance(script, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\.groovy", script):
            raise CantShare("the declared Fiji script must be one Groovy file beside its manifest")
        names.add(script)
    tests = manifest.get("tests", [])
    if not isinstance(tests, list) or any(not isinstance(t, dict) for t in tests):
        raise CantShare("Fiji examples must be declared tests")
    examples = [t.get("input") for t in tests]
    if manifest.get("example"):
        examples.append(manifest["example"])
    for name in examples:
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", name)
                or not name.lower().endswith(catalog.EXAMPLE_SUFFIXES)):
            raise CantShare("a Fiji example must be a declared picture beside its manifest")
        names.add(name)
    return frozenset(names)


def _package_file(name: str, declared: frozenset[str] | None) -> bool:
    if declared is None:
        return _allowed(name)
    if name in declared or name == "README.md" or name.lower() in ATTRIBUTION:
        return True
    if name == "requirements.in" and any(path.endswith(".py") for path in declared):
        return True
    return name.endswith("/__init__.py") and any(
        path.startswith(name.removesuffix("__init__.py")) and path.endswith(".py") for path in declared)


def _read_relative(directory: int, name: str, maximum=MAX_FILE) -> bytes:
    parents = []
    try:
        for part in name.split("/")[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            parents.append(directory)
        return bounded_files.read_at(directory, name.split("/")[-1], maximum)
    finally:
        for descriptor in reversed(parents):
            os.close(descriptor)


def _open_directory(path: Path) -> int:
    return bounded_files.open_directory(path)


@functools.lru_cache(maxsize=4)
def _template_files(templates: Path) -> frozenset[str]:
    """Every reviewed template's declared entry data files (template.toml `entry_files`), read once per templates folder:
    they are part of the app's own source and don't change while it runs."""
    import tomllib

    names: set[str] = set()
    for path in templates.glob("*/template.toml"):
        try:
            names.update(str(n) for n in tomllib.loads(path.read_text()).get("entry_files", []))
        except (OSError, ValueError):
            continue
    return frozenset(names)


def export(key: str, by: str = "", environ: Mapping[str, str] = os.environ) -> Path:
    """The entry `key` as a share file (see the module's docstring); its path."""
    m = catalog.get(key)
    if m is None:
        raise CantShare(f"there is no entry called {key}")
    from . import env_runner
    if m.runner == "fiji":
        from . import fiji_runner, fiji_sandbox
        if not fiji_sandbox.supported():
            raise CantShare("Fiji sharing needs the supported OS sandbox on this computer")
        installed = fiji_runner.in_use(m, environ)
        if installed is None:
            raise CantShare("Turn on and check this Fiji capability before sharing it")
        m = installed.m
    else:
        installed = env_runner.in_use(m, environ)
        if m.members and installed is not None:
            m = installed.m
    if problem := integration_problem(m):
        raise CantShare(problem)
    data = pack(m, by=by, proof=installed.proof if installed is not None else {})
    out = root(environ) / "outbox"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{m.id}{SUFFIX}"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def portable_files(m: catalog.Manifest, environ: Mapping[str, str] = os.environ, *, max_total: int | None = None) -> dict[str, bytes]:
    """Declared portable content for file sharing and catalog proposals; never local state."""
    if m.runner not in RUNNERS.values():
        raise CantShare(f"{m.name} runs with the app's own code: only an entry with its own adapter travels as a file "
                        "(the catalog brings the others)")
    local = [f["path"] for f in m.files if not str(f.get("url", "")).startswith("https://")
             and not (m.source.get("hf") and not f.get("url"))]
    if local:
        raise CantShare(f"its weights ({', '.join(local[:3])}) are only on this Mac: put them where others can fetch them "
                        "(a Hugging Face repository), then add it from there and share that")
    folder = catalog.entry_folder(m, environ)
    format_ = _format(m)
    files = {}
    try:
        directory = _open_directory(folder)
    except OSError as exc:
        raise CantShare("the capability folder must contain no links") from exc
    try:
        try:
            manifest = bounded_files.read_at(directory, catalog.MANIFEST, MAX_FILE)
        except (OSError, ValueError) as exc:
            raise CantShare("the manifest could not be read as a bounded regular file") from exc
        declared = _declared_files(manifest, format_, m.id)
        wheels = declared_wheels(manifest, format_)
        limits = {wheel.file: wheel.bytes for wheel in wheels}
        total_limit = MAX_TOTAL + sum(limits.values())
        if max_total is not None:
            total_limit = min(total_limit, max_total)
        candidates = set(os.listdir(directory))
        if declared is not None:
            candidates.update(declared)
        if format_ in BUNDLE_FORMATS:
            for name in declared:
                parts = name.split("/")[:-1]
                for length in range(1, len(parts) + 1):
                    parent = "/".join(parts[:length]) + "/__init__.py"
                    try:
                        _read_relative(directory, parent)
                    except FileNotFoundError:
                        continue
                    except (OSError, ValueError) as exc:
                        raise CantShare(f"{parent} must be a bounded regular file without links") from exc
                    candidates.add(parent)
        for name in sorted(candidates):
            if not _package_file(name, declared):
                continue
            try:
                allowance = min(limits.get(name, MAX_FILE), total_limit - sum(map(len, files.values())))
                data = manifest if name == catalog.MANIFEST else _read_relative(directory, name, allowance)
            except FileNotFoundError as exc:
                raise CantShare(f"{m.name} is missing declared files: {name}") from exc
            except OSError as exc:
                raise CantShare(f"{name} could not be read as a regular file (links are refused)") from exc
            except ValueError as exc:
                raise CantShare(f"{name} is not a regular file or is too big for a shared capability") from exc
            if sum(map(len, files.values())) + len(data) > total_limit:
                raise CantShare(f"{name} is too big for a shared capability")
            files[name] = data
            if len(files) + 1 > MAX_ENTRIES:
                raise CantShare("this capability has too many files for a share")
    finally:
        os.close(directory)
    if len({name.casefold() for name in files}) != len(files):
        raise CantShare("the capability has ambiguous duplicate file names")
    required = declared if declared is not None else {catalog.MANIFEST, "adapter.py"}
    if required - files.keys():
        raise CantShare(f"{m.name}'s folder is missing declared files: {', '.join(sorted(required - files.keys()))}")
    _verify_wheels(files, wheels, m.env.get('lock'))
    return files


def pack(m: catalog.Manifest, *, by: str = "", proof: dict | None = None, max_total: int | None = None) -> bytes:
    """A bounded portable copy of this exact manifest's folder, including an installed producer snapshot."""
    files = portable_files(m, max_total=max_total)
    format_ = _format(m)
    text = files[catalog.MANIFEST].decode()
    if by and "contributed_by" not in tomllib.loads(text):
        from .mine import _toml_value

        # Root insertion is independent of key spelling and table layout. Inspect parsed fields,
        # not comments or descriptions, and preserve any attribution the author already supplied.
        text = "contributed_by = " + _toml_value({"name": by}) + "\n" + text
    files[catalog.MANIFEST] = text.encode()
    proof = proof or {}
    shared = {"format": format_, "id": m.id, "name": m.name, "by": by or (m.contributed_by or {}).get("name", ""),
              "from": m.key,
              "proof": {k: proof.get(k) for k in ("says", "found", "at", "picture") if proof.get(k) is not None}}
    shared_text = json.dumps(shared, indent=1)
    limits = {wheel.file: wheel.bytes for wheel in declared_wheels(files[catalog.MANIFEST], format_)}
    total_limit = MAX_TOTAL + sum(limits.values())
    if max_total is not None:
        total_limit = min(total_limit, max_total)
    if (any(len(data) > limits.get(name, MAX_FILE) for name, data in files.items())
            or sum(map(len, files.values())) + len(shared_text.encode()) > total_limit):
        raise CantShare("this entry and its sharing metadata are too big for a shared capability")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data in files.items():
            z.writestr(zipfile.ZipInfo(f"catalog/models/{m.id}/{name}"), data, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr(zipfile.ZipInfo("SHARED.json"), shared_text, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def _archive_directory(data: bytes) -> int:
    end = data.rfind(b'PK\x05\x06', max(0, len(data) - 65557))
    if end < 0 or end + 22 > len(data):
        raise CantShare('The shared archive has no complete directory')
    _, disk, directory_disk, disk_count, count, size, offset, comment = struct.unpack_from('<4s4H2LH', data, end)
    if disk or directory_disk or disk_count != count or count > MAX_ENTRIES or end + 22 + comment != len(data):
        raise CantShare('The shared archive has too many files or an unsupported directory')
    if offset + size != end:
        raise CantShare('The shared archive directory is not canonical')
    cursor, actual, expanded = offset, 0, 0
    while cursor < end:
        if cursor + 46 > end or data[cursor:cursor + 4] != b'PK\x01\x02':
            raise CantShare('The shared archive directory is incomplete')
        name, extra, note = struct.unpack_from('<3H', data, cursor + 28)
        expanded += struct.unpack_from('<L', data, cursor + 24)[0]
        if expanded > MAX_TOTAL + wheel_recipe.MAX_BYTES:
            raise CantShare('The shared archive exceeds its expanded byte allowance')
        cursor += 46 + name + extra + note
        actual += 1
        if actual > MAX_ENTRIES or cursor > end:
            raise CantShare('The shared archive contains too many files or an invalid directory')
    if actual != count:
        raise CantShare('The shared archive file count does not match its directory')
    return expanded


def expanded_size(path: Path) -> int:
    """Inspect bounded directory bytes without inflating any archive member."""
    directory = _open_directory(path.parent)
    try:
        return _archive_directory(bounded_files.read_at(directory, path.name, MAX_ARCHIVE))
    finally:
        os.close(directory)


def _read(path: Path) -> tuple[dict, str, dict[str, bytes]]:
    """A share file's SHARED.json, its entry id and its files, every rule checked (see the module's docstring)."""
    try:
        directory = _open_directory(path.parent)
        try:
            data = bounded_files.read_at(directory, path.name, MAX_ARCHIVE)
        finally:
            os.close(directory)
        _archive_directory(data)
        z = zipfile.ZipFile(io.BytesIO(data))
    except ValueError as exc:
        raise CantShare("The shared archive is not a regular file or is too big") from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise CantShare(f"{path.name} isn't a capability someone shared (not a zip file)") from exc
    with z:
        infos = z.infolist()
        if len(infos) > MAX_ENTRIES:
            raise CantShare('The shared archive contains too many files')
        folded = []
        for info in infos:
            name = info.filename[:-1] if info.is_dir() else info.filename
            if (info.orig_filename != info.filename or "\\" in name
                    or any(part in ("", ".", "..") for part in name.split("/"))):
                raise CantShare(f"{path.name} holds a noncanonical path ({info.filename}), which no catalog entry holds: refused whole")
            folded.append(name.casefold())
        if len(set(folded)) != len(folded):
            raise CantShare(f"{path.name} holds ambiguous duplicate file names: refused whole")
        total = sum(i.file_size for i in infos)
        if total > MAX_TOTAL + wheel_recipe.MAX_BYTES:
            raise CantShare(f"{path.name} holds {total // 1_000_000} MB: a shared capability is small (weights never travel)")
        for info in infos:
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise CantShare(f"{path.name} holds a link ({info.filename}): refused whole")
            if info.file_size > wheel_recipe.MAX_BYTES:
                raise CantShare(f"{info.filename} is too big for a shared capability")
        try:
            if z.getinfo('SHARED.json').file_size > MAX_FILE:
                raise CantShare('Sharing metadata is too big')
            shared = json.loads(z.read("SHARED.json"))
        except (KeyError, ValueError) as exc:
            raise CantShare(f"{path.name} says nothing of what it is (no SHARED.json)") from exc
        if not isinstance(shared, dict):
            raise CantShare("SHARED.json must describe one capability")
        id_, format_ = str(shared.get("id", "")), shared.get("format")
        if (not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", id_)
                or type(format_) is not int or format_ not in RUNNERS):
            raise CantShare(f"{path.name} is a kind of share this app doesn't read")
        try:
            if z.getinfo(f'catalog/models/{id_}/{catalog.MANIFEST}').file_size > MAX_FILE:
                raise CantShare('Shared manifest is too big')
            manifest = z.read(f"catalog/models/{id_}/{catalog.MANIFEST}")
        except KeyError as exc:
            raise CantShare(f"{path.name} has no manifest") from exc
        declared = _declared_files(manifest, format_, id_)
        wheels = declared_wheels(manifest, format_)
        limits = {wheel.file: wheel.bytes for wheel in wheels}
        if total > MAX_TOTAL + sum(limits.values()):
            raise CantShare('The shared capability exceeds its declared size allowance')
        files = {}
        for i in infos:
            if i.is_dir() or i.filename == "SHARED.json":
                continue
            parts = Path(i.filename).parts
            relative = "/".join(parts[3:])
            if (len(parts) < 4 or (format_ not in BUNDLE_FORMATS and len(parts) != 4)
                    or parts[:3] != ("catalog", "models", id_) or not _package_file(relative, declared)):
                raise CantShare(f"{path.name} holds {i.filename}, which no catalog entry holds: refused whole")
            if i.file_size > limits.get(relative, MAX_FILE):
                raise CantShare(f'{relative} is too big for its declared file allowance')
            files[relative] = z.read(i)
    required = declared if declared is not None else {catalog.MANIFEST, "adapter.py"}
    if required - files.keys():
        raise CantShare(f"{path.name} is missing declared files: {', '.join(sorted(required - files.keys()))}")
    _verify_wheels(files, wheels, tomllib.loads(manifest.decode('utf-8')).get('env', {}).get('lock'))
    return shared, id_, files


def channel_name(id_: str, files: dict[str, bytes], format_: int = FORMAT) -> str:
    if format_ in (FIJI_FORMAT, *BUNDLE_FORMATS):
        content = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:32]
        prefix = 'fiji' if format_ == FIJI_FORMAT else 'bundle'
        return f'{prefix}-{digest}'
    digest = hashlib.sha256(b"".join(files[k] for k in sorted(files))).hexdigest()[:6]
    return f"shared-{id_[:24]}-{digest}".lower()


def open_file(path: Path, environ: Mapping[str, str] = os.environ) -> dict:
    """A share file opened (see the module's docstring): {"channel", "model", "by", "says"}."""
    from . import channels

    shared, id_, files = _read(Path(path))
    if shared["format"] == FIJI_FORMAT:
        from . import fiji_sandbox
        if not fiji_sandbox.supported():
            raise CantShare("This computer cannot safely run shared Fiji capabilities: the OS sandbox is unavailable")
    name = channel_name(id_, files, shared["format"])
    if any(c["name"] == name for c in catalog.channels(environ)):
        return {"channel": name, "model": f"{name}/{id_}", "by": shared.get("by", ""), "kind": "shared",
                "says": f"{shared.get('name') or id_} is here already, from a file shared earlier"}
    identity = hashlib.sha256(json.dumps({key: hashlib.sha256(value).hexdigest() for key, value in files.items()},
                                        sort_keys=True).encode()).hexdigest()
    try:
        by = str(shared.get("by") or "")
        staging = catalog.channels_root(environ)
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.shared-', dir=staging) as temporary:
            out = Path(temporary) / 'out'
            entry = out / id_
            entry.mkdir(parents=True)
            for fname, data in files.items():
                if fname == catalog.MANIFEST:  # keep the existing namespaced install identity
                    data = re.sub(rb"install:(?:[a-z0-9_-]+/)?" + re.escape(id_.encode()) + rb"\b",
                                  b"install:" + name.encode() + b"/" + id_.encode(), data)
                destination = entry / fname
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                destination.chmod(0o444)
            metadata = out / 'SHARED.json'
            metadata.write_text(json.dumps(shared, indent=1))
            metadata.chmod(0o444)
            channels.import_snapshot(name, identity, out,
                f"{by}'s shared file" if by else f"a shared file ({shared.get('name') or id_})", environ)
    except (channels.CantDo, OSError) as exc:
        raise CantShare(str(exc)) from exc
    by = shared.get("by") or "someone"
    preparation = ("it checks the pinned Fiji generation in an isolated process first" if shared["format"] == FIJI_FORMAT
                   else "it downloads its weights and checks itself first")
    return {"channel": name, "model": f"{name}/{id_}", "by": shared.get("by", ""), "kind": "shared",
            "says": f"Opened {shared.get('name') or id_}, shared by {by}: community, not reviewed here. Allow code from it "
                    f"on its card, then turn it on ({preparation})."}
