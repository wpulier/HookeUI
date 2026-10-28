"""Retained, explicitly reviewed scientific recipes over public tool contributions.

Preparing a step only copies accepted inputs and configures its declared tool.
The ordinary scientific session executes it; only an explicitly accepted output
advances the run. Reload never executes, repeats or invents manual corrections.
A run names its own exact input, which may differ from the recipe's original.
A researcher may record a step attempt as failed: its working copy is kept, the
run says it is partial, and preparing again makes a separate working copy.
"""
import hashlib
import os
from env_worker.scientific_tool_document import snapshot
from gpu_worker.bundle_contract import capability_key
from . import scientific_tools as tools
from .scientific_comparisons import exact, identity, reference
from .scientific_projects import InvalidProject, _json

FORMAT = 'scientific-recipe-v1'
RUN_FORMAT = 'scientific-recipe-run-v2'
RUN_FORMATS = {'scientific-recipe-run-v1':{'format','recipe','completed','working'},
               RUN_FORMAT:{'format','recipe','input','completed','working','attempts'}}
MAX_STEPS = 12
MAX_ATTEMPTS = 32
MAX_NOTE = 500


def token(value):
    return hashlib.sha256(_json(value)).hexdigest()


def request_id(value):
    if type(value) is not str or len(value) != 32 or any(c not in '0123456789abcdef' for c in value):
        raise InvalidProject('Start this recipe action with its stable request identity')
    return value


def step(store, project, session, revision):
    saved = tools.scene(store, project, session, revision)
    view = saved.document['state']['scientificView']
    if 'tool' not in view:
        raise InvalidProject('Save the tool and all layers before retaining its recipe settings')
    tool = snapshot(view['tool'], view.get('layers', []))
    if 'recipe' not in tool:
        raise InvalidProject('This integration has not declared reproducible recipe settings; use its saved controls')
    runtime = view['runtime']
    return saved, {'template':identity(saved), 'name':saved.document['title'],
                   'capability':capability_key(runtime['bundle'],runtime['operation']),
                   'generation':runtime['generation'], 'runtime':runtime,
                   'parameters':tool['recipe'], 'review':'required'}


def recipe(store, project, session, revision):
    saved = store.read(project,session,revision=revision)
    state = saved.document['state'].get('recipe')
    if (saved.document['kind'] != 'scientific-recipe' or type(state) is not dict
            or set(state) != {'format','input','steps'} or state['format'] != FORMAT
            or type(state['steps']) is not list or not 1 <= len(state['steps']) <= MAX_STEPS):
        raise InvalidProject('This recipe version is unsupported; preserve its original document')
    exact(store,state['input'],session)
    for row in state['steps']:
        if type(row) is not dict or set(row) != {'template','name','capability','generation','runtime','parameters','review'} or row['review'] != 'required':
            raise InvalidProject('This recipe step version is unsupported')
        template = exact(store,row['template'],session)
        _, expected = step(store,template.id,session,template.revision)
        if _json(row) != _json(expected):
            raise InvalidProject('Recipe settings differ from their accepted template')
    return saved,state


def create(store, project, session, revision, request, *, check=lambda:None):
    request_id(request)
    saved,row = step(store,project,session,revision)
    produced = saved.document['state'].get('hookeOutput',{})
    if type(produced) is not dict or 'base' not in produced:
        raise InvalidProject('This tool has no captured producing input for a recipe')
    source = exact(store,produced['base'],session)
    tools.scene(store,source.id,session,source.revision)
    doc = {'kind':'scientific-recipe','title':('Recipe · '+saved.document['title'])[:240],
           'resources':[reference(source),reference(saved)],'runtimes':saved.document['runtimes'],
           'state':{'recipe':{'format':FORMAT,'input':identity(source),'steps':[row]}}}
    check()
    return store.create(session,doc,identity='p'+token({'recipe':identity(saved),'request':request})[:32])


def append(store,project,session,revision,template,template_revision,*,check=lambda:None):
    saved,state = recipe(store,project,session,revision)
    if len(state['steps']) >= MAX_STEPS:
        raise InvalidProject('This recipe already has twelve steps; retain a separate recipe')
    source,row = step(store,template,session,template_revision)
    state['steps'].append(row)
    doc=saved.document;doc['state']['recipe']=state;doc['resources'].append(reference(source))
    doc['runtimes']=list({tuple(sorted(r.items())):r for r in [*doc['runtimes'],*source.document['runtimes']]}.values())
    return store.save(project,session,doc,expected_revision=revision,transaction=token({'recipe':identity(saved),'append':row}),check=check)


def begin(store,project,session,revision,request,input=None,*,check=lambda:None):
    request_id(request)
    saved,state=recipe(store,project,session,revision)
    source=exact(store,state['input'] if input is None else input,session)
    tools.scene(store,source.id,session,source.revision)
    # Several runs of one recipe are told apart by their input in Data and windows.
    doc={'kind':'scientific-recipe-run','title':('Run on '+source.document['title']+' · '+saved.document['title'])[:240],
         'resources':[reference(saved),reference(source)],
         'runtimes':saved.document['runtimes'],
         'state':{'recipeRun':{'format':RUN_FORMAT,'recipe':identity(saved),'input':identity(source),
                               'completed':[],'working':None,'attempts':[]}}}
    check()
    return store.create(session,doc,identity='p'+token({'recipe':identity(saved),'run':request,'input':identity(source)})[:32])


def run(store,project,session,revision):
    """The saved run, upgraded in memory to the current format; earlier revisions stay as saved."""
    saved=store.read(project,session,revision=revision)
    state=saved.document['state'].get('recipeRun')
    if (saved.document['kind']!='scientific-recipe-run' or type(state) is not dict
            or set(state)!=RUN_FORMATS.get(state.get('format')) or type(state['completed']) is not list):
        raise InvalidProject('This recipe run version is unsupported; retain its saved work')
    definition=exact(store,state['recipe'],session)
    _,definition_state=recipe(store,definition.id,session,definition.revision)
    if state['format']!=RUN_FORMAT:
        state={**state,'format':RUN_FORMAT,'input':definition_state['input'],'attempts':[]}
    exact(store,state['input'],session)
    if len(state['completed'])>len(definition_state['steps']):
        raise InvalidProject('This run has more accepted results than declared steps')
    for item in state['completed']:
        if type(item) is not dict or set(item)!={'working','result'}:
            raise InvalidProject('A recipe outcome has an unsupported association')
        exact(store,item['working'],session)
        result = exact(store,item['result'],session)
        produced = result.document['state'].get('hookeOutput', {})
        if type(produced) is not dict or _json(produced.get('base')) != _json(item['working']):
            raise InvalidProject('An accepted recipe result has lost its producing association')
    if type(state['attempts']) is not list or len(state['attempts'])>MAX_ATTEMPTS:
        raise InvalidProject('This run has an unsupported attempt history')
    for item in state['attempts']:
        if (type(item) is not dict or set(item)!={'step','working','outcome','note'} or item['outcome']!='failed'
                or type(item['step']) is not int or not 0<=item['step']<=len(state['completed'])
                or type(item['note']) is not str or len(item['note'])>MAX_NOTE):
            raise InvalidProject('A failed recipe attempt has an unsupported record')
        exact(store,item['working'],session)
    if state['working'] is not None:
        exact(store,state['working'],session)
    return saved,state,definition_state


def prepare(store,project,session,revision,*,environ=os.environ,check=lambda:None):
    saved,state,definition=run(store,project,session,revision)
    if state['working'] is not None:return saved
    index=len(state['completed'])
    if index==len(definition['steps']):raise InvalidProject('This run is complete; start a new run explicitly')
    source=exact(store,state['completed'][-1]['result'] if index else state['input'],session)
    item=definition['steps'][index]
    # A step tried again after a recorded failure gets its own working copy.
    attempt=sum(row['step']==index for row in state['attempts'])
    transaction=token({'run':saved.id,'step':index,'source':identity(source),'definition':state['recipe'],
                       **({'attempt':attempt} if attempt else {})})
    working=tools.fork(store,source,item['capability'],item['generation'],transaction,
                       recipe=item['parameters'],environ=environ,check=check)
    state['working']=identity(working)
    doc=saved.document;doc['state']['recipeRun']=state;doc['resources'].append(reference(working))
    return store.save(project,session,doc,expected_revision=revision,transaction=transaction,check=check)


def fail(store,project,session,revision,note,*,check=lambda:None):
    """Record the researcher's judgment that this step attempt failed. Nothing is removed."""
    saved,state,_=run(store,project,session,revision)
    if state['working'] is None:raise InvalidProject('Prepare this step before recording its attempt as failed')
    if type(note) is not str or len(note.strip())>MAX_NOTE:
        raise InvalidProject('Describe the failure in at most 500 characters')
    if len(state['attempts'])>=MAX_ATTEMPTS:
        raise InvalidProject('This run has 32 failed attempts; start a separate run to continue')
    row={'step':len(state['completed']),'working':state['working'],'outcome':'failed','note':note.strip()}
    state['attempts'].append(row);state['working']=None
    doc=saved.document;doc['state']['recipeRun']=state
    return store.save(project,session,doc,expected_revision=revision,transaction=token({'run':saved.id,'failed':row}),check=check)


def accept(store,project,session,revision,result_id,result_revision,*,check=lambda:None):
    saved,state,definition=run(store,project,session,revision)
    if state['working'] is None:raise InvalidProject('Prepare and execute this step before accepting its result')
    result=tools.scene(store,result_id,session,result_revision)
    produced=result.document['state'].get('hookeOutput',{})
    if type(produced) is not dict or produced.get('base') != state['working']:
        raise InvalidProject('This result was not produced from the current recipe step; keep the earlier work')
    # Later accepted corrections/measurements in this same output retain its
    # producing base. The researcher chooses the exact corrected revision here.
    row={'working':state['working'],'result':identity(result)}
    state['completed'].append(row);state['working']=None
    doc=saved.document;doc['state']['recipeRun']=state;doc['resources'].append(reference(result))
    return store.save(project,session,doc,expected_revision=revision,transaction=token(row),check=check)


def candidates(store,project,session,revision):
    _,state,_=run(store,project,session,revision)
    if state['working'] is None:return []
    rows=[]
    for saved in store.list(session):
        output=saved.document['state'].get('hookeOutput',{})
        if type(output) is dict and output.get('base')==state['working']:
            rows.append({'result':identity(saved),'title':saved.document['title']})
    return rows


def tool_name(runtime,environ=os.environ):
    """The integration's own name for an operation, for labels only; never an identity."""
    from gpu_worker import bundle_contract, manifests as catalog
    owner={**catalog.manifests(),**catalog.mine(environ)}.get(runtime.get('bundle'))
    try: members=bundle_contract.parse_members(list(owner.members),owner) if owner is not None else []
    except (ValueError,TypeError): members=[]
    return next((m.name for m in members if m.id==runtime.get('operation')),runtime.get('operation'))


def inputs(store,session,environ=os.environ):
    """Saved native scenes a new run may start from, newest first. Unexecuted recipe copies are left out."""
    rows=[]
    for saved in store.list(session):
        view=saved.document['state'].get('scientificView')
        if type(view) is not dict or 'recipeTool' in view:continue
        try: tools.scene(store,saved.id,session,saved.revision)
        except (ValueError,OSError):continue
        rows.append({'input':identity(saved),'title':saved.document['title'],
                     'layers':len(view.get('layers',[])),'tool':tool_name(view['runtime'],environ) if isinstance(view.get('runtime'),dict) else None})
        if len(rows)==64:break
    return rows


def templates(store,session,environ=os.environ):
    rows=[]
    for saved in store.list(session):
        try: _,row=step(store,saved.id,session,saved.revision)
        except (ValueError,OSError):continue
        rows.append({**row,'tool':tool_name(row['runtime'],environ)})
    return rows
