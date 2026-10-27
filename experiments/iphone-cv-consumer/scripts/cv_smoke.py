#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke check for the iPhone CV consumer service.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--bridge-url", default="http://127.0.0.1:8765")
    return parser.parse_args()


def fetch(url: str, timeout: float = 4.0) -> bytes:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url: str, timeout: float = 4.0) -> dict[str, object]:
    return json.loads(fetch(url, timeout=timeout).decode("utf-8"))


def jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index < len(data) - 9:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2:
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if index + 7 > len(data):
                return None
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            return width, height
        index += segment_length
    return None


def print_check(ok: bool, label: str, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {label}{': ' + detail if detail else ''}")


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    bridge_url = args.bridge_url.rstrip("/")
    failed = False

    try:
        health = fetch_json(f"{base_url}/api/health")
        service_ok = health.get("service") == "iphone-cv-consumer"
        print_check(service_ok, "CV service reachable", str(health.get("state")))
        failed = failed or not service_ok
    except urllib.error.URLError as exc:
        print_check(False, "CV service reachable", str(exc))
        return 1
    except Exception as exc:
        print_check(False, "CV service reachable", str(exc))
        return 1

    bridge = health.get("bridge") if isinstance(health.get("bridge"), dict) else {}
    bridge_matches = str(bridge.get("url", "")).rstrip("/") == bridge_url
    print_check(bridge_matches, "Bridge dependency URL", str(bridge.get("url")))
    failed = failed or not bridge_matches

    health_ok = bool(health.get("ok"))
    print_check(
        health_ok,
        "Live CV frame decode",
        f"state={health.get('state')} mode={health.get('inputMode')} fps={health.get('estimatedFps')}",
    )
    failed = failed or not health_ok

    if health_ok:
        try:
            raw = fetch(f"{base_url}/raw.jpg")
            raw_dimensions = jpeg_dimensions(raw)
            raw_ok = bool(raw_dimensions) and len(raw) > 1000
            print_check(raw_ok, "Raw JPEG endpoint", f"bytes={len(raw)} dimensions={raw_dimensions}")
            failed = failed or not raw_ok
        except Exception as exc:
            print_check(False, "Raw JPEG endpoint", str(exc))
            failed = True

        try:
            processed = fetch(f"{base_url}/processed.jpg")
            processed_dimensions = jpeg_dimensions(processed)
            processed_ok = bool(processed_dimensions) and len(processed) > 1000
            print_check(
                processed_ok,
                "Processed JPEG endpoint",
                f"bytes={len(processed)} dimensions={processed_dimensions}",
            )
            failed = failed or not processed_ok
        except Exception as exc:
            print_check(False, "Processed JPEG endpoint", str(exc))
            failed = True
    else:
        print("CV service is running, but frames are not live yet.")
        print(f"Open camera bridge: {bridge_url}/camera/")

    if failed:
        print("\nCV smoke result: NOT READY")
        return 1

    print("\nCV smoke result: READY")
    print(f"CV dashboard: {base_url}")
    print(f"Processed stream: {base_url}/processed.mjpeg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
