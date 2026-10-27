"""Keys the app saves: the macOS Keychain, through `keyring` (docs/design/2026-10-12-the-open-catalog.md, P1).

One Keychain service, "MicroscopeMind" (MICROMIND_KEYCHAIN_SERVICE names another: the Mac app's Beta keeps its keys
apart from Stable's, packaging/macos/channels.json); the account is the key's name (GEMINI_API_KEY...). Values are read
here and put into the process's environment by keys.py, never printed, logged, sent to the studio or told to a model.

What was read is kept for the process (a Keychain read can take a few milliseconds and the capabilities wire reads keys
often); this module's own set and delete keep it current. MICROMIND_KEYCHAIN=off turns the Keychain off (the tests, so
they never read this Mac's keys); MICROMIND_KEYCHAIN=file:<path> keeps a second stack's saved keys in a file of its own
(mode 0600) instead, so trying Add key there never touches this Mac's Keychain. Without `keyring` it is off, and the
studio offers no Add key.
"""
from __future__ import annotations

import os
import threading

SERVICE = "MicroscopeMind"
_lock = threading.Lock()
_read: dict[str, str | None] = {}
_store = None  # a test's stand-in: an object with get_password, set_password, delete_password


class FileStore:
    """A second stack's own saved keys (MICROMIND_KEYCHAIN=file:<path>): JSON, readable by its owner only."""

    def __init__(self, path: str) -> None:
        self.path = path

    def _all(self) -> dict:
        import json

        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        import json

        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)

    def get_password(self, service: str, name: str) -> str | None:
        return self._all().get(name)

    def set_password(self, service: str, name: str, value: str) -> None:
        self._write({**self._all(), name: value})

    def delete_password(self, service: str, name: str) -> None:
        data = self._all()
        data.pop(name, None)
        self._write(data)


def enabled() -> bool:
    if os.environ.get("MICROMIND_KEYCHAIN", "").lower() in ("off", "0", "no"):
        return _store is not None
    return _store is not None or _backend() is not None


def service() -> str:
    """The Keychain service keys are saved under: SERVICE, or MICROMIND_KEYCHAIN_SERVICE."""
    return os.environ.get("MICROMIND_KEYCHAIN_SERVICE") or SERVICE


def _backend():
    if _store is not None:
        return _store
    setting = os.environ.get("MICROMIND_KEYCHAIN", "")
    if setting.startswith("file:"):
        return FileStore(setting[len("file:"):])
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def get(name: str) -> str | None:
    """The app-saved value of a key, or None."""
    if not enabled():
        return None
    with _lock:
        if name not in _read:
            try:
                _read[name] = _backend().get_password(service(), name) or None
            except Exception:  # noqa: BLE001 - a locked or unavailable Keychain is "no saved key", never a crash
                _read[name] = None
        return _read[name]


def set(name: str, value: str) -> None:
    if not enabled():
        raise RuntimeError("keys can't be saved on this Mac (no Keychain)")
    with _lock:
        _backend().set_password(service(), name, value)
        _read[name] = value


def delete(name: str) -> None:
    if not enabled():
        return
    with _lock:
        try:
            _backend().delete_password(service(), name)
        except Exception:  # noqa: BLE001 - nothing saved is nothing to delete
            pass
        _read[name] = None


def forget() -> None:
    """Read the Keychain afresh next time (a restart, a test)."""
    with _lock:
        _read.clear()
