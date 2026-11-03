"""Codex, driven through `codex app-server` over stdio (ADR 0029).

The user's own Codex, signed in as they signed it in (ChatGPT or an API key): Hooke never sees or sets its credentials.
One thread per launch, started in Codex's own workspace-write sandbox with approvals on request, so everything that
leaves the sandbox comes to the user as an ask. Hooke's messages start a turn (`turn/start`) or steer the running one
(`turn/steer`); Interrupt is `turn/interrupt`. Recorded against codex-cli 0.163.0 (tests/fixtures/agents/).
"""
from __future__ import annotations

import asyncio

from .adapter import Adapter, Permission, Spec, short
from .jsonrpc import Peer, RpcError
from .transport import Gone

CLIENT = {"name": "hooke", "title": "Hooke", "version": "1"}
# The asks Codex makes, each answered only by the user (conductor.py); everything else it asks for is declined.
APPROVALS = {"item/commandExecution/requestApproval": "command", "item/fileChange/requestApproval": "files",
             "item/permissions/requestApproval": "permissions",
             "execCommandApproval": "legacy-command", "applyPatchApproval": "legacy-files"}


class Codex(Adapter):
    program, label = "codex", "Codex"

    def __init__(self, transport_factory, executable: str = "codex"):
        super().__init__(transport_factory)
        self.executable = executable
        self.peer: Peer | None = None
        self.thread: str | None = None
        self.turn: str | None = None
        self.model: str | None = None
        self._asks: dict[str, tuple[object, str, dict]] = {}  # permission id -> (request id, kind, params)
        self._reader: asyncio.Task | None = None

    def argv(self) -> list[str]:
        return [self.executable, "app-server"]

    async def start(self, spec: Spec) -> None:
        self.transport = await self._factory(self.argv(), spec.cwd, spec.env)
        self.peer = Peer(self.transport, on_request=self._request, on_notification=self._notification)
        self._reader = asyncio.create_task(self._read())
        await self.peer.request("initialize", {"clientInfo": CLIENT,
                                               "capabilities": {"experimentalApi": False, "requestAttestation": False}})
        await self.peer.notify("initialized")
        params = {"cwd": spec.cwd, "approvalPolicy": "on-request", "sandbox": "workspace-write",
                  "developerInstructions": spec.instructions}
        if spec.model:
            params["model"] = spec.model
        if spec.resume:
            started = await self.peer.request("thread/resume", {"threadId": spec.resume, **params})
        else:
            started = await self.peer.request("thread/start", params)
        self.thread = started["thread"]["id"]
        self.model = started.get("model")
        self.session = self.thread
        self.emit("session", session=self.thread, model=self.model)
        await self._turn(spec.brief)

    async def _read(self) -> None:
        try:
            await self.peer.run()
        finally:
            self._finish(self.transport.returncode, "the program ended")

    @staticmethod
    def _input(text: str) -> list[dict]:
        return [{"type": "text", "text": text, "text_elements": []}]

    async def _turn(self, text: str) -> None:
        result = await self.peer.request("turn/start", {"threadId": self.thread, "input": self._input(text)})
        self.turn = result["turn"]["id"]
        if not self.busy:
            self.emit("turn", state="working", turn=self.turn)

    async def send(self, text: str) -> str:
        if self.ended:
            raise Gone("the program has exited")
        if self.busy and self.turn:
            try:
                await self.peer.request("turn/steer", {"threadId": self.thread, "input": self._input(text),
                                                       "expectedTurnId": self.turn})
                return "steered"
            except RpcError:
                pass  # the turn ended meanwhile: the message starts the next one
        await self._turn(text)
        return "started"

    async def interrupt(self) -> bool:
        if not (self.busy and self.turn):
            return False
        await self.peer.request("turn/interrupt", {"threadId": self.thread, "turnId": self.turn})
        return True

    # --- what Codex says ------------------------------------------------------------------------------------------
    async def _notification(self, message: dict) -> None:
        method, params = message.get("method"), message.get("params") or {}
        if method == "turn/started":
            self.turn = params["turn"]["id"]
            if not self.busy:
                self.emit("turn", state="working", turn=self.turn)
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            status = turn.get("status")
            state = {"completed": "completed", "interrupted": "interrupted"}.get(status, "failed")
            error = (turn.get("error") or {}).get("message")
            self.emit("turn", state=state, turn=turn.get("id"), error=error)
        elif method == "item/agentMessage/delta":
            self.emit("delta", item=params.get("itemId"), text=params.get("delta") or "")
        elif method == "item/started":
            self._item(params.get("item") or {}, done=False)
        elif method == "item/completed":
            self._item(params.get("item") or {}, done=True)
        elif method == "thread/tokenUsage/updated":
            total = ((params.get("tokenUsage") or {}).get("total") or {})
            self.emit("usage", tokens=total.get("totalTokens"), costUsd=None)
        elif method == "error":
            error = params.get("error") or {}
            self.emit("warning", text=short(error.get("message") or "Codex reported an error"),
                      retrying=bool(params.get("willRetry")))
        elif method == "serverRequest/resolved":
            rid = params.get("requestId")
            for pid, (request_id, _, _) in list(self._asks.items()):
                if request_id == rid:
                    self._asks.pop(pid, None)
                    if self.permissions.pop(pid, None) is not None:
                        self.emit("resolved", id=pid, outcome="withdrawn")

    def _item(self, item: dict, *, done: bool) -> None:
        kind = item.get("type")
        if kind == "agentMessage" and done:
            self.emit("text", item=item.get("id"), text=item.get("text") or "",
                      final=item.get("phase") == "final_answer")
        elif kind == "commandExecution":
            state = "running" if not done else ("done" if item.get("exitCode") in (0, None)
                                                and item.get("status") != "failed" else "failed")
            self.emit("tool", item=item.get("id"), title="Ran a command" if done else "Running a command",
                      detail=short(item.get("command") or ""), state=state)
        elif kind == "fileChange":
            paths = ", ".join(c.get("path", "") for c in item.get("changes") or [])
            self.emit("tool", item=item.get("id"), title="Changed files" if done else "Changing files",
                      detail=short(paths), state="running" if not done else (
                          "failed" if item.get("status") == "failed" else "done"))
        elif kind == "mcpToolCall":
            self.emit("tool", item=item.get("id"), title=f"Used {item.get('server')}·{item.get('tool')}",
                      detail=short(item.get("arguments") or ""), state="running" if not done else (
                          "failed" if item.get("error") else "done"))
        elif kind == "webSearch" and done:
            self.emit("tool", item=item.get("id"), title="Searched the web", detail=short(item.get("query") or ""),
                      state="done")

    async def _request(self, message: dict) -> None:
        method, params, rid = message.get("method"), message.get("params") or {}, message.get("id")
        kind = APPROVALS.get(method)
        if kind is None:
            # Questions, elicitations, dynamic tools and sign-in refreshes are not Hooke's to answer.
            await self.peer.reply(rid, error={"code": -32601, "message": "Hooke doesn't answer this request"})
            return
        pid = f"codex:{rid}"
        if kind in ("command", "legacy-command"):
            command = params.get("command")
            command = " ".join(command) if isinstance(command, list) else (command or "")
            title, detail = "Run a command", command
        elif kind == "permissions":
            title, detail = "Widen its sandbox", short(params.get("permissions") or "")
        else:
            changes = params.get("fileChanges") or {}
            title = "Change files"
            detail = ", ".join(changes) if changes else (params.get("grantRoot") or "files in its folder")
        self._asks[pid] = (rid, kind, params)
        ask = Permission(id=pid, title=title, detail=short(detail, 1200), reason=short(params.get("reason") or ""),
                         always=True)
        self.permissions[pid] = ask
        self.emit("permission", id=pid, title=ask.title, detail=ask.detail, reason=ask.reason, always=True)

    async def answer(self, permission: str, allow: bool, *, always: bool = False) -> None:
        rid, kind, params = self._asks.pop(permission)
        self.permissions.pop(permission, None)
        if kind == "permissions":
            granted = {"permissions": params.get("permissions") or {}, "scope": "session" if always else "turn"} \
                if allow else {"permissions": {}, "scope": "turn"}
            await self.peer.reply(rid, granted)
            self.emit("resolved", id=permission, outcome="allowed" if allow else "declined")
            return
        if kind.startswith("legacy"):
            decision = ("approved_for_session" if always else "approved") if allow else {
                "denied": {"rejection": "The researcher declined this in Hooke."}}
        elif kind == "command" and allow and always and params.get("proposedExecpolicyAmendment"):
            decision = {"acceptWithExecpolicyAmendment": {
                "execpolicy_amendment": params["proposedExecpolicyAmendment"]}}
        else:
            decision = ("acceptForSession" if always else "accept") if allow else "decline"
        await self.peer.reply(rid, {"decision": decision})
        self.emit("resolved", id=permission, outcome="allowed" if allow else "declined")
