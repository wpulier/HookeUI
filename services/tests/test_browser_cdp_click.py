"""Synthetic trusted-click admission; no Chromium or native scientific qualification."""
from types import SimpleNamespace

import pytest

from tests.browser_cdp import BrowserError, Page


@pytest.mark.parametrize('state', [
    {'visible': False, 'target': True, 'disabled': False},  # visibility is required even with a nonzero box
    {'visible': True, 'target': False, 'disabled': False},  # another control overlays the chosen point
    {'visible': True, 'target': True, 'disabled': True},
])
def test_hidden_obscured_or_disabled_control_sends_no_trusted_input(state):
    events = []
    page = SimpleNamespace(box=lambda selector: (100, 200), eval=lambda expression: state,
                           mouse=lambda *args, **kwargs: events.append((args, kwargs)))
    with pytest.raises(BrowserError, match='not a visible enabled hit target'):
        Page.click(page, 'selectedButton')
    assert events == []


def test_visible_control_or_its_descendant_receives_the_checked_offset_coordinate():
    events, reads = [], []
    def evaluate(expression):
        reads.append(expression)
        return {'visible': True, 'target': True, 'disabled': False}
    page = SimpleNamespace(box=lambda selector: (100, 200), eval=evaluate,
                           mouse=lambda *args, **kwargs: events.append((args, kwargs)))
    Page.click(page, 'selectedButton', offset=(3, -5))
    assert len(reads) == 1 and 'elementFromPoint(103, 195)' in reads[0]
    assert [row[0] for row in events] == [('mouseMoved', 103, 195), ('mousePressed', 103, 195), ('mouseReleased', 103, 195)]
