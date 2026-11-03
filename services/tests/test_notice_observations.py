"""Catalog notice display snapshots never replace live execution authority."""
import os
from dataclasses import replace

import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, env_observations as observations, env_runner
from orchestrator.maintenance import revocations, status, store
from tests.test_channels import home  # noqa: F401
from tests.test_maintenance import turned_on
from tests.test_maintenance_lifecycle import REVOKED, notices


def test_new_block_remains_authoritative_inside_old_display(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    installed = env_runner.selected(m)
    snapshot = observations.collect()
    notices(home, REVOKED.format(v='0.1', severity='block'))
    channels.update()
    with observations.display(snapshot):
        assert revocations.revoked(m, m.version)['severity'] == 'block'
        with store.display_reads():
            assert revocations.revoked(m, m.version) is None, 'display retains its observation'
            assert 'revoked' in revocations.blocked(installed), 'execution ignores even an explicit display scope'
    assert revocations.revoked(m, m.version)['severity'] == 'block'


def test_notice_views_reuse_real_collected_rows_and_refresh_changes(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    text = REVOKED.format(v='0.1', severity='warn') + '''
[[unmaintained]]
entry = "dark-spots"
why = "maintenance ended"
[[fixes]]
entry = "dark-spots"
versions = ["dark-spots 0.1"]
why = "measurement defect"
fixed_in = "dark-spots 0.2"
'''
    notices(home, text)
    channels.update()
    m = catalog.get(m.key)
    snapshot = observations.collect()
    with monkeypatch.context() as patch:
        patch.setattr(revocations, '_files', lambda *args: pytest.fail('notice files enumerated on display caller'))
        with observations.display(snapshot):
            assert status.of(m)['defect']['severity'] == 'warn'
            with store.display_reads():
                assert revocations.unmaintained(m)['why'] == 'maintenance ended'
                assert revocations.fix(m, m.version)['fixedIn'] == 'dark-spots 0.2'
                assert len(revocations.fixes(m)) == 1
                other = dict(os.environ, MICROMIND_CATALOG=str(home / 'other'))
                assert revocations.revoked(m, m.version, other) is None
                assert revocations.revoked(replace(m, version='another generation'), m.version) is None
    notices(home, '')
    channels.update()
    current = catalog.get(m.key)
    with observations.display(observations.collect()), store.display_reads():
        assert revocations.revoked(current, current.version) is None
        assert revocations.unmaintained(current) is None
        assert revocations.fixes(current) == []


def test_one_unreadable_channel_does_not_freeze_other_notice_observations(home, monkeypatch):
    m = turned_on(home, monkeypatch)
    good = catalog.get('edgetam')
    (catalog.ROOT / revocations.FILE).write_text(
        f'[[revoked]]\nentry = "{good.id}"\nversion = "{good.version}"\nseverity = "warn"\n')
    files = revocations._files
    with monkeypatch.context() as patch:
        def unavailable(entry, environ):
            if entry.channel == m.channel:
                raise PermissionError('channel cannot be read')
            return files(entry, environ)
        patch.setattr(revocations, '_files', unavailable)
        snapshot = observations.collect()
    with observations.display(snapshot):
        assert 'could not be read' in status.of(m)['maintained']['why']
        with store.display_reads():
            assert revocations.revoked(good, good.version)['severity'] == 'warn'
    with observations.display(observations.collect()):
        assert status.of(m)['maintained'] is None, 'the next successful read clears the unavailable state'
