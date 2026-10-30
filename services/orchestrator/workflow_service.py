"""One command boundary for Studio and the agent; dispatch outlives its requesting view."""
import asyncio
import contextvars
from contextlib import nullcontext

from .scientific_projects import InvalidProject
from .scientific_comparisons import identity
from .workflows import Workflows, wire, allowed_actor, run_identity
from .workflow_execution import ScientificSteps, starter


class WorkflowService:
    def __init__(self,hub,day):
        self.hub,self.day=hub,day
        self.workflows=Workflows(hub.resources.projects)
        self.steps=ScientificSteps(self.workflows,hub)
        self.steps.day=day
        self.tasks={}
        self.wakeups=set()
        self.closing=False

    def owner(self,session):
        if type(session) is not str:raise InvalidProject('Choose the destination Project')
        return self.day.elsewhere(session)

    def observed_status(self,id_,session):
        """Read retained status together with this process's actual execution ownership.

        A missing driver cannot establish termination of a dispatched worker. Reads
        disclose that uncertainty without changing receipts or restarting anything.
        """
        return self._observe(self.workflows.status(id_,session))

    def observed_statuses(self, ids, session):
        return {id_:self._observe(row) for id_,row in self.workflows.statuses(ids,session).items()}

    def _observe(self, result):
        task=self.tasks.get((result['session'],result['id']))
        owned=task is not None and not task.done()
        latest={(a['item'],a['node']):a for a in result['attempts']}
        unresolved=any(a['status'] in ('running','awaiting-research') for a in latest.values())
        unstarted=result['status']=='ready'
        result['executionObservation']={'state':'owned' if owned else 'interrupted' if unresolved else 'unstarted' if unstarted else 'idle',
            'reason':'This Hooke process owns the workflow driver.' if owned else
                     'No workflow driver owns these dispatched attempts. Reconcile the original execution before continuing; nothing was replayed.' if unresolved else
                     'This admitted run has no active driver. Choose Resume to execute its remaining steps; nothing was replayed.' if unstarted else
                     'No workflow driver is active; retained results and review remain available.'}
        return result

    def launch(self,id_,session):
        key=(session,id_)
        if self.closing:return
        if key in self.tasks and not self.tasks[key].done():
            self.wakeups.add(key)
            return
        self.wakeups.discard(key)
        task=asyncio.create_task(self.drive(id_,session),context=contextvars.Context())
        self.tasks[key]=task
        task.add_done_callback(lambda finished:self.finished(key,finished))

    def finished(self,key,task):
        if self.tasks.get(key) is not task:return
        self.tasks.pop(key,None)
        if not task.cancelled() and (error:=task.exception()) is not None:
            import logging
            logging.getLogger(__name__).error('Workflow driver stopped; retained attempts need reconciliation: %s',error)
        if key in self.wakeups:
            self.wakeups.discard(key)
            if not self.closing:self.launch(key[1],key[0])

    async def drive(self,id_,session):
        from .capture import captures
        try:
            while not self.closing:
                claim=await asyncio.to_thread(self.workflows.claim,id_,session)
                if claim is None:
                    status=await asyncio.to_thread(self.workflows.status,id_,session)
                    research=[a for a in status['attempts'] if a['status']=='awaiting-research']
                    if not research:return
                    from .workflow_research import collect
                    for a in research:
                        claim={'run':id_,'session':session,'attempt':{'id':a['id']}}
                        try:
                            outcome=await asyncio.to_thread(collect,self.workflows,claim,a['research'],status['control']=='stop',self.hub)
                            if outcome:await asyncio.to_thread(self.workflows.finish,claim,outcome)
                        except (ValueError,OSError):
                            # Retained Study evidence remains authoritative; never manufacture settlement.
                            return
                    await asyncio.sleep(1)
                    continue
                def execute():
                    room=captures(self.hub).memory.reserve('analysis',128<<20,'workflow scientific step')
                    with room:return self.steps.execute(claim)
                try:
                    if self.steps.on_loop(claim):
                        # The app's own models and researchers (experiment_steps): awaited here, their work in their
                        # workers at research priority, each holding its own memory; the live view never waits on it.
                        outcome=await self.steps.execute_on_loop(claim)
                    else:outcome=await asyncio.to_thread(execute)
                except asyncio.CancelledError:
                    # Work in a thread may still own its operation. Recovery must reconcile it.
                    raise
                except Exception as exc:
                    outcome={'status':'failed','output':None,'note':str(exc)[:500] or 'Scientific step failed'}
                await asyncio.to_thread(self.workflows.finish,claim,outcome)
        finally:
            # Completion callback owns removal and wakes requests received during this driver's final read.
            pass

    async def close(self):
        self.closing=True
        self.steps.closing=True
        tasks=list(self.tasks.values())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    async def command(self,body,*,actor):
        if not allowed_actor(actor) or type(body) is not dict:raise InvalidProject('Unknown workflow caller')
        b=dict(body);action=b.pop('action',None);w=self.workflows
        def fields(*names):
            if set(b)!=set(names):raise InvalidProject('Use only the declared workflow command fields')
        if action=='capability':
            study=b.pop('study',None)
            if study is not None and actor!='studio':raise InvalidProject('Connected agents use Study workflow admission')
            fields('session','capability','generation','inputs','parameters','request')
            self.owner(b['session'])
            from .matrix_capability_runs import descriptor, parameters
            from .workflows import token, request
            request(b['request'])
            declared=descriptor(b['capability'])
            if not declared['ready']:raise InvalidProject(declared['reason'])
            if declared['generation']!=b['generation']:raise InvalidProject('The selected capability generation changed')
            normalized=parameters(b['capability'],b['parameters'])
            ports=[p['id'] for p in declared['inputs']]
            if type(b['inputs']) is not dict or set(b['inputs'])!=set(ports):raise InvalidProject('Choose exactly the declared capability inputs')
            bindings={port:ref for port,ref in b['inputs'].items()}
            graph={'format':1,'input':{'kind':'source-file'},'nodes':[{'id':'operation','name':declared.get('name',b['capability']),
                'kind':'tool','needs':['input:'+port for port in ports],
                'settings':{'capability':b['capability'],'generation':b['generation'],'parameters':normalized,'mode':'automatic',
                            'bindings':{port:'input:'+port for port in ports}}}]}
            # A single manual invocation is still one ordinary frozen workflow run.
            await asyncio.to_thread(w.bindings,graph,bindings,b['session'])
            definition=await asyncio.to_thread(w.create,declared.get('name',b['capability']),graph,token({'manual':b['request']})[:32],actor=actor,action='capability')
            if study is not None:
                from .research.studies import for_workspace
                operation, _ = await for_workspace(self.hub,self.day).start_workflow(study,b['session'],request=b['request'],
                    workflow=definition.id,revision=definition.revision,inputs=[b['inputs'][ports[0]]],bindings=bindings,actor=actor,label='You')
                return {'run':operation['workflow'],'operation':operation}
            return await self.command({'action':'start','workflow':definition.id,'revision':definition.revision,'session':b['session'],
                'inputs':[b['inputs'][ports[0]]],'bindings':bindings,'request':b['request']},actor=actor)
        if action=='create':
            fields('name','graph','request');saved=await asyncio.to_thread(w.create,b['name'],b['graph'],b['request'],actor=actor)
            return {'workflow':wire(saved)}
        if action=='edit':
            fields('workflow','revision','name','graph','removed')
            saved=await asyncio.to_thread(w.edit,b['workflow'],b['revision'],b['name'],b['graph'],b['removed'],actor=actor);return {'workflow':wire(saved)}
        if action=='duplicate':
            fields('workflow','revision','name','request')
            saved=await asyncio.to_thread(w.duplicate,b['workflow'],b['revision'],b['name'],b['request'],actor=actor);return {'workflow':wire(saved)}
        if action in ('upgrade','capture'):
            fields('source','session','revision','name','request');self.owner(b['session'])
            if action=='capture':
                from .scientific_recipes import create
                original=await asyncio.to_thread(create,w.store,b['source'],b['session'],b['revision'],b['request'])
                source,revision=original.id,original.revision
            else:source,revision=b['source'],b['revision']
            saved=await asyncio.to_thread(w.upgrade,source,b['session'],revision,b['name'],b['request'],actor=actor,action=action);return {'workflow':wire(saved)}
        if action in ('start','validate'):
            fields('workflow','revision','session','inputs',*(['request'] if action=='start' else []),*(['bindings'] if 'bindings' in b else []))
            owner=self.owner(b['session']);definition=await asyncio.to_thread(w.definition,b['workflow'],b['revision'])
            graph=definition.document['state']['workflow']['graph']
            if owner.local_only and any(n['kind']=='researcher' for n in graph['nodes']):raise InvalidProject('This Project is local only; research provider execution is unavailable')
            bindings=await asyncio.to_thread(w.bindings,graph,b.get('bindings',{}),b['session'])
            for ref in b['inputs']:
                from .scientific_comparisons import exact
                await asyncio.to_thread(exact,w.store,ref,b['session'])
            readiness=await asyncio.to_thread(self.steps.validate,graph,b['inputs'],b['session'],bindings)
            if action=='validate':return {'readiness':readiness}
            existing=await asyncio.to_thread(lambda: w.store.for_session(b['session'])._folder(run_identity(b['session'],b['request'])).exists())
            if not readiness['available']:raise InvalidProject('; '.join(r['reason'] for r in readiness['steps'] if r['reason']))
            saved=await asyncio.to_thread(w.start,b['workflow'],b['revision'],b['session'],b['inputs'],b['request'],actor=actor,bindings=bindings)
            if not existing:self.launch(saved.id,saved.session)
            return {'run':await asyncio.to_thread(self.observed_status,saved.id,saved.session)}
        if action in ('pause','resume','stop','retry','recover','resolve','review','revise','check'):
            names=['run','session']
            if action=='resolve':names+=['attempt','note']
            if action=='review':names+=['attempt','result','accept','request']
            if action=='revise':names+=['attempt','result','request']
            fields(*names);self.owner(b['session']);id_,session=b['run'],b['session']
            observation=await asyncio.to_thread(self.observed_status,id_,session)
            if action in ('resume','retry') and observation['executionObservation']['state']=='interrupted':
                raise InvalidProject('Reconcile the interrupted execution before continuing')
            if action=='revise':
                saved=await asyncio.to_thread(w.revise,id_,session,b['attempt'],b['result'],b['request'],actor=actor)
                self.launch(saved.id,session)
                return {'run':await asyncio.to_thread(self.observed_status,saved.id,session)}
            if action=='review':await asyncio.to_thread(w.review,id_,session,b['attempt'],b['result'],b['accept'],b['request'],actor=actor)
            elif action=='resolve':await asyncio.to_thread(w.resolve,id_,session,b['attempt'],b['note'],actor=actor)
            elif action=='recover':
                task=self.tasks.get((session,id_))
                if task is not None and not task.done():raise InvalidProject('This process still owns the execution; use Stop and wait for settlement')
                await asyncio.to_thread(w.recover,id_,session,actor=actor)
            elif action!='check':await asyncio.to_thread(w.control,id_,session,action,actor=actor)
            if action in ('resume','retry','review','stop') and observation['executionObservation']['state']!='interrupted':self.launch(id_,session)
            return {'run':await asyncio.to_thread(self.observed_status,id_,session)}
        raise InvalidProject('Choose a supported workflow command')


def service(hub,day):
    current=getattr(hub,'workflow_service',None)
    if current is None:
        current=hub.workflow_service=WorkflowService(hub,day)
    return current
