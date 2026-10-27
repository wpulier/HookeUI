"""Check local build prerequisites before downloading packages or stopping a Studio."""
import shutil
import re
import subprocess
import sys


def main():
    missing = []
    for command, fix in (
        ("uv", "Install uv (brew install uv)."),
        ("node", "Install supported Node.js (brew install node@24)."),
        ("npm", "Install npm with Node.js (brew install node@24)."),
        ("ffmpeg", "Install FFmpeg (brew install ffmpeg)."),
        ("ffprobe", "Install FFmpeg (brew install ffmpeg)."),
        ("swiftc", "Install Xcode Command Line Tools (xcode-select --install)."),
        ("codesign", "Install Xcode Command Line Tools (xcode-select --install)."),
    ):
        if not shutil.which(command):
            missing.append(f"{command} is missing. {fix}")
    if shutil.which("node"):
        try:
            version = subprocess.run(["node", "--version"], check=True, capture_output=True,
                                     text=True, timeout=10).stdout.strip()
            match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", version)
            major, minor = (int(match[1]), int(match[2])) if match else (0, 0)
        except (OSError, subprocess.SubprocessError):
            major, minor = 0, 0
        # Intersection of the locked Vite 8 and Vitest 5 engine ranges.
        if not ((major == 22 and minor >= 12) or major == 24 or major >= 26):
            missing.append("Node.js must be 22.12+ within 22.x, 24.x, or 26+ (the Studio build/test contract). "
                           "Install a supported version (brew install node@24) and put its bin directory on PATH.")
    if missing:
        print("ObservationUI setup prerequisites:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 1
    print("ObservationUI setup prerequisites are available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
