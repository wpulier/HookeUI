"""One capture and one atomic acceptance for a set of declared editable layers."""
import secrets
import shutil

from .downloads import SPARE
from .scientific_arrays import SAVE_WORKING_BYTES, MAX_NATIVE_COMPONENTS
from .scientific_host import CaptureRefused
from .scientific_projects import Conflict, InvalidProject
from .scientific_publication import ArrayPublication
from .scientific_saves import LayerSave, SaveConflict, SaveNotStarted, SaveUnavailable, admission


class LayersSave:
    def __init__(self, store, saved, identity, layers, *, producer=None):
        store = store.for_session(saved.session, write=True)
        if (type(layers) is not list or not 1 < len(layers) <= MAX_NATIVE_COMPONENTS
                or any(type(row) is not dict or set(row) != {'layer', 'input'} for row in layers)):
            raise InvalidProject('Choose the editable layers to save together')
        if (any(type(row['layer']) is not str or type(row['input']) is not str for row in layers)
                or len({row['layer'] for row in layers}) != len(layers)
                or len({row['input'] for row in layers}) != len(layers)):
            raise InvalidProject('Choose each editable layer and input once')
        self.layers = []
        try:
            for row in layers:
                self.layers.append(LayerSave(store, saved, identity, None, row['layer'], input=row['input'], producer=producer))
            if len({row.owner.reference['component'] for row in self.layers}) != len(layers):
                raise InvalidProject('Choose each scientific component once')
        except BaseException:
            for row in self.layers: row.close()
            raise
        self.store, self.saved, self.identity = store, saved, identity
        self.transaction = secrets.token_hex(32)
        self.edit = self.receipt = self.copy_receipt = self.copy_attempt = None
        self._publication = None
        self.save_workspace = None
        self.failure = self.latest = None
        self.worker_frame = None
        self.granular = self.onto = None  # set by the session: which layers a newer revision kept, where it saved

    def _conflict(self):
        self.failure = 'conflict'
        try:
            latest = self.store.read(self.saved.id, self.saved.session)
        except (OSError, InvalidProject):
            return
        self.latest = {'day': latest.session, 'id': latest.id, 'version': latest.revision, 'picture': None}

    def capture(self, worker, *, for_copy=False):
        self.save_workspace = getattr(worker, 'save_workspace', None)
        if self.edit is not None:
            raise InvalidProject('These layers are already captured; retry saving the same values')
        with admission(self.save_workspace):
            size = sum(row.bytes for row in self.layers)
            if shutil.disk_usage(self.store.root).free < size + SAVE_WORKING_BYTES + SPARE:
                raise InvalidProject('There is not enough space to stage all these layers; free space and retry')
            if not for_copy and self.store.read(self.saved.id, self.saved.session).revision != self.saved.revision:
                self._conflict()
                raise SaveConflict('A newer revision was accepted. These private layers remain available; save them as a separate document')
            try:
                for row in self.layers:
                    row.edit = row.owner.snapshot(max_written_bytes=row.bytes, producer=row.producer)
                worker.stage_resources([(row.layer, row.input, row.edit) for row in self.layers])
                self.edit = tuple(row.edit for row in self.layers)
            except CaptureRefused as error:
                self.close()
                raise SaveNotStarted(f'No layers were saved. Private work remains available. Correct the layer or its declared input, then save again. {error}') from error
            except BaseException:
                self.close()
                raise

    def publish(self, stopping=lambda: False):
        if self.receipt is not None:
            return self.status()
        if self.failure == 'conflict':
            raise SaveConflict('These layers conflict with newer work; save them as a separate document')
        if self.edit is None:
            raise SaveUnavailable('Capture the complete set of layers before saving')
        if stopping():
            self.close()
            raise SaveUnavailable('Saving stopped before publication. No layers were accepted')
        with admission(self.save_workspace):
            try:
                if self._publication is None:
                    self._publication = ArrayPublication(self.store, self.saved,
                        [edit.prepared() for edit in self.edit], self.transaction)
                self.receipt = self._publication.publish()
            except Conflict as error:
                self._conflict()
                raise SaveConflict('Another edit was accepted first. No part of this save was accepted; keep these layers as a separate document') from error
            except (OSError, InvalidProject) as error:
                raise SaveUnavailable('The complete layer save could not be confirmed. Retry saving these same values without rerunning Python') from error
        return self.status()

    def copy(self, worker, frame, stopping=lambda: False):
        if self.copy_receipt is not None:
            return self.status()
        if self.failure != 'conflict':
            raise SaveUnavailable('Save a separate document only after a confirmed revision conflict')
        if stopping():
            raise SaveUnavailable('Copy stopped before publication; private changes remain available')
        if self.edit is None:
            self.capture(worker, for_copy=True)
        with admission(self.save_workspace):
            try:
                if self.copy_attempt is None:
                    self.copy_attempt = self.store.copy_candidate(self.saved, [row.edit.prepared() for row in self.layers])
                self.copy_receipt = self.copy_attempt.publish(stopping)
            except (OSError, InvalidProject) as error:
                raise SaveUnavailable(f'The separate document is not confirmed saved. Retry the same captured layers. {error}') from error
        return self.status()

    def status(self):
        accepted = self.copy_receipt or self.receipt
        return {'request': self.identity, 'state': 'copied' if self.copy_receipt else 'saved' if accepted else self.failure or 'staged',
                'resource': None if accepted is None else {'day': accepted.session, 'id': accepted.id, 'version': accepted.revision, 'picture': None},
                'layer': self.layers[0].layer, 'layers': [row.layer for row in self.layers],
                **({'latest': dict(self.latest)} if self.latest and not accepted else {}),
                **({'copyCaptured': self.edit is not None, 'copyNeedsPreview': False} if self.failure == 'conflict' and not accepted else {}),
                **({'conflicts': [dict(row) for row in self.granular]} if self.granular and self.failure == 'conflict' and not accepted else {}),
                **({'onto': dict(self.onto)} if self.onto and self.receipt is not None and not self.copy_receipt else {})}

    def close(self):
        try:
            if self.copy_attempt is not None:
                self.copy_attempt.close()
        finally:
            for row in self.layers:
                row.close()
