"""One response observes setup once; the next response observes changed facts."""
from gpu_worker import model_files
from orchestrator.app import App
from orchestrator.paths import Paths
from tests.test_fiji_generations import home  # noqa: F401 - isolated stand-in Fiji fixture


def test_capability_options_and_model_cards_share_one_file_observation(tmp_path, monkeypatch):
    app = App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)
    location = model_files.location
    observations = []

    def changed_files(model, environ):
        if model.id != "sam2.1-tiny":
            return location(model, environ)
        observations.append(model.id)
        # A file disappears after its first observation. The response's card and
        # option must agree, and the next response must see it missing.
        return ("folder", 0) if len(observations) == 1 else (None, 1)

    monkeypatch.setattr(model_files, "location", changed_files)

    def state():
        wire = app.capabilities_wire(counts=False)
        outline = next(role for role in wire["roles"] if role["id"] == "outline")
        option = next(option for option in outline["options"] if option["id"] == "sam2.1-tiny")
        card = next(row for row in wire["models"] if row["id"] == "sam2.1-tiny")
        return option["readiness"]["status"], card["status"]

    assert state() == ("ready", "ready")
    assert observations == ["sam2.1-tiny"]
    assert state() == ("needs-setup", "needs-setup")
    assert observations == ["sam2.1-tiny", "sam2.1-tiny"]


def test_each_response_reads_current_keys_proof_generation_and_failures(home, monkeypatch):
    from orchestrator import fiji_bindings, fiji_generations as fiji, model_checks

    app = App.build("http://127.0.0.1:1", Paths.under(home / "app"), use_gpu=False)

    def option(role, name):
        wire = app.capabilities_wire(counts=False)
        group = next(row for row in wire["roles"] if row["id"] == role)
        return next(row for row in group["options"] if row["id"] == name)

    assert option("vision", "gemini-3.8-flash")["readiness"]["status"] == "needs-setup"
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-response-key")
    assert option("vision", "gemini-3.8-flash")["readiness"]["status"] == "ready"
    monkeypatch.delenv("GEMINI_API_KEY")
    assert option("vision", "gemini-3.8-flash")["readiness"]["status"] == "needs-setup"

    binding = "fiji-analyze-particles"
    assert fiji.first() == 1
    assert option("cells", binding)["readiness"]["next"]["says"] == "Prove it on Fiji"
    monkeypatch.setitem(fiji.PROOFS, binding, lambda folder: "matches its macro: stand-in only")
    assert fiji.renew() == 2
    assert option("cells", binding)["readiness"]["next"]["says"] == "Prove it on Fiji"
    # A historical summary is not proof of today's native-pixel contract.
    stamp = {**fiji_bindings.proof_identity(), "passed": True, "summary": "process stand-in proof"}
    monkeypatch.setitem(fiji.PROOFS, binding, lambda folder: stamp)
    assert fiji.renew() == 3
    assert option("cells", binding)["readiness"]["status"] == "ready"
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-response-key")
    entry = model_checks.find("vision", "gemini-3.8-flash")
    app.hub.checks.record(entry, "broken", "stand-in failure")
    assert option("vision", "gemini-3.8-flash")["readiness"]["status"] == "broken"
    app.hub.checks.record(entry, "works")
    assert option("vision", "gemini-3.8-flash")["readiness"]["status"] == "ready"
    assert fiji.rollback() == 2
    assert option("cells", binding)["readiness"]["next"]["says"] == "Prove it on Fiji"
