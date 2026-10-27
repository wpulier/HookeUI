"""Publish a frozen set of native changes in one existing Project CAS.

Only parent-owned prepared arrays enter here. No worker chooses a destination,
revision or replacement reference. All components and provenance move together;
retry uses the identical document and transaction, including after a lost ack.
"""
import copy

from .scientific_projects import InvalidProject, _json


class ArrayPublication:
    def __init__(self, store, saved, prepared, transaction):
        from .scientific_arrays import MAX_NATIVE_COMPONENTS
        from .scientific_copies import PreparedArray
        from .scientific_projects import DIGEST
        if (type(prepared) not in (list, tuple) or not 0 < len(prepared) <= MAX_NATIVE_COMPONENTS
                or type(transaction) is not str or not DIGEST.fullmatch(transaction)
                or store.read(saved.id, saved.session, revision=saved.revision) != saved):
            raise InvalidProject('Publish changes from one exact saved scientific revision')
        replacements = {}
        records = []
        components = set()
        for item in prepared:
            if (not isinstance(item, PreparedArray) or item.store is not store
                    or (item.project, item.session, item.revision) != (saved.id, saved.session, saved.revision)):
                raise InvalidProject('Every scientific change must belong to the same saved revision')
            original, reference = item.original, item.reference
            if original not in saved.document['resources'] or reference['component'] in components:
                raise InvalidProject('Choose each accepted scientific component once')
            components.add(reference['component'])
            replacements[_json(original)] = reference
            records.append(item.provenance)
        if len(records) > 1 and any(record is None for record in records):
            raise InvalidProject('Every change in a scientific save needs its producing provenance')
        document = saved.document
        document['resources'] = [replacements.get(_json(ref), ref) for ref in document['resources']]
        view = document['state'].get('scientificView')
        if type(view) is dict and set(view) in ({'runtime', 'inputs'}, {'runtime','inputs','layers'}, {'runtime','inputs','layers','tool'}, {'runtime','inputs','layers','initializeTool'}, {'runtime','inputs','layers','initializeTool','recipeTool'}) and type(view['inputs']) is dict:
            view['inputs'] = {name: replacements.get(_json(ref), ref) for name, ref in view['inputs'].items()}
        from .scientific_provenance import attach
        attach(document, records[0] if len(records) == 1 else
               {'format': 2, 'transaction': transaction, 'changes': records})
        self.store, self.saved, self.transaction = store, saved, transaction
        self._document = document
        self.receipt = None

    @property
    def document(self):
        return copy.deepcopy(self._document)

    def publish(self):
        if self.receipt is None:
            self.receipt = self.store.save(self.saved.id, self.saved.session, self._document,
                expected_revision=self.saved.revision, transaction=self.transaction)
        return self.receipt
