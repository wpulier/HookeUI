"""Synthetic HTTP requests exercise production candidate and workspace services."""
import io
import json
import shutil
import tomllib
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gpu_worker import manifests as catalog
from orchestrator import capability_import, capability_journey_api as api, capability_workspace as workspaces
from orchestrator import channels, own_trials, readiness, share
from orchestrator.origin import studio_origin
from research_worker import prompts
from tests.test_channels import home  # noqa: F401
from tests.test_share import yours
from tests.test_fiji_share import capability

HEADER = 'X-MicroscopeMind'
ORIGIN = 'http://127.0.0.1:8770'


@pytest.fixture
def web(home):
    built = SimpleNamespace(day=SimpleNamespace(sid='session-a'), hub=SimpleNamespace(), sync_mine=AsyncMock())
    app = FastAPI()
    api.register(app, built, studio_origin, HEADER)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, built


def headers(action='capability-workspace'):
    return {HEADER: action, 'Origin': ORIGIN}


def create(client, **values):
    return client.post('/api/capability-workspaces', json={'session': 'session-a', 'description': 'Measure spots', **values}, headers=headers())


def upload(client, data, **query):
    return client.post('/api/capability-import', params={'session': 'session-a', **query}, content=data,
                       headers=headers('capability-import'))


def test_workspace_handoff_and_download_use_private_opaque_identity(web):
    client, built = web
    response = create(client)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['ok'] and prompts.skill_rules() in value['instructions']
    item = workspaces.resolve(value['id'])
    assert item.candidate_id == value['candidate']
    shutil.copytree(catalog.ROOT / 'spot-measure', item.candidate)
    manifest = item.candidate / catalog.MANIFEST
    manifest.write_text(manifest.read_text().replace('spot-measure', item.candidate_id))
    downloaded = client.post('/api/capability-workspaces/' + item.id,
                             json={'session': 'session-a', 'action': 'package'}, headers=headers())
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers['content-type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert json.loads(archive.read('SHARED.json'))['id'] == item.candidate_id
    assert catalog.channels() == []
    built.sync_mine.assert_not_called()


@pytest.mark.parametrize('route,header', [('/api/capability-workspaces', 'capability-workspace'),
                                         ('/api/capability-workspaces/' + 'a' * 32, 'capability-workspace'),
                                         ('/api/capability-import', 'capability-import')])
@pytest.mark.parametrize('origin,correct', [(None, True), ('https://evil.invalid', True), (ORIGIN, False)])
def test_all_routes_require_studio_origin_and_exact_action(web, route, header, origin, correct):
    client, built = web
    sent = {HEADER: header if correct else 'actor:user'}
    if origin:
        sent['Origin'] = origin
    response = client.post(route, json={'session': 'session-a'}, headers=sent)
    assert response.status_code == 403
    assert not workspaces.root().exists()
    built.sync_mine.assert_not_called()


@pytest.mark.parametrize('change', [{'session': 'other'}, {'actor': 'user'}, {'description': 123}])
def test_invalid_workspace_request_has_actionable_error(web, change):
    client, _ = web
    response = create(client, **change)
    assert response.status_code in (400, 409)
    assert response.json()['says']
    assert not workspaces.root().exists()


def test_bounded_requests_and_missing_canonical_skill_fail_without_handoff(web, monkeypatch):
    client, _ = web
    response = client.post('/api/capability-workspaces', content=b'x' * (api.MAX_JSON + 1), headers=headers())
    assert response.status_code == 413
    monkeypatch.setattr(prompts, 'SKILL', prompts.SKILL.parent / 'missing')
    response = create(client)
    assert response.status_code == 400 and 'skill' in response.json()['says']
    assert not workspaces.root().exists()


def test_workspace_session_is_checked_after_creation_before_returning_instructions(web, monkeypatch):
    client, built = web
    original = workspaces.create
    def changed(*args, **kwargs):
        item = original(*args, **kwargs)
        built.day.sid = 'session-b'
        return item
    monkeypatch.setattr(workspaces, 'create', changed)
    response = create(client)
    assert response.status_code == 409
    assert 'instructions' not in response.json()
    recovery = response.json()['recovery']
    assert recovery['session'] == 'session-a'
    item = workspaces.resolve(recovery['workspace']['id'])
    assert recovery['workspace'] == {'id': item.id, 'folder': str(item.folder), 'candidate': item.candidate_id}
    resumed = client.post('/api/capability-workspaces/' + item.id,
                          json={'session': 'session-b', 'action': 'read'}, headers=headers())
    assert resumed.status_code == 200 and resumed.json()['instructions']
    built.sync_mine.assert_not_called()


def test_an_edited_package_always_creates_a_fresh_untrusted_candidate_and_preserves_active_entry(web, home):
    client, built = web
    key = yours(home)
    original = catalog.get(key)
    original_folder = catalog.entry_folder(original)
    original_bytes = {p.name: p.read_bytes() for p in original_folder.iterdir() if p.is_file()}
    data = share.export(key).read_bytes()
    first = upload(client, data)
    second = upload(client, data)
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    a, b = first.json(), second.json()
    assert a['model'] != b['model'] and a['channel'] != b['channel']
    for result in (a, b):
        m = catalog.get(result['model'])
        assert m is not None and m.channel == result['channel']
        assert readiness.model(m.key, {}).steps[0].kind == 'trust'
        assert own_trials.awaiting_picture(m.key)
        assert not m.contract, 'legacy manifest contract remains unchanged'
        assert result['lineage']['id'] == key and result['lineage']['version'] == original.version
        assert len(result['lineage']['sha256']) == 64
        assert result['comparisonRequired']
    assert original_bytes == {p.name: p.read_bytes() for p in original_folder.iterdir() if p.is_file()}
    assert built.sync_mine.await_count == 2
    assert not list((catalog.catalog_root() / 'uploads').iterdir())


def test_format_two_preserves_contract_parameter_authority_and_full_source_lineage(web, tmp_path):
    client, _ = web
    original = catalog.get('spot-measure')
    directory = tmp_path / 'source' / original.id
    shutil.copytree(catalog.entry_folder(original), directory)
    path = directory / catalog.MANIFEST
    fields = tomllib.loads(path.read_text())
    parameter = next(item for item in fields['inputs'] if item['type'] == 'int')
    parameter['agent'] = False
    path.write_text('\n'.join(capability_import._toml(k) + ' = ' + capability_import._toml(v) for k, v in fields.items()))
    m, problems = catalog.read(path, namespace='maker')
    assert m, problems
    response = upload(client, share.pack(m))
    assert response.status_code == 200, response.text
    result = response.json()
    imported = catalog.get(result['model'])
    assert imported.contract == m.contract
    assert imported.derived_from['id'] == m.key
    assert imported.derived_from == result['lineage']
    assert [(p.id, p.agent, p.default) for p in imported.params] == [(p.id, p.agent, p.default) for p in m.params]


def test_fiji_import_is_refused_without_boundary_and_untrusted_when_supported(web, home, monkeypatch):
    from orchestrator import fiji_sandbox
    client, _ = web
    data = share.pack(capability(home))
    monkeypatch.setattr(fiji_sandbox, 'supported', lambda: False)
    response = upload(client, data)
    assert response.status_code == 400 and 'sandbox' in response.json()['says']
    assert catalog.channels() == []
    monkeypatch.setattr(fiji_sandbox, 'supported', lambda: True)
    response = upload(client, data)
    assert response.status_code == 200, response.text
    key = response.json()['model']
    m = catalog.get(key)
    assert m.runner == 'fiji' and own_trials.awaiting_picture(key)
    assert not channels.trusted(m)


@pytest.mark.parametrize('data', [b'not a zip', b'PK\x03\x04bad'])
def test_malformed_import_leaves_no_channel_or_upload(web, data):
    client, built = web
    response = upload(client, data)
    assert response.status_code == 400
    assert catalog.channels() == []
    assert not list((catalog.catalog_root() / 'uploads').iterdir())
    built.sync_mine.assert_not_called()


def test_oversized_import_is_bounded_before_archive_parser(web, monkeypatch):
    client, built = web
    monkeypatch.setattr(share, 'MAX_ARCHIVE', 128)
    response = upload(client, b'a' * 129)
    assert response.status_code == 413
    assert catalog.channels() == []
    built.sync_mine.assert_not_called()


def test_invalid_trial_refuses_before_creating_a_candidate(web, home):
    client, built = web
    response = upload(client, share.export(yours(home)).read_bytes(), trial='missing')
    assert response.status_code == 400
    assert catalog.channels() == []
    built.sync_mine.assert_not_called()


def test_session_change_after_validation_prevents_catalog_publication(web, home, monkeypatch):
    client, built = web
    original = capability_import.prepare
    def changed(*args, **kwargs):
        prepared = original(*args, **kwargs)
        built.day.sid = 'session-b'
        return prepared
    monkeypatch.setattr(capability_import, 'prepare', changed)
    response = upload(client, share.export(yours(home)).read_bytes())
    assert response.status_code == 409
    assert catalog.channels() == []
    built.sync_mine.assert_not_called()


def test_session_change_during_publication_never_syncs_or_selects_candidate(web, home, monkeypatch):
    client, built = web
    original = capability_import.publish
    def changed(*args, **kwargs):
        value = original(*args, **kwargs)
        built.day.sid = 'session-b'
        return value
    monkeypatch.setattr(capability_import, 'publish', changed)
    response = upload(client, share.export(yours(home)).read_bytes())
    assert response.status_code == 409
    built.sync_mine.assert_not_called()
    assert len(catalog.channels()) == 1
    recovery = response.json()['recovery']
    assert recovery['session'] == 'session-a'
    candidate = catalog.get(recovery['candidate'])
    assert candidate is not None and not channels.trusted(candidate)
    assert own_trials.awaiting_picture(candidate.key)
    assert candidate.channel == catalog.channels()[0]['name']


def test_only_opaque_workspace_ids_are_resolved_and_secret_fields_are_refused(web):
    client, _ = web
    response = create(client)
    id_ = response.json()['id']
    for body in ({'session': 'session-a', 'action': 'read', 'path': '/etc'},
                 {'session': 'session-a', 'action': 'read', 'apiKey': 'must-not-store'}):
        result = client.post('/api/capability-workspaces/' + id_, json=body, headers=headers())
        assert result.status_code == 400
    result = client.post('/api/capability-workspaces/not-an-id',
                         json={'session': 'session-a', 'action': 'read'}, headers=headers())
    assert result.status_code == 400


def test_declared_input_and_output_survive_package_import(web, home):
    client, _ = web
    m = catalog.get('spot-measure')
    response = upload(client, share.pack(m))
    assert response.status_code == 200, response.text
    imported = catalog.get(response.json()['model'])
    assert imported.inputs == m.inputs and imported.outputs == m.outputs
    assert imported.derived_from['version'] == m.version


def test_corrected_package_can_retry_after_failure_without_affecting_existing_candidate(web, home):
    client, built = web
    data = share.export(yours(home)).read_bytes()
    good = upload(client, data).json()
    path = catalog.entry_folder(catalog.get(good['model'])) / catalog.MANIFEST
    before = path.read_bytes()
    assert upload(client, b'broken').status_code == 400
    assert path.read_bytes() == before and own_trials.awaiting_picture(good['model'])
    retry = upload(client, data)
    assert retry.status_code == 200 and retry.json()['model'] != good['model']
    assert path.read_bytes() == before
    assert built.sync_mine.await_count == 2


def test_owned_file_work_finishes_before_cancelled_request_cleans_its_directory():
    import asyncio
    import threading

    started, finish, ended = threading.Event(), threading.Event(), threading.Event()
    def worker():
        started.set()
        assert finish.wait(5)
        ended.set()
    async def check():
        task = asyncio.create_task(api._work(worker))
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not ended.is_set()
        task.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ended.is_set()
    asyncio.run(check())


def test_selected_picture_is_attached_only_to_the_new_candidate(web, home):
    client, _ = web
    trial = 'a' * 32
    own_trials.folder(trial).mkdir(parents=True)
    own_trials.save(trial, {'id': trial, 'job': 'find', 'sha256': 'b' * 64})
    data = share.export(yours(home)).read_bytes()
    response = upload(client, data, trial=trial)
    assert response.status_code == 200, response.text
    key = response.json()['model']
    assert json.loads(own_trials.marker(key).read_text()) == {'model': key, 'trial': trial}
    assert own_trials.state(trial)['model'] == key
    assert not own_trials.marker('mine/dark-spots').exists()


def test_read_and_reveal_do_not_accept_caller_paths(web, monkeypatch):
    client, _ = web
    item = create(client).json()
    def reveal(args, **kwargs):
        assert args == ['open', '-R', item['folder']]
        assert kwargs['timeout'] == 10 and kwargs['check'] is True
    monkeypatch.setattr(api.sys, 'platform', 'darwin')
    monkeypatch.setattr(api.subprocess, 'run', reveal)
    for action in ('read', 'reveal'):
        response = client.post('/api/capability-workspaces/' + item['id'],
                               json={'session': 'session-a', 'action': action}, headers=headers())
        assert response.status_code == 200 and response.json()['instructions'] == item['instructions']


def test_controlled_memory_refusal_preserves_catalog_and_retry_releases_reservation(web, home):
    from orchestrator.capture import captures
    client, built = web
    data = share.export(yours(home)).read_bytes()
    memory = captures(built.hub).memory
    allowance = memory.allowances['analysis']
    memory.allowances['analysis'] = 1
    response = upload(client, data)
    assert response.status_code == 503 and 'retry' in response.json()['says']
    assert catalog.channels() == [] and memory.reserved['analysis'] == 0
    memory.allowances['analysis'] = allowance
    response = upload(client, data)
    assert response.status_code == 200, response.text
    assert memory.reserved['analysis'] == 0


def test_comparison_marker_failure_refuses_before_any_candidate_becomes_discoverable(web, home, monkeypatch):
    client, built = web
    def refuse(*args, **kwargs):
        raise OSError('controlled storage refusal')
    monkeypatch.setattr(own_trials, 'await_comparison', refuse)
    response = upload(client, share.export(yours(home)).read_bytes())
    assert response.status_code == 400
    assert catalog.channels() == []
    built.sync_mine.assert_not_called()


def test_import_keeps_prior_derivation_in_portable_metadata(web, tmp_path):
    client, _ = web
    m = catalog.get('spot-measure')
    folder = tmp_path / m.id
    shutil.copytree(catalog.entry_folder(m), folder)
    manifest = folder / catalog.MANIFEST
    old = {'id': 'maker/original-measure', 'version': '7', 'sha256': 'a' * 64}
    text = manifest.read_text()
    manifest.write_text('derived_from = ' + capability_import._toml(old) + '\n' + text)
    source, problems = catalog.read(manifest)
    assert source, problems
    uploaded = upload(client, share.pack(source))
    assert uploaded.status_code == 200, uploaded.text
    channel = next(row for row in catalog.channels() if row['name'] == uploaded.json()['channel'])
    metadata = catalog.snapshot(channel) / 'SHARED.json'
    retained = json.loads(metadata.read_text())
    assert retained['source_lineage'] == old
    assert retained['imported_from'] == uploaded.json()['lineage']


def changed_manifest(package, change):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package)) as source, zipfile.ZipFile(out, 'w') as target:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith('/model.toml'):
                fields = tomllib.loads(data.decode())
                change(fields)
                data = '\n'.join(capability_import._toml(k) + ' = ' + capability_import._toml(v)
                                 for k, v in fields.items()).encode()
            target.writestr(name, data)
    return out.getvalue()


@pytest.mark.parametrize('field,value', [
    ('lock', '/private/secret/env.lock'), ('lock', '../env.lock'),
    ('command', '/usr/bin/python -c pass'), ('command', '../bin/python -c pass'),
    ('example', '/private/secret.png'), ('example', '../secret.png'),
    ('check', '/private/secret.py'), ('check', '../secret.py'),
    ('adapter', '../secret:run'), ('test', '/private/example.png'), ('test', '../example.png'),
    ('file', '/private/secret.weights'), ('file', '../secret.weights'),
    ('archive', '../secret.weights'), ('from', '/private/secret.weights'),
])
def test_outside_references_are_rejected_before_any_manifest_consumer_or_channel(web, field, value, monkeypatch):
    client, built = web
    original = share.pack(catalog.get('spot-measure'))
    def change(fields):
        if field == 'lock':
            fields['env']['lock'] = value
        elif field == 'command':
            fields['env']['commands'] = [value]
        elif field == 'test':
            fields['tests'] = [{'input': value}]
        elif field in ('file', 'archive', 'from'):
            record = {'path': 'weights.dat', 'url': 'https://example.org/weights', 'bytes': 1, 'sha256': 'a' * 64}
            if field == 'file':
                record['path'] = value
            else:
                record[field] = {'path': value}
            fields['files'] = [record]
        else:
            fields[field] = value
    bad = changed_manifest(original, change)
    def never_read(*args, **kwargs):
        raise AssertionError('an outside reference reached a manifest consumer')
    monkeypatch.setattr(catalog, 'read', never_read)
    response = upload(client, bad)
    assert response.status_code == 400, response.text
    assert 'relative' in response.json()['says'] or 'module and function' in response.json()['says']
    assert catalog.channels() == []
    built.sync_mine.assert_not_called()


@pytest.mark.parametrize('value', ['/private/secret.groovy', '../secret.groovy'])
def test_outside_fiji_script_is_refused_before_boundary_probe_or_publication(web, home, value, monkeypatch):
    from orchestrator import fiji_sandbox
    client, built = web
    original = share.pack(capability(home))
    bad = changed_manifest(original, lambda fields: fields['fiji'].update(script=value))
    def never_probe():
        raise AssertionError('an invalid script reached the runtime boundary')
    monkeypatch.setattr(fiji_sandbox, 'supported', never_probe)
    response = upload(client, bad)
    assert response.status_code == 400 and 'Groovy file' in response.json()['says']
    assert catalog.channels() == []
    built.sync_mine.assert_not_called()


def test_prepared_package_is_rechecked_before_publication_if_changed(web, tmp_path):
    client, _ = web
    source = tmp_path / 'source.observationui.zip'
    source.write_bytes(share.pack(catalog.get('spot-measure')))
    prepared = capability_import.prepare(source, tmp_path)
    prepared.path.write_bytes(changed_manifest(prepared.path.read_bytes(),
                                               lambda fields: fields['env'].update(lock='/private/secret.lock')))
    with pytest.raises(workspaces.WorkspaceError, match='relative'):
        capability_import.publish(prepared)
    assert catalog.channels() == []
    assert not (own_trials.root() / 'models').exists()


@pytest.mark.parametrize('output', ['points.2d', 'spectra.raman'])
def test_guided_import_refuses_uncomparable_outputs_before_publication_but_legacy_share_still_reads(web, tmp_path, output):
    client, built = web
    original = share.pack(catalog.get('spot-measure'))
    bad = changed_manifest(original, lambda fields: fields['outputs'][0].update(type=output))
    path = tmp_path / 'legacy.observationui.zip'
    path.write_bytes(bad)
    assert share._read(path)[1] == 'spot-measure'
    response = upload(client, bad)
    assert response.status_code == 400, response.text
    assert output in response.json()['says'] and 'cannot compare' in response.json()['says']
    assert 'Nothing has been installed' in response.json()['says']
    assert catalog.channels() == [] and not (own_trials.root() / 'models').exists()
    built.sync_mine.assert_not_called()


@pytest.mark.parametrize('input_,output', [('clip.2d.rgb8', 'tracks.graph'), ('volume.3d', 'table.objects')])
def test_guided_import_refuses_inputs_the_picture_comparison_cannot_supply(web, input_, output):
    client, built = web
    original = share.pack(catalog.get('spot-measure'))
    def change(fields):
        fields['inputs'][0]['type'] = input_
        fields['outputs'][0]['type'] = output
    response = upload(client, changed_manifest(original, change))
    assert response.status_code == 400, response.text
    assert 'compare' in response.json()['says'] or 'integration' in response.json()['says']
    assert catalog.channels() == [] and not (own_trials.root() / 'models').exists()
    built.sync_mine.assert_not_called()


@pytest.mark.parametrize('output', [output for _, output in own_trials.JOBS.values()])
def test_every_existing_picture_comparison_output_remains_importable(web, output):
    client, _ = web
    original = share.pack(catalog.get('spot-measure'))
    data = changed_manifest(original, lambda fields: fields['outputs'][0].update(type=output))
    response = upload(client, data)
    assert response.status_code == 200, response.text
    assert own_trials.awaiting_picture(response.json()['model'])


def test_publication_rechecks_comparison_support_after_staging(web, tmp_path):
    client, _ = web
    source = tmp_path / 'source.observationui.zip'
    source.write_bytes(share.pack(catalog.get('spot-measure')))
    prepared = capability_import.prepare(source, tmp_path)
    prepared.path.write_bytes(changed_manifest(prepared.path.read_bytes(),
                                               lambda fields: fields['outputs'][0].update(type='points.2d')))
    with pytest.raises(share.CantShare, match='cannot compare'):
        capability_import.publish(prepared)
    assert catalog.channels() == [] and not (own_trials.root() / 'models').exists()


def test_workspace_instruction_failure_keeps_recovery_without_incomplete_handoff(web, monkeypatch):
    client, _ = web
    def unavailable(*args, **kwargs):
        raise workspaces.WorkspaceError('the workspace skill is missing or changed')
    monkeypatch.setattr(workspaces, 'instructions', unavailable)
    response = create(client)
    assert response.status_code == 400 and 'skill' in response.json()['says']
    recovery = response.json()['recovery']
    assert recovery['session'] == 'session-a'
    assert workspaces.resolve(recovery['workspace']['id'])
    assert 'instructions' not in recovery['workspace']


def test_catalog_sync_failure_keeps_import_recovery_and_comparison_gate(web, home):
    client, built = web
    built.sync_mine.side_effect = RuntimeError('controlled sync failure')
    response = upload(client, share.export(yours(home)).read_bytes())
    assert response.status_code == 400
    recovery = response.json()['recovery']
    assert recovery['session'] == 'session-a'
    candidate = catalog.get(recovery['candidate'])
    assert candidate is not None and not channels.trusted(candidate)
    assert own_trials.awaiting_picture(candidate.key)


def test_guided_bundle_upload_preserves_nested_modules_and_requires_result_review(web, home):
    from tests.test_bundle_share import package
    from orchestrator import env_runner, kinds
    client, built = web
    original = package(home)
    result = upload(client, share.pack(original, by='Maya'))
    assert result.status_code == 200, result.text
    added = result.json()
    received = catalog.get(added['model'])
    assert received.members and received.key != original.key
    assert received.derived_from['id'] == original.key
    assert (catalog.entry_folder(received) / 'adapters/operations.py').is_file()
    assert own_trials.pending(received)
    assert env_runner.in_use(received) is None
    assert not [k for k in kinds.available() if k['key'].startswith(received.key + '#')]
    assert readiness.model(received.key, {}).steps[0].kind == 'trust'


def test_guided_scientific_candidate_with_wheel_imports_within_default_memory_without_activation(web, home):
    from tests.test_wheel_recipe import wheel_owner, WHEEL
    from orchestrator import env_runner, catalog_view
    from orchestrator.capture import captures
    client, built = web
    owner, data = wheel_owner(home, b'x' * (3 << 20))
    folder = catalog.entry_folder(owner)
    text = (folder / 'model.toml').read_text().split('[[members]]')[0]
    text += '''
[[members]]
id = "viewer"
name = "Scientific review"
adapter = "adapter:open_host"
bridge = "scientific-host-v1"
inputs = [{id = "project", type = "project.scientific"}]
outputs = [{id = "result", type = "project.scientific"}]
'''
    (folder / 'model.toml').write_text(text)
    owner, problems = catalog.read(folder / 'model.toml')
    assert owner is not None, problems
    response = upload(client, share.pack(owner))
    assert response.status_code == 200, response.text
    received = catalog.get(response.json()['model'])
    assert not channels.trusted(received)
    assert env_runner.in_use(received) is None
    assert readiness.model(received.key, {}).status == 'needs-setup'
    assert readiness.model(received.key, {}).next.kind == 'trust'
    assert not catalog_view.shareable(received)
    assert (catalog.entry_folder(received) / WHEEL).read_bytes() == data
    memory = captures(built.hub).memory
    assert memory.peak['analysis'] < 64 << 20
    assert memory.reserved['analysis'] == 0


def test_guided_bundle_review_acceptance_and_artifact_routes(web, home, monkeypatch):
    from tests.test_bundle_share import package
    from tests.test_env_runner import standin_uv
    from orchestrator import env_runner, envs, kinds, catalog_view
    client, built = web
    added = upload(client, share.pack(package(home))).json()
    owner = catalog.get(added['model'])
    channels.trust(added['channel'])
    request = {'session': 'session-a', 'model': owner.key, 'action': 'read'}
    def review(**updates):
        return client.post('/api/capability-review', json={**request, **updates}, headers=headers('capability-review'))
    assert review().status_code == 400
    standin_uv(home, monkeypatch)
    envs.install(owner)
    assert catalog_view.execution_block(owner)['action'] == 'review-bundle'
    assert not any(k['key'].startswith(owner.key + '#') for k in kinds.available())
    response = review()
    assert response.status_code == 200, response.text
    checked = response.json()
    assert len(checked['cases']) == 2
    check = checked['cases'][0]
    params = {'model': owner.key, 'generation': checked['generation'], 'digest': checked['digest'], 'case': check['case'], 'role': 'actual'}
    actual = client.get('/api/capability-review-artifact', params=params)
    assert actual.status_code == 200 and actual.headers['content-type'] == 'image/png'
    assert actual.headers['x-content-type-options'] == 'nosniff'
    assert client.get('/api/capability-review-artifact', params={**params, 'role': '../../secret'}).status_code == 400
    assert review(action='accept', generation=checked['generation'], digest='wrong').status_code == 400
    assert review(action='accept', generation=checked['generation'], digest=checked['digest'], session='old').status_code == 409
    assert own_trials.pending(owner)
    accepted = review(action='accept', generation=checked['generation'], digest=checked['digest'])
    assert accepted.status_code == 200, accepted.text
    assert not own_trials.pending(owner)
    assert catalog_view.execution_block(owner) is None
    env_runner.register()
    assert {k['key'] for k in kinds.available() if k['key'].startswith(owner.key + '#')} == {owner.key + '#invert', owner.key + '#mean'}
    assert review(action='accept', generation=checked['generation'], digest=checked['digest']).status_code == 200


@pytest.mark.parametrize('change', [{'actor': 'user'}, {'session': 'other'}])
def test_bundle_review_refuses_untrusted_or_stale_context(web, change):
    client, _ = web
    result = client.post('/api/capability-review', json={'action': 'read', 'session': 'session-a', 'model': 'x', **change}, headers=headers('capability-review'))
    assert result.status_code in (400, 409)
    result = client.post('/api/capability-review', json={'action': 'read', 'session': 'session-a', 'model': 'x'}, headers={'Origin': 'https://example.org', HEADER: 'capability-review'})
    assert result.status_code == 403


@pytest.mark.parametrize('model', [[], {}, 123, ['not-a-model']])
def test_bundle_accept_malformed_identity_is_a_scoped_refusal(web, model):
    client, _ = web
    response = client.post('/api/capability-review', json={'action': 'accept', 'session': 'session-a', 'model': model,
                           'generation': 'a' * 16, 'digest': 'b' * 64}, headers=headers('capability-review'))
    assert response.status_code == 400
    assert response.json()['says']


def test_retained_input_picker_is_studio_scoped_and_rechecks_session(web, monkeypatch):
    from orchestrator import retained_inputs, resources
    client, built = web
    monkeypatch.setattr(resources, 'resources_of', lambda hub: object())
    monkeypatch.setattr(retained_inputs, 'choices', lambda index, session, declaration=None: [])
    def ask(**kwargs):
        return client.post('/api/capability-inputs', json={'session': 'session-a'}, **kwargs)
    assert ask().status_code == 403
    assert ask(headers=headers('capability-inputs')).json() == {'ok': True, 'inputs': []}
    def changed(index, session, declaration=None):
        built.day.sid = 'changed'
        return []
    monkeypatch.setattr(retained_inputs, 'choices', changed)
    assert ask(headers=headers('capability-inputs')).status_code == 409


def test_workspace_package_cannot_outgrow_its_admitted_snapshot_budget(web, home, monkeypatch):
    from tests.test_wheel_recipe import wheel_owner, WHEEL
    from orchestrator import bounded_files
    client, _ = web
    item = workspaces.resolve(create(client).json()['id'])
    owner, _ = wheel_owner(home, b'small synthetic wheel')
    shutil.copytree(catalog.entry_folder(owner), item.candidate)
    manifest = item.candidate / catalog.MANIFEST
    manifest.write_text(manifest.read_text().replace(owner.id, item.candidate_id))
    original = api._workspace_package_allowance
    observed_limits = []
    read_at = bounded_files.read_at
    def observed(fd, name, limit):
        if name == WHEEL:
            observed_limits.append(limit)
        return read_at(fd, name, limit)
    def changed(candidate):
        admitted = original(candidate)
        # A concurrent author grows the declared artifact after HTTP admission.
        data = b'x' * (admitted + 1)
        import hashlib
        raw = tomllib.loads(manifest.read_text())
        raw['env']['wheels'][0].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        manifest.write_text('\n'.join(capability_import._toml(k) + ' = ' + capability_import._toml(v) for k, v in raw.items()))
        (item.candidate / WHEEL).write_bytes(data)
        return admitted
    monkeypatch.setattr(api, '_workspace_package_allowance', changed)
    monkeypatch.setattr(bounded_files, 'read_at', observed)
    response = client.post('/api/capability-workspaces/' + item.id,
        json={'session': 'session-a', 'action': 'package'}, headers=headers())
    assert response.status_code == 400, response.text
    assert observed_limits and max(observed_limits) <= share.MAX_TOTAL + len(b'small synthetic wheel')
    assert not list(item.folder.glob('*.observationui.zip'))
