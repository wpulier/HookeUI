"""Read the existing population wire for an independent captured-evidence artifact.

No acquisition, analysis, layer selection or writes. Session and revision must still
match the captured head after checking the retained files on a worker thread.
"""
import asyncio
import copy


class OverlayRefused(ValueError):
    pass


async def read_overlay(app, eid: str, session: str, revision: int) -> dict:
    if type(revision) is not int or revision < 0:
        raise OverlayRefused('Choose an exact retained mask revision.')
    store = getattr(app.hub, 'evidence', None)
    item = store.get(eid) if store else None
    generation = app.day.generation
    head = item.revision if item is not None else None

    def current():
        if app.day.sid != session or app.day.generation != generation:
            raise OverlayRefused('Open the originating session to view these masks.')
        if item is None or store.get(eid) is not item or item.session != session:
            raise OverlayRefused('The retained masks are unavailable in this session.')
        if item.revision != head:
            raise OverlayRefused('The retained revision changed. Reopen the result; no newer masks were substituted.')
        if item.status in ('failed', 'unavailable'):
            raise OverlayRefused('The retained evidence is unavailable. Restore its files before reopening.')

    current()
    try:
        with store.exact_revision(item, head):
            current()
            data = copy.deepcopy(item.wire(revision=revision))
            data.update(width=item.view.get('width'), height=item.view.get('height'))
    except ValueError as exc:
        raise OverlayRefused(str(exc)) from None
    if not all(isinstance(data[k], int) and data[k] > 0 for k in ('width', 'height')):
        raise OverlayRefused('The retained capture dimensions are missing.')
    from .brain.registry import ToolFailed
    try:
        await asyncio.to_thread(store.require_retained, item)
    except ToolFailed as exc:
        raise OverlayRefused(str(exc)) from None
    try:
        with store.exact_revision(item, head):
            current()
    except ValueError as exc:
        raise OverlayRefused(str(exc)) from None
    return data
