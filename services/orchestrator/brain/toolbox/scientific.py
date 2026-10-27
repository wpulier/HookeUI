"""Explicit accepted native Project input, through the built-in measurement service."""
import json

from ..registry import CURRENT_RUN, Tool, ToolFailed, attach, schema
from ... import scientific_measurement as measurement
from ... import population_native, method_workflow
from ...capture import Refused as MemoryRefused
from ...scientific_project_api import _owned, _wire
from .resources import require_current


def add_scientific_tools(registry, services):
    if services.day is None or getattr(services.hub, 'resources', None) is None:
        return

    async def compare_retained_methods(ctx,args):
        try:
            require_current(ctx,services.day)
            if ctx.asked is None or ctx.asked.session != services.day.sid:
                raise ValueError('Ask again in the original Project conversation')
            if (type(args) is not dict or set(args) != {'operation','action','selection'}
                    or type(args['selection']) is not dict or args['selection'].get('session') != ctx.asked.session):
                raise ValueError('Choose the exact method selection from this Project')
            check=lambda:require_current(ctx,services.day)
            if args['action']=='describe':
                result=await method_workflow.preview(services.hub,services.day,args['operation'],args['selection'],
                                                    caller='agent:'+ctx.channel,check_caller=check)
            else:
                result=await method_workflow.run(services.hub,services.day,args['operation'],args['action'],args['selection'],
                                                caller='agent:'+ctx.channel,check_caller=check)
                saved=result['project'];result={**result,'project':_wire(saved)}
                running=CURRENT_RUN.get()
                if running is not None:
                    running.resource={'id':saved.id,'day':saved.session,'title':saved.document['title'],
                                      'version':saved.revision,'operation':'method evidence retained'}
            attach(values=result)
            return json.dumps(result)
        except (ValueError,OSError,MemoryRefused) as exc:
            raise ToolFailed(str(exc),next='check saved revisions; reuse the request for unchanged choices or start a new one') from exc

    registry.add(Tool('compare_retained_methods',
        'Describe, retain or choose a method comparison using exact saved arrays, never new inference. '
        'For describe/retain, selection is {session,source,arms,request}; source and each completed mask are '
        '{kind:array,session,id,revision,component,digest}. Arms A/B/optional C declare id a/b/c, factor '
        '(boundary-definition/algorithm/settings/version), says, status completed/failed/cancelled, and mask '
        'or outcome {says, optional history and event}. Preserve request identity on retries. '
        'For choose, selection is {session,project,revision,arm,rationale}. Shared coordinate identity is required. '
        'Missing producing histories stay unverified, failed A stays unavailable, and agreement is not accuracy. '
        'Retain and choose change saved work and require the user’s request.',
        schema({'operation':{'type':'string','enum':[method_workflow.OPERATION]},
                'action':{'type':'string','enum':['describe','retain','choose']},'selection':{'type':'object'}},
               required=['operation','action','selection']),compare_retained_methods,
        kind='effect',inline_ms=1500,family='measuring',retained_input=True))

    async def retain_population_labels(ctx, args):
        try:
            require_current(ctx, services.day)
            if ctx.asked is None or ctx.asked.session != services.day.sid:
                raise ValueError('The original request has no current Project; ask again in its conversation')
            if type(args) is not dict or set(args) != {'action', 'operation', 'session', 'id', 'revision', 'request'} or args.get('action') not in {'describe', 'run'}:
                raise ValueError('Choose the declared operation and exact saved population revision')
            if args['session'] != ctx.asked.session:
                raise ValueError('The selected population belongs to another Project session')
            if args['action'] == 'describe':
                result = population_native.describe(services.hub, services.day, args['operation'],
                    {k: args[k] for k in ('session', 'id', 'revision')})
                attach(values=result)
                return json.dumps(result)
            result = await population_native.run(services.hub, services.day, args['operation'],
                {k: args[k] for k in ('session', 'id', 'revision', 'request')}, caller='agent:'+ctx.channel,
                check_caller=lambda: require_current(ctx, services.day))
            saved = result.pop('project')
            result['project'] = _wire(saved)
            attach(values=result)
            running = CURRENT_RUN.get()
            if running is not None:
                running.resource = {'id': saved.id, 'day': saved.session, 'title': saved.document['title'],
                                    'version': saved.revision, 'operation': 'population labels retained'}
            return (f"Retained population revision {args['revision']} in Project {saved.id}, revision {saved.revision}, "
                    'native inputs image and labels. The producer was not rerun. This does not claim it is on screen.')
        except (ValueError, OSError, MemoryRefused) as exc:
            raise ToolFailed(str(exc), next='inspect that saved population revision, then retry the acceptance with the same request') from exc

    registry.add(Tool('retain_population_labels',
        'Retain an explicitly selected saved population revision as unchanged image and native instance labels for '
        'Measure saved objects. Requires a single-plane grayscale imported original, exclusive positive uint16 instance '
        'IDs and pixel units. Preserve the request identity on retries; do not substitute a newer revision. '
        'Changes saved work and must follow the user’s request. No model preparation or producer rerun.',
        schema({'action': {'type': 'string', 'enum': ['describe', 'run']},
                'operation': {'type': 'string', 'enum': [population_native.OPERATION]},
                'session': {'type': 'string'}, 'id': {'type': 'string'},
                'revision': {'type': 'integer', 'minimum': 0}, 'request': {'type': 'string', 'minLength': 16, 'maxLength': 128}},
               required=['action', 'operation', 'session', 'id', 'revision', 'request']),
        retain_population_labels, kind='effect', inline_ms=1500, family='measuring',retained_input=True))

    async def measure_saved_objects(ctx, args):
        try:
            require_current(ctx, services.day)
            if ctx.asked is None or ctx.asked.session != services.day.sid:
                raise ValueError('The original request has no current Project; ask again in its conversation')
            fields = {'action', 'operation', 'project', 'revision', 'input'}
            if (type(args) is not dict or set(args) not in (fields, fields | {'session'})
                    or args['action'] not in {'describe', 'run'}):
                raise ValueError('Choose describe or run with the declared operation and exact saved Project revision and labels input')
            # The conversation already captured the owner. Models must not reconstruct
            # its journal ID from a workspace UUID or a document's legacy `project` key.
            # Older explicit callers remain supported, with the same foreign-owner refusal.
            if 'session' in args and args['session'] != ctx.asked.session:
                raise ValueError('The selected labels belong to another Project session')
            selected = {k:args[k] for k in ('project','revision','input')}
            selected['session'] = ctx.asked.session
            check = lambda: require_current(ctx, services.day)
            if args['action'] == 'describe':
                result = await _owned(measurement.describe, services.hub.resources.projects,
                                     args['operation'], selected, ctx.asked.session)
                check()
                attach(values=result)
                return json.dumps(result)
            result = await measurement.run(services.hub, services.day, args['operation'], selected,
                                           caller='agent:'+ctx.channel, check_caller=check)
            saved = result.pop('project')
            result['output'] = {'id':saved.id,'session':saved.session,'revision':saved.revision,'digest':saved.digest,
                                'table':'objects'}
            attach(values=result)
            running = CURRENT_RUN.get()
            if running is not None:
                running.resource = {'id':saved.id,'day':saved.session,'title':saved.document['title'],
                                    'version':saved.revision,'operation':'object measurements saved'}
            return (f"{'Reused' if result['reused'] else 'Saved'} object measurements in Project {saved.id}, "
                    f"revision {saved.revision}, table objects. Consumed accepted revision {selected['revision']} "
                    f"input {selected['input']}; segmentation was not rerun. This does not claim the table is on screen.")
        except (ValueError, OSError, MemoryRefused) as exc:
            raise ToolFailed(str(exc), next='review the accepted labels and measurement description, then retry explicitly') from exc

    registry.add(Tool('measure_saved_objects',
        'Describe or run the built-in Measure saved objects operation on explicitly selected accepted Project labels. '
        'Use describe to inspect supported axes, units and readiness before run. Supply the exact saved revision and '
        'input from the selected resource; never guess an ID or substitute the latest revision. Save label corrections '
        'first. Counts label IDs, preserves originals, and publishes a typed table in the same Project; no model setup '
        'or segmentation rerun. The owning Project is captured from this conversation; do not supply a workspace ID. '
        'run changes saved work and must follow the user’s request.',
        schema({'action':{'type':'string','enum':['describe','run']},
                'operation':{'type':'string','enum':[measurement.OPERATION]},
                'project':{'type':'string','description':'The saved scientific document ID (p followed by hex), not the owning Project/day or workspace UUID'},
                'revision':{'type':'integer','minimum':1,'description':'The exact accepted version of that document'},
                'input':{'type':'string','description':'The labels key in that document’s scientificView.inputs'}},
               required=['action','operation','project','revision','input']),
        measure_saved_objects,kind='effect',inline_ms=1500,family='measuring',retained_input=True))
