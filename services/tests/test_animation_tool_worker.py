"""Actual upstream animation through the common confined document path.

Synthetic scientific inputs; this does not qualify export or an ordinary-user
package installation. A prepared candidate is required, never installed by tests.
"""
from contextlib import ExitStack
import ast
import json
import os
from pathlib import Path

import numpy as np
import pytest

from gpu_worker import manifests
from orchestrator import scientific_host, scientific_project_review
from orchestrator.scientific_outputs import OutputSave
from tests.test_scientific_edits import scene, values


def test_actual_animation_interpolation_document_reopen_and_original_preservation(scene, monkeypatch):
    location = os.environ.get('NAPARI_ANIMATION_HOST_PREPARATION')
    if not location:
        pytest.skip('requires prepared actual animation candidate; not package qualification')
    prepared = json.loads(Path(location).read_text())
    for key, value in prepared['environment'].items(): monkeypatch.setenv(key, value)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    manifests.manifests.cache_clear()
    store, saved, reference, original = scene
    bundle, operation = prepared['key'].split('#')
    runtime = {'bundle': bundle, 'generation': Path(prepared['folder']).name, 'operation': operation}
    body = saved.document
    body['runtimes'] = [runtime]
    body['state']['scientificView'] = {'runtime': runtime, 'inputs': {'specimen': reference}}
    saved = store.save(saved.id, saved.session, body, expected_revision=saved.revision)
    workers = []
    start = scientific_host.ReviewWorker.start
    def diagnostic(worker):
        workers.append(worker)
        try: return start(worker)
        except BaseException as error:
            path = worker.work / 'stderr.log'
            if path.exists(): error.add_note(path.read_text()[-8000:])
            raise
    monkeypatch.setattr(scientific_host.ReviewWorker, 'start', diagnostic)
    def execute(worker, source):
        reply, _ = worker.request({'op': 'python-resources', 'source': source})
        assert reply['console']['error'] == '', reply['console']
        return reply['console']['result']
    try:
        with ExitStack() as stack:
            _, worker = stack.enter_context(scientific_project_review.review(store, saved.id, saved.session, saved.revision))
            execute(worker, "from napari_animation.easing import Easing\na = viewer._hooke_animation\nviewer.layers[0].opacity = 0.4\na.capture_keyframe(steps=3)\nviewer.layers[0].opacity = 0.8\nviewer.camera.zoom *= 1.5\na.capture_keyframe(steps=4, ease=Easing.LINEAR)\na.key_frames[0].name = 'Original view'\na.key_frames[1].name = 'Closer view'\na.set_movie_frame_index(2)")
            expected = execute(worker, "(len(a.key_frames), len(a._frames), float(viewer.layers[0].opacity), int(viewer.layers[0].data.sum()))")
            count, length, opacity, total = ast.literal_eval(expected)
            assert (count, length, total) == (2, 5, int(original.sum()))
            assert opacity == pytest.approx(0.6, abs=1e-12)
            execute(worker, "from adapter import encode\n[len(str(encode(frame.viewer_state.layers))) for frame in a.key_frames]")
            # Both directions enforce the same aggregate feature-table allowance.
            invalid, _ = worker.request({'op': 'python-resources', 'source': "import pandas as pd\nimport numpy as np\nencode(pd.DataFrame({'value': np.zeros(4096, dtype='u1')}))"})
            assert 'bounded arrays' in invalid['console']['error']
            _, image = worker.request({'op': 'render', 'width': 256, 'height': 192})
            header, _ = worker.request({'op': 'layer-inventory'})
            layer = header['inventory']['layers'][0]['id']
            from orchestrator.scientific_saves import SaveNotStarted
            for change, correction in (
                ("viewer.layers[0].name = 'Renamed after keyframes'", "viewer.layers[0].name = 'specimen'"),
                ("a.key_frames[0].name = 'x' * 257", "a.key_frames[0].name = 'Original view'"),
            ):
                execute(worker, change)
                refused = OutputSave(store, saved, 'animation-refused-save-1234', layer,
                                     {'semantic': 'instance-labels', 'axes': ['y', 'x']}, tool=True)
                stack.callback(refused.close)
                with pytest.raises(SaveNotStarted): refused.capture(worker)
                assert worker.process.poll() is None and np.array_equal(values(store, saved, reference), original)
                execute(worker, correction)
            save = OutputSave(store, saved, 'animation-document-save-1234', layer,
                              {'semantic': 'instance-labels', 'axes': ['y', 'x']}, tool=True)
            stack.callback(save.close)
            save.capture(worker); save.publish()
            accepted = store.read(save.target, saved.session)
            tool = accepted.document['state']['scientificView']['tool']['document']
            assert tool['schema'] == 'hooke.napari-animation-0.0.10'
            assert [row['name'] for row in tool['state']['keyframes']] == ['Original view', 'Closer view']
            assert np.array_equal(values(store, saved, reference), original)
            with scientific_project_review.review(store, accepted.id, accepted.session, 1) as (_, restored):
                actual = execute(restored, "a = viewer._hooke_animation\n(len(a.key_frames), len(a._frames), float(viewer.layers[0].opacity), int(viewer.layers[0].data.sum()))")
                assert actual == expected
                _, reopened_image = restored.request({'op': 'render', 'width': 256, 'height': 192})
                assert reopened_image == image
                native, _ = restored.request({'op': 'native-panel-render', 'width': 640, 'height': 640})
                assert any(row['role'] == 'SpinBox' and row['value'] == '3' for row in native['nativePanel']['accessibility'])
                # A second save must preserve exact data-only keyframes and controls.
                header, _ = restored.request({'op': 'layer-inventory'})
                repeated = OutputSave(store, accepted, 'animation-document-again-1234', header['inventory']['layers'][0]['id'],
                                      {'semantic': 'instance-labels', 'axes': ['y', 'x']}, tool=True)
                stack.callback(repeated.close)
                repeated.capture(restored); repeated.publish()
                again = store.read(repeated.target, saved.session)
                assert again.document['state']['scientificView']['tool']['document'] == tool
                # Editing an actual upstream keyframe still drives interpolation.
                assert execute(restored, "a.key_frames[1].steps = 6\na.key_frames.events.changed(index=1, value=a.key_frames[1])\nlen(a._frames)") == '7'
            assert worker.process.poll() is None
        assert all(worker.process.poll() is not None and not worker.work.exists() for worker in workers)
        assert np.array_equal(values(store, saved, reference), original)
    finally:
        manifests.manifests.cache_clear()
