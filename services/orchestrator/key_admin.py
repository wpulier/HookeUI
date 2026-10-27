"""Adding, replacing and removing a key from the studio, and making every part of the app use the result at once (P1 of
docs/design/2026-10-12-the-open-catalog.md).

One lifecycle:
- **save**: the key is tested (key_tests.py); a key the service refused is never saved; one that could not be checked
  (a timeout, no connection, a rate limit) is saved only when the user says so (`unchecked`). It goes in the Keychain.
- **remove**: only a key this app saved; the key then comes from the next source (the keys file), or from none.
- **apply**: every key is resolved again (keys.py) and put into this process's environment, the clients that keep one
  are dropped (vision, the brain's function calling; fal builds a client per call), the research and presentation
  workers and the GPU workers are sent the change, and the studio gets the capabilities again.

A key set outside the app (the environment) is never changed or removed here; its card says where to change it. Values
are never logged, put in Activity or on a wire, and never told to a model.
"""
from __future__ import annotations

import asyncio
import logging
import time

from gpu_worker.manifests import services

from . import key_tests, keychain, keys

log = logging.getLogger(__name__)


def _short(name: str) -> str:
    """"Google Gemini API" -> "Google", "OpenAI API" -> "OpenAI", "fal.ai" -> "fal": the service as people say it."""
    return {"Google Gemini API": "Google", "OpenAI API": "OpenAI", "Anthropic API": "Anthropic", "fal.ai": "fal"}.get(name, name)


class KeyAdmin:
    def __init__(self, app) -> None:
        self.app = app
        self.checked: dict[str, dict] = {}  # key name -> {"outcome", "at"}: the last test of a key saved here

    def facts(self) -> dict[str, dict]:
        """Each service's key as the studio and the brain may know it: where it comes from, whether it is there, its last
        test here; never the value."""
        resolved = keys.resolve(self.app.paths.keys_file)
        blocked = keys.file_problem(self.app.paths.keys_file)  # there but unreadable: never "Not added" (F-L8-3)
        out = {}
        for service in services().values():
            r = resolved.get(service.key)
            out[service.key] = {
                **({"fileBlocked": blocked} if blocked and not (r and r.present) else {}),
                "service": service.id, "name": service.name, "short": _short(service.name), "getKey": service.get_key,
                "enables": service.enables,
                "present": bool(r and r.present), "source": r.source if r else None,
                "sourceSays": keys.SOURCE_WORDS.get(r.source, "") if r and r.source else "",
                "canSave": keychain.enabled() and not (r and r.source == "environment"),
                **({"checked": self.checked[service.key]} if service.key in self.checked else {}),
            }
        return out

    async def save(self, service_id: str, value: str, unchecked: bool = False) -> dict:
        service = services().get(service_id)
        if service is None:
            return {"ok": False, "says": "There is no such service."}
        short = _short(service.name)
        current = keys.resolve(self.app.paths.keys_file).get(service.key)
        if current is not None and current.source == "environment":
            return {"ok": False, "says": f"Your {short} key is set outside the app (in the environment it was started "
                                         f"with); change it there."}
        if not keychain.enabled():
            return {"ok": False, "says": "Keys can't be saved on this Mac (no Keychain); put it in your keys file."}
        value = (value or "").strip()
        outcome = await asyncio.to_thread(key_tests.check, service.id, value)
        if outcome.state == "rejected":
            return {"ok": False, "state": "rejected", "says": f"{short} didn't accept that key."}
        if outcome.state == "unchecked" and not unchecked:
            return {"ok": False, "state": "unchecked", "says": f"Couldn't reach {short} to check it ({outcome.why}). "
                                                               f"Try again, or save it unchecked."}
        await asyncio.to_thread(keychain.set, service.key, value)
        self.checked[service.key] = {"outcome": outcome.state, "at": time.strftime("%H:%M")}
        await self.apply()
        says = f"{short} accepted the key." if outcome.state == "accepted" else "Saved without checking it."
        return {"ok": True, "state": outcome.state, "says": says + " Each model now shows what else it needs, if anything."}

    async def remove(self, service_id: str) -> dict:
        service = services().get(service_id)
        if service is None:
            return {"ok": False, "says": "There is no such service."}
        short = _short(service.name)
        current = keys.resolve(self.app.paths.keys_file).get(service.key)
        if current is None or current.source != "keychain":
            where = {"environment": "set outside the app", "file": "in your keys file"}.get(current.source if current else "", "")
            return {"ok": False, "says": f"Your {short} key isn't saved by this app" + (f"; it is {where}." if where else ".")}
        await asyncio.to_thread(keychain.delete, service.key)
        self.checked.pop(service.key, None)
        await self.apply()
        now = keys.resolve(self.app.paths.keys_file).get(service.key)
        then = (" The key in your keys file is used now." if now is not None and now.source == "file" else "")
        return {"ok": True, "says": f"Removed your {short} key from this Mac.{then}"}

    async def apply(self) -> None:
        """Every key resolved again and used by every part of the app now."""
        changed, removed = keys.set_environment(keys.resolve(self.app.paths.keys_file))
        if changed or removed:
            agent = getattr(self.app, "agent", None)
            if agent is not None:
                agent._visions.clear()  # vision clients hold the key they were made with
                agent.fc._clients.clear()  # so do the brain's function-calling clients
            for channel in self._workers():
                try:
                    await channel.call("keys", timeout=10.0, set=changed, unset=removed)
                except Exception as exc:  # noqa: BLE001 - a worker that went away starts again with the new keys
                    log.warning("a worker did not take the new keys: %s", type(exc).__name__)
            if "HF_TOKEN" in changed or "HF_TOKEN" in removed:
                token = {k: v for k, v in changed.items() if k == "HF_TOKEN"}
                gone = [k for k in removed if k == "HF_TOKEN"]
                for client in getattr(self.app.hub, "gpu_clients", lambda: [])():
                    if getattr(client, "ready", False):
                        try:
                            await client.call("keys", timeout=10.0, priority="background", set=token, unset=gone)
                        except Exception as exc:  # noqa: BLE001 - a worker started later gets it from its environment
                            log.warning("a GPU worker did not take the new token: %s", type(exc).__name__)
        self.app.publish_settings()
        observer = getattr(self.app, "observer", None)
        if (changed or removed) and observer is not None:
            # A key changed: whether it works and what it lets this account download are asked again (observed.py).
            task = asyncio.ensure_future(observer.sweep(force=True))
            getattr(self.app, "_tasks", []).append(task)

    def _workers(self) -> list:
        research = getattr(self.app, "research", None)
        out = []
        for channel in (getattr(research, "_worker", None), getattr(getattr(research, "presentations", None), "worker", None)):
            if channel is not None and getattr(channel, "alive", False):
                out.append(channel)
        return out
