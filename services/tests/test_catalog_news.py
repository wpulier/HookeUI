"""New capabilities arrive by themselves (orchestrator/catalog_news.py; docs/design/2026-10-13-capabilities-for-everyone.md
C1): what someone merges into the reviewed catalog reaches this Mac at the next check, made by the app on its own once a
day, and is offered as new (its picture, what it does, who added it, its badge) until it is turned on, seen or "not for
me". Nothing new downloads, installs, runs or turns on by itself. A scratch upstream repository (tests/test_channels)."""
import time

from fastapi.testclient import TestClient

from gpu_worker import manifests as catalog
from orchestrator import catalog_news, channels, envs, readiness
from orchestrator.paths import Paths
from orchestrator.server import create_app
from tests.test_channels import _git, home  # noqa: F401 - the scratch upstream and catalog
from tests.test_env_runner import ADAPTER, ENTRY

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")
CONTRIBUTED = ('tags = ["diatoms", "plankton"]\ncontributed_by = { name = "Maya Okafor", link = "https://example.org/maya" }\n'
               'example = "example.png"\n')


def contribute(home, entry: str = "dark-spots") -> str:
    """Maya's entry merged upstream: its manifest with tags, her name and its picture, its adapter."""
    folder = home / "upstream" / "catalog" / "models" / entry
    folder.mkdir(parents=True, exist_ok=True)
    text = ENTRY.format().replace('id = "dark-spots"', f'id = "{entry}"').replace("dark-spots", entry)
    (folder / "model.toml").write_text(text.replace("[[implements]]", CONTRIBUTED + "\n[[implements]]", 1))
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(ADAPTER)
    (folder / "example.png").write_bytes(PNG)
    _git("add", ".", cwd=home / "upstream")
    _git("commit", "-q", "-m", f"Catalog: {entry}", cwd=home / "upstream")
    return _git("rev-parse", "HEAD", cwd=home / "upstream")


def test_a_capability_merged_upstream_is_offered_as_new_and_never_turned_on_by_itself(home):
    assert catalog_news.new() == [], "a fresh install is not new from top to bottom"
    contribute(home)
    r = channels.update()
    assert r["added"] == ["dark-spots"]
    found = catalog_news.new(about=["Diatoms on a slide"])
    assert [n["key"] for n in found] == ["dark-spots"]
    n = found[0]
    assert (n["by"], n["badge"], n["trusted"], n["relevant"]) == ("Maya Okafor", "reviewed", True, True)
    assert n["example"] == "/api/catalog/example/dark-spots" and n["tags"] == ["diatoms", "plankton"]
    assert not catalog_news.new(about=["blood smear"])[0]["relevant"]
    # Offered, never done: its one step is still the user's, and nothing was built.
    assert [s.kind for s in readiness.model("dark-spots", {}).steps] == ["install"]
    assert envs.state(catalog.get("dark-spots"))["state"] != "ready"
    assert catalog_news.new(ready=["dark-spots"]) == [], "once on, no longer offered as new"
    assert catalog_news.seen() == ["dark-spots"] and catalog_news.new() == []


def test_not_for_me_is_forever_and_new_ends_after_a_month(home):
    catalog_news.state()  # this Mac's catalog as it was (the app reads it at start, before any check)
    contribute(home, "dark-spots")
    contribute(home, "light-spots")
    channels.update()
    assert {n["key"] for n in catalog_news.new()} == {"dark-spots", "light-spots"}
    catalog_news.dismiss("dark-spots")
    assert [n["key"] for n in catalog_news.new()] == ["light-spots"]
    data = catalog_news.state()
    data["known"]["light-spots"]["seen"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 40 * 86400))
    catalog_news._save(data, __import__("os").environ)
    assert catalog_news.new() == []


def test_a_check_is_due_once_a_day_and_never_from_tests_or_the_sim(home, monkeypatch):
    assert not catalog_news.automatic() and not catalog_news.due(), "conftest (and the sim harness) turn it off"
    monkeypatch.setenv("MICROMIND_CATALOG_AUTO", "1")
    assert catalog_news.due()
    catalog_news.record_check({"ok": True, "says": "Nothing new"})
    assert not catalog_news.due() and catalog_news.due(now=time.time() + catalog_news.EVERY_S + 1)
    assert catalog_news.last_check()["says"] == "Nothing new"


def test_the_app_looks_by_itself_takes_no_code_and_get_it_fetches_on_the_users_press(home, tmp_path):
    """App.peek_catalog (the daily look): each entry's manifest and picture only, never its adapter or lockfile, nothing
    listed, installed or run; New for you offers it with Get it, which fetches that same commit in full."""
    commit = contribute(home)
    client = TestClient(create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path / "app")))
    with client:
        app = client.app.state.app
        r = client.portal.call(app.peek_catalog)
        assert r["commit"] == commit
        files = sorted(p.name for p in (channels.peek_dir("reviewed", commit)).rglob("*") if p.is_file())
        assert files == ["example.png", "model.toml"], "a look takes no code: no adapter, no lockfile"
        assert catalog.get("dark-spots") is None and not (home / "catalog" / "channels" / "reviewed").exists()
        news = app.capabilities_wire()["setup"]["news"]
        item = next(n for n in news["new"] if n["key"] == "dark-spots")
        assert (item["fetched"], item["by"], item["commit"]) == (False, "Maya Okafor", commit)
        picture = client.get("/api/catalog/example/dark-spots")
        assert picture.status_code == 200 and picture.content == PNG, "its picture, from what was looked at"
        assert app.downloads.wire() == [] and app.installs.wire() == []
        from orchestrator.commands import catalog_news_get

        got = client.portal.call(catalog_news_get, app, None, {"type": "catalog-news-get", "key": "dark-spots",
                                                            "commit": item['commit'], "offer": item['offer']})
        assert got["ok"] and got["commit"] == commit, got
        m = catalog.get("dark-spots")
        assert m is not None and (catalog.entry_folder(m) / "adapter.py").is_file(), "its code arrives on the press"
        assert [s.kind for s in readiness.model("dark-spots", {}).steps] == ["install"], "still the user's to turn on"
        item = next(n for n in app.capabilities_wire()["setup"]["news"]["new"] if n["key"] == "dark-spots")
        assert item["fetched"] is True
        assert client.get("/api/catalog/example/dinov2-small").status_code == 404, "no picture of its own"
        assert client.get("/api/catalog/example/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_an_example_must_be_a_picture_beside_its_manifest(home):
    folder = catalog.ROOT / "dark-spots"
    folder.mkdir()
    text = ENTRY.format()
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(ADAPTER)
    for example, ok in (('"example.png"', True), ('"../model.toml"', False), ('"notes.txt"', False)):
        (folder / "example.png").write_bytes(PNG)
        (folder / "notes.txt").write_text("x")
        (folder / "model.toml").write_text(text.replace("[[implements]]", f"example = {example}\n\n[[implements]]", 1))
        catalog.manifests.cache_clear()
        assert (catalog.get("dark-spots") is not None) is ok, catalog.problems().get("dark-spots")
    (folder / "model.toml").write_text(text.replace("[[implements]]", 'tags = ["Not Plain!"]\n\n[[implements]]', 1))
    catalog.manifests.cache_clear()
    assert "`tags` are at most 12 plain lowercase words" in " ".join(catalog.problems()["dark-spots"])
