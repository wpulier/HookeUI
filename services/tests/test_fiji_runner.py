"""Fiji functions as catalog folders (orchestrator/fiji_runner.py; ADR 0024): a folder names an ImageJ command and its
options as the macro recorder writes them; it is proven only by agreeing with Fiji itself (the installed Fiji must know
the command, and the same command run as a macro must give the same result, at its defaults and across its declared
range); a new function that fails is recorded and not offered, and one that worked never stops working by a new
generation. A stand-in function worker and macro here; the real Fiji's proof is make catalog-proof (and
bench/capability_test.py)."""
from types import SimpleNamespace

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import fiji_generations as fiji, fiji_runner, kinds
from tests.test_fiji_generations import home  # noqa: F401 - a stand-in Fiji in a temporary catalog


def blur() -> catalog.Manifest:
    m = catalog.get("fiji-gaussian-blur")
    assert m is not None, catalog.problems().get("fiji-gaussian-blur")
    return m


def test_the_gaussian_blur_folder_is_a_capability_with_its_parameter_and_no_app_code():
    m = blur()
    assert m.contract and m.runner == "fiji" and m.fiji == {"command": "Gaussian Blur...", "options": "sigma={sigma}"}
    assert [p.id for p in m.params] == ["sigma"] and m.outputs[0].type == "image.2d.rgb8"
    assert fiji_runner.options_of(m, {"sigma": 2.0}) == "sigma=2"
    assert fiji_runner.value_sets(m) == [{"sigma": 2.0}, {"sigma": 0.5}, {"sigma": 20.0}]


def test_options_are_written_as_the_recorder_writes_them():
    m = blur()
    words = catalog.Input("method", "choice", options=("Default", "Max Entropy"), default="Default")
    flag = catalog.Input("dark", "bool", default=False)
    f = SimpleNamespace(params=(words, flag), fiji={"options": "method={method} {dark}"})
    assert fiji_runner.options_of(f, {"method": "Max Entropy", "dark": True}) == "method=[Max Entropy] dark"
    assert fiji_runner.options_of(f, {"method": "Default", "dark": False}) == "method=Default"
    with pytest.raises(fiji.Failed, match="can't be written"):
        fiji_runner.options_of(m, {"sigma": 'x" ; run("Quit'})


@pytest.mark.parametrize("change, said", [
    (('options = "sigma={sigma}"', 'options = "radius={radius}"'), "none of its parameters"),
    (('options = "sigma={sigma}"', 'options = ""'), "leave out ['sigma']"),
    (('command = "Gaussian Blur..."', 'script = "run.py"'), "names its ImageJ `command`"),
    (('type = "image.2d.rgb8"\nlabel = "Blurred picture"', 'type = "points.2d"\nlabel = "Blurred picture"'), "gives one of"),
])
def test_a_fiji_folder_is_checked_before_anything_runs(tmp_path, change, said):
    text = (catalog.ROOT / "fiji-gaussian-blur" / "model.toml").read_text()
    assert change[0] in text
    (tmp_path / "fiji-gaussian-blur").mkdir()
    (tmp_path / "fiji-gaussian-blur" / "model.toml").write_text(text.replace(*change, 1))
    _m, problems = catalog.read(tmp_path / "fiji-gaussian-blur" / "model.toml")
    assert any(said in p for p in problems), problems


class StandIn:
    """The function worker: it knows the commands it is told, and blurs by a box (or by what `make` gives)."""

    def __init__(self, commands=("Gaussian Blur...",), make=None):
        self.commands, self.make, self.calls = set(commands), make, []

    def call(self, request, n=None):
        self.calls.append(request)
        if request["op"] == "has_command":
            return {"ok": True, "found": request["command"] in self.commands}
        import cv2

        picture = cv2.imread(request["image"])
        cv2.imwrite(request["out"], self.make(picture, request) if self.make else picture)
        return {"ok": True, "kind": "picture", "path": request["out"]}

    def stop(self):
        pass


def macro_result(m, image, values, n, environ=None):
    """Fiji's own result, as a macro: here, the picture unchanged."""
    picture = fiji_runner._picture(image)
    return {"picture": picture, "shape": list(picture.shape)}


@pytest.fixture
def standin(monkeypatch):
    def use(worker):
        monkeypatch.setattr("orchestrator.fiji_bindings.Worker", lambda script=None: worker)
        monkeypatch.setattr(fiji_runner, "by_macro", macro_result)
    return use


def darken(picture, request):
    """A stand-in effect of sigma: darker as it grows (both sides compute it the same way)."""
    sigma = float(request["options"].split("=")[1])
    return (picture * (1 - sigma / 40)).astype(picture.dtype)


def macro_darkened(m, image, values, n, environ=None):
    picture = (fiji_runner._picture(image) * (1 - values["sigma"] / 40)).astype("uint8")
    return {"picture": picture, "shape": list(picture.shape)}


def test_it_is_proven_only_by_agreeing_with_fijis_own_macro_at_every_value(standin, tmp_path, monkeypatch):
    m = blur()
    worker = StandIn(make=darken)
    standin(worker)
    monkeypatch.setattr(fiji_runner, "by_macro", macro_darkened)
    got = fiji_runner.proof(m, tmp_path / "7" / "Fiji")
    assert got["passed"] and got["contract"] == fiji_runner.CONTRACT and len(got["cases"]) == 6, "2 pictures, 3 sigmas"
    assert [c["values"]["sigma"] for c in got["cases"]] == [2.0, 0.5, 20.0] * 2
    assert "identical to Fiji's own Gaussian Blur" in got["summary"] and got["cases"][1]["options"] == "sigma=0.5"
    assert worker.calls[0] == {"op": "has_command", "command": "Gaussian Blur..."}, "the installed Fiji is asked first"


def test_a_parameter_that_changes_nothing_is_never_proven(standin, tmp_path):
    """Both sides get the same options, so parity alone can't see one that never reaches the command."""
    standin(StandIn())  # it returns the picture unchanged whatever sigma is
    from orchestrator import fiji_runner as runner

    standin_macro = runner.by_macro
    with pytest.raises(fiji.Failed, match="Sigma .radius. changed nothing"):
        fiji_runner.proof(blur(), tmp_path / "7" / "Fiji")
    assert standin_macro is macro_result


def test_a_command_the_installed_fiji_doesnt_know_is_never_proven(standin, tmp_path):
    standin(StandIn(commands=()))
    with pytest.raises(fiji.Failed, match="has no command 'Gaussian Blur...'"):
        fiji_runner.proof(blur(), tmp_path / "7" / "Fiji")


def test_a_result_that_isnt_fijis_own_is_never_proven(standin, tmp_path):
    standin(StandIn(make=lambda picture, request: 255 - picture))
    with pytest.raises(fiji.Failed, match="differs from Fiji's own Gaussian Blur"):
        fiji_runner.proof(blur(), tmp_path / "7" / "Fiji")


def test_its_proof_is_of_exactly_this_folder_and_worker():
    m = blur()
    good = {**fiji_runner.identity(m), "passed": True}
    assert fiji_runner.proof_current(m, good)
    assert not fiji_runner.proof_current(m, {**good, "workerSha256": "0" * 64}), "a changed worker is proven again"
    assert not fiji_runner.proof_current(m, {**good, "passed": False})


def test_a_new_function_that_fails_is_said_and_one_that_worked_never_stops_working(home, monkeypatch):
    m = blur()
    monkeypatch.setattr(fiji_runner, "entries", lambda environ=None: [m])
    outcome = {"fail": True}

    def proof(entry, root, environ=None):
        if outcome["fail"]:
            raise fiji.Failed("its picture differs")
        return {**fiji_runner.identity(entry), "passed": True, "summary": "stand-in parity"}

    monkeypatch.setattr(fiji_runner, "proof", proof)
    assert fiji.first() == 1 and fiji.current() == 1, "a new function that fails never blocks the generation"
    assert fiji.receipt(1)["proofs"]["fiji-gaussian-blur"]["passed"] is False
    assert "its proof failed on Fiji generation 1: its picture differs" in fiji.proven("fiji-gaussian-blur")
    assert fiji_runner.in_use(m) is None
    outcome["fail"] = False
    assert fiji.renew() == 2 and fiji.proven("fiji-gaussian-blur") == ""
    assert fiji_runner.in_use(m).n == 2
    outcome["fail"] = True  # an update would lose it: refused, and generation 2 stays the one in use
    with pytest.raises(fiji.Failed, match="works on generation 2 and failed on this one"):
        fiji.renew()
    assert fiji.current() == 2 and fiji_runner.in_use(m) is not None


def test_a_proven_fiji_function_comes_through_the_door(monkeypatch):
    m = blur()
    monkeypatch.setattr(fiji_runner, "in_use", lambda entry, environ=None: fiji_runner.Installed(
        entry, {"passed": True, "summary": "stand-in parity"}, 2) if entry.key == m.key else None)
    found = [k for k in kinds.available() if k["key"] == "fiji-gaussian-blur"]
    assert found and found[0]["runner"] == "fiji" and found[0]["family"] == "seeing" and found[0]["runnable"]
    assert found[0]["params"][0]["id"] == "sigma" and "sigma (a number, 0.5-20.0, px, default 2.0)" in found[0]["says"]


def test_a_proven_fiji_function_is_on_its_card_and_can_be_tried(monkeypatch):
    """Its card asks the door's backend (live 2026-10-15: proven on Fiji generation 4, its card said "Needs setup · Its
    adapter needs a functional check" and had no Try, since only python-env's in_use was asked), and says the proof in
    Fiji's own words; the Actions row says ImageJ runs it, never the app's own code."""
    from orchestrator import catalog_view
    from orchestrator.capabilities import contributed

    m = blur()
    monkeypatch.setattr(fiji_runner, "in_use", lambda entry, environ=None: fiji_runner.Installed(
        entry, {"passed": True, "summary": "stand-in parity"}, 2) if entry.key == m.key else None)
    kind = catalog_view.kind_info(m)
    assert kind["proved"] and kind["activeVersion"] == m.version
    assert kind["proof"] == "proven on Fiji generation 2: stand-in parity"
    row = next(r for r in contributed(None) if r["id"] == "catalog:fiji-gaussian-blur")
    assert row["runsOn"] == "Gaussian Blur (Fiji): ImageJ, in the app's own Fiji on this Mac"


def test_your_own_fiji_function_waits_for_the_sandboxed_fiji():
    m = blur()
    import dataclasses

    mine = dataclasses.replace(m, namespace="mine")
    assert fiji_runner.in_use(mine) is None and mine not in fiji_runner.entries()


def test_run_function_reads_back_what_fiji_wrote(standin):
    m = blur()
    worker = StandIn(make=lambda picture, request: np.zeros_like(picture))
    picture = np.full((8, 8, 3), 200, np.uint8)
    got = fiji_runner.run_function(m, picture, {"sigma": 2.0}, n=1, using=worker)
    assert got["shape"] == [8, 8, 3] and int(got["picture"].max()) == 0
    assert worker.calls[-1]["options"] == "sigma=2" and worker.calls[-1]["command"] == "Gaussian Blur..."


def test_worker_identity_hashes_unchanged_bootstrap_once_but_rechecks_capability_files(tmp_path, monkeypatch):
    """Repeated display proof checks do not reread the installed app worker."""
    import hashlib
    from pathlib import Path

    worker = tmp_path / "functions.groovy"
    manifest = tmp_path / catalog.MANIFEST
    script = tmp_path / "run.groovy"
    for path, content in ((worker, b"worker"), (manifest, b"manifest"), (script, b"script")):
        path.write_bytes(content)
    monkeypatch.setattr(fiji_runner, "FUNCTIONS", worker)
    # The installed worker is older than the racy window in real use; a file written this instant is rightly never
    # cached (test_linux_portability.py), so this test's fresh file is treated as an old one.
    from orchestrator import envs
    monkeypatch.setattr(envs, "RACY_NS", -1)
    entry = SimpleNamespace(folder=tmp_path, fiji={"script": script.name})
    reads, read_bytes = [], Path.read_bytes

    def read(path):
        reads.append(path)
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    first = fiji_runner.identity(entry)
    assert fiji_runner.identity(entry) == first
    assert reads.count(worker) == 1
    assert reads.count(manifest) == reads.count(script) == 2
    assert first == {"contract": fiji_runner.CONTRACT, "workerSha256": hashlib.sha256(b"worker").hexdigest(),
                     "manifestSha256": hashlib.sha256(b"manifest").hexdigest(),
                     "scriptSha256": hashlib.sha256(b"script").hexdigest()}


@pytest.mark.parametrize("field", ["st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"])
def test_worker_identity_cache_invalidates_every_file_signature_component(tmp_path, monkeypatch, field):
    """Controlled filesystem observations isolate each component, including ctime."""
    import hashlib
    from pathlib import Path

    worker = tmp_path / "functions.groovy"
    worker.write_bytes(b"first")
    monkeypatch.setattr(fiji_runner, "FUNCTIONS", worker)
    stat, read = Path.stat, Path.read_bytes
    values = {name: getattr(worker.stat(), name) for name in
              ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
    # An old file: one changed within envs.RACY_NS is rightly never cached (test_linux_portability.py).
    from orchestrator import envs
    values["st_ctime_ns"] -= 10 * envs.RACY_NS
    content, reads = b"first", []
    monkeypatch.setattr(Path, "stat", lambda path, **kwargs: SimpleNamespace(**values) if path == worker else stat(path, **kwargs))

    def read_worker(path):
        if path == worker:
            reads.append(True)
            return content
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", read_worker)
    before = fiji_runner._sha(worker)
    assert fiji_runner._sha(worker) == before
    assert len(reads) == 1
    content = b"other"
    values[field] += 1
    assert fiji_runner._sha(worker) == hashlib.sha256(content).hexdigest() != before
    assert len(reads) == 2


def test_worker_identity_cache_cannot_hide_removal_or_replacement(tmp_path, monkeypatch):
    import hashlib
    import os

    worker = tmp_path / "functions.groovy"
    worker.write_bytes(b"first")
    monkeypatch.setattr(fiji_runner, "FUNCTIONS", worker)
    first = fiji_runner._sha(worker)
    previous = worker.stat()
    worker.unlink()
    with pytest.raises(FileNotFoundError):
        fiji_runner._sha(worker)
    # Same path, length and restored mtime still cannot inherit the old digest.
    worker.write_bytes(b"other")
    os.utime(worker, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert fiji_runner._sha(worker) == hashlib.sha256(b"other").hexdigest() != first
