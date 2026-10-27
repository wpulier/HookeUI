"""Research argument syntax at the public Registry model-input boundary.

The existing catalogue builds real tool declarations with inert owners; this
checks description/schema parity, not installed App or worker capability.
"""
import re

import pytest

from orchestrator.brain.toolbox import catalogue


@pytest.mark.parametrize('tool_name', ['research_status', 'read_result', 'show_result'])
def test_research_reference_is_followable_in_every_model_menu(tool_name):
    registry = catalogue()
    for layout in ('flat', 'families', 'compact'):
        text = registry.prompt_section(layout)
        line = next(row for row in text.splitlines() if row.startswith(f'- "{tool_name}"('))
        # Inspect the argument itself, not unrelated tool prose or another tool's
        # reference description elsewhere in the prompt.
        detail = line.split('researcher?: ', 1)[1].split(', wait?:', 1)[0]
        detail = detail.split(', part?:', 1)[0].split(', item?:', 1)[0].split('):', 1)[0]
        assert 'number' in detail and 'id' in detail, (layout, line)
        assert re.search(r'\{\s*step\s*,\s*output\s*\}', detail), (layout, line)


def test_fc_keeps_legacy_and_exact_original_output_reference_shapes():
    tools = {tool['name']: tool for tool in catalogue().responses_tools()}
    for name in ('research_status', 'read_result', 'show_result'):
        reference = tools[name]['parameters']['properties']['researcher']
        legacy, original = reference['oneOf']
        assert legacy['type'] == 'string'
        assert original['type'] == 'object' and original['additionalProperties'] is False
        assert set(original['required']) == {'step', 'output'}
        assert original['properties']['step'] == {'type': 'integer', 'minimum': 1}
        assert original['properties']['output'] == {'type': 'integer', 'minimum': 0}
