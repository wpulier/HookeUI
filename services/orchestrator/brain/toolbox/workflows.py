"""Named workflow discovery and commands, shared by typed and spoken requests."""
import asyncio
import json
from ..registry import Tool, ToolFailed, attach, schema
from .resources import require_current
from ...workflow_service import service
from ...workflows import wire


def add_workflow_tools(registry,services):
    if services.day is None or getattr(services.hub,'resources',None) is None:return
    async def query(ctx,args):
        try:
            require_current(ctx,services.day)
            if ctx.asked is None:raise ValueError('Ask from the destination Project conversation')
            runner=service(services.hub,services.day);w=runner.workflows
            if args.get('capabilities'):
                from ...matrix_capability_runs import descriptors
                value={'capabilities':await asyncio.to_thread(descriptors)}
            elif args.get('starter'):
                from ...workflow_execution import starter
                value={'name':'Measure bright regions','graph':starter()}
            elif args.get('run'):
                value={'run':await asyncio.to_thread(runner.observed_status,args['run'],ctx.asked.session)}
            elif args.get('workflow'):
                value={'workflow':wire(await asyncio.to_thread(w.definition,args['workflow'],args.get('revision')))}
            else:value={'workflows':await asyncio.to_thread(w.list,args.get('query',''),False)}
            attach(values=value);return json.dumps(value)
        except (ValueError,OSError,KeyError) as exc:raise ToolFailed(str(exc),next='list saved workflows and choose its exact identity') from exc
    registry.add(Tool('find_workflows','Find named personal workflows, inspect an exact definition, or read a run in this Project. '
        'starter=true returns the currently qualified local example for creating a workflow. Reads never execute. If names are ambiguous, present the choices; use an exact workflow ID and revision for execution.',
        schema({'capabilities':{'type':'boolean'},'starter':{'type':'boolean'},'query':{'type':'string'},'workflow':{'type':'string'},'revision':{'type':'integer'},'run':{'type':'string'}},required=[]),
        query,kind='query',family='researchers'))

    async def command(ctx,args):
        try:
            require_current(ctx,services.day)
            if ctx.asked is None:raise ValueError('Ask from the destination Project conversation')
            if type(args) is not dict or set(args)!={'command'} or type(args['command']) is not dict:raise ValueError('Supply one workflow command')
            body=dict(args['command'])
            if 'session' in body and body['session']!=ctx.asked.session:raise ValueError('This request belongs to another Project')
            if body.get('action') in ('capability','start','validate','capture','upgrade','pause','resume','stop','retry','recover','resolve','review','revise','check'):
                body['session']=ctx.asked.session
            if type(body.get('action')) is str and body['action'].startswith('study-'):
                from ...research.studies import for_workspace
                body.pop('session',None)
                value=await for_workspace(services.hub,services.day).command(body,ctx.asked.session,actor='agent:'+ctx.channel,label='Hooke '+ctx.channel)
            else:
                value=await service(services.hub,services.day).command(body,actor='agent:'+ctx.channel)
            attach(values=value);return json.dumps(value)
        except (ValueError,OSError,KeyError,TypeError) as exc:raise ToolFailed(str(exc),next='inspect the saved workflow and reuse the request identity for an unchanged retry') from exc
    registry.add(Tool('workflow',
        'Manage or invoke a saved workflow only as requested. Discover matrix capabilities with find_workflows(capabilities=true). '
        'command.action capability(capability,generation,inputs,parameters,request) invokes one catalog operation through a frozen workflow; inputs maps declared ports to exact Project refs. '
        'Study actions use this same command: study-list(), study-create(request,title,objective,criteria,budgetUsd,inputs), study-read(study), study-evidence(study,reference), '
        'study-claim(study,request,statement,evidence,dependsOn,limitations), study-run(study,request,workflow,revision,inputs[,bindings]). '
        'Read evidence before publishing a claim; preserve failed experiments and exact revisions. '
        'command.action: create(name,graph,request), '
        'edit(workflow,revision,name,graph,removed), duplicate(workflow,revision,name,request), '
        'capture/upgrade(source,revision,name,request), validate/start(workflow,revision,inputs[,bindings,request]), '
        'pause/resume/stop/retry/recover/check(run), resolve(run,attempt,note), review(run,attempt,result,accept,request), revise(run,attempt,result,request). '
        'Project is captured from this conversation. Inputs/result are exact {id,session,revision,digest} from retained resources. '
        'request is a stable 32-character lowercase hex identity. Never invent IDs or claim a run is complete merely because it started. '
        'graph is {format:1,input:{kind:scientific-scene},nodes:[{id,name,kind,needs,settings}]}, max 12 fixed steps; '
        'needs refers to input or prior node IDs. kinds: tool,review,measure,export,compare,researcher. '
        'Tool settings: capability,generation,parameters,mode automatic/interactive; copy these from an inspected definition '
        'or captured accepted tool. Other settings come from inspected definitions; do not invent execution eligibility. '
        'Review requires the user’s acceptance of the exact result; never auto-approve scientific judgments. '
        'Resolve uncertainty only with observed termination evidence. Generated improvements create a new version.',
        schema({'command':{'type':'object'}},required=['command']),command,kind='effect',inline_ms=1000,family='researchers',retained_input=True))
