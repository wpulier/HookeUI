"""PEP 517 hooks inside an already confined, offline build worker."""
from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import tomllib


def main():
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    source, output = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    os.chdir(source)
    project = tomllib.loads((source / 'pyproject.toml').read_text())
    system = project['build-system']

    checked = set()

    def require(items, extras=('',)):
        if type(items) is not list or len(items) > 256:
            raise ValueError('Build requirements must be a bounded list')
        for item in items:
            requirement = Requirement(item)
            if requirement.url:
                raise ValueError('Build hooks cannot fetch requirements; pin them in the builder lock')
            if requirement.marker and not any(requirement.marker.evaluate({'extra': extra}) for extra in extras):
                continue
            try:
                distribution = importlib.metadata.distribution(requirement.name)
                version = distribution.version
            except importlib.metadata.PackageNotFoundError as exc:
                raise ValueError(f'Missing build dependency: {requirement.name}; pin it in the builder lock') from exc
            if not requirement.specifier.contains(version, prereleases=True):
                raise ValueError(f'Build dependency {requirement.name} {version} does not satisfy {requirement.specifier}')
            declared = {canonicalize_name(extra) for extra in distribution.metadata.get_all('Provides-Extra', [])}
            selected = {canonicalize_name(extra) for extra in requirement.extras}
            if not selected <= declared:
                raise ValueError(f'Unknown build extras for {requirement.name}')
            key = (canonicalize_name(requirement.name), tuple(sorted(selected)))
            if key not in checked:
                checked.add(key)
                if len(checked) > 256:
                    raise ValueError('Build dependency closure exceeds its allowance')
                require(distribution.requires or [], ('', *sorted(selected)))

    require(system['requires'])
    paths = system.get('backend-path', [])
    if type(paths) is not list or len(paths) > 16:
        raise ValueError('Invalid build backend paths')
    for path in reversed(paths):
        if type(path) is not str:
            raise ValueError('Invalid build backend path')
        resolved = (source / path).resolve()
        if not resolved.is_relative_to(source) or not resolved.is_dir():
            raise ValueError('Build backend path leaves the source')
        sys.path.insert(0, str(resolved))
    module, _, attribute = system['build-backend'].partition(':')
    backend = importlib.import_module(module)
    if paths and not any(Path(backend.__file__).resolve().is_relative_to((source / path).resolve()) for path in paths):
        raise ValueError('The build backend did not load from its declared source path')
    if attribute:
        for name in attribute.split('.'):
            backend = getattr(backend, name)
    needs = getattr(backend, 'get_requires_for_build_wheel', lambda config_settings: [])(None)
    require(needs)
    result = backend.build_wheel(str(output), config_settings=None)
    if type(result) is not str or Path(result).name != result or not result.endswith('.whl'):
        raise ValueError('Build backend did not return a wheel filename')
    print(json.dumps({'wheel': result}), flush=True)


if __name__ == '__main__':
    main()
