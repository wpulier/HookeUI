"""Resolve a saved scientific document into exact, bounded private-host inputs.

This is not browser authentication or a saved edit. The workspace owner must admit
the original Project/Tab/window before entering, keep the context through reaping,
and provide current presentation receipts for interaction. Reopening a document
never calls this function automatically and never replays saved Python.
"""
from contextlib import contextmanager, ExitStack
import copy
from dataclasses import replace
import os

from env_worker.native_regions import IDENTITY
from gpu_worker.bundle_contract import capability_key
from . import jobs, scientific_host, owned_process
from .scientific_arrays import SAVE_WORKING_BYTES
from .scientific_files import VERIFY_WORKING_BYTES, RETAINED_METADATA_BYTES
from .scientific_projects import InvalidProject

FILE_METADATA_BYTES = RETAINED_METADATA_BYTES


def _files(store, project, session, revision, references, check, directories=None):
    """Keep metadata bounded across files as well as within each manifest."""
    sources = {}
    used = 0
    for name, reference in references.items():
        check()
        source = store.file_source(project, session, revision, reference, check=check)
        # Conservative CPython string + tuple-pointer allowance, plus labels/reference.
        used += len(source.chunks) * 160 + 4096
        if used > FILE_METADATA_BYTES:
            raise InvalidProject('These sources exceed the reader metadata allowance; open fewer sources together')
        description=(directories or {}).get(name)
        if description is not None:
            from env_worker.source_directory import index
            description=index(description,source.size)
            used+=len(str(description).encode())*4
            if used>FILE_METADATA_BYTES:raise InvalidProject('Folder metadata exceeds the reader allowance')
            source=replace(source,directory=description)
        sources[name] = source
    return sources


def native_inputs(store, saved, bindings, *, allow_empty=False):
    """Resolve exact accepted native grants independently of any viewer runtime."""
    if type(bindings) is not dict or not (0 if allow_empty else 1) <= len(bindings) <= 8:
        raise InvalidProject('A scientific view needs one to eight explicitly named native inputs')
    sources = {}
    for name, reference in bindings.items():
        if type(name) is not str or IDENTITY.fullmatch(name) is None:
            raise InvalidProject('A scientific input needs a valid bounded name')
        if type(reference) is not dict or reference not in saved.document['resources']:
            raise InvalidProject('A scientific input must name an exact resource accepted in this document revision')
        # The existing array store validates ownership, metadata and bounded chunk reads.
        # Other resource families require their own qualified reader, never an RGB fallback.
        sources[name] = store.array_source(saved.id, saved.session, saved.revision, reference)
    return sources


def _resolve(store, project, session, revision):
    """Read only the named accepted revision; never substitute its latest head."""
    if type(revision) is not int or revision < 1:
        raise InvalidProject('Choose an exact saved scientific document revision before opening its tools')
    saved = store.read(project, session, revision=revision)
    document = saved.document
    spec = document['state'].get('scientificView')
    if type(spec) is not dict or set(spec) not in ({'runtime','inputs'}, {'runtime','inputs','layers'}, {'runtime','inputs','files'}, {'runtime','inputs','files','directories'}, {'runtime','inputs','layers','tool'}, {'runtime','inputs','layers','initializeTool'}, {'runtime','inputs','layers','initializeTool','recipeTool'}):
        raise InvalidProject('This document needs a declared scientific view integration and named inputs')
    if 'initializeTool' in spec and (type(spec['initializeTool']) is not int or spec['initializeTool'] != 1):
        raise InvalidProject('This tool initialization version is unsupported; keep the original saved revision')
    if 'recipeTool' in spec:
        from env_worker.scientific_tool_document import document as recipe_document
        recipe_document(spec['recipeTool'])
    runtime, bindings = spec['runtime'], spec['inputs']
    if (type(runtime) is not dict or set(runtime) != {'bundle', 'generation', 'operation'}
            or runtime not in document['runtimes']):
        raise InvalidProject('The scientific view must use a runtime recorded in this document revision')
    files=spec.get('files',{})
    if (type(files) is not dict or len(files)>64 or any(type(name) is not str or not IDENTITY.fullmatch(name)
            or type(ref) is not dict or ref not in document['resources'] for name,ref in files.items())):
        raise InvalidProject('Scientific readers need exact accepted file references and distinct input names')
    directories=spec.get('directories',{})
    if type(directories) is not dict or any(name not in files for name in directories):
        raise InvalidProject('Folder bindings must name an exact accepted file input')
    sources = native_inputs(store, saved, bindings, allow_empty=bool(files))
    if 'layers' in spec:
        from env_worker.scientific_output_wire import retained_scene
        retained_scene(spec['layers'], sources)
        if 'tool' in spec:
            from env_worker.scientific_tool_document import snapshot
            snapshot(spec['tool'], spec['layers'])
    return saved, copy.deepcopy(runtime), sources


@contextmanager
def review(store, project, session, revision, *, environ=os.environ):
    """Run off-loop after workspace admission; yields the immutable saved identity and worker."""
    with jobs.MEMORY.reserve(SAVE_WORKING_BYTES / (1 << 20)):
        saved, runtime, sources = _resolve(store, project, session, revision)
        key = capability_key(runtime['bundle'], runtime['operation'])
        spec = saved.document['state']['scientificView']
        extra = {'retained_layers': spec['layers']} if 'layers' in spec else {}
        if 'tool' in spec:
            extra['retained_tool'] = spec['tool']
        if 'initializeTool' in spec:
            extra['initialize_tool'] = spec['initializeTool']
        if 'recipeTool' in spec:
            extra['recipe_tool'] = spec['recipeTool']
        with ExitStack() as leases:
            if spec.get('files'):
                leases.enter_context(jobs.MEMORY.reserve((VERIFY_WORKING_BYTES + FILE_METADATA_BYTES) / (1 << 20)))
                with owned_process.scope(60) as bound:
                    extra['files'] = _files(store, project, session, revision, spec['files'], bound.check, spec.get('directories'))
            with scientific_host.review(key, runtime['generation'], sources, environ, **extra) as worker:
                yield saved, worker
