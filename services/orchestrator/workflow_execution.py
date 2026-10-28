"""Scientific workflow adapters. Original algorithms and native publication remain shared."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import itertools
import math
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from . import bright_objects, scientific_resource_actions as actions
from .scientific_comparisons import exact, identity
from .scientific_projects import InvalidProject
from .workflows import token

MAX_PIXELS=1_000_000


def bright_generation():
    return hashlib.sha256(Path(bright_objects.__file__).read_bytes()+Path(__file__).read_bytes()+cv2.__version__.encode()).hexdigest()


def starter():
    return {'format':1,'input':{'kind':'scientific-scene','semantic':'intensity','axes':['y','x'],'dtype':'|u1','calibration':'optional'},'nodes':[
        {'id':'regions','name':'Identify bright regions','kind':'tool','needs':['input'],
         'settings':{'capability':'basic-bright-objects','generation':bright_generation(),'mode':'automatic',
                     'parameters':dict(bright_objects.DEFAULTS)}},
        {'id':'review','name':'Inspect and correct','kind':'review','needs':['regions'],'settings':{}},
        {'id':'measure','name':'Measure accepted regions','kind':'measure','needs':['review'],'settings':{}},
        {'id':'export','name':'Export measurements','kind':'export','needs':['measure'],'settings':{}}]}


def keep_file(store,session,req,name,body):
    from .scientific_file_intake import FileIntake
    intake=FileIntake(store,session,req,name=name,size=len(body),max_bytes=8<<20)
    try:
        for start in range(0,len(body),1<<20):intake.write(body[start:start+(1<<20)])
        return intake.publish()
    finally:intake.close()


class ArrayOutput:
    """In-process producer using the same bounded output grant as scientific workers."""
    def __init__(self,data,geometry):
        self.data=data
        self.info={'format':'native-layer-output-v1','shape':list(data.shape),'dtype':data.dtype.str,
                   'name':'Bright regions','type':'labels','visible':True,'opacity':.65,'geometry':geometry}

    def describe_output(self,layer):return copy.deepcopy(self.info)

    def stage_output(self,layer,info,meaning,edit):
        from env_worker import native_edits
        from .scientific_host import LayerStaging
        staging=LayerStaging(edit);grant=native_edits.bind_output(edit.specification(),staging.answer)
        for index in itertools.product(*(range(n) for n in grant.grid)):
            region=tuple(slice(i*c,min((i+1)*c,n)) for i,c,n in zip(index,grant.chunks,grant.shape))
            grant.write(index,self.data[region])
        staging.finish({'ok':True,'staged':edit.identity},b'')


class ScientificSteps:
    def __init__(self,workflows,hub=None):
        self.workflows,self.store,self.hub=workflows,workflows.store,hub

    def check(self,claim):
        if self.workflows.run(claim['run'],claim['session']).document['state']['workflowRun']['control']=='stop':
            raise InvalidProject('Workflow stop requested; retained results remain')

    def array(self,saved,semantic=None):
        rows=actions.inventory(self.store,saved.id,saved.session,saved.revision)['arrays']
        if semantic:rows=[r for r in rows if r['semantic']==semantic]
        if len(rows)!=1:raise InvalidProject('Choose a saved scene with exactly one matching array; select or convert explicitly first')
        return rows[0],actions.selected(self.store,saved.id,saved.session,saved.revision,rows[0]['key'])[2]

    def validate(self,definition,inputs,session,bindings=None):
        """Read-only readiness, including first-step native admission; no setup or dispatch."""
        rows=[]
        required=definition.get('input',{'kind':'scientific-scene'})
        try:
            for ref in inputs:
                saved=exact(self.store,ref,session)
                if required['kind'] in ('text-file','source-file'):
                    if saved.document['kind']!='source-file' and 'matrixOperation' not in saved.document['state']:raise InvalidProject('Choose a retained scientific source file')
                elif len(required)>1:
                    _,source=self.array(saved,required.get('semantic'))
                    if 'axes' in required and list(source.axes)!=required['axes']:raise InvalidProject('Input axes do not meet the workflow declaration')
                    if 'dtype' in required and source.dtype!=required['dtype']:raise InvalidProject('Input data type does not meet the workflow declaration')
                    if required.get('calibration')=='required' and not source.descriptor.get('calibration'):raise InvalidProject('This workflow requires explicit calibration')
        except (ValueError,OSError) as exc:
            rows.append({'id':'input','available':False,'reason':str(exc),'mode':'admission'})
        for node in definition['nodes']:
            problem=None
            try:
                if node['kind']=='tool':
                    s=node['settings']
                    if s['mode']=='automatic':
                        if s['capability']=='basic-bright-objects':
                            if s['generation']!=bright_generation():raise InvalidProject('The pinned generation is unavailable; explicitly update a new workflow version')
                            if len(node['needs'])!=1:raise InvalidProject('Bright regions takes one source')
                            self.parameters(s['parameters'])
                        else:
                            from .matrix_capability_runs import descriptor, parameters
                            declared=descriptor(s['capability'])
                            if declared['generation']!=s['generation']:raise InvalidProject('The pinned capability generation is unavailable; save a new workflow version')
                            if not declared['ready']:raise InvalidProject(declared['reason'])
                            parameters(s['capability'],s['parameters'])
                            ports=[p['id'] for p in declared['inputs']]
                            mapping=s.get('bindings') or ({ports[0]:node['needs'][0]} if len(ports)==len(node['needs'])==1 else {})
                            if set(mapping)!=set(ports):raise InvalidProject('Bind every declared capability input port explicitly')
                    else:
                        from .prepared_bundles import prepared_member
                        from .scientific_tools import accepts
                        with prepared_member(s['capability'],s['generation']) as p:
                            if not accepts(p.m):raise InvalidProject('This tool does not declare scientific controls')
                if node['kind']=='researcher':
                    from .workflow_research import readiness
                    readiness(node['settings'])
                if node['kind'] in ('review','measure','export','compare') and node['settings']:
                    raise InvalidProject('This step has no configurable settings')
                if node['kind']=='tool' and node['settings']['mode']=='automatic' and node['settings']['capability']=='basic-bright-objects' and node['needs']==['input']:
                    for ref in inputs:self.bright_input(exact(self.store,ref,session),read=False)
            except (ValueError,OSError) as exc:problem=str(exc)
            rows.append({'id':node['id'],'available':problem is None,'reason':problem,'mode':node['settings'].get('mode','automatic')})
        return {'available':all(r['available'] for r in rows),'steps':rows}

    @staticmethod
    def parameters(values):
        if (set(values)!=set(bright_objects.DEFAULTS) or type(values['sigma']) not in (int,float)
                or not math.isfinite(values['sigma']) or not 0<=values['sigma']<=10
                or type(values['min_area']) is not int or not 1<=values['min_area']<=100000
                or type(values['connectivity']) is not int or values['connectivity'] not in (4,8)):
            raise InvalidProject('Use sigma 0–10, minimum area 1–100000 pixels and connectivity 4 or 8')

    def bright_input(self,saved,read=True):
        row,source=self.array(saved,'intensity')
        if source.axes!=('y','x') or source.dtype!='|u1' or math.prod(source.shape)>MAX_PIXELS:
            raise InvalidProject('Bright regions requires a retained 8-bit grayscale y/x image of at most 1 million pixels; select or convert explicitly')
        calibration=source.descriptor.get('calibration')
        if calibration:
            affine=calibration.get('affine');axes=calibration.get('axes');units=calibration.get('units')
            if axes!=['y','x'] or not isinstance(affine,list) or len(affine)!=2 or any(len(r)!=3 for r in affine) or len(units or [])!=2:
                raise InvalidProject('This calibration needs an explicit supported spatial conversion')
            basis=[[affine[0][2],affine[1][2]],
                   [affine[0][2]+affine[0][0],affine[1][2]+affine[1][0]],
                   [affine[0][2]+affine[0][1],affine[1][2]+affine[1][1]]]
        else:basis=[[0,0],[1,0],[0,1]];units=['pixel','pixel']
        geometry={'basis':basis,'axisLabels':['y','x'],'units':units}
        if not read:return None,geometry
        # Bounded blocks obey native-region limits even with heavily chunked input.
        data=np.empty(source.shape,dtype=source.dtype)
        for origin,block in actions.blocks(source,lambda:None):
            data[tuple(slice(i,i+n) for i,n in zip(origin,block.shape))]=block
        return data,geometry

    def execute(self,claim):
        self.check(claim);node=claim['node'];s=node['settings']
        sources=[exact(self.store,r,claim['session']) for r in claim['inputs']];source=sources[0]
        if node['kind']=='review':return {'status':'awaiting-review','output':identity(source)}
        if node['kind']=='tool' and s['mode']=='interactive':
            from .scientific_tools import fork
            working=fork(self.store,source,s['capability'],s['generation'],token(claim['attempt']),recipe=s['parameters'],check=lambda:self.check(claim))
            return {'status':'awaiting-controls','working':identity(working),'output':None}
        if node['kind']=='tool' and s['mode']=='automatic' and s['capability']!='basic-bright-objects':
            from .matrix_capability_runs import descriptor, run_matrix
            declared=descriptor(s['capability']);ports=[p['id'] for p in declared['inputs']]
            mapping=s.get('bindings') or ({ports[0]:node['needs'][0]} if len(ports)==len(node['needs'])==1 else {})
            if set(mapping)!=set(ports):raise InvalidProject('Bind every declared capability input port explicitly')
            refs=dict(zip(node['needs'],claim['inputs']))
            saved=run_matrix(self.store,claim['session'],s['capability'],s['generation'],
                {port:refs[dependency] for port,dependency in mapping.items()},s['parameters'],
                token(claim['attempt'])[:32],check=lambda:self.check(claim))
        elif node['kind']=='tool':
            check=self.validate({'nodes':[node]},claim['inputs'],claim['session'])
            if not check['available']:raise InvalidProject(check['steps'][0]['reason'])
            data,geometry=self.bright_input(source)
            success,encoded=cv2.imencode('.png',data)
            if not success:raise InvalidProject('The native image could not be encoded for the existing algorithm')
            capture=SimpleNamespace(id=claim['attempt']['id'],analysis_bytes=encoded.tobytes(),width=data.shape[1],height=data.shape[0],source_image=identity(source))
            result=bright_objects.analyze(capture,None,s['generation'],s['parameters'])
            from .evidence import decode_labels
            from .scientific_outputs import OutputSave
            output=OutputSave(self.store,source,claim['attempt']['id'],token(claim['attempt'])[:32],
                {'semantic':'instance-labels','axes':['y','x']},producer=result['producer'])
            try:
                worker=ArrayOutput(decode_labels(result['labels']),geometry)
                row,native=self.array(source,'intensity')
                if native.descriptor.get('calibration'):
                    from env_worker.scientific_output_coordinates import fingerprint
                    worker.info['coordinatesFrom']={'format':'same-input-grid-v1','input':row['key'],
                        'reference':row['reference'],'sha256':fingerprint(native.shape,native.dtype,native.read)}
                output.capture(worker);self.check(claim);output.publish()
                saved=self.store.read(output.target,source.session)
            finally:output.close()
        elif node['kind']=='measure':
            row,_=self.array(source,'instance-labels')
            # Operate on a private copy; never add a measurement to an independently edited input.
            from .scientific_copies import PreparedArray
            candidate=self.store.copy_candidate(source,PreparedArray(self.store,source.id,source.session,source.revision,row['reference'],row['reference']))
            candidate.identity='p'+token({'copy':claim['attempt']})[:32];candidate.transaction=token({'copy':claim['attempt']})
            try:copied=candidate.publish(lambda:self.workflows.run(claim['run'],claim['session']).document['state']['workflowRun']['control']=='stop')
            finally:candidate.close()
            saved=actions.measure(self.store,copied.id,copied.session,copied.revision,row['key'],check=lambda:self.check(claim),
                                  admission={'caller':'workflow','run':claim['run'],'attempt':claim['attempt']})
        elif node['kind']=='export':
            body=actions.table_csv(actions.table(self.store,source.id,source.session,source.revision))
            self.check(claim);saved=keep_file(self.store,source.session,token(claim['attempt']),'measurements.csv',body)
        elif node['kind']=='compare':
            saved=self.compare(claim,sources)
        elif node['kind']=='researcher':
            from .workflow_research import start
            return start(self.workflows,claim)
        else:raise InvalidProject('This step has no scientific execution adapter')
        doc=saved.document
        doc['state']['workflowOutput']={'run':claim['run'],'attempt':claim['attempt'],'inputs':claim['inputs'],'node':node}
        saved=self.store.save(saved.id,saved.session,doc,expected_revision=saved.revision)
        return {'status':'completed','output':identity(saved)}

    def compare(self,claim,sources):
        if self.hub is None:raise InvalidProject('Comparison needs the scientific host')
        from . import method_workflow
        from .admission import native_source
        masks=[]
        for saved in sources:
            row,_=self.array(saved,'instance-labels')
            selection={'kind':'array','id':saved.id,'session':saved.session,'revision':saved.revision,'component':row['reference']['component']}
            _,_,captured=native_source(self.store,selection,saved.session);masks.append({**selection,'digest':captured['revisionDigest']})
        arms=[{'id':chr(97+i),'factor':'algorithm','says':saved.document['title'],'status':'completed','mask':mask}
              for i,(saved,mask) in enumerate(zip(sources,masks))]
        root=exact(self.store,self.workflows.status(claim['run'],claim['session'])['inputs'][claim['item']],claim['session'])
        row,_=self.array(root)
        original={'kind':'array','id':root.id,'session':root.session,'revision':root.revision,
                  'component':row['reference']['component'],'digest':root.digest}
        selection={'session':claim['session'],'source':original,'arms':arms,'request':token(claim['attempt'])}
        result=asyncio.run(method_workflow.run(self.hub,SimpleNamespace(sid=claim['session'],generation=0),
            method_workflow.OPERATION,'retain',selection,caller='studio',check_caller=lambda:self.check(claim)))
        return result['project']
