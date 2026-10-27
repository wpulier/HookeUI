"""Every env_worker module the confined review worker imports is copied into its sandbox (scientific_host.BOOTSTRAP).

The worker runs from a bootstrap folder holding only BOOTSTRAP's files, so a module missing there fails only when an
actual worker starts; this checks the import graph statically instead.
"""
import ast
from pathlib import Path

from orchestrator.scientific_host import BOOTSTRAP

SERVICES = Path(__file__).resolve().parents[1]


def _relative_imports(path):
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module:
                names.add(node.module.split('.')[0] + '.py')
            else:
                names.update(alias.name + '.py' for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or '').startswith('env_worker.'):
            names.add(node.module.split('.')[1] + '.py')
    return names


def test_every_module_the_worker_imports_is_in_its_sandbox():
    copied = set(BOOTSTRAP['env_worker'])
    pending, seen = ['scientific_host_server.py'], set()
    while pending:
        name = pending.pop()
        if name in seen or not (SERVICES / 'env_worker' / name).is_file():
            continue
        seen.add(name)
        pending += sorted(_relative_imports(SERVICES / 'env_worker' / name))
    missing = sorted(n for n in seen if n not in copied)
    assert not missing, f'imported by the review worker but not copied into its sandbox: {missing}'
