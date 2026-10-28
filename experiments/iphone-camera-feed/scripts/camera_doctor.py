"""The camera's checklist: what is true right now, in order, and the one thing to do.

Built only from facts the bridge read (its frames, the device list, the phone's own API, this Mac); never a guess.
Each item is a fact with its evidence. The first one that fails carries the fix, and an action the bridge can take
itself where there is one, so anyone with only the camera page gets the picture back (Will, 2026-10-12: "knowing,
not guessing"). The console, the studio's Phone panel and its no-camera notice all show this one list.

Standard library only: the bridge runs on the system Python.
"""
from __future__ import annotations

# What a button does (POST to the bridge): the console and the studio map these to their own controls.
ACTIONS = {
    "reconnect": "Reconnect",
    "reset": "Reset camera",
    "start-stream": "Start the phone's stream",
    "use-iphone": "Use the iPhone",
    "settings-file": "Get the settings file",
    "phone-check": "Run the Phone check",
}

# A quality that crashed Blackmagic Camera 3.5.20 whenever its stream started (2026-10-12, 15 crash reports).
UNSAFE_QUALITIES = ("Microscope", "Steady", "Steady low latency")  # each crashed the app as its stream started

FROZEN_AFTER_S = 1.0  # identical frames for this long are a frozen picture, not a live one
STALE_AFTER_S = 2.0  # no new frame for this long: the picture is not arriving
# A camera just asked for is starting, not broken: for this long after a switch or a start, no first frame is "starting"
# (no fix, no red), so changing the source never looks like a fault (Will, 2026-10-13: "it can't feel like you're
# causing a bug"). First frames take 1-4 s on the iPhone and the Mac's cameras; Blackmagic's stream gets its own grace.
STARTING_S = 10.0
STREAM_STARTING_S = 20.0  # phone_control.PhoneLink.STREAM_GRACE_S
BUSY_LOAD_PER_CORE = 1.5  # above this the Mac itself slows the live view (contract's "poor" CPU line)


# What each fact's failure is called, for the headline ("Not live: the phone isn't streaming").
PROBLEMS = {
    "arriving": "no picture is arriving", "size": "the camera is sending the wrong size", "moving": "the picture is frozen",
    "permission": "MicroscopeMind Camera may not use the camera", "free": "something else is holding the iPhone",
    "found": "this Mac can't find the iPhone", "listed": "the iPhone isn't offering its camera",
    "crash": "Blackmagic Camera crashed",
    "app": "Blackmagic Camera isn't open", "link": "the phone can't reach this Mac", "settings": "HookeUI's connection isn't set up in the app",
    "quality": "its stream quality crashes the app", "streaming": "the phone isn't streaming",
    "app-reading": "the app isn't reading the camera", "room": "this Mac is busy",
    "proven": "the phone's controls aren't checked for this version of the app",
    "other-chosen": "no camera is chosen", "other-connected": "this Mac doesn't see the camera",
}


def _item(key: str, title: str, ok: bool | None, evidence: str, fix: str = "", action: str | None = None,
          detail: str = "") -> dict:
    """`evidence` is what the page shows, in plain words; `detail` is the technical fact behind it (an address, a
    version, a load), for Copy report and whoever is helping."""
    return {"id": key, "title": title, "ok": ok, "evidence": evidence, "detail": detail, "fix": fix if ok is False else "",
            "action": action if ok is False else None, "problem": PROBLEMS.get(key, title) if ok is False else ""}


def _load_words(load: float) -> str:
    """A Mac's load per core, said plainly (1.0 is every core busy all the time)."""
    return "mostly idle" if load < 0.4 else "fairly busy" if load < 0.9 else "busy" if load < BUSY_LOAD_PER_CORE else "very busy"


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    return f"{seconds:.1f} s ago" if seconds < 10 else f"{round(seconds)} s ago"


def _picture(f: dict, wake: str, starting_s: float = STARTING_S) -> list[dict]:
    """The picture itself, the same for every source: arriving, the right size, moving."""
    frames = int(f.get("captureFrames") or 0)
    age = f.get("lastFrameAge")
    arriving = frames > 0 and age is not None and age < STALE_AFTER_S
    starting = frames == 0 and bool(f.get("running")) and (f.get("askedFor") or 0) < starting_s
    if starting:
        evidence = "starting"
    elif frames == 0:
        evidence = f"asked {round(f.get('askedFor') or 0)} s ago, nothing yet" if f.get("running") else "nothing is asking the camera"
    elif arriving:
        evidence = f"{round(f.get('fps') or 0)} a second"
    else:
        evidence = f"the last one came {_ago(age)}"
    items = [_item("arriving", "The picture is arriving", True if arriving else None if starting else False, evidence, wake, "reconnect",
                   detail=f"last frame {_ago(age)}, {f.get('fps') or 0:.1f} fps, {frames} frames")]
    size, target = f.get("frameSize"), f.get("targetSize")
    if arriving and size and target:
        right = tuple(size) == tuple(target)
        items.append(_item("size", "The picture is the right size", right,
                           "the size the app is built for" if right else f"{size[0]}x{size[1]}, not {target[0]}x{target[1]}",
                           "The camera is sending the wrong size, which slows the whole app. Reset camera.", "reset",
                           detail=f"the newest is {size[0]}x{size[1]}, the app wants {target[0]}x{target[1]}"))
    if arriving and f.get("frozenFor") is not None:
        frozen = f["frozenFor"] >= FROZEN_AFTER_S
        items.append(_item("moving", "The picture isn't frozen", not frozen,
                           f"the same picture, exactly, for {f['frozenFor']:.0f} s" if frozen else "each picture is new",
                           "The camera is repeating one picture. Reset camera.", "reset",
                           detail=f"byte-identical frames for {f['frozenFor']:.1f} s"))
    return items


def _continuity(f: dict) -> list[dict]:
    items = [
        _item("permission", "MicroscopeMind Camera may use the camera", not f.get("permissionDenied"),
              "macOS refused it" if f.get("permissionDenied") else "allowed",
              "Open System Settings › Privacy & Security › Camera, turn on MicroscopeMind Camera, then Reconnect."),
        _item("free", "Nothing else is holding the iPhone", not (f.get("deskView") or f.get("blackmagicOpen")),
              "Desk View is open" if f.get("deskView") else "Blackmagic Camera is open on the phone" if f.get("blackmagicOpen")
              else "no Desk View, Blackmagic Camera closed",
              "Quit Desk View." if f.get("deskView") else "Close Blackmagic Camera and lock the iPhone (Continuity only starts on a locked phone)."),
    ]
    # Where it stops, in two facts (Will, 2026-10-13: "make it clearer to us what's happening"): does macOS find the
    # phone at all (the cable, or its microphone listed), and does it offer the phone's camera.
    present, cable, nearby = bool(f.get("devicePresent")), f.get("usbCable"), f.get("iphoneNearby")
    found = True if (present or cable or nearby) else None if (cable is None and nearby is None) else False
    items.append(_item("found", "This Mac finds the iPhone", found,
                       "on the cable" if cable else "nearby, wirelessly" if found else "not found" if found is False else "not checked yet",
                       "Plug the iPhone into this Mac with its cable, or turn on Wi-Fi and Bluetooth on both.",
                       detail=f"cable {'yes' if cable else 'no' if cable is False else 'unknown'}, its microphone "
                              f"{'listed' if nearby else 'not listed' if nearby is False else 'unknown'}"))
    items.append(_item("listed", "The iPhone offers its camera", present,
                       "yes" if present else "not yet",
                       "Lock the iPhone, close any app using its camera, or turn Continuity Camera off and on on the iPhone.",
                       "reset", detail="in the camera list" if present else "not in the camera list"))
    if f.get("devicePresent"):
        items += _picture(f, "Lock the iPhone and keep it on the eyepiece, then Reconnect. If it still sends nothing, Reset camera.")
    return items


def _blackmagic(f: dict) -> list[dict]:
    phone = f.get("phone") or {}
    addresses = phone.get("addresses") or {}
    # The bridge picks the link: the cable when it shares a network, otherwise Wi-Fi. Nobody chooses.
    link = f.get("link") or ("usb" if addresses.get("usb") else "wifi")
    items: list[dict] = []
    crash = f.get("appCrashedAt")
    if crash:
        items.append(_item("crash", "Blackmagic Camera hasn't crashed", False, f"its crash report says it crashed at {crash}",
                           "Open Blackmagic Camera again. If it crashes as its stream starts, choose Settings › Live Stream › "
                           "Quality › Standard."))
    # Its picture can arrive before its control API is found (the phone link looks every few seconds, and a stream
    # started earlier carries on): a picture arriving is the stronger fact, so the app is then "not checked yet",
    # never "isn't open" over a live picture (2026-10-13, after a restart).
    picture = int(f.get("captureFrames") or 0) > 0 and (f.get("lastFrameAge") is not None and f["lastFrameAge"] < STALE_AFTER_S)
    reachable = bool(phone.get("reachable"))
    if phone.get("discoveryIssue") and not reachable and not picture:
        return [_item("discovery", "Hooke can find cameras on this network", False,
                      "camera discovery needs attention", phone["discoveryIssue"])]
    items.append(_item("app", "Blackmagic Camera is open on the phone", True if reachable else (None if picture else False),
                       "it answered" if reachable else "its picture arrives; finding its controls" if picture else "it doesn't answer",
                       "Open Blackmagic Camera on the iPhone and keep it in front. Its Settings › Network Access › HTTP "
                       "Server must be on, and Remote Camera Control off." + (" Keep the phone on this Mac's Wi-Fi." if link == "wifi" else ""),
                       detail=f"its API answered as {phone.get('name') or 'the phone'}" if phone.get("reachable")
                       else f"no answer ({phone.get('error') or 'not found on the network'})"))
    address = (addresses.get(link) or "").split("//")[-1].split(":")[0]
    items.append(_item("link", "The phone can reach this Mac", bool(address),
                       f"over {'the cable' if link == 'usb' else 'Wi-Fi'}" if address else "this Mac isn't on Wi-Fi",
                       "Join this Mac to the same Wi-Fi as the phone, or plug the phone into this Mac.",
                       detail=f"over {'the cable' if link == 'usb' else 'Wi-Fi'} ({address})" if address else "no Wi-Fi address"))
    if not reachable:
        return items + (_picture(f, "Reconnect.") if picture else [])
    items.append(_item("settings", "HookeUI is set up in the app", not phone.get("needsSettings"),
                       "the app doesn't know where to send the picture" if phone.get("needsSettings") else "it streams to this Mac",
                       "Import the settings file: Settings › Live Stream › Platform › Import Stream Settings.", "settings-file",
                       detail="the app has no server for this Mac" if phone.get("needsSettings")
                       else f"server {phone.get('server') or 'chosen'}"))
    quality = phone.get("quality")
    unsafe = f.get("unsafeQualities") or UNSAFE_QUALITIES
    items.append(_item("quality", "Its stream quality is safe", quality not in unsafe,
                       f"{quality} crashes the app" if quality in unsafe else "it doesn't crash the app",
                       "In Blackmagic Camera: Settings › Live Stream › Quality, choose Standard. If Standard isn't listed, "
                       "import the settings file again (Get the settings file).", "settings-file",
                       detail=f"the app reports quality {quality or 'unknown'}"))
    version = phone.get("app")
    if version and f.get("provenVersions") is not None:
        proven = version in f["provenVersions"]
        items.append(_item("proven", "Its settings can be changed from here", proven,
                           "checked on this version of the app" if proven else "the app was updated: not checked yet",
                           "Blackmagic Camera was updated. Run the Phone check (about 10 minutes, keep the app open): until "
                           "then the picture works, but its settings can't be changed from here.", "phone-check",
                           detail=f"Blackmagic Camera {version}" + ("" if proven else ", not checked yet")))
    status = phone.get("streamStatus")
    # The picture arriving is the proof: the app's own status lags a link change ("Interrupted", "Connecting" while its
    # picture flows, live 2026-10-13 after unplugging the cable), so it never says "not streaming" over a live picture.
    streaming = status == "Streaming" or picture
    # Just switched to it, or the app just opened: the bridge is starting its stream (not a fault yet).
    starting = not streaming and bool(f.get("running")) and (f.get("askedFor") or 0) < STREAM_STARTING_S
    items.append(_item("streaming", "The phone is streaming", True if streaming else None if starting else False,
                       "yes" if streaming else "starting" if starting else "not yet" if status in (None, "Idle") else "it was interrupted",
                       "Start it here, or with the Live Stream switch in the app's Settings." if status in (None, "Idle") else
                       "The phone can't reach this Mac: check the link above, then start its stream again.", "start-stream",
                       detail=f"its stream is {status or 'unknown'}"))
    if streaming:
        items += _picture(f, "The phone streams but nothing arrives. Reconnect (a fresh listener takes its stream).",
                          STREAM_STARTING_S)
    return items


def _other(f: dict) -> list[dict]:
    """Any other camera: one this Mac sees (captured by name, as the iPhone is), or a stream address."""
    name, stream = f.get("otherDevice"), f.get("otherStream")
    if not (name or stream):
        return [_item("other-chosen", "A camera is chosen", False, "none yet",
                      "Choose a camera, or type its stream address, under Other camera on the camera page.")]
    if stream:
        connect = f.get("otherConnectTo") or stream
        wake = (f"Send the picture from the other device to {connect}." if f.get("otherListens")
                else "Check the address, and that the camera is on and on this Mac's network. Then Reconnect.")
        return [_item("other-chosen", "A camera is chosen", True, "a stream", detail=stream)] + _picture(f, wake)
    items = [_item("other-chosen", "A camera is chosen", True, name)]
    if f.get("permissionDenied"):
        items.append(_item("permission", "MicroscopeMind Camera may use the camera", False, "macOS refused it",
                           "Open System Settings › Privacy & Security › Camera, turn on MicroscopeMind Camera, then Reconnect."))
    present = bool(f.get("devicePresent"))
    items.append(_item("other-connected", "This Mac sees the camera", present,
                       "it is connected" if present else "it isn't in this Mac's camera list",
                       f"Plug {name} into this Mac, or check its cable, then press Reconnect. If it is plugged in, unplug it "
                       "and plug it back in.", "reconnect"))
    if present:
        items += _picture(f, "Unplug the camera and plug it back in, then Reconnect.")
    return items


def _this_mac(f: dict) -> list[dict]:
    items = []
    if f.get("readers") is not None:
        items.append(_item("app-reading", "Hooke is connected to the feed", f["readers"] > 0,
                           "connected" if f["readers"] > 0 else "no connected viewer",
                           "Open Hooke and choose the live camera, or reload the camera setup page.",
                           detail=f"{f['readers']} reader{'s' if f['readers'] != 1 else ''} of the live feed"))
    load = f.get("loadPerCore")
    if load is not None:
        items.append(_item("room", "This Mac has room", load < BUSY_LOAD_PER_CORE, _load_words(load),
                           "Close apps you don't need (the studio's meter lists what is busiest): a very busy Mac slows the "
                           "live view.", detail=f"load {load:.1f} per core"))
    return items


# What not being connected yet is: a step the user takes, never a fault (Will, 2026-10-13: "You toggle a tab, that's not a
# fault, you didn't break anything ... nicely say: you need to connect, here's how"). When the first thing missing is one
# of these, the checklist is a short guide: what to do, numbered, and "Waiting for ..." (no red, no "Not live:").
CONNECT_STEPS = ("free", "found", "listed", "app", "link", "other-chosen", "other-connected")


def _connect(f: dict, source: str, first: dict) -> dict:
    """The guide for a camera not connected yet: its headline, the steps in order, what to check if it still doesn't
    come, and what the page is waiting for."""
    if source == "continuity":
        lock = "Lock the iPhone: press its side button."
        if first["id"] == "free" and f.get("deskView"):
            return {"headline": "Desk View is using the iPhone", "waiting": "Waiting for the iPhone", "help": [],
                    "steps": ["Quit Desk View on this Mac.", lock, "Keep it near this Mac. The picture comes in by itself."]}
        if first["id"] == "free":
            return {"headline": "Blackmagic Camera is using the iPhone's camera", "waiting": "Waiting for the iPhone", "help": [],
                    "steps": ["On the iPhone, swipe up from the bottom and swipe Blackmagic Camera away.", lock,
                              "Keep it near this Mac. The picture comes in by itself."]}
        if first["id"] == "found" or not (f.get("usbCable") or f.get("iphoneNearby")):  # not found, or not known found
            return {"headline": "Connect your iPhone", "waiting": "Waiting for the iPhone",
                    "steps": ["Plug the iPhone into this Mac with its cable. It's the surest way.",
                              "Or without the cable: turn on Wi-Fi and Bluetooth on the iPhone and on this Mac.", lock],
                    "help": ["The iPhone and this Mac use the same Apple Account.",
                             "Continuity Camera is on, on the iPhone: Settings › General › AirPlay & Continuity."]}
        return {"headline": "This Mac finds your iPhone, but not its camera yet", "waiting": "Waiting for the iPhone's camera",
                "steps": [lock, "If an app on the iPhone is using the camera (Blackmagic Camera, Camera), swipe it away.",
                          "Still nothing? On the iPhone: Settings › General › AirPlay & Continuity, turn Continuity Camera "
                          "off and on again."],
                "help": ["Or plug the iPhone into this Mac with its cable.",
                         "The iPhone and this Mac use the same Apple Account."]}
    if source == "other":
        name = f.get("otherDevice")
        if first["id"] == "other-chosen" or not name:
            return {"headline": "Choose a camera", "steps": ["Choose your camera in the list above."],
                    "waiting": "Waiting for a camera", "help": []}
        return {"headline": f"Plug in {name}", "steps": [f"Plug {name} into this Mac.", "It appears here by itself."],
                "waiting": f"Waiting for {name}",
                "help": ["If it is plugged in, unplug it and plug it back in."]}
    if first["id"] == "link":
        return {"headline": "Connect the iPhone to this Mac",
                "steps": ["Plug the iPhone into this Mac with its cable, or put this Mac on the iPhone's Wi-Fi."],
                "waiting": "Waiting for the iPhone", "help": []}
    return {"headline": "Open Blackmagic Camera on your iPhone",
            "steps": ["Open the Blackmagic Camera app on the iPhone and keep it in front.",
                      "Keep the iPhone on this Mac's Wi-Fi, or plug it in.", "The picture comes in by itself."],
            "waiting": "Waiting for Blackmagic Camera",
            "help": ["In the app: Settings › Network Access › HTTP Server is on.",
                     "In the app: Remote Camera Control is off."]}


def checklist(f: dict) -> dict:
    """The checklist for the chosen source. `f` holds the facts (CameraBridge.doctor_facts); every field optional."""
    source = f.get("source") or "continuity"
    if f.get("footage"):
        return {"source": "footage", "chosen": source, "live": True, "mode": "live", "headline": "Test footage is playing",
                "items": [], "first": None}
    if source == "none":
        # No camera is the user's choice (Will, 2026-10-15): calm, never a fault or a guide to connect.
        return {"source": "none", "chosen": "none", "live": False, "mode": "off", "headline": "No camera",
                "line": "This uses pictures and videos only. Choose a camera when you want one.", "items": [], "first": None}
    items = {"continuity": _continuity, "other": _other}.get(source, _blackmagic)(f) + _this_mac(f)
    failure = f.get("streamRuntimeFailure")
    if failure:
        items.insert(0, _item("stream-runtime", failure["text"], False,
                             "the installed video receiver does not support this stream",
                             " ".join(failure["steps"]), detail=failure["code"]))
    failing = next((item for item in items if item["ok"] is False), None)
    # What doesn't stop the picture (the app reading it, a busy Mac, controls not yet checked) leaves it live.
    camera_ok = all(item["ok"] is not False for item in items if item["id"] not in ("app-reading", "room", "proven"))
    live = camera_ok and any(item["id"] == "arriving" and item["ok"] for item in items)
    headline = ("Live, but " if live else "Not live: ") + failing["problem"] if failing else "Live" if live else "Starting the camera"
    via = None
    if source == "blackmagic":
        addresses = (f.get("phone") or {}).get("addresses") or {}
        via = "the cable" if (f.get("link") or ("usb" if addresses.get("usb") else "wifi")) == "usb" else "Wi-Fi"
    if live and via:  # the link is said even when something else needs attention (live 2026-10-13: plugged in, unsaid)
        headline = f"Live over {via}, but {failing['problem']}" if failing else f"Live, over {via}"
    mode = "live" if live else "problem" if failing else "starting"
    guide: dict = {}
    if failing and not live and failing["id"] in CONNECT_STEPS:
        mode, guide = "connect", _connect(f, source, failing)
        headline = guide["headline"]
    return {"source": source, "chosen": source, "live": live, "headline": headline, "via": via, "mode": mode, **guide,
            "first": failing["id"] if failing else None, "items": items}


def report(doctor: dict, events: list[dict]) -> str:
    """The checklist and the recent camera events as plain text, to paste to someone helping."""
    marks = {True: "ok ", False: "NO ", None: "?  "}
    lines = [f"Camera: {doctor.get('source')} - {'live' if doctor.get('live') else 'not live'}"]
    for item in doctor.get("items", []):
        lines.append(f"  {marks[item['ok']]}{item['title']} ({item.get('detail') or item['evidence']})")
        if item["fix"]:
            lines.append(f"     fix: {item['fix']}")
    if events:
        lines.append("Recent camera events:")
        lines += [f"  {event['at']}  {event['text']}" for event in events[-20:]]
    return "\n".join(lines)
