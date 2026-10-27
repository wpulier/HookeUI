"""Bench hygiene (bench/hygiene.py, roadmap W0-7): prompt variants keep meaning, unasked actions
and consistency are counted on their own."""
import asyncio

from bench.hygiene import VARIANTS, Variant, summarise, tag
from orchestrator.brain.fast import FAST


def test_variants_change_formatting_not_content():
    prompt = "Line one of a\nwrapped paragraph.\n\n- a list item\n- another\n\nReply with JSON only:\n{\"a\": 1}"
    assert VARIANTS["as-is"](prompt) == prompt
    reflowed = VARIANTS["reflowed"](prompt)
    assert "Line one of a wrapped paragraph." in reflowed and "- a list item\n- another" in reflowed and '{"a": 1}' in reflowed
    assert VARIANTS["framed"](prompt).endswith(prompt)
    # The real fast prompt keeps every word under each variant.
    for name, transform in VARIANTS.items():
        assert set(FAST.split()) <= set(transform(FAST).split()), name


def test_variant_rewrites_the_prompt_it_sends():
    sent = []

    class Vision:
        async def ask(self, images, prompt, max_output_tokens=700):
            sent.append(prompt)
            return "reply"

    assert asyncio.run(Variant(Vision(), "framed").ask([], "hello", max_output_tokens=5)) == "reply"
    assert sent == ["The request below comes from a person at a microscope.\n\nhello"]


def test_summary_counts_unasked_actions_consistency_and_tags():
    rows = [
        {"variant": "as-is", "text": "Okay", "expected": "none", "used": ["highlight"], "right": False, "unasked": True},
        {"variant": "as-is", "text": "Okay", "expected": "none", "used": [], "right": True, "unasked": False},
        {"variant": "as-is", "text": "Zoom in", "expected": "zoom", "used": ["zoom"], "right": True, "unasked": False},
        {"variant": "as-is", "text": "Zoom in", "expected": "zoom", "used": ["zoom"], "right": True, "unasked": False},
        {"variant": "framed", "text": "Okay", "expected": "none", "used": [], "right": True, "unasked": False},
    ]
    out = summarise(rows)
    assert out["as-is"]["unasked"] == 0.5 and out["as-is"]["unaskedCases"] == ["Okay"]
    assert out["as-is"]["consistency"] == 0.5  # "Okay" disagreed across repeats, "Zoom in" agreed
    assert out["as-is"]["byTag"] == {"act": "2/2", "remark": "1/2"}
    assert out["spreadBetweenVariants"] == 0.25
    assert [tag(e) for e in ("none", None, "answer", "look", "zoom", ("a", "b"))] == ["remark", "remark", "notes", "vision", "act", "act"]
