"""Opt-in timing seam for the isolated workspace review, never a replacement worker."""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass

from .brain.registry import ToolFailed


@dataclass
class OperationDelay:
    milliseconds: int
    client: object
    run: str | None
    session: str
    consumed: bool = False


_DELAY: contextvars.ContextVar[OperationDelay | None] = contextvars.ContextVar('workspace-study-delay', default=None)


def action_delay(message, client, session):
    if 'workspaceStudyDelayMs' not in message:
        return None
    value = message['workspaceStudyDelayMs']
    if os.environ.get('MICROMIND_WORKSPACE_STUDY') != '1':
        raise ToolFailed('controlled workspace delay is only available in the isolated review profile', next=None)  # no next: this test seam cannot run outside its isolated profile
    if type(value) is not int or not 1 <= value <= 15000:
        raise ToolFailed('controlled workspace delay must be between 1 and 15000 milliseconds', next=None)  # no next: the refusal states the accepted test interval
    return OperationDelay(value, client, message.get('run'), session)


@contextmanager
def delaying(delay):
    token = _DELAY.set(delay)
    try:
        yield
    finally:
        _DELAY.reset(token)


async def before_execution(capture, execution):
    delay = _DELAY.get()
    if delay is None or delay.consumed or capture is None:
        return
    delay.consumed = True
    source = getattr(capture, 'source_image', None) or {}
    delay.client.offer(json.dumps({'type': 'workspace-study-delay', 'run': delay.run,
        'session': delay.session, 'milliseconds': delay.milliseconds, 'execution': execution.id,
        'capture': getattr(capture, 'id', None), 'source': source.get('asset'), 'phase': 'captured'}))
    await asyncio.sleep(delay.milliseconds / 1000)


def profile_id(paths):
    if os.environ.get('MICROMIND_WORKSPACE_STUDY') != '1':
        raise ToolFailed('workspace profile identity is only available in the isolated review profile', next=None)  # no next: this legacy review endpoint is intentionally unavailable here
    from .workspace_profile import profile_id as namespace

    return namespace(paths)
