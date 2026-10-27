"""The Mac app's release checks that publish nothing (packaging/macos/README.md "Channels"): the next build number,
higher than every channel's published build, and the check that a built feed and dmg are signed by the channel's own
update key."""
import base64
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MACOS = Path(__file__).resolve().parents[2] / "packaging/macos"
spec = importlib.util.spec_from_file_location("release_number", MACOS / "release_number.py")
release_number = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_number)


def feed(*builds: int, signed: bytes = b"") -> bytes:
    items = "".join(f"<item><title>HookeUI</title><sparkle:version>{b}</sparkle:version>"
                    f"<enclosure url=\"https://example.org/{b}.dmg\" length=\"1\"/></item>" for b in builds)
    return (f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0" '
            f'xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel>{items}</channel></rss>'
            ).encode() + signed


def test_the_next_build_exceeds_every_channels_published_build():
    stable, beta = feed(3412), feed(3390, 3460, signed=b"<!-- sparkle-signatures:\nedSignature: x\nlength: 1\n-->")
    assert release_number.next_build(3400, [stable, beta]) == 3461  # beta's 3460 though stable is the one promoted
    assert release_number.next_build(3500, [stable, beta]) == 3500  # the commit count is the floor, as before
    assert release_number.next_build(3400, [None, None]) == 3400  # no channel has published yet


def test_a_feed_that_cannot_be_read_gives_no_number(tmp_path):
    good, broken, missing = tmp_path / "good.xml", tmp_path / "broken.xml", tmp_path / "missing.xml"
    good.write_bytes(feed(3412))
    broken.write_bytes(feed(3412).replace(b"3412", b"next"))
    run = lambda *feeds: subprocess.run([sys.executable, str(MACOS / "release_number.py"), "--commit-count", "3000",
                                         *[f for path in feeds for f in ("--feed", str(path))]],
                                        capture_output=True, text=True)
    ok = run(good, missing)
    assert ok.returncode == 0 and ok.stdout.strip() == "3413" and "no release yet" in ok.stderr
    refused = run(good, broken)
    assert refused.returncode == 1 and not refused.stdout and "No number given" in refused.stderr
    assert run().returncode != 0  # every channel's feed is required


@pytest.fixture(scope="module")
def verifier(tmp_path_factory):
    if sys.platform != "darwin" or not shutil.which("swiftc"):
        pytest.skip("needs macOS's CryptoKit and swiftc")
    out = tmp_path_factory.mktemp("verify") / "verify_update"
    subprocess.run(["swiftc", "-O", str(MACOS / "verify_update.swift"), "-o", str(out)], check=True,
                   capture_output=True, timeout=300)
    return out


def signed_release(tmp_path: Path, key, dmg_bytes: bytes = b"payload " * 64):
    """A feed and dmg as build.sh leaves them: the enclosure signs the dmg, and the feed's signature is a comment
    after the bytes it signs (sign_update; checked against Sparkle 2.10.0's sign_update when this was written)."""
    dmg = tmp_path / "HookeUI-Beta-0.2.0.dmg"
    dmg.write_bytes(dmg_bytes)
    signature = base64.b64encode(key.sign(dmg_bytes)).decode()
    body = feed(3600).replace(b'length="1"/>', f'length="{len(dmg_bytes)}" sparkle:edSignature="{signature}"/>'.encode())
    block = f"<!-- sparkle-signatures:\nedSignature: {base64.b64encode(key.sign(body)).decode()}\nlength: {len(body)}\n-->"
    appcast = tmp_path / "appcast.xml"
    appcast.write_bytes(body + block.encode())
    return appcast, dmg


def public(key, path: Path) -> Path:
    from cryptography.hazmat.primitives import serialization as s

    path.write_text(base64.b64encode(key.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)).decode())
    return path


def test_a_release_signed_with_another_channels_key_is_refused(verifier, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    beta, stable = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    appcast, dmg = signed_release(tmp_path, beta)
    check = lambda key: subprocess.run([str(verifier), str(key), str(appcast), str(dmg)], capture_output=True, text=True)
    assert check(public(beta, tmp_path / "beta.pub")).returncode == 0
    wrong = check(public(stable, tmp_path / "stable.pub"))
    assert wrong.returncode == 1 and "not signed by this channel's key" in wrong.stderr
    dmg.write_bytes(dmg.read_bytes()[:-1] + b"!")  # same length, other bytes
    changed = check(tmp_path / "beta.pub")
    assert changed.returncode == 1 and "the dmg is not signed" in changed.stderr
    appcast.write_bytes(appcast.read_bytes().split(b"<!-- sparkle-signatures:")[0])
    unsigned = check(tmp_path / "beta.pub")
    assert unsigned.returncode == 1 and "not signed" in unsigned.stderr
