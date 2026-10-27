"""The Phone check: one guided session that learns what Blackmagic Camera's API really allows on this phone.

We learned the API one live failure at a time (a profile that crashed the app on every stream start, a URL that broke
its HTTP server). The Phone check learns it once, in about ten minutes, while someone keeps the app open:
  1. every documented route is read (reads have never hurt the app), ranges kept;
  2. every candidate write is tried one at a time: the same value first, then one small change and back, and after
     each the app is asked if it still answers and the phone's crash reports are read;
  3. a 3 s recording is made on the phone (if the record write is safe) and stopped;
  4. each lens is chosen by its focal length, and kept when the phone says it switched (the lens in use is restored);
  5. white balance is set once automatically, and the white balance and tint it had put back.
Reads the bridge has used for days are taken together; every other read is checked one at a time like a write (on
2026-10-12 reading the colour-correction settings crashed the app). Each run carries on from the last: nothing that
crashed the app is tried again. Streaming qualities are not tried here: a quality that crashes the app left it
crashing again on every open (2026-10-12), so streaming gets its own guided test.
What it proved is written to one manifest (blackmagic-api.json, per app version). The bridge sends only writes the
manifest marks safe for the running app version (phone_control.Phone.put), so nothing unproven reaches the phone.

Standard library only: the bridge runs on the system Python.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "blackmagic-api.json"

# Every documented route worth reading (Blackmagic's camera REST API); the app answers a subset.
READS = """
/system/product /system/format /system/supportedFormats /system/codecFormat /system/supportedCodecFormats
/transports/0 /transports/0/record /transports/0/timecode /media/workingset /media/active /presets /presets/active
/lens/iris /lens/zoom /lens/focus /lens/focus/autoFocus /lens/opticalImageStabilization /lens/stabilization
/video/iso /video/supportedISOs /video/gain /video/supportedGains /video/whiteBalance /video/whiteBalanceTint
/video/autoWhiteBalance /video/ndFilter /video/shutter /video/supportedShutters /video/autoExposure
/video/detailSharpening /video/detailSharpeningLevel /video/stabilization /video/imageStabilization
/colorCorrection/lift /colorCorrection/gamma /colorCorrection/gain /colorCorrection/offset /colorCorrection/contrast
/colorCorrection/color /camera/power /camera/still /camera/photo /monitoring/focusAssist /monitoring/zebra
/livestreams/0 /livestreams/0/activePlatform /livestreams/platforms /event/list
""".split()


def _near(values, current, step=1):
    """A value next to `current` in `values` (a small, real change), or None."""
    values = [v for v in values if v != current]
    if not values:
        return None
    return min(values, key=lambda v: abs((v if isinstance(v, (int, float)) else 0) - (current or 0)))


def _flip(key):
    return lambda cur, ranges: {**cur, key: not cur.get(key)} if isinstance(cur.get(key), bool) else None


def _nudge(key, delta, low, high):
    def change(cur, ranges):
        value = cur.get(key)
        if not isinstance(value, (int, float)):
            return None
        target = value + delta if value + delta <= high else value - delta
        return {key: max(low, min(high, target))}
    return change


def _from_range(key, range_route, range_key):
    def change(cur, ranges):
        options = (ranges.get(range_route) or {}).get(range_key) or []
        target = _near(options, cur.get(key))
        return {key: target} if target is not None else None
    return change


# The writes a microscope picture can use, safest first. Each: an id, plain words, where it is read and written,
# and how to make one small real change (None: only the same value is tried). Restoring sends what was read.
WRITES = [
    {"id": "autofocus", "title": "Autofocus on and off", "route": "/lens/focus/autoFocus",
     "body": lambda cur: {"enabled": cur.get("enabled")}, "change": _flip("enabled")},
    {"id": "focus", "title": "Focus", "route": "/lens/focus",
     "body": lambda cur: {"normalised": cur.get("normalised")}, "change": _nudge("normalised", 0.02, 0.0, 1.0)},
    {"id": "exposure", "title": "Auto exposure on and off", "route": "/video/autoExposure",
     "body": lambda cur: {"mode": cur.get("mode"), "type": cur.get("type", "")},
     "change": lambda cur, ranges: {"mode": "Off" if cur.get("mode") != "Off" else "Continuous", "type": cur.get("type", "")}},
    {"id": "iso", "title": "ISO", "route": "/video/iso", "body": lambda cur: {"iso": cur.get("iso")},
     "change": _from_range("iso", "/video/supportedISOs", "supportedISOs")},
    {"id": "shutter", "title": "Shutter", "route": "/video/shutter", "body": lambda cur: {"shutterSpeed": cur.get("shutterSpeed")},
     "change": _from_range("shutterSpeed", "/video/supportedShutters", "shutterSpeeds")},
    {"id": "white-balance", "title": "White balance", "route": "/video/whiteBalance",
     "body": lambda cur: {"whiteBalance": cur.get("whiteBalance")}, "change": _nudge("whiteBalance", 100, 2500, 10000)},
    {"id": "tint", "title": "Tint", "route": "/video/whiteBalanceTint",
     "body": lambda cur: {"whiteBalanceTint": cur.get("whiteBalanceTint")}, "change": _nudge("whiteBalanceTint", 2, -50, 50)},
    {"id": "zoom", "title": "Zoom", "route": "/lens/zoom", "body": lambda cur: {"normalised": cur.get("normalised")},
     "change": _nudge("normalised", 0.05, 0.0, 1.0)},
    {"id": "iris", "title": "Iris", "route": "/lens/iris", "body": lambda cur: dict(cur), "change": None},
    {"id": "gain", "title": "Gain", "route": "/video/gain", "body": lambda cur: dict(cur), "change": None},
    {"id": "nd", "title": "ND filter", "route": "/video/ndFilter", "body": lambda cur: dict(cur), "change": None},
    {"id": "sharpening", "title": "Sharpening", "route": "/video/detailSharpening", "body": lambda cur: dict(cur), "change": None},
    {"id": "stabilization", "title": "Stabilization", "route": "/video/stabilization", "body": lambda cur: dict(cur),
     "change": lambda cur, ranges: {k: (not v if isinstance(v, bool) else v) for k, v in cur.items()}},
    {"id": "lens-stabilization", "title": "Lens stabilization", "route": "/lens/opticalImageStabilization",
     "body": lambda cur: dict(cur), "change": lambda cur, ranges: {k: (not v if isinstance(v, bool) else v) for k, v in cur.items()}},
]

# White balance set once from the view (Blackmagic's API: a trigger with no value). Tried by its own step.
AUTO_WHITE_BALANCE = "/video/whiteBalance/doAuto"

# Writes the bridge needs to stream at all: allowed on any app version (an app update must not stop the picture),
# and proven on 3.5.200045 since 2026-10-11.
CORE_WRITES = ("/livestreams/0/start", "/livestreams/0/stop", "/livestreams/0/activePlatform")

# Reads the bridge has made safely for days (3.5.200045, 2026-10-11/12): taken together. Every other read is one at a
# time, the app asked after it.
KNOWN_READS = ("/system/product", "/system/format", "/system/supportedFormats", "/transports/0/record",
               "/transports/0/timecode", "/media/workingset", "/lens/zoom", "/lens/focus", "/lens/focus/autoFocus",
               "/video/iso", "/video/supportedISOs", "/video/whiteBalance", "/video/whiteBalanceTint", "/video/shutter",
               "/video/supportedShutters", "/video/autoExposure", "/camera/power", "/livestreams/0",
               "/livestreams/0/activePlatform", "/livestreams/platforms")


def load_manifest(path: Path | None = None) -> dict:
    try:
        return json.loads((path or MANIFEST_PATH).read_text())
    except (OSError, ValueError):
        return {"versions": {}}


def safe_writes(manifest: dict, app_version: str | None) -> set[str]:
    """The routes the manifest proved safe to write on this app version."""
    entry = (manifest.get("versions") or {}).get(app_version or "") or {}
    return {route for route, result in (entry.get("writes") or {}).items() if result.get("safe") is True}


def crashed_qualities(manifest: dict, app_version: str | None) -> list[str]:
    """Stream qualities that crashed the app as its stream started, learned on this phone (never started again)."""
    entry = (manifest.get("versions") or {}).get(app_version or "") or {}
    return list(entry.get("crashedQualities") or [])


def record_crashed_quality(app_version: str, quality: str, path: Path | None = None) -> None:
    path = path or MANIFEST_PATH
    manifest = load_manifest(path)
    entry = manifest.setdefault("versions", {}).setdefault(app_version, {})
    qualities = entry.setdefault("crashedQualities", [])
    if quality not in qualities:
        qualities.append(quality)
        path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")


def allowed(manifest: dict, app_version: str | None, route: str) -> bool:
    return route in CORE_WRITES or route in safe_writes(manifest, app_version)


class PhoneCheck:
    """One run of the Phone check, on its own thread; `state` is what the camera page shows. It writes what it learns
    to the manifest as it goes, so a run cut short (the app closed, the page closed) carries on next time."""

    def __init__(self, phone_factory, crash_times, manifest_path: Path = MANIFEST_PATH, sleep=time.sleep,
                 settle_s: float = 3.0, reopen_wait_s: float = 300.0, **_ignored) -> None:
        self.phone_factory = phone_factory  # () -> phone_control.Phone or None (the app found, writes unchecked)
        self.crash_times = crash_times  # () -> [epoch seconds] (the phone's crash reports)
        self.manifest_path = manifest_path
        self.sleep = sleep
        self.settle_s = settle_s
        self.reopen_wait_s = reopen_wait_s
        self.state: dict[str, object] = {"running": False, "step": "", "done": 0, "total": 0, "needsYou": "",
                                         "results": {}, "summary": [], "finished": False, "error": ""}
        self.thread: threading.Thread | None = None

    # --- running ------------------------------------------------------------------------------------------------
    def start(self) -> dict:
        if self.thread and self.thread.is_alive():
            return self.state
        self.state.update(running=True, finished=False, error="", needsYou="", summary=[], results={})
        self.thread = threading.Thread(target=self._run, daemon=True, name="phone-check")
        self.thread.start()
        return self.state

    def _say(self, step: str, done: int | None = None) -> None:
        self.state["step"] = step
        if done is not None:
            self.state["done"] = done

    def _run(self) -> None:
        try:
            self.run()
        except Exception as exc:  # the page says so; nothing else depends on it
            self.state["error"] = f"The Phone check stopped: {exc}"
        finally:
            self.state["running"] = False

    def run(self) -> dict:
        phone = self._phone()
        if phone is None:
            self.state["error"] = "Blackmagic Camera isn't open. Open it on the phone, keep it in front, and start again."
            return self.state
        product = phone.get("/system/product") or {}
        version = str(product.get("softwareVersion") or "unknown")
        entry = self._entry(version, product)
        reads, writes = entry["reads"], entry["writes"]
        others = [r for r in READS if r not in KNOWN_READS and reads.get(r) not in ("ok", "missing", "crashed")]
        todo = [w for w in WRITES if not writes.get(w["route"], {}).get("byCheck")]
        record_due = not writes.get("/transports/0/record", {}).get("byCheck")
        lenses_due = "lenses" not in entry
        auto_wb_due = not writes.get(AUTO_WHITE_BALANCE, {}).get("byCheck")
        self.state["total"] = 1 + len(others) + len(todo) + int(record_due) + int(lenses_due) + int(auto_wb_due)
        done = 0
        ranges: dict[str, object] = {}
        self._say("Reading the settings the Mac already uses", done)
        for route in KNOWN_READS:
            try:
                ranges[route] = phone.get(route)
                reads[route] = "ok"
            except Exception:
                reads[route] = "missing"
        done += 1
        self._save(version, entry)
        for route in others:
            done += 1
            self._say(f"Reading the camera's other settings ({done - 1} of {len(others)})", done)
            reads[route], value = self._try_read(phone, route)
            if value is not None:
                ranges[route] = value
            self._save(version, entry)
            phone = self._phone() or phone
        for route in KNOWN_READS + tuple(r for r in READS if reads.get(r) == "ok"):
            if route not in ranges and reads.get(route) == "ok":
                try:
                    ranges[route] = phone.get(route)
                except Exception:
                    pass
        for probe in todo:
            done += 1
            self._say(f"Trying {probe['title'].lower()}", done)
            if reads.get(probe["route"]) == "crashed":
                writes[probe["route"]] = {"safe": False, "note": "reading it crashed the app", "byCheck": True}
            else:
                writes[probe["route"]] = {**self._try_write(phone, probe, ranges), "byCheck": True}
            self._save(version, entry)
            phone = self._phone() or phone
        if record_due:
            done += 1
            self._say("Trying a 3-second recording on the phone", done)
            writes["/transports/0/record"] = {**self._try_record(phone, reads), "byCheck": True}
        if lenses_due:
            done += 1
            self._say("Trying each lens", done)
            entry["lenses"] = self._try_lenses(phone, reads)
            self._save(version, entry)
        if auto_wb_due:
            done += 1
            self._say("Trying white balance set once, automatically", done)
            writes[AUTO_WHITE_BALANCE] = {**self._try_auto_white_balance(phone, reads, writes), "byCheck": True}
            self._save(version, entry)
        entry["complete"] = True
        entry["ranges"] = {route: ranges[route] for route in ("/video/supportedISOs", "/video/supportedShutters",
                                                              "/system/supportedFormats", "/lens/zoom") if route in ranges}
        entry["events"] = reads.get("/event/list") == "ok"
        self._save(version, entry)
        self.state.update(finished=True, done=self.state["total"], step="Done",
                          results={"version": version, "writes": writes}, summary=self._summary(writes, reads))
        return self.state

    # --- the manifest, kept as it goes --------------------------------------------------------------------------
    def _entry(self, version: str, product: dict) -> dict:
        manifest = load_manifest(self.manifest_path)
        entry = (manifest.get("versions") or {}).get(version) or {}
        reads = entry.get("reads") if isinstance(entry.get("reads"), dict) else {}
        # Only this check's own verdicts are carried on (older booleans and live use are tried again, properly).
        reads = {r: v for r, v in reads.items() if v in ("ok", "missing", "crashed")}
        writes = dict(entry.get("writes") or {})
        return {**entry, "product": product.get("productName"), "reads": reads, "writes": writes, "complete": False,
                "by": "Phone check", "provenAt": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def _save(self, version: str, entry: dict) -> None:
        manifest = load_manifest(self.manifest_path)
        manifest.setdefault("versions", {})[version] = entry
        self.manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")

    # --- one step -----------------------------------------------------------------------------------------------
    def _phone(self):
        try:
            return self.phone_factory()
        except Exception:
            return None

    def _alive(self, phone) -> bool:
        for _ in range(3):
            try:
                phone.get("/system/product")
                return True
            except Exception:
                self.sleep(1.0)
                phone = self._phone() or phone
        return False

    def _reports(self) -> set:
        try:
            return set(self.crash_times())
        except Exception:
            return set()

    def _crashed_since(self, before: set, started: float) -> bool:
        """A crash report that is new and dated at or after the step began (the phone names them to the second, and
        lists some only a minute late: an older one appearing now is not this step's)."""
        return any(t >= started - 1 for t in self._reports() - before)

    def _wait_for_reopen(self) -> bool:
        self.state["needsYou"] = "Blackmagic Camera closed. Open it again on the phone and keep it in front."
        deadline = time.monotonic() + self.reopen_wait_s
        while time.monotonic() < deadline:
            phone = self._phone()
            if phone is not None and self._alive(phone):
                self.state["needsYou"] = ""
                return True
            self.sleep(2.0)
        return False

    def _check(self, phone, before: set, started: float) -> str | None:
        """After a step: None if the app is fine, else why not."""
        self.sleep(self.settle_s)
        if not self._alive(phone):
            return "the app stopped answering"
        if self._crashed_since(before, started):
            return "the phone's crash report says the app crashed"
        return None

    def _try_read(self, phone, route: str):
        before, started = self._reports(), time.time()
        try:
            value = phone.get(route)
        except Exception:
            value = None
        problem = self._check(phone, before, started)
        if problem:
            self._wait_for_reopen()
            return "crashed", None
        return ("ok", value) if value is not None else ("missing", None)

    def _try_write(self, phone, probe: dict, ranges: dict) -> dict:
        route = probe["route"]
        current = ranges.get(route)
        if not isinstance(current, dict):
            return {"safe": None, "note": "the app doesn't have this setting"}
        before, started = self._reports(), time.time()
        try:
            phone.put(route, probe["body"](current))
        except Exception as error:
            return {"safe": None, "note": f"refused: {error}"}
        problem = self._check(phone, before, started)
        if problem:
            self._wait_for_reopen()
            return {"safe": False, "note": problem}
        result = {"safe": True, "note": "same value accepted", "changes": False}
        change = probe["change"](current, ranges) if probe.get("change") else None
        if change:
            before, started = self._reports(), time.time()
            try:
                phone.put(route, change)
                problem = self._check(phone, before, started)
                after = phone.get(route) if not problem else None
                phone.put(route, probe["body"](current))  # back as it was
            except Exception as error:
                problem = problem or f"the change was refused: {error}"
                after = None
            if problem:
                self._wait_for_reopen()
                return {"safe": False, "note": problem}
            changed = isinstance(after, dict) and any(after.get(k) != current.get(k) for k in change)
            result.update(changes=changed, note="changes the picture" if changed else "accepted, but the value didn't change")
        return result

    def _try_record(self, phone, reads: dict) -> dict:
        if reads.get("/transports/0/record") != "ok":
            return {"safe": None, "note": "the app doesn't say whether it records"}
        state = phone.get("/transports/0/record") or {}
        if state.get("recording"):
            return {"safe": None, "note": "the phone was already recording: not tried"}
        before, started = self._reports(), time.time()
        try:
            phone.put("/transports/0/record", {"recording": True})
            self.sleep(3.0)
            during = phone.get("/transports/0/record") or {}
            phone.put("/transports/0/record", {"recording": False})
        except Exception as error:
            return {"safe": None, "note": f"refused: {error}"}
        problem = self._check(phone, before, started)
        if problem:
            self._wait_for_reopen()
            return {"safe": False, "note": problem}
        if during.get("recording"):
            return {"safe": True, "note": f"recorded {during.get('clipName') or 'a clip'} on the phone", "clip": during.get("clipName")}
        return {"safe": True, "note": "accepted, but the phone didn't start recording", "changes": False}

    def _try_auto_white_balance(self, phone, reads: dict, writes: dict) -> dict:
        """White balance set once by the app from what it sees (a trigger, no value): tried once, the app asked after,
        and the white balance and tint it had put back, each only where writing it is proven safe."""
        if reads.get("/video/whiteBalance") != "ok":
            return {"safe": None, "note": "the app has no white balance setting"}
        phone = self._phone() or phone
        if not self._alive(phone) and not self._wait_for_reopen():
            raise RuntimeError("Blackmagic Camera didn't open again: run the check again once it is open")
        phone = self._phone() or phone
        balance, tint = phone.get("/video/whiteBalance") or {}, phone.get("/video/whiteBalanceTint") or {}
        before, started = self._reports(), time.time()
        try:
            phone.put(AUTO_WHITE_BALANCE, "")
        except Exception as error:
            return {"safe": None, "note": f"refused: {error}"}
        problem = self._check(phone, before, started)
        if problem:
            self._wait_for_reopen()
            return {"safe": False, "note": problem}
        try:
            # The app finishes setting it a moment later (2026-10-12: read at once it was unchanged, and the auto
            # value landed after the restore): wait until two reads a second apart agree, at most 6 s.
            after = phone.get("/video/whiteBalance") or {}
            for _ in range(6):
                self.sleep(1.0)
                again = phone.get("/video/whiteBalance") or {}
                if again == after and again != balance:
                    break
                after = again
            for route, value in (("/video/whiteBalance", balance), ("/video/whiteBalanceTint", tint)):
                if value and (writes.get(route) or {}).get("safe") is True:
                    phone.put(route, value)
        except Exception:
            after = {}
        changed = bool(after) and after != balance
        return {"safe": True, "changes": changed,
                "note": "sets white balance from the view" if changed else "accepted (the view's white balance was already right)"}

    LENSES = (13, 24, 48, 100, 200)  # an iPhone 17 Pro's, as the app lists them

    def _try_lenses(self, phone, reads: dict) -> list[int]:
        """Each lens chosen by its focal length, kept when the phone reports it switched; the lens in use restored."""
        if reads.get("/lens/zoom") != "ok":
            return []
        phone = self._phone() or phone
        if not self._alive(phone):
            if not self._wait_for_reopen():
                raise RuntimeError("Blackmagic Camera didn't open again: run the check again once it is open")
            phone = self._phone() or phone
        start = phone.get("/lens/zoom") or {}
        proven = []
        for focal in self.LENSES:
            before, started = self._reports(), time.time()
            try:
                phone.put("/lens/zoom", {"focalLength": focal})
            except Exception:
                continue
            problem = self._check(phone, before, started)
            if problem:
                self._wait_for_reopen()
                break  # a crash: no more lenses are tried
            try:
                if int((phone.get("/lens/zoom") or {}).get("focalLength") or 0) == focal:
                    proven.append(focal)
            except Exception:
                pass
        if start.get("focalLength"):
            try:
                phone.put("/lens/zoom", {"focalLength": start["focalLength"]})
            except Exception:
                pass
        return proven

    @staticmethod
    def _summary(writes: dict, reads: dict) -> list[str]:
        titles = {probe["route"]: probe["title"] for probe in WRITES}
        titles["/transports/0/record"] = "Recording on the phone"
        works = [titles.get(r, r) for r, w in writes.items() if w.get("safe") is True and w.get("byCheck")]
        unsafe = [titles.get(r, r) for r, w in writes.items() if w.get("safe") is False]
        crashed_reads = [r for r, v in reads.items() if v == "crashed"]
        lines = [f"Works from the Mac: {', '.join(works) or 'nothing yet'}."]
        if unsafe:
            lines.append(f"Never sent again (it closed the app): {', '.join(unsafe)}.")
        if crashed_reads:
            lines.append(f"Reading these closed the app, so they are never read: {', '.join(crashed_reads)}.")
        lines.append(f"{sum(1 for v in reads.values() if v == 'ok')} settings can be read.")
        return lines
