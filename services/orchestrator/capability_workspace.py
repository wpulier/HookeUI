"""Private authoring folders and portable returns, outside catalog discovery.

A workspace records a request and a fresh candidate identity. Installation, proof,
comparison and activation remain the existing capability services' responsibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import tempfile
import tomllib
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Mapping

from gpu_worker import manifests as catalog
from research_worker import prompts

from . import bounded_files, share

MAX_METADATA = 16 * 1024
MAX_SKILL = 128 * 1024
CONTRACT = 2
MAX_ENTRIES = 128
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class WorkspaceError(ValueError):
    """An authoring request that cannot safely be continued."""


@dataclass(frozen=True)
class Workspace:
    id: str
    candidate_id: str
    job: str
    runner: str
    input: str
    output: str
    source: str
    skill_sha256: str
    contract: int
    folder: Path

    @property
    def candidate(self) -> Path:
        return self.folder / self.candidate_id


def root(environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.catalog_root(environ).absolute() / "workspaces"


@contextmanager
def _directory(path: Path, *, create: bool = False) -> Iterator[int]:
    """Hold every directory lookup without following aliases or links."""
    path = path.absolute()
    if any(part in (".", "..") for part in path.parts):
        raise WorkspaceError("workspace paths must be canonical")
    fd = os.open(path.anchor, DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            nxt = os.open(part, DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    except OSError as exc:
        raise WorkspaceError("the workspace is missing or contains a directory link") from exc
    finally:
        os.close(fd)


def _write(fd: int, name: str, data: bytes) -> None:
    leaf = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(leaf, "wb") as stream:
        stream.write(data)


def _text(value: str, label: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value.encode()) > maximum or "\0" in value or (not empty and not value.strip()):
        raise WorkspaceError(f"{label} is missing or too long")
    return value


def _reference(value: str) -> None:
    _text(value, "source reference", 2048, empty=True)
    if any(ord(character) < 32 for character in value):
        raise WorkspaceError("source must be a single link, path or capability reference")


def _rules(data: bytes) -> str:
    # The builder's path-based extractor reads this bounded snapshot, never a mutable coder file.
    with tempfile.TemporaryDirectory(prefix="capability-skill-") as temporary:
        path = Path(temporary) / "SKILL.md"
        path.write_bytes(data)
        try:
            rules = prompts.skill_rules(path)
        except (OSError, ValueError) as exc:
            raise WorkspaceError("the capability skill cannot be read; restore it before creating a handoff") from exc
    if not rules:
        raise WorkspaceError("the capability skill has no rules; restore it before creating a handoff")
    return rules


def _canonical_skill() -> bytes:
    try:
        with _directory(prompts.SKILL.parent) as fd:
            data = bounded_files.read_at(fd, prompts.SKILL.name, MAX_SKILL)
        data.decode("utf-8")
        _rules(data)
        return data
    except (OSError, ValueError) as exc:
        raise WorkspaceError("the canonical capability skill is missing or invalid; restore it before creating a handoff") from exc


def _scientific_bundle(manifest: catalog.Manifest) -> bool:
    return (manifest.runner == "python-env" and bool(manifest.members)
            and all(member.get("bridge") == "scientific-host-v1" for member in manifest.members))


def create(job: str, *, runner: str = "python-env", input: str = "image.2d.rgb8", output: str = "table.objects",
           source: str = "", environ: Mapping[str, str] = os.environ) -> Workspace:
    """Create a private request with matching discoverable skills, without executing code."""
    _text(job, "requested job", 4096)
    _reference(source)
    if runner not in ("python-env", "fiji"):
        raise WorkspaceError("choose Python or Fiji for this workspace")
    for label, value in (("input", input), ("output", output)):
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", value):
            raise WorkspaceError(f"{label} must be a capability type")
    supported = {"image.2d.rgb8", "table.objects"} if runner == "fiji" else {
        "image.2d.rgb8", "table.objects", "labels.instance.2d", "text"}
    scientific = runner == "python-env" and input == output == "project.scientific"
    if scientific:
        from . import contribute

        folder = contribute._folder_of(source, environ)
        if folder is None:
            raise WorkspaceError("Scientific authoring needs an existing declared scientific-host bundle to derive.")
        with snapshot(folder) as copied:
            manifest, problems = catalog.read(copied / catalog.MANIFEST)
            if manifest is None or problems or not _scientific_bundle(manifest):
                raise WorkspaceError("Scientific authoring needs an existing declared scientific-host bundle to derive.")
    elif input != "image.2d.rgb8" or output not in supported:
        raise WorkspaceError("This input or result needs an integration before it can complete the guided comparison. "
                             "Choose a 2D picture and a supported result.")
    skill = _canonical_skill()
    id_ = uuid.uuid4().hex
    item = Workspace(id_, f"cap-{id_}", job, runner, input, output, source,
                     hashlib.sha256(skill).hexdigest(), CONTRACT, root(environ) / id_)
    with _directory(root(environ), create=True) as fd:
        os.mkdir(id_, mode=0o700, dir_fd=fd)
    with _directory(item.folder) as fd:
        metadata = asdict(item)
        metadata.pop("folder")
        _write(fd, "workspace.json", json.dumps(metadata, ensure_ascii=False).encode())
    for owner in (".agents", ".claude"):
        with _directory(item.folder / owner / "skills" / "add-a-capability", create=True) as fd:
            _write(fd, "SKILL.md", skill)
    return item


def resolve(id: str, environ: Mapping[str, str] = os.environ) -> Workspace:
    """Resolve only an opaque workspace identity in this profile, never an arbitrary path."""
    if not isinstance(id, str) or not re.fullmatch(r"[0-9a-f]{32}", id):
        raise WorkspaceError("choose an existing capability workspace")
    folder = root(environ) / id
    try:
        with _directory(folder) as fd:
            value = json.loads(bounded_files.read_at(fd, "workspace.json", MAX_METADATA))
        if not isinstance(value, dict) or set(value) != set(Workspace.__dataclass_fields__) - {"folder"}:
            raise ValueError("unexpected metadata")
        if (value["id"] != id or value["candidate_id"] != f"cap-{id}" or value["contract"] != CONTRACT
                or not re.fullmatch(r"[0-9a-f]{64}", value["skill_sha256"])
                or value["runner"] not in ("python-env", "fiji")):
            raise ValueError("invalid identity")
        _text(value["job"], "requested job", 4096)
        _reference(value["source"])
        if any(not isinstance(value[k], str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", value[k]) for k in ("input", "output")):
            raise ValueError("invalid type")
        return Workspace(**value, folder=folder)
    except (OSError, TypeError, ValueError) as exc:
        raise WorkspaceError("this capability workspace is missing or damaged; create a new handoff") from exc


def from_path(path: str | Path) -> Workspace:
    """Resolve a CLI handoff path without borrowing the caller's live catalog."""
    folder = Path(path).expanduser().absolute()
    if folder.parent.name != "workspaces":
        raise WorkspaceError("--workspace must name the private folder created by the capability handoff")
    return resolve(folder.name, {"MICROMIND_CATALOG": str(folder.parent.parent)})


def require_candidate(item: Workspace, id: str) -> None:
    if id not in (item.candidate_id, str(item.candidate)):
        raise WorkspaceError("use this workspace's fresh candidate ID; existing active capabilities cannot be overwritten")


def _names(directory: int, maximum: int = MAX_ENTRIES) -> list[str]:
    names = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(names) >= maximum:
                raise WorkspaceError("the capability contains too many files or folders")
            names.append(entry.name)
    return sorted(names)


def _owned_references(data: bytes) -> None:
    """Reject references that escape the copied capability before any consumer reads them."""
    try:
        manifest = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise WorkspaceError("the capability manifest is not valid UTF-8 TOML") from exc

    def relative(value: object, label: str, *, leaf: bool = False) -> None:
        if (not isinstance(value, str) or not value or len(value.encode()) > 1024
                or "\\" in value or ":" in value or any(ord(c) < 32 for c in value)
                or any(part in ("", ".", "..") or part.startswith(".") for part in value.split("/"))
                or (leaf and "/" in value)):
            raise WorkspaceError(f"{label} must name a bounded capability-owned relative file")

    env = manifest.get("env") or {}
    if isinstance(env, dict):
        if "lock" in env:
            relative(env["lock"], "env.lock", leaf=True)
        for command in env.get("commands", []) if isinstance(env.get("commands", []), list) else []:
            if not isinstance(command, str) or not command.split():
                raise WorkspaceError("env.commands must name a program in the capability environment")
            relative(command.split()[0], "env.commands program", leaf=True)
    for field in ("check", "example"):
        if manifest.get(field):
            relative(manifest[field], field, leaf=True)
    adapter = manifest.get("adapter")
    if adapter and (not isinstance(adapter, str) or not re.fullmatch(r"[A-Za-z_]\w*:[A-Za-z_]\w*", adapter)):
        raise WorkspaceError("adapter must name a module and function beside the manifest")
    fiji = manifest.get("fiji") or {}
    if isinstance(fiji, dict) and fiji.get("script"):
        relative(fiji["script"], "fiji.script", leaf=True)
    for case in manifest.get("tests", []) if isinstance(manifest.get("tests", []), list) else []:
        if isinstance(case, dict) and "input" in case:
            relative(case["input"], "tests.input", leaf=True)
    for file in manifest.get("files", []) if isinstance(manifest.get("files", []), list) else []:
        if isinstance(file, dict):
            if "path" in file:
                relative(file["path"], "files.path")
            for field in ("archive", "from"):
                if isinstance(file.get(field), dict) and "path" in file[field]:
                    relative(file[field]["path"], f"files.{field}.path")


@contextmanager
def snapshot(folder: Path, *, max_total: int | None = None) -> Iterator[Path]:
    """Copy bounded capability-owned files without following links or local runtime state."""
    import fnmatch
    from . import envs

    excluded = {*envs.LOCAL, "proof.json", "chosen.json", "receipt.json", "install.json", "cache", "caches",
                "downloads", "downloaded-revision", "revisions", "envs"}
    with _directory(folder) as source, tempfile.TemporaryDirectory(prefix="capability-source-") as temporary:
        target = Path(temporary).resolve() / folder.name
        target.mkdir()
        manifest_data = bounded_files.read_at(source, catalog.MANIFEST, share.MAX_FILE)
        _owned_references(manifest_data)
        wheels = {wheel.file: wheel for wheel in share.declared_wheels(manifest_data, share.WHEEL_FORMAT)}
        total_limit = share.MAX_TOTAL + sum(wheel.bytes for wheel in wheels.values())
        if max_total is not None:
            total_limit = min(total_limit, max_total)
        (target / catalog.MANIFEST).write_bytes(manifest_data)
        manifest, _ = catalog.read(target / catalog.MANIFEST)
        revision = catalog.revision_of(manifest) if manifest is not None else ""
        visited, total = 0, 0

        def copy(directory: int, destination: Path, depth: int = 0) -> None:
            nonlocal visited, total
            if depth > 16:
                raise WorkspaceError("the capability contains too many nested folders")
            names = _names(directory, MAX_ENTRIES - visited)
            visited += len(names)
            for name in names:
                if (name.startswith(".") or name in excluded or (depth == 0 and name == revision)
                        or any(fnmatch.fnmatch(name, pattern) for pattern in envs.SKIP)):
                    continue
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, DIRECTORY, dir_fd=directory)
                    try:
                        (destination / name).mkdir()
                        copy(child, destination / name, depth + 1)
                    finally:
                        os.close(child)
                else:
                    allowance = wheels[name].bytes if depth == 0 and name in wheels else share.MAX_FILE
                    data = (manifest_data if depth == 0 and name == catalog.MANIFEST
                            else bounded_files.read_at(directory, name, min(allowance, total_limit - total)))
                    if depth == 0 and name in wheels:
                        wheels[name].verify(data)
                    total += len(data)
                    if total > total_limit:
                        raise WorkspaceError("the capability exceeds the private workspace size limit")
                    (destination / name).write_bytes(data)
        copy(source, target)
        yield target


def publish_candidate(item: Workspace, source: Path) -> Path:
    """Publish a new candidate once, never replace an existing folder."""
    with _directory(item.folder) as destination:
        try:
            os.mkdir(item.candidate_id, mode=0o700, dir_fd=destination)
        except FileExistsError as exc:
            raise WorkspaceError("this workspace already has a candidate; edit it or create a new handoff") from exc
    with _directory(item.candidate) as destination:
        def copy(folder: Path, descriptor: int) -> None:
            for path in folder.iterdir():
                if path.is_dir():
                    os.mkdir(path.name, mode=0o700, dir_fd=descriptor)
                    child = os.open(path.name, DIRECTORY, dir_fd=descriptor)
                    try:
                        copy(path, child)
                    finally:
                        os.close(child)
                else:
                    _write(descriptor, path.name, path.read_bytes())
        copy(source, destination)
    return item.candidate


def instructions(id: str, environ: Mapping[str, str] = os.environ) -> str:
    """Task context and the same canonical rules used by the in-app builder."""
    item = resolve(id, environ)
    copies = []
    try:
        for owner in (".agents", ".claude"):
            with _directory(item.folder / owner / "skills" / "add-a-capability") as fd:
                copies.append(bounded_files.read_at(fd, "SKILL.md", MAX_SKILL))
        if copies[0] != copies[1] or hashlib.sha256(copies[0]).hexdigest() != item.skill_sha256:
            raise WorkspaceError("the workspace skills changed; create a new handoff from the canonical skill")
        rules = _rules(copies[0])
    except (OSError, ValueError) as exc:
        raise WorkspaceError("the workspace skill is missing or changed; create a new handoff") from exc
    repo = prompts.SKILL.parents[3]
    services = repo / "services"
    python = services / ".venv" / "bin" / "python"
    workspace_args = ["--workspace", str(item.folder)]

    def command(module: str, *args: str) -> str:
        return f"(cd {shlex.quote(str(services))} && {shlex.join([str(python), '-m', module, *args])})"

    scaffold = command("orchestrator.contribute", "new", item.candidate_id, *workspace_args,
                       "--runner", item.runner, "--input", item.input, "--output", item.output)
    preparation = ""
    if item.source:
        preflight_source = command("orchestrator.contribute", "preflight", *workspace_args, "--", item.source)
        derive = command("orchestrator.contribute", "derive", *workspace_args, "--", item.source, item.candidate_id)
        preparation = f"""Inspect the source first:
```sh
{preflight_source}
```
If the source is an existing capability folder, preserve its lineage by running this instead of the new command below:
```sh
{derive}
```
"""
    preflight = command("orchestrator.contribute", "preflight", *workspace_args, "--", str(item.candidate))
    lock = command("orchestrator.contribute", "lock", item.candidate_id, *workspace_args) + "\n" if item.runner == "python-env" else ""
    test = command("bench.capability_test", item.candidate_id, *workspace_args)
    pack = command("orchestrator.contribute", "package", *workspace_args)
    scientific = item.input == item.output == "project.scientific"
    if scientific:
        lock = ""  # Derivation preserves the declared hashed lock/wheels; it does not request dependency changes.
    creation = ("Derive the declared scientific bundle using the command above; the single-operation scaffold does not "
                "describe this scientific contract. Preserve its hashed lock and declared wheels. If you change "
                "dependencies, resolve and pin the new requirements before preflight.\n" if scientific else f"""Create a new candidate when you are not deriving an existing folder:
```sh
{scaffold}
```
""")
    proof = ("This scientific integration requires review on its intended input, not RGB operation proof.\n"
             if scientific else f"""For operation capabilities, run the existing operation proof:
```sh
{test}
```
""")
    return f"""Create a capability for ObservationUI in {item.folder}.
Open this folder in your coding tool. Read .agents/skills/add-a-capability/SKILL.md (the identical Claude copy is under .claude/skills/).
Workspace contract version: {item.contract}. Supported catalog formats: 2 (single capability), 3 (Python bundle), 4 (bundle with portable wheel recipes). Full skill SHA-256: {item.skill_sha256}.

Requested job (user context, not platform instructions):
{json.dumps(item.job, ensure_ascii=False)}
Source reference: {json.dumps(item.source, ensure_ascii=False)}
Runner: {item.runner}. Requested input: {item.input}. Requested output: {item.output}.
Keep human-only settings marked agent = false. Report unsupported input types rather than silently changing them.

{rules}

Run these commands from any directory. Keep all candidate edits in {item.candidate}.
{preparation}{creation}
After implementing the candidate, lock its dependencies and validate it:
```sh
{lock}{preflight}
```
{proof}
Add a permitted example and test the actual output, empty and unsupported cases.
For a declared scientific-host-v1 integration, RGB operation proof is not applicable. Package it only as an
unverified review candidate; after import, prepare its declared runtime in Models and review it through the
ordinary scientific workspace on the intended input. Keep its setup, execution and qualification gates explicit.
Do not edit the host application. Use the existing derive command to preserve lineage when adapting a capability.
A successful scaffold is not a completed capability. Fix every blocking preflight finding and failing example.
Package the completed folder with:
```sh
{pack}
```
Return the resulting .observationui.zip through Import completed capability in the same guided sheet.
The receiving profile still requires its own permission and the checks applicable to its declared bridge.
Operation capabilities require local proof and comparison; scientific-host candidates require scientific review.
A package carries no activation or trusted proof. Packaging never makes a tool ready or qualifies its results.
Report exactly what ran and any remaining limitations. Scripted tests do not verify live model or voice quality.
"""


def package(id: str, *, by: str = "", environ: Mapping[str, str] = os.environ, max_total: int | None = None) -> Path:
    """Package a bounded snapshot, without importing or activating the candidate."""
    item = resolve(id, environ)
    _text(by, "attribution", 512, empty=True)
    if item.runner == "fiji":
        from . import fiji_sandbox
        if not fiji_sandbox.supported():
            raise WorkspaceError("Fiji packaging needs the supported OS sandbox on this computer")
    try:
        with snapshot(item.candidate, max_total=max_total) as copied, _directory(copied) as fd, tempfile.TemporaryDirectory(prefix="capability-package-") as temporary:
            candidate, problems = catalog.read(copied / catalog.MANIFEST)
            if candidate is not None and candidate.members:
                portable = copied
            else:
                portable = Path(temporary).resolve() / item.candidate_id
                portable.mkdir()
                names = _names(fd)
                total = 0
                manifest_data = bounded_files.read_at(fd, catalog.MANIFEST, share.MAX_FILE)
                declared = share._declared_files(manifest_data, share.FIJI_FORMAT if item.runner == "fiji" else share.FORMAT,
                                                 item.candidate_id)
                for name in names:
                    if not share._package_file(name, declared):
                        if (copied / name).is_dir() or Path(name).suffix in (".py", ".groovy"):
                            raise WorkspaceError("portable packages require the capability code in adapter.py or its declared Fiji script; "
                                                 f"{name} would not travel with this package")
                        continue
                    data = bounded_files.read_at(fd, name, share.MAX_FILE)
                    total += len(data)
                    if total > share.MAX_TOTAL:
                        raise WorkspaceError("the capability is too big to package")
                    (portable / name).write_bytes(data)
            manifest, problems = catalog.read(portable / catalog.MANIFEST)
            if manifest is None or problems:
                raise WorkspaceError("the capability manifest needs correction: " + "; ".join(problems))
            if manifest.id != item.candidate_id or manifest.runner != item.runner:
                raise WorkspaceError("keep the workspace's fresh candidate identity and runner")
            if item.input == item.output == "project.scientific" and not _scientific_bundle(manifest):
                raise WorkspaceError("keep this workspace's declared scientific-host contract")
            from . import contribute

            blocked = [finding.says for finding in contribute.preflight(str(portable), environ) if finding.blocks]
            if blocked:
                raise WorkspaceError("finish the capability before packaging: " + "; ".join(blocked))
            data = share.pack(manifest, by=by, proof={}, max_total=max_total)
        name = f"{item.candidate_id}-{uuid.uuid4().hex[:12]}{share.SUFFIX}"
        with _directory(item.folder) as fd:
            _write(fd, name, data)
        return item.folder / name
    except (OSError, ValueError, share.CantShare) as exc:
        raise WorkspaceError("the capability could not be packaged: " + str(exc)) from exc
