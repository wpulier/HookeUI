"""Any agent that speaks the Agent Client Protocol over stdio (ADR 0029): Grok Build (`grok agent stdio`), Gemini CLI
(`gemini --acp`) and others later, through one client.

Hooke is the ACP client. It offers no file system or terminal of its own (the agent uses its own, in its own folder),
and passes no MCP servers. A turn is one `session/prompt`; it ends with the prompt's stop reason. ACP has no steering
of its own, so a message sent while a turn runs cancels that prompt (`session/cancel`) and is sent as the next one, in
the same session, as T3 Code does: the agent keeps what it did and carries on with the note. Interrupt is
`session/cancel` alone, which also answers any waiting ask as cancelled, as the protocol requires. Every
`session/request_permission` goes to the user. Spec: https://agentclientprotocol.com (protocol version 1); tested
against Grok Build 1.0.41's recorded stream (tests/fixtures/agents/).
"""
from __future__ import annotations

import asyncio

from .adapter import Adapter, Permission, Spec, short
from .jsonrpc import Peer, RpcError
from .transport import Gone

PROTOCOL = 1
PROMPT_S = 6 * 3600.0  # a turn may run for hours; Stop and Interrupt end it sooner


class SignInNeeded(RuntimeError):
    """The agent wants its own sign-in first: Hooke never signs in for the user."""


class Acp(Adapter):
    program, label = "acp", "An ACP agent"

    def __init__(self, transport_factory, argv: list[str], *, program: str = "acp", label: str = "An ACP agent"):
        super().__init__(transport_factory)
        self._argv = list(argv)
        self.program, self.label = program, label
        self.peer: Peer | None = None
        self.capabilities: dict = {}
        self._steer: str | None = None  # a note that cancelled the running prompt, sent as soon as it has stopped
        self._prompt: asyncio.Task | None = None
        self._asks: dict[str, tuple[object, list[dict]]] = {}  # permission id -> (request id, options)
        self._words: list[str] = []  # the agent's words since its last tool call
        self._n = 0
        self._cancelling = False
        self.model: str | None = None
        self._tokens = 0

    def argv(self) -> list[str]:
        return list(self._argv)

    async def start(self, spec: Spec) -> None:
        self.transport = await self._factory(self.argv(), spec.cwd, spec.env)
        self.peer = Peer(self.transport, on_request=self._request, on_notification=self._notification,
                         version="2.0")
        asyncio.create_task(self._read())
        hello = await self.peer.request("initialize", {
            "protocolVersion": PROTOCOL, "clientInfo": {"name": "hooke", "title": "Hooke", "version": "1"},
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}})
        self.capabilities = (hello or {}).get("agentCapabilities") or {}
        try:
            if spec.resume and self.capabilities.get("loadSession"):
                await self.peer.request("session/load", {"sessionId": spec.resume, "cwd": spec.cwd,
                                                         "mcpServers": []})
                self.session = spec.resume
            else:
                created = await self.peer.request("session/new", {"cwd": spec.cwd, "mcpServers": []})
                self.session = created["sessionId"]
                self.model = ((created.get("models") or {}).get("currentModelId"))
        except RpcError as error:
            # An agent that wants signing in says so here (ACP auth_required); the user signs in in its own app.
            raise SignInNeeded(error.error.get("message") or "the agent refused to start a session") from error
        self.emit("session", session=self.session, model=self.model)
        # Standing instructions travel with the first prompt: ACP has no separate system text.
        await self.send(f"{spec.instructions}\n\n{spec.brief}")

    async def _read(self) -> None:
        try:
            await self.peer.run()
        finally:
            self._finish(self.transport.returncode, "the program ended")

    # --- the verbs ------------------------------------------------------------------------------------------------
    async def send(self, text: str) -> str:
        if self.ended:
            raise Gone("the program has exited")
        if self.busy:
            first = self._steer is None
            self._steer = text if first else f"{self._steer}\n\n{text}"
            if first:
                await self._cancel()
            return "steered"
        self._turn(text)
        return "started"

    def _turn(self, text: str) -> None:
        self._n += 1
        self._words = []
        self.emit("turn", state="working", turn=str(self._n))
        self._prompt = asyncio.create_task(self._run_prompt(text, str(self._n)))

    async def _run_prompt(self, text: str, turn: str) -> None:
        try:
            result = await self.peer.request("session/prompt", {
                "sessionId": self.session, "prompt": [{"type": "text", "text": text}]}, timeout=PROMPT_S)
            reason = (result or {}).get("stopReason")
            usage = ((result or {}).get("_meta") or {}).get("usage") or {}
            if usage.get("totalTokens") is not None:  # an agent's own count, where it gives one (Grok Build does)
                self._tokens += int(usage["totalTokens"])
                self.emit("usage", tokens=self._tokens, costUsd=None)
            self._flush(final=reason == "end_turn")
            if reason == "cancelled" or self._cancelling:
                state, error = "interrupted", None
            elif reason in ("end_turn", None):
                state, error = "completed", None
            else:
                state, error = "failed", {"max_tokens": "It reached its output limit.",
                                          "max_turn_requests": "It reached its step limit.",
                                          "refusal": "It refused."}.get(reason, f"It stopped ({reason}).")
        except (RpcError, asyncio.TimeoutError) as error:
            self._flush(final=False)
            state, error = "failed", short(str(error) or "the prompt failed")
        except Gone:
            return
        self._cancelling = False
        steer, self._steer = self._steer, None
        if steer is not None and state == "interrupted" and not self.ended:
            self._n += 1  # the same turn goes on with the note: no end is said
            self._words = []
            self._prompt = asyncio.create_task(self._run_prompt(steer, str(self._n)))
            return
        self.emit("turn", state=state, turn=turn, error=error)
        if steer is not None and not self.ended:  # it had ended anyway: the note starts the next turn
            self._turn(steer)

    async def interrupt(self) -> bool:
        if not self.busy:
            return False
        self._steer = None
        await self._cancel()
        return True

    async def _cancel(self) -> None:
        self._cancelling = True
        for pid in list(self._asks):
            rid, _ = self._asks.pop(pid)
            self.permissions.pop(pid, None)
            await self.peer.reply(rid, {"outcome": {"outcome": "cancelled"}})
            self.emit("resolved", id=pid, outcome="withdrawn")
        await self.peer.notify("session/cancel", {"sessionId": self.session})

    async def answer(self, permission: str, allow: bool, *, always: bool = False) -> None:
        rid, options = self._asks.pop(permission)
        self.permissions.pop(permission, None)
        wanted = (("allow_always", "allow_once") if always else ("allow_once", "allow_always")) if allow else (
            "reject_once", "reject_always")
        pick = next((o for kind in wanted for o in options if o.get("kind") == kind), None)
        if pick is None:
            outcome = {"outcome": "cancelled"}  # no option says what the user said: never pick a different one
        else:
            outcome = {"outcome": "selected", "optionId": pick.get("optionId")}
        await self.peer.reply(rid, {"outcome": outcome})
        self.emit("resolved", id=permission, outcome="allowed" if allow and pick else "declined")

    # --- what the agent says --------------------------------------------------------------------------------------
    def _flush(self, *, final: bool) -> None:
        text = "".join(self._words).strip()
        self._words = []
        if text:
            self.emit("text", item=f"acp-{self._n}-{len(text)}", text=text, final=final)

    async def _notification(self, message: dict) -> None:
        if message.get("method") != "session/update":
            return
        update = (message.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") == "text":
                self._words.append(content.get("text") or "")
                self.emit("delta", item=None, text=content.get("text") or "")
        elif kind in ("tool_call", "tool_call_update"):
            if kind == "tool_call":
                self._flush(final=False)
            status = update.get("status")
            state = {"completed": "done", "failed": "failed"}.get(status, "running")
            detail = update.get("rawInput") or [loc.get("path") for loc in update.get("locations") or []]
            self.emit("tool", item=update.get("toolCallId"), title=update.get("title"),
                      detail=short(detail) if detail else None, state=state)

    async def _request(self, message: dict) -> None:
        method, params, rid = message.get("method"), message.get("params") or {}, message.get("id")
        if method != "session/request_permission":
            # Hooke offered no file system or terminal: the agent uses its own.
            await self.peer.reply(rid, error={"code": -32601, "message": "Hooke doesn't offer this"})
            return
        call = params.get("toolCall") or {}
        options = [o for o in params.get("options") or [] if isinstance(o, dict)]
        pid = f"acp:{rid}"
        self._asks[pid] = (rid, options)
        detail = call.get("rawInput") or [loc.get("path") for loc in call.get("locations") or []] or call.get("title")
        ask = Permission(id=pid, title=call.get("title") or "Use a tool", detail=short(detail or "", 1200),
                         always=any(o.get("kind") == "allow_always" for o in options))
        self.permissions[pid] = ask
        self.emit("permission", id=pid, title=ask.title, detail=ask.detail, reason="", always=ask.always)


def grok(transport_factory, executable: str = "grok") -> Acp:
    # Its default permission mode: every action outside its own rules asks (never --always-approve).
    return Acp(transport_factory, [executable, "--permission-mode", "default", "agent", "stdio"], program="grok",
               label="Grok Build")


def gemini(transport_factory, executable: str = "gemini") -> Acp:
    return Acp(transport_factory, [executable, "--acp"], program="gemini", label="Gemini CLI")
