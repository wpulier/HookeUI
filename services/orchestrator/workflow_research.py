"""Retained bounded-report workflow nodes; managed execution is an optional service.

Standalone core may reopen reports, but does not import the private dispatcher or
invent provider settlement. Tool-enabled local researchers drive workflows from
outside the graph through the shared researcher Hands interface.
"""
import math
from .scientific_projects import InvalidProject
from .scientific_comparisons import identity
from .research.efforts import Efforts, KEY, FINAL

UNAVAILABLE = 'This workflow researcher node requires a managed research service; use a local or connected researcher to drive capability workflows'

def validate(settings):
    from .experiment_steps import is_local, validate_local
    if is_local(settings):
        # The app's own researchers (experiment_steps): an option beside the managed service, which stays optional.
        validate_local(settings)
        return
    if (type(settings) is not dict or set(settings)!={'question','criteria','provider','maximumUsd','seconds','tools','output'}
            or any(type(settings[k]) is not str or not 0<len(settings[k])<=8000 for k in ('question','criteria'))
            or settings['provider']!='gemini-google' or settings['tools']!=[] or settings['output']!='report'
            or type(settings['maximumUsd']) not in (float,int) or not math.isfinite(settings['maximumUsd'])
            or not 0<settings['maximumUsd']<=100 or type(settings['seconds']) is not int or not 1<=settings['seconds']<=3600):
        raise InvalidProject('Research steps require a Gemini evidence report, no executable tools, explicit criteria, a positive allowance up to $100 and a 1–3600 second limit')


def readiness(settings, hub=None):
    validate(settings)
    from .experiment_steps import is_local, readiness_local
    if is_local(settings):
        readiness_local(hub, settings)
        return
    raise InvalidProject(UNAVAILABLE)


def start(workflows, claim):
    readiness(claim['node']['settings'])


def collect(workflows, claim, research, stopping=False, hub=None):
    from .experiment_steps import LOCAL, collect_local
    if research.get('provider')==LOCAL:
        return collect_local(hub, workflows, claim, research, stopping)
    service=Efforts(workflows.store)
    saved=service.read(research['id'],claim['session'])
    task=saved.document['state'][KEY]['tasks'].get(research['request'])
    if task is None:
        return {'status':'uncertain','output':identity(saved),'note':'Research dispatch has no task receipt. Reconcile the original service before retrying.'}
    if task['status'] not in FINAL:
        # An unavailable backend is not evidence of termination or cancellation.
        raise InvalidProject('The original research service must reconcile this retained task; its allowance remains held')
    if task['status']!='completed':
        return {'status':'stopped' if stopping else 'failed','output':identity(saved),'note':'Research ended without an accepted report.'}
    return {'status':'awaiting-review','output':identity(saved),'note':'Inspect the retained report and its exact evidence before continuing.'}
