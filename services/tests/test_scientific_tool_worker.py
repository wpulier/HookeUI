"""Actual confined runtime and immutable Project data; not fresh-user qualification."""
from contextlib import ExitStack
import copy
import json
import os
from pathlib import Path

import numpy as np
import pytest

from gpu_worker import manifests
from orchestrator import scientific_host, scientific_project_review
from orchestrator.scientific_outputs import OutputSave
from tests.test_scientific_edits import scene, values


def test_actual_tool_save_reopen_and_corrupt_restore_preserve_accepted_work(scene, monkeypatch):
    location = os.environ.get('NAPARI_TOOL_HOST_PREPARATION')
    if not location:
        pytest.skip('requires prepared tool-document integration; not clean-machine qualification')
    prepared = json.loads(Path(location).read_text())
    for key, value in prepared['environment'].items(): monkeypatch.setenv(key, value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    manifests.manifests.cache_clear()
    store, saved, ref, original = scene
    bundle, operation = prepared['key'].split('#')
    runtime = {'bundle': bundle, 'generation': Path(prepared['folder']).name, 'operation': operation}
    body = saved.document
    body['runtimes'] = [runtime]
    body['state']['scientificView'] = {'runtime': runtime, 'inputs': {'specimen': ref}}
    saved = store.save(saved.id, saved.session, body, expected_revision=saved.revision)
    def execute(worker, source):
        reply, _ = worker.request({'op': 'python-resources', 'source': source})
        assert reply['console']['error'] == '', reply['console']
        return reply
    workers = []
    start = scientific_host.ReviewWorker.start
    def diagnostic(worker):
        workers.append(worker)
        try: return start(worker)
        except BaseException as error:
            log = worker.work / 'stderr.log'
            if log.exists(): error.add_note(log.read_text()[-8000:])
            raise
    monkeypatch.setattr(scientific_host.ReviewWorker, 'start', diagnostic)
    try:
        with ExitStack() as stack:
            _, good = stack.enter_context(scientific_project_review.review(store, saved.id, saved.session, saved.revision))
            _, worker = stack.enter_context(scientific_project_review.review(store, saved.id, saved.session, saved.revision))
            execute(worker, "viewer._hooke_tool_state.update(title='Saved experiment', keyframes=[{'point':[1.0,2.0], 'steps':17}]); viewer.layers[0].data[0,0]=73")
            header, _ = worker.request({'op':'layer-inventory'})
            assert header['toolDocumentAvailable'] is True
            layer = header['inventory']['layers'][0]['id']
            assert header['inventory']['layers'][0]['inputMeaning'] == {'semantic':'instance-labels', 'axes':['y','x']}
            execute(worker, "viewer._hooke_tool_state['mutateOnCapture']=True")
            refused = OutputSave(store, saved, 'refused-tool-save-1234', layer,
                                 {'semantic':'instance-labels','axes':['y','x']}, tool=True)
            from orchestrator.scientific_saves import SaveNotStarted
            with pytest.raises(SaveNotStarted, match='changed scientific'):
                refused.capture(worker)
            assert worker.process.poll() is None and not store._folder(refused.target).exists()
            assert np.array_equal(values(store, saved, ref), original)
            execute(worker, "viewer._hooke_tool_state['mutateOnCapture']=False; viewer.layers[0].data[0,0]=73")
            save = OutputSave(store, saved, 'tool-document-save-1234', layer,
                              {'semantic': 'instance-labels', 'axes': ['y','x']}, tool=True)
            stack.callback(save.close)
            save.capture(worker); save.publish()
            accepted = store.read(save.target, saved.session)
            expected = original.copy(); expected[0,0] = 73
            spec = accepted.document['state']['scientificView']
            assert np.array_equal(values(store, accepted, spec['inputs']['output']), expected)
            # Reopening is an explicit new owner, with the exact pinned generation.
            with scientific_project_review.review(store, accepted.id, accepted.session, 1) as (_, reopened):
                result, _ = reopened.request({'op':'python-resources', 'source': "(viewer._hooke_tool_state['title'], viewer._hooke_tool_state['keyframes'][0]['steps'], int(viewer.layers[0].data[0,0]))"})
                assert result['console']['result'] == "('Saved experiment', 17, 73)"
                header, _ = reopened.request({'op':'native-panel-render','width':320,'height':240})
                assert any(row['name']=='Capture keyframe' for row in header['nativePanel']['accessibility'])
            # A private candidate whose restore mutates science is refused; no
            # accepted document or unrelated worker is changed by that failure.
            bad = copy.deepcopy(accepted.document)
            bad['state']['scientificView']['tool']['document']['state']['corruptOnRestore'] = True
            rejected = store.save(accepted.id, accepted.session, bad, expected_revision=1)
            with pytest.raises((scientific_host.Failed, EOFError, OSError)):
                with scientific_project_review.review(store, rejected.id, rejected.session, rejected.revision):
                    pytest.fail('Mutating restore was admitted')
            assert store.read(accepted.id, accepted.session, revision=1) == accepted
            assert np.array_equal(values(store, accepted, spec['inputs']['output']), expected)
            assert np.array_equal(values(store, saved, ref), original)
            result, _ = good.request({'op':'python-resources','source':'int(viewer.layers[0].data[0,0])'})
            assert result['console']['result'] == str(int(original[0,0]))
        assert all(worker.process.poll() is not None and not worker.work.exists() for worker in workers)
    finally:
        manifests.manifests.cache_clear()
