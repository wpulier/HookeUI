"""One maintenance attempt for one entry, run in the low-priority maintenance worker (worker.py), never on the loop.

Under review or auto (policy.effective), one attempt:
1. checks the entry's source and obtains a newer commit (discover.check);
2. compares the update candidate's declared facts with the active generation's (compat.compare): anything only a person
   can accept stops it at Needs review, said exactly;
3. checks this Mac (readiness.misfit), its code trust and its integration;
4. gets its pinned files (downloads.download) and builds its generation beside the active one (envs.build);
5. proves it (env_runner.prove: the adapter contract and its declared examples);
6. under auto, promotes it (promote.promote, with its first check and rollback); under review, waits at Ready to apply.

Each step is written to attempts/<key>.json before it starts, so status can say what is happening and a later attempt
resumes from what is already there: a published revision is not downloaded again, a receipted generation is not built
again. Stop ends only this attempt. A policy revoked or lowered meanwhile ends it before it promotes.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog

from . import compat, discover, policy, store

RUNNING = ("preparing", "checking", "applying")
SPARE_BYTES = 2 * 1024 ** 3  # free space an attempt leaves untouched beyond what it needs (MT4)
ENV_BYTES = 1024 ** 3  # what one environment's build may take when nothing better is known
LOOK_S = 0.5  # how often a running attempt reads its policy again (a revocation ends it)


def path(key: str, environ: Mapping[str, str] = os.environ):
    return store.path(f"attempts/{store.safe(key)}.json", environ)


def last(key: str, environ: Mapping[str, str] = os.environ) -> dict:
    return store.read(path(key, environ), {})


def unfinished(environ: Mapping[str, str] = os.environ) -> list[str]:
    """Entries whose attempt stopped while applying (no live process): each resumes its first check (_resume)."""
    root = store.path("attempts", environ)
    out = []
    for p in sorted(root.glob("*.json")) if root.is_dir() else ():
        row = store.read(p, {})
        if row.get("state") == "applying" and row.get("key") and not alive(row):
            out.append(row["key"])
    return out


def alive(att: dict) -> bool:
    try:
        os.kill(int(att.get("pid", 0)), 0)
        return int(att.get("pid", 0)) > 0
    except (OSError, ValueError):
        return False


class _Attempt:
    def __init__(self, key: str, environ: Mapping[str, str]):
        self.key, self.environ = key, environ
        self.row = {"key": key, "pid": os.getpid(), "started": store.now(), "state": "preparing",
                    "says": "Looking for an update", "target": {}}
        self._looked, self._stopped = 0.0, False
        self.save()

    def save(self, **change) -> dict:
        self.row.update(change, updated=store.now())
        store.write(path(self.key, self.environ), self.row)
        return dict(self.row)

    def end(self, state: str, says: str, **change) -> dict:
        return self.save(state=state, says=says, pid=0, ended=store.now(), **change)

    def stop(self) -> bool:
        """True once the policy no longer allows this attempt; read at most every LOOK_S."""
        now = time.monotonic()
        if not self._stopped and now - self._looked >= LOOK_S:
            self._looked = now
            self._stopped = policy.get(self.key, self.environ)["policy"] not in ("review", "auto")
        return self._stopped


@contextmanager
def _entry_lock(key: str, environ: Mapping[str, str]):
    """Both manual Apply and the worker own the entry's full attempt, including its first check."""
    import fcntl

    lock = store.path(f"attempts/{store.safe(key)}.lock", environ)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(fd)  # the lock goes with it, also when the process is killed


def run(key: str, environ: Mapping[str, str] = os.environ, *, again: bool = False) -> dict:
    """Run one attempt under its entry lock; `again` means a person's Check/Retry bypasses the look cache."""
    with _entry_lock(key, environ) as acquired:
        if not acquired:
            return {"key": key, "state": "busy", "says": "Its update is being prepared already"}
        return _run(key, environ, again=again)


def apply(m: catalog.Manifest, generation: str, environ: Mapping[str, str] = os.environ, *, digest: str | None = None) -> dict:
    """A person's Apply journals the same recoverable first check without authorizing future updates."""
    from .. import env_runner
    from . import promote

    with _entry_lock(m.key, environ) as acquired:
        if not acquired:
            raise promote.Failed("Its update is being prepared already")
        previous = last(m.key, environ)
        if previous.get("state") == "applying":
            raise promote.Failed("Its previous update's first check must finish; choose Check for an update")
        target = env_runner.generation(m, generation, environ)
        if target is None:
            raise promote.Failed(f"that version of {m.name} is no longer here, ready and proven")
        from ..bundle_admission import local_workflow
        if local_workflow(target.m):
            from . import workflow
            try:
                workflow.require_accepted(m, generation, digest, environ)
            except ValueError as exc:
                raise promote.Failed(str(exc)) from exc
        active = env_runner.selected(m, environ)
        a = _Attempt(m.key, environ)
        a.save(state="applying", says=f"Applying {target.m.version}: making it active, then checking it runs",
               target={"generation": generation, "version": target.m.version,
                       "from": active.folder.name if active else "", "fromVersion": active.m.version if active else ""})
        try:
            decision = promote.promote(m, generation, by="user", environ=environ, check=False)
        except promote.Failed:
            store.write(path(m.key, environ), previous)  # A refusal preserves the exact Needs review card.
            raise
        decision = promote.check_after(m, decision, by="user", environ=environ)
        if decision["action"] == "rollback":
            a.end("failed", f"Update failed: {decision['why']}. {decision['version']} stays in use",
                  why=decision["why"], decision=decision)
        else:
            a.end("applied", f"Updated to {decision['version']}", decision=decision)
        return decision


def _room(m: catalog.Manifest, environ: Mapping[str, str]) -> str:
    """"" when the disk has room for the candidate's files and environment beside SPARE_BYTES; else what it needs."""
    import shutil

    from .. import downloads
    from gpu_worker import model_files

    need = ENV_BYTES + (downloads.needs_bytes(model_files.owner(m), environ) if m.files else 0)
    root = catalog.catalog_root(environ)
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    if free >= need + SPARE_BYTES:
        return ""
    return f"needs {downloads.size_words(need + SPARE_BYTES)} free, the disk has {downloads.size_words(free)}"


def _run(key: str, environ: Mapping[str, str], *, again: bool) -> dict:
    from .. import envs, env_runner, owned_process
    from .. import downloads
    from . import promote

    m = discover.fresh(key, environ)
    if m is None:
        return {"key": key, "state": "none", "says": "It is no longer in the catalog"}
    before = last(key, environ)
    if before.get("state") == "applying" and not alive(before):  # it stopped between promotion and its first check
        return _resume(m, before, environ)
    allowed, _less = policy.effective(m, environ)
    if allowed not in ("review", "auto"):
        return {"key": key, "state": "none", "says": "Its policy allows nothing unattended"}
    a = _Attempt(key, environ)
    try:
        rec = discover.check(m, environ, obtain=True, again=again)
        if not rec["ok"]:
            return a.end("none", f"Couldn't check for an update: {rec.get('why')}", why=rec.get("why", ""))
        if rec.get("removed"):
            return a.end("none", f"Its catalog's newest commit ({rec['removed']['commit'][:7]}) no longer has it; "
                                 "it keeps working here", why="removed upstream")
        found = rec.get("found")
        if not found:
            return a.end("none", "Up to date")
        if found.get("derived"):  # yours: merged from its recorded base, applied by you (adapt.py)
            from . import adapt

            report = store.read(Path(found["derived"]) / "merge.json", {})
            a.save(target={"derived": found["derived"], "version": found.get("version", "")})
            left = adapt.unresolved(Path(found["derived"]), report)
            state = "needs-review" if left else "ready-to-apply"
            return a.end(state, adapt.words(report, left), derived=found["derived"])
        catalog_m = discover.fresh(key, environ)  # the pinned catalog's entry: the key promotion and selection use
        m = discover.candidate_manifest(catalog_m, found["snapshot"], environ) if found.get("snapshot") else catalog_m
        if m is None:
            return a.end("none", "Its candidate snapshot no longer has it", why="removed upstream")
        active = env_runner.selected(catalog_m, environ)
        target = {"generation": found.get("generation", ""), "version": m.version, "commit": found.get("commit", ""),
                  "from": active.folder.name if active else "", "fromVersion": active.m.version if active else "",
                  **({"snapshot": found["snapshot"]} if found.get("snapshot") else {})}
        a.save(target=target)
        if active is None:
            return a.end("none", f"{m.name} isn't turned on here: nothing to maintain")
        from . import revocations
        if (notice := revocations.revoked(m, m.version, environ)) is not None:  # never promote a revoked version (MT3)
            return a.end("unavailable", f"Update unavailable here: {m.version} was revoked by its maintainers: "
                                        f"{notice['why']}", why=notice["why"])
        result = compat.compare(active.m, m)
        held_for_review = ""  # prepared and proven first, then waits for the person (review.prepare_first)
        if not result["compatible"]:
            from .. import envs
            from . import review

            try:
                generation = envs.generation(m, environ)
            except OSError:
                generation = ""
            mark = review.fingerprint(m, generation, result["review"])
            said = f"Needs review: {compat.words(result['review'])}"
            if review.accepted(m, mark, environ) is None:
                if not review.prepare_first(result["review"]):
                    return a.end("needs-review", said, review=result["review"], fingerprint=mark)
                held_for_review = said
                a.save(review=result["review"], fingerprint=mark)
            if step := review.missing_setup(active.m, m, environ):  # acceptance is not setup: never promote what can't run
                return a.end("needs-review", f"{said}. First: {step}", review=result["review"], fingerprint=mark,
                             setup=step)
        problem = _machine(m, environ)
        if problem:
            return a.end("unavailable", f"Update unavailable here: {problem}", why=problem)
        if a.stop():
            return a.end("stopped", "Stopped: its maintenance policy changed")
        if short := _room(m, environ):  # this entry alone waits; the others go on (MT4)
            return a.end("unavailable", f"Update unavailable here: it {short}", why=short)
        if m.files:
            from gpu_worker import model_files

            o = model_files.owner(m)
            if model_files.published(o, environ) is None:
                need = downloads.needs_bytes(o, environ)
                a.save(state="preparing", says=f"Preparing update: downloading {downloads.size_words(need)} to "
                                              f"{model_files.revision_dir(o, environ)}")
                downloads.download(m, environ=environ, stop=a.stop)
        a.save(state="preparing", says=f"Preparing update: building its own Python ({envs.size_hint(m, environ)} "
                                       "packages, each checked by its hash)")
        env_runner.preserve_active(m, environ)
        d = envs.build(m, environ, say=lambda step: a.save(says=f"Preparing update: {step}"), stop=a.stop)
        from ..bundle_admission import local_workflow
        if local_workflow(m):
            target['generation'] = d.name
            a.save(target=target, folder=str(d))
            return a.end('needs-review', 'Prepared beside the current version. In a saved Project choose Try update, use its controls, Save tool and reopen. Then review the update results in Models before Apply.', prepared=True)
        a.save(state="checking", says="Checking update: running its proof and declared examples", folder=str(d))
        from .. import generation_leases

        proven = env_runner.generation(catalog_m, d.name, environ)  # proven by an earlier attempt or Install: not again
        if proven is not None:
            proof = proven.proof
        else:
            with generation_leases.use(d):
                proof = env_runner.prove(m, environ, stop=a.stop)
        if a.stop():
            return a.end("stopped", "Stopped before applying: its maintenance policy changed")
        if not proof.get("ok"):
            why = proof.get("why") or "its proof didn't pass"
            return a.end("failed", f"Update failed: {why}. {target['fromVersion']} stays in use", why=why)
        target["generation"] = d.name
        a.save(target=target)
        if held_for_review:  # built and proven; a person accepts what changed, and then it is promoted
            return a.end("needs-review", f"{held_for_review} (prepared and proven here)", prepared=True)
        allowed, less = policy.effective(m, environ)
        if allowed != "auto":
            return a.end("ready-to-apply", f"Ready to apply: {m.version}" + (f" ({less})" if less else ""), less=less)
        # Said before the pointer moves: a stop from here on resumes with the first check (_resume), never as a failure.
        a.save(state="applying", says=f"Applying {m.version}: making it active, then checking it runs")
        decision = promote.promote(catalog_m, d.name, by="maintenance:auto", environ=environ)
        if decision["action"] == "rollback":
            return a.end("failed", f"Update failed: {decision['why']}. {target['fromVersion']} stays in use",
                         why=decision["why"], decision=decision)
        return a.end("applied", f"Updated to {m.version}", decision=decision)
    except (envs.Failed, env_runner.Failed, downloads.Failed, downloads.Cancelled, promote.Failed,
            owned_process.Stopped) as exc:
        if a.stop():
            return a.end("stopped", "Stopped: its maintenance policy changed")
        why = str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
        was = a.row.get("target", {}).get("fromVersion") or "the version in use"
        return a.end("failed", f"Update failed: {why}. {was} stays in use", why=why)


def _resume(m: catalog.Manifest, before: dict, environ: Mapping[str, str]) -> dict:
    """An attempt that stopped while applying: if its promotion happened and no first check followed, run the check now
    and roll back on failure; if the promotion never happened, say so. The row is this attempt's."""
    from .. import env_runner
    from . import promote

    a = _Attempt(m.key, environ)
    target = before.get("target") or {}
    a.save(target=target, state="applying", says=f"Completing {target.get('version', '')}'s interrupted first check")

    def failed(decision: dict) -> dict:
        policy.hold(m.key, f"the update to {target.get('version', 'the newer version')} failed its first check: "
                    f"{decision['why']}", environ)
        return a.end("failed", f"Update failed: {decision['why']}. {decision['version']} stays in use",
                     why=decision["why"], decision=decision)

    with store.locked(environ):
        active = env_runner.chosen(m, environ)  # The promoted folder may be what broke.
        moves = promote.decisions(m.key, environ, moves=True)
        last_move = moves[-1] if moves else {}
        if (last_move.get("action") == "rollback" and last_move.get("from") == target.get("generation")
                and last_move.get("to") == active and last_move.get("by", "").endswith(":first-check")):
            return failed(last_move)  # Rollback finished before the crash; retain it, without repeating the move.
        if (not active or last_move.get("action") != "promote" or last_move.get("to") != active
                or active != target.get("generation")):
            return a.end("none", "Its update was stopped before it was applied; the version in use is unchanged")
        checked = next((d for d in reversed(promote.decisions(m.key, environ))
                        if d.get("action") == "checked" and d.get("of") == last_move.get("at")
                        and d.get("to") == active), None)
        if checked is not None:
            if checked.get("ok"):
                return a.end("applied", f"Updated to {last_move.get('version', '')}", decision=last_move)
            decision = promote.rollback(m, by=f"{last_move.get('by', 'maintenance:auto')}:first-check",
                                        why=checked.get("why") or "its first check failed", environ=environ)
            return failed(decision)
    decision = promote.check_after(m, last_move, by=last_move.get("by", "maintenance:auto"), environ=environ)
    if decision["action"] == "rollback":
        return a.end("failed", f"Update failed: {decision['why']}. {decision['version']} stays in use",
                     why=decision["why"], decision=decision)
    return a.end("applied", f"Updated to {last_move.get('version', '')}", decision=last_move)


def _machine(m: catalog.Manifest, environ: Mapping[str, str]) -> str:
    """"" when this Mac can prepare the candidate and its code may run here; else why not."""
    from .. import channels, readiness
    from ..bundle_admission import local_problem

    if why := readiness.misfit(m):
        return why
    if not channels.trusted(m, environ):
        return f"code from the community channel {m.channel} isn't allowed here any more"
    return local_problem(m) or ""
