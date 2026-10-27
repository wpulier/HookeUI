"""Declared private reader discovery and exact-source binding in the existing registry.

Binding records intent; it neither executes a plugin nor establishes qualification.
The ordinary scientific-review owner later revalidates and confines that generation.
"""
from __future__ import annotations
import hashlib
import os

from gpu_worker import bundle_contract, manifests as catalog
from . import bundle_admission, channels, env_runner, envs, own_trials, owned_process, prepared_bundles, readiness
from .scientific_projects import InvalidProject, _json
from .scientific_file_intake import FileIntake
from .scientific_files import CHUNK_BYTES, MAX_CHUNKS, _name


def source_document(store, project, session, revision):
    if type(revision) is not int or revision<1:
        raise InvalidProject('Choose the exact saved source revision')
    saved=store.read(project,session,revision=revision)
    source=saved.document['state'].get('sourceFile')
    if (saved.document['kind'] not in ('source-file','source-directory') or type(source) is not dict
            or set(source)!={'format','name','size','sha256','reference'}
            or type(source['format']) is not int or source['format']!=1
            or type(source['size']) is not int or not 0<=source['size']<=CHUNK_BYTES*MAX_CHUNKS
            or type(source['sha256']) is not str or len(source['sha256'])!=64
            or any(c not in '0123456789abcdef' for c in source['sha256'])
            or type(source['reference']) is not dict or source['reference'] not in saved.document['resources']):
        raise InvalidProject('Choose an original source file in Project Data')
    _name(source['name'])
    source=dict(source)
    source['kind']='directory' if saved.document['kind']=='source-directory' else 'file'
    if source['kind']=='directory':
        from env_worker.source_directory import index
        from .scientific_directory_intake import stack_of
        try:source['directory']=index(saved.document['state'].get('sourceDirectory'),source['size'])
        except ValueError as error:raise InvalidProject(str(error)) from error
        if stack:=stack_of(saved.document['state']):source['kind'],source['stack']='stack',stack
    return saved,source


KIND_WORDS={'file':'single files','directory':'folders','stack':'ordered stacks of files'}
FACT_WORDS={'values':'native values','dtype':'data type','byte-order':'byte order','axes':'axes','calibration':'calibration'}


def _listed(words):
    words=list(words)
    return words[0] if len(words)==1 else ', '.join(words[:-1])+' and '+words[-1]


def _this(source):
    if source.get('kind')=='stack':return f'a stack of {len(source["directory"]["entries"])} files'
    return 'a folder' if source.get('kind')=='directory' else 'a single file'


def _patterns(reader):
    if reader.get('format')==3:return list(reader['patterns'])
    return ['*'+suffix for suffix in reader.get('extensions',())]


def _declared(reader, source):
    """Whether the declared capability fits this source by kind and name: napari's filename patterns."""
    import fnmatch
    kind=reader.get('kind','file')
    if kind!=source.get('kind','file'):
        return f'This reader opens {KIND_WORDS.get(kind,kind)}; this source is {_this(source)}'
    name=source['name'].lower()
    if reader.get('format')!=3:
        if not name.endswith(tuple(reader['extensions'])):return 'This reader accepts '+', '.join(reader['extensions'])
        return ''
    entries=[row['path'] for row in source.get('directory',{}).get('entries',())]
    def stray(patterns):
        return [path for path in entries if not any(fnmatch.fnmatchcase(path.rsplit('/',1)[-1].lower(),p) for p in patterns)]
    def some(paths):
        return f'“{paths[0]}”'+(f' and {len(paths)-1} more do not' if len(paths)>1 else ' does not')
    if kind=='stack':
        # napari matches a stack's files, not the label Hooke gives the collection.
        if missing:=stray(reader['patterns']):
            return f'This reader opens stacks of files matching {", ".join(reader["patterns"])}; {some(missing)}'
    elif not any(fnmatch.fnmatchcase(name,pattern) for pattern in reader['patterns']):
        return f'This reader opens names matching {", ".join(reader["patterns"])}; this source is named “{source["name"]}”'
    missing=[path for path in reader.get('requires',()) if path not in set(entries)]
    if missing:
        return f'This reader needs the folder to contain {_listed(missing[:3])}; this one does not'
    if reader.get('entries') and (missing:=stray(reader['entries'])):
        return f'This reader needs every file to match {", ".join(reader["entries"])}; {some(missing)}'
    return ''


def inexact(reader):
    """Why Hooke would not keep this source's native facts exact with this reader, or ''."""
    if reader.get('format')!=3:
        return (f'This reader’s declaration (format {reader.get("format")}) does not state which native facts it keeps '
                'exact, so Hooke will not open scientific data with it. Its integration needs a format-3 reader declaration')
    missing=[FACT_WORDS[fact] for fact in FACT_WORDS if fact not in reader['keeps']]
    if missing:
        return (f'This reader does not declare that it keeps {_listed(missing)} exact, '
                'so Hooke will not open scientific data with it')
    return ''


def compatibility(reader, source):
    if not reader:return 'This integration does not declare a source-file reader'
    if why:=_declared(reader,source):return why
    if source['size']>reader['maximum_bytes']:
        return f'This reader opens sources up to {reader["maximum_bytes"]:,} bytes; this one is {source["size"]:,} bytes'
    return inexact(reader)


def choice(rows, source):
    """Choose by declared capability: one exact reader is chosen, several are the researcher's choice, none is said."""
    ready=[row for row in rows if row['canReview']]
    if len(ready)==1:
        return {'state':'chosen','capability':ready[0]['key'],'generation':ready[0]['generation'],
                'says':f'{ready[0]["name"]} is the one installed reader that declares this source and keeps its native facts exact.'}
    if ready:
        return {'state':'ambiguous','capabilities':[row['key'] for row in ready],
                'says':f'{len(ready)} installed readers declare this source and keep its native facts exact. Choose one; Hooke does not choose between them.'}
    if not rows:
        return {'state':'refused','says':'No installed integration declares a scientific reader.',
                'next':'add a reader integration in Models, then check again; the saved source stays as it is'}
    fitting=[row for row in rows if row['matches']]
    if not fitting:
        offered='; '.join(f'{row["name"]} ({KIND_WORDS.get(row["kind"],row["kind"])} {", ".join(row["patterns"])})' for row in rows[:5])
        return {'state':'refused','says':f'No installed reader declares {_this(source)} named “{source["name"]}”. Installed readers open: {offered}.',
                'next':'add a reader integration for this format in Models; the saved source stays as it is'}
    said=' '.join(f'{row["name"]}: {row["reason"].rstrip(".")}.' for row in fitting[:5])
    return {'state':'refused','says':f'{len(fitting)} installed reader{"s" if len(fitting)>1 else ""} declare{"" if len(fitting)>1 else "s"} this source, but none can open it exactly now. {said}',
            'next':'follow the step each reader names, then check again; the saved source stays as it is'}


def discover(store, project, session, revision, environ=os.environ):
    """Off-loop metadata only: no imports, installs, downloads or runtime proof."""
    saved,source=source_document(store,project,session,revision)
    rows=[]
    for owner in {**catalog.manifests(),**catalog.mine(environ)}.values():
        if owner.runner!='python-env' or not owner.members:continue
        generation=None;preparation_error=''
        try:
            generation=envs.generation(owner,environ)
            folder=envs.root(owner,environ)/generation
            if not (folder/envs.RECEIPT).is_file():
                preparation_error='Prepare this integration in Models before reviewing its reader'
        except (OSError,ValueError):
            preparation_error='This integration’s preparation is incomplete. Repair it in Models, then check readers again'
        try:
            installed = env_runner.in_use(owner, environ)
        except (OSError, ValueError):
            installed = None
        declaration_owner = (installed.m if installed is not None and bundle_admission.local_reader(installed.m)
                             else owner)
        local = bundle_admission.local_reader(declaration_owner)
        if local:
            if installed is None:
                preparation_error = 'Complete this reader’s exact reference checks in Models before opening it'
            else:
                generation = installed.folder.name
                declaration_owner = installed.m
                preparation_error = ('Review and enable this reader’s checked results in Models before opening it'
                                     if own_trials.pending(installed.m, environ) else '')
        for member in bundle_contract.parse_members(list(declaration_owner.members),declaration_owner):
            if not member.reader:continue
            declared=_declared(member.reader,source)
            # Incompatibility (this reader can never open this source exactly) is said apart from what setup
            # or this Mac's devices could change.
            reason=compatibility(member.reader,source)
            # A declaration that states no kept facts is the integration's to update, not this source's misfit.
            blocked='setup' if reason and member.reader.get('format')!=3 and reason==inexact(member.reader) else 'incompatible'
            if not reason:reason,blocked=readiness.misfit(declaration_owner),'device'
            if not reason and not channels.trusted(owner,environ):
                reason,blocked='Allow this catalog’s code before reviewing its reader','setup'
            if not reason:reason,blocked=preparation_error,'setup'
            blocked=blocked if reason else ''
            rows.append({'key':bundle_contract.capability_key(owner.key,member.id),'name':member.name,
                         'generation':generation,'owner':owner.key,'canReview':not bool(reason),
                         'reason':reason,'blocked':blocked,'matches':not declared,'kind':member.reader.get('kind','file'),
                         'patterns':_patterns(member.reader),
                         'keeps':list(member.reader['keeps']) if member.reader.get('format')==3 else None,
                         'scope':('Exact reader reference checks; independent scientific qualification remains open' if local and not preparation_error
                                  else 'Private candidate; reader operation and clean-machine qualification remain unproven')})
    return {'source':{'id':saved.id,'session':session,'revision':revision,'digest':saved.digest},'readers':rows,
            'choice':choice(rows,source)}


class _ReaderIntake(FileIntake):
    def __init__(self,store,saved,source,key,generation,input,keeps=None,*,name):
        owner,member=bundle_contract.parse_capability_key(key)
        self.runtime={'bundle':owner,'generation':generation,'operation':member}
        self.input=input
        self.reader_name=name
        self.directory=saved.document['state'].get('sourceDirectory')
        stack=saved.document['state'].get('sourceStack')
        self.origin={'format':1,'source':{'id':saved.id,'session':saved.session,'revision':saved.revision,'digest':saved.digest},
                     'capability':key,'generation':generation,'scope':'private reader candidate; not operation qualification'}
        if keeps is not None:self.origin['keeps']=list(keeps)
        if stack is not None:self.origin['stack']=dict(stack)  # the new axis, for checking what the reader opens
        identity=hashlib.sha256(_json(self.origin)).hexdigest()
        super().__init__(store,saved.session,identity,name=source.name,size=source.size,max_bytes=source.size,
                         retained_source=source)

    def document(self,ref):
        document=super().document(ref)
        document['kind']='scene';document['runtimes']=[self.runtime]
        document['title']=f'{self.name[:80]} · {self.origin["capability"][:130]} · {self.origin["generation"]}'
        document['state']['scientificView']={'runtime':self.runtime,'inputs':{},'files':{self.input:ref}}
        if self.directory is not None:
            document['state']['scientificView']['directories']={self.input:self.directory}
        document['state']['sourceReader']=self.origin
        # Names are presentation, not a new reader request. Recover an exact legacy
        # receipt unchanged, including a later user rename in its own revision.
        if self.store._folder(self.target).exists():
            accepted=self.store.read(self.target,self.session,revision=1)
            if accepted.document==document:
                return document
        document['title']=f'{self.name[:120]} · {self.reader_name[:100]}'
        document['state']['sourceReader']={**self.origin,'name':self.reader_name,'sourceName':self.name}
        return document


def bind(store, project, session, revision, key, generation, *, environ=os.environ, check=lambda:None):
    saved,source=source_document(store,project,session,revision)
    with owned_process.scope(60) as bound:
        def current():bound.check();check()
        current()
        with prepared_bundles.prepared_member(key,generation,environ) as prepared:
            if bundle_admission.local_reader(prepared.m.owner):
                installed = env_runner.in_use(prepared.m.owner, environ)
                if (installed is None or installed.folder.name != generation
                        or own_trials.pending(installed.m, environ)):
                    raise InvalidProject('The selected reader or its accepted checks changed; review it in Models and choose it again')
            if why:=compatibility(prepared.m.reader,source):raise InvalidProject(why)
            if prepared.m.bridge!='scientific-host-v1':raise InvalidProject('This reader has no scientific host bridge')
            actual=store.file_source(project,session,revision,source['reference'],check=current,verify_content=False)
            if any(source[field]!=getattr(actual,field) for field in ('name','size','sha256')):
                raise InvalidProject('The source identity changed; reopen its saved original')
            candidate=_ReaderIntake(store,saved,actual,key,generation,prepared.m.reader['input'],prepared.m.reader.get('keeps'),name=prepared.m.name)
            try:
                current()
                return candidate.publish(lambda: (current() or False))
            finally:candidate.close()
