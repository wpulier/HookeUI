"""The real-check launcher retains authorized credentials, never production writes."""
import asyncio
import os
from types import SimpleNamespace

import pytest


def test_scratch_app_reload_keeps_selected_key_source(tmp_path, monkeypatch):
    from bench import capability_check
    from orchestrator.app import App
    from orchestrator import keys, keychain

    home = tmp_path / 'check-results'
    source = tmp_path / 'authorized-keys.txt'
    source.write_text('OPENAI_API_KEY=synthetic-check-sentinel\n')
    monkeypatch.setenv('MICROMIND_HOME', str(home))
    monkeypatch.setenv('MICROMIND_KEYS_FILE', str(source))
    monkeypatch.setenv('MICROMIND_KEYCHAIN', 'off')
    monkeypatch.setattr(keychain, '_store', None)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv(keys.APPLIED, raising=False)
    monkeypatch.setattr(capability_check.tempfile, 'mkdtemp', lambda **kw: str(tmp_path / 'scratch'))

    class Checked(Exception):
        pass

    def build(url, paths, **kw):
        keys.load_keys(paths.keys_file)  # App.build's actual reload boundary
        assert os.environ.get('OPENAI_API_KEY') == 'synthetic-check-sentinel'
        assert paths.keys_file == source
        assert paths.home == tmp_path / 'scratch'
        assert paths.logs.is_relative_to(paths.home)
        raise Checked

    monkeypatch.setattr(App, 'build', build)
    with pytest.raises(Checked):
        asyncio.run(capability_check.check(SimpleNamespace()))
