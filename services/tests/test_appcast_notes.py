"""The update offer's notes (packaging/macos/appcast.py notes_html): what Sparkle shows when an update is offered. The
0.3.0 offer and the first 0.4.0 candidate's showed every hard-wrapped line as a paragraph of its own and ** as typed
(seen in the update rehearsal VM, 2026-10-26)."""
import importlib.util
from pathlib import Path

APPCAST = Path(__file__).resolve().parents[2] / "packaging" / "macos" / "appcast.py"
spec = importlib.util.spec_from_file_location("appcast", APPCAST)
appcast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(appcast)


def test_wrapped_list_items_and_paragraphs_join_and_bold_and_code_render():
    notes = """# HookeUI Beta 0.4.0 — the workspace, alive

This Beta redesigns how the workspace looks and feels: a calm instrument where the specimen is the brightest thing on
screen and anything working draws the eye by itself.

## What changed

- **The lit cell.** One sign for the work of researchers, runs and setup, after Hooke's own cork cell: gold while
  something works, green when it is ready.
- **Tabs.** The Tab row holds only Tabs & Main is always first; see `moved.md`.
"""
    out = appcast.notes_html(notes)
    assert out == (
        "<h3>HookeUI Beta 0.4.0 — the workspace, alive</h3>"
        "<p>This Beta redesigns how the workspace looks and feels: a calm instrument where the specimen is the brightest "
        "thing on screen and anything working draws the eye by itself.</p>"
        "<h3>What changed</h3>"
        "<ul><li><strong>The lit cell.</strong> One sign for the work of researchers, runs and setup, after Hooke&#x27;s own "
        "cork cell: gold while something works, green when it is ready.</li>"
        "<li><strong>Tabs.</strong> The Tab row holds only Tabs &amp; Main is always first; see <code>moved.md</code>.</li></ul>")


def test_the_real_notes_have_no_stray_marks_or_split_sentences():
    notes = (Path(__file__).resolve().parents[2] / "packaging" / "macos" / "release-notes" / "0.4.0.md").read_text()
    out = appcast.notes_html(notes)
    assert "**" not in out and "<p>  " not in out
    # every list item is one item: no paragraph starts in lower case (a wrapped line cut from its sentence)
    import re
    assert not re.search(r"<p>[a-z]", out), re.findall(r"<p>[a-z][^<]{0,40}", out)
    assert out.count("<li>") == sum(1 for line in notes.splitlines() if line.startswith("- "))
