"""Every first-party module the shipped app imports ships with it (packaging/macos/snapshot.py RUNTIME).

Beta 0.7.0's first candidate failed to start on a clean Mac: kinds.py imports scientific_compute (since 71a9a4c), which
RUNTIME left out, and no test runs the packaged tree. This reads every shipped Python file's imports, including those
inside functions, and checks that each one naming a package under services/ resolves to a shipped file.
"""
import ast
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / 'services'


def _snapshot():
    spec = importlib.util.spec_from_file_location('source_snapshot', ROOT / 'packaging/macos/snapshot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_file(name):
    """The repo path of a first-party module under services/, or None when it isn't one."""
    parts = name.split('.')
    if not (SERVICES / parts[0]).is_dir():
        return None
    base = 'services/' + '/'.join(parts)
    for path in (base + '.py', base + '/__init__.py'):
        if (ROOT / path).is_file():
            return path
    return None  # an attribute (from a import b) or a name that isn't a file: its package is checked instead


def test_every_first_party_import_of_the_shipped_app_ships():
    snapshot = _snapshot()
    tracked = subprocess.run(['git', 'ls-files', 'services'], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split()
    shipped = [p for p in tracked if p.endswith('.py') and snapshot.runtime(p)]
    assert 'services/orchestrator/kinds.py' in shipped
    missing = {}
    for path in shipped:
        tree = ast.parse((ROOT / path).read_text(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module] + [f'{node.module}.{alias.name}' for alias in node.names]
            else:
                continue
            for name in names:
                target = _module_file(name)
                if target and not snapshot.runtime(target):
                    missing.setdefault(target, path)
    assert not missing, ('shipped code imports files the Mac app leaves out (add them to RUNTIME in '
                         f'packaging/macos/snapshot.py): {missing}')
