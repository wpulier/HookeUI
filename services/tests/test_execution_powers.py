from types import SimpleNamespace

from orchestrator import execution
from gpu_worker.worker import execution_facts


def test_capture_is_available_by_default_with_explicit_off_switch(monkeypatch):
    monkeypatch.delenv("MICROMIND_EXECUTION_INSPECT", raising=False)
    assert execution.ExecutionLog.from_env().enabled
    monkeypatch.setenv("MICROMIND_EXECUTION_INSPECT", "0")
    assert not execution.ExecutionLog.from_env().enabled


def test_inspection_names_actual_cell_option_from_capability_catalogue():
    from orchestrator.options import CELLS

    log = execution.ExecutionLog(enabled=True)
    with log.request("ask") as root:
        with execution.span("tool:outline_cells", provider="app"):
            with execution.span("gpu", selected="sam3.1-local",
                                **execution_facts(SimpleNamespace(device="cpu"), {"op": "cells", "model": "cellpose-sam"})):
                pass
    powers = log.inspect(root.id)["powers"]
    assert len(powers) == 1
    assert powers[0]["label"] == CELLS["cellpose-sam"].label
    assert powers[0]["outcome"] == "completed"
    assert powers[0]["capability"]
    assert "SAM 3.1" not in powers[0]["label"]


def test_failed_attempt_and_retained_fallback_are_both_truthful():
    from orchestrator.options import OUTLINE

    log = execution.ExecutionLog(enabled=True)
    with log.request("click") as root:
        with execution.span("gpu", role="outline", optionId="sam3.1-local", model="checkpoint"):
            execution.finish("failed")
        with execution.span("gpu", role="outline", optionId="edgetam", model="checkpoint"):
            pass
    powers = log.inspect(root.id)["powers"]
    assert [(p["label"], p["outcome"]) for p in powers] == [
        (OUTLINE["sam3.1-local"].label, "failed"), (OUTLINE["edgetam"].label, "completed")]


def test_app_action_and_blind_inspection_do_not_invent_models():
    log = execution.ExecutionLog(enabled=True)
    with log.request("ask") as root:
        with execution.span("tool:close_magnifier", provider="app"):
            pass
    power = log.inspect(root.id)["powers"][0]
    assert power["label"] == "HookeUI"
    assert power["capability"] == "Close the magnifier"
    log._roots[root.id].trial_id = "trial1"
    assert "powers" not in log.inspect(root.id)


def test_catalogue_work_happens_only_on_inspection(monkeypatch):
    from orchestrator import execution_powers

    calls = []
    original = execution_powers.powers
    monkeypatch.setattr(execution_powers, "powers", lambda steps: (calls.append(True), original(steps))[1])
    log = execution.ExecutionLog(enabled=True)
    with log.request("ask") as root:
        with execution.span("vision", model="gemini-3.8-flash", optionId="gemini-3.8-flash-hires"):
            pass
    log.references()
    assert calls == []
    assert log.inspect(root.id)["powers"][0]["label"] == "Gemini 3.8 Flash (high resolution)"
    assert len(calls) == 1


def test_click_distinguishes_naming_from_segmentation_and_appearance_matching():
    log = execution.ExecutionLog(enabled=True)
    with log.request("click") as root:
        with execution.span("outline"):
            with execution.span("segment"):
                with execution.span("gpu", role="outline", optionId="edgetam", model="yonigozlan/EdgeTAM-hf"):
                    pass
            with execution.span("gpu", role="embed", optionId="dinov2-small", model="facebook/dinov2-small"):
                pass
        with execution.span("name"):
            with execution.span("vision", model="gemini-3.5-flash"):
                pass
    powers = log.inspect(root.id)["powers"]
    assert [(p["capability"], p["label"]) for p in powers] == [
        ("Outlining", "EdgeTAM"),
        ("Look-alikes", "DINOv2 small"),
        ("Naming", "Gemini 3.5 Flash"),
    ]


def test_click_model_is_not_assumed_to_be_naming_without_a_recorded_name_step():
    log = execution.ExecutionLog(enabled=True)
    with log.request("click") as root:
        with execution.span("vision", model="gemini-3.5-flash"):
            pass
    assert log.inspect(root.id)["powers"][0]["capability"] == "Model"
