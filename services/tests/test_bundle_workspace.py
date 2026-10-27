"""Bundle returns use the public authoring workspace and portable share boundary."""
import shutil

import pytest

from gpu_worker import manifests as catalog
from orchestrator import bundle_runtime, capability_workspace as workspaces, contribute, share
from tests.test_channels import home  # noqa: F401
from tests.test_bundle_share import package


def candidate(home):
    owner = package(home)
    environ = {'MICROMIND_CATALOG': str(home / 'authoring')}
    item = workspaces.create('Invert and measure', environ=environ)
    shutil.copytree(catalog.entry_folder(owner), item.candidate)
    manifest = item.candidate / 'model.toml'
    manifest.write_text(manifest.read_text().replace('image-tools', item.candidate_id))
    return item, environ


def test_public_workspace_packages_declared_bundle_modules_and_references(home):
    item, environ = candidate(home)
    returned = workspaces.package(item.id, by='Maya', environ=environ)
    shared, id_, files = share._read(returned)
    assert id_ == item.candidate_id and shared['format'] == 3 and shared['proof'] == {}
    assert 'adapters/operations.py' in files and 'adapters/__init__.py' in files
    assert {'reference-invert.png', 'reference-mean.json'} <= files.keys()
    assert 'activation.json' not in files and 'private.key' not in files and 'adapters/unrelated.py' not in files
    assert catalog.mine(environ) == {}


@pytest.mark.parametrize('damage', ['missing-reference', 'symlink-module', 'wrong-identity'])
def test_bundle_workspace_refuses_incomplete_unsafe_or_reused_candidates(home, damage):
    item, environ = candidate(home)
    if damage == 'missing-reference':
        (item.candidate / 'reference-mean.json').unlink()
    elif damage == 'symlink-module':
        module = item.candidate / 'adapters/operations.py'
        module.unlink()
        module.symlink_to(item.candidate / 'private.key')
    else:
        manifest = item.candidate / 'model.toml'
        manifest.write_text(manifest.read_text().replace(item.candidate_id, 'existing-owner'))
    with pytest.raises(workspaces.WorkspaceError):
        workspaces.package(item.id, environ=environ)
    assert not list(item.folder.glob('*.observationui.zip'))
    assert catalog.mine(environ) == {}


def scientific_candidate(home, *, reader=False):
    from tests.test_bundle_manifest import HEAD

    environ = {'MICROMIND_CATALOG': str(home / 'authoring')}
    item = workspaces.create('Inspect scientific input', environ=environ)
    item.candidate.mkdir()
    manifest = HEAD.replace('image-tools', item.candidate_id) + '''
[[members]]
id = "inspect"
name = "Scientific inspection"
adapter = "adapter:create"
bridge = "scientific-host-v1"
inputs = [{id = "project", type = "project.scientific"}]
outputs = [{id = "result", type = "project.scientific"}]
'''
    if reader:
        manifest += '''reader = { format = 3, input = "source", kind = "file", patterns = ["*.npy"], maximum_bytes = 1024, keeps = ["values", "dtype", "byte-order", "axes", "calibration"] }
'''
    (item.candidate / 'model.toml').write_text(manifest)
    (item.candidate / 'env.lock').write_text('')
    # Metadata-only packaging must not execute unreviewed author code.
    (item.candidate / 'adapter.py').write_text('raise RuntimeError("candidate code must not execute during packaging")\n')
    return item, environ


@pytest.mark.parametrize('reader', [False, True])
def test_scientific_authoring_candidate_can_travel_without_rgb_proof_or_activation(home, reader):
    item, environ = scientific_candidate(home, reader=reader)
    findings = contribute.preflight(str(item.candidate), environ)
    assert not [finding.says for finding in findings if finding.blocks]
    assert any('not a proven tool' in finding.says for finding in findings)
    returned = workspaces.package(item.id, environ=environ)
    shared, id_, files = share._read(returned)
    assert id_ == item.candidate_id and shared['proof'] == {}
    assert 'adapter.py' in files and 'activation.json' not in files
    assert catalog.mine(environ) == {}
    owner, errors = catalog.read(item.candidate / 'model.toml')
    assert owner is not None and not errors
    assert not bundle_runtime.proof_complete(owner, item.candidate, {'ok': True})
    instructions = workspaces.instructions(item.id, environ)
    assert 'unverified review candidate' in instructions
    assert 'Packaging never makes a tool ready' in instructions


@pytest.mark.parametrize('damage', ['missing-adapter', 'unhashed-lock', 'unfinished', 'unproven-operation'])
def test_scientific_candidate_still_requires_safe_complete_package_and_other_member_proof(home, damage):
    item, environ = scientific_candidate(home)
    if damage == 'missing-adapter':
        (item.candidate / 'adapter.py').unlink()
    elif damage == 'unhashed-lock':
        (item.candidate / 'env.lock').write_text('numpy==2.3.4\n')
    elif damage == 'unfinished':
        (item.candidate / 'adapter.py').write_text('# TODO implement the scientific tool\n')
    else:
        with (item.candidate / 'model.toml').open('a') as stream:
            stream.write('''
[[members]]
id = "measure"
name = "Unproven measure"
adapter = "adapter:measure"
inputs = [{id = "image", type = "image.2d.rgb8"}]
outputs = [{id = "result", type = "table.objects"}]
''')
    with pytest.raises(workspaces.WorkspaceError):
        workspaces.package(item.id, environ=environ)
    assert not list(item.folder.glob('*.observationui.zip'))
    assert catalog.mine(environ) == {}


def test_declared_scientific_bundle_has_an_honest_derived_authoring_handoff(home):
    source, environ = scientific_candidate(home, reader=True)
    original = (source.candidate / 'model.toml').read_bytes()
    item = workspaces.create('Adapt scientific reader', input='project.scientific', output='project.scientific',
                             source=str(source.candidate), environ=environ)
    text = workspaces.instructions(item.id, environ)
    assert 'Requested input: project.scientific. Requested output: project.scientific.' in text
    assert 'orchestrator.contribute derive' in text
    assert 'orchestrator.contribute new' not in text and 'bench.capability_test' not in text
    assert 'orchestrator.contribute lock' not in text and 'Preserve its hashed lock' in text
    contribute.derive(str(source.candidate), item.candidate_id, workspace=item.folder, environ=environ)
    returned = workspaces.package(item.id, environ=environ)
    shared, identifier, files = share._read(returned)
    assert identifier == item.candidate_id and shared['proof'] == {}
    assert b'derived_from' in files['model.toml']
    assert (source.candidate / 'model.toml').read_bytes() == original
    assert catalog.mine(environ) == {}


@pytest.mark.parametrize('source_kind', ['missing', 'ordinary'])
def test_scientific_handoff_requires_a_declared_scientific_source(home, source_kind):
    source, environ = candidate(home)
    path = source.candidate if source_kind == 'ordinary' else home / 'missing'
    before = set(workspaces.root(environ).iterdir())
    with pytest.raises(workspaces.WorkspaceError, match='existing declared scientific-host bundle'):
        workspaces.create('Adapt reader', input='project.scientific', output='project.scientific',
                          source=str(path), environ=environ)
    assert set(workspaces.root(environ).iterdir()) == before


def test_scientific_return_cannot_silently_change_the_requested_contract(home):
    source, environ = scientific_candidate(home)
    item = workspaces.create('Adapt reader', input='project.scientific', output='project.scientific',
                             source=str(source.candidate), environ=environ)
    contribute.derive(str(source.candidate), item.candidate_id, workspace=item.folder, environ=environ)
    manifest = item.candidate / 'model.toml'
    text = manifest.read_text().replace('"bridge" = "scientific-host-v1", ', '')
    manifest.write_text(text.replace('project.scientific', 'image.2d.rgb8'))
    with pytest.raises(workspaces.WorkspaceError, match='declared scientific-host contract'):
        workspaces.package(item.id, environ=environ)
    assert not list(item.folder.glob('*.observationui.zip'))
