"""Saves of existing layers in one scientific session, placed by the inputs their values came from.

Real Project storage and the real session loop over the existing stand-in worker; no native host,
browser or clean-machine qualification.
"""
import asyncio
import uuid

import numpy as np
import pytest

from orchestrator.scientific_rebase import CHANGED, REMOVED, UNCHANGED
from orchestrator.scientific_saves import SaveConflict
from tests.test_scientific_arrays import fill
from tests.test_scientific_reviews import save_rig


def two_layers(tmp_path):
    from orchestrator.scientific_workspace import ReviewOrigin
    rig, data = save_rig(tmp_path)
    draft = rig.store.array_draft(rig.saved.id, rig.saved.session, component='array.mask', shape=data.shape,
        dtype=data.dtype.str, axes=['y', 'x'], chunks=[2, 3], scientific={'semantic': 'instance-labels'},
        max_written_bytes=data.nbytes)
    mask = fill(draft, data + 10)
    document = rig.saved.document
    document['resources'].append(mask)
    document['state']['scientificView']['inputs']['mask'] = mask
    rig.saved = rig.store.save(rig.saved.id, rig.saved.session, document, expected_revision=rig.saved.revision)
    rig.ref['version'] = rig.saved.revision
    rig.item['view']['legacy']['document']['resource'] = rig.ref
    rig.layout['views'][0]['items'][0]['resource'] = rig.ref
    rig.change()
    rig.origin = ReviewOrigin.admit(rig.screen, rig.client, rig.pin(), rig.item['id'], rig.saved, rig.layout)
    return rig, data


LAYER = {'labels': 'a' * 32, 'mask': 'b' * 32}


def values(rig, revision, input):
    saved = rig.store.read(rig.saved.id, rig.saved.session, revision=revision)
    reference = saved.document['state']['scientificView']['inputs'][input]
    source = rig.store.array_source(rig.saved.id, rig.saved.session, revision, reference)
    return source.read((slice(0, 2), slice(0, 3)))


def foreign(rig, input, data, *, remove=False):
    """Another session's accepted save of one input (or its removal), on the current head."""
    head = rig.store.read(rig.saved.id, rig.saved.session)
    document = head.document
    inputs = document['state']['scientificView']['inputs']
    old = inputs[input]
    if remove:
        del inputs[input]
        document['resources'] = [ref for ref in document['resources'] if ref != old]
    else:
        new = fill(rig.store.array_edit(rig.saved.id, rig.saved.session, head.revision, old,
                                        max_written_bytes=data.nbytes), data)
        document['resources'] = [new if ref == old else ref for ref in document['resources']]
        inputs[input] = new
    return rig.store.save(rig.saved.id, rig.saved.session, document, expected_revision=head.revision)


def session(rig, data):
    async def start():
        identity = await rig.manager.open(rig.origin)
        written = {}
        def one(layer, input, edit):
            written[input] = written.get(input, 0) + 1
            edit.write([0, 0], data + 100 * (1 + list(LAYER).index(input)) + written[input])
        def several(rows):
            for layer, input, edit in rows:
                one(layer, input, edit)
        rig.workers[0].stage_resource = one
        rig.workers[0].stage_resources = several
        rig.change(); rig.item['scientificPaint'] = None
        return identity
    return start


async def save(rig, identity, *inputs):
    command = ({'op': 'save-resource', 'layer': LAYER[inputs[0]], 'input': inputs[0]} if len(inputs) == 1 else
               {'op': 'save-resources', 'layers': [{'layer': LAYER[i], 'input': i} for i in inputs]})
    command.update(resource=dict(rig.ref), request=uuid.uuid4().hex)
    return (await rig.manager.request(rig.client, identity, command, snapshot=rig.pin()))['arraySave']


def test_saving_one_layer_never_strands_another_or_its_own_next_save(tmp_path):
    rig, data = two_layers(tmp_path)
    async def go():
        try:
            identity = await session(rig, data)()
            first = await save(rig, identity, 'labels')
            # The session stays open for its other private work after a confirmed save.
            ran = await rig.manager.request(rig.client, identity, {'op': 'python-resources', 'resource': dict(rig.ref),
                'cell': uuid.uuid4().hex, 'source': '1+1'}, snapshot=rig.pin())
            assert ran['ok']
            second = await save(rig, identity, 'mask')
            third = await save(rig, identity, 'labels')
            assert [first['state'], second['state'], third['state']] == ['saved'] * 3
            assert [s['resource']['version'] for s in (first, second, third)] == [rig.saved.revision + n for n in (1, 2, 3)]
            assert 'onto' not in second and 'onto' not in third  # nothing else was accepted meanwhile
            head = third['resource']['version']
            assert np.array_equal(values(rig, head, 'labels'), data + 102)
            assert np.array_equal(values(rig, head, 'mask'), data + 201)
            # Each save's record names the revision it was accepted on, directly, as its exact base.
            for revision in (head - 2, head - 1, head):
                accepted = rig.store.read(rig.saved.id, rig.saved.session, revision=revision)
                base = accepted.document['state']['hookeEdit']['base']
                assert base['revision'] == revision - 1 and base['digest'] == accepted.parent
        finally:
            await rig.manager.close()
    asyncio.run(go())


def test_another_sessions_save_of_a_different_layer_is_kept_and_this_save_lands_on_it(tmp_path):
    rig, data = two_layers(tmp_path)
    async def go():
        try:
            identity = await session(rig, data)()
            other = foreign(rig, 'mask', data + 7)
            result = await save(rig, identity, 'labels')
            assert result['state'] == 'saved' and result['resource']['version'] == other.revision + 1
            assert result['onto'] == {'version': other.revision, 'since': rig.saved.revision}
            head = result['resource']['version']
            assert np.array_equal(values(rig, head, 'mask'), data + 7)  # the other save is kept
            assert np.array_equal(values(rig, head, 'labels'), data + 101)
            accepted = rig.store.read(rig.saved.id, rig.saved.session)
            base = accepted.document['state']['hookeEdit']['base']
            assert base['revision'] == other.revision and base['digest'] == accepted.parent  # accepted directly on it
        finally:
            await rig.manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('removed', [False, True])
def test_a_changed_layer_conflicts_layer_by_layer_and_the_rest_can_still_be_saved(tmp_path, removed):
    rig, data = two_layers(tmp_path)
    async def go():
        try:
            identity = await session(rig, data)()
            other = foreign(rig, 'labels', data + 7, remove=removed)
            with pytest.raises(SaveConflict, match='newer revision'):
                await save(rig, identity, 'labels', 'mask')
            assert rig.store.read(rig.saved.id, rig.saved.session) == other  # nothing was saved
            status = rig.manager.sessions(rig.client)[0]['arraySave']
            assert status['state'] == 'conflict' and status['latest']['version'] == other.revision
            assert status['conflicts'] == [
                {'layer': LAYER['labels'], 'input': 'labels', 'state': REMOVED if removed else CHANGED},
                {'layer': LAYER['mask'], 'input': 'mask', 'state': UNCHANGED}]
            # The unaffected layer is saved on its own, on the newer revision.
            kept = await save(rig, identity, 'mask')
            assert kept['state'] == 'saved' and kept['resource']['version'] == other.revision + 1
            if not removed:
                assert np.array_equal(values(rig, kept['resource']['version'], 'labels'), data + 7)
            # The changed one never overwrites the other save, even now this session saved on that revision.
            with pytest.raises(SaveConflict):
                await save(rig, identity, 'labels')
            copied = await rig.manager.request(rig.client, identity, {'op': 'save-copy', 'frame': None,
                'request': rig.manager._owners[identity].array_save.identity}, snapshot=rig.pin())
            assert copied['arraySave']['state'] == 'copied'
            assert rig.store.read(rig.saved.id, rig.saved.session).revision == other.revision + 1
        finally:
            await rig.manager.close()
    asyncio.run(go())


def test_a_lost_acknowledgement_still_records_what_the_session_now_comes_from(tmp_path, monkeypatch):
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.scientific_saves import SaveUnavailable
    rig, data = two_layers(tmp_path)
    async def go():
        try:
            identity = await session(rig, data)()
            real = ProjectStore.save
            failed = []
            def lose(self, *args, **kwargs):
                receipt = real(self, *args, **kwargs)
                if not failed:
                    failed.append(receipt)
                    raise OSError('acknowledgement lost')
                return receipt
            monkeypatch.setattr(ProjectStore, 'save', lose)
            command = {'op': 'save-resource', 'layer': LAYER['labels'], 'input': 'labels',
                       'resource': dict(rig.ref), 'request': uuid.uuid4().hex}
            with pytest.raises(SaveUnavailable):
                await rig.manager.request(rig.client, identity, command, snapshot=rig.pin())
            retried = await rig.manager.request(rig.client, identity, {'op': 'save-retry', 'request': command['request']},
                                                snapshot=rig.pin())
            assert retried['arraySave']['state'] == 'saved'
            again = await save(rig, identity, 'labels')  # the retry's acceptance is this session's base now
            assert again['state'] == 'saved' and 'onto' not in again
        finally:
            await rig.manager.close()
    asyncio.run(go())
