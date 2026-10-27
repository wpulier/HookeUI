"""Prompt-contract regression only; actual planner quality is measured separately."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.fast import decide
from tests.test_sight import make_brain


@pytest.mark.parametrize(('followup', 'point_only'), [(False, False), (True, False), (False, True)])
def test_fast_and_followup_carry_capability_and_exact_target_contract(followup, point_only):
    brain, _, _ = make_brain({}, {})
    prompts = []

    async def ask(images, prompt, **kwargs):
        prompts.append(prompt)
        return SimpleNamespace(data={'intent': 'request', 'needs_look': True, 'actions': []},
                               model='gemini-3.8-flash', latency_ms=1)

    asyncio.run(decide(brain.moment(), 'highlight the requested parts', SimpleNamespace(ask=ask),
                       'Declared tools: find, highlight, zoom', 'gemini-3.8-flash', followup=followup, point_only=point_only))
    assert len(prompts) == 1
    prompt = prompts[0]
    assert 'Declared tools: find, highlight, zoom' in prompt
    for requirement in ('tool catalogue below is authoritative', 'non-microscope subjects',
                        'Specialist tools retain their declared', 'containing object is not the requested part',
                        'find(what=the requested part, inside=the containing ID)',
                        'Never clear an ID and then address it', 'highlight directly replaces earlier app highlights'):
        assert requirement in prompt
    if point_only:
        assert 'leave actions empty when no listed ID denotes the requested thing' in prompt
        assert 'No other tools' in prompt


def test_shared_spatial_contract_reaches_finder_camera_and_both_fast_rounds():
    from orchestrator.prompts import FIND, TOOLS_SECTION
    from orchestrator.brain.fast import FAST, FOLLOWUP

    for prompt in (FIND, TOOLS_SECTION, FAST, FOLLOWUP):
        assert 'Image-left and image-right mean the viewer' in prompt
        assert "subject's own perspective" in prompt
        assert 'Keep generic part labels neutral' in prompt
        assert 'Never invent a mirror transform' in prompt
        assert 'qualify the uncertainty' in prompt
    # Normal planning must not inherit the optional point-only tool restriction.
    for prompt in (FAST, FOLLOWUP):
        assert 'Under point-only restrictions' not in prompt


def test_measurement_scope_guidance_reaches_planning_and_outcome_answers():
    from orchestrator.prompts import ASK, BRAIN_FC, MEASUREMENT_BASIS, QUERY_ANSWER, RECONCILE, with_tools
    from orchestrator.brain.fast import FAST, FOLLOWUP

    camera = with_tools(ASK, 'Declared tools: track_motion, inspect_camera', 'No outlines')
    for prompt in (FAST, FOLLOWUP, camera, BRAIN_FC, QUERY_ANSWER, RECONCILE):
        assert MEASUREMENT_BASIS in prompt
        assert 'Answer the original question directly' in prompt
        assert 'do not identify the things measured' in prompt
        assert 'the particular thing the user' in prompt
        assert 'say what remains unknown' in prompt
    assert 'Declared tools: track_motion, inspect_camera' in camera


def test_without_a_scale_every_answering_prompt_states_no_size_in_pixels():
    """Live 2026-10-11: "cell c1487, with an area of 3,472 px²" was said with no scale set. AGENTS.md: without a
    scale no size is stated. Pixel measurements stay facts for comparing and ranking (the summary says "no scale,
    so no µm"); the answer rule says so wherever an answer is written."""
    from orchestrator.prompts import ASK, BRAIN_FC, QUERY_ANSWER, RECONCILE, with_tools
    from orchestrator.brain.fast import FAST, FOLLOWUP

    camera = with_tools(ASK, 'Declared tools: track_motion, inspect_camera', 'No outlines')
    for prompt in (FAST, FOLLOWUP, camera, BRAIN_FC, QUERY_ANSWER, RECONCILE):
        assert ('Without a scale, pixel measurements only compare and rank things: the answer states no length or '
                'area in px or px² unless the user asks for pixels.') in ' '.join(prompt.split())
