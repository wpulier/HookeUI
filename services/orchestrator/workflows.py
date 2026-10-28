"""Personal reusable instructions and Project-owned, immutable scientific run evidence.

The controller owns dispatch claims; adapters never accept client-written execution receipts.
Each attempt is its own bounded revisioned document. Reads never dispatch or repair work.
"""
from __future__ import annotations

import copy
import hashlib
import re
from contextlib import contextmanager

from .file_locks import fcntl
from .scientific_projects import ProjectStore, InvalidProject, Conflict, _json
from .scientific_comparisons import exact, identity, reference

OWNER = 'personal'
KINDS = {'tool', 'review', 'measure', 'export', 'compare', 'researcher'}
ACTORS = {'user', 'studio', 'agent:chat', 'agent:voice', 'agent:system', 'agent:researcher'}
MAX_NODES, MAX_INPUTS, MAX_ATTEMPTS = 12, 64, 32
PENDING = {'running', 'awaiting-review', 'awaiting-controls', 'awaiting-research', 'uncertain'}


def allowed_actor(value):
    return type(value) is str and (value in ACTORS or re.fullmatch(r'(?:agent:ac_[0-9a-f]{16,32}|researcher:r[0-9]+)', value) is not None)


valid_actor = allowed_actor


def token(value):
    return hashlib.sha256(_json(value)).hexdigest()


def request(value):
    if type(value) is not str or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise InvalidProject('Use the original 32-character workflow request identity')
    return value


def run_identity(session, req):
    request(req)
    from .scientific_projects import _identity
    _identity(session, 'session')
    return 'p'+token({'workflowRun':req,'session':session})[:32]


def title(value):
    if type(value) is not str or not 0 < len(value.strip()) <= 240:
        raise InvalidProject('Name the workflow in 1–240 characters')
    return value.strip()


def graph(value):
    if (type(value) is not dict or set(value) != {'format', 'input', 'nodes'} or value['format'] != 1
            or type(value['input']) is not dict or type(value['nodes']) is not list
            or not 1 <= len(value['nodes']) <= MAX_NODES or len(_json(value)) > 64 << 10):
        raise InvalidProject('Use a version 1 scientific workflow with 1–12 bounded steps')
    requirements=value['input']
    if (requirements.get('kind') not in ('scientific-scene','text-file','source-file')
            or set(requirements)-{'kind','semantic','axes','dtype','calibration'}
            or 'semantic' in requirements and requirements['semantic'] not in ('intensity','instance-labels','class-labels')
            or 'dtype' in requirements and requirements['dtype'] not in ('|u1','<u2','<u4','<f4','<f8')
            or 'calibration' in requirements and requirements['calibration'] not in ('optional','required')
            or 'axes' in requirements and (type(requirements['axes']) is not list or not requirements['axes'] or
                 any(a not in ('t','c','z','y','x') for a in requirements['axes']) or len(set(requirements['axes']))!=len(requirements['axes']))
            or requirements['kind'] in ('text-file','source-file') and set(requirements)!={'kind'}):
        raise InvalidProject('Declare a scientific array’s role, ordered axes, type and calibration, or a text-file input')
    ids = {'input'}
    for node in value['nodes']:
        if (type(node) is not dict or set(node) != {'id','name','kind','needs','settings'}
                or type(node['id']) is not str or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',node['id'])
                or node['id'] in ids or node['kind'] not in KINDS or type(node['settings']) is not dict
                or type(node['needs']) is not list or not 1 <= len(node['needs']) <= 3
                or any(type(n) is not str for n in node['needs']) or len(set(node['needs'])) != len(node['needs'])):
            raise InvalidProject('Each workflow step needs a unique identity, supported operation and connected inputs')
        title(node['name']); ids.add(node['id'])
        if node['kind']=='compare' and len(node['needs'])<2:
            raise InvalidProject('Comparison needs at least two fixed branches')
        if node['kind'] not in ('compare','tool') and len(node['needs']) != 1:
            raise InvalidProject('Only comparison steps take multiple inputs')
        if node['kind'] == 'tool':
            settings=node['settings']
            if (not {'capability','generation','parameters','mode'} <= set(settings) or set(settings)-{'capability','generation','parameters','mode','bindings'}
                    or any(type(settings[k]) is not str or not settings[k] for k in ('capability','generation'))
                    or settings['mode'] not in ('automatic','interactive') or type(settings['parameters']) is not dict):
                raise InvalidProject('A tool needs its pinned capability, generation, parameters and execution mode')
            bindings=settings.get('bindings')
            if bindings is not None and (type(bindings) is not dict or not bindings or any(type(k) is not str or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}',k) or v not in node['needs'] for k,v in bindings.items())):
                raise InvalidProject('Map each capability input port to one connected step')
            if len(node['needs'])>1 and bindings is None:raise InvalidProject('Multiple tool inputs require explicit port bindings')
        if node['kind'] == 'researcher':
            from .workflow_research import validate
            validate(node['settings'])
    external={n for node in value['nodes'] for n in node['needs'] if n.startswith('input:')}
    if any(re.fullmatch(r'input:[a-z][a-z0-9_-]{0,39}',n) is None for n in external):raise InvalidProject('Name each external input binding')
    reached={'input',*external}
    remaining=list(value['nodes'])
    while remaining:
        ready=[n for n in remaining if set(n['needs']) <= reached]
        if not ready:raise InvalidProject('Workflow connections must form a fixed graph without cycles or missing steps')
        reached.update(n['id'] for n in ready);remaining=[n for n in remaining if n not in ready]
    return copy.deepcopy(value)


def wire(saved):
    return {**identity(saved), 'name':saved.document['title'], **saved.document['state']['workflow']}


class Workflows:
    def __init__(self, store):
        self.store=store
        self.library=ProjectStore(store.root.parent/'workflow-library', runtime_root=store.runtime_root)

    def definition(self, id_, revision=None):
        saved=self.library.read(id_,OWNER,revision=revision)
        if saved.document['kind'] != 'scientific-workflow':raise InvalidProject('Choose a saved workflow')
        graph(saved.document['state']['workflow']['graph'])
        return saved

    def list(self, query='', removed=False):
        if type(query) is not str or len(query)>240:raise InvalidProject('Use a short workflow search')
        return [wire(s) for s in self.library.list(OWNER) if s.document['kind']=='scientific-workflow'
                and s.document['state']['workflow']['removed']==removed
                and query.casefold() in s.document['title'].casefold()]

    def create(self,name,definition,req,*,actor='studio',action='create'):
        name=title(name);definition=graph(definition);request(req)
        if not allowed_actor(actor) or action not in ('create','duplicate','upgrade','capture','capability'):
            raise InvalidProject('Unknown workflow author or definition action')
        doc={'kind':'scientific-workflow','title':name,'state':{'workflow':{'graph':definition,'removed':False,
             'createdBy':actor,'lastChange':{'by':actor,'action':action,'revision':1}}},
             'resources':[],'runtimes':self._runtimes(definition)}
        id_='p'+token({'workflow':req})[:32]
        if self.library._folder(id_).exists():
            original=self.definition(id_,1)
            # A retry locates the original revision; a later caller never becomes its author.
            # Legacy definitions have no author fields, and are not retroactively attributed.
            prior=original.document;wanted=copy.deepcopy(doc)
            for value in (prior,wanted):
                value['state']['workflow'].pop('createdBy',None)
                value['state']['workflow'].pop('lastChange',None)
            if prior != wanted:raise Conflict('This request already saved a different workflow')
            return original
        return self.library.create(OWNER,doc,identity=id_)

    @staticmethod
    def _runtimes(definition):
        rows=[]
        for n in definition['nodes']:
            s=n['settings']
            if n['kind']=='tool' and '#' in s['capability']:
                bundle,operation=s['capability'].split('#',1)
                row={'bundle':bundle,'operation':operation,'generation':s['generation']}
                if row not in rows:rows.append(row)
        return rows

    def edit(self,id_,revision,name,definition,removed,*,actor='studio'):
        if type(removed) is not bool:raise InvalidProject('Choose remove or restore explicitly')
        if not allowed_actor(actor):raise InvalidProject('Unknown workflow author')
        saved=self.definition(id_,revision);doc=saved.document
        prior=doc['state']['workflow']
        action='remove' if removed and not prior['removed'] else 'restore' if prior['removed'] and not removed else 'edit'
        doc['title']=title(name);doc['state']['workflow']={'graph':graph(definition),'removed':removed,
            **({'createdBy':prior['createdBy']} if 'createdBy' in prior else {}),
            'lastChange':{'by':actor,'action':action,'revision':revision+1}}
        doc['runtimes']=self._runtimes(definition)
        return self.library.save(id_,OWNER,doc,expected_revision=revision)

    def duplicate(self,id_,revision,name,req,*,actor='studio'):
        return self.create(name,self.definition(id_,revision).document['state']['workflow']['graph'],req,actor=actor,action='duplicate')

    def upgrade(self,id_,session,revision,name,req,*,actor='studio',action='upgrade'):
        from .scientific_recipes import recipe
        _,definition=recipe(self.store,id_,session,revision)
        nodes=[]
        for i,step in enumerate(definition['steps']):
            nodes.append({'id':f'tool{i}','name':step['name'],'kind':'tool','needs':[f'tool{i-1}' if i else 'input'],
                'settings':{'capability':step['capability'],'generation':step['generation'],
                            'parameters':step['parameters'],'mode':'interactive'}})
        return self.create(name,{'format':1,'input':{'kind':'scientific-scene'},'nodes':nodes},req,actor=actor,action=action)

    def start(self,id_,revision,session,inputs,req,*,actor,bindings=None):
        request(req)
        if not allowed_actor(actor):raise InvalidProject('Unknown workflow caller')
        if type(inputs) is not list or not 1<=len(inputs)<=MAX_INPUTS:raise InvalidProject('Select 1–64 exact saved inputs')
        definition=self.definition(id_,revision)
        if self.definition(id_).document['state']['workflow']['removed']:raise InvalidProject('This workflow was removed; restore it before starting another run')
        sources=[exact(self.store,ref,session) for ref in inputs]
        bindings=self.bindings(definition.document['state']['workflow']['graph'],bindings or {},session)
        frozen={'id':id_,'revision':revision,'digest':definition.digest,'name':definition.document['title'],
                'graph':definition.document['state']['workflow']['graph']}
        state={'format':1,'definition':frozen,'inputs':[identity(s) for s in sources],'control':'active','actor':actor,**({'bindings':bindings} if bindings else {})}
        runid=run_identity(session,req)
        if self.store._folder(runid).exists():
            original=self.store.read(runid,session,revision=1)
            prior=original.document['state']['workflowRun']
            if {k:prior.get(k,{}) for k in ('definition','inputs','bindings')} != {k:state.get(k,{}) for k in ('definition','inputs','bindings')}:
                raise Conflict('This Start request already belongs to different work')
            return original
        return self.store.create(session,{'kind':'scientific-workflow-run','title':f"Run · {frozen['name']}"[:240],
            'state':{'workflowRun':state},'resources':[reference(s) for s in sources]+[reference(exact(self.store,r,session)) for r in bindings.values()],
            'runtimes':definition.document['runtimes']},identity=runid)

    def bindings(self,definition,values,session):
        needed={n[6:] for node in definition['nodes'] for n in node['needs'] if n.startswith('input:')}
        if type(values) is not dict or set(values)!=needed:raise InvalidProject('Supply exactly the named workflow input bindings: '+', '.join(sorted(needed)))
        return {name:identity(exact(self.store,ref,session)) for name,ref in values.items()}

    @contextmanager
    def locked(self,id_,session):
        self.run(id_,session) # validates identity before constructing a lock path
        with open(self.store.root/f'.workflow-{id_}.lock','a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(lock,fcntl.LOCK_UN)

    def run(self,id_,session):
        saved=self.store.read(id_,session)
        if saved.document['kind']!='scientific-workflow-run':raise InvalidProject('Choose a workflow run')
        return saved

    def attempts(self,id_,session):
        rows=[]
        for saved in self.store.list(session):
            state=saved.document['state'].get('workflowAttempt')
            if saved.document['kind']=='scientific-workflow-attempt' and type(state) is dict and state.get('run')==id_:
                rows.append({**state,'id':saved.id,'revision':saved.revision})
        return sorted(rows,key=lambda a:(a['item'],a['node'],a['number']))

    def status(self,id_,session):
        saved=self.run(id_,session);state=saved.document['state']['workflowRun'];rows=self.attempts(id_,session)
        latest={(a['item'],a['node']):a for a in rows};active=list(latest.values())
        complete=len(active)==len(state['inputs'])*len(state['definition']['graph']['nodes']) and all(a['status']=='completed' for a in active)
        control=state['control']
        if control=='stop':status='stopping' if any(a['status'] in ('running','uncertain','awaiting-research') for a in active) else 'stopped'
        elif complete:status='completed'
        elif control=='paused':status='paused'
        else:status=next((s for s in ('uncertain','failed','rejected','awaiting-review','awaiting-controls','awaiting-research','running') if any(a['status']==s for a in active)),'ready')
        return {**identity(saved),**state,'status':status,'attempts':rows}

    def claim(self,id_,session):
        with self.locked(id_,session):
            status=self.status(id_,session)
            for a in sorted(status['attempts'],key=lambda a:a.get('reviewRunRevision',0)):
                if not a.get('originAttempt') and a.get('reviewRunRevision',0)>status.get('lastAction',{}).get('revision',0):
                    self._review_action(id_,session,a['id'],a)
                    status=self.status(id_,session)
            if status['status'] not in ('ready','running'):return None
            latest={(a['item'],a['node']):a for a in status['attempts']}
            nodes=status['definition']['graph']['nodes']
            for item,source in enumerate(status['inputs']):
                if all(latest.get((item,n['id']),{}).get('status')=='completed' for n in nodes):continue
                for node in nodes:
                    previous=latest.get((item,node['id']))
                    if previous and previous['status']!='retry':continue
                    if not all(n=='input' or n.startswith('input:') or latest.get((item,n),{}).get('status')=='completed' for n in node['needs']):continue
                    inputs=[source if n=='input' else status['bindings'][n[6:]] if n.startswith('input:') else latest[(item,n)]['output'] for n in node['needs']]
                    number=previous['number']+1 if previous else 1
                    if number>MAX_ATTEMPTS:raise InvalidProject('This step has 32 attempts; start a separate run')
                    attemptid='p'+token({'run':id_,'item':item,'node':node['id'],'attempt':number})[:32]
                    state={'run':id_,'item':item,'node':node['id'],'number':number,'status':'running','inputs':inputs,'output':None,'note':'','actor':status.get('lastAction',{}).get('actor',status['actor'])}
                    saved=self.store.create(session,{'kind':'scientific-workflow-attempt','title':node['name'],
                        'state':{'workflowAttempt':state},'resources':[],'runtimes':[]},identity=attemptid)
                    return {'run':id_,'session':session,'item':item,'node':copy.deepcopy(node),'inputs':inputs,'attempt':identity(saved)}
                return None # sequential batch; independent branches within this item can advance
            return None

    def _save_attempt(self,saved,state):
        doc=saved.document;doc['state']['workflowAttempt']=state
        refs=[state.get(k) for k in ('output','working')]
        doc['resources']=[reference(exact(self.store,r,saved.session)) for r in refs if r is not None]
        return self.store.save(saved.id,saved.session,doc,expected_revision=saved.revision)

    def finish(self,claim,outcome):
        with self.locked(claim['run'],claim['session']):
            saved=self.store.read(claim['attempt']['id'],claim['session']);state=saved.document['state']['workflowAttempt']
            if state['status'] not in ('running','uncertain','awaiting-research'):
                # Retain a late receipt separately; it cannot change the accepted attempt.
                if outcome.get('output'):
                    output=exact(self.store,outcome['output'],claim['session'])
                    self.store.create(claim['session'],{'kind':'scientific-workflow-late-result','title':'Late workflow result',
                        'state':{'workflowLate':{'attempt':identity(saved),'outcome':outcome}},'resources':[reference(output)],'runtimes':[]},
                        identity='p'+token({'late':saved.id,'outcome':outcome})[:32])
                return
            if outcome.get('status') not in {'completed','awaiting-review','awaiting-controls','awaiting-research','failed','uncertain','stopped'}:
                raise InvalidProject('Unsupported execution outcome')
            for key in ('output','working'):
                if outcome.get(key):exact(self.store,outcome[key],claim['session'])
            state.update(copy.deepcopy(outcome))
            if self.run(claim['run'],claim['session']).document['state']['workflowRun']['control']=='stop' and state['status'] not in ('uncertain','awaiting-research'):
                state['status']='stopped'
            self._save_attempt(saved,state)

    def review(self,id_,session,attempt,result,accept,req,*,actor='studio'):
        if not allowed_actor(actor):raise InvalidProject('Unknown workflow caller')
        request(req)
        if type(accept) is not bool:raise InvalidProject('Choose accept or reject')
        with self.locked(id_,session):
            saved=self.store.read(attempt,session);state=saved.document['state'].get('workflowAttempt',{})
            binding=token({'result':result,'accept':accept,'request':req})
            if state.get('run')!=id_:raise InvalidProject('Choose this run’s attempt')
            if state.get('review')==binding:
                self._review_action(id_,session,attempt,state)
                return self.status(id_,session)
            if self.run(id_,session).document['state']['workflowRun']['control']=='stop':raise InvalidProject('This run was stopped')
            if state['status'] not in ('awaiting-review','awaiting-controls'):raise Conflict('This attempt is no longer awaiting review')
            selected=exact(self.store,result,session)
            if state['status']=='awaiting-controls':
                if selected.document['state'].get('hookeOutput',{}).get('base')!=state.get('working'):
                    raise InvalidProject('Choose the result produced by these exact controls')
            else:
                original=state['output']
                if selected.id!=original['id'] or selected.revision<original['revision']:
                    raise InvalidProject('Accept an exact corrected revision of the same result')
                # Revisions of the same immutable document form an append-only ancestry.
                original_saved=exact(self.store,original,session)
                marker=original_saved.document['state'].get('workflowOutput')
                if marker and selected.document['state'].get('workflowOutput')!=marker:
                    raise InvalidProject('The corrected result lost its producing workflow association')
            state.update(status='completed' if accept else 'rejected',output=identity(selected),review=binding,reviewActor=actor,
                         reviewRunRevision=self.run(id_,session).revision+1)
            self._save_attempt(saved,state)
            self._review_action(id_,session,attempt,state)
            return self.status(id_,session)

    def _review_action(self,id_,session,attempt,state):
        # Repair the receipt/run publication gap without overwriting a later action.
        if 'reviewRunRevision' not in state:return
        run=self.run(id_,session);doc=run.document;prior=doc['state']['workflowRun'].get('lastAction',{})
        if prior.get('revision',0)>=state['reviewRunRevision']:return
        doc['state']['workflowRun']['lastAction']={'action':'review','actor':state['reviewActor'],
            'attempt':attempt,'revision':state['reviewRunRevision']}
        self.store.save(id_,session,doc,expected_revision=run.revision)

    def control(self,id_,session,action,*,actor='studio'):
        if not allowed_actor(actor):raise InvalidProject('Unknown workflow caller')
        with self.locked(id_,session):
            saved=self.run(id_,session);doc=saved.document;state=doc['state']['workflowRun']
            if state.get('initializing'):raise InvalidProject('Finish initializing the derived run by retrying its original correction request')
            if state['control']=='stop':
                if action=='stop':return self.status(id_,session)
                raise InvalidProject('Stopped runs remain retained; start a new run')
            if action=='retry':
                rows=self.attempts(id_,session)
                latest={(r['item'],r['node']):r for r in rows}
                if any(a['status']=='uncertain' for a in latest.values()):raise InvalidProject('Resolve the uncertain execution before retrying')
                if any(a['number']>=MAX_ATTEMPTS and a['status'] in ('failed','rejected') for a in latest.values()):
                    raise InvalidProject('This step has 32 attempts; start a separate run')
                for a in latest.values():
                    if a['status'] in ('failed','rejected'):
                        old=self.store.read(a['id'],session);s=old.document['state']['workflowAttempt'];s['status']='retry';self._save_attempt(old,s)
                state['control']='active'
            elif action in ('pause','resume','stop'):state['control']={'pause':'paused','resume':'active','stop':'stop'}[action]
            else:raise InvalidProject('Choose pause, resume, stop or retry')
            if action=='stop':
                for a in self.attempts(id_,session):
                    if a['status'] in ('awaiting-review','awaiting-controls','retry'):
                        old=self.store.read(a['id'],session);s=old.document['state']['workflowAttempt'];s['status']='stopped';self._save_attempt(old,s)
            state['lastAction']={'action':action,'actor':actor,'revision':saved.revision+1}
            self.store.save(id_,session,doc,expected_revision=saved.revision)
        return self.status(id_,session)

    def recover(self,id_,session,*,actor='studio'):
        """Explicit process-start reconciliation. Never replay an unknown dispatch."""
        if not allowed_actor(actor):raise InvalidProject('Unknown workflow caller')
        with self.locked(id_,session):
            for a in self.attempts(id_,session):
                if a['status']=='running':
                    saved=self.store.read(a['id'],session);state=saved.document['state']['workflowAttempt']
                    state.update(status='uncertain',recoveredBy=actor,note='Execution was interrupted. Reconcile its retained result or confirm termination before retrying.')
                    self._save_attempt(saved,state)

    def resolve(self,id_,session,attempt,note,*,actor='studio'):
        if not allowed_actor(actor):raise InvalidProject('Unknown workflow caller')
        if type(note) is not str or not 1<=len(note.strip())<=500:raise InvalidProject('Record how termination was confirmed')
        with self.locked(id_,session):
            saved=self.store.read(attempt,session);state=saved.document['state']['workflowAttempt']
            if state['run']!=id_ or state['status']!='uncertain':raise InvalidProject('Choose this run’s uncertain attempt')
            state.update(status='failed',note=note.strip(),resolvedBy=actor)
            self._save_attempt(saved,state)
        return self.status(id_,session)

    def revise(self,id_,session,attempt,result,req,*,actor='studio'):
        """Continue corrected work in a new run, retaining unaffected accepted branches."""
        if not allowed_actor(actor):raise InvalidProject('Unknown workflow caller')
        request(req)
        with self.locked(id_,session):
            original=self.run(id_,session);status=self.status(id_,session)
            selected=next((a for a in status['attempts'] if a['id']==attempt),None)
            if selected is None or selected['status']!='completed' or not selected['output']:
                raise InvalidProject('Choose an accepted attempt before continuing from a correction')
            corrected=exact(self.store,result,session)
            if corrected.id!=selected['output']['id'] or corrected.revision<selected['output']['revision']:
                raise InvalidProject('Choose a corrected revision of the same accepted result')
            before=exact(self.store,selected['output'],session).document['state'].get('workflowOutput')
            if before and corrected.document['state'].get('workflowOutput')!=before:
                raise InvalidProject('The correction lost its producing association')
            definition=status['definition']
            if self.definition(definition['id']).document['state']['workflow']['removed']:
                raise InvalidProject('Restore this removed workflow before creating a derived run')
            binding={'run':id_,'attempt':attempt,'result':result}
            target='p'+token({'derivedWorkflow':req,'session':session})[:32]
            if self.store._folder(target).exists():
                saved=self.run(target,session)
                if saved.document['state']['workflowRun'].get('derivedFrom')!=binding:
                    raise Conflict('This request already belongs to another correction')
                return self._seed_derived(saved)
            descendants={selected['node']}
            while True:
                more={n['id'] for n in definition['graph']['nodes'] if set(n['needs'])&descendants}
                if more<=descendants:break
                descendants|=more
            doc=original.document;doc['state']['workflowRun'].update(control='paused',derivedFrom=binding,actor=actor,lastAction={'action':'revise','actor':actor,'revision':1})
            seeds=[]
            latest={(a['item'],a['node']):a for a in status['attempts']}
            for a in latest.values():
                if a['status']!='completed' or a['item']==selected['item'] and a['node'] in descendants and a['node']!=selected['node']:continue
                state={k:copy.deepcopy(v) for k,v in a.items() if k not in ('id','revision','review')}
                state.update(run=target,number=1,originAttempt=a['id'])
                if a['item']==selected['item'] and a['node']==selected['node']:state['output']=identity(corrected)
                aid='p'+token({'run':target,'item':a['item'],'node':a['node'],'attempt':1})[:32]
                seeds.append({'id':aid,'state':state})
            doc['state']['workflowRun']['initializing']=seeds
            saved=self.store.create(session,doc,identity=target)
            return self._seed_derived(saved)

    def _seed_derived(self,saved):
        with self.locked(saved.id,saved.session):
            saved=self.run(saved.id,saved.session);doc=saved.document;state=doc['state']['workflowRun']
            if 'initializing' not in state:return saved
            for seed in state['initializing']:
                if self.store._folder(seed['id']).exists():continue
                a=seed['state']
                self.store.create(saved.session,{'kind':'scientific-workflow-attempt','title':a['node'],
                    'state':{'workflowAttempt':a},'resources':[reference(exact(self.store,a['output'],saved.session))],
                    'runtimes':[]},identity=seed['id'])
            del state['initializing'];state['control']='active'
            return self.store.save(saved.id,saved.session,doc,expected_revision=saved.revision)
