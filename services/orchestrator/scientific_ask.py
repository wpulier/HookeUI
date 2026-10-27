"""Typed Ask owns one explicit saved native input; no live-frame fallback or new store."""
import asyncio
import copy
import json
import re
from contextlib import asynccontextmanager

from .brain.registry import ToolFailed
from . import retained_inputs


@asynccontextmanager
async def admitted(hub, value, request):
    if value is None:
        yield None
        return
    origin = getattr(request, 'workspace_origin', None)
    if (not isinstance(value, dict) or set(value) != {'reference', 'digest', 'tab'}
            or not isinstance(origin, dict) or value.get('tab') != origin.get('tab')
            or not isinstance(value.get('digest'), str) or not re.fullmatch('[0-9a-f]{64}', value['digest'])
            or not isinstance(value.get('reference'), dict) or value['reference'].get('kind') != 'array'
            or value['reference'].get('session') != origin.get('session')):
        raise ToolFailed('The saved attachment does not belong to this Project and Tab.', next='attach the saved input again before sending')
    selected = copy.deepcopy(value)
    if request.superseded('session'):
        raise ToolFailed('The attachment belongs to an earlier Project opening.', next='attach it again')
    async with retained_inputs.admitted(hub, selected['reference'], origin['session']) as capture:
        if (request.superseded('session') or capture.source_image['nativeSource']['revisionDigest'] != selected['digest']):
            raise ToolFailed('The captured saved revision changed or its Project was reopened.', next='reopen and attach the saved revision again')
        request.hold(capture, 'analysis', 'look')
        request.scientific_input = capture
        yield capture


def facts(capture):
    source = capture.source_image
    return {'channel': 'retained-source', 'source': copy.deepcopy(source['resource']),
            'revisionDigest': source['nativeSource']['revisionDigest'],
            'scientific': copy.deepcopy(source['scientific']), 'sha256': source['sha256']}


def notes(capture):
    return ('Explicit typed-Ask attachment: the saved native input below, not the live Stage. '
            'Its saved revision is immutable; do not substitute a newer revision or camera picture. '
            'Use run_capability for declared supported operations on this input. Camera/scene effects '
            'and research handoff are unavailable for this attachment; say the limitation if requested. '
            + json.dumps(facts(capture), sort_keys=True))


async def decode(capture):
    """Keep the caller's hold until an off-loop bounded decode actually ends on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(capture.bgr))
    cancelled = False
    while True:
        try:
            image = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                image = task.result()
                break
    if cancelled:
        raise asyncio.CancelledError()
    if image is None:
        raise ToolFailed('The saved input could not be decoded.', next='reopen and attach it again')
    return image
