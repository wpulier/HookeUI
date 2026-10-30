"""Real accepted snapshot I/O with synthetic measurements and hostile filesystem changes."""
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.datatables import DataTableRefused
from orchestrator.table_reading import read_accepted
from tests.test_project_table_routing import project
from tests.waiting import HANG_S


def saved(project):
    app, locations, tables, _ = project
    session = app.day.new('Accepted numeric I/O')
    record = asyncio.run(tables.create(['d1'], mode='frozen'))
    return app, session, record, tables.root_for(session), tables.path_of(record, session)


@pytest.mark.parametrize('damage', ['fifo', 'file-link', 'directory-link', 'root-link'])
def test_special_files_refuse_promptly_without_changing_journal(project, tmp_path, damage):
    app, session, record, root, path = saved(project)
    journal = app.day.journal.path.read_bytes()
    if damage == 'fifo':
        path.unlink(); os.mkfifo(path)
    else:
        target = path if damage == 'file-link' else path.parent if damage == 'directory-link' else root
        preserved = tmp_path / 'preserved'
        target.rename(preserved)
        target.symlink_to(preserved, target_is_directory=damage != 'file-link')
    # An accidental blocking open fails by timeout, rather than hanging the test runner.
    code = '''import json, sys
from pathlib import Path
from orchestrator.datatables import DataTableRefused
from orchestrator.table_reading import read_accepted
try: read_accepted(Path(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3], 'dataset-table')
except DataTableRefused: sys.exit(0)
sys.exit(1)
'''
    result = subprocess.run([sys.executable, '-c', code, str(root), json.dumps(record), session],
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
        capture_output=True, timeout=HANG_S)
    assert result.returncode == 0, result.stderr.decode()
    assert app.day.journal.path.read_bytes() == journal


def test_symlink_replacement_at_open_is_refused(project, tmp_path, monkeypatch):
    app, session, record, root, path = saved(project)
    journal = app.day.journal.path.read_bytes()
    outside = tmp_path / 'outside.json'; outside.write_bytes(path.read_bytes())
    opened = os.open
    def replace(name, flags, *args, **kwargs):
        if name == path.name:
            path.unlink(); path.symlink_to(outside)
        return opened(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', replace)
    with pytest.raises(DataTableRefused, match='unavailable'):
        read_accepted(root, record, session, 'dataset-table')
    assert app.day.journal.path.read_bytes() == journal


def test_same_inode_change_during_read_is_refused(project, monkeypatch):
    app, session, record, root, path = saved(project)
    journal = app.day.journal.path.read_bytes()
    read = os.read
    changed = False
    def changing(fd, size):
        nonlocal changed
        body = read(fd, size)
        if not changed:
            changed = True
            with path.open('ab') as stream: stream.write(b' ')
        return body
    monkeypatch.setattr(os, 'read', changing)
    with pytest.raises(DataTableRefused, match='changed'):
        read_accepted(root, record, session, 'dataset-table')
    assert app.day.journal.path.read_bytes() == journal


def test_cancellation_reaches_bounded_reader(project):
    app, session, record, root, _ = saved(project)
    journal = app.day.journal.path.read_bytes()
    checks = 0
    def check():
        nonlocal checks
        checks += 1
        if checks == 3: raise RuntimeError('cancelled during read')
    with pytest.raises(RuntimeError, match='cancelled during read'):
        read_accepted(root, record, session, 'dataset-table', check=check)
    assert checks == 3 and app.day.journal.path.read_bytes() == journal


@pytest.mark.parametrize('field,value', [('snapshot', None), ('snapshot', []),
    ('snapshot', {'path': 'missing'}), ('source', None), ('spec', None)])
def test_malformed_accepted_metadata_has_recoverable_refusal(project, field, value):
    app, session, record, root, _ = saved(project)
    journal = app.day.journal.path.read_bytes()
    kind = 'dataset-table'
    if field in ('source', 'spec'):
        kind = 'analysis'
        record = {**record, 'id': 'an1', 'snapshot': {**record['snapshot'], 'path': f'{session}/an1/v1.json'},
                  'source': {'session': session}, 'spec': {}}
    with pytest.raises(DataTableRefused):
        read_accepted(root, {**record, field: value}, session, kind)
    assert app.day.journal.path.read_bytes() == journal


@pytest.mark.parametrize('body', [b'null', b'[]', b'1'])
def test_nonobject_snapshot_has_recoverable_refusal(project, body):
    app, session, record, root, path = saved(project)
    journal = app.day.journal.path.read_bytes()
    path.write_bytes(body)
    record = {**record, 'snapshot': {**record['snapshot'], 'sha256': hashlib.sha256(body).hexdigest()}}
    with pytest.raises(DataTableRefused, match='object'):
        read_accepted(root, record, session, 'dataset-table')
    assert app.day.journal.path.read_bytes() == journal
