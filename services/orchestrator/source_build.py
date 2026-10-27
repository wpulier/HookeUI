"""Bounded source-to-wheel preparation, separate from installed runtime generations.

This does not install or activate the result. The caller supplies a prepared, pinned
builder environment and reserves execution resources before calling off the loop.
The normal package installer continues to require wheels. No build hook runs in the
parent, downloads a dependency or writes into an active environment.
"""
from __future__ import annotations

import base64
import csv
from email.parser import BytesParser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import time
import zipfile

from . import envs, generation_leases, owned_process, sandbox, worker_resources
from .paths import SERVICES

MAX_ARCHIVE = 64 << 20
MAX_EXPANDED = 256 << 20
MAX_FILE = 64 << 20
MAX_ENTRIES = 20000
MAX_LOG = 1 << 20
BOUNDARY = 'source-wheel-macos-v1'
BOOTSTRAP = SERVICES / 'env_worker/build_source.py'


class Failed(ValueError):
    pass


def _path(name):
    if type(name) is not str:
        raise Failed('The archive contains a noncanonical path')
    path = PurePosixPath(name)
    if (not name or len(name) > 1024 or '\\' in name or '\x00' in name
            or path.is_absolute() or any(p in ('', '.', '..') for p in name.rstrip('/').split('/'))
            or any(':' in p for p in path.parts)):
        raise Failed('The archive contains a noncanonical path')
    return path


def _entries(archive):
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ENTRIES:
        raise Failed('The archive has too many entries or is empty')
    total, names = 0, set()
    for item in entries:
        path = _path(item.filename)
        key = str(path).casefold()
        if key in names:
            raise Failed('The archive repeats a path')
        names.add(key)
        mode = item.external_attr >> 16
        if item.flag_bits & 1 or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise Failed('Encrypted, linked and special archive entries are refused')
        if item.file_size > MAX_FILE or item.file_size < 0:
            raise Failed('An archive member exceeds its size allowance')
        total += item.file_size
        if total > MAX_EXPANDED:
            raise Failed('The expanded archive exceeds its size allowance')
    return entries


def _copy_regular(source, target, limit):
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as reader:
        if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
            raise Failed('A pinned artifact must be a regular file')
        digest, total = hashlib.sha256(), 0
        with target.open('xb') as writer:
            while block := reader.read(1 << 20):
                total += len(block)
                if total > limit:
                    raise Failed('The artifact exceeds its size allowance')
                digest.update(block)
                writer.write(block)
    return digest.hexdigest(), total


def unpack(archive_path, destination):
    with zipfile.ZipFile(archive_path) as archive:
        entries = _entries(archive)
        for item in entries:
            path = destination.joinpath(*_path(item.filename).parts)
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, path.open('xb') as output:
                remaining = item.file_size
                while remaining:
                    data = source.read(min(1 << 20, remaining))
                    if not data:
                        raise Failed('The source archive ended early')
                    output.write(data)
                    remaining -= len(data)
                if source.read(1):
                    raise Failed('The source archive member exceeded its declared size')


def check_wheel(path):
    """Validate archive paths and every RECORD hash without importing wheel code."""
    with zipfile.ZipFile(path) as archive:
        entries = _entries(archive)
        files = {item.filename: item for item in entries if not item.is_dir()}
        records = [name for name in files if name.endswith('.dist-info/RECORD')]
        if len(records) != 1 or files[records[0]].file_size > 4 << 20:
            raise Failed('The wheel needs one bounded RECORD')
        record = records[0]
        prefix = record.removesuffix('RECORD')
        if prefix + 'METADATA' not in files or prefix + 'WHEEL' not in files:
            raise Failed('The wheel is missing its distribution metadata')
        if files[prefix + 'METADATA'].file_size > 1 << 20 or files[prefix + 'WHEEL'].file_size > 16384:
            raise Failed('The wheel metadata exceeds its allowance')
        from packaging.utils import canonicalize_name, parse_wheel_filename
        from packaging.version import Version
        name, version, _, _ = parse_wheel_filename(path.name)
        info = prefix.removesuffix('/').removesuffix('.dist-info')
        distribution, separator, metadata_version = info.rpartition('-')
        if '/' in info or not separator or canonicalize_name(distribution) != name or Version(metadata_version) != version:
            raise Failed('The wheel distribution directory disagrees with its filename')
        metadata = BytesParser().parsebytes(archive.read(prefix + 'METADATA'))
        if (len(metadata.get_all('Name', [])) != 1 or len(metadata.get_all('Version', [])) != 1
                or canonicalize_name(metadata.get('Name', '')) != name or Version(metadata.get('Version', '')) != version):
            raise Failed('The wheel filename and distribution metadata disagree')
        seen = set()
        for row in csv.reader(io.StringIO(archive.read(record).decode('utf-8'))):
            if len(row) != 3 or row[0] in seen or row[0] not in files:
                raise Failed('The wheel RECORD disagrees with its files')
            name, signature, size = row
            seen.add(name)
            if name == record:
                if signature or size:
                    raise Failed('The wheel RECORD must not hash itself')
                continue
            if not signature.startswith('sha256=') or not size.isdecimal() or int(size) != files[name].file_size:
                raise Failed('Each wheel member needs its exact size and SHA256')
            digest = hashlib.sha256()
            with archive.open(name) as stream:
                while data := stream.read(1 << 20):
                    digest.update(data)
            expected = base64.urlsafe_b64encode(digest.digest()).rstrip(b'=').decode()
            if signature != 'sha256=' + expected:
                raise Failed('The wheel RECORD contains an incorrect hash')
        if seen != set(files):
            raise Failed('The wheel has files absent from RECORD')


def build(source: Path, *, sha256: str, subdirectory: str, builder, destination: Path,
          environ=None, stop=lambda: False, seconds=300):
    """Build a private wheel candidate using an existing manifest's pinned environment.

    `builder` must be a private catalog entry, prepared by envs.build. `destination`
    is a new caller-owned directory. Failed attempts remain with their diagnostic
    receipt and work; only a successful receipt names a validated wheel artifact.
    """
    environ = os.environ if environ is None else environ
    if not sandbox.available() or os.environ.get('MICROMIND_SANDBOX_READ'):
        raise Failed('Source preparation requires the unmodified macOS confinement boundary')
    if not re.fullmatch('[a-f0-9]{64}', sha256 or ''):
        raise Failed('Source preparation needs the exact archive SHA256')
    relative = _path(subdirectory)
    if builder.namespace != 'mine' or not builder.env or builder.members:
        raise Failed('Use a private, pinned builder environment without bundle members')
    folder = envs.folder(builder, environ)
    with owned_process.scope(seconds=seconds, stop=stop), generation_leases.use(folder):
        return _build(source, sha256, relative, builder, folder, destination, environ, stop, seconds)


def _build(source, sha256, relative, builder, folder, destination, environ, stop, seconds):
    receipt_path = folder / envs.RECEIPT
    receipt = json.loads(receipt_path.read_bytes())
    lock_digest = hashlib.sha256(envs.lock_file(builder, environ).read_bytes()).hexdigest()
    if (receipt.get('model') != builder.key or receipt.get('generation') != folder.name
            or receipt.get('lockSha256') != lock_digest):
        raise Failed('Prepare the builder from its exact pinned lock before building source')
    destination = Path(destination).absolute()
    destination.mkdir(parents=False, exist_ok=False)
    work = destination / 'work'
    work.mkdir()
    record = {'ok': False, 'boundary': BOUNDARY, 'sourceSha256': sha256,
              'subdirectory': str(relative), 'builder': builder.key, 'generation': folder.name,
              'lockSha256': lock_digest, 'python': receipt.get('python'),
              'bootstrapSha256': hashlib.sha256(BOOTSTRAP.read_bytes()).hexdigest(),
              'scope': 'private wheel preparation; no runtime or clean-machine qualification'}
    process = guard = None
    started = time.monotonic()
    try:
        actual, _ = _copy_regular(source, work / 'source.zip', MAX_ARCHIVE)
        if actual != sha256:
            raise Failed('The source archive does not match its pinned SHA256')
        extracted, output = work / 'source', work / 'output'
        extracted.mkdir()
        output.mkdir()
        unpack(work / 'source.zip', extracted)
        root = extracted.joinpath(*relative.parts)
        if not root.is_dir() or not (root / 'pyproject.toml').is_file():
            raise Failed('The selected source directory needs pyproject.toml')
        executable = folder / 'bin/python'
        with owned_process.scope():
            pass
        command = sandbox.wrap([str(executable), '-I', '-u', str(BOOTSTRAP), str(root), str(output)],
                               [folder, sandbox.python_home(executable), BOOTSTRAP], [work],
                               gpu=False, children=False)
        environment = sandbox.confine({'PATH': '/usr/bin:/bin', 'SOURCE_DATE_EPOCH': '315532800',
                                       'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}, work)
        process = subprocess.Popen(command, cwd=work, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   close_fds=True, start_new_session=True)
        guard = worker_resources.Guard(process, work, worker_resources.Limits(
            file_bytes=MAX_FILE, work_bytes=768 << 20, work_entries=60000, processes=1))
        guard.start()
        with owned_process.scope(stop=stop), owned_process.watching(process, seconds, 'source preparation'):
            count, tail = 0, bytearray()
            while data := process.stdout.read(65536):
                count += len(data)
                if count > MAX_LOG:
                    raise Failed('The build exceeded its log allowance')
                tail.extend(data)
                del tail[:-65536]
            process.wait()
        guard.check()
        if process.returncode:
            raise Failed('The confined build failed: ' + envs.last_reason(tail.decode('utf-8', 'replace'))[-1200:])
        # Terminate any remaining owned activity before inspecting writable artifacts.
        guard.close()
        guard.check()
        if output.is_symlink() or not output.is_dir():
            raise Failed('The build replaced its output directory')
        wheels = list(output.iterdir())
        if len(wheels) != 1 or not wheels[0].name.endswith('.whl'):
            raise Failed('The build must produce exactly one wheel')
        artifact = destination / wheels[0].name
        digest, size = _copy_regular(wheels[0], artifact, MAX_FILE)
        check_wheel(artifact)
        artifact.chmod(0o444)
        record.update(ok=True, wheel=artifact.name, wheelSha256=digest, bytes=size)
        return artifact
    except owned_process.Stopped as exc:
        record['why'] = str(exc)[-2000:]
        raise Failed(str(exc)) from exc
    except BaseException as exc:
        record['why'] = str(exc)[-2000:]
        raise
    finally:
        if guard is not None:
            guard.close()
        elif process is not None and process.poll() is None:
            owned_process.terminate(process)
        if process is not None:
            process.stdout.close()
        record['seconds'] = round(time.monotonic() - started, 3)
        (destination / 'build.json').write_text(json.dumps(record, indent=2) + '\n')


def main(argv=None):
    import argparse
    from gpu_worker import manifests as catalog
    from . import capability_workspace

    parser = argparse.ArgumentParser(description='Prepare a private wheel with pinned, confined build tools; does not activate a capability')
    parser.add_argument('source', type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--subdirectory', required=True)
    parser.add_argument('--builder', required=True, type=Path, help='private manifest folder with the complete hashed builder lock')
    parser.add_argument('--into', required=True, type=Path, help='new private preparation directory')
    args = parser.parse_args(argv)
    try:
        if not sandbox.available() or os.environ.get('MICROMIND_SANDBOX_READ'):
            raise Failed('Source preparation requires the unmodified macOS confinement boundary')
        destination = Path(os.path.abspath(args.into))
        destination.mkdir(parents=False, exist_ok=False)
        if not re.fullmatch('[a-f0-9]{64}', args.sha256):
            raise Failed('Source preparation needs the exact archive SHA256')
        relative = _path(args.subdirectory)
        source = destination / 'source.zip'
        digest, _ = _copy_regular(args.source, source, MAX_ARCHIVE)
        if digest != args.sha256:
            raise Failed('The source archive does not match its pinned SHA256')
        with zipfile.ZipFile(source) as archive:
            _entries(archive)
            if str(relative / 'pyproject.toml') not in archive.namelist():
                raise Failed('The selected source directory needs pyproject.toml')
        (destination / 'home').mkdir()
        isolated = {'HOME': str(destination / 'home'), 'MICROMIND_CATALOG': str(destination / 'catalog')}
        with capability_workspace.snapshot(Path(os.path.abspath(args.builder))) as copied:
            kept = destination / 'builder' / copied.name
            shutil.copytree(copied, kept)
            builder, problems = catalog.read(kept / catalog.MANIFEST, 'mine')
            if builder is None:
                raise Failed('The builder declaration is invalid: ' + '; '.join(problems))
            if not builder.env or builder.members or builder.env.get('native') or builder.env.get('commands'):
                raise Failed('This build bridge needs a pinned Python-only builder without native commands or bundle members')
            envs.build(builder, isolated, say=print)
            result = build(source, sha256=args.sha256, subdirectory=args.subdirectory,
                           builder=builder, destination=destination / 'result', environ=isolated)
        print(result)
        print('Private wheel prepared. Pin its hash in the runtime recipe; operation, workspace and clean-machine checks remain required.')
        return 0
    except (OSError, ValueError, envs.Failed, owned_process.Stopped, zipfile.BadZipFile) as exc:
        print(f'Source preparation failed: {exc}', file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
