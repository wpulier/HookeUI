"""One explicitly admitted layer snapshot. Retries publish its staged bytes only."""
from contextlib import contextmanager
import math
import shutil
import threading

from . import jobs
from .downloads import SPARE
from .scientific_arrays import SAVE_WORKING_BYTES
from .scientific_edits import ResourceEdits
from .scientific_history import CELL
from .scientific_host import CaptureRefused
from .scientific_projects import Conflict, InvalidProject
from env_worker.native_regions import DTYPES


class SaveUnavailable(RuntimeError):
    pass


class SaveConflict(SaveUnavailable):
    pass


class SaveNotStarted(SaveUnavailable):
    pass


class SaveWorkspace:
    """One prepaid parent workspace, shared only by serial saves of its worker."""
    def __init__(self):
        self._lock = threading.Lock()
        self._active = True

    @contextmanager
    def claim(self):
        if not self._lock.acquire(blocking=False):
            raise SaveUnavailable('Wait for the current scientific save to finish before saving again')
        try:
            yield self._active
        finally:
            self._lock.release()

    def close(self):
        with self._lock:
            self._active = False


@contextmanager
def reserve_workspace():
    # Called before the owned worker starts. Keep this reservation in its cleanup
    # stack, including when process reaping fails; captured saves cannot mint credit.
    with jobs.MEMORY.reserve(SAVE_WORKING_BYTES / (1 << 20)):
        workspace = SaveWorkspace()
        try:
            yield workspace
        finally:
            workspace.close()


@contextmanager
def admission(workspace=None):
    if workspace is not None:
        with workspace.claim() as reserved:
            if reserved:
                # The session already reserved this peak. Still refuse newly
                # observed system pressure; do not subtract that same peak twice.
                from . import machine
                from .loads import MARGIN_MB
                free = machine.free_mb()
                if free is None or free < SAVE_WORKING_BYTES / (1 << 20) + MARGIN_MB:
                    raise SaveUnavailable('There is not enough free memory to use the reserved save workspace. '
                                          'Keep this session open, wait for other work to finish, then retry saving')
                yield
                return
    try:
        with jobs.MEMORY.reserve(SAVE_WORKING_BYTES / (1 << 20)):
            yield
    except jobs.Failed as error:
        raise SaveUnavailable('There is not enough memory to save array changes now. Wait for other work to finish, then retry') from error


class LayerSave:
    def __init__(self, store, saved, identity, frame, layer_id, *, producer=None, input=None):
        store = store.for_session(saved.session, write=True)
        from .scientific_provenance import frozen
        self.producer = frozen(producer)
        if type(identity) is not str or CELL.fullmatch(identity) is None:
            raise InvalidProject('Saving array changes needs its original request identity')
        if frame is not None:
            layer = next((row for row in frame['layers'] if row['id'] == layer_id), None)
            if layer is None or not layer.get('editableInput'):
                raise InvalidProject('This integration has not bound that layer to an editable scientific input')
            input = layer['editableInput']
        from env_worker.native_regions import IDENTITY
        if type(input) is not str or IDENTITY.fullmatch(input) is None:
            raise InvalidProject('Choose a declared editable scientific input')
        spec = saved.document['state'].get('scientificView')
        reference = spec.get('inputs', {}).get(input) if type(spec) is dict else None
        if reference is None or reference not in saved.document['resources']:
            raise InvalidProject('This editable layer has no exact input in the originating saved revision')
        source = store.array_source(saved.id, saved.session, saved.revision, reference)
        self.bytes = math.prod(source.shape) * DTYPES[source.dtype]
        self.store, self.saved, self.identity = store, saved, identity
        self.layer, self.input, self.worker_frame = layer_id, input, frame['frame'] if frame is not None else None
        self.owner = ResourceEdits(store, saved.id, saved.session, saved.revision, reference, layer=layer_id)
        self.save_workspace = None
        self.edit = self.receipt = None
        self.failure = None
        self.latest = None
        self.copy_attempt = self.copy_receipt = None
        self.granular = self.onto = None  # set by the session: which layers a newer revision kept, where it saved

    def _conflict(self):
        self.failure = 'conflict'
        try:
            latest = self.store.read(self.saved.id, self.saved.session)
        except (OSError, InvalidProject):
            return
        self.latest = {'day': latest.session, 'id': latest.id, 'version': latest.revision, 'picture': None}

    def capture(self, worker):
        self.save_workspace = getattr(worker, 'save_workspace', None)
        if self.edit is not None:
            raise InvalidProject('This array save was already captured; retry its publication without running the worker again')
        with admission(self.save_workspace):
            if shutil.disk_usage(self.store.root).free < self.bytes + SAVE_WORKING_BYTES + SPARE:
                raise InvalidProject('There is not enough free space to stage these array changes; free space and try again')
            try:
                edit = self.owner.begin(max_written_bytes=self.bytes, producer=self.producer)
            except Conflict as error:
                self._conflict()
                raise SaveConflict('A newer revision was accepted. Your edits remain in this private view; open the newer saved revision to inspect it') from error
            try:
                if self.worker_frame is None:
                    worker.stage_resource(self.layer, self.input, edit)
                else:
                    worker.stage_layer(self.worker_frame, self.layer, self.input, edit)
            except CaptureRefused as error:
                edit.cancel()
                raise SaveNotStarted(f'Nothing was saved. Private changes are still available. Correct the layer or its declared input, then save again. {error}') from error
            except Exception as error:
                edit.cancel()
                raise RuntimeError("Scientific layer export failed; the private session stopped and accepted data stayed unchanged") from error
            except BaseException:
                edit.cancel()
                raise
            self.edit = edit

    def publish(self, stopping=lambda: False):
        if self.receipt is not None:
            return self.status()
        if self.failure == 'conflict':
            raise SaveConflict('These array changes are not available for save-only retry; inspect the newer saved revision')
        if self.edit is None or self.failure:
            raise SaveUnavailable('These array changes are not available for save-only retry; reopen accepted Data and inspect the newer revision')
        if stopping():
            self.close()
            raise SaveUnavailable('Saving was stopped before publication. The accepted revision stays unchanged')
        with admission(self.save_workspace):
            try:
                self.receipt = self.edit.commit()
            except Conflict as error:
                self._conflict()
                raise SaveConflict('Another scientific edit was accepted first. Its data was kept; these changes were not accepted') from error
            except (OSError, InvalidProject) as error:
                if not self.edit.retryable:
                    self.failure = 'failed'
                    raise SaveUnavailable('These staged changes cannot be saved. The accepted data stays available; stop this private session and reopen accepted Data') from error
                raise SaveUnavailable('Array changes could not be confirmed saved. Retry saving these same changes; do not rerun Python or the tool') from error
        return self.status()

    def copy(self, worker, frame, stopping=lambda: False):
        self.save_workspace = getattr(worker, 'save_workspace', None)
        if self.copy_receipt is not None:
            return self.status()
        if self.failure != 'conflict':
            raise SaveUnavailable('Save a separate document only after a confirmed revision conflict')
        with admission(self.save_workspace):
            if self.edit is None:
                if frame is None and self.worker_frame is not None:
                    raise InvalidProject('Refresh and inspect the private view before capturing its changes')
                if shutil.disk_usage(self.store.root).free < self.bytes + SAVE_WORKING_BYTES + SPARE:
                    raise InvalidProject('There is not enough space to capture these private changes; free space and retry')
                edit = self.owner.snapshot(max_written_bytes=self.bytes, producer=self.producer)
                try:
                    if self.worker_frame is None:
                        worker.stage_resource(self.layer, self.input, edit)
                    else:
                        worker.stage_layer(frame['frame'], self.layer, self.input, edit)
                except CaptureRefused as error:
                    edit.cancel()
                    raise SaveNotStarted(f'Copy capture did not start; private work is still available. Correct the layer, save again, then choose a separate document. {error}') from error
                except BaseException:
                    edit.cancel()
                    raise
                self.edit = edit
            try:
                if self.copy_attempt is None:
                    self.copy_attempt = self.store.copy_candidate(self.saved, self.edit.prepared())
                self.copy_receipt = self.copy_attempt.publish(stopping)
            except (OSError, InvalidProject) as error:
                raise SaveUnavailable(f'The separate copy is not confirmed saved. Retry Save as a separate document to use the same captured values. {error}') from error
        return self.status()

    def status(self):
        accepted = self.copy_receipt or self.receipt
        reference = None if accepted is None else {'day': accepted.session, 'id': accepted.id,
                                                     'version': accepted.revision, 'picture': None}
        return {'request': self.identity, 'state': 'copied' if self.copy_receipt else 'saved' if reference else self.failure or 'staged',
                'resource': reference, 'layer': self.layer, **({'latest': dict(self.latest)} if self.latest and not reference else {}),
                **({'copyCaptured': self.edit is not None, 'copyNeedsPreview': self.edit is None and self.worker_frame is not None} if self.failure == 'conflict' and not reference else {}),
                **({'conflicts': [dict(row) for row in self.granular]} if self.granular and self.failure == 'conflict' and not reference else {}),
                **({'onto': dict(self.onto)} if self.onto and self.receipt is not None and not self.copy_receipt else {})}

    def close(self):
        try:
            if self.copy_attempt is not None:
                self.copy_attempt.close()
        finally:
            try:
                if self.edit is not None:
                    self.edit.cancel()
            finally:
                self.owner.close()
