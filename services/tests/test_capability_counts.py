"""Key-unlock counts resolve current choices, without constructing unused menus."""
from types import SimpleNamespace

import pytest

from orchestrator import capabilities, options, readiness


@pytest.mark.parametrize('keys,changes,problems,limited', [
    ({}, {}, {}, False),
    ({'GEMINI_API_KEY': True}, {}, {}, False),
    ({'ANTHROPIC_API_KEY': True}, {}, {}, False),
    ({'GEMINI_API_KEY': True, 'FAL_KEY': True}, {}, {}, False),
    ({'GEMINI_API_KEY': True}, {'sight': 'off'}, {}, False),
    ({'GEMINI_API_KEY': True}, {}, {'outline': 'Owned worker is unavailable'}, False),
    ({'GEMINI_API_KEY': True}, {}, {}, True),
])
def test_key_count_matches_full_availability_without_resolving_unselected_options(monkeypatch, keys, changes, problems, limited):
    settings = options.Settings()
    for name, value in changes.items():
        setattr(settings, name, value)
    registry = SimpleNamespace(tools={'models': object()}) if limited else None
    complete = capabilities.wire(settings, keys, registry, problems=problems)
    expected = sum(row['available'] for row in complete['capabilities'] if not row.get('generated'))
    original = readiness.option
    visited = []
    def selected(group, option, *args, **kwargs):
        visited.append((group, option))
        assert settings.to_wire().get(group) == option, 'A count must not resolve an unselected model'
        return original(group, option, *args, **kwargs)
    monkeypatch.setattr(readiness, 'option', selected)
    answer = capabilities.wire(settings, keys, registry, problems=problems, _counting=True)
    assert answer == {'works': expected}
    assert visited
