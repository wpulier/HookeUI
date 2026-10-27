#!/usr/bin/env python3
"""Explicit release-Mac workflow: prepare one candidate, then publish those exact bytes.

See RELEASING.md. Branch merges never call this. Stable requires a published Beta
of the same source. The native bundles differ, so Stable is built and checked again.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from release_number import next_build, read
import release_companions as companions

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CHANNELS = json.loads((HERE / "channels.json").read_text())


def run(*args: str, capture: bool = False, env: dict | None = None) -> str:
    result = subprocess.run(args, cwd=REPO, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None, env=env)
    return result.stdout.strip() if capture else ""


def version(value: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Version must be three numbers, for example 0.2.2")
    return value


def require_source(channel: str, commit: str) -> None:
    """Beta comes from dev; Stable comes from main after same-source promotion."""
    if channel not in CHANNELS:
        raise ValueError("Unknown release channel")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use a full source commit SHA")
    branch = "dev" if channel == "beta" else "main"
    # An explicit refspec refreshes the exact remote branch even in narrow clones.
    # Missing branches or failed fetches must not reuse stale local eligibility.
    run("git", "fetch", "--quiet", "--no-tags", "origin",
        f"refs/heads/{branch}:refs/remotes/origin/{branch}")
    run("git", "merge-base", "--is-ancestor", commit, f"origin/{branch}")


def published_receipt(repo: str, tag: str) -> dict:
    url = f"https://github.com/{repo}/releases/download/{tag}/release.json"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def published_beta(version_number: str) -> dict:
    tag = "v" + version(version_number)
    repo = CHANNELS["beta"]["releases"]
    release = json.loads(run("gh", "api", f"repos/{repo}/releases/tags/{tag}", capture=True))
    if release["draft"] or release["prerelease"]:
        raise ValueError("The Beta is unpublished or withdrawn; it cannot be promoted")
    return published_receipt(repo, tag)


def require_promotion(channel: str, commit: str, beta: dict | None) -> None:
    if channel == "beta":
        if beta is not None:
            raise ValueError("Only Stable promotes a Beta")
        return
    if (not beta or beta.get("channel") != "beta" or beta.get("revision") != commit
            or beta.get("bundleId") != CHANNELS["beta"]["bundleId"]
            or not beta.get("notarized") or not beta.get("appcast")):
        raise ValueError("Stable must use the exact source of a published, notarized Beta")


def next_release_build(commit_count: int) -> int:
    feeds = []
    floor = commit_count
    for config in CHANNELS.values():
        # Include withdrawn releases: withdrawing an offer must never reuse its number.
        tags = run("gh", "api", f"repos/{config['releases']}/releases", "--paginate",
                   "--jq", '.[] | select(.draft | not) | .tag_name', capture=True).splitlines()
        for tag in tags:
            feed = read(f"https://github.com/{config['releases']}/releases/download/{tag}/appcast.xml")
            if feed is None:
                # The original October 19 download-only beta deliberately had no updater.
                # Its published receipt still counts; a missing *expected* feed is an error.
                receipt = published_receipt(config["releases"], tag)
                build = receipt.get("build")
                if (receipt.get("appcast") is not False or type(build) is not int or build < 1
                        or "v" + receipt.get("version", "") != tag):
                    raise ValueError(f"Published release {config['releases']} {tag} has no appcast; restore its receipt first")
                floor = max(floor, build + 1)
            else:
                feeds.append(feed)
    return next_build(floor, feeds)


def require_candidate(receipt: dict, intent: dict, sha256: str, out: Path, companions_sha256: str | None = None) -> None:
    if receipt.get("betaAcceptance") != intent.get("betaAcceptance"):
        raise ValueError("Beta acceptance changed after preparation")
    if intent.get("notesSha256"):
        if hashlib.sha256((out / "release-notes.md").read_bytes()).hexdigest() != intent["notesSha256"]:
            raise ValueError("Release notes changed after preparation; retain the frozen notes")
    if receipt.get("companions") != intent.get("companions"):
        raise ValueError("Companion identity changed after preparation")
    companions.verify(out, receipt.get("companions"), companions_sha256)
    channel = intent["channel"]
    if receipt.get("channel") != channel or receipt.get("bundleId") != CHANNELS[channel]["bundleId"]:
        raise ValueError("Candidate belongs to a different channel")
    if any(receipt.get(field) != intent[field] for field in ("revision", "version", "build")):
        raise ValueError("Candidate identity changed after preparation")
    if not receipt.get("notarized") or not receipt.get("appcast"):
        raise ValueError("Candidate must be notarized and have a signed update feed")
    name = receipt["dmg"]
    if Path(name).name != name:
        raise ValueError("Candidate filename must be local to its output directory")
    digest = hashlib.sha256()
    with (out / name).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if not sha256 or sha256 != actual or actual != receipt.get("dmgSha256"):
        raise ValueError("Approval hash does not match the exact candidate")


@contextmanager
def source_checkout(commit: str):
    """Native shell and packaging scripts must come from the selected source too."""
    with tempfile.TemporaryDirectory(prefix="hooke-release-source-") as temporary:
        source = Path(temporary) / "source"
        run("git", "worktree", "add", "--quiet", "--detach", str(source), commit)
        try:
            yield source
        finally:
            run("git", "worktree", "remove", "--force", str(source))


def prepare(args: argparse.Namespace) -> None:
    version(args.version)
    require_source(args.channel, args.commit)
    beta = published_beta(args.promote_from) if args.promote_from else None
    require_promotion(args.channel, args.commit, beta)
    out = args.out.resolve()
    if out.exists():
        raise ValueError("Use a new output directory; existing candidates are never overwritten")
    # Freeze companion bytes before building; author folders cannot change the candidate.
    with tempfile.TemporaryDirectory(prefix="hooke-release-companions-") as temporary:
        frozen = Path(temporary)
        notes = getattr(args, "notes", None) or os.environ.get("NOTES")
        if notes:
            shutil.copyfile(notes, frozen / "release-notes.md")
        manifest = getattr(args, "companions", None)
        binding = companions.stage(manifest, frozen) if manifest else None
        if args.channel == "stable":
            companions.promotion(beta.get("companions"), binding)
        count = int(run("git", "rev-list", "--count", args.commit, capture=True))
        build = next_release_build(count)
        env = {**os.environ, "RELEASE_CHANNEL": args.channel, "BUILD_NUMBER": str(build), "OUT": str(out),
               "TOOLS": str(Path(os.environ.get("TOOLS", HERE / "build")).resolve())}
        if notes:
            env["NOTES"] = str(frozen / "release-notes.md")
        with source_checkout(args.commit) as source:
            run("bash", str(source / "packaging/macos/build.sh"), args.version, args.commit, env=env)
        receipt = json.loads((out / "release.json").read_text())
        intent = {"channel": args.channel, "version": args.version, "revision": args.commit,
                  "build": build, "promote_from": args.promote_from, "evidence": args.evidence}
        if notes:
            shutil.copyfile(frozen / "release-notes.md", out / "release-notes.md")
            intent["notesSha256"] = hashlib.sha256((out / "release-notes.md").read_bytes()).hexdigest()
        if getattr(args, "acceptance", None):
            receipt["betaAcceptance"] = intent["betaAcceptance"] = args.acceptance
        if binding:
            shutil.copytree(frozen / "companions", out / "companions")
            receipt["companions"] = intent["companions"] = binding
        require_candidate(receipt, intent, receipt["dmgSha256"], out, binding["sha256"] if binding else None)
        if binding or getattr(args, "acceptance", None):
            (out / "release.json").write_text(json.dumps(receipt, indent=2) + "\n")
        (out / "release-intent.json").write_text(json.dumps(intent, indent=2) + "\n")
    print(f"Prepared {args.channel} {args.version}. Nothing published.\n"
          f"SHA256: {receipt['dmgSha256']}\nVerify this artifact before explicitly publishing {out}.")
    release_record(out)


def publish(args: argparse.Namespace) -> None:
    out = args.out.resolve()
    intent = json.loads((out / "release-intent.json").read_text())
    receipt = json.loads((out / "release.json").read_text())
    if args.channel != intent["channel"]:
        raise ValueError("The explicit channel differs from the prepared candidate")
    approval = getattr(args, "companions_sha256", None)
    require_candidate(receipt, intent, args.sha256, out, approval)
    require_source(args.channel, intent["revision"])
    beta = published_beta(intent["promote_from"]) if intent["promote_from"] else None
    require_promotion(args.channel, intent["revision"], beta)
    if args.channel == "stable":
        companions.promotion(beta.get("companions"), receipt.get("companions"))
    if next_release_build(0) > receipt["build"]:
        raise ValueError("A newer build was released after preparation; prepare a new candidate")
    # publish.sh verifies both signatures and the exact channel URL before touching GitHub.
    env = {**os.environ, "OUT": str(out)}
    if intent.get("notesSha256"):
        env["NOTES"] = str(out / "release-notes.md")
    extra = ["--companions-sha256", approval] if approval else []
    run("bash", str(HERE / "publish.sh"), "--channel", args.channel, *extra, env=env)
    (out / "publication-receipt.json").write_text(json.dumps({**intent, "dmgSha256": args.sha256,
        "url": f"https://github.com/{CHANNELS[args.channel]['releases']}/releases/tag/v{intent['version']}"}, indent=2) + "\n")
    release_record(out, sync=bool(intent.get("notesSha256")))


def release_record(out: Path, *, sync: bool = False) -> None:
    """One derived handoff, not another editable status ledger."""
    receipt = json.loads((out / "release.json").read_text())
    intent = json.loads((out / "release-intent.json").read_text())
    require_candidate(receipt, intent, receipt["dmgSha256"], out,
                      receipt.get("companions", {}).get("sha256"))
    publication = out / "publication-receipt.json"
    published = publication.exists()
    config = CHANNELS[receipt["channel"]]
    url = f"https://github.com/{config['releases']}/releases/tag/v{receipt['version']}"
    if published and json.loads(publication.read_text()) != {**intent, "dmgSha256": receipt["dmgSha256"], "url": url}:
        raise ValueError("Publication receipt differs from the prepared candidate")
    download = f"https://github.com/{config['releases']}/releases/download/v{receipt['version']}/{receipt['dmg']}"
    lines = [f"# {config['name']} {receipt['version']} ({receipt['build']})", "",
             f"Status: {'published; public offer and bytes verified' if published else 'prepared; not published'}.",
             f"Source: `{receipt['revision']}`", f"DMG SHA256: `{receipt['dmgSha256']}`",
             f"[Download]({download}) · [Release files]({url})"]
    if intent.get("evidence"):
        lines.append(f"[Evidence]({intent['evidence']})")
    if receipt.get("companions"):
        lines.append(f"Companion manifest SHA256: `{receipt['companions']['sha256']}`")
    acceptance = intent.get("betaAcceptance")
    if acceptance:
        lines += ["", f"Accepted Beta: {acceptance['version']} ({acceptance['build']}), source `{acceptance['revision']}`.",
                  f"Owner acceptance: {acceptance['statement']}"]
    else:
        lines += ["", "Installed owner acceptance is separate; record it when promoting this Beta."]
    notes = out / "release-notes.md"
    if intent.get("notesSha256"):
        lines += ["", notes.read_text().strip()]
    lines += ["", "Use Check for Updates in this channel's app; save drafts and choose when to restart.",
              "For first installation, open the DMG and drag the app into Applications. Apple Silicon, macOS 14 or later.",
              "FFmpeg/SRT/OpenSSL sources and build scripts are attached to the release.", ""]
    path = out / "release-record.md"
    path.write_text("\n".join(lines))
    if sync:
        if not published:
            raise ValueError("Cannot sync a release record before confirmed publication")
        if published_receipt(config["releases"], "v" + receipt["version"]) != receipt:
            raise ValueError("Published receipt differs; do not overwrite its release record")
        run("gh", "release", "edit", "v" + receipt["version"], "--repo", config["releases"],
            "--notes-file", str(path.resolve()))
    print(f"Release record: {path}")


def ship(args: argparse.Namespace) -> None:
    """Convenience actions reuse the existing build/publish boundaries and lock."""
    if args.command == "promote":
        local = json.loads((args.beta / "release.json").read_text())
        beta = published_beta(local["version"])
        if local != beta:
            raise ValueError("Local Beta receipt differs from the published Beta")
        intent = json.loads((args.beta / "release-intent.json").read_text())
        require_candidate(local, intent, local["dmgSha256"], args.beta,
                          local.get("companions", {}).get("sha256"))
        args.channel, args.commit = "stable", beta["revision"]
        require_promotion("stable", args.commit, beta)
        args.version = args.promote_from = beta["version"]
        args.companions = args.beta / "companions/companions.json" if beta.get("companions") else None
        args.acceptance = {key: beta[key] for key in ("version", "build", "revision", "dmgSha256")}
        if not args.accepted.strip():
            raise ValueError("Record Will's actual installed Beta acceptance")
        args.acceptance["statement"] = args.accepted.strip()
        args.notes = args.notes or args.beta / "release-notes.md"
    else:
        args.channel, args.promote_from = "beta", None
    out = args.out.resolve()
    if out.exists():
        receipt = json.loads((out / "release.json").read_text())
        intent = json.loads((out / "release-intent.json").read_text())
        if any(intent[key] != getattr(args, key) for key in ("channel", "version")) or intent["revision"] != args.commit:
            raise ValueError("Existing directory belongs to another candidate; use its recorded source or a new directory")
        if args.command == "promote" and intent.get("betaAcceptance") != args.acceptance:
            raise ValueError("Existing candidate has a different Beta acceptance record")
        require_candidate(receipt, intent, receipt["dmgSha256"], out, receipt.get("companions", {}).get("sha256"))
        if not intent.get("notesSha256") and not (out / "publication-receipt.json").exists():
            # Upgrade only unpublished legacy metadata; retain the signed artifact.
            notes = args.notes.read_bytes()
            (out / "release-notes.md").write_bytes(notes)
            intent["notesSha256"] = hashlib.sha256(notes).hexdigest()
            (out / "release-intent.json").write_text(json.dumps(intent, indent=2) + "\n")
        release_record(out)
        print("Reusing the prepared candidate; no rebuild.")
    elif not args.notes.is_file():
        raise ValueError("Provide release notes with the walkthrough, changes and known limits")
    if (out / "publication-receipt.json").exists():
        release_record(out)
        print("Already published. No new build or publication; use record --sync for a failed notes sync.")
        return
    if args.command == "promote":
        if args.promotion_pr:
            pr = json.loads(run("gh", "api", f"repos/wpulier/HookeUI/pulls/{args.promotion_pr}", capture=True))
            if pr["base"]["ref"] != "main" or pr["head"]["sha"] != args.commit:
                raise ValueError("Promotion PR must target main with exactly the accepted Beta head")
            if not pr["merged"]:
                run("git", "fetch", "--quiet", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
                merged_tree = run("git", "merge-tree", "--write-tree", "origin/main", args.commit, capture=True)
                accepted_tree = run("git", "rev-parse", args.commit + "^{tree}", capture=True)
                if merged_tree != accepted_tree:
                    raise ValueError("Promotion would introduce changes outside the accepted Beta; reconcile through dev/Beta")
                run("gh", "pr", "merge", str(args.promotion_pr), "--repo", "wpulier/HookeUI",
                    "--merge", "--match-head-commit", args.commit)
        require_source("stable", args.commit)
        run("git", "diff", "--exit-code", args.commit, "origin/main")
    if not out.exists():
        prepare(args)
    if args.publish:
        receipt = json.loads((out / "release.json").read_text())
        publish(argparse.Namespace(channel=args.channel, out=out, sha256=receipt["dmgSha256"],
                                  companions_sha256=receipt.get("companions", {}).get("sha256")))
    else:
        print("Inspect release-record.md. Add --publish only when publication is authorized.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("prepare", help="build/sign/notarize only; never publishes")
    build.add_argument("channel", choices=CHANNELS)
    build.add_argument("version")
    build.add_argument("commit")
    build.add_argument("--promote-from", help="required for Stable: accepted Beta version")
    build.add_argument("--evidence", required=True, help="HTTPS link to the release ticket and checks")
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--companions", type=Path, help="explicit local companion manifest; Stable must match its Beta")
    offer = commands.add_parser("publish", help="offer an approved exact candidate; does not rebuild")
    offer.add_argument("channel", choices=CHANNELS)
    offer.add_argument("--out", type=Path, required=True)
    offer.add_argument("--sha256", required=True, help="approved DMG hash from release.json")
    offer.add_argument("--companions-sha256", help="separately approved companion manifest hash, required when present")
    beta = commands.add_parser("beta", help="prepare once, optionally publish; reuses an existing matching candidate")
    beta.add_argument("version", type=version)
    beta.add_argument("commit", help="full frozen dev SHA, never a moving branch name")
    beta.add_argument("--companions", type=Path)
    beta.add_argument("--notes", type=Path, required=True, help="changes, short walkthrough and known limits")
    promote = commands.add_parser("promote", help="promote an owner-accepted, published Beta; never selects the moving dev tip")
    promote.add_argument("beta", type=Path, help="retained published Beta output directory")
    promote.add_argument("--accepted", required=True, help="Will's actual installed Beta acceptance, quoted or linked")
    promote.add_argument("--promotion-pr", type=int, help="reviewed main PR pinned to the accepted Beta head; omit if already merged")
    promote.add_argument("--notes", type=Path, help="defaults to the Beta's frozen notes")
    for action in (beta, promote):
        action.add_argument("--evidence", required=True, help="HTTPS evidence or release ticket link")
        action.add_argument("--out", type=Path, required=True)
        action.add_argument("--publish", action="store_true", help="explicitly authorize offering this candidate after preparation; omit to prepare only")
    record = commands.add_parser("record", help="render the handoff from existing receipts; no build, test or publication")
    record.add_argument("out", type=Path)
    record.add_argument("--sync", action="store_true", help="refresh only an already-published release's GitHub notes")
    args = parser.parse_args()
    try:
        if args.command in ("prepare", "beta", "promote") and not args.evidence.startswith("https://"):
            raise ValueError("Evidence must be an HTTPS ticket/checks link")
        for name in ("RELEASE_CHANNEL", "RELEASES", "FEED_URL", "ED_PUBLIC_KEY_FILE", "SPARKLE_KEY_FILE", "ALLOW_UNNOTARIZED"):
            if name in os.environ:
                raise ValueError(f"Unset {name}; public channels use their canonical configuration")
        lockdir = Path.home() / ".config/hookeui-release"
        lockdir.mkdir(parents=True, exist_ok=True)
        with (lockdir / "channel-release.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == "record":
                release_record(args.out.resolve(), sync=args.sync)
            else:
                {"prepare": prepare, "publish": publish, "beta": ship, "promote": ship}[args.command](args)
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as error:
        print(f"Release stopped: {error}. Check retained artifacts before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
