"""Tests start with isolated app, asset and model stores and no account credentials.
Individual tests may install synthetic keys and inputs after this collection boundary."""
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

os.environ.pop('HOOKEUI_RESEARCH_EXTENSION', None)

_TMP = tempfile.mkdtemp(prefix="micromind-test-")
# Forced, not defaulted: a shell that exports any of these (pointing at real data or keys, or
# setting a cap or engine) must not reach the tests.
for _name in [n for n in os.environ if n.startswith("MICROMIND_")]:
    del os.environ[_name]
os.environ["MICROMIND_HOME"] = _TMP
os.environ["MICROMIND_ASSETS"] = os.path.join(_TMP, "assets")
os.environ["MICROMIND_RECORDINGS_DIR"] = os.path.join(_TMP, "recordings")
os.environ["MICROMIND_KEYS_FILE"] = "/nonexistent"
# Models' files (gpu_worker/model_files.py): every model counts as downloaded ("standin": the stand-in workers load no
# real model), so what this Mac has downloaded never changes a golden; a test of a model not downloaded names its own
# empty folder. The models folder is a temporary one, never this Mac's ~/MicroscopeMind/catalog.
os.environ["MICROMIND_MODEL_FILES"] = "standin"
os.environ["MICROMIND_CATALOG"] = os.path.join(_TMP, "catalog")
# Never this Mac's Keychain (keychain.py): a test that needs saved keys gives keychain._store a stand-in.
os.environ["MICROMIND_KEYCHAIN"] = "off"
# The library's looks (library_looks.py) never run on their own here: a test of them calls `step` itself.
os.environ["MICROMIND_LIBRARY_LOOKS"] = "0"
os.environ["MICROMIND_CATALOG_AUTO"] = "0"  # never checked online from a test (catalog_news.py)
# The stand-in environments' Python wraps this one: inside macOS's sandbox (sandbox.py) it may read this Python's install.
os.environ["MICROMIND_SANDBOX_READ"] = os.pathsep.join([sys.prefix, str(Path(sys.executable).resolve().parents[1])])

# Catalog metadata only: no model, adapter or key store is opened by this reader.
# A provider key inherited from the shell must not make a contributor's test pass.
from gpu_worker.manifests import services  # noqa: E402

for _service in services().values():
    os.environ.pop(_service.key, None)


@pytest.fixture(autouse=True)
def _isolated_provider_credentials(monkeypatch):
    """App-loaded synthetic keys and their origin marker belong to one test.

    Register absent variables too: load_keys writes directly to os.environ,
    so delenv alone cannot restore a variable that was absent at setup.
    """
    from orchestrator.keys import APPLIED, KNOWN_KEYS

    for name in (*KNOWN_KEYS, APPLIED):
        monkeypatch.setenv(name, '')
        monkeypatch.delenv(name)


@pytest.fixture
def research_key(_isolated_provider_credentials, monkeypatch):
    """A set-up app whose researchers' model (GPT-6.1 Sol by default) has its key, so starting a real-model researcher is
    not refused for want of it (research/manager.engine_key_refusal; the refusal itself: test_research_engine_key.py).
    After `_isolated_provider_credentials`, which clears every real key."""
    monkeypatch.setenv('OPENAI_API_KEY', 'test-openai-key')


@pytest.fixture(autouse=True)
def _isolated_catalog(tmp_path, monkeypatch):
    """Unrelated profiles must not register saved work in a worker-wide catalog.

    Keep the collection-time isolation above; function fixtures may explicitly
    override this root when testing shared profiles or prepared environments.
    No retention records are removed and production limits remain unchanged.
    """
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'catalog'))


# --- Wall-clock bounds: checked by make test-timing, not by the gate --------------------------------------------------
# A bound on how long something took ("returns at once": under 0.5 s) fails when the Mac is loaded (other agents, the
# suite's own workers), not when the code is wrong, and a failure there costs a rerun. Two ways, by what depends on time:
# - a bound on a measured time is stated with the `wall_clock` fixture, `wall_clock.within(took, 0.5, "starting it")`,
#   which marks the test `wall_clock`: the gate (make test) runs it with every other check and leaves the bound out;
# - a test that only works when something takes no longer than a tiny limit (a child started within 0.15 s) is marked
#   `timing`: the gate leaves the whole test out.
# make test-timing runs both, one at a time, with the bounds on (--timing). A bound that only catches a hang (5 s or more
# for what takes milliseconds) stays an assert.


def pytest_addoption(parser):
    parser.addoption("--timing", action="store_true", help="check the `wall_clock` fixture's bounds (make test-timing)")


def pytest_configure(config):
    config.addinivalue_line("markers", "wall_clock: has bounds on measured time, checked only with --timing")
    config.addinivalue_line("markers", "timing: works only when the Mac is not loaded; left out of make test")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """A test that takes the `wall_clock` fixture is marked `wall_clock`, before `-m` selects."""
    for item in items:
        if "wall_clock" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.wall_clock)


class WallClock:
    def __init__(self, checked: bool):
        self.checked = checked

    def within(self, took: float, limit: float, what: str = "it") -> None:
        """`took` (seconds) is under `limit`, when wall-clock bounds are checked (--timing)."""
        if self.checked:
            assert took < limit, f"{what} took {took:.3f} s, over its {limit} s"


@pytest.fixture
def wall_clock(request) -> WallClock:
    return WallClock(request.config.getoption("--timing"))


@pytest.fixture
def new_york():
    """Local dates near midnight and across a clock change are the zone's: one with daylight saving, wherever the
    machine is."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TZ", "America/New_York")
        time.tzset()
        yield
    time.tzset()


@pytest.fixture(autouse=True)
def _background_threads_at_normal_priority(monkeypatch):
    """The app lowers its install queue, downloads, registrations and thumbnails to macOS's background QoS
    (thumbnails.background_priority: efficiency cores only), which a loaded Mac starves: at load 50-100 an install queue
    whose install raised at once made no progress in 60 s. Here those threads run at normal priority; the processes it
    starts with `taskpolicy -b` are unchanged, and test_thumbnails calls the real one."""
    from orchestrator import thumbnails

    monkeypatch.setattr(thumbnails, "background_priority", lambda: None)
