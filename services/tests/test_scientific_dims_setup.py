"""Synthetic regression for fresh-case UI setup; no native or browser qualification."""
from types import SimpleNamespace

import pytest

from tests.test_scientific_dims_browser import _set_order


@pytest.mark.parametrize('target', [[0, 1, 2, 3, 4], [0, 1, 2, 4, 3]])
def test_setting_order_from_a_fresh_view_applies_only_a_real_draft(target):
    """The default plane and 3D combination start at the requested order, with Apply disabled."""
    class Session:
        order = [0, 1, 2, 3, 4]
        draft = False
        changes = []
        def dims(self): return {'order': list(self.order)}
        def settle(self): assert not self.draft
        def choose(self, name, axis):
            position = int(name)
            other = self.order.index(axis)
            assert other != position, 'no input event when the selected option did not change'
            self.order = list(self.order)
            self.order[position], self.order[other] = self.order[other], self.order[position]
            self.draft = True
        def apply(self):
            assert self.draft, 'Apply is disabled without an unsent view change'
            self.changes = [*self.changes, list(self.order)]
            self.draft = False
            return 'View changes applied. Saved Project data is unchanged.'
    session = Session()
    class Page:
        def eval(self, expression):
            if expression.endswith('.open'): return True
            if '.disabled' in expression: return not session.draft
            return [str(n) for n in range(5)]
    studio = SimpleNamespace(session=session, page=Page())
    assert _set_order(studio, target).startswith('View changes applied')
    assert session.order == target and not session.draft
    assert session.changes[-1] == target
    if target == [0, 1, 2, 3, 4]:
        assert len(session.changes) == 2 and session.changes[0] != target
