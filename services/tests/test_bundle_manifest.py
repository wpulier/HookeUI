"""Bundle ownership and legacy digest compatibility; no upstream execution."""
import dataclasses

from gpu_worker import manifests as catalog
from orchestrator import envs

HEAD = '''requires = { catalog = 3 }
id = "image-tools"
name = "Image tools"
runs = "cpu"
version = "1"
licence = ["image-tools"]
runner = "python-env"
env = { python = "3.13.5", lock = "env.lock", imports = ["numpy"] }
needs_to_run = ["install:image-tools"]
component = { what = "Image tools", licence = "MIT", url = "https://example.org/tools", tier = "open" }
'''
MEMBERS = '''
[[members]]
id = "invert"
name = "Invert image"
adapter = "adapters.invert:run"
inputs = [{ id = "image", type = "image.2d.rgb8" }]
outputs = [{ id = "result", type = "image.2d.rgb8" }]
[[members]]
id = "mean"
name = "Mean intensity"
adapter = "adapters.measure:run"
inputs = [{ id = "image", type = "image.2d.rgb8" }]
outputs = [{ id = "table", type = "table.objects" }]
'''


def write(tmp_path, text=HEAD + MEMBERS):
    folder = tmp_path / 'image-tools'
    folder.mkdir(exist_ok=True)
    (folder / 'model.toml').write_text(text)
    (folder / 'env.lock').write_text('')
    return catalog.read(folder / 'model.toml', 'mine')


def test_bundle_owns_one_environment_and_two_distinct_declarations(tmp_path):
    from gpu_worker.bundle_contract import parse_members, view
    owner, errors = write(tmp_path)
    assert owner is not None, errors
    members = parse_members(list(owner.members), owner)
    assert [view(owner, m).key for m in members] == ['mine/image-tools#invert', 'mine/image-tools#mean']
    assert not owner.implements and not owner.adapter and not owner.contract
    assert owner.env['lock'] == 'env.lock'


def test_old_reader_explicitly_rejects_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, 'CATALOG_FORMAT', 2)
    owner, errors = write(tmp_path)
    assert owner is None and isinstance(errors[0], catalog.Incompatible)


def test_bundle_cannot_mix_owner_and_member_operations(tmp_path):
    for extra in ('adapter = "adapter:run"\n', 'operation = "cells"\n', 'inputs = []\n'):
        owner, errors = write(tmp_path, HEAD + extra + MEMBERS)
        assert owner is None and errors


def test_member_code_changes_generation_but_display_words_do_not(tmp_path):
    owner, errors = write(tmp_path)
    assert owner is not None, errors
    before = envs.entry_digest(owner)
    words = [dict(m) for m in owner.members]
    words[0]['name'] = 'Different title'
    assert envs.entry_digest(dataclasses.replace(owner, members=tuple(words))) == before
    words[0]['adapter'] = 'adapters.changed:run'
    assert envs.entry_digest(dataclasses.replace(owner, members=tuple(words))) != before


def test_nonbundle_format_cannot_smuggle_members(tmp_path):
    for fmt in (1, 2):
        owner, errors = write(tmp_path, (HEAD + MEMBERS).replace('catalog = 3', f'catalog = {fmt}'))
        assert owner is None and errors
