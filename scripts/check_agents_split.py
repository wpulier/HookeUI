"""Check that a split of the root AGENTS.md moved every paragraph, losing none and copying none.

The root AGENTS.md became a map on 2026-10-15: each part's description moved, verbatim, to a file the map names
(a nested AGENTS.md beside its code, or docs/architecture/). This compares the old file with the map and those
files: every paragraph, list item, heading and code block of the old file must appear exactly as often as before
(once), whitespace and indentation aside. It also says where each old section went.

    python3 scripts/check_agents_split.py                      # old file: origin/main's AGENTS.md
    python3 scripts/check_agents_split.py --old e92e4111       # a commit before the split
    python3 scripts/check_agents_split.py --old path/to/AGENTS.md

It is a check of the move itself: once the moved text is edited, run it against the split's own commit.
"""
import argparse
import collections
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ITEM = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")


def units(text):
    """The file's paragraphs, list items (each with its continuation lines), headings and code blocks, normalized,
    each with the "## " section it is in."""
    out, current, fence, section = [], [], None, "(top)"

    def flush():
        if current:
            out.append((section, " ".join(" ".join(current).split())))
            current.clear()

    for line in text.split("\n"):
        if fence is not None:
            fence.append(line)
            if line.strip().startswith("```"):
                out.append((section, "\n".join(l.strip() for l in fence)))
                fence = None
            continue
        if line.strip().startswith("```"):
            flush()
            fence = [line]
        elif not line.strip():
            flush()
        elif line.startswith("#"):
            flush()
            if line.startswith("## "):
                section = line[3:].strip()
            out.append((section, line.lstrip("#").strip()))
        elif ITEM.match(line):
            flush()
            current.append(line.strip())
        else:
            current.append(line.strip())
    flush()
    return out


def old_text(spec):
    path = Path(spec)
    if path.is_file():
        return path.read_text()
    return subprocess.run(["git", "show", f"{spec}:AGENTS.md"], cwd=ROOT, check=True, capture_output=True,
                          text=True).stdout


def new_files():
    """The map and every part's file it names."""
    names = re.findall(r"`((?:[\w.-]+/)*(?:AGENTS\.md|[\w.-]+\.md))`", (ROOT / "AGENTS.md").read_text())
    parts = [n for n in dict.fromkeys(names) if n.endswith("AGENTS.md") or n.startswith("docs/architecture/")]
    return ["AGENTS.md"] + [n for n in parts if n != "AGENTS.md"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--old", default="origin/main", help="a git ref whose AGENTS.md is the old file, or a path")
    args = parser.parse_args()
    old = old_text(args.old)
    files = new_files()
    missing_files = [f for f in files if not (ROOT / f).is_file()]
    if missing_files:
        sys.exit(f"the map names files that do not exist: {missing_files}")
    where = collections.defaultdict(list)
    for f in files:
        for _, u in units((ROOT / f).read_text()):
            where[u].append(f)

    wanted = collections.Counter(u for _, u in units(old))
    problems, landed = [], collections.defaultdict(collections.Counter)
    for section, u in units(old):
        found = where.get(u, [])
        if len(found) != wanted[u]:
            problems.append(f"{'lost' if not found else 'found ' + str(len(found)) + 'x'} in {section}: {u[:100]}")
        for f in set(found):
            landed[section][f] += 1

    for name, files_ in landed.items():
        print(f"{name}\n    " + "\n    ".join(f"{n:4d}  {f}" for f, n in files_.most_common()))
    total = sum(wanted.values())
    print(f"\n{total} paragraphs, items, headings and code blocks in the old AGENTS.md; "
          f"{total - len(problems)} found exactly once in {len(files)} files.")
    if problems:
        print("\n".join(problems))
        sys.exit(1)
    data = (ROOT / "AGENTS.md").read_text()
    print(f"The map: {data.count(chr(10))} lines, {len(data.encode())} bytes (old: {old.count(chr(10))} lines, "
          f"{len(old.encode())} bytes).")


if __name__ == "__main__":
    main()
