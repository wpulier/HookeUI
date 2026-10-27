"""The retained scientific resource families, one transaction across them, and one inventory.

``FAMILIES`` says, for every family the H01 work package names, where its records live, how its bytes
are stored and which module opens it. ``publish`` accepts new tables, embeddings and artifacts of one
exact saved revision in one Project CAS: every family checks and writes its parts first, so nothing
is accepted unless all of it is. ``inventory`` lists every resource of a revision by family, each
opened by its own family's checks; one that cannot be opened is listed as unreadable with the reason,
never silently dropped.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from env_worker import native_regions as wire
from .scientific_projects import InvalidProject

# Family, its semantic roles, where its record lives in a document and how its bytes are stored.
FAMILIES = (
    {'family': 'image', 'semantics': ('intensity', 'probabilities'), 'record': 'scientificView.layers',
     'storage': 'native-layer-output-v1 / native-rgb-output-v1 array component', 'opens': 'scientific_project_review'},
    {'family': 'labels', 'semantics': ('class-labels', 'instance-labels'), 'record': 'scientificView.layers',
     'storage': 'native-layer-output-v1 integer array component', 'opens': 'scientific_project_review'},
    {'family': 'points', 'semantics': ('point-set',), 'record': 'scientificView.layers',
     'storage': 'native-points-output-v1 coordinate component and typed state', 'opens': 'scientific_project_review'},
    {'family': 'shapes', 'semantics': ('shape-set',), 'record': 'scientificView.layers',
     'storage': 'native-shapes-output-v1 vertex, offset and typed state components', 'opens': 'scientific_project_review'},
    {'family': 'surface', 'semantics': ('surface-mesh',), 'record': 'scientificView.layers',
     'storage': 'native-surface-output-v1 vertex, face and value components', 'opens': 'scientific_project_review'},
    {'family': 'vectors', 'semantics': ('vector-set',), 'record': 'scientificView.layers',
     'storage': 'native-vectors-output-v1 origin/displacement component', 'opens': 'scientific_project_review'},
    {'family': 'tracks', 'semantics': ('track-coordinates',), 'record': 'scientificView.layers',
     'storage': 'native-tracks-output-v1 coordinate component, features and division state', 'opens': 'scientific_project_review'},
    {'family': 'table', 'semantics': ('object-table', 'lineage-graph', 'relation-table'), 'record': 'scientificTables',
     'storage': 'one native array component per typed column', 'opens': 'scientific_tables'},
    {'family': 'embeddings', 'semantics': ('embeddings',), 'record': 'scientificEmbeddings',
     'storage': 'one native [row, feature] array component keyed to an object table', 'opens': 'scientific_embeddings'},
    {'family': 'artifact', 'semantics': ('trained-artifact', 'animation-document'), 'record': 'scientificArtifacts',
     'storage': 'one retained file component with a typed descriptor; never executed on open', 'opens': 'scientific_artifacts'},
    {'family': 'source-file', 'semantics': (), 'record': 'sourceFile',
     'storage': 'project-source-file-v1 retained file (source-file and source-directory documents)', 'opens': 'scientific_files'},
)
RECORDS = ('scientificTables', 'scientificEmbeddings', 'scientificArtifacts')


@dataclass
class Prepared:
    """One family's checked, written parts awaiting the shared CAS."""
    record: str
    replaced: set
    references: list
    state: dict
    declared: dict


def chunks(dtype, shape):
    """Chunks along the first axis, splitting later axes only when one row exceeds the region budget."""
    item = wire.DTYPES[dtype]
    sizes = [max(1, n) for n in shape]
    out = list(sizes)
    for axis in range(len(out)):
        rest = math.prod(out[axis + 1:]) * item
        if rest <= wire.MAX_REGION_BYTES:
            out[axis] = max(1, min(sizes[axis], wire.MAX_REGION_BYTES // rest))
            break
        out[axis] = 1
    return out


def write_array(store, saved, component, data, axes, descriptor):
    """One exact native component: every chunk written, the manifest finished, nothing accepted yet."""
    draft = store.array_draft(saved.id, saved.session, component=component, shape=list(data.shape),
                              dtype=data.dtype.str, axes=list(axes), chunks=chunks(data.dtype.str, data.shape),
                              scientific=descriptor, max_written_bytes=data.nbytes)
    import itertools
    for index in itertools.product(*(range(n) for n in draft.grid)):
        region = tuple(slice(i * c, (i + 1) * c) for i, c in zip(index, draft.chunks))
        draft.write(index, np.ascontiguousarray(data[region]))
    return draft.finish()


def publish(store, saved, *, tables=None, embeddings=None, artifacts=None, replace=(), state=None,
            transaction=None, check=lambda: None):
    """New tables, embeddings and artifacts of one exact revision, accepted together or not at all."""
    from . import scientific_artifacts, scientific_embeddings, scientific_tables
    if state is not None and (type(state) is not dict or any(key in state for key in RECORDS)):
        raise InvalidProject('A producer record cannot replace a family record')
    if store.read(saved.id, saved.session, revision=saved.revision) != saved:
        raise InvalidProject('Publish from one exact saved revision')
    if not (tables or embeddings or artifacts):
        raise InvalidProject('Choose something to publish')
    prepared = []
    if tables:
        prepared.append(scientific_tables.prepare(store, saved, tables, replace=replace, check=check))
    pending = prepared[0].declared if tables else {}
    if embeddings:
        prepared.append(scientific_embeddings.prepare(store, saved, embeddings, pending, replace=replace, check=check))
    if artifacts:
        prepared.append(scientific_artifacts.prepare(store, saved, artifacts, replace=replace, check=check))
    if tables:
        # An embedding follows its table's rows: one not republished here cannot outlive a replaced table.
        scientific_embeddings.check_tables_kept(saved.document, pending, republished=set(embeddings or {}))
    document = saved.document
    replaced = set().union(*(part.replaced for part in prepared))
    document['resources'] = ([ref for ref in document['resources'] if ref.get('component') not in replaced]
                             + [ref for part in prepared for ref in part.references])
    document['state'].update(state or {})
    for part in prepared:
        document['state'][part.record] = part.state
    check()
    return store.save(saved.id, saved.session, document, expected_revision=saved.revision,
                      transaction=transaction, check=check)


def inventory(store, project, session, revision):
    """Every resource of one revision, by family; an unreadable one is listed with why."""
    from . import scientific_artifacts, scientific_embeddings, scientific_tables
    saved = store.read(project, session, revision=revision)
    state = saved.document['state']
    rows = []

    def entry(family, name, opener, describe):
        try:
            rows.append({'family': family, 'name': name, 'readable': True, **describe(opener())})
        except (InvalidProject, ValueError, OSError) as error:
            rows.append({'family': family, 'name': name, 'readable': False, 'reason': str(error)})

    view = state.get('scientificView') if type(state.get('scientificView')) is dict else {}
    for layer in view.get('layers', []) if type(view.get('layers')) is list else []:
        metadata = layer.get('metadata', {}) if type(layer) is dict else {}
        rows.append({'family': metadata.get('type', 'unknown'), 'name': metadata.get('name', '?'), 'readable': True,
                     'shape': metadata.get('shape'), 'dtype': metadata.get('dtype'), 'opens': 'the scientific view'})
    for name in scientific_tables.tables(saved.document):
        entry('table', name, lambda name=name: scientific_tables.open_table(store, project, session, revision, name),
              lambda t: {'semantic': t.schema['semantic'], 'rows': t.rows, 'columns': len(t.schema['columns']),
                         'key': t.schema['key']})
    for name in scientific_embeddings.records(saved.document):
        entry('embeddings', name, lambda name=name: scientific_embeddings.open_embedding(store, project, session, revision, name),
              lambda e: {'semantic': 'embeddings', 'rows': e.schema['rows'], 'dims': e.schema['dims'],
                         'table': e.schema['table']})
    for name in scientific_artifacts.records(saved.document):
        entry('artifact', name, lambda name=name: scientific_artifacts.open_artifact(store, project, session, revision, name),
              lambda a: {'semantic': a.schema['semantic'], 'size': a.schema['size'], 'sha256': a.schema['sha256'],
                         'mediaType': a.schema['media_type']})
    if type(state.get('sourceFile')) is dict:
        rows.append({'family': 'source-file', 'name': state['sourceFile'].get('name'), 'readable': True,
                     'size': state['sourceFile'].get('size')})
    return {'source': {'id': project, 'session': session, 'revision': revision, 'digest': saved.digest},
            'resources': rows}


def remap(state, mapping):
    """References from family records to other components follow the plain copy of those bytes."""
    from . import scientific_embeddings, scientific_tables
    scientific_tables.remap(state, mapping)
    scientific_embeddings.remap(state, mapping)
