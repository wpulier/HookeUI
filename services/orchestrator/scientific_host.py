"""Owned private review worker; no browser authority, activation or accepted edits.

Call off-loop after authorizing exact Project revisions and bounded source readers.
Keep the context until its process is reaped. A browser-facing owner must additionally
bind requests to Project, Tab, placement, incarnation and rendered-frame receipts.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack, suppress
from contextvars import ContextVar
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import re
import shutil
import secrets
import signal
import subprocess
import tempfile
import threading

from env_worker.napari_cgl import ViewportLimits
from env_worker import scientific_view_wire, scientific_console, scientific_label_wire, scientific_python_help, scientific_source_wire
from env_worker.server import read_message, write_message
from env_worker.scientific_input_refusal import InputRefused
from gpu_worker import manifests as catalog
from . import channels, jobs, owned_process, prepared_artifacts, prepared_bundles, sandbox, worker_resources
from .native_regions import NativeRegionInputs
from .paths import SERVICES

MAX_FRAME_BYTES = 4 << 20
MAX_HEADER = 128 << 10
CALL_SECONDS = 60
WORKER_LIMITS = worker_resources.Limits(memory_bytes=1 << 30, processes=1, file_bytes=1 << 20,
                                      work_bytes=64 << 20, work_entries=512)
BOOTSTRAP = {'env_worker': ('server.py', 'source_files.py', 'source_directory.py', 'native_regions.py', 'lazy_arrays.py', 'lazy_labels.py', 'native_edits.py', 'region_reader.py', 'napari_cgl.py',
                            'scientific_reader_probe.py', 'scientific_points.py', 'scientific_tracks.py', 'scientific_layer_colors.py', 'scientific_vectors.py', 'scientific_surface_data.py', 'scientific_surfaces.py', 'scientific_shape_data.py', 'scientific_shapes.py', 'scientific_structured_state.py', 'scientific_output_wire.py', 'scientific_output_coordinates.py', 'scientific_calibration.py', 'scientific_decoders.py', 'scientific_outputs.py', 'scientific_controls.py', 'scientific_callback_errors.py', 'scientific_input_refusal.py', 'scientific_native_panel.py', 'scientific_native_panel_wire.py', 'scientific_tool_document.py', 'scientific_family_drafts.py', 'scientific_sequence.py', 'scientific_interrupt.py', 'scientific_diagnostics.py', 'scientific_python_help.py', 'scientific_console.py', 'scientific_layer_exports.py', 'scientific_label_history.py', 'scientific_labels.py', 'scientific_label_wire.py', 'scientific_labels_tool.py', 'scientific_annotate.py', 'scientific_view_bindings.py', 'scientific_view_preferences.py', 'scientific_view_wire.py', 'scientific_colormap.py', 'scientific_image_sampling.py', 'scientific_path_appearance.py', 'scientific_surface_appearance.py', 'scientific_inspection.py', 'scientific_point_inspection.py', 'scientific_geometry_selection.py', 'scientific_layer_selection.py', 'scientific_feature_inspection.py', 'scientific_shape_inspection.py', 'scientific_source_wire.py', 'scientific_grid.py', 'scientific_slices.py', 'scientific_text_appearance.py', 'scientific_point_appearance.py', 'scientific_clipping.py', 'scientific_image_plane.py', 'scientific_image_appearance.py', 'scientific_labels_appearance.py', 'scientific_image_histogram.py', 'scientific_geometry_style.py', 'scientific_camera.py', 'scientific_host.py', 'scientific_host_server.py'),
             'gpu_worker': ('semantic_resources.py', 'reader_proof_contract.py')}


class Failed(ValueError):
    pass


# Who hears the worker's startup steps (env_worker.scientific_host.STARTUP_STEPS) while a review opens: set by the owner
# that opens it (scientific_reviews), read when its worker is made, so the steps reach it without another parameter
# through every opener in between.
_STARTUP = ContextVar('scientific-startup', default=None)


@contextmanager
def reporting(starting):
    """Within this block, a worker opened in this thread calls starting(step) as each startup step begins."""
    token = _STARTUP.set(starting)
    try:
        yield
    finally:
        _STARTUP.reset(token)


class ChangeRefused(Failed):
    """The confined worker refused a view change before applying it."""


class ControlFailed(Failed):
    """An admitted callback failed; retain private work for explicit inspection."""


class RestoreRefused(ChangeRefused):
    """The saved presentation was refused before any view changes were applied."""


class CaptureRefused(Failed):
    """No native chunk was sent; private work stays available for correction."""


class ToolFailed(Failed):
    """The tool did not open, in its own words made safe to show (`shown_words`); saved work is unchanged."""


# A path in a tool's words: from the start, a space, a quote or a bracket, never a "Y/X" or "and/or" inside a word.
PATH_WORDS = re.compile(r"(?<![\w.~/])(?:~|\.{1,2})?/[^\s'\"“”‘’`<>()\[\]]*")
SHOWN_CHARACTERS = 300


def shown_words(text):
    """A tool's own words as the window may show them (services/orchestrator/report.py's rule): its first line, with
    no key, token or path and no control character, at most SHOWN_CHARACTERS; '' when nothing is left to say."""
    from .report import redact
    line = next((part.strip() for part in str(text or '').splitlines() if part.strip()), '')
    line = PATH_WORDS.sub('[path]', redact(line))
    line = ''.join(character for character in line if character.isprintable()).strip()
    return line if len(line) <= SHOWN_CHARACTERS else line[:SHOWN_CHARACTERS - 1].rstrip() + '…'


FAILURE_LOG_BYTES = 64 << 10


def keep_failure(work, entry, environ=os.environ):
    """Keep the tail of a worker's stderr when it did not open: its temporary folder goes with the review, so the
    words would go too. Kept with the dated performance logs (logs/tool-failures/<day>/, pruned by make logs-prune),
    keys and tokens removed; on this Mac only, never shown or reported (report.local_details reads them apart)."""
    from datetime import datetime
    from .paths import Paths
    from .report import redact
    try:
        with (Path(work) / 'stderr.log').open('rb') as errors:
            errors.seek(0, os.SEEK_END)
            errors.seek(max(0, errors.tell() - FAILURE_LOG_BYTES))
            words = errors.read().decode('utf-8', 'replace')
        now = datetime.now()
        folder = Paths.from_env(environ).logs / 'tool-failures' / now.strftime('%Y-%m-%d')
        folder.mkdir(parents=True, exist_ok=True)
        kept = folder / f"{now.strftime('%H%M%S')}-{failure_name(entry)}.log"
        kept.write_text(redact(words), encoding='utf-8')
        return kept
    except OSError:
        return None


def failure_name(entry):
    """A catalog entry's key as a file name (a bundle's key holds '/' and '#')."""
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(entry))[:160]


def admit_header(value):
    """Match the actual encoder and unchanged worker header limit before sending."""
    try:
        size=len(json.dumps(value,allow_nan=False).encode())
    except (TypeError,ValueError,RecursionError) as error:
        raise CaptureRefused('Scientific metadata must contain finite JSON values') from error
    if size>MAX_HEADER:
        raise CaptureRefused('The combined scientific metadata exceeds this view’s message allowance. Save a smaller selection; private work is unchanged.')


_retained = {}
_retained_lock = threading.Lock()


def release_worker(worker, stack):
    try:
        worker.close()
    finally:
        if worker.process is not None and worker.process.poll() is None:
            with _retained_lock:
                _retained[worker] = stack
        else:
            stack.close()


def retry_cleanup():
    """Retry retained failed shutdowns; an app owner must call this during shutdown."""
    with _retained_lock:
        retained = list(_retained.items())
    failures = []
    for worker, stack in retained:
        try:
            release_worker(worker, stack)
        except Exception as error:
            failures.append(error)
        finally:
            if worker.process is None or worker.process.poll() is not None:
                with _retained_lock:
                    _retained.pop(worker, None)
    from .scientific_outputs import retry_cleanup as retry_outputs
    from .scientific_file_intake import retry_cleanup as retry_intakes
    return failures + retry_outputs() + retry_intakes()


def _token(value, size):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(size) + '}', value) is not None


def base_digests(edits):
    """The stage request's base chunk digests: a header that admits them and the payload itself.

    Each layer contributes 16 bytes per chunk in grid order when it edits an accepted base, else none.
    """
    import hashlib
    parts = [edit.base_digests() if hasattr(edit, 'base_digests') else None for edit in edits]
    if not any(parts):
        return {}, b''
    body = b''.join(part or b'' for part in parts)
    return {'baseDigests': {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                            'layers': [len(part or b'') // 16 for part in parts]}}, body


def snapshot_storage(work, specification, limits):
    """Choose within existing limits; the original RAM path remains bounded too."""
    from env_worker.native_regions import DTYPES
    from env_worker.scientific_layer_exports import _SnapshotFiles
    from .downloads import SPARE
    usage = worker_resources.fiji_sandbox._work_size(work, limits)
    size = math.prod(specification['shape']) * DTYPES[specification['dtype']]
    files = (size + _SnapshotFiles.FILE_BYTES - 1) // _SnapshotFiles.FILE_BYTES
    fits = (size + usage['bytes'] <= limits.work_bytes
            and files + usage['entries'] + 1 <= limits.work_entries
            and min(size, _SnapshotFiles.FILE_BYTES) <= limits.file_bytes)
    # The worker and retained Project may live on different volumes. A free-space
    # probe is advisory; a write failure before transport also uses bounded RAM.
    try:
        fits = fits and shutil.disk_usage(work).free >= size + SPARE
    except OSError:
        fits = False
    return 'files' if fits else 'memory'


def checked_reply(request, header, body):
    if request['op'] == 'observe':
        if (set(header) != {'ok', 'imageFrame'} or header['ok'] is not True or body
                or (header['imageFrame'] is not None and not _token(header['imageFrame'], 64))):
            raise Failed('The scientific worker returned an invalid observation')
        return
    if request['op'] == 'sequence-begin':
        from env_worker.scientific_sequence import description
        if set(header) != {'ok', 'sequence'} or header['ok'] is not True or body:
            raise Failed('The scientific sequence returned an invalid declaration')
        description(header['sequence'])
        return
    if request['op'] == 'native-panel-render':
        from env_worker import scientific_native_panel_wire as native
        try:
            if set(header) != {'ok', 'nativePanel', 'imageFrame'} or header['ok'] is not True or (header['imageFrame'] is not None and not _token(header['imageFrame'], 64)):
                raise ValueError('Invalid panel reply')
            frame = native.frame(header['nativePanel'], body)
            if (frame['width'], frame['height']) != (request['width'], request['height']):
                raise ValueError('Panel dimensions changed')
        except (ValueError, TypeError, KeyError) as error:
            raise Failed('The scientific worker returned an invalid native panel') from error
        return
    if request['op'] == 'native-panel-change':
        if header != {'ok': True, 'nativeChanged': True} or header.get('nativeChanged') is not True or header.get('ok') is not True or body:
            raise Failed('The native panel returned an invalid change receipt')
        return
    if request['op'] == 'python-assist':
        if set(header) != {'ok', 'assistance'} or body:
            raise Failed('Python assistance returned an invalid response')
        scientific_python_help.reply(header['assistance'], request['mode'], request['expression'])
        return
    if request['op'] == 'layer-inventory':
        optional = {key for key in ('nativePanelAvailable', 'toolDocumentAvailable') if key in header}
        if set(header) != {'ok', 'inventory'} | optional or body or any(type(header[key]) is not bool for key in optional):
            raise Failed('The scientific worker returned an invalid layer inventory')
        scientific_view_wire.inventory(header['inventory'])
        return
    if request['op'] in ('python', 'python-resources'):
        if set(header) != {'ok', 'console'} or body:
            raise Failed('The scientific console returned an invalid response')
        scientific_console.reply(header['console'])
        return
    if request['op'] == 'inspect':
        from env_worker.scientific_inspection import reply
        if set(header) != {'ok','inspection'} or body:
            raise Failed('Scientific inspection returned an invalid response')
        reply(header['inspection'], request)
        return
    if request['op'] == 'pick':
        from env_worker.scientific_host import vector
        point = header.get('position')
        if set(header) != {'ok', 'position'} or body or type(point) is not list or not 2 <= len(point) <= 8:
            raise Failed('The scientific worker returned an invalid position')
        vector(point, len(point))
        return
    expected = {'ok', 'frame'}
    if request['op'] == 'labels':
        scientific_label_wire.receipt(header.get('edit'), request['request'])
        expected.add('edit')
    frame = header.get('frame')
    if (set(header) != expected or type(frame) is not dict
            or frame.get('format') != 'scientific-host-v1' or not _token(frame.get('frame'), 64)
            or frame.get('width') != request['width'] or frame.get('height') != request['height']
            or frame.get('pixelRatio') != 1 or frame.get('derivedDisplay') is not True
            or frame.get('privatePreview') is not True or type(frame.get('dirty')) is not bool
            or len(body) != request['width'] * request['height'] * 4):
        raise Failed('The scientific worker returned an invalid private frame')
    try:
        scientific_view_wire.frame(frame)
    except (TypeError, ValueError, RecursionError) as exc:
        raise Failed('The scientific worker returned invalid view or control metadata') from exc


def _bootstrap(root):
    for package, names in BOOTSTRAP.items():
        target = root / package
        target.mkdir(parents=True)
        (target / '__init__.py').touch()
        for name in names:
            shutil.copy2(SERVICES / package / name, target / name)


class LayerStaging:
    """The worker may stage every bounded chunk once, but cannot publish the edit."""
    def __init__(self, edit):
        from itertools import product
        from env_worker.native_edits import layout
        self.edit = edit
        self.specification = edit.specification()
        spec = self.specification
        *_, grid = layout(spec['shape'], spec['dtype'], spec['axes'], spec['chunks'])
        self._indices = iter(product(*(range(n) for n in grid)))
        self._next = next(self._indices, None)
        self.received = 0

    def refuse(self, header, payload):
        if (not self.received and not payload and type(header) is dict
                and set(header) == {"ok", "code", "error"} and header["ok"] is False
                and header["code"] == "NATIVE_CAPTURE_NOT_STARTED"
                and type(header["error"]) is str and 0 < len(header["error"]) <= 2000):
            raise CaptureRefused(header["error"])
        raise Failed("The scientific export failed after transport began or returned an invalid refusal")

    def admit(self, header, size):
        # Each chunk once, in order: its bytes, or (on a base) a claim that it is the base's chunk,
        # which the edit accepts only for the exact base digest.
        if (header.get('op') not in ('write-native-chunk', 'keep-native-chunk', 'keep-native-base-chunk') or self._next is None
                or header.get('index') != list(self._next)):
            raise Failed('Native staging needs each expected chunk exactly once; workers cannot publish revisions')
        self.edit.admit_message(header, size)

    def answer(self, header, payload):
        self.admit(header, len(payload))
        self.received += 1
        result = self.edit.answer(header, payload)
        self._next = next(self._indices, None)
        return result

    @property
    def complete(self):
        return self._next is None

    def finish(self, header, payload):
        if (self._next is not None or payload or type(header) is not dict
                or header != {'ok': True, 'staged': self.specification['token']} or header.get('ok') is not True):
            raise Failed('The scientific layer export was incomplete; no revision was accepted')


class LayersStaging:
    """One ordered transfer, with no partial publication or new write authority."""
    def __init__(self, edits):
        self.layers = [LayerStaging(edit) for edit in edits]
        self.index = 0
        self.received = 0
        self._advance_complete()

    def _advance_complete(self):
        # Empty components have no transport messages, but remain in the exact
        # ordered completion receipt. Nonempty missing chunks never advance.
        while self.index < len(self.layers) and self.layers[self.index].complete:
            self.index += 1

    def admit(self, header, size):
        if self.index >= len(self.layers):
            raise Failed('The scientific export sent more layers than admitted')
        self.layers[self.index].admit(header, size)

    def answer(self, header, payload):
        self.admit(header, len(payload))
        current = self.layers[self.index]
        result = current.answer(header, payload)
        self.received += 1
        self._advance_complete()
        return result

    def refuse(self, header, payload):
        if not self.received:
            self.layers[0].refuse(header, payload)
        raise Failed('The scientific export failed after transport began')

    def finish(self, header, payload):
        if (self.index != len(self.layers) or payload or type(header) is not dict
                or header != {'ok': True, 'staged': [row.specification['token'] for row in self.layers]}
                or header.get('ok') is not True):
            raise Failed('The scientific layer set was incomplete; no revision was accepted')


class ReviewWorker:
    def __init__(self, prepared, grants, work, bootstrap, environ, *, retained_layers=None, retained_tool=None, initialize_tool=None, recipe_tool=None, files=None, artifacts=None, limits=WORKER_LIMITS):
        self.files = files
        self.artifacts = artifacts
        self.limits = limits
        self._interrupt_lock = threading.Lock()
        self._interrupt_fd = self._python_token = None
        self._interrupt_sent = self._python_ready = False
        self.retained_layers = retained_layers
        self.retained_tool = retained_tool
        self.initialize_tool = initialize_tool
        self.recipe_tool = recipe_tool
        self.prepared, self.grants, self.environ = prepared, grants, environ
        self._lock, self._stopped = threading.Lock(), threading.Event()
        self.process = self.guard = self._errors = None
        self.work = work
        self._closed = False
        self.bootstrap = bootstrap
        self._starting = _STARTUP.get()
        self._step = -1  # the last startup step reported, by its place in STARTUP_STEPS

    def start(self):
        prepared, work, bootstrap = self.prepared, self.work, self.bootstrap
        grants = self.grants
        opening={'op':'open','inputs':grants.specifications(),**({'files':self.files.specifications()} if self.files is not None else {}),**({'retainedLayers':self.retained_layers} if self.retained_layers is not None else {})}
        if self.retained_tool is not None:
            from env_worker.scientific_tool_document import snapshot
            if self.retained_layers is None or self.files is not None:
                raise Failed('A saved tool requires retained layers and no undeclared file inputs')
            opening['retainedTool'] = snapshot(self.retained_tool, self.retained_layers)
        if self.initialize_tool is not None:
            if (type(self.initialize_tool) is not int or self.initialize_tool != 1
                    or self.retained_layers is None or self.retained_tool is not None or self.files is not None):
                raise Failed('Tool initialization requires supported retained layers without a foreign codec or files')
            opening['initializeTool'] = self.initialize_tool
        if self.recipe_tool is not None:
            from env_worker.scientific_tool_document import document
            if self.initialize_tool != 1:
                raise Failed('Recipe settings require explicit tool initialization')
            opening['recipeTool'] = document(self.recipe_tool)
        admit_header(opening)
        self._allowed()
        command = sandbox.wrap([str(prepared.python), '-I', '-u',
                                str(bootstrap / 'env_worker/scientific_host_server.py'),
                                str(prepared.entry), prepared.m.adapter],
                               [prepared.folder, prepared.python_home, bootstrap,
                                *(self.artifacts.read_paths if self.artifacts else ())], [work],
                               gpu=True, children=False)
        environment = sandbox.confine({'PATH': '/usr/bin:/bin', 'QT_QPA_PLATFORM': 'offscreen',
                                       'QT_API': 'pyside6', 'NAPARI_ASYNC': '0',
                                       'NAPARI_CONFIG': str(work / 'napari.yaml'),
                                       'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                                       'VECLIB_MAXIMUM_THREADS': '1', 'NUMBA_NUM_THREADS': '1',
                                       'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                                       **({'MICROMIND_ENTRY_FILES': str(self.artifacts.root)} if self.artifacts else {})}, work)
        self._errors = (work / 'stderr.log').open('wb')
        interrupt_read, self._interrupt_fd = os.pipe()
        try:
            os.set_blocking(self._interrupt_fd, False)
            command.append(str(interrupt_read))
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self._errors, cwd=work, env=environment,
                                            start_new_session=True, close_fds=True, pass_fds=(interrupt_read,))
        finally:
            os.close(interrupt_read)
        self.guard = worker_resources.Guard(self.process, work, self.limits)
        self.guard.start()
        header, body = self._exchange(opening)
        if header != {'ok': True, 'ready': prepared.m.adapter} or body:
            raise Failed('The scientific candidate did not initialize its declared interaction')

    def _tool_name(self):
        name = getattr(getattr(getattr(self, 'prepared', None), 'm', None), 'name', None)
        return name if type(name) is str and name.strip() else 'The tool'

    def _allowed(self):
        owner = catalog.get(self.prepared.m.owner.key)
        if owner is None or not channels.trusted(owner, self.environ):
            raise Failed('Permission for this candidate changed; review its catalog before reopening')
        if self._stopped.is_set():
            raise Failed('This scientific review stopped; reopen the saved Project to start a new review')
        if self.guard is not None:
            self.guard.check()

    def _exchange(self, request, staging=None, *, payload=b''):
        seconds = getattr(staging, 'seconds', CALL_SECONDS)
        with owned_process.scope(seconds, self._stopped.is_set), owned_process.locked(self._lock):
            try:
                self._allowed()
                with owned_process.watching(self.process, seconds, 'scientific review'):
                    write_message(self.process.stdin, request, payload)
                    while True:
                        def admit(header, size):
                            if type(header) is not dict:
                                raise Failed('The scientific worker returned an invalid message')
                            if header.get('op') in ('write-native-chunk', 'keep-native-chunk', 'keep-native-base-chunk', 'commit-native-edit', 'write-family-part'):
                                if staging is None:
                                    raise Failed('This scientific request has no native write grant')
                                staging.admit(header, size)
                            elif header.get('op') in ('read-native-region','read-source-file','read-source-directory'):
                                if size:
                                    raise Failed('Native input requests cannot contain data')
                            elif size:
                                frame = header.get('nativePanel' if request['op'] == 'native-panel-render' else 'frame')
                                if (request['op'] not in ('render', 'change', 'navigate', 'labels', 'native-panel-render', 'sequence-render') or type(frame) is not dict
                                        or frame.get('width') != request['width']
                                        or frame.get('height') != request['height']
                                        or size != request['width'] * request['height'] * 4):
                                    raise Failed('The scientific worker returned an unexpected viewport')
                        header, body = read_message(self.process.stdout, max_header=MAX_HEADER,
                                                    max_payload=MAX_FRAME_BYTES, admit=admit)
                        if header.get('op') == 'starting':
                            from env_worker.scientific_host import STARTUP_STEPS
                            step = header.get('step')
                            if (body or request['op'] != 'open' or set(header) != {'op', 'step'}
                                    or step not in STARTUP_STEPS or STARTUP_STEPS.index(step) <= self._step):
                                raise Failed('The scientific worker reported an invalid startup step')
                            self._step = STARTUP_STEPS.index(step)
                            if self._starting is not None:
                                self._starting(step)
                            continue
                        if header.get('op') == 'python-started':
                            if (body or request['op'] not in ('python','python-resources')
                                    or header != {'op':'python-started','token':request['interruptToken']}):
                                raise Failed('The Python worker returned an invalid execution receipt')
                            with self._interrupt_lock:
                                if self._python_ready:
                                    raise Failed('The Python worker repeated its execution receipt')
                                self._python_ready = True
                            continue
                        if header.get('op') in ('write-native-chunk', 'keep-native-chunk', 'keep-native-base-chunk', 'write-family-part') and staging is not None:
                            self._allowed()
                            reply, payload = staging.answer(header, body)
                            write_message(self.process.stdin, reply, payload)
                            continue
                        if header.get('op') not in ('read-native-region','read-source-file','read-source-directory'):
                            break
                        self._allowed()
                        obsolete = getattr(self, '_navigation_superseded', lambda: False)
                        if obsolete() and header.get('view') is True:
                            write_message(self.process.stdin, {'ok': False, 'code': 'SCIENTIFIC_VIEW_SUPERSEDED'})
                            continue
                        if header['op'] in ('read-source-file','read-source-directory'):
                            if self.files is None:raise Failed('This worker has no selected source-file grants')
                            reply,payload=self.files.answer(header,len(body))
                        else:reply, payload = self.grants.answer(header, len(body))
                        if obsolete() and header.get('view') is True:
                            reply, payload = {'ok': False, 'code': 'SCIENTIFIC_VIEW_SUPERSEDED'}, b''
                        write_message(self.process.stdin, reply, payload)
                self._allowed()
                if (request.get('op') == 'navigate' and header == {'ok': False, 'code': 'SCIENTIFIC_VIEW_SUPERSEDED'} and not body):
                    return header, body
                if header.get('ok') is not True:
                    if (request.get('op') == 'open' and not body
                            and set(header) == {'ok', 'code', 'error', 'next'} and header['ok'] is False
                            and header['code'] == 'SCIENTIFIC_INPUT_REFUSED'):
                        try:
                            refusal = InputRefused(header['error'], header['next'])
                        except ValueError:
                            raise Failed('The scientific worker returned invalid input recovery guidance') from None
                        raise refusal
                    if ((request.get('op') in ('observe', 'native-panel-render', 'native-panel-change')
                            or (request.get('op') == 'change' and request.get('request', {}).get('operation') == 'controls'))
                            and not body and set(header) == {'ok', 'code', 'error'} and header['ok'] is False
                            and header['code'] == 'SCIENTIFIC_CONTROL_FAILED'
                            and type(header['error']) is str and 0 < len(header['error']) <= 2000):
                        raise ControlFailed(header['error'])
                    if (request.get('op') in ('observe','change','navigate','inspect','native-panel-render','native-panel-change') and not body
                            and set(header) == {'ok', 'code', 'error'} and header['ok'] is False
                            and header['code'] == 'SCIENTIFIC_CHANGE_NOT_APPLIED'
                            and type(header['error']) is str and 0 < len(header['error']) <= 2000):
                        raise ChangeRefused(header['error'])
                    if (request.get('op') == 'change' and request.get('request', {}).get('operation') == 'restore-view'
                            and not body and set(header) == {'ok', 'code', 'error'} and header['ok'] is False
                            and header['code'] == 'VIEW_RESTORE_NOT_APPLIED'
                            and type(header['error']) is str and 0 < len(header['error']) <= 2000):
                        raise RestoreRefused(header['error'])
                    if request.get('op') in ('describe-output', 'describe-tool-layer') and not body and type(header) is dict and set(header) == {'ok','code','error'} and header['ok'] is False and header['code'] == 'NATIVE_CAPTURE_NOT_STARTED' and type(header['error']) is str and 0 < len(header['error']) <= 2000:
                        raise CaptureRefused(header['error'])
                    if (request.get('op') in ('sequence-begin', 'sequence-render') and not body
                            and set(header) == {'ok', 'code', 'error'} and header['ok'] is False
                            and header['code'] == 'SCIENTIFIC_SEQUENCE_FAILED'
                            and type(header['error']) is str and 0 < len(header['error']) <= 2000):
                        raise Failed('Saved movie rendering failed. The saved tool is unchanged. ' + header['error'])
                    if (request.get('op') == 'open' and not body and set(header) == {'ok', 'error'}
                            and type(header['error']) is str and (words := shown_words(header['error']))):
                        raise ToolFailed(f'{self._tool_name()} did not open: “{words}”')
                    if staging is not None:
                        staging.refuse(header, body)
                    raise Failed('The scientific interaction failed; reopen its private review after correcting the integration')
                return header, body
            except (CaptureRefused, ChangeRefused, ControlFailed):
                raise
            except BaseException:
                self._stopped.set()
                self.grants.close()
                if getattr(self,'files',None) is not None:self.files.close()
                if self.guard is not None:
                    self.guard.close()
                raise

    def family_drafts(self):
        """The tables, embeddings and artifacts this session's Python or plugins drafted (shape-checked only)."""
        from .scientific_family_saves import drafts
        header, body = self._exchange({'op': 'family-drafts'})
        try:
            if body or set(header) != {'ok', 'drafts'}:
                raise ValueError('Invalid draft listing')
            return drafts(header['drafts'])
        except Exception:
            self.close(); raise

    def stage_families(self, names, staging, *, unchanged=(), base=()):
        """Receive the named drafts' parts; each accepted input they name must still be unchanged in its layer."""
        import hashlib
        request = {'op': 'stage-families', 'names': list(names), 'unchanged': list(unchanged)}
        body = b''.join(base)
        if body:
            request['baseDigests'] = {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                                      'layers': [len(part) // 16 for part in base]}
        try:
            header, payload = self._exchange(request, staging, payload=body)
            staging.finish(header, payload)
        except CaptureRefused:
            staging.cancel(); raise
        except BaseException:
            staging.cancel(); self.close(); raise

    def describe_output(self, layer, *, tool=False):
        from env_worker.scientific_output_wire import metadata
        if not _token(layer,32): raise Failed('Choose a current native output layer')
        header,body = self._exchange({'op':'describe-tool-layer' if tool else 'describe-output','layer':layer})
        try:
            if body or set(header) != {'ok','output'}: raise ValueError('Invalid output metadata')
            return metadata(header['output'])
        except Exception:
            self.close(); raise

    def admit_streamed_output(self, info):
        # A worker declaration alone grants no larger publication. Every source
        # must still be in this owner's live immutable regional input grants.
        from env_worker.scientific_output_wire import metadata
        info = metadata(info)
        allowed = [spec['scientific']['reference'] for spec in self.grants.specifications().values()]
        if self.files is not None:
            allowed.extend(spec['reference'] for spec in self.files.specifications().values() if spec.get('regional') is True)
        if not info.get('regionalSources') or any(ref not in allowed for ref in info['regionalSources']):
            raise Failed('The streamed output no longer names its admitted immutable source')
        self._allowed()

    def stage_output(self, layer, info, meaning, edit):
        from env_worker.native_regions import DTYPES
        from env_worker.scientific_output_wire import compatible
        compatible(info,meaning)
        if not _token(layer,32): raise Failed('Choose a current native output layer')
        staging = LayerStaging(edit)
        if 'regionalSources' in info:
            self.admit_streamed_output(info)
            from env_worker.scientific_output_wire import transfer_seconds
            staging.seconds = transfer_seconds(math.prod(info['shape'])*DTYPES[info['dtype']])
        try:
            header,payload = self._exchange({'op':'stage-output','layer':layer,'metadata':info,'meaning':meaning,
                'edit':staging.specification,'snapshot':snapshot_storage(self.work,staging.specification,WORKER_LIMITS)},staging)
            staging.finish(header,payload)
        except CaptureRefused:
            edit.cancel(); raise
        except BaseException:
            edit.cancel(); self.close(); raise

    def stage_output_components(self, layer, info, meaning, grants):
        from env_worker.scientific_output_wire import compatible, COMPOUND
        from env_worker.native_regions import DTYPES
        compatible(info,meaning)
        if not _token(layer,32) or info['type'] not in COMPOUND or set(grants) != set(info['components']):
            raise Failed('Choose current native geometry and its complete component grants')
        names = sorted(grants)
        staging = LayersStaging([grants[name] for name in names])
        size = sum(math.prod(row.specification['shape'])*DTYPES[row.specification['dtype']] for row in staging.layers)
        snapshot = snapshot_storage(self.work, {'shape':[size+len(names)*(1 << 20)],'dtype':'|u1'},WORKER_LIMITS)
        try:
            header,payload = self._exchange({'op':'stage-output-components','layer':layer,'metadata':info,'meaning':meaning,
                'components':[{'name':name,'edit':row.specification} for name,row in zip(names,staging.layers)],
                'snapshot':snapshot}, staging)
            staging.finish(header,payload)
        except CaptureRefused:
            for grant in grants.values(): grant.cancel()
            raise
        except BaseException:
            for grant in grants.values(): grant.cancel()
            self.close(); raise

    def stage_layer(self, frame, layer, input, edit):
        if not _token(frame, 64):
            raise Failed('Native staging needs the exact current frame')
        self._stage({'op': 'stage-layer', 'frame': frame}, layer, input, edit)

    def stage_outputs(self, rows, *, tool=False):
        from env_worker.scientific_output_wire import compatible, selections
        from env_worker.native_regions import DTYPES
        one_pyramid = len(rows)==1 and 'levels' in rows[0]['metadata']
        selections([{'layer':row['layer'],'meaning':row['meaning']} for row in rows], minimum=1 if tool or one_pyramid else 2)
        grants=[grant for row in rows for _,grant in sorted(row['grants'].items())]
        staging=LayersStaging(grants)
        size=sum(math.prod(grant.specification()['shape'])*DTYPES[grant.specification()['dtype']] for grant in grants)
        snapshot=snapshot_storage(self.work,{'shape':[size+len(grants)*(1 << 20)],'dtype':'|u1'},WORKER_LIMITS)
        layers=[]
        for row in rows:
            compatible(row['metadata'],row['meaning'])
            layers.append({'layer':row['layer'],'metadata':row['metadata'],'meaning':row['meaning'],
                           'components':[{'name':name,'edit':grant.specification()} for name,grant in sorted(row['grants'].items())]})
            if tool:
                from env_worker.scientific_output_wire import COMPOUND
                layers[-1]['input'] = row['names']['vertices' if row['metadata']['type'] in COMPOUND else 'output']
        if not tool and all('regionalSources' in row['metadata'] for row in rows):
            from env_worker.scientific_output_wire import transfer_seconds
            for row in rows:
                self.admit_streamed_output(row['metadata'])
            staging.seconds = transfer_seconds(size)
        request={'op':'stage-tool' if tool else 'stage-outputs','layers':layers,'snapshot':snapshot}
        admit_header(request)
        try:
            header,payload=self._exchange(request,staging)
            saved_tool = None
            if tool:
                from env_worker.scientific_tool_document import snapshot as tool_snapshot
                if type(header) is not dict or set(header) != {'ok', 'staged', 'tool'}:
                    raise Failed('The tool snapshot is incomplete; no scientific document was accepted')
                saved_tool = tool_snapshot(header['tool'], layers)
                header = {key: value for key, value in header.items() if key != 'tool'}
            staging.finish(header,payload)
            return saved_tool
        except CaptureRefused:
            for grant in grants: grant.cancel()
            raise
        except BaseException:
            for grant in grants: grant.cancel()
            self.close(); raise

    def stage_resource(self, layer, input, edit):
        self._stage({'op': 'stage-resource'}, layer, input, edit)

    def stage_resources(self, layers):
        from env_worker.native_regions import IDENTITY, DTYPES
        from .scientific_arrays import MAX_NATIVE_COMPONENTS, MAX_LOGICAL_BYTES
        if type(layers) is not list or not 1 < len(layers) <= MAX_NATIVE_COMPONENTS:
            raise Failed('Choose a bounded set of editable scientific layers')
        if any(not _token(layer, 32) or type(input) is not str or not IDENTITY.fullmatch(input)
               for layer, input, _ in layers):
            raise Failed('Native staging needs declared editable layer inputs')
        if len({layer for layer, _, _ in layers}) != len(layers) or len({input for _, input, _ in layers}) != len(layers):
            raise Failed('Choose each scientific layer and input once')
        staging = LayersStaging([edit for _, _, edit in layers])
        specs = [row.specification for row in staging.layers]
        size = sum(math.prod(spec['shape']) * DTYPES[spec['dtype']] for spec in specs)
        if size > MAX_LOGICAL_BYTES:
            raise Failed('The scientific snapshot exceeds the admitted native data allowance')
        # Each layer owns a directory and may need one partly filled final file.
        # Reserve that overhead as well as the aggregate bytes when choosing disk.
        snapshot = snapshot_storage(self.work, {'shape': [size + len(layers) * (1 << 20)], 'dtype': '|u1'}, WORKER_LIMITS)
        try:
            base = base_digests([edit for _, _, edit in layers])
            request = {'op': 'stage-resources', 'snapshot': snapshot, **base[0],
                       'layers': [{'layer': layer, 'input': input, 'edit': spec}
                                  for (layer, input, _), spec in zip(layers, specs)]}
            header, payload = self._exchange(request, staging, payload=base[1])
            staging.finish(header, payload)
        except CaptureRefused:
            for _, _, edit in layers: edit.cancel()
            raise
        except BaseException:
            for _, _, edit in layers: edit.cancel()
            self.close()
            raise

    def _stage(self, request, layer, input, edit):
        from env_worker.native_regions import IDENTITY
        if not _token(layer, 32) or type(input) is not str or IDENTITY.fullmatch(input) is None:
            raise Failed('Native staging needs a declared editable layer input')
        staging = LayerStaging(edit)
        try:
            snapshot = snapshot_storage(self.work, staging.specification, WORKER_LIMITS)
            base = base_digests([edit])
            header, payload = self._exchange({**request, 'layer': layer, 'input': input,
                                              'edit': staging.specification, 'snapshot': snapshot, **base[0]},
                                             staging, payload=base[1])
            staging.finish(header, payload)
        except CaptureRefused:
            edit.cancel()
            raise
        except BaseException:
            edit.cancel()
            self.close()
            raise

    def navigate(self, request, navigation):
        """Drain superseded native reads without terminating this private worker."""
        ViewportLimits(max_side=1024, max_working_bytes=32 << 20).check(request.get('width'), request.get('height'))
        first = True
        with owned_process.scope(CALL_SECONDS):
            try:
                while True:
                    sequence, value = navigation.target()
                    self._navigation_superseded = lambda: navigation.superseded(sequence)
                    outgoing = {**request, 'op': 'navigate', 'navigation': navigation.identity, 'first': first,
                                'request': {**request['request'], 'value': value}}
                    header, pixels = self._exchange(outgoing)
                    first = False
                    if header.get('ok') is not True:
                        continue
                    try:
                        checked_reply(request, header, pixels)
                    except Exception:
                        self.close()
                        raise
                    if navigation.finish(sequence):
                        return {**header, 'navigation': {'id': navigation.identity, 'sequence': sequence, 'state': 'rendered'}}, pixels
            finally:
                self._navigation_superseded = lambda: False

    def request(self, request):
        if type(request) is not dict or request.get('op') not in ('render', 'change', 'pick', 'inspect', 'python', 'python-resources', 'python-assist', 'labels', 'layer-inventory', 'observe', 'native-panel-render', 'native-panel-change', 'sequence-begin', 'sequence-render'):
            raise Failed('Choose a declared scientific view interaction')
        if request['op'] == 'sequence-begin' and set(request) != {'op'}:
            raise Failed('Begin a declared saved sequence without additional fields')
        if request['op'] == 'sequence-render' and (set(request) != {'op', 'index', 'width', 'height'}
                or type(request['index']) is not int or not 0 <= request['index'] < 4096):
            raise Failed('Choose a bounded sequence index and viewport')
        python = request['op'] in ('python', 'python-resources')
        control = dict(request)
        if python:
            scientific_console.source(control.pop('source', None))
        try:
            encoded = json.dumps(control, allow_nan=False).encode()
        except (TypeError, ValueError, RecursionError) as exc:
            raise Failed('Scientific interaction values must be finite JSON') from exc
        if len(encoded) > MAX_HEADER:
            raise Failed('Scientific interaction exceeds its allowance')
        if request['op'] == 'native-panel-render' and set(request) != {'op', 'width', 'height'}:
            raise Failed('Native panel rendering needs its exact viewport dimensions')
        if request['op'] == 'native-panel-change':
            value = request.get('request')
            if (set(request) != {'op', 'request'} or type(value) is not dict
                    or set(value) != {'panel', 'frame', 'op', 'value'}
                    or not _token(value['panel'], 32) or not _token(value['frame'], 64)):
                raise Failed('Native interaction requires the exact panel and rendered frame')
        if request['op'] in ('layer-inventory', 'observe') and set(request) != {'op'}:
            raise Failed('Layer inventory uses only the admitted session inputs')
        if request['op'] == 'python-assist':
            if set(request) != {'op', 'mode', 'expression'}:
                raise Failed('Python assistance uses only the current session namespace')
            scientific_python_help.query(request['mode'], request['expression'])
        if request['op'] == 'python':
            if set(request) != {'op', 'frame', 'source'} or not _token(request['frame'], 64):
                raise Failed('Python execution needs the current private scientific frame')
            scientific_console.source(request['source'])
        if request['op'] == 'python-resources':
            if set(request) != {'op', 'source'}:
                raise Failed('Resource Python uses only the admitted session inputs')
            scientific_console.source(request['source'])
        if request['op'] == 'inspect':
            from env_worker.scientific_inspection import command
            command(request)
        if request['op'] == 'labels':
            scientific_label_wire.command(request)
        if request['op'] in ('render', 'change', 'labels', 'native-panel-render', 'sequence-render'):
            ViewportLimits(max_side=1024, max_working_bytes=32 << 20).check(request.get('width'), request.get('height'))
        outgoing = json.loads(encoded)
        payload = b''
        if python:
            with self._interrupt_lock:
                self._python_token = secrets.token_hex(16)
                self._interrupt_sent = self._python_ready = False
                outgoing['interruptToken'] = self._python_token
        try:
            if python:
                outgoing['source'] = request['source']
                outgoing, payload = scientific_source_wire.encode(outgoing)
            header, body = self._exchange(outgoing, payload=payload)
        finally:
            if python:
                with self._interrupt_lock:
                    self._python_token = None
                    self._python_ready = False
        try:
            checked_reply(request, header, body)
        except Exception:
            self.close()
            raise
        return header, body

    def reader_observation(self):
        """Private proof producer only; not a browser interaction or qualification API."""
        from gpu_worker.reader_proof_contract import observation
        header, body = self._exchange({'op': 'reader-proof'})
        if body or set(header) != {'ok', 'readerProof'} or header['ok'] is not True:
            raise Failed('The scientific reader did not return its bounded data observation')
        return observation(header['readerProof'])

    def interrupt_python(self):
        """Do not take the exchange lock: the running cell owns it until it replies."""
        with self._interrupt_lock:
            if (self._stopped.is_set() or not self._python_ready or self._python_token is None or self._interrupt_fd is None
                    or self.process is None or self.process.poll() is not None):
                return False
            if self._interrupt_sent:
                return True
            token = self._python_token.encode('ascii')
            if os.write(self._interrupt_fd, token) != len(token):
                raise Failed('The Python interrupt could not be sent; its session has not been stopped')
            self._interrupt_sent = True
            self.process.send_signal(signal.SIGUSR1)
            return True

    def close(self):
        self._stopped.set()
        with self._interrupt_lock:
            if self._interrupt_fd is not None:
                os.close(self._interrupt_fd)
                self._interrupt_fd = None
            self._python_token = None
        self.grants.close()
        if getattr(self,'files',None) is not None:self.files.close()
        errors = []
        if self.guard is not None:
            try:
                self.guard.close()
            except Exception as error:
                errors.append(error)
        if self.process is not None and self.process.poll() is None:
            try:
                owned_process.terminate(self.process)
            except Exception as error:
                errors.append(error)
        if self.process is not None and self.process.poll() is None:
            raise Failed('Scientific worker could not be reaped; its runtime and private files remain reserved')
        with self._lock:
            if self._closed:
                return
            self._closed = True
            streams = [self._errors]
            if self.process is not None:
                streams.extend([self.process.stdin, self.process.stdout])
            for stream in streams:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception as error:
                        errors.append(error)
        if self.guard is not None:
            try:
                self.guard.check()
            except Exception as error:
                errors.append(error)
        if errors:
            raise Failed('Scientific review cleanup reported a failure') from errors[0]


@contextmanager
def review(key, generation, sources, environ=os.environ, *, retained_layers=None, retained_tool=None, initialize_tool=None, recipe_tool=None, files=None):
    """Private, bounded lifecycle; sources have already passed parent Project authorization.

Returned frames are derived RGBA previews. The caller must bound retained responses;
this first boundary admits one active frame, not a movie or saved scientific output.
"""
    if not sandbox.available():
        raise Failed('Scientific review requires macOS confinement; no unrestricted fallback is available')
    stack, worker, primary = ExitStack(), None, None
    try:
        prepared = stack.enter_context(prepared_bundles.prepared_member(key, generation, environ))
        if prepared.m.bridge != 'scientific-host-v1':
            raise Failed('This operation has no declared scientific-host integration')
        artifacts = stack.enter_context(prepared_artifacts.open_files(prepared.m.owner, environ))
        limits = replace(WORKER_LIMITS, memory_bytes=prepared.m.resources.get('memory_mb', 1024) << 20)
        stack.enter_context(jobs.MEMORY.reserve((limits.memory_bytes + (32 << 20)) / (1 << 20)))
        from .source_files import SourceFileInputs
        reader=prepared.m.reader
        regional=(reader['input'],) if reader.get('access')=='regional' else ()
        file_grants=SourceFileInputs(files,regional=regional) if files else None
        if file_grants is not None:stack.callback(file_grants.close)
        grants = NativeRegionInputs(sources,allow_empty=file_grants is not None)
        stack.callback(grants.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='hooke-scientific-review-')))
        work, bootstrap = root / 'work', root / 'bootstrap'
        work.mkdir()
        _bootstrap(bootstrap)
        worker = ReviewWorker(prepared, grants, work, bootstrap, environ, retained_layers=retained_layers,
                              retained_tool=retained_tool, initialize_tool=initialize_tool, recipe_tool=recipe_tool, files=file_grants, artifacts=artifacts, limits=limits)
        from .scientific_saves import reserve_workspace
        worker.save_workspace = stack.enter_context(reserve_workspace())
        try:
            worker.start()
        except Failed:
            # Its own traceback lands in stderr as it exits; wait briefly, then keep it beyond this temporary folder.
            if worker.process is not None:
                with suppress(subprocess.TimeoutExpired):
                    worker.process.wait(timeout=2)
            keep_failure(work, prepared.m.owner.key, environ)
            raise
        yield worker
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            if worker is None:
                stack.close()
            else:
                release_worker(worker, stack)
        except Exception as cleanup:
            if primary is None:
                raise
            primary.add_note(f'Scientific review cleanup also failed: {cleanup}')
