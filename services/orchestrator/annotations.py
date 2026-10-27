"""User observations on exact retained sources; never labels inferred from the live view."""
from __future__ import annotations

import asyncio
import copy
import re

from .brain.registry import ToolFailed, step_output


def source_of(hub, day, target: str) -> dict:
    from .evidence import evidence_of
    from .resources import resources_of

    if re.fullmatch(r'[ce]\d+', target):
        evidence = evidence_of(hub)
        found = [(p, p.members.get(target)) for p in list(evidence.items.values()) if target in p.members or target == p.id]
        if len(found) != 1:
            raise ToolFailed(f'{target} does not resolve to one retained member', next='read the saved populations first')
        population, member = found[0]
        if population.status != 'saved' or population.path is None:
            raise ToolFailed(f'{target} is not saved yet', next='wait for its population to be saved')
        return {'day': day.sid, 'id': population.id, 'population': population.id, **({'member': member.id} if member else {}),
                'revision': population.revision, 'frame': population.capture_id, 'view': copy.deepcopy(population.view)}
    entry = resources_of(hub).get(target)
    if entry is None or entry.kind == 'annotation':
        raise ToolFailed(f'{target!r} is not a resolved retained source', next='read resources and choose its exact source ID')
    return {'day': entry.day, 'id': entry.id, 'kind': entry.kind, 'revision': entry.version,
            'capture': copy.deepcopy(entry.source)}


async def annotate(hub, day, args: dict, ctx) -> dict:
    from .ticket import current

    aid = str(args.get('id') or '')
    previous = (day.annotations.get(aid) or [None])[-1] if aid else None
    if aid and previous is None:
        raise ToolFailed(f'{aid} is not an annotation saved in this session', next='read resources of kind annotation')
    target = str(args.get('target') or '').strip()
    if target.startswith('@'):
        target = step_output(ctx, target)
    if previous is not None:
        provenance = copy.deepcopy(previous['source'])
        if target and target != (provenance.get('member') or provenance.get('id')):
            raise ToolFailed('editing a note cannot change its source', next='create a separate note on that source')
    else:
        provenance = await asyncio.to_thread(source_of, hub, day, target)
        existing = [history[-1] for history in day.annotations.values() if history
                    and history[-1]['source'].get('day') == provenance.get('day')
                    and (history[-1]['source'].get('member') or history[-1]['source'].get('id')) == target]
        if existing and args.get('separate') is not True:
            choices = '; '.join(f"{note['id']} ({note['title'] or 'untitled'}, version {note['version']})" for note in existing[:8])
            raise ToolFailed(f'{target} already has notes: {choices}. No new note was created and no note was edited',
                             next='use the exact annotation id to edit; separate=true only for an explicitly additional note')
    title = str(args.get('title', (previous or {}).get('title', '')))
    text = str(args.get('text', (previous or {}).get('text', '')))
    request = current()
    why = request.superseded('session') if request is not None else None
    if why or (ctx.asked is not None and not day.current(ctx.asked.routing_generation)):
        raise ToolFailed(why or 'the session changed before the note was saved', next='ask again in the current conversation')
    try:
        # One short append, without yielding between the scope check and persistence. A failed write is not a save.
        return day.record_annotation(provenance, text, title, annotation_id=aid, asked=ctx.asked)
    except (OSError, ValueError) as exc:
        raise ToolFailed(f'the note was not saved: {exc}', next='resolve the save problem and try again') from None
