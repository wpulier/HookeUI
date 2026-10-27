"""Synthetic workspace writes through the existing apply/Ticket boundary.

Window state has its own per-instance fences; a legacy magnifier close must not
cancel an independent window. Cancellation and a new day still cancel it.
No browser, camera, model or source delivery is exercised here.
"""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import apply as writes
from orchestrator.scene import Scene
from orchestrator.ticket import Ticket, generations


def setup():
    hub = SimpleNamespace(scene=Scene())
    return hub, Ticket(generations(hub), 'command')


def test_workspace_destination_is_explicit_and_needs_no_biological_capture():
    hub, ticket = setup()
    changed = []
    assert getattr(writes, 'WORKSPACE', None) == 'workspace', 'MISSING_FEATURE: explicit workspace destination'
    result = asyncio.run(writes.apply(hub, lambda place: changed.append(place) or 'window-a',
                                     scope='session', destination=writes.WORKSPACE, ticket=ticket))
    assert result.ok and result.value == 'window-a' and changed == [None]


def test_closing_legacy_magnifier_does_not_cancel_an_independent_window():
    hub, ticket = setup()
    generations(hub).bump('magnifier', why='the other window closed')
    changed = []
    result = asyncio.run(writes.apply(hub, lambda place: changed.append('independent-window'),
                                     scope='session', destination='workspace', ticket=ticket))
    assert result.ok and changed == ['independent-window']


@pytest.mark.parametrize('stop', ['cancel', 'day'])
def test_canceled_or_old_day_workspace_write_changes_nothing(stop):
    hub, ticket = setup()
    if stop == 'cancel':
        ticket.cancel('user canceled this request')
    else:
        generations(hub).bump('session', why='new day')
    changed = []
    result = asyncio.run(writes.apply(hub, lambda place: changed.append('late-window'),
                                     scope='session', destination='workspace', ticket=ticket))
    assert result.ok is False and result.reason and changed == []


def test_unknown_destination_cannot_silently_write_workspace_or_live_state():
    hub, ticket = setup()
    changed = []
    with pytest.raises(ValueError, match='destination'):
        asyncio.run(writes.apply(hub, lambda place: changed.append('unauthorized-write'),
                                 scope='session', destination='workspaec', ticket=ticket))
    assert changed == []


def test_workspace_state_write_does_not_run_live_geometry_placement():
    hub, ticket = setup()
    changed = []
    async def biological_placement():
        pytest.fail('workspace state was mislabeled as biological live placement')
    result = asyncio.run(writes.apply(hub, lambda place: changed.append(place), scope='session',
                                     destination='workspace', ticket=ticket, place=biological_placement))
    assert result.ok and result.placement is None and changed == [None]
