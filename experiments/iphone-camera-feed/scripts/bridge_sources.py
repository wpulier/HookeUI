"""The bridge's live sources (the iPhone through Continuity Camera, or Blackmagic Camera's stream), their names, and
the choice kept across restarts (--source-file). Which one is on is the main module's (iphone_bridge_server.py
use_live_source, live_source_kind): these helpers never read it."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from bridge_common import wall_timestamp


# The live sources, one bridge: the iPhone through Continuity Camera; the Blackmagic Camera app's stream (over the
# cable or Wi-Fi, into one SRT listener: the link is only where the phone is told to send it); any other camera: one
# this Mac sees (a USB microscope camera, a webcam, a capture card), captured by name as the iPhone is, or a stream
# address (srt://, rtmp://, rtsp://); and no camera (Will, 2026-10-15): pictures and videos only, nothing captured,
# scanned or reset, the bridge's feed only the assets the studio plays.
SOURCE_KINDS = ("continuity", "blackmagic", "other", "none")
# Blackmagic picks its link by itself (phone_control.link_for): the cable when it shares a network, else Wi-Fi.
STREAM_KINDS = ("blackmagic",)
# Earlier names, read from saved choices and make camera-source: the first version had one "stream" source, and until
# 2026-10-12 "wifi" and "usb" pinned a link.
SOURCE_ALIASES = {"stream": "blackmagic", "wifi": "blackmagic", "usb": "blackmagic", "off": "none", "files": "none"}
SOURCE_TITLES = {"continuity": "iPhone", "blackmagic": "Blackmagic Camera", "other": "Other camera", "none": "No camera"}
SOURCE_DEVICE_NAMES = {"blackmagic": "Blackmagic Camera"}


def source_kind(kind: str) -> str:
    kind = SOURCE_ALIASES.get(kind, kind)
    if kind not in SOURCE_KINDS:
        raise ValueError(f"a live source is one of {', '.join(SOURCE_KINDS)}, not {kind!r}")
    return kind


def read_source_choice(args: argparse.Namespace) -> dict:
    if not args.source_file:
        return {}
    try:
        data = json.loads(Path(args.source_file).expanduser().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_source_choice(args: argparse.Namespace, kind: str) -> None:
    """Kept for the next start. Private to this user: a stream URL can hold a passphrase."""
    if not args.source_file:
        return
    path = Path(args.source_file).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"kind": kind, "url": getattr(args, "stream_url", None), "savedAt": wall_timestamp(time.time())}
    # The other camera last chosen, kept whichever source is on, so choosing Other again goes back to it.
    if getattr(args, "other_device", None):
        data["otherDevice"] = args.other_device
    if getattr(args, "other_url", None):
        data["otherUrl"] = args.other_url
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(data, handle, indent=2)
    os.replace(temporary, path)


def source_choices(args: argparse.Namespace, active: str, facts: dict | None = None) -> list[dict[str, object]]:
    """The sources for the console and the studio: which is on (`active`, the main module's live_source_kind)
    and whether each could go live now (`facts`: the bridge's cached addresses and phone, never read here)."""
    facts = facts or {}
    addresses = facts.get("addresses") or {}
    phone = facts.get("phone") or {}
    return [
        {"kind": "continuity", "title": SOURCE_TITLES["continuity"], "active": active == "continuity",
         "device": getattr(args, "device_name", None) or args.target_device},
        {"kind": "blackmagic", "title": SOURCE_TITLES["blackmagic"], "active": active in STREAM_KINDS,
         "link": phone.get("link"), "linkUp": bool(addresses.get("usb")), "phone": bool(phone.get("reachable")),
         "connectTo": addresses.get(phone.get("link") or "wifi")},
        {"kind": "other", "title": SOURCE_TITLES["other"], "active": active == "other",
         "device": getattr(args, "other_device", None), "stream": bool(getattr(args, "other_url", None))},
        {"kind": "none", "title": SOURCE_TITLES["none"], "active": active == "none"},
    ]
