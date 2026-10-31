"""A native capability's labels are published with their execution record (native_capability_runs.NativeOutput).

FileIntake learned an importer `producer` of its own (2e5ae60); its constructor then set it to None after NativeOutput had
stored the execution record there, so publishing native labels raised TypeError (hooke-web's hosted worker test found it).
"""
import numpy as np

from orchestrator.native_capability_runs import NativeOutput
from orchestrator.project_repository import document_root
from orchestrator.scientific_projects import ProjectStore
from tests.test_project_folder_copy import setup


def test_native_labels_publish_with_their_execution_record(tmp_path):
    root, _repo, first, _second = setup(tmp_path)
    store = ProjectStore(document_root(root))
    saved = store.read(first.id, root.session)
    labels = np.zeros((6, 8), dtype=np.uint32)
    labels[1:3, 1:4] = 1
    labels[3:5, 5:7] = 2
    record = {'kind': 'capability', 'capability': 'cellpose-sam', 'generation': 'g1', 'environment': {'env': 'e1'},
              'params': {}, 'input': {'id': saved.id}, 'producer': {'entryGeneration': 'g1'},
              'execution': {'id': 'x1'}, 'sha256': 'f' * 64, 'at': 1793400000.0}
    intake = NativeOutput(store, saved, 'd' * 32, labels, {}, record)
    try:
        assert intake.producer == record
        kept = intake.publish()
    finally:
        intake.close()
    output = kept.document['state']['hookeOutput']
    assert output['producer'] == record
    assert output['at'] == record['at']
    assert 'sourceImport' not in kept.document['state']  # a run's output, not an imported file
