"""Automatic discovery stays metadata-only across an external Git process that ignores blob filtering."""
import os
from pathlib import Path
import sys

import pytest

from orchestrator import channels
from tests.test_channels import ENTRY, _git, home, publish


def test_an_unfiltered_server_cannot_send_code_during_automatic_discovery(home, monkeypatch):
    publish(home, "dark-spots", ENTRY.format(), adapter="SENTINEL: code the user has not requested")
    commands = home / "bin"
    commands.mkdir()
    transferred = home / "unrequested-code-transferred"
    program = commands / "git"
    # Git's documented successful-warning behavior is reproduced at the process boundary. Every actual Git read
    # uses the scratch origin, never the network. The marker is made only after a real unfiltered fetch succeeds.
    program.write_text(f'''#!{sys.executable}
import os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if args[0] == "ls-remote": args[1] = {str(home / 'upstream')!r}
if args[:3] == ["remote", "add", "origin"]: args[3] = {str(home / 'upstream')!r}
if args[0] == "fetch":
    args = [x for x in args if not x.startswith("--filter=")]
    result = subprocess.run(["/usr/bin/git", *args])
    if result.returncode == 0:
        Path({str(transferred)!r}).write_text("adapter bytes arrived in the fetched Git objects")
        print("warning: filtering not recognized by server, ignoring", file=sys.stderr)
    sys.exit(result.returncode)
os.execv("/usr/bin/git", ["git", *args])
''')
    program.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("MICROMIND_REVIEWED_CHANNEL", "https://git.example.test/catalog.git")
    try:
        channels.peek()
    except channels.CantDo:
        pass  # unsupported metadata transport must refuse before receiving blobs
    assert not transferred.exists(), "filtering the archive after a fetch cannot undo downloading code"


def test_automatic_preview_rejects_a_large_image_before_publishing_it(home):
    publish(home, "dark-spots", ENTRY.format())
    picture = home / "upstream" / "catalog" / "models" / "dark-spots" / "huge.png"
    picture.write_bytes(b"x" * (12 * 1024 * 1024))
    _git("add", ".", cwd=home / "upstream")
    _git("commit", "-q", "-m", "Oversized preview", cwd=home / "upstream")
    with pytest.raises(channels.CantDo, match="(?:large|size|limit|bound)"):
        channels.peek()
    assert not any(p.name == 'huge.png' for p in (home / 'catalog' / 'news').rglob('*'))


def test_github_preview_reads_only_pinned_metadata_and_checks_its_identity(home, monkeypatch):
    import hashlib
    import httpx

    from orchestrator import catalog_metadata

    commit = 'a' * 40
    manifest = ENTRY.format().encode()
    blob = hashlib.sha1(f'blob {len(manifest)}\0'.encode() + manifest).hexdigest()
    asked = []
    corrupt = False

    def respond(request):
        asked.append(str(request.url))
        if request.url.host == 'api.github.com':
            return httpx.Response(200, json={'tree': [
                {'path': 'catalog/models/dark-spots/model.toml', 'mode': '100644', 'size': len(manifest), 'sha': blob},
                {'path': 'catalog/models/dark-spots/adapter.py', 'mode': '100644', 'size': 99, 'sha': 'b' * 40},
            ], 'truncated': False})
        assert request.url.path == f'/someone/catalog/{commit}/catalog/models/dark-spots/model.toml'
        return httpx.Response(200, content=manifest[:-1] + b'x' if corrupt else manifest)

    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    target = home / 'preview'
    assert catalog_metadata.fetch('https://github.com/someone/catalog.git', commit, target, 'catalog/models') == 1
    assert (target / 'dark-spots/model.toml').read_bytes() == manifest
    assert len(asked) == 2 and not any('adapter.py' in url for url in asked)
    corrupt = True
    with pytest.raises(channels.CantDo, match='identity'):
        catalog_metadata.fetch('https://github.com/someone/catalog.git', commit, home / 'corrupt', 'catalog/models')
    assert not (home / 'corrupt').exists()


def test_uppercase_picture_extensions_obey_the_same_preview_count_limit(home, monkeypatch):
    import httpx
    from orchestrator import catalog_metadata

    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={'tree': [
            {'path': f'catalog/models/m{i}/preview.PNG', 'mode': '100644', 'size': 0, 'sha': 'b' * 40}
            for i in range(65)] + [
            {'path': 'catalog/models/m0/model.toml', 'mode': '100644', 'size': 0, 'sha': 'b' * 40}], 'truncated': False})
    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(channels.CantDo, match='count limit'):
        catalog_metadata.fetch('https://github.com/someone/catalog.git', 'a' * 40, home / 'preview', 'catalog/models')
    assert len(requests) == 1, 'reject the listing before requesting any blob'


def test_local_replace_refs_cannot_rewrite_the_pinned_preview(home):
    from orchestrator import catalog_metadata

    publish(home, 'dark-spots', ENTRY.format())
    repo = home / 'upstream'
    original = _git('rev-parse', 'HEAD', cwd=repo).strip()
    changed = ENTRY.format().replace('Dark spots', 'Replaced description')
    publish(home, 'dark-spots', changed)
    replacement = _git('rev-parse', 'HEAD', cwd=repo).strip()
    _git('replace', original, replacement, cwd=repo)
    dest = home / 'preview'
    catalog_metadata.fetch(str(repo), original, dest, 'catalog/models')
    assert (dest / 'dark-spots' / 'model.toml').read_text() == ENTRY.format()
