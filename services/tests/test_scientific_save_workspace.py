"""Bounded save headroom is admitted before model loading, not after its result."""
import pytest
from orchestrator import jobs, machine, scientific_saves as saves
from orchestrator.loads import MARGIN_MB
from tests.test_scientific_edits import scene, values
from tests.test_scientific_outputs import attempt


def test_admitted_workspace_saves_actual_values_after_model_load_without_double_reserving(scene,monkeypatch):
    memory=jobs.JobMemory();monkeypatch.setattr(jobs,'MEMORY',memory)
    free=[MARGIN_MB+4096+256+32]
    monkeypatch.setattr(machine,'free_mb',lambda:free[0])
    store,saved,save,worker=attempt(scene)
    with memory.reserve(128), memory.reserve(4096+32), saves.reserve_workspace() as workspace:
        worker.save_workspace=workspace
        free[0]=MARGIN_MB+512  # Model has loaded; its peak remains reserved.
        with pytest.raises(saves.SaveUnavailable):
            with saves.admission():pass
        free[0]=MARGIN_MB+127
        with pytest.raises(saves.SaveUnavailable):save.capture(worker)
        assert worker.calls==0 and store.read(saved.id,saved.session)==saved
        free[0]=MARGIN_MB+512
        save.capture(worker);save.publish()
        output=store.read(save.target,saved.session)
        assert values(store,output,output.document['resources'][0]).tobytes()==worker.data.tobytes()
        assert store.read(saved.id,saved.session)==saved and worker.calls==1
        assert memory.reserved_mb==4096+32+256
        save.close()
    assert memory.reserved_mb==0
    with saves.admission(workspace):
        assert memory.reserved_mb==128, 'Released workspace must obtain a fresh reservation'
    assert memory.reserved_mb==0


def test_workspace_refuses_overlapping_save_and_releases_on_failure(monkeypatch):
    memory=jobs.JobMemory();monkeypatch.setattr(jobs,'MEMORY',memory)
    monkeypatch.setattr(machine,'free_mb',lambda:MARGIN_MB+128)
    with saves.reserve_workspace() as workspace:
        with saves.admission(workspace):
            with pytest.raises(saves.SaveUnavailable):
                with saves.admission(workspace):pass
        with pytest.raises(RuntimeError):
            with saves.admission(workspace):raise RuntimeError('controlled storage failure')
        with saves.admission(workspace):assert memory.reserved_mb==128
    assert memory.reserved_mb==0


def test_existing_layer_save_uses_only_its_workers_reserved_workspace(scene,monkeypatch):
    from tests.test_scientific_saves import setup, Worker
    memory=jobs.JobMemory();monkeypatch.setattr(jobs,'MEMORY',memory)
    free=[MARGIN_MB+4096+128+32];monkeypatch.setattr(machine,'free_mb',lambda:free[0])
    store,saved,shown,data=setup(scene)
    changed=data.copy();changed[1,4]=65535
    worker=Worker(changed)
    with memory.reserve(4096+32), saves.reserve_workspace() as workspace:
        worker.save_workspace=workspace;free[0]=MARGIN_MB+512
        save=saves.LayerSave(store,saved,'save-1234567890123456',shown,'d'*32)
        save.capture(worker);save.publish()
        accepted=store.read(saved.id,saved.session)
        assert values(store,accepted,accepted.document['resources'][0]).tobytes()==changed.tobytes()
        assert values(store,saved,saved.document['resources'][0]).tobytes()==data.tobytes()
        assert worker.calls==1
        save.close()
    assert memory.reserved_mb==0
