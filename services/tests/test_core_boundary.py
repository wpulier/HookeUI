"""Standalone scientific core: real local storage, no account or service extension."""
import sys
import pytest
from tests.test_scientific_project_api import web, ORIGIN  # noqa: F401

HEADERS = {'Origin': ORIGIN, 'X-MicroscopeMind': 'research-effort'}


def test_standalone_studies_have_no_managed_provider_or_auth_requirement(web):
    client, built = web
    response = client.get('/api/research-readiness', params={'session': built.day.sid})
    assert response.status_code == 200
    assert response.json()['providers'] == []
    assert response.json()['executionAvailable'] is False
    assert not any(name.startswith(('hooke_cloud', 'research_runtime.claude', 'research_runtime.google')) for name in sys.modules)


def test_local_study_can_be_saved_reopened_and_stopped_without_cloud(web):
    client, built = web
    built.day.local_only = True
    data = {'session': built.day.sid, 'request': 'local-draft', 'title': 'Local study',
            'objective': 'Compare local images', 'criteria': 'Measured area', 'budgetUsd': 1, 'inputs': []}
    result = client.post('/api/research-efforts', headers=HEADERS, json=data)
    assert result.status_code == 200, result.text
    saved = result.json()['effort']
    read = client.get('/api/research-efforts/' + saved['id'], params={'session': built.day.sid})
    assert read.json()['effort'] == saved
    stop = client.post('/api/research-efforts/' + saved['id'] + '/control', headers=HEADERS,
                       json={'session': built.day.sid, 'action': 'stop'})
    assert stop.status_code == 200
    assert stop.json()['effort']['status'] == 'stopped'


def test_missing_execution_has_explicit_response_and_cannot_start_work(web):
    client, built = web
    response = client.post('/api/research-efforts/missing/start', headers=HEADERS,
                           json={'session': built.day.sid})
    assert response.status_code == 503
    assert response.json()['ok'] is False
    assert 'extension' in response.json()['says'].lower()


def test_extension_is_operator_selected_and_invalid_configuration_fails_closed(monkeypatch, tmp_path):
    from orchestrator.server import create_app
    from orchestrator.paths import Paths
    monkeypatch.setenv('HOOKEUI_RESEARCH_EXTENSION', 'missing_extension_for_test')
    with pytest.raises(ModuleNotFoundError, match="missing_extension_for_test"):
        create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

@pytest.mark.parametrize('lifecycle', [object(), type('BadStop', (), {'start': lambda self: None, 'stop': lambda self: None})()])
def test_malformed_extension_lifecycle_is_refused_before_start(monkeypatch, tmp_path, lifecycle):
    from types import SimpleNamespace
    from orchestrator.server import create_app
    from orchestrator.paths import Paths
    module = SimpleNamespace(register=lambda app, *args: setattr(app.state, 'research_lifecycle', lifecycle))
    monkeypatch.setitem(sys.modules, 'fixture_extension', module)
    monkeypatch.setenv('HOOKEUI_RESEARCH_EXTENSION', 'fixture_extension')
    with pytest.raises(RuntimeError, match='lifecycle'):
        create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))


def test_extension_stop_failure_still_closes_core_resources(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from fastapi.testclient import TestClient
    from orchestrator.server import create_app
    from orchestrator.paths import Paths
    from orchestrator import execution_archive
    from research_worker import api_usage
    lifecycle = SimpleNamespace(start=Mock(), stop=AsyncMock(side_effect=RuntimeError('extension-stop-fixture')))
    module = SimpleNamespace(register=lambda app, *args: setattr(app.state, 'research_lifecycle', lifecycle))
    monkeypatch.setitem(sys.modules, 'fixture_extension', module)
    monkeypatch.setenv('HOOKEUI_RESEARCH_EXTENSION', 'fixture_extension')
    app = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
    built = app.state.app
    monkeypatch.setattr(built, 'start', AsyncMock())
    stopped = AsyncMock(); monkeypatch.setattr(built, 'stop', stopped)
    ledger = Mock(); archive = Mock()
    monkeypatch.setattr(api_usage, 'UsageLedger', Mock(return_value=ledger))
    monkeypatch.setattr(execution_archive, 'ExecutionArchive', Mock(return_value=archive))
    with pytest.raises(RuntimeError, match='extension-stop-fixture'):
        with TestClient(app):
            pass
    stopped.assert_awaited_once()
    ledger.close.assert_called_once()
    archive.close.assert_called_once()
