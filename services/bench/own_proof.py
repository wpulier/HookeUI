"""An entry's own proof, for those no model check runs (bench/catalog_proof.py runs this in its offline process): a Fiji
generation's proofs run again on the generation in use (fiji_generations.check), a contributed adapter run on a real
picture (env_runner.prove), a job's run on its fixture (jobs.prove). Prints {id: [ok, words]} as JSON."""
from __future__ import annotations

import json
import sys


def has_own_proof(m) -> bool:
    return m.runner in ("fiji", "python-env")


def prove(m) -> tuple[bool, str]:
    from orchestrator import env_runner, fiji_generations, jobs

    if m.runner == "fiji":
        try:
            proofs = fiji_generations.check()
        except fiji_generations.Failed as exc:
            return False, str(exc)
        if m.kind == "tool-function":
            said = proofs.get(m.id)
            return (bool(said), f"proven on Fiji: {said}" if said else "no proof of it ran (fiji_bindings.py)")
        return True, f"Fiji runs headless: {proofs.get('smoke')}"
    if jobs.is_job(m):
        p = jobs.prove(m)
    else:
        p = env_runner.prove(m)
    if not p.get("ok"):
        return False, f"its proof failed: {p.get('why')}"
    return True, p.get("says") or f"its adapter found {p.get('found')} on a {p.get('picture')} picture in {p.get('seconds')} s"


def main(ids: list[str]) -> int:
    from gpu_worker import manifests as catalog

    out = {}
    for i in ids:
        try:
            out[i] = prove(catalog.get(i))
        except Exception as exc:  # noqa: BLE001 - one entry's error is its line, never the others'
            out[i] = (False, f"{type(exc).__name__}: {exc}")
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
