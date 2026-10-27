"""Opt-in real macOS contribution reproduction through production package and setup paths."""
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, envs, fiji_contributions, fiji_generations, fiji_runner, fiji_sandbox, share


@pytest.mark.skipif(not os.environ.get('FIJI_SANDBOX_TEST_RUNTIME'), reason='requires isolated pinned Fiji runtime')
def test_real_shared_fiji_reproduces_in_second_profile_and_failed_update_preserves_first(tmp_path, monkeypatch):
    runtime = Path(os.environ['FIJI_SANDBOX_TEST_RUNTIME']).resolve()
    assert fiji_sandbox.supported()
    profiles = [tmp_path / name for name in ('creator', 'recipient')]
    generation = int(runtime.parent.name)
    for profile in profiles:
        tools = profile / 'tools' / 'fiji'
        tools.mkdir(parents=True)
        subprocess.run(['cp', '-cR', str(runtime.parent), str(tools / str(generation))], check=True)
        (tools / 'current').write_text(str(generation))
    monkeypatch.setenv('MICROMIND_CATALOG', str(profiles[0]))
    source = catalog.ROOT / 'fiji-gaussian-blur'
    target = catalog.mine_root() / 'journey-portable-blur'
    shutil.copytree(source, target)
    manifest = target / catalog.MANIFEST
    manifest.write_text(manifest.read_text().replace('fiji-gaussian-blur', target.name))
    (target / 'NOTICE').write_text('Derived from the bundled Gaussian Blur capability for a private test.\n')
    catalog.mine.cache_clear()
    m = catalog.get('mine/journey-portable-blur')
    assert m is not None, catalog.problems()
    before = fiji_generations.receipt(generation)
    envs.install(m)
    installed = fiji_runner.in_use(m)
    assert installed is not None
    pixels = fiji_runner._picture(target / 'example-synthetic-texture.png')
    first = fiji_runner.run_function(installed.m, pixels, {'sigma': 2}, n=installed.n)['picture']
    package = share.export(m.key, by='Private test')
    active = (fiji_contributions.root(m) / 'active.json').read_bytes()
    manifest.write_text(manifest.read_text().replace('Gaussian Blur...', 'No such Fiji command'))
    changed, problems = catalog.read(manifest, 'mine')
    assert changed is not None and not problems
    with pytest.raises(fiji_generations.Failed):
        fiji_contributions.install(changed)
    assert (fiji_contributions.root(m) / 'active.json').read_bytes() == active
    retained = fiji_runner.in_use(changed)
    assert retained is not None and not retained.current
    assert np.array_equal(first, fiji_runner.run_function(retained.m, pixels, {'sigma': 2}, n=retained.n)['picture'])
    assert fiji_generations.receipt(generation) == before
    monkeypatch.setenv('MICROMIND_CATALOG', str(profiles[1]))
    catalog.mine.cache_clear()
    opened = share.open_file(package)
    recipient = catalog.get(opened['model'])
    assert recipient is not None and not channels.trusted(recipient)
    assert fiji_runner.in_use(recipient) is None
    channels.trust(opened['channel'])
    envs.install(recipient)
    reproduced = fiji_runner.in_use(recipient)
    assert reproduced is not None
    second = fiji_runner.run_function(reproduced.m, pixels, {'sigma': 2}, n=reproduced.n)['picture']
    assert np.array_equal(first, second)
    assert fiji_generations.receipt(generation) == before
