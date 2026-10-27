"""A deliberate Try it chooses a proved option and prepares an owned, portable input when the stage is empty."""
from __future__ import annotations

import asyncio
import time
import uuid

from . import capability_example, capability_shelf
from .brain.registry import ToolFailed

# The normal product interaction for each job. Requests with real prerequisites ask for those inputs through the
# existing agent/action path; a generated example is always identified as synthetic, never a scientific specimen.
TRIALS = {
    'vision': ('ask', 'Tell me what is visible in this picture. Distinguish observation from interpretation.'),
    'cells': ('ask', 'Outline and measure every cell or particle in this picture.'),
    'outline': ('click', 'Click or drag a box around an object to try this outline method.'),
    'fixed:find-all': ('ask', 'Find all objects like the one I selected. If I have not selected one, help me choose it first.'),
    'voice': ('voice', 'Talk is ready. Say what you want to inspect in the picture.'),
    'research': ('ask', 'Research what is visible in this picture, keeping observed evidence separate from hypotheses.'),
    'embed': ('ask', 'Group the segmented cells by how they look. If segmentation is missing, tell me which step is needed.'),
    'fixed:lesson-writer': ('ask', 'Make a short interactive lesson about this view. Label synthetic examples honestly.'),
    'image': ('ask', 'Make an explanatory illustration of this view, clearly labelled as an illustration.'),
    'fixed:video': ('ask', 'Make a short explanatory video about this view, clearly labelled as a generated explanation.'),
    'zeroshot': ('click', 'Click an object to try medical-name ranking. A synthetic example does not establish a biological identity.'),
    'fixed:link': ('ask', 'Link the segmented cells in two saved captures. Help me choose both captures if they are not selected.'),
}

# Local operations already have a complete human form. Opening it must not require an unrelated chat provider.
# Keep the legacy gesture recipes above for older clients' prepared-token consumption.
LOCAL_ACTIONS = {'cells': 'cells', 'embed': 'group-cells'}


def _option(app, job: str, option: str) -> dict:
    wire = app.capabilities_wire(counts=False)
    role = next((r for r in wire['roles'] if r['id'] == job), None)
    chosen = next((o for o in capability_shelf._options(role) if o['id'] == option), None) if role else None
    if chosen is None or not capability_shelf._proven(chosen):
        raise ToolFailed('That option has no current passed functional check; turn it on first', next='open its capability card')
    return chosen


async def prepare(app, client, job: str, option: str, *, empty: bool) -> dict:
    chosen = _option(app, job, option)
    if job not in TRIALS:
        raise ToolFailed('Try this supporting model through its main capability', next='choose a capability at the top of Models')
    asked = app.day.here()
    asset = None
    if empty:
        picture = await asyncio.to_thread(capability_example.picture, app.paths)
        aid = await asyncio.to_thread(app.hub.library.register, 'public-example:three-particles-v1', picture,
                                      title=capability_example.TITLE, at=time.time(), origin=None, group='example')
        found = await asyncio.to_thread(app.hub.library.get, aid)
        if found is None:
            raise ToolFailed('The example could not be opened from the library', next='refresh the Models page')
        asset = {**found.metadata(), 'thumb': f'/api/library/{aid}/thumbnail'}
    if asked != app.day.here() or asked.session != app.day.sid:
        raise ToolFailed('That conversation is no longer open', next='try from the conversation now open')
    action, words = TRIALS[job]
    token = uuid.uuid4().hex
    offers = getattr(app, '_capability_trials', {})
    now = time.time()
    app._capability_trials = {key: row for key, row in offers.items() if now - row['at'] < 180}
    app._capability_trials[token] = {'job': job, 'option': option, 'asked': asked, 'client': client, 'at': now}
    configured = LOCAL_ACTIONS.get(job)
    return {'ok': True, 'job': job, 'option': option, 'token': token, 'session': asked.session, 'asset': asset,
            'action': 'configure' if configured else action,
            **({'capability': configured} if configured else {}),
            'words': 'Configure this operation, then press Run. Its model choice applies to this run only.' if configured else words,
            'label': chosen['said'] or chosen['label']}


def consume(app, client, token: str, action: str) -> dict:
    """One prepared gesture, owned by its original conversation and connection."""
    offer = getattr(app, '_capability_trials', {}).pop(str(token), None)
    if offer is None or time.time() - offer['at'] >= 180:
        raise ToolFailed('That Try it offer expired or was consumed', next='press Try it again')
    if offer['client'] is not client or offer['asked'] != app.day.here() or offer['asked'].session != app.day.sid:
        raise ToolFailed('That Try it offer belongs to another conversation or connection', next='press Try it here')
    if TRIALS[offer['job']][0] != action:
        raise ToolFailed('That Try it offer needs its indicated interaction', next='press Try it again')
    _option(app, offer['job'], offer['option'])  # downloaded/proved facts may have changed meanwhile
    return offer


async def apply(app, offer: dict) -> None:
    """Use the normal per-request chooser, so receipt, refusal and exact-model policy agree."""
    from .commands import run_tool
    job = offer['job']
    if job.startswith('fixed:'):
        role = next(r for r in app.capabilities_wire(counts=False)['roles'] if r['id'] == job)
        active = 'fallback' if (role.get('fallback') or {}).get('active') else 'primary'
        if offer['option'] != active:
            raise ToolFailed('That fixed capability changed its active provider', next='press Try it again')
        return  # the normal fixed path already chooses precisely this active provider
    if job == 'voice':
        return  # the Talk endpoint receives the prepared model explicitly
    if job == 'zeroshot':
        from .ticket import current
        request = current()
        if request is None or request.kind != 'click':
            raise ToolFailed('Medical ranking needs its prepared click', next='press Try it and click an object')
        # Global `use zeroshot` remains unsupported: this deliberate gesture owns only its click and its naming task.
        request.overrides[job] = offer['option']
        if not app.agent.available():
            raise ToolFailed('Naming this click also needs the Seeing model', next='turn on Tell you what you are looking at')
        if app.agent.zeroshot.candidates() is None:
            raise ToolFailed('This medical mode needs a blood or Pap smear session subject; the synthetic example has neither',
                             next='use a matching specimen, or choose and check MedSigLIP on every click')
        app.agent.log({'kind': 'capability-trial-choice', 'job': job, 'option': offer['option'], 'request': request.id,
                       'origin': 'user-chosen', 'scope': 'one click'})
        return
    chosen = await run_tool(app, 'use', {'operation': {'embed': 'looks'}.get(job, job),
                                       'implementation': offer['option'], 'by': 'user', 'policy': 'raw'})
    if not chosen['ok']:
        raise ToolFailed(chosen['error'] or 'That exact model cannot run here', next='open its capability card')
