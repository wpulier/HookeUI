"""Portable Fiji packages carry source and examples, never an installed runtime."""
import dataclasses
import io
import json
import shutil
import zipfile
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, fiji_runner, fiji_sandbox, share
from tests.test_channels import home  # noqa: F401


def capability(home, script=True):
    m = catalog.get('fiji-gaussian-blur')
    id_ = 'shared-blur'
    folder = home / 'candidate' / id_
    folder.mkdir(parents=True)
    text = (catalog.entry_folder(m) / catalog.MANIFEST).read_text().replace(m.id, id_)
    if script:
        text += '\nscript = "filter.groovy"\n'
        (folder / 'filter.groovy').write_text('return image\n')
    (folder / catalog.MANIFEST).write_text(text)
    (folder / 'example-synthetic-texture.png').write_bytes(b'example fixture')
    (folder / 'LICENSE').write_bytes(b'Original attribution\r\n')
    (folder / 'activation.json').write_text('{}')
    (folder / 'private.groovy').write_text('never shared')
    (folder / 'plugin.jar').write_bytes(b'never shared')
    return dataclasses.replace(m, id=id_, folder=str(folder))


def archive(tmp_path, data):
    path = tmp_path / 'test.observationui.zip'
    path.write_bytes(data)
    return path


def mutate(data, changes):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, 'w') as target:
        names = set(source.namelist()) | changes.keys()
        for name in sorted(names):
            value = changes[name] if name in changes else source.read(name)
            if value is not None:
                target.writestr(name, value)
    return out.getvalue()


@pytest.mark.parametrize('script', [True, False])
def test_format_two_carries_only_declared_fiji_sources_examples_and_attribution(home, tmp_path, script):
    m = capability(home, script)
    shared, id_, files = share._read(archive(tmp_path, share.pack(m, by='Maya')))
    assert shared['format'] == 2 and shared['by'] == 'Maya' and id_ == m.id
    assert set(files) == {'model.toml', 'example-synthetic-texture.png', 'LICENSE'} | ({'filter.groovy'} if script else set())
    assert files['LICENSE'] == b'Original attribution\r\n'


@pytest.mark.parametrize('name', ['plugin.jar', 'adapter.py', 'other.groovy', 'example-secret.png', 'activation.json'])
def test_format_two_refuses_undeclared_code_data_and_state(home, tmp_path, name):
    m = capability(home)
    data = mutate(share.pack(m), {f'catalog/models/{m.id}/{name}': b'not declared'})
    with pytest.raises(share.CantShare, match='no catalog entry holds'):
        share._read(archive(tmp_path, data))


@pytest.mark.parametrize('script', ['../private.groovy', '/private.groovy', 'plugin.jar', 'nested/code.groovy', '.private.groovy'])
def test_declared_script_is_one_groovy_leaf(home, tmp_path, script):
    m = capability(home)
    path = catalog.entry_folder(m) / catalog.MANIFEST
    path.write_text(path.read_text().replace('filter.groovy', script))
    with pytest.raises(share.CantShare, match='one Groovy file'):
        share.pack(m)


def test_missing_declared_source_refuses_pack_and_import(home, tmp_path):
    m = capability(home)
    data = share.pack(m)
    (catalog.entry_folder(m) / 'filter.groovy').unlink()
    with pytest.raises(share.CantShare, match='missing declared'):
        share.pack(m)
    bad = mutate(data, {f'catalog/models/{m.id}/filter.groovy': None})
    with pytest.raises(share.CantShare, match='missing declared'):
        share._read(archive(tmp_path, bad))


@pytest.mark.parametrize('format_', [1, 3, True, ['invalid']])
def test_fiji_cannot_masquerade_as_python_or_an_unknown_format(home, tmp_path, format_):
    m = capability(home)
    data = mutate(share.pack(m), {'SHARED.json': json.dumps({'format': format_, 'id': m.id})})
    with pytest.raises(share.CantShare, match='format|kind of share'):
        share._read(archive(tmp_path, data))


def test_script_symlink_and_oversized_examples_are_refused(home, tmp_path):
    m = capability(home)
    folder = catalog.entry_folder(m)
    (folder / 'filter.groovy').unlink()
    (folder / 'filter.groovy').symlink_to(folder / 'private.groovy')
    with pytest.raises(share.CantShare, match='links'):
        share.pack(m)
    (folder / 'filter.groovy').unlink()
    (folder / 'filter.groovy').write_text('return image')
    (folder / 'example-synthetic-texture.png').write_bytes(b'x' * (share.MAX_FILE + 1))
    with pytest.raises(share.CantShare, match='too big'):
        share.pack(m)


def test_export_requires_sandbox_and_exact_proof_then_uses_installed_snapshot(home, monkeypatch):
    m = capability(home)
    monkeypatch.setattr(catalog, 'get', lambda key: m)
    monkeypatch.setattr(fiji_sandbox, 'supported', lambda: False)
    with pytest.raises(share.CantShare, match='sandbox'):
        share.export(m.key)
    monkeypatch.setattr(fiji_sandbox, 'supported', lambda: True)
    monkeypatch.setattr(fiji_runner, 'in_use', lambda *args: None)
    with pytest.raises(share.CantShare, match='check this Fiji'):
        share.export(m.key)
    snapshot = dataclasses.replace(m, folder=str(home / 'snapshot'))
    shutil.copytree(catalog.entry_folder(m), catalog.entry_folder(snapshot))
    (catalog.entry_folder(m) / 'filter.groovy').write_text('an unproven edit')
    monkeypatch.setattr(fiji_runner, 'in_use', lambda *args: SimpleNamespace(m=snapshot, proof={'passed': True}))
    _, _, files = share._read(share.export(m.key))
    assert files['filter.groovy'] == b'return image\n'


def test_unsupported_recipient_refuses_before_creating_a_channel(home, monkeypatch, tmp_path):
    m = capability(home)
    path = archive(tmp_path, share.pack(m))
    monkeypatch.setattr(fiji_sandbox, 'supported', lambda: False)
    with pytest.raises(share.CantShare, match='sandbox is unavailable'):
        share.open_file(path)
    assert not (share.root() / 'repos').exists()


def test_second_profile_import_preserves_script_attribution_and_permission_gate(home, monkeypatch, tmp_path):
    m = capability(home)
    path = archive(tmp_path, share.pack(m, by='Maya'))
    monkeypatch.setattr(fiji_sandbox, 'supported', lambda: True)
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'recipient'))
    catalog.manifests.cache_clear()
    result = share.open_file(path)
    received = catalog.get(result['model'])
    assert received is not None, catalog.problems()
    assert received.runner == 'fiji' and received.namespace == result['channel']
    assert received.contributed_by == {'name': 'Maya'}
    assert (catalog.entry_folder(received) / 'filter.groovy').read_bytes() == b'return image\n'
    assert (catalog.entry_folder(received) / 'LICENSE').read_bytes() == b'Original attribution\r\n'
    assert not channels.trusted(received)
    assert fiji_runner.in_use(received) is None
    assert share.open_file(path)['says'].endswith('from a file shared earlier')


def test_fiji_channel_identity_includes_names_and_full_content():
    first = share.channel_name('same', {'model.toml': b'a', 'one.groovy': b'bc'}, share.FIJI_FORMAT)
    second = share.channel_name('same', {'model.toml': b'ab', 'one.groovy': b'c'}, share.FIJI_FORMAT)
    third = share.channel_name('same', {'model.toml': b'a', 'two.groovy': b'bc'}, share.FIJI_FORMAT)
    assert len({first, second, third}) == 3
    assert len(first.removeprefix('fiji-')) == 32


def test_oversized_archive_is_refused_before_zip_parser(tmp_path, monkeypatch):
    path = tmp_path / 'large.zip'
    with path.open('wb') as f:
        f.truncate(share.MAX_ARCHIVE + 1)
    monkeypatch.setattr(share.zipfile, 'ZipFile', lambda *_: pytest.fail('oversized archive reached parser'))
    with pytest.raises(share.CantShare, match='too big'):
        share._read(path)


def test_directory_entry_limit_is_checked_before_zip_parser(tmp_path, monkeypatch):
    path = tmp_path / 'many.zip'
    with zipfile.ZipFile(path, 'w') as z:
        for i in range(share.MAX_ENTRIES + 1):
            z.writestr(str(i), b'')
    monkeypatch.setattr(share.zipfile, 'ZipFile', lambda *_: pytest.fail('oversized directory reached parser'))
    with pytest.raises(share.CantShare, match='too many'):
        share._read(path)
