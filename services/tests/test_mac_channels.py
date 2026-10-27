"""The Mac app's release channels (packaging/macos/channels.json, channels.py; packaging/macos/README.md "Channels").

Stable and Beta are two apps from one source. Installed side by side, Beta must never share or overwrite Stable's data,
runtime, keys, downloads, processes or app bundle, and Stable must never read Beta's feed. Stable's identity is what
every installed copy already has, so it must not change underneath them.
"""
import copy
import importlib.util
import json
import os
import plistlib
import re
import subprocess
from pathlib import Path

import pytest

from orchestrator import keychain, paths

REPO = Path(__file__).resolve().parents[2]
MACOS = REPO / "packaging/macos"
MAIN = MACOS / "HookeUI/main.swift"
spec = importlib.util.spec_from_file_location("channels", MACOS / "channels.py")
channels = importlib.util.module_from_spec(spec)
spec.loader.exec_module(channels)

NORMAL_FEED = "https://github.com/wpulier/hookeui.org/releases/latest/download/appcast.xml"


def render(template: str, name: str) -> dict:
    text = channels.info_plist((MACOS / "HookeUI" / template).read_text(), name, version="0.2.0", build="3600",
                               feed="https://example.org/appcast.xml", edkey="public-key-fixture")
    assert "@" not in re.sub(r"<string>[^<]*</string>", "", text), "a placeholder was left in " + template
    return plistlib.loads(text.encode())


def test_stable_keeps_the_identity_every_installed_copy_has():
    stable = channels.channel("stable")
    assert stable["bundleId"] == "org.hookeui.HookeUI" and stable["name"] == "HookeUI"
    assert stable["cameraBundleId"] == "org.hookeui.camera"
    assert (stable["supportFolder"], stable["dataFolder"], stable["keychainService"]) == (
        "HookeUI", "MicroscopeMind", "MicroscopeMind")
    assert (stable["orchestratorPort"], stable["cameraPort"]) == (8770, 8765)
    assert stable["feedURL"] == NORMAL_FEED and stable["releases"] == "wpulier/hookeui.org"
    assert stable["edPublicKey"] == "sparkle-public-key.txt" and not stable["isolated"]
    info = render("Info.plist", "stable")
    assert info["CFBundleIdentifier"] == "org.hookeui.HookeUI"
    assert info["CFBundleName"] == info["CFBundleDisplayName"] == "HookeUI"
    assert info["NSCameraUsageDescription"] == "HookeUI reads your microscope camera for the live view."
    # A keys file on the Desktop: macOS asks for it instead of refusing silently (ticket 62, F-L8-3)
    assert info["NSDesktopFolderUsageDescription"] == "HookeUI reads your API keys file on the Desktop."
    assert render("Camera-Info.plist", "stable")["CFBundleIdentifier"] == "org.hookeui.camera"


def test_beta_shares_nothing_that_lets_it_touch_stable():
    stable, beta = channels.channel("stable"), channels.channel("beta")
    for field in channels.DISTINCT:
        assert beta[field] != stable[field], field
    assert beta["isolated"]
    assert {beta["orchestratorPort"], beta["cameraPort"]}.isdisjoint({stable["orchestratorPort"], stable["cameraPort"]})
    assert beta["feedURL"] not in channels.LEGACY_FEEDS and beta["releases"] != stable["releases"]
    info, camera = render("Info.plist", "beta"), render("Camera-Info.plist", "beta")
    assert info["CFBundleIdentifier"] == "org.hookeui.HookeUI.beta" and info["CFBundleName"] == "HookeUI Beta"
    assert camera["CFBundleIdentifier"] == "org.hookeui.camera.beta"
    assert info["HookeUIChannel"] == "beta" and info["HookeUIIsolated"] is True
    assert (info["HookeUISupportFolder"], info["HookeUIDataFolder"], info["HookeUIKeychainService"]) == (
        beta["supportFolder"], beta["dataFolder"], beta["keychainService"])


def test_every_channel_keeps_the_update_policy():
    for name in channels.load():
        policy = render("Info.plist", name)
        assert policy["SUEnableAutomaticChecks"] and policy["SURequireSignedFeed"]
        assert policy["SUVerifyUpdateBeforeExtraction"]
        assert not policy["SUAllowsAutomaticUpdates"] and not policy["SUAutomaticallyUpdate"]
        assert policy["SUFeedURL"] == "https://example.org/appcast.xml"
        assert policy["SUPublicEDKey"] == "public-key-fixture"


@pytest.mark.parametrize("change, said", [
    (lambda c: c["beta"].update(dataFolder="MicroscopeMind"), "share dataFolder"),
    (lambda c: c["beta"].update(supportFolder="HookeUI"), "share supportFolder"),
    (lambda c: c["beta"].update(bundleId="org.hookeui.hookeui"), "bundle identifier"),
    (lambda c: c["beta"].update(keychainService="MicroscopeMind"), "share keychainService"),
    (lambda c: c["beta"].update(releases="wpulier/hookeui.org", feedURL=NORMAL_FEED), "share releases"),
    (lambda c: c["beta"].update(feedURL="https://hookeui.org/beta/appcast.xml"), "latest release's appcast"),
    (lambda c: c["beta"].update(cameraPort=8770), "ports differ"),
    (lambda c: c["beta"].update(isolated=False), "isolated"),
    (lambda c: c["beta"].update(dataFolder="../MicroscopeMind"), "one folder name"),
    (lambda c: c["beta"].pop("edPublicKey"), "missing"),
    (lambda c: c.pop("stable"), "no stable"),
])
def test_a_channel_that_would_share_with_another_is_refused(change, said):
    raw = copy.deepcopy(json.loads(channels.CHANNELS.read_text()))
    change(raw)
    with pytest.raises(channels.ChannelError, match=said):
        channels.validate(raw)


def test_the_launcher_takes_every_folder_port_and_name_from_its_channel():
    source = MAIN.read_text()
    template = (MACOS / "HookeUI/Info.plist").read_text()
    for key in re.findall(r"<key>(HookeUI[A-Za-z]+)</key>", template):
        assert f'info["{key}"]' in source, key
    for fixed in ('Application Support/HookeUI"', 'Logs/HookeUI"', 'appendingPathComponent("MicroscopeMind")',
                  "choosePort(8770", "choosePort(8765", 'appendingPathComponent("HookeUI.app")',
                  'let appName = "HookeUI"'):
        assert fixed not in source, fixed
    for variable in ("MICROMIND_SAVES", "MICROMIND_ASSETS", "MICROMIND_DATASETS_DIR", "MICROMIND_KEYCHAIN_SERVICE",
                     "MICROMIND_BLACKMAGIC_PORT", "MICROMIND_CAMERA_SETTINGS_PORT"):
        assert f'env["{variable}"]' in source, variable
    isolated = source[source.index("if channel.isolated {"):]
    isolated = isolated[:isolated.index("\n        }\n")]
    for variable in ("MICROMIND_KEYS_FILE", "HF_HOME", "TORCH_HOME", "XDG_CACHE_HOME", "CELLPOSE_LOCAL_MODELS_PATH",
                     "MICROSAM_CACHEDIR"):
        assert f'env["{variable}"]' in isolated, variable


def test_a_beta_launch_environment_keeps_every_save_in_its_own_folder(tmp_path):
    """What main.swift's baseEnvironment gives a Beta: every path the services write is under its own folders."""
    home = tmp_path / "home"
    beta = channels.channel("beta")
    data = home / beta["dataFolder"]
    support = home / "Library/Application Support" / beta["supportFolder"]
    environ = {"HOME": str(home), "MICROMIND_SAVES": str(data), "MICROMIND_ASSETS": str(data),
               "MICROMIND_DATASETS_DIR": str(data / "datasets"), "MICROMIND_KEYS_FILE": str(data / "micromind.txt"),
               "MICROMIND_KEYCHAIN_SERVICE": beta["keychainService"], "HF_HOME": str(support / "caches/huggingface")}
    layout = paths.Paths.from_env(environ)
    for field in ("home", *paths.OVERRIDES):
        assert getattr(layout, field).is_relative_to(data), field
    for where in (paths.envs(environ), paths.tools(environ), paths.finetuned(environ), paths.assets_root(environ)):
        assert where.is_relative_to(data)
    stable = channels.channel("stable")
    assert not data.is_relative_to(home / stable["dataFolder"])


def test_the_keychain_service_follows_the_channel(monkeypatch):
    saved = {}

    class Store:
        def get_password(self, service, name):
            return saved.get((service, name))

        def set_password(self, service, name, value):
            saved[(service, name)] = value

        def delete_password(self, service, name):
            saved.pop((service, name), None)

    monkeypatch.setattr(keychain, "_store", Store())
    monkeypatch.delenv("MICROMIND_KEYCHAIN_SERVICE", raising=False)
    keychain.forget()
    assert keychain.service() == "MicroscopeMind"
    keychain.set("GEMINI_API_KEY", "stable-canary")
    monkeypatch.setenv("MICROMIND_KEYCHAIN_SERVICE", "MicroscopeMind Beta")
    keychain.forget()
    assert keychain.get("GEMINI_API_KEY") is None  # Beta does not read Stable's saved key
    keychain.set("GEMINI_API_KEY", "beta-canary")
    assert saved == {("MicroscopeMind", "GEMINI_API_KEY"): "stable-canary",
                     ("MicroscopeMind Beta", "GEMINI_API_KEY"): "beta-canary"}
    keychain.forget()


def build(tmp_path, **env) -> subprocess.CompletedProcess:
    skip = ("RELEASE_CHANNEL", "CHANNEL", "OUT", "TOOLS", "ED_PUBLIC", "FEED_URL", "RELEASES")
    environ = {k: v for k, v in os.environ.items() if not k.startswith(skip)}
    return subprocess.run(["bash", str(MACOS / "build.sh"), "0.2.0", "HEAD"], cwd=REPO, capture_output=True, text=True,
                          env={**environ, "OUT": str(tmp_path / "out"), "TOOLS": str(tmp_path / "tools"), **env},
                          timeout=60)


def test_a_build_of_an_unknown_channel_or_without_its_update_key_stops_before_building(tmp_path):
    unknown = build(tmp_path, RELEASE_CHANNEL="nightly")
    assert unknown.returncode != 0 and "no channel 'nightly'" in unknown.stderr
    keyless = build(tmp_path, RELEASE_CHANNEL="beta", ED_PUBLIC_KEY_FILE=str(tmp_path / "missing.txt"))
    assert keyless.returncode == 1 and "No update key for the beta channel" in keyless.stderr
    assert not (tmp_path / "out").exists() and not (tmp_path / "tools").exists()


def test_a_beta_builds_apart_from_stable():
    script = (MACOS / "build.sh").read_text()
    assert 'OUT="${OUT:-$HERE/build-$CHANNEL}"' in script and 'APP="$OUT/$CHANNEL_NAME.app"' in script
    assert channels.environment("beta")["CHANNEL_FILE_STEM"] == "HookeUI-Beta"
    assert channels.environment("stable")["CHANNEL_FILE_STEM"] == "HookeUI"


@pytest.mark.parametrize("name, url", [
    ("beta", NORMAL_FEED),
    ("beta", "https://github.com/wpulier/hookeui.org/releases/download/v0.2.0/appcast.xml"),
    ("stable", "https://github.com/wpulier/hookeui-beta/releases/latest/download/appcast.xml"),
    ("stable", "https://hookeui.org/beta/appcast.xml"),
])
def test_no_build_reads_another_channels_feed(name, url, tmp_path):
    with pytest.raises(channels.ChannelError):
        channels.check_feed(name, url)
    crossed = build(tmp_path, RELEASE_CHANNEL=name, FEED_URL=url, ED_PUBLIC_KEY_FILE=str(MACOS / "sparkle-public-key.txt"))
    assert crossed.returncode != 0 and ("never reads it" in crossed.stderr or "bridge" in crossed.stderr)
    assert not (tmp_path / "out").exists()


def test_a_build_may_read_its_own_feed_or_a_private_test_feed():
    for name, c in channels.load().items():
        channels.check_feed(name, c["feedURL"])
        channels.check_feed(name, "http://127.0.0.1:8000/appcast.xml")


def test_native_channels_declare_the_bonjour_service_used_to_find_blackmagic():
    for name in channels.load():
        info = render('Info.plist', name)
        assert info['NSBonjourServices'] == ['_http._tcp']
        assert info['NSLocalNetworkUsageDescription']


def test_camera_listener_and_setup_downloads_are_channel_owned():
    stable, beta = channels.channel("stable"), channels.channel("beta")
    assert (stable["streamPort"], stable["settingsPort"]) == (9710, 8767)
    assert (beta["streamPort"], beta["settingsPort"]) == (9810, 8867)
    for name in ("stable", "beta"):
        c, info = channels.channel(name), render("Info.plist", name)
        assert info["HookeUIStreamPort"] == c["streamPort"]
        assert info["HookeUISettingsPort"] == c["settingsPort"]
    raw = copy.deepcopy(channels.load())
    raw["beta"]["settingsPort"] = stable["settingsPort"]
    with pytest.raises(channels.ChannelError, match="ports differ"):
        channels.validate(raw)
