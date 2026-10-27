"""Materialize declared portable wheel inputs inside an owned runtime generation."""
import os
from pathlib import Path

from gpu_worker import manifests as catalog, wheel_recipe
from . import bounded_files


def prepare(manifest, generation, environ=os.environ):
    wheels = wheel_recipe.declared(manifest.env or {})
    if not wheels:
        return None
    directory = None
    try:
        directory = bounded_files.open_directory(catalog.entry_folder(manifest, environ))
        lock = bounded_files.read_at(directory, manifest.env['lock'], 2 << 20).decode('utf-8')
        contents = {}
        for wheel in wheels:
            data = bounded_files.read_at(directory, wheel.file, wheel.bytes)
            wheel.verify(data)
            contents[wheel.file] = data
    except OSError as error:
        raise ValueError('A declared wheel or lock is missing, linked, or unreadable') from error
    finally:
        if directory is not None:
            os.close(directory)
    destination = Path(generation).absolute() / '.wheel-inputs'
    # Retain artifact identity in installer origin metadata as well as lock checks.
    urls = {wheel.file: (destination / wheel.file).as_uri() + '#sha256=' + wheel.sha256 for wheel in wheels}
    resolved = wheel_recipe.resolve(lock, wheels, urls)
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in contents.items():
        path = destination / name
        with path.open('xb') as output:
            output.write(data)
        path.chmod(0o444)
    path = destination / 'resolved.lock'
    with path.open('x') as output:
        output.write(resolved)
    path.chmod(0o444)
    return path
