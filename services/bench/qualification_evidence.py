"""Qualification evidence records and the H14 binding manifest (qualification/q-procedures.md §6, study §3.3).

- `validate(record)` checks a record has the fields q-procedures.md §6 lists, with hashes where hashes go, and that
  its procedure names one of the frozen procedure files by its SHA-256.
- `canonical(record)` is the signed bytes: sorted keys, no whitespace, `signatures` removed.
- `sign` and `verify` use `ssh-keygen -Y sign/verify` with namespace `hooke-qualification` and the signer registry
  (an `allowed_signers` file, `contract/qualification-signers` once Will creates it by PR).
- `check_manifest(manifest)` refuses a binding manifest with any null field, and synthetic data where the design
  requires real microscopy (§4.2).

This is the import format the acceptance runner lacks (`make scientific-acceptance` reports these children as
`external`). Wiring the runner to accept verified records is later H14 work; nothing here marks a child passed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

NAMESPACE = "hooke-qualification"
FORMAT = "hooke-qualification-evidence/1"
FROZEN = {
    "h14-study-design.md": "3a5b915760559eed21f926e0cd6d115fc1a70dd9fccc7c032248bcf23aeaa80d",
    "q-procedures.md": "f900cda20db258a44f8e222fcac25d077087c1232d1591d011c1b1d2a9bd28c8",
}
ASPECTS = {"configured", "operational", "usable", "platform-macos", "platform-windows", "platform-linux"}
RESULTS = {"pass", "fail", "inconclusive"}
SHA = re.compile(r"^[0-9a-f]{64}$")
REQUIRED = ("format", "case", "group", "aspect", "artifact", "target", "fixtures", "procedure", "started", "ended",
            "result", "interventions", "retries", "limitations", "files", "operator", "reviewer", "signatures")
ARTIFACT = ("app", "sdk", "bundle", "environment_lock", "model")
TARGET = ("os", "build", "arch", "hardware", "gpu", "driver", "image", "reset", "instance")
REAL_DATA_TASKS = ("T5", "T7", "T8", "T9", "T13")


class EvidenceError(ValueError):
    pass


def validate(record: dict) -> dict:
    missing = [k for k in REQUIRED if k not in record]
    if missing:
        raise EvidenceError(f"missing fields: {', '.join(missing)}")
    if record["format"] != FORMAT:
        raise EvidenceError(f"format must be {FORMAT}")
    if record["aspect"] not in ASPECTS or record["result"] not in RESULTS:
        raise EvidenceError("aspect or result is not one q-procedures.md §6 allows")
    for k in ARTIFACT:
        if k not in record["artifact"]:
            raise EvidenceError(f"artifact.{k} missing")
    for k in ("app", "bundle", "environment_lock"):
        if not SHA.match(str(record["artifact"][k])):
            raise EvidenceError(f"artifact.{k} must be a SHA-256")
    if record["artifact"]["model"] is not None and not SHA.match(str(record["artifact"]["model"])):
        raise EvidenceError("artifact.model must be a SHA-256 or null")
    for k in TARGET:
        if not record["target"].get(k):
            raise EvidenceError(f"target.{k} missing or empty")
    if not SHA.match(str(record["target"]["image"])):
        raise EvidenceError("target.image must be a SHA-256")
    proc = str(record["procedure"])
    sha, _, section = proc.partition("#")
    if sha not in FROZEN.values() or not section:
        raise EvidenceError("procedure must be <frozen file SHA-256>#<section> of a frozen procedure")
    for group in ("fixtures", "files"):
        bad = [k for k, v in record[group].items() if not SHA.match(str(v))]
        if bad:
            raise EvidenceError(f"{group} without a SHA-256: {', '.join(bad)}")
    if not record["files"]:
        raise EvidenceError("a record must list the files it rests on")
    if record["operator"] == record["reviewer"]:
        raise EvidenceError("operator and reviewer must be different people")
    return record


def canonical(record: dict) -> bytes:
    body = {k: v for k, v in record.items() if k != "signatures"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign(record: dict, key: Path, signer: str) -> dict:
    validate({**record, "signatures": record.get("signatures", [])})
    with tempfile.TemporaryDirectory() as tmp:
        msg = Path(tmp) / "record.json"
        msg.write_bytes(canonical(record))
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(key), "-n", NAMESPACE, str(msg)],
                       check=True, capture_output=True)
        sig = (Path(tmp) / "record.json.sig").read_text()
    return {**record, "signatures": [*record.get("signatures", []), {"signer": signer, "sig": sig}]}


def verify(record: dict, allowed_signers: Path) -> list[str]:
    """Return the signers whose signatures verify; raise if none does or any fails."""
    validate(record)
    if not record["signatures"]:
        raise EvidenceError("unsigned record")
    good = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, s in enumerate(record["signatures"]):
            sig = Path(tmp) / f"{i}.sig"
            sig.write_text(s["sig"])
            r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers), "-I", s["signer"],
                                "-n", NAMESPACE, "-s", str(sig)], input=canonical(record), capture_output=True)
            if r.returncode != 0:
                raise EvidenceError(f"signature {i} by {s['signer']!r} does not verify")
            good.append(s["signer"])
    return good


def _nulls(obj, path="") -> list[str]:
    if obj is None:
        return [path or "(root)"]
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in _nulls(v, f"{path}.{k}" if path else k)]
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in _nulls(v, f"{path}[{i}]")]
    return []


def check_manifest(manifest: dict) -> dict:
    """The binding manifest may start the final study only with no null field (§3.3) and real data where required."""
    nulls = _nulls(manifest)
    if nulls:
        raise EvidenceError("the binding manifest has null fields, so the study cannot start: " + ", ".join(nulls[:20])
                            + (" …" if len(nulls) > 20 else ""))
    if manifest.get("design_sha256") != FROZEN["h14-study-design.md"]:
        raise EvidenceError("the manifest must name the frozen study design by its SHA-256")
    synthetic = [t for t in REAL_DATA_TASKS if manifest.get("data", {}).get(t, {}).get("source") == "synthetic"]
    if synthetic:
        raise EvidenceError(f"real microscopy is required for {', '.join(synthetic)} (design §4.2)")
    if int(manifest["study"]["completers"]) < 1:
        raise EvidenceError("study.completers must be positive")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate, sign or verify a qualification evidence record")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("record", type=Path)
    s = sub.add_parser("sign")
    s.add_argument("record", type=Path)
    s.add_argument("--key", type=Path, required=True)
    s.add_argument("--signer", required=True)
    c = sub.add_parser("verify")
    c.add_argument("record", type=Path)
    c.add_argument("--allowed-signers", type=Path, required=True)
    m = sub.add_parser("manifest")
    m.add_argument("manifest", type=Path)
    args = ap.parse_args(argv)
    data = json.loads(args.record.read_text() if hasattr(args, "record") else args.manifest.read_text())
    if args.cmd == "validate":
        validate(data)
        print("valid")
    elif args.cmd == "sign":
        args.record.write_text(json.dumps(sign(data, args.key, args.signer), indent=1, sort_keys=True) + "\n")
        print("signed")
    elif args.cmd == "verify":
        print("verified:", ", ".join(verify(data, args.allowed_signers)))
    else:
        check_manifest(data)
        print("manifest complete")


if __name__ == "__main__":
    main()
