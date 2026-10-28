"""Optional first example: real sessions, assets, Actions and retained evidence.

The profile pointer is small; source pixels and results keep their ordinary owners. The
session header independently retains the provider policy, including after dismiss/finish.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

from .brain.registry import ToolFailed

VERSION = 1
from .examples import GUIDE  # the bundled example's own folder and key (examples.py), the Library's too
NAME = "Example: Human cells"


def of(app):
    if getattr(app, "_onboarding", None) is None:
        app._onboarding = Onboarding(app)
    return app._onboarding


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class Onboarding:
    def __init__(self, app):
        self.app = app
        self.path = app.paths.home / "onboarding" / "progress.json"
        self.lock = asyncio.Lock()
        self.active = None
        try:
            self.state = json.loads(self.path.read_text())
            if (self.state.get("version") != VERSION
                    or self.state.get("status") not in ("idle", "active", "dismissed", "complete")
                    or self.state.get("step") not in ("analyze", "measurements", "return", "complete")
                    or self.state.get("localOnly") is not True
                    or not all(k in self.state for k in ("session", "assetId", "result"))):
                raise ValueError("unknown or incomplete guide state")
        except FileNotFoundError:
            self.state = self.empty()
        except (ValueError, TypeError, AttributeError):
            raise ToolFailed("The saved walkthrough could not be read.", next="restore its local progress file from backup") from None

    @staticmethod
    def empty():
        return {"version": VERSION, "status": "idle", "step": "analyze", "session": None,
                "assetId": None, "result": None, "localOnly": True}

    async def save(self, **changes):
        state = {**self.state, **changes}
        await asyncio.to_thread(_write, self.path, state)
        self.state = state

    async def wire(self):
        asset = await asyncio.to_thread(self.app.hub.library.get, self.state.get("assetId")) if self.state.get("assetId") else None
        return {k: self.state[k] for k in ("version", "status", "step", "session", "result", "localOnly")} | {
            "asset": asset.metadata() if asset else None}

    def require_owner(self, msg):
        sid = self.state["session"]
        if not sid or msg.get("session") != sid or self.app.day.sid != sid:
            raise ToolFailed("The example session is no longer open.", next="open Example walkthrough again")

    def population(self):
        result = self.state["result"]
        if result is None:
            return None
        p = self.app.hub.evidence.get(result["id"])
        if (p is None or p.session != self.state["session"] or p.revision != result["revision"]
                or p.implementation != "basic-bright-objects"
                or (p.view.get("sourceImage") or {}).get("asset") != self.state["assetId"]):
            raise ToolFailed("The walkthrough's saved result is no longer available at this revision.",
                             next="restart the walkthrough; your previous work stays saved")
        return p

    async def retained(self, p):
        # Evidence's existing writer owns publication and journal filing. A status is
        # observed, not fabricated, and no tutorial timer advances a UI step.
        async with asyncio.timeout(30):
            while p.status == "saving":
                await asyncio.sleep(0.01)
        if p.status != "saved":
            raise ToolFailed("The result could not be saved.", next="check free disk space, then retry")
        await asyncio.to_thread(self.app.hub.evidence.require_retained, p)

    async def show(self, client):
        from .commands import present
        self.require_owner({"session": self.state["session"]})
        p = self.population()
        if p is not None:
            expected = {"session": p.session, "id": p.id, "revision": p.revision}
            await self.retained(p)
            self.require_owner({"session": p.session})
            if self.app.day.on_stage != self.state["assetId"] or p.revision != expected["revision"]:
                raise ToolFailed("Open the example image again to see its saved outlines.", next="open Example walkthrough again")
            shown = await present(self.app, client, {"what": p.id, "do": "show", "revision": p.revision})
            if not shown.get("ok"):
                raise ToolFailed(shown.get("summary") or "The saved outlines could not be opened.", next="retry the walkthrough")
            self.require_owner({"session": p.session})
            if self.population() is not p or p.revision != expected["revision"]:
                raise ToolFailed("The result changed while opening its outlines.", next="retry the walkthrough")
            return expected
        return None

    async def start(self, client, restart=False):
        from . import commands
        from . import examples

        # Verify before opening anything. Copy lossless pixels to this profile so a
        # checkout move/removal cannot strand its session. Original TIFF and attribution
        # remain in the shipped example package. The Library lists this same copy (library.py `_bundled`).
        before = self.app.day.sid
        try:
            image, data = await asyncio.to_thread(examples.placed, self.app.paths.snapshots)
        except (OSError, ValueError):
            raise ToolFailed("The example image is missing or damaged.", next="reinstall this release to restore the example") from None
        voice = self.app.brain.voice
        if voice is not None and not voice.closed:
            await voice.close()  # finish the old stream before any example image is opened
        if self.app.day.sid != before:
            raise ToolFailed("The session changed while the example opened.", next="choose Try an example again")
        sid = self.state["session"]
        if not sid and not restart:
            # Recover the narrow crash window after session creation but before the
            # profile pointer was published. The ordinary journal is authoritative.
            infos = await asyncio.to_thread(self.app.day.catalogue.list)
            for info in infos:
                old = self.app.day.elsewhere(info.id)
                if getattr(old, "guide", None) == GUIDE:
                    sid = old.sid
                    break
        if self.app.day.sid != before:
            raise ToolFailed("The session changed while the example opened.", next="choose Try an example again")
        if restart or not sid:
            if self.app.day.sid != before:
                raise ToolFailed("The session changed while the example opened.", next="choose Try an example again")
            self.app.new_session(NAME, local_only=True, guide=GUIDE)
            sid = self.app.day.sid
            # Keep in-memory ownership even if disk publication fails; retry cannot
            # create another session. Recovery after restart uses its journal header.
            self.state = {**self.empty(), "session": sid}
            await self.save(status="active")
        elif self.app.day.sid != sid:
            self.app.switch_session(sid)
        if not self.app.day.local_only:
            raise ToolFailed("The example session's local policy is missing.", next="restart the walkthrough")
        asset_id = await asyncio.to_thread(self.app.hub.library.register, examples.KEY, image, title=examples.TITLE, at=0,
                                           origin=None, group="example", specimen=examples.SPECIMEN)
        # Registration is authoritative even when the old file is missing (and
        # therefore absent from library.get). Keep its identity while restoring
        # verified pixels inside the bridge's existing media roots.
        await asyncio.to_thread(self.app.hub.library.relocate_registered, asset_id, image)
        await self.save(session=sid, assetId=asset_id, sha256=hashlib.sha256(data).hexdigest(), status="active",
                  step="measurements" if self.state["step"] == "complete" else self.state["step"])
        await commands.source(self.app, client, {"open": asset_id, "session": sid})
        self.require_owner({"session": sid})
        if self.state["result"]:
            return await self.show(client)
        return None

    async def analyze(self, client):
        from .commands import action
        from research_worker import api_usage
        sid, aid = self.state["session"], self.state["assetId"]
        if self.state["result"]:
            await self.retained(self.population())
            return await self.show(client)
        # Recover work saved before a disconnect/crash or failed progress write.
        saved = self.app.hub.evidence.find_source_result(session=sid, asset=aid,
            implementation="basic-bright-objects", sha256=self.state["sha256"])
        if saved is not None:
            await self.retained(saved)
            await self.save(result={"id": saved.id, "revision": saved.revision}, step="measurements")
            return await self.show(client)
        if self.app.day.on_stage != aid:
            raise ToolFailed("Open the example image before finding its bright objects.", next="open Example walkthrough again")
        generation = self.app.day.generation
        staged = self.app.hub.stage_meta
        asset = await asyncio.to_thread(self.app.hub.library.get, aid)
        if asset is None or hashlib.sha256(await asyncio.to_thread(asset.path.read_bytes)).hexdigest() != self.state["sha256"]:
            raise ToolFailed("The example image is missing or damaged.", next="open Example walkthrough again to restore it")
        self.require_owner({"session": sid})
        if (self.app.day.generation != generation or self.app.day.on_stage != aid
                or self.app.hub.stage_meta is not staged or (staged or {}).get("id") != aid):
            raise ToolFailed("Open the example image before finding its bright objects.", next="open Example walkthrough again")
        # No await between authority revalidation and ordinary action ticket admission.
        with api_usage.local_only(True):
            done = await action(self.app, client, {"capability": "cells", "args": {}, "use": {"cells": "basic-bright-objects"}})
        store = self.app.hub.evidence
        # A result completed after a session switch remains owned by the request.
        p = store.find_source_result(session=sid, asset=aid, implementation="basic-bright-objects",
                                     executions=done.get("executions", []))
        if p is None:
            raise ToolFailed("The analysis did not retain a result for this specimen.", next="retry Find bright objects")
        await self.retained(p)
        await self.save(result={"id": p.id, "revision": p.revision}, step="measurements")
        return await self.show(client)

    async def _command(self, client, msg):
        action = msg.get("action")
        async with self.lock:
            rendered = None
            if action == "status":
                pass
            elif action in ("start", "restart"):
                rendered = await self.start(client, restart=action == "restart")
            elif action in ("analyze", "measurements", "finish", "dismiss"):
                self.require_owner(msg)
                if action == "analyze":
                    if self.state["status"] != "active":
                        raise ToolFailed("The walkthrough is not active.", next="open Example walkthrough again")
                    rendered = await self.analyze(client)
                elif action == "dismiss":
                    await self.save(status="dismissed")
                else:
                    if self.state["status"] != "active":
                        raise ToolFailed("The walkthrough is not active.", next="open Example walkthrough again")
                    if action == "finish" and self.state["step"] != "return":
                        raise ToolFailed("Open the measurements before finishing the walkthrough.", next="choose See measurements")
                    p = self.population()
                    if p is None or msg.get("result") != self.state["result"]:
                        raise ToolFailed("That is not the walkthrough's saved result.", next="open its measurements again")
                    await self.retained(p)
                    if action == "measurements":
                        from .tables import read_table
                        await read_table(self.app, p.id, p.session, p.revision)
                        self.require_owner(msg)
                        await self.save(step="return")
                    else:
                        if self.app.day.on_stage != self.state["assetId"]:
                            raise ToolFailed("Return to the example specimen first.", next="open Example walkthrough again")
                        rendered = await self.show(client)
                        await self.save(status="complete", step="complete")
            else:
                raise ToolFailed("Unknown walkthrough action.", next="reload the workspace")
            return {"ok": True, "onboarding": await self.wire(), "rendered": rendered}

    async def command(self, client, msg):
        # Same action/request owner joins in-flight work; a disconnected socket does
        # not abandon a result that the evidence writer is already keeping.
        key = (msg.get("action"), msg.get("session"), id(client), json.dumps(msg.get("result"), sort_keys=True))
        if self.active is not None and self.active[0] == key and not self.active[1].done():
            return await asyncio.shield(self.active[1])
        task = asyncio.create_task(self._command(client, dict(msg)))
        self.active = (key, task)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The owned task continues to retain its result; consume its eventual
            # error if the requesting socket has disappeared.
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            raise
        finally:
            if task.done() and self.active is not None and self.active[1] is task:
                self.active = None


# --- the small-models offer (Will, 2026-10-26) ----------------------------------------------------------------------
# The workspace offers a few small models once: in the first-launch Welcome, and for people past it as one row at the
# top of Models. The answer is kept in the data home, so neither asks again. Only the answer is kept here: downloads are
# the ordinary model-download and model-turn-on commands, in their one queue (apps/studio/src/starter-models.ts).
OFFER_ANSWERS = ("not-now", "download")


def models_offer_path(home: Path) -> Path:
    return home / "onboarding" / "models.json"


def models_offer(home: Path) -> dict:
    """Whether the offer was answered, and how. A missing or unreadable record is unanswered: asking again downloads
    nothing by itself."""
    try:
        saved = json.loads(models_offer_path(home).read_text())
    except (OSError, ValueError):
        saved = None
    answer = saved.get("answer") if isinstance(saved, dict) else None
    if answer not in OFFER_ANSWERS:
        return {"answered": False, "answer": None, "at": None, "models": []}
    models = saved.get("models") if isinstance(saved.get("models"), list) else []
    return {"answered": True, "answer": answer, "at": saved.get("at"),
            "models": [m for m in models if isinstance(m, str)]}


def answer_models_offer(home: Path, answer: str, models=()) -> dict:
    """Keeps the answer: Not now, or Download with the models chosen (catalog entries only)."""
    from datetime import datetime, timezone
    from gpu_worker import manifests

    if answer not in OFFER_ANSWERS:
        raise ToolFailed("That is not an answer to the models offer.", next="reload the workspace")
    chosen = list(dict.fromkeys(str(m) for m in (models or [])))[:20] if answer == "download" else []
    unknown = [m for m in chosen if manifests.get(m) is None]
    if unknown:
        raise ToolFailed(f"There is no model {unknown[0]} in the catalog.", next="reload the Models page")
    _write(models_offer_path(home), {"version": 1, "answer": answer, "models": chosen,
                                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return models_offer(home)
