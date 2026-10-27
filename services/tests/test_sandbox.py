"""macOS's sandbox around code nobody reviewed (orchestrator/sandbox.py; C7): a community catalog's adapter runs inside
sandbox-exec, reading only its Python, its environment, its entry and its files, writing only its work folder; a file
elsewhere is out of its reach. Reviewed code (the catalog's own, a reviewed template) runs as before; where the sandbox
can't run, code nobody reviewed is refused. A model made from a reviewed template is its makers' code (TorchScript is
code), so it runs inside too. Inside, it can't signal another process, read a descriptor the app held, reach a system
service that acts outside it (LaunchServices, the pasteboard) or share memory under another's name."""
import dataclasses
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, env_runner, envs, link_recognise, sandbox
from orchestrator.evidence import decode_labels
from tests.test_channels import _git, home, publish  # noqa: F401 - a scratch catalog and upstream
from tests.test_env_runner import ENTRY, standin_uv

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not sandbox.available(), reason="macOS's sandbox only")

PRYING = '''
import numpy as np

SECRET = {secret!r}

def segment(picture, params):
    labels = np.zeros(picture.shape[:2], np.int32)
    try:
        open(SECRET).read()
        labels[0, 0] = 7  # read what it should never read
    except OSError:
        labels[0, 0] = 3  # refused
    labels[5:9, 5:9] = 1
    return labels
'''


def community(home, monkeypatch, adapter: str):
    lab = home / "lab"
    (lab / "catalog" / "models").mkdir(parents=True)
    _git("init", "-q", "-b", "main", cwd=lab)
    (home / "upstream").rename(home / "upstream-reviewed")
    lab.rename(home / "upstream")
    publish(home, "dark-spots", ENTRY.format().replace('"install:dark-spots"', '"install:upstream/dark-spots"'), adapter)
    channels.add(str(home / "upstream"))
    channels.trust("upstream")
    m = catalog.get("upstream/dark-spots")
    standin_uv(home, monkeypatch)
    envs.install(m)  # its proof runs inside the sandbox too
    return m


def test_a_community_adapter_runs_inside_the_sandbox_and_cant_read_beyond_it(home, monkeypatch):
    secret = home / "elsewhere" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("a key")
    m = community(home, monkeypatch, PRYING.format(secret=str(secret)))
    assert sandbox.required(m)
    wrapped = []
    real = sandbox.wrap
    monkeypatch.setattr(sandbox, "wrap", lambda cmd, read, write, **kwargs: wrapped.append(cmd) or real(cmd, read, write, **kwargs))
    installed = env_runner.in_use(m)
    client = env_runner.Client(installed)
    try:
        out = client.run(np.full((20, 20, 3), 200, np.uint8))
    finally:
        client.stop()
    assert wrapped, "it was started inside sandbox-exec"
    from orchestrator.evidence import decode_labels

    labels = decode_labels(out["labels"])
    assert int(labels[0, 0]) == 3, "the file elsewhere was out of its reach (7: it read it)"


def test_code_nobody_reviewed_is_refused_where_the_sandbox_cant_run(home, monkeypatch):
    m = community(home, monkeypatch, PRYING.format(secret="/nonexistent"))
    monkeypatch.setattr(sandbox, "available", lambda: False)
    client = env_runner.Client(env_runner.in_use(m))
    with pytest.raises(env_runner.Failed, match="macOS's sandbox"):
        client.run(np.zeros((8, 8, 3), np.uint8))


def test_reviewed_code_needs_no_sandbox(home):
    assert not sandbox.required(catalog.get("dinov2-small"))


def test_installing_a_community_entry_imports_its_packages_inside_the_sandbox(home, monkeypatch):
    """Install's own check imports the entry's packages, their code running for the first time: inside the sandbox too."""
    seen = []
    real = sandbox.wrap
    monkeypatch.setattr(sandbox, "wrap", lambda cmd, read, write, **kwargs: seen.append(cmd) or real(cmd, read, write, **kwargs))
    community(home, monkeypatch, PRYING.format(secret="/nonexistent"))
    assert any("import sys; import numpy" in " ".join(cmd) for cmd in seen), seen


def run_once(m, picture=None) -> np.ndarray:
    client = env_runner.Client(env_runner.in_use(m))
    try:
        out = client.run(picture if picture is not None else np.full((20, 20, 3), 200, np.uint8))
    finally:
        client.stop()
    return decode_labels(out["labels"])


# A reviewed template's runner, byte for byte, and the model it loads: a stand-in for TorchScript, a file of its makers'
# Python the runner executes (as torch.jit.load runs a model's code).
TEMPLATE_RUNNER = """
from pathlib import Path

_MODEL = {}


def segment(picture, params):
    if not _MODEL:
        exec((Path(__file__).parent / "model.txt").read_text(), _MODEL)  # loading the model runs its makers' code
    return _MODEL["run"](picture)
"""

UNTRUSTED_MODEL = """
import numpy as np


def run(picture):
    labels = np.zeros(picture.shape[:2], np.int32)
    try:
        open({secret!r}).read()
        labels[0, 0] = 7  # read what it should never read
    except OSError:
        labels[0, 0] = 3  # refused
    labels[5:9, 5:9] = 1  # what the model is for
    return labels
"""


def from_template(home, monkeypatch, model: str):
    tpl = home / "templates" / "stand-in"
    tpl.mkdir(parents=True)
    (tpl / "template.toml").write_text('id = "stand-in"\n')
    (tpl / "adapter.py").write_text(TEMPLATE_RUNNER)
    (tpl / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    monkeypatch.setattr(link_recognise, "TEMPLATES", home / "templates")
    folder = catalog.mine_root() / "spots"
    folder.mkdir(parents=True)
    (folder / "model.toml").write_text(ENTRY.format().replace("dark-spots", "spots")
                                       .replace('"install:spots"', '"install:mine/spots"'))
    for f in ("adapter.py", "env.lock"):
        shutil.copyfile(tpl / f, folder / f)
    (folder / "model.txt").write_text(model)
    catalog.manifests.cache_clear()
    m = catalog.mine().get("mine/spots")
    assert m is not None, catalog.problems()
    standin_uv(home, monkeypatch)
    envs.install(m)
    return m


def test_a_model_run_by_a_reviewed_template_is_still_its_makers_code_and_runs_inside(home, monkeypatch):
    """Template identity says which runner it is, and the hashes which model: neither says the model is safe."""
    secret = home / "elsewhere" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("a key")
    m = from_template(home, monkeypatch, UNTRUSTED_MODEL.format(secret=str(secret)))
    assert link_recognise.reviewed_template(m) == "stand-in", "the runner is the template's, byte for byte"
    assert sandbox.required(m), "a template's model is its makers' code"
    assert "inside macOS's sandbox" in env_runner.adapter_fact(m)["says"]
    wrapped = []
    real = sandbox.wrap
    monkeypatch.setattr(sandbox, "wrap", lambda cmd, read, write, **kwargs: wrapped.append(cmd) or real(cmd, read, write, **kwargs))
    labels = run_once(m)
    assert wrapped, "it was started inside sandbox-exec"
    assert int(labels[0, 0]) == 3, "the model's code couldn't read the file elsewhere (7: it read it)"
    assert int((labels == 1).sum()) == 16, "its legitimate work runs under the boundary"


PROBING = """
import ctypes
import os
from multiprocessing import shared_memory

import numpy as np


def _looked_up(name):
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    port = ctypes.c_uint(0)
    return libc.bootstrap_look_up(ctypes.c_uint.in_dll(libc, "bootstrap_port"), name.encode(), ctypes.byref(port)) == 0


def _shared(name):
    try:
        one = shared_memory.SharedMemory(name=name, create=True, size=16)
    except OSError:
        return False
    one.close()
    one.unlink()
    return True


def _refused(probe):
    try:
        return 7 if probe() else 3
    except OSError:
        return 3


def segment(picture, params):
    labels = np.zeros(picture.shape[:2], np.int32)
    labels[0, 0] = _refused(lambda: os.kill({pid}, 0) or True)  # another of this user's processes
    labels[0, 1] = _refused(lambda: os.read({fd}, 64) == {canary!r})  # a descriptor the app held
    labels[0, 2] = _refused(lambda: _looked_up("com.apple.coreservices.launchservicesd"))  # opens links outside
    labels[0, 3] = _refused(lambda: _looked_up("com.apple.pasteboard.1"))
    labels[0, 4] = _refused(lambda: _shared("someone-elses"))
    labels[1, 0] = 1 if _looked_up("com.apple.system.opendirectoryd.libinfo") else 0  # what Python needs still works
    labels[1, 1] = 1 if _shared("psm_mine") else 0
    env = os.environ
    labels[1, 2] = 1 if env.get("HOME") == env.get("TMPDIR") and not any(
        k in env for k in ("MICROMIND_CATALOG", "OPENAI_API_KEY")) else 0
    labels[5:9, 5:9] = 1
    return labels
"""


def test_inside_it_reaches_no_other_process_descriptor_service_or_memory(home, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-canary-not-a-key")
    sleeper = subprocess.Popen(["/bin/sleep", "60"])
    canary = tmp_path / "canary"
    canary.write_bytes(b"canary bytes the app holds")
    held = os.open(canary, os.O_RDONLY)
    fd = 200
    os.dup2(held, fd, inheritable=True)  # held open and inheritable in the app: subprocess must not pass it on
    try:
        m = community(home, monkeypatch, PROBING.format(pid=sleeper.pid, fd=fd, canary=canary.read_bytes()))
        labels = run_once(m)
    finally:
        os.close(fd)
        os.close(held)
        sleeper.kill()
        sleeper.wait()
    refused = {"signal another process": labels[0, 0], "read a descriptor the app held": labels[0, 1],
               "reach LaunchServices": labels[0, 2], "reach the pasteboard": labels[0, 3],
               "share memory under another name": labels[0, 4]}
    assert all(int(v) == 3 for v in refused.values()), {k: int(v) for k, v in refused.items()}  # 7: it could
    assert int(labels[1, 0]) == 1, "user and group names (libinfo) still resolve: the probe ran"
    assert int(labels[1, 1]) == 1, "its own workers' shared memory still works"
    assert int(labels[1, 2]) == 1, "its home is its work folder, and nothing names the catalog or a key"


def test_its_install_check_runs_from_a_folder_the_sandbox_can_read(home, monkeypatch):
    """Acceptance 2026-10-13: the app runs from its checkout, which the sandbox doesn't let an entry read, and the install's
    import check inherited that folder, so torch's first getcwd() failed (PermissionError in os.path.abspath, the BioImage
    Model Zoo's affable-shark). Every sandboxed check now runs from the environment's own .check folder."""
    checkout = home / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)  # as the app is started: from a folder outside everything the sandbox lets it read
    runs = []
    real = envs._run
    monkeypatch.setattr(envs, "_run", lambda cmd, env, stop, what, **kw: runs.append((cmd, kw.get("cwd"))) or
                        real(cmd, env, stop, what, **kw))
    m = community(home, monkeypatch, PRYING.format(secret=str(home / "nothing")))
    confined = [(cmd, cwd) for cmd, cwd in runs if cmd[0] == sandbox.EXEC]
    assert confined, "its import check ran inside the sandbox"
    assert all(cwd and os.path.realpath(cwd).startswith(os.path.realpath(envs.folder(m))) for _cmd, cwd in confined)
    # What that folder is for: inside the same profile, getcwd() works from it and is refused from the checkout.
    cmd, cwd = confined[0]
    probe = [*cmd[:3], sys.executable, "-I", "-c", "import os; print(os.getcwd())"]
    inside = subprocess.run(probe, cwd=cwd, capture_output=True, text=True)
    outside = subprocess.run(probe, cwd=str(checkout), capture_output=True, text=True)
    assert inside.returncode == 0, inside.stderr[-400:]
    assert outside.returncode != 0 and "Operation not permitted" in outside.stderr


# Real wheels, built offline from uv's own cache (never downloaded here: skipped when a wheel isn't in the cache).
NUMPY, SCIPY_OK, SCIPY_OLD = "numpy==2.2.6", "scipy==1.16.3", "scipy==1.15.3"


@pytest.fixture(scope="module")
def wheels_env(tmp_path_factory):
    managed = sorted((Path.home() / ".local" / "share" / "uv" / "python").glob("cpython-3.13.*-macos-*"))
    if not managed:
        pytest.skip("no Python 3.13 of uv's own here (the test never downloads one)")
    try:
        uv = envs.uv()
    except envs.Failed:
        pytest.skip("uv isn't installed here")
    made = {}

    def make(*pins: str) -> Path:
        if pins not in made:
            d = tmp_path_factory.mktemp("wheels") / "env"
            env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "UV_OFFLINE": "1",
                   "UV_PYTHON_PREFERENCE": "only-managed"}
            for cmd in ([uv, "venv", "--quiet", "--python", managed[-1].name.split("-")[1], str(d)],
                        [uv, "pip", "install", "--quiet", "--only-binary", ":all:", "--python", str(d / "bin" / "python"),
                         *pins]):
                done = subprocess.run(cmd, env=env, capture_output=True, text=True)
                if done.returncode:
                    pytest.skip(f"{' '.join(pins)} isn't in uv's cache here (the test never downloads): "
                                f"{done.stderr.strip()[-200:]}")
            made[pins] = d
        return made[pins]

    return make


def confined_import(env_dir: Path, work: Path, statement: str) -> subprocess.CompletedProcess:
    """A statement in that environment's Python, inside the profile exactly as the app wraps an entry's (env_runner)."""
    work.mkdir(parents=True, exist_ok=True)
    exe = env_dir / "bin" / "python"
    cmd = sandbox.wrap([str(exe), "-I", "-c", statement], read=[env_dir, sandbox.python_home(exe), *sandbox.bootstrap()],
                       write=[work])
    return subprocess.run(cmd, cwd=work, env=sandbox.confine(envs.clean_env(None, env_dir), work), capture_output=True,
                          text=True)


SCIPY_ADAPTER = """
import numpy as np
from scipy import ndimage  # its compiled parts and the Fortran runtime its wheel bundles (scipy/.dylibs), loaded at import
import scipy.sparse.linalg  # PROPACK's, the first library macOS 27 refused in scipy 1.15.3's wheels


def segment(picture, params):
    labels, _ = ndimage.label(picture.mean(axis=2) < 128)
    return labels.astype(np.int32)
"""


def with_env(m, env_dir: Path) -> env_runner.Installed:
    """The community entry's adapter run by an environment of real wheels (its snapshot copied in, as Install keeps it)."""
    installed = env_runner.Installed(m, env_dir, {}, True)
    installed.entry.mkdir(parents=True, exist_ok=True)
    (installed.entry / "adapter.py").write_text(SCIPY_ADAPTER)
    return installed


def test_a_wheels_own_native_libraries_load_inside_the_sandbox(home, monkeypatch, wheels_env, tmp_path):
    """C4's builder, 2026-10-14: its first tries failed with scipy's compiled part refused. That was never the sandbox: a
    wheel's own native libraries (its .so files and the Fortran runtime it bundles) load inside the profile as the app
    makes it, imported directly and through the worker that runs an adapter."""
    env_dir = wheels_env(NUMPY, SCIPY_OK)
    m = community(home, monkeypatch, PRYING.format(secret="/nonexistent"))  # its stand-in install needs the tests' Python
    monkeypatch.delenv("MICROMIND_SANDBOX_READ")  # from here, the profile exactly as the app makes it
    done = confined_import(env_dir, tmp_path / "work", "import scipy.sparse.linalg, scipy.ndimage, scipy.linalg; print('ok')")
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr[-1500:]
    picture = np.full((12, 12, 3), 200, np.uint8)
    picture[4:8, 4:8] = 0
    client = env_runner.Client(with_env(m, env_dir))
    try:
        labels = decode_labels(client.run(picture)["labels"])
    finally:
        client.stop()
    assert int((labels == 1).sum()) == 16, "scipy's compiled code ran inside the sandbox"


def test_a_library_macos_refuses_is_said_by_package_and_reason_not_closed(home, monkeypatch, wheels_env, tmp_path):
    """scipy 1.15.3's wheels: macOS 27's loader refuses their compiled parts everywhere (a zero-fill thread-local section
    with a file offset), inside the sandbox or not; elsewhere they load inside it too. Inside or out is the same outcome,
    and an adapter that can't import says so by its package, version and macOS's reason, never "didn't start (closed)"."""
    env_dir = wheels_env(NUMPY, SCIPY_OLD)
    m = community(home, monkeypatch, PRYING.format(secret="/nonexistent"))
    monkeypatch.delenv("MICROMIND_SANDBOX_READ")
    statement = "import scipy.sparse.linalg; print('ok')"
    inside = confined_import(env_dir, tmp_path / "work", statement)
    outside = subprocess.run([str(env_dir / "bin" / "python"), "-I", "-c", statement], cwd=tmp_path, capture_output=True,
                             text=True, env=envs.clean_env(None, env_dir))
    assert inside.returncode == outside.returncode, ("the sandbox changed whether it loads", inside.stderr[-1500:])
    client = env_runner.Client(with_env(m, env_dir))
    try:
        if outside.returncode == 0:  # a macOS that loads them: they load inside too
            client.run(np.full((12, 12, 3), 200, np.uint8))
            return
        with pytest.raises(env_runner.Failed) as caught:
            client.run(np.full((12, 12, 3), 200, np.uint8))
    finally:
        client.stop()
    said = str(caught.value)
    assert "(closed)" not in said and "stopped before it was ready" in said, said
    assert "macOS couldn't load scipy 1.15.3's compiled part" in said, said
    reason = outside.stderr.strip().splitlines()[-1].rsplit("(", 1)[-1].rstrip(")")
    assert reason in said and len(said) < 400, said  # macOS's own reason, short enough for any record


def test_an_adapter_that_fails_to_import_says_why(home, monkeypatch):
    """Its worker ends before its hello: its own last line is the reason, not "(closed)"."""
    m = community(home, monkeypatch, PRYING.format(secret="/nonexistent"))
    installed = env_runner.in_use(m)
    (installed.entry / "broken.py").write_text("import not_a_package_here\n")  # as an adapter's import fails at start
    client = env_runner.Client(dataclasses.replace(installed, m=dataclasses.replace(m, adapter="broken:segment")))
    try:
        with pytest.raises(env_runner.Failed) as caught:
            client.run(np.zeros((8, 8, 3), np.uint8))
    finally:
        client.stop()
    assert str(caught.value) == ("Dark spots's adapter stopped before it was ready: "
                                 "ModuleNotFoundError: No module named 'not_a_package_here'"), str(caught.value)
