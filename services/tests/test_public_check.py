"""A contributor's pytest process cannot borrow the invoking account's inputs or keys."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from orchestrator.keys import KNOWN_KEYS


@pytest.mark.parametrize("probe", ["assets", "credentials"])
def test_collection_and_execution_are_isolated_from_the_invoking_account(tmp_path, probe):
    services = Path(__file__).resolve().parents[1]
    suite = tmp_path / "suite"
    suite.mkdir()
    # Load the actual test boundary in a new interpreter, before collecting this probe.
    (suite / "conftest.py").symlink_to(services / "tests" / "conftest.py")
    inherited = tmp_path / "invoking-account"
    (inherited / "footage" / "real").mkdir(parents=True)
    (inherited / "footage" / "real" / "private.mp4").write_bytes(b"private sentinel")
    key_file = inherited / "keys.txt"
    key_file.write_text("OPENAI_API_KEY=synthetic-file-value\n")
    checks = {
        "assets": """
            import json
            from orchestrator import assets
            from orchestrator.assets import store
            from orchestrator.paths import Paths
            from gpu_worker.manifests import catalog_root

            # Check the boundary before enumerating: a failure never inspects the real home.
            root = store().root
            assert root.is_relative_to(Paths.from_env().home), 'assets escaped the isolated profile'
            assert not list(root.glob('**/*')), 'test assets did not start empty'
            assert catalog_root().is_relative_to(Paths.from_env().home)
            assert not list(catalog_root().glob('**/*')), 'test models did not start empty'

            def test_generated_inputs_are_local(tmp_path, monkeypatch):
                asset_id = 'synthetic-isolation-probe'
                manifest = tmp_path / 'synthetic-assets.json'
                manifest.write_text(json.dumps({'backup': {}, 'real': [
                    {'id': asset_id, 'file': f'footage/real/{asset_id}.mp4'}
                ], 'bench': []}))
                monkeypatch.setattr(assets, 'MANIFEST', manifest)
                local = store().real_file(asset_id)
                assert not local.exists()
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(b'generated fixture')
                assert store().real_file(asset_id).read_bytes() == b'generated fixture'
        """,
        "credentials": """
            from orchestrator import keys, keychain

            assert not any(key.present for key in keys.resolve().values()), 'inherited credential available at collection'
            assert not keychain.enabled(), 'account keychain is available'

            def test_a_test_can_supply_its_own_synthetic_key(monkeypatch):
                monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-local-value')
                key = keys.resolve()['OPENAI_API_KEY']
                assert key.present and key.source == 'environment' and key.value == 'synthetic-local-value'
        """,
    }
    (suite / "test_probe.py").write_text(textwrap.dedent(checks[probe]))
    env = {**os.environ, **dict.fromkeys(KNOWN_KEYS, "synthetic-inherited-value"),
           "MICROMIND_ASSETS": str(inherited), "MICROMIND_CATALOG": str(inherited / "catalog"),
           "MICROMIND_HOME": str(inherited), "MICROMIND_KEYS_FILE": str(key_file),
           "MICROMIND_KEYCHAIN": "on", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "PYTEST_ADDOPTS": "", "PYTEST_PLUGINS": "",
           "PYTHONPATH": str(services)}
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-ra", str(suite)],
                            cwd=services, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert (inherited / "footage" / "real" / "private.mp4").read_bytes() == b"private sentinel"
    assert list((inherited / "footage" / "real").iterdir()) == [inherited / "footage" / "real" / "private.mp4"]
