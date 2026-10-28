#!/usr/bin/env python3
"""The Mac app's release channels (channels.json; packaging/macos/README.md "Channels"): Stable and Beta are two apps
from one source, each with its own name, bundle identifier, Application Support and data folders, Keychain service,
ports, releases repository, update feed and update key, so a Beta installed beside Stable never shares or overwrites
Stable's data, runtime or processes.

    channels.py env CHANNEL                 KEY=VALUE lines build.sh reads (no shell evaluation)
    channels.py plist CHANNEL --template Info.plist --version 0.1.2 --build 3400 --feed URL --edkey KEY --out PATH
    channels.py feed CHANNEL URL            refuses a feed URL that is another channel's (or the original beta's)
    channels.py check                       validates channels.json

Standard library only: build.sh runs it with the Mac's own python3.
"""
from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
CHANNELS = HERE / "channels.json"
STABLE = "stable"
FIELDS = {
    "name": str, "bundleId": str, "cameraBundleId": str, "supportFolder": str, "dataFolder": str,
    "keychainService": str, "orchestratorPort": int, "cameraPort": int, "streamPort": int, "settingsPort": int, "isolated": bool, "releases": str,
    "feedURL": str, "edPublicKey": str, "urlScheme": str,
}
# What two channels may never share: one is enough to let one app read, overwrite or stop the other's.
DISTINCT = ("name", "bundleId", "cameraBundleId", "supportFolder", "dataFolder", "keychainService", "releases",
            "feedURL", "edPublicKey", "urlScheme")
# The original beta's website bridge (packaging/macos/README.md "Original beta"): clients of org.hookeui.HookeUI read
# it, so no channel may publish there.
LEGACY_FEEDS = ("https://hookeui.org/beta/appcast.xml",)


class ChannelError(ValueError):
    pass


def validate(channels: dict) -> dict:
    if STABLE not in channels:
        raise ChannelError("channels.json has no stable channel")
    for key, c in channels.items():
        if not re.fullmatch(r"[a-z]+", key):
            raise ChannelError(f"channel {key!r}: a lowercase word")
        missing = set(FIELDS) - set(c)
        extra = set(c) - set(FIELDS)
        if missing or extra:
            raise ChannelError(f"channel {key}: missing {sorted(missing)}, unknown {sorted(extra)}")
        for field, kind in FIELDS.items():
            if type(c[field]) is not kind:
                raise ChannelError(f"channel {key}: {field} is a {kind.__name__}")
        if c["feedURL"] != f"https://github.com/{c['releases']}/releases/latest/download/appcast.xml":
            raise ChannelError(f"channel {key}: feedURL must be the latest release's appcast.xml on {c['releases']}")
        if c["feedURL"] in LEGACY_FEEDS:
            raise ChannelError(f"channel {key}: {c['feedURL']} is the original beta's bridge")
        if not re.fullmatch(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", c["bundleId"]):
            raise ChannelError(f"channel {key}: bundleId {c['bundleId']!r}")
        for folder in ("supportFolder", "dataFolder"):
            if "/" in c[folder] or c[folder].startswith(".") or not c[folder].strip():
                raise ChannelError(f"channel {key}: {folder} is one folder name")
        if not re.fullmatch(r"hookeui(-[a-z]+)?", c["urlScheme"]):
            # Its links ("Open in Hooke", ADR 0026) open only this channel's app: Stable's is hookeui, another's its own.
            raise ChannelError(f"channel {key}: urlScheme is hookeui or hookeui-<word>")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 -]*", c["name"]):
            raise ChannelError(f"channel {key}: name {c['name']!r}")
        if (key == STABLE) == c["isolated"]:
            raise ChannelError(f"channel {key}: only stable uses the Mac-wide defaults (isolated is false only there)")
    keys = list(channels)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            for field in DISTINCT:
                if channels[a][field] == channels[b][field]:
                    raise ChannelError(f"channels {a} and {b} share {field} {channels[a][field]!r}")
            if channels[b]["bundleId"].lower() == channels[a]["bundleId"].lower():
                raise ChannelError(f"channels {a} and {b} share a bundle identifier")
    ports = [p for c in channels.values() for p in (c["orchestratorPort"], c["cameraPort"], c["streamPort"], c["settingsPort"])]
    if len(set(ports)) != len(ports):
        raise ChannelError("every channel's preferred ports differ from every other's")
    return channels


def load(path: Path = CHANNELS) -> dict:
    return validate(json.loads(Path(path).read_text()))


def channel(name: str, path: Path = CHANNELS) -> dict:
    channels = load(path)
    if name not in channels:
        raise ChannelError(f"no channel {name!r} (channels.json has {', '.join(channels)})")
    return channels[name]


def check_feed(name: str, url: str, path: Path = CHANNELS) -> None:
    """A build of `name` may read a test feed of its own, never another channel's: Stable must never be offered a Beta
    and a Beta must never install over Stable's updates."""
    channels = load(path)
    if name not in channels:
        raise ChannelError(f"no channel {name!r}")
    if url in LEGACY_FEEDS:
        raise ChannelError(f"{url} is the original beta's bridge, read by Stable's bundle identifier")
    for other, c in channels.items():
        if other != name and (url == c["feedURL"] or f"github.com/{c['releases']}/" in url):
            raise ChannelError(f"{url} is the {other} channel's feed; a {name} build never reads it")


def file_stem(c: dict) -> str:
    """The dmg's name without spaces: HookeUI, HookeUI-Beta."""
    return c["name"].replace(" ", "-")


def environment(name: str, path: Path = CHANNELS) -> dict[str, str]:
    """What build.sh needs, as plain strings."""
    c = channel(name, path)
    return {
        "CHANNEL_NAME": c["name"],
        "CHANNEL_BUNDLE_ID": c["bundleId"],
        "CHANNEL_FILE_STEM": file_stem(c),
        "CHANNEL_RELEASES": c["releases"],
        "CHANNEL_ED_PUBLIC_KEY": str(HERE / c["edPublicKey"]),
    }


def substitute(template: str, values: dict[str, str]) -> str:
    def one(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise ChannelError(f"the template asks for @{key}@, which isn't given")
        return values[key]

    return re.sub(r"@([A-Z_]+)@", one, template)


def info_plist(template: str, name: str, *, version: str, build: str, feed: str = "", edkey: str = "",
               path: Path = CHANNELS) -> str:
    """The app's Info.plist (HookeUI/Info.plist) or its camera helper's (HookeUI/Camera-Info.plist) for one channel."""
    c = channel(name, path)
    if not re.fullmatch(r"\d+(\.\d+){1,2}", version) or not build.isdigit():
        raise ChannelError("version is like 0.1.2 and build is a number")
    values = {
        "VERSION": version, "BUILD": build, "FEED": feed, "EDKEY": edkey, "CHANNEL": name, "NAME": c["name"],
        "BUNDLE_ID": c["bundleId"], "CAMERA_ID": c["cameraBundleId"], "SUPPORT": c["supportFolder"],
        "DATA": c["dataFolder"], "KEYCHAIN": c["keychainService"], "ORCH_PORT": str(c["orchestratorPort"]),
        "CAMERA_PORT": str(c["cameraPort"]), "STREAM_PORT": str(c["streamPort"]),
        "SETTINGS_PORT": str(c["settingsPort"]), "ISOLATED": "true" if c["isolated"] else "false",
        "URL_SCHEME": c["urlScheme"],
    }
    text = substitute(template, {k: escape(v) for k, v in values.items()})
    plistlib.loads(text.encode())  # a broken plist stops the build here, not at launch
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    env = sub.add_parser("env")
    env.add_argument("channel")
    feed = sub.add_parser("feed")
    feed.add_argument("channel")
    feed.add_argument("url")
    plist = sub.add_parser("plist")
    plist.add_argument("channel")
    for flag in ("template", "version", "build", "out"):
        plist.add_argument(f"--{flag}", required=True)
    plist.add_argument("--feed", default="")
    plist.add_argument("--edkey", default="")
    a = ap.parse_args(argv)
    try:
        if a.command == "check":
            print(", ".join(load()))
        elif a.command == "feed":
            check_feed(a.channel, a.url)
        elif a.command == "env":
            for key, value in environment(a.channel).items():
                print(f"{key}={value}")
        else:
            text = info_plist(Path(a.template).read_text(), a.channel, version=a.version, build=a.build,
                              feed=a.feed, edkey=a.edkey)
            Path(a.out).write_text(text)
    except ChannelError as e:
        print(f"channels.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
