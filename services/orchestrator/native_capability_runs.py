"""Headless native cells caller through the same catalog, Runner and Project store.

No package-name routing or cloud-specific scientific result storage. This caller
accepts the same lossless uint8 Y/X native input as the ordinary cells action,
keeps exact label values and records the originating accepted revision. It never
opens a camera, paints a view, chooses a fallback or accepts a provider's result.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import time
import zlib

import numpy as np

from . import kinds, native_picture_inputs
from .capability_identity import supports_qualification
from .capability_runs import Failed, Refused
from .evidence import decode_labels
from .scientific_file_intake import FileIntake, request_identity
from .scientific_families import write_array
from .scientific_projects import InvalidProject, _json
from .scientific_provenance import UNRECORDED, frozen


class NativeOutput(FileIntake):
    """Atomic independent native output via existing intake/publication transaction."""
    def __init__(self, store, saved, request, labels, descriptor, producer):
        self.base = saved
        record = frozen(producer)
        self.descriptor = copy.deepcopy(descriptor)
        body = _json(record)
        super().__init__(store, saved.session, request, name='execution.json', size=len(body), max_bytes=32 << 10)
        # After FileIntake's own constructor, which sets its importer `producer` (None here): this is the run's record.
        self.producer = record
        try:
            self.write(body)
            seed = self.private.read(self.target, saved.session)
            self.reference = write_array(self.private, seed, 'array.labels', labels, ['y', 'x'],
                {'semantic': 'instance-labels', **({'calibration': descriptor['calibration']}
                                                 if 'calibration' in descriptor else {})})
        except BaseException:
            self.close()
            raise

    def document(self, receipt):
        saved = self.base
        provenance = {'format': 1, 'base': {'id': saved.id, 'session': saved.session,
                       'revision': saved.revision, 'digest': saved.digest},
                      'inputs': saved.document['resources'], 'output': self.reference,
                      'meaning': {'semantic': 'instance-labels', 'axes': ['y', 'x']},
                      'producer': self.producer, 'transaction': self.transaction,
                      'at': self.producer['at'], 'replayable': False,
                      'runtimes': saved.document['runtimes'], 'unrecorded': UNRECORDED,
                      'coordinateMeaning': 'Exact admitted Y/X grid and calibration; native label IDs are unchanged.'}
        return {'kind': 'scene', 'title': 'Segmented labels', 'runtimes': saved.document['runtimes'],
                'resources': [self.reference, receipt],
                'state': {'scientificView': {'inputs': {'labels': self.reference}},
                          'hookeOutput': provenance}}


def _admit(capability, params, environ):
    kinds.register(environ=environ)
    with kinds.read_once():
        kind = kinds.match(kinds.available(environ), capability)
        if kind is None or kind.get('runnable') is False or not native_picture_inputs.supported(kind):
            raise Refused('Choose a ready single-output cells capability supporting this retained native input',
                          next='open Tools, prepare a supported cells capability, and select it for this saved input')
        if not supports_qualification(kind.get('runner')) and kind.get('runner') != 'gpu-worker':
            raise Refused('This worker requires generation-bound capability and environment admission',
                          next='choose a prepared capability whose runner binds its generation and environment')
        manifest = kinds.manifest_of(kind, environ)
        values = kinds.values_for(kind, params, environ, m=manifest, agent=True) if kind.get('params') or params else {}
        return {**kind, '_manifest': manifest}, values


def checked_labels(wire, shape):
    """Bound the shared wire decoder before allocation; retain exact native IDs/dtype."""
    if (type(wire) is not dict or not {'dtype', 'shape', 'zlib'} <= set(wire)
            or type(wire['zlib']) is not bytes or len(wire['zlib']) > 8 << 20
            or wire['shape'] != list(shape) or wire.get('origin', [0, 0]) not in ([0, 0], (0, 0))):
        raise Failed('The declared labels do not cover the admitted native grid')
    dtype = np.dtype(wire['dtype'])
    if dtype.kind not in 'ui' or dtype.itemsize > 4:
        raise Failed('The declared labels are not supported native integer IDs')
    count = int(np.prod(shape)) * dtype.itemsize
    decoder = zlib.decompressobj()
    raw = decoder.decompress(wire['zlib'], count + 1)
    if len(raw) != count or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise Failed('The label payload does not match its bounded declared layout')
    labels = decode_labels(wire)
    if np.any(labels < 0):
        raise Failed('Instance labels must be nonnegative')
    return labels


async def run_native(host, *, reference, session, capability, generation, environment,
                     params, request, environ=os.environ, check=lambda: None):
    """One execution intent is owned by the caller; reopening only reads its result."""
    from .operations import Prompt
    store = await asyncio.to_thread(host.resources.projects.for_session, session, write=True)
    source, descriptor, exact = native_picture_inputs.layout(store, reference, session)
    saved = store.read(reference['id'], session, revision=reference['revision'])
    kind, values = await asyncio.to_thread(_admit, capability, params, environ)
    if type(generation) is not str or not generation or type(environment) is not dict or not environment:
        raise InvalidProject('Pin the admitted entry generation and prepared environment')
    target, _ = request_identity(session, request)
    if store._folder(target).exists():
        previous = store.read(target, session, revision=1)
        producer = previous.document['state'].get('hookeOutput', {}).get('producer', {})
        if (producer.get('input') != exact or producer.get('capability') != capability
                or producer.get('generation') != generation or producer.get('environment') != environment
                or producer.get('params') != values):
            raise InvalidProject('This native request belongs to different scientific work')
        return previous
    check()
    gpu = None
    capture, hold = await asyncio.to_thread(native_picture_inputs.resolve, host, reference, session)
    try:
        if kind.get('runner') == 'gpu-worker':
            from .native_gpu_runtime import prepare
            gpu = await prepare(host, kind['_manifest'], generation, environment)
        check()
        done = await host.runner.run(kind['operation'], capture, Prompt('field'), choice=kind['id'],
            policy='raw', origin='headless', params={'inputs': json.dumps(values, sort_keys=True),
                'expectedGeneration': generation, 'expectedEnvironment': json.dumps(environment, sort_keys=True)})
        output = done.output
        producer = output.get('producer') or {}
        if gpu is not None:
            from .capability_identity import generation_of
            if generation_of(kind['_manifest']) != generation:
                raise Failed('The capability changed during execution; no result published')
            producer = {**producer, 'entryGeneration': generation, 'environment': environment,
                        'device': gpu.device, 'binding': 'observed worker packages and pinned weight bytes'}
        if (producer.get('entryGeneration') != generation or producer.get('environment') != environment
                or output.get('output') not in (None, 'labels.instance.2d')):
            raise Failed('The producer did not confirm its admitted generation, environment and output type')
        check()
        with host.captures.memory.reserve('analysis', int(np.prod(source.shape)) * 16, 'retaining exact native labels'):
            labels = checked_labels(output.get('labels'), source.shape)
            record = {'kind': 'capability', 'capability': capability, 'generation': generation,
                      'environment': environment, 'params': values, 'input': exact,
                      'producer': producer, 'execution': done.execution.wire(),
                      'sha256': hashlib.sha256(labels.tobytes()).hexdigest(), 'at': time.time()}
            intake = NativeOutput(store, saved, request, labels, descriptor, record)
            try:
                check()
                kept = intake.publish(lambda: bool(check()))
                host.runner.produced(done.execution, kept.id)
                return kept
            finally:
                intake.close()
    finally:
        hold.release()
        if gpu is not None:
            await asyncio.to_thread(gpu.stop)
