"""Parent-owned new native outputs, published as independent scientific documents.

Staging is private. The final directory rename admits all bytes and provenance
at once. Lost acknowledgements retry the identical document, never Python.
"""
import copy
import math
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import time
import threading

_pending_cleanup = set()
_cleanup_lock = threading.Lock()

def retry_cleanup():
    with _cleanup_lock: attempts=list(_pending_cleanup)
    failures=[]
    for attempt in attempts:
        if not attempt.close(): failures.append(OSError('Scientific output staging cleanup remains pending'))
    return failures

from env_worker import native_edits as wire, scientific_output_wire as outputs
from env_worker.native_regions import DTYPES, MAX_REGION_BYTES
from env_worker import scientific_output_coordinates as coordinates
from . import scientific_arrays as arrays, owned_process
from .downloads import SPARE
from .scientific_history import CELL
from .scientific_host import CaptureRefused
from .scientific_projects import ProjectStore, InvalidProject, Conflict, _document
from .scientific_provenance import UNRECORDED, frozen
from .scientific_retention import publication
from .scientific_saves import admission, SaveNotStarted, SaveUnavailable


def image_chunks(shape, dtype):
    """Stream consecutive image rows before moving to the next leading-axis slab."""
    chunks = [1] * len(shape)
    remaining = MAX_REGION_BYTES // DTYPES[dtype]
    for axis in reversed(range(len(shape))):
        chunks[axis] = min(max(1, shape[axis]), remaining)
        remaining //= chunks[axis]
    return chunks


class OutputGrant:
    def __init__(self, draft, *, vertex_count=None, shape_types=None):
        self.draft = draft
        self.vertex_count = vertex_count
        self.shape_types = shape_types
        self.identity = secrets.token_hex(32)
        self.reference = None

    def specification(self):
        d = self.draft
        return {'format':wire.OUTPUT_FORMAT,'token':self.identity,'shape':list(d.shape),'dtype':d.dtype,
                'axes':list(d.axes),'chunks':list(d.chunks)}

    def admit_message(self, header, size):
        d = self.draft
        if (d.closed or type(header) is not dict or set(header) != {'op','token','index','shape','dtype'}
                or header['op'] != 'write-native-chunk' or header['token'] != self.identity
                or type(size) is not int or size < 0):
            raise InvalidProject('This output chunk has no active write grant')
        shape,count = wire.chunk(header['index'],d.shape,d.dtype,d.chunks,d.grid)
        if (type(header['shape']) is not list or any(type(n) is not int for n in header['shape'])
                or header['shape'] != list(shape) or header['dtype'] != d.dtype or size != count
                or d.attempted_bytes+size > d.allowance):
            raise InvalidProject('The output chunk does not match its bounded native grant')

    def answer(self, header, payload):
        import numpy as np
        if type(payload) is not bytes: raise InvalidProject('Output chunks require binary bytes')
        self.admit_message(header,len(payload))
        data=np.frombuffer(payload,dtype=self.draft.dtype).reshape(header['shape'])
        meaning=self.draft.scientific['semantic']
        if (meaning in ('class-labels','instance-labels') and np.any(data < 0)
                or meaning == 'probabilities' and (not np.isfinite(data).all() or np.any(data < 0) or np.any(data > 1))):
            raise InvalidProject('Output values do not satisfy the declared scientific meaning')
        if (meaning in ('surface-vertices','shape-vertices') and not np.isfinite(data).all()
                or meaning == 'surface-faces' and (self.vertex_count is None
                    or np.any(data < 0) or np.any(data >= self.vertex_count))):
            raise InvalidProject('Surface topology must reference existing finite vertices')
        if meaning == 'shape-offsets':
            from env_worker.scientific_shape_data import boundaries
            try:
                if self.shape_types is None or tuple(data.shape) != self.draft.shape:
                    raise ValueError('Shape boundaries require one complete bounded component')
                boundaries(data,self.shape_types,self.vertex_count)
            except ValueError as error: raise InvalidProject(str(error)) from error
        self.draft.write(header['index'],data)
        return {'ok':True,'kind':wire.OUTPUT_FORMAT,'index':header['index'],'bytes':len(payload)}, b''

    def cancel(self):
        self.draft.close()


class OutputSave:
    kind = 'new-output'
    capture_seconds = outputs.transfer_seconds(outputs.MAX_STREAMED_BYTES) + 8 * 60

    def __init__(self, store, saved, identity, layer, meaning, *, producer=None, selections=None, tool=False):
        store = store.for_session(saved.session, write=True)
        from .scientific_host import _token
        if (type(identity) is not str or not CELL.fullmatch(identity) or not _token(layer,32)
                or store.read(saved.id,saved.session,revision=saved.revision) != saved):
            raise InvalidProject('A new output needs its exact initiating revision and request')
        try: self.meaning = outputs.declaration(meaning)
        except ValueError as error: raise InvalidProject(str(error)) from error
        self.runtime = self.retained_runtime(saved)
        self.runtimes = [self.runtime] if self.runtime is not None else []
        self.store,self.saved,self.identity,self.layer = store,saved,identity,layer
        self.producer = frozen(producer)
        self.target = 'p'+secrets.token_hex(16)
        self.transaction = secrets.token_hex(32)
        self.stage = self.grant = self.info = self.body = self.receipt = self.reference = None
        self.edit = self.failure = self.worker_frame = None
        self.save_workspace = None
        self.closed = False
        self.grants = {}
        self.references = {}
        try:
            self.selections = (outputs.selections(selections, minimum=1 if tool else 2) if selections is not None else
                               [{'layer':layer,'meaning':self.meaning}])
            if self.selections[0] != {'layer':layer,'meaning':self.meaning}:
                raise ValueError('The primary output must match the first selected layer')
        except ValueError as error: raise InvalidProject(str(error)) from error
        self.items = []
        self.primary = None
        self.tool = tool
        self.tool_snapshot = None
        self.publication_bytes = arrays.MAX_NEW_BYTES

    @staticmethod
    def retained_runtime(saved):
        spec = saved.document['state'].get('scientificView')
        if type(spec) is not dict or spec.get('runtime') not in saved.document['runtimes']:
            raise InvalidProject('A new output needs its retained scientific runtime')
        return copy.deepcopy(spec['runtime'])

    def calibration(self):
        if self.items:
            return self.item_calibration(self.items[0])
        return outputs.calibration(self.info,self.meaning['axes'],
            {'session':self.saved.session,'id':self.target,'revision':1})

    def capture(self, worker):
        self.save_workspace = getattr(worker, 'save_workspace', None)
        if self.stage is not None: raise InvalidProject('Retry the captured output without capturing again')
        with admission(self.save_workspace), owned_process.scope(self.capture_seconds) as capture_bound:
            transport = False
            try:
                input_bindings = {}
                coordinate_digests = {}
                if self.tool:
                    inventory, _ = worker.request({'op': 'layer-inventory'})
                    input_bindings = {row['id']: row.get('editableInput') for row in inventory['inventory']['layers']}
                    if list(input_bindings) != [row['layer'] for row in self.selections]:
                        raise ValueError('Save the complete current tool layer set in its current order')
                for selection in self.selections:
                    capture_bound.check()
                    description = (worker.describe_output(selection['layer'], tool=True) if self.tool else
                                   worker.describe_output(selection['layer']))
                    info, meaning = outputs.compatible(description, selection['meaning'])
                    item = {'layer':selection['layer'],'metadata':info,'meaning':meaning}
                    relation = info.get('coordinatesFrom')
                    if relation is not None:
                        reference = self.saved.document['state']['scientificView']['inputs'].get(relation['input'])
                        if reference != relation['reference']:
                            raise ValueError('Output coordinates no longer name the exact admitted input revision')
                        source = self.store.array_source(self.saved.id, self.saved.session, self.saved.revision, reference)
                        calibration = coordinates.grid(info, meaning, source)
                        key = relation['input']
                        if key not in coordinate_digests:
                            coordinate_digests[key] = coordinates.fingerprint(source.shape, source.dtype, source.read)
                        if coordinate_digests[key] != relation['sha256']:
                            raise ValueError('The coordinate input has unsaved changes; accept it and rerun before saving this output')
                        item['scientific'] = {'calibration':calibration}
                        item['coordinatesFrom'] = copy.deepcopy(relation)
                    binding = input_bindings.get(selection['layer'])
                    if binding is not None:
                        reference = self.saved.document['state']['scientificView']['inputs'].get(binding)
                        if reference is None:
                            raise ValueError('This tool layer no longer names an admitted scientific input')
                        source = self.store.array_source(self.saved.id, self.saved.session, self.saved.revision, reference)
                        if (list(source.shape) != info['shape'] or source.dtype != info['dtype']
                                or meaning != {'semantic': source.descriptor.get('semantic'),
                                    'axes': list(source.axes[:-1] if info['format']==outputs.RGB_FORMAT else source.axes)}):
                            raise ValueError('A changed input shape, type or meaning needs an explicit converted layer')
                        item['scientific'] = {key: copy.deepcopy(value) for key, value in source.descriptor.items() if key != 'reference'}
                        calibration = item['scientific'].get('calibration')
                        if calibration is not None and not outputs.same_calibration(outputs.calibration(info, meaning['axes'], calibration['space']), calibration):
                            raise ValueError('A changed input calibration needs an explicit converted layer')
                    self.items.append(item)
                self.info = self.items[0]['metadata']
                parts = [outputs.array_parts(row['metadata']) for row in self.items]
                count = sum(math.prod(part['shape'])*DTYPES[part['dtype']] for components in parts for part in components.values())
                # Retained host admission is still bounded. Refuse before transport,
                # not after accepting a document that cannot reopen.
                if sum(map(len,parts)) > 8 or count > outputs.MAX_STREAMED_BYTES:
                    raise ValueError('These layers exceed the current view input or streamed-byte allowance; save a smaller set')
                if count > arrays.MAX_NEW_BYTES:
                    if self.tool or any('regionalSources' not in row['metadata'] for row in self.items):
                        raise ValueError('Larger publication requires immutable regional grey images; save other layers separately')
                    for row in self.items:
                        worker.admit_streamed_output(row['metadata'])
                    self.publication_bytes = count
                chunk_count = sum(math.prod(math.ceil(n/c) for n,c in zip(part['shape'],image_chunks(part['shape'],part['dtype'])))
                                  for components in parts for part in components.values())
                if chunk_count > arrays.MAX_CHUNKS:
                    raise ValueError('These outputs exceed the retained chunk-index allowance; save a smaller set')
                if shutil.disk_usage(self.store.root).free < count+arrays.SAVE_WORKING_BYTES+SPARE:
                    raise ValueError('Free disk space before saving this output')
                self.stage = Path(tempfile.mkdtemp(prefix='.scientific-output-',dir=self.store.root))
                self.private = ProjectStore(self.stage)
                seed = self.private.create(self.saved.session, {'kind':'scene','title':self.info['name'],
                    'state':{},'resources':[],'runtimes':self.runtimes},identity=self.target)
                for index,(item,components) in enumerate(zip(self.items,parts)):
                    capture_bound.check()
                    prefix = '' if len(self.items)==1 else f'layer{index+1}_'
                    item['names'] = {name:prefix+name for name in sorted(components)}
                    item['grants'] = self.allocate(seed,item,components)
                    self.grants.update({item['names'][name]:grant for name,grant in item['grants'].items()})
                self.primary = self.items[0]['names']['vertices' if self.info['type'] in outputs.COMPOUND else 'output']
                self.grant = self.grants[self.primary]
                self.preflight_reopen()
                capture_bound.check()
                transport = True
                if self.tool:
                    self.tool_snapshot = worker.stage_outputs(self.items, tool=True)
                    self.preflight_reopen()
                elif len(self.items)>1 or 'levels' in self.info: worker.stage_outputs(self.items)
                elif self.info['type'] in outputs.COMPOUND:
                    worker.stage_output_components(self.layer,self.info,self.meaning,self.grants)
                else: worker.stage_output(self.layer,self.info,self.meaning,self.grant)
                self.edit = self.grant
            except (CaptureRefused, ValueError, OSError) as error:
                self.close()
                from .scientific_host import Failed
                if (transport or isinstance(error,Failed)) and not isinstance(error,CaptureRefused):
                    raise RuntimeError('Scientific output transfer failed; the private session stopped and accepted data stayed unchanged') from error
                raise SaveNotStarted(f'No new output was saved. Private work remains available. {error}') from error
            except BaseException:
                self.close(); raise

    def allocate(self, seed, item, components):
        info,meaning = item['metadata'],item['meaning']
        space = self.item_calibration(item)['space']
        grants = {}
        for name,row in sorted(components.items()):
            level_info = (outputs.level_metadata(info,0 if name=='output' else int(name[5:]))
                          if 'levels' in info else info)
            chunks = [max(1,n) for n in row['shape']]
            if info['type'] == 'image' and info['format'] == outputs.FORMAT:
                chunks = image_chunks(row['shape'], row['dtype'])
            else:
                while math.prod(chunks)*DTYPES[row['dtype']] > MAX_REGION_BYTES:
                    i=max(range(len(chunks)),key=chunks.__getitem__); chunks[i]=max(1,chunks[i]//2)
            if info['type'] in outputs.COMPOUND:
                contract = outputs.component_contract(info['type'])
                axes = contract.axes(name,len(row['shape']))
                scientific = {'semantic':contract.semantic(name)}
                if name == 'vertices': scientific.update(outputs.scientific(info,meaning['axes'],space))
            else:
                axes = outputs.storage_axes(info,meaning['axes'])
                scientific = {**item.get('scientific', {}), 'semantic':meaning['semantic'], **outputs.scientific(level_info,meaning['axes'],space)}
                if 'calibration' in item.get('scientific', {}) and 'levels' not in info:
                    scientific['calibration'] = copy.deepcopy(item['scientific']['calibration'])
            draft = self.private.array_draft(seed.id,seed.session,component='array.'+item['names'][name],
                shape=row['shape'],dtype=row['dtype'],axes=axes,chunks=chunks,
                scientific=scientific,max_written_bytes=math.prod(row['shape'])*DTYPES[row['dtype']])
            shape_types = None
            if name == 'offsets':
                from env_worker.scientific_shapes import geometry_types
                shape_types = geometry_types(info['state'])
            grant = OutputGrant(draft,vertex_count=info['shape'][0] if name in ('faces','offsets') else None,
                                shape_types=shape_types)
            grants[name] = grant
            # Own each draft immediately, including failure halfway through allocation.
            self.grants[item['names'][name]] = grant
        return grants

    def item_calibration(self, item):
        if 'calibration' in item.get('scientific', {}):
            return copy.deepcopy(item['scientific']['calibration'])
        space={'session':self.saved.session,'id':self.target,'revision':1}
        if len(self.items)>1:
            space['component']='coordinates.'+item['names']['vertices' if item['metadata']['type'] in outputs.COMPOUND else 'output']
        return outputs.calibration(item['metadata'],item['meaning']['axes'],space)

    def preflight_reopen(self):
        from .native_regions import NativeRegionInputs, Source
        from .scientific_host import admit_header
        sources={}; layers=[]
        for name,grant in self.grants.items():
            d=grant.draft
            # Revision hashes and read tokens have fixed encoded lengths. This is
            # the exact future open envelope size, without reading/finalizing data.
            reference={'session':d.session,'id':d.project,'component':d.component,'revision':'0'*64}
            sources[name]=Source(d.shape,d.dtype,d.axes,{**d.scientific,'reference':reference},lambda _:None)
        for item in self.items:
            info,names=item['metadata'],item['names']
            row={'input':names['vertices' if info['type'] in outputs.COMPOUND else 'output'],'metadata':coordinates.plain(info)}
            if info['type'] in outputs.COMPOUND:row['components']=names
            if 'levels' in info:row['levels']=[names[name] for name in outputs.array_parts(info)]
            layers.append(row)
        grants=NativeRegionInputs(sources)
        try:admit_header({'op':'open','inputs':grants.specifications(),'retainedLayers':layers,
                         **({'retainedTool': self.tool_snapshot} if self.tool_snapshot is not None else {})})
        finally:grants.close()

    def document(self):
        source={'id':self.saved.id,'session':self.saved.session,'revision':self.saved.revision,'digest':self.saved.digest}
        layers=[]; records=[]
        for item in self.items:
            info,meaning,names = item['metadata'],item['meaning'],item['names']
            primary = names['vertices' if info['type'] in outputs.COMPOUND else 'output']
            layer={'input':primary,'metadata':coordinates.plain(info)}
            if info['type'] in outputs.COMPOUND: layer['components']=names
            if 'levels' in info:layer['levels']=[names[name] for name in outputs.array_parts(info)]
            layers.append(layer)
            records.append({'layer':item['layer'],'output':self.references[primary],'meaning':meaning,
                            'components':{name:self.references[key] for name,key in names.items()},
                            'calibration':self.item_calibration(item),
                            **({'coordinatesFrom':item['coordinatesFrom']} if 'coordinatesFrom' in item else {})})
        provenance={'format':1 if len(layers)==1 else 2,'base':source,'inputs':self.saved.document['resources'],
                    'producer':self.producer,'transaction':self.transaction,'at':time.time(),'replayable':False,
                    'runtimes':copy.deepcopy(self.saved.document['runtimes']),'unrecorded':UNRECORDED,
                    'coordinateMeaning':'Layer transforms and units are retained. No coordinate-space alignment was inferred.'}
        if any('coordinatesFrom' in item for item in self.items):
            provenance['coordinateMeaning'] = ('Explicit same-input-grid declarations retain the exact admitted source '
                'reference and value proof; its geometry was checked. Other layers retain their independent spaces.')
        state={'scientificView':{**({'runtime':self.runtime} if self.runtime is not None else {}),'inputs':self.references,'layers':layers},'hookeOutput':provenance}
        if self.tool:
            from env_worker.scientific_tool_document import snapshot
            state['scientificView']['tool'] = snapshot(self.tool_snapshot, layers)
        if len(layers)==1:
            state['coordinateSpace']=records[0]['calibration']
            provenance.update(output=self.reference,meaning=self.meaning)
            if 'coordinatesFrom' in self.items[0]:
                provenance['coordinatesFrom'] = self.items[0]['coordinatesFrom']
            if self.info['type'] in outputs.COMPOUND:
                state['scientificResource']={'semantic':self.meaning['semantic'],'relations':self.references,
                    'calibration':self.calibration(),'storage_format':outputs.COMPOUND[self.info['type']],'storage_version':'1'}
                provenance['components']=self.references
            elif 'levels' in self.info:
                provenance['components']=self.references
        else:
            provenance['outputs']=records
        suffix=f' + {len(layers)-1} layers'
        title = (self.saved.document['title'] if self.tool else
                 self.info['name'] if len(layers)==1 else self.info['name'][:240-len(suffix)]+suffix)
        return _document({'kind':'scene','title':title,
                          'resources':list(self.references.values()),'runtimes':self.runtimes,'state':state})

    def publish(self, stopping=lambda:False):
        if self.receipt is not None: return self.status()
        if self.closed or self.edit is None: raise SaveUnavailable('Capture the native output before publishing it')
        if stopping(): raise SaveUnavailable('Output publication stopped. Accepted work is unchanged')
        deadline = time.monotonic() + outputs.transfer_seconds(self.publication_bytes)
        def check():
            if self.closed or stopping():
                raise InvalidProject('Output publication stopped; accepted work is unchanged')
            if time.monotonic() >= deadline:
                raise InvalidProject('Output verification timed out; retry the same captured output')
        with admission(self.save_workspace):
            try:
                for name,grant in self.grants.items():
                    check()
                    if name not in self.references: self.references[name]=grant.draft.finish()
                if self.reference is None: self.reference=self.references[self.primary]
                if self.body is None: self.body=self.document()
                folder=self.private._folder(self.target)
                target=self.store._folder(self.target)
                with self.store._locked(self.store.root):
                    if target.exists() or target.is_symlink():
                        first=self.store.read(self.target,self.saved.session,revision=1)
                        if first._document != self.body or first.transaction != self.transaction:
                            raise Conflict('This new document identity belongs to other work; nothing was overwritten')
                        self.store._sync_directory(self.store.root); self.receipt=first
                        return self.status()
                self.private._publish(folder,self.saved.session,self.body,[],transaction=self.transaction,
                                      check=check,native_new_bytes=self.publication_bytes)
                with self.store._locked(self.store.root), publication(self.store.root,self.store.runtime_root):
                    check()
                    if target.exists() or target.is_symlink(): raise Conflict('The output destination changed')
                    os.rename(folder,target)
                    self.store._sync_directory(self.store.root)
                    self.receipt=self.store.read(self.target,self.saved.session,revision=1)
            except (OSError,InvalidProject) as error:
                raise SaveUnavailable(f'The new output is not confirmed saved. Retry the same captured values without rerunning code. {error}') from error
        return self.status()

    def status(self):
        return {'request':self.identity,'kind':self.kind,**({'tool': True} if self.tool else {}),'state':'saved' if self.receipt else 'staged',
                'layer':self.layer,**({'layers':[item['layer'] for item in self.selections]} if len(self.selections)>1 else {}),'resource':None if self.receipt is None else
                {'day':self.receipt.session,'id':self.receipt.id,'version':self.receipt.revision,'picture':None}}

    def close(self):
        self.closed=True
        for grant in self.grants.values(): grant.cancel()
        try:
            if self.stage is not None: shutil.rmtree(self.stage); self.stage=None
        except OSError:
            with _cleanup_lock: _pending_cleanup.add(self)
            return False
        else:
            with _cleanup_lock: _pending_cleanup.discard(self)
            return True

    def release(self):
        # Acceptance is final; an empty staging directory that cannot be removed
        # must not kill the worker or its unrelated private layers/variables.
        return self.close()


class BuiltinOutputSave(OutputSave):
    """Parent-owned algorithm output; no external viewer runtime is borrowed from its input.

    Only trusted in-process adapters construct this class. Worker/API output capture
    continues using OutputSave and its retained-runtime admission. Native publication,
    provenance, coordinate validation and bounded chunk grants remain identical.
    """
    def __init__(self, *args, producer, **kwargs):
        if (type(producer) is not dict
                or not isinstance(producer.get('implementation'), str) or not producer['implementation']
                or not isinstance(producer.get('version'), str) or not producer['version']
                or kwargs.get('tool')):
            raise InvalidProject('Built-in output requires an identified versioned producer, not external tool capture')
        super().__init__(*args, producer=producer, **kwargs)

    @staticmethod
    def retained_runtime(saved):
        spec = saved.document['state'].get('scientificView')
        if type(spec) is not dict or type(spec.get('inputs')) is not dict:
            raise InvalidProject('Built-in output requires retained native inputs')
        return None
