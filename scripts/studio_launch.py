"""Read private launch defaults as data; never load key contents or execute config."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FIELDS = {"home": "MICROMIND_HOME", "keysFile": "MICROMIND_KEYS_FILE"}
MAX_BYTES = 16 * 1024


def defaults(environ: dict[str, str]) -> dict[str, str]:
    root = Path(environ.get("MICROMIND_ASSETS") or Path.home() / "MicroscopeMind").expanduser()
    config = root / "launch.json"
    if not config.exists():
        return {}
    try:
        with config.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("launch settings exceed 16 KiB")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("launch settings cannot be read as JSON") from exc
    if (not isinstance(data, dict) or type(data.get("schema")) is not int
            or data["schema"] != 1 or set(data) - {"schema", *FIELDS}):
        raise ValueError("launch settings require schema 1 and only home/keysFile paths")
    result = {}
    for field, variable in FIELDS.items():
        if field not in data:
            continue
        value = data[field]
        if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(f"launch setting {field} must be a nonempty path without control characters")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError(f"launch setting {field} must be an absolute path")
        # Explicit launch choices override the saved defaults, including an
        # explicit legacy/three-home selection and explicitly empty variables.
        if variable in environ or (field == "home" and "MICROMIND_SAVES" in environ):
            continue
        if not (path.is_dir() if field == "home" else path.is_file()):
            raise ValueError(f"launch setting {field} does not point to an existing {'directory' if field == 'home' else 'file'}")
        result[variable] = str(path)
    return result


if __name__ == "__main__":
    try:
        for key, value in defaults(dict(os.environ)).items():
            print(f"{key}={value}")
    except ValueError as error:
        print(f"HookeUI: {error}. Check launch.json in the shared data home; no services were stopped.", file=sys.stderr)
        raise SystemExit(2)
