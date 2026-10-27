"""Adding a capability, for people and AI coders (the skill `add-a-capability`; ADR 0024's one door): one way in.

- **preflight** (`make capability-preflight FROM=<folder or link>`): what stands in the way, named before anything is
  installed or downloaded: the manifest's problems (format 2), what its lock asks for that this Mac can't run (CUDA),
  what its adapter does at run time that it may not (publish.FORBIDDEN), what the app can't give it yet, and, for a
  link, which route it takes (a reviewed template, or a folder to write).
- **scaffold** (`make capability-new ID= RUNNER= OUTPUT= [INPUT=] [INTO=mine|catalog]`): a valid folder to fill in, so
  nobody writes a manifest from memory.
- **lock** (`make capability-lock ID=`): its requirements as wheels only, every one by its hash, for this Mac (as the
  in-app builder locks them, drafts.py).
- **derive** (`make capability-derive FROM= ID= [INTO=]`): your own version of a capability, its folder copied with
  `derived_from` naming the one it came from, under an id of its own, so the original stays as it is.

Nothing here runs a capability's code: reading a folder is metadata only (ADR 0021).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog

# Packages that run only with NVIDIA's CUDA: no Mac can run them (named before they are installed, never after).
CUDA_ONLY = re.compile(r"^(nvidia-[\w-]+|cupy[\w-]*|triton|xformers|bitsandbytes|flash[-_]attn|tensorrt[\w-]*)\s*==",
                       re.IGNORECASE)
CUDA_CODE = re.compile(r"\.cuda\(\)|device\s*=\s*[\"']cuda[\"']")


@dataclass(frozen=True)
class Finding:
    blocks: bool  # it can't be installed or run here until this changes
    says: str


def _folder_of(source: str, environ: Mapping[str, str] = os.environ) -> Path | None:
    path = Path(source).expanduser()
    if path.is_file() and path.name == catalog.MANIFEST:
        return path.parent
    if path.is_dir():
        return path
    roots = (catalog.mine_root(environ),) if source.startswith("mine/") else (catalog.ROOT, catalog.mine_root(environ))
    for root in roots:  # by its folder's name, whether or not its manifest reads yet
        if (root / source.removeprefix("mine/") / catalog.MANIFEST).is_file():
            return root / source.removeprefix("mine/")
    yours = catalog.mine(environ)
    found = catalog.get(source) or yours.get(source) or yours.get(f"mine/{source}")
    return Path(found.folder) if found is not None and found.folder else None


# What the scaffold writes for its author to fill in (scaffold, below). A folder or workspace candidate with any "TODO" is
# unfinished (its author's own marker counts too); an AI draft's review counts only these, since a draft that ran is read
# by its reviewer, and a "TODO" in the AI's own comment blocked Use it with no way out (ticket 62 L13 review; drafts.py).
PLACEHOLDERS = ("TODO: what it does, in at most 12 words", "TODO: the command's page in imagej.net/ij/docs/menus",
                "TODO: Measure, Find, Outline, Clearer picture...", "TODO_param", "TODO_option", 'label = "TODO"',
                "TODO: the menu command, as the macro recorder writes it", "TODO: its licence (MIT, Apache-2.0...)",
                "TODO: where it comes from", "TODO: what it changes", "TODO what it does.")


def unfilled(text: str) -> bool:
    return any(p in text for p in PLACEHOLDERS)


def preflight(source: str, environ: Mapping[str, str] = os.environ, *, workspace: str | Path | None = None) -> list[Finding]:
    """Everything that stands in the way of `source` (a folder, an entry's id, or a link), in plain words."""
    if workspace is not None:
        from . import capability_workspace as workspaces
        item = workspaces.from_path(workspace)
        if re.match(r"https?://", source):
            return _link(source)
        folder = item.candidate if source in (item.candidate_id, str(item.candidate)) else _folder_of(source, environ)
        if folder is None:
            return [Finding(True, f"{source!r} is no capability folder")]
        with workspaces.snapshot(folder) as copied:
            return preflight(str(copied), environ)
    if re.match(r"https?://", source):
        return _link(source)
    folder = _folder_of(source, environ)
    if folder is None or not (folder / catalog.MANIFEST).is_file():
        return [Finding(True, f"{source!r} is no folder with a {catalog.MANIFEST}, entry id or link")]
    m, problems = catalog.read(folder / catalog.MANIFEST)
    out = [Finding(True, f"{f.name} still says TODO: fill each one in") for f in sorted(folder.iterdir())
           if f.is_file() and f.suffix in (".toml", ".py", ".groovy", ".in") and "TODO" in f.read_text(errors="replace")]
    out += [Finding(True, p) for p in problems]
    if m is None:
        return out
    if not m.contract and not m.members:
        out.append(Finding(False, "it is written in catalog format 1 ([[implements]]): format 2 ([[inputs]] and "
                                  "[[outputs]]) gives it a form, parameters and every actor (ADR 0024)"))
    lock = folder / str(m.env.get("lock", "")) if m.env else None
    if m.runner == "python-env":
        if lock is None or not lock.is_file():
            out.append(Finding(True, "it has no lockfile yet: make capability-lock ID=" + m.id))
        else:
            lines = [l.strip() for l in lock.read_text().splitlines() if l.strip() and not l.lstrip().startswith(("#", "--hash"))]
            for line in lines:
                if CUDA_ONLY.match(line):
                    out.append(Finding(True, f"its lock needs {line.split('==')[0]}, which runs only with NVIDIA's CUDA: "
                                             "no Mac runs it"))
            if any(not l.endswith("\\") and "--hash=" not in l for l in lines if "==" in l):
                out.append(Finding(True, "every package in its lock needs its hash: make capability-lock ID=" + m.id))
        from .publish import FORBIDDEN

        for code in sorted(folder.rglob("*.py") if m.members else folder.glob("*.py")):
            text = code.read_text(errors="replace")
            if m.members and 'TODO' in text:
                out.append(Finding(True, f'{code.relative_to(folder)} still says TODO: fill each one in'))
            for pattern, why in FORBIDDEN:
                for hit in pattern.finditer(text):
                    if why is None and "local_files_only=True" in hit.group(0):
                        continue
                    out.append(Finding(True, f"{code.name} {why or 'loads from Hugging Face at run time without local_files_only=True (pin the files)'}"))
            if CUDA_CODE.search(text):
                out.append(Finding(True, f"{code.name} puts work on CUDA: a Mac has none (use the CPU, or mps with a CPU "
                                         "fallback)"))
    if m.members:
        from . import bundle_runtime, kinds, share
        from gpu_worker import bundle_contract
        try:
            share.pack(m)
        except (share.CantShare, OSError, ValueError) as exc:
            out.append(Finding(True, f'the bundle cannot travel as a complete package: {exc}'))
        for member in bundle_runtime.declarations(m):
            if member.bridge == 'scientific-host-v1':
                out.append(Finding(False, f'{member.name}: scientific-host candidate for review, not a proven tool. '
                                          'Import the package in Models, prepare its declared runtime, then review it '
                                          'on the intended scientific input. Packaging does not grant readiness, '
                                          'activation or qualification; RGB reference cases cannot prove this bridge.'))
                continue
            view = bundle_contract.view(m, member)
            why = kinds.takes_why(view)
            if len(view.data_inputs) != 1 or view.data_inputs[0].type != 'image.2d.rgb8':
                why = 'bundle checks currently require one 2D RGB8 image'
            if kinds.implements(view) is None:
                why = 'built-in job implementations are not supported by bundles yet'
            if why:
                out.append(Finding(True, f'{member.name}: {why}'))
            if not member.reference_cases:
                out.append(Finding(True, f'{member.name} needs reference_cases before it can be proven'))
    if m.contract:
        from . import kinds

        why = kinds.takes_why(m)
        if why:
            out.append(Finding(True, f"the app can't run it yet: {why}"))
        if not m.tests:
            out.append(Finding(False, "it has no `tests` (a pinned example and the outcome it gives): capability-test "
                                      "proves it on generated pictures only"))
    if m.runner == "fiji":
        out.append(Finding(False, f"its command ({m.fiji.get('command')!r}) is checked against the installed Fiji's own "
                                  "menus in its proof, and its result against Fiji's own macro"))
    if m.licence and not m.component:
        from . import licences

        if any(l not in licences.COMPONENTS for l in m.licence):
            out.append(Finding(True, f"its licence {list(m.licence)} names no component the app lists: declare "
                                     "`component = {{ what, licence, url, tier }}`"))
    return out


def _link(url: str) -> list[Finding]:
    from . import link_recognise

    try:
        found = link_recognise.recognise(url)
    except Exception as exc:  # noqa: BLE001 - a link that can't be read is said, never raised
        return [Finding(True, f"the link couldn't be read: {exc}")]
    kind, why = found.kind, found.why
    if kind == "template":
        return [Finding(False, f"a reviewed template ({found.template}) runs it: paste the link in the Toolbox's Add, or "
                               "make model-add FROM=<link>")]
    if kind == "needs-code":
        return [Finding(False, "no reviewed template runs it: make capability-new ID=<id>, then write its adapter "
                               "from its makers' own example (its code must come from a published wheel, pinned in "
                               "env.lock)")]
    if kind == "elsewhere":
        return [Finding(False, why)]
    return [Finding(True, why or f"{url} is no link the app can add from")]


# --- scaffold ----------------------------------------------------------------------------------------------------
RETURNS = {
    "image.2d.rgb8": "picture  # a uint8 (height, width, 3) RGB array",
    "table.objects": '{"columns": [{"name": "value", "unit": "", "definition": "what it measures"}], "rows": [[0.0]]}',
    "text": '"what it says"',
    "points.2d": '{"points": [[0.0, 0.0]]}  # x, y in the picture\'s pixels',
    "labels.instance.2d": "np.zeros(picture.shape[:2], np.int32)  # one label per thing, 0 the background",
    "tracks.graph": '{"tracks": [{"points": [[0, 0.0, 0.0]]}]}  # frame index, x, y',
}


def scaffold(id: str, runner: str = "python-env", output: str = "table.objects", input: str = "image.2d.rgb8",
             into: str = "mine", environ: Mapping[str, str] = os.environ, *, workspace: str | Path | None = None) -> Path:
    """A valid folder to fill in: its manifest (format 2), and for python-env an adapter and requirements."""
    if workspace is not None:
        from . import capability_workspace as workspaces
        item = workspaces.from_path(workspace)
        workspaces.require_candidate(item, id)
        if into != "mine" or (runner, input, output) != (item.runner, item.input, item.output):
            raise ValueError("use the runner, input and output requested by this private workspace")
        with tempfile.TemporaryDirectory(prefix="capability-scaffold-") as temporary:
            folder = scaffold(item.candidate_id, runner, output, input, environ={"MICROMIND_CATALOG": temporary})
            return workspaces.publish_candidate(item, folder)
    if not catalog.KIND_ID.fullmatch(id):
        raise ValueError(f"an id is a short lowercase word with hyphens ({id!r})")
    if runner not in ("python-env", "fiji"):
        raise ValueError("RUNNER is python-env (your own Python code or model) or fiji (an ImageJ command)")
    root = catalog.ROOT if into == "catalog" else catalog.mine_root(environ)
    folder = root / id
    if folder.exists():
        raise ValueError(f"{folder} already exists")
    name = id.replace("-", " ").capitalize()
    head = (f'requires = {{ catalog = 2 }}\nid = "{id}"\nname = "{name}"\nruns = "{"this-mac" if runner == "fiji" else "cpu"}"\n'
            f'version = "1"\n')
    if runner == "fiji":
        body = (head + 'licence = ["fiji"]\nkind = "tool-function"\nwhat = "TODO: what it does, in at most 12 words"\n'
                'runner = "fiji"\nsource = { tool = "fiji", url = "TODO: the command\'s page in imagej.net/ij/docs/menus" }\n'
                f'needs_to_run = ["install:{id}"]\ncategory = "TODO: Measure, Find, Outline, Clearer picture..."\n\n'
                '[[inputs]]\nid = "image"\ntype = "image.2d.rgb8"\n\n'
                '[[inputs]]\nid = "TODO_param"\ntype = "number"\ndefault = 1.0\nmin = 0.0\nmax = 10.0\n'
                'label = "TODO"\n\n'
                f'[[outputs]]\nid = "result"\ntype = "{output if output in catalog.FIJI_OUTPUTS else "image.2d.rgb8"}"\n\n'
                '[fiji]\ncommand = "TODO: the menu command, as the macro recorder writes it"\n'
                'options = "TODO_option={TODO_param}"\n')
    else:
        body = (head + f'licence = ["{id}"]\nwhat = "TODO: what it does, in at most 12 words"\nrunner = "python-env"\n'
                f'adapter = "adapter:run"\nenv = {{ python = "3.13.5", lock = "env.lock", imports = ["numpy"] }}\n'
                f'needs_to_run = ["install:{"" if into == "catalog" else "mine/"}{id}"]\n'
                f'component = {{ what = "{name}", licence = "TODO: its licence (MIT, Apache-2.0...)", '
                f'url = "TODO: where it comes from", tier = "open" }}\n'
                f'source = {{ url = "TODO: where it comes from" }}\n'
                f'category = "TODO: Measure, Find, Outline, Clearer picture..."\n\n'
                f'[[inputs]]\nid = "image"\ntype = "{input}"\n\n'
                '[[inputs]]\nid = "threshold"\ntype = "int"\ndefault = 128\nmin = 0\nmax = 255\nlabel = "Threshold"\n'
                'tooltip = "TODO: what it changes"\n\n'
                f'[[outputs]]\nid = "result"\ntype = "{output}"\n')
    folder.mkdir(parents=True)
    (folder / catalog.MANIFEST).write_text(body)
    if runner == "python-env":
        returns = RETURNS.get(output, '{"value": 0}  # any JSON value: a type the app keeps as data')
        (folder / "adapter.py").write_text(
            '"""' + f"{name}: TODO what it does." + '\n\nrun(picture, params) is called with the view (a uint8 RGB array; '
            'a clip is (frames, height, width, 3)) and its parameters\' values, checked, defaults filled. Return its '
            'output; {"unsupported": "why"} to refuse a picture it can\'t take; an empty result when it finds nothing. '
            'No network, no installs: pin files in the manifest."""\nimport numpy as np\n\n\ndef run(picture, params):\n'
            f'    threshold = params["threshold"]\n    return {returns}\n')
        (folder / "requirements.in").write_text("numpy==2.3.4\n")
    return folder


def lock(id: str, environ: Mapping[str, str] = os.environ, *, workspace: str | Path | None = None) -> Path:
    """Its requirements.in locked as wheels only, every one by its hash, for this Mac (Python 3.13, Apple Silicon)."""
    if workspace is not None:
        from . import bounded_files, capability_workspace as workspaces
        item = workspaces.from_path(workspace)
        workspaces.require_candidate(item, id)
        if item.runner != "python-env":
            raise ValueError("Fiji capabilities use the pinned Fiji runtime and do not need a Python lock")
        with workspaces.snapshot(item.candidate) as copied:
            requirements = (copied / "requirements.in").read_bytes()
            locked = lock(str(copied), environ)
            with workspaces._directory(copied) as source:
                data = bounded_files.read_at(source, locked.name, workspaces.share.MAX_FILE)
        with workspaces._directory(item.candidate) as destination:
            if bounded_files.read_at(destination, "requirements.in", workspaces.share.MAX_FILE) != requirements:
                raise ValueError("requirements changed while locking; run capability-lock again")
            name = f".env-{uuid.uuid4().hex}.lock"
            workspaces._write(destination, name, data)
            os.rename(name, "env.lock", src_dir_fd=destination, dst_dir_fd=destination)
        return item.candidate / "env.lock"
    folder = _folder_of(id, environ)
    if folder is None or not (folder / "requirements.in").is_file():
        raise ValueError(f"{id}: no folder with a requirements.in")
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    subprocess.run([uv, "pip", "compile", "--quiet", "--generate-hashes", "--only-binary", ":all:", "--python-version",
                    "3.13", "--python-platform", "aarch64-apple-darwin", str(folder / "requirements.in"), "-o",
                    str(folder / "env.lock")], check=True,
                   env={**os.environ, "MACOSX_DEPLOYMENT_TARGET": "14.0"})  # the oldest macOS the app runs on (torch's wheels)
    return folder / "env.lock"


def _manifest_text(fields: dict) -> str:
    # Use the portable package writer so quoted keys, arrays and tables retain their parsed meaning.
    from .capability_import import _toml

    return "\n".join(_toml(key) + " = " + _toml(value) for key, value in fields.items()) + "\n"


def derive(source: str, id: str, into: str = "mine", environ: Mapping[str, str] = os.environ, *,
           workspace: str | Path | None = None) -> Path:
    """Your own version: its folder copied under an id of its own, `derived_from` naming the one it came from (its id,
    version and the sha256 of its manifest and adapter), the original left as it is."""
    if workspace is not None:
        from . import capability_workspace as workspaces
        item = workspaces.from_path(workspace)
        workspaces.require_candidate(item, id)
        if into != "mine":
            raise ValueError("a private workspace cannot write the reviewed catalog")
        original = _folder_of(source, environ)
        if original is None:
            raise ValueError(f"{source!r} is no capability here")
        known = catalog.mine(environ).get(source) or catalog.get(source)
        source_key = known.key if known is not None and Path(known.folder).absolute() == original.absolute() else (
            f"mine/{original.name}" if original.parent.absolute() == catalog.mine_root(environ).absolute() else original.name)
        with workspaces.snapshot(original) as copied, tempfile.TemporaryDirectory(prefix="capability-derive-") as temporary:
            manifest, problems = catalog.read(copied / catalog.MANIFEST)
            if manifest is None or manifest.runner != item.runner:
                raise ValueError("the source must use this workspace's runner: " + "; ".join(problems))
            digest = hashlib.sha256()
            for path in sorted(p for p in copied.rglob("*") if p.is_file()):
                digest.update(path.relative_to(copied).as_posix().encode() + b"\0" + path.read_bytes())
            folder = derive(str(copied), item.candidate_id, environ={"MICROMIND_CATALOG": temporary})
            path = folder / catalog.MANIFEST
            fields = tomllib.loads(path.read_text())
            fields["derived_from"] = {"id": source_key, "version": manifest.version, "sha256": digest.hexdigest()}
            path.write_text(_manifest_text(fields))
            return workspaces.publish_candidate(item, folder)
    from_folder = _folder_of(source, environ)
    if from_folder is None:
        raise ValueError(f"{source!r} is no capability here")
    m, problems = catalog.read(from_folder / catalog.MANIFEST)
    if m is None:
        raise ValueError(f"{source}: " + "; ".join(problems))
    if not m.contract and not m.members:
        raise ValueError(f"{m.name} is written in catalog format 1: derive from a capability in format 2 or 3")
    if not catalog.KIND_ID.fullmatch(id) or id == m.id:
        raise ValueError(f"a derived version has an id of its own, a short lowercase word with hyphens ({id!r})")
    root = catalog.ROOT if into == "catalog" else catalog.mine_root(environ)
    folder = root / id
    if folder.exists():
        raise ValueError(f"{folder} already exists")
    # Only the entry's own files (envs.entry_files: never a weights revision folder kept beside its manifest, this Mac's
    # state there or caches), the same ones its digest and a share read.
    from . import envs

    own = envs.entry_files(m, environ)
    digest = hashlib.sha256()
    for rel in own:
        if len(rel.parts) == 1 or m.members:
            digest.update(rel.as_posix().encode() + b"\0" + (from_folder / rel).read_bytes())
    for rel in own:
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(from_folder / rel, folder / rel)
        # A channel's files are read-only (channels.fetch): your version is yours to change.
        (folder / rel).chmod((folder / rel).stat().st_mode | 0o200)
    fields = tomllib.loads((from_folder / catalog.MANIFEST).read_text())
    fields.update(id=id, name=m.name + " (your version)", version="1",
                  derived_from={"id": m.key, "version": m.version, "sha256": digest.hexdigest()})
    for requirement in ("needs_to_get", "needs_to_run"):
        if requirement in fields:
            fields[requirement] = [
                f"install:{'' if into == 'catalog' or m.members else 'mine/'}{id}"
                if re.fullmatch(r"install:(?:[a-z0-9_-]+/)?" + re.escape(m.id), step) else step
                for step in fields[requirement]]
    if m.component:
        fields["licence"] = [id if licence == m.id else licence for licence in fields["licence"]]
    (folder / catalog.MANIFEST).write_text(_manifest_text(fields))
    return folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.contribute")
    sub = parser.add_subparsers(dest="what", required=True)
    one = sub.add_parser("preflight")
    one.add_argument("source")
    one.add_argument("--workspace")
    one = sub.add_parser("new")
    one.add_argument("id")
    one.add_argument("--runner", default="python-env")
    one.add_argument("--output", default="table.objects")
    one.add_argument("--input", default="image.2d.rgb8")
    one.add_argument("--into", default="mine", choices=("mine", "catalog"))
    one.add_argument("--workspace")
    one = sub.add_parser("lock")
    one.add_argument("id")
    one.add_argument("--workspace")
    one = sub.add_parser("derive")
    one.add_argument("source")
    one.add_argument("id")
    one.add_argument("--into", default="mine", choices=("mine", "catalog"))
    one.add_argument("--workspace")
    one = sub.add_parser("package")
    one.add_argument("--workspace", required=True)
    one.add_argument("--by", default="")
    args = parser.parse_args(argv)
    try:
        if args.what == "preflight":
            found = preflight(args.source, workspace=args.workspace)
            for f in found:
                print(("BLOCKS  " if f.blocks else "note    ") + f.says)
            if not found:
                print("Nothing stands in the way: install it (make catalog-install ID=…) and test it (make capability-test ID=…)")
            return 1 if any(f.blocks for f in found) else 0
        if args.what == "new":
            folder = scaffold(args.id, args.runner, args.output, args.input, args.into, workspace=args.workspace)
            print(f"Made {folder}: fill in each TODO, then make capability-preflight FROM={folder}")
        elif args.what == "lock":
            print(f"Locked {lock(args.id, workspace=args.workspace)}")
        elif args.what == "derive":
            folder = derive(args.source, args.id, args.into, workspace=args.workspace)
            print(f"Made {folder}, derived from {args.source}: change it, then make capability-test ID={args.id}")
        elif args.what == "package":
            from . import capability_workspace as workspaces
            item = workspaces.from_path(args.workspace)
            print(workspaces.package(item.id, by=args.by, environ={"MICROMIND_CATALOG": str(item.folder.parent.parent)}))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Can't: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
