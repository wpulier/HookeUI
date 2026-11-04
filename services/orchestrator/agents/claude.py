"""Claude Code, driven through the installed `claude` program in headless mode (ADR 0029).

`claude -p` with stream-json in and out. Anthropic's terms keep subscriptions out of the Agent SDK, so Hooke runs the
user's own `claude`, signed in as they signed it in. It does not pass `--bare`: that mode reads only an API key and
never the user's sign-in. It runs with `--safe-mode` instead (none of the user's own hooks, plugins, CLAUDE.md or MCP
servers; `--strict-mcp-config`), Claude Code's sandbox on, and `--max-budget-usd` from the launch's budget.

Every permission prompt comes to Hooke over the same stdio (`--permission-prompt-tool stdio`, the control protocol's
`can_use_tool`), and only the user answers it. Interrupt is the control protocol's `interrupt`. A message sent while a
turn runs steers it (`"priority": "now"`): Claude Code ends the segment it was writing (a `result` that says
`aborted_streaming`) and carries on with the message in the same turn. `--resume` continues a conversation. Values go
in `--flag=value` form, so none can be read as another flag. Recorded against Claude Code 2.1.310
(tests/fixtures/agents/).
"""
from __future__ import annotations

import asyncio
import json
import uuid

from .adapter import Adapter, Permission, Spec, short
from .transport import Gone

CONTROL_S = 30.0
SETTINGS = {"sandbox": {"enabled": True}}


def settings(hooke: str | None, skill: str | None = None) -> dict:
    """Claude Code's sandbox on; Hooke's own command (only that path) outside it and allowed, since it reaches only
    Hooke's door with this launch's connection, and Hooke's skill file readable. Every other command, file and tool
    still asks. (An absolute path in a Read rule is written with two slashes.)"""
    if not hooke:
        return SETTINGS
    allow = [f"Bash({hooke}:*)"] + ([f"Read(/{skill})"] if skill and skill.startswith("/") else [])
    return {"sandbox": {"enabled": True, "excludedCommands": [hooke]}, "permissions": {"allow": allow}}


class Claude(Adapter):
    program, label = "claude-code", "Claude Code"

    def __init__(self, transport_factory, executable: str = "claude"):
        super().__init__(transport_factory)
        self.executable = executable
        self.model: str | None = None
        self.billing: str | None = None  # Claude Code's apiKeySource: "none" is the user's own sign-in
        self._control: dict[str, asyncio.Future] = {}
        self._asks: dict[str, dict] = {}  # permission id -> the can_use_tool request
        self._last_text: tuple[str, str] | None = None  # (item, text) of the turn's latest words
        self._interrupting = False
        self._steers = 0  # steering messages sent: each ends the segment it interrupts with an aborted result
        self._tokens = 0
        self._reader: asyncio.Task | None = None

    def argv(self, spec: Spec) -> list[str]:
        args = [self.executable, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--permission-prompt-tool", "stdio",
                "--permission-mode", "default", "--safe-mode", "--strict-mcp-config",
                "--settings", json.dumps(settings(spec.hooke, spec.skill)), "--append-system-prompt", spec.instructions]
        if spec.budget_usd is not None:
            args.append(f"--max-budget-usd={spec.budget_usd:.2f}")
        if spec.model:
            args.append(f"--model={spec.model}")
        if spec.resume:
            args.append(f"--resume={spec.resume}")
        return args

    async def start(self, spec: Spec) -> None:
        self.transport = await self._factory(self.argv(spec), spec.cwd, spec.env)
        self._reader = asyncio.create_task(self._read())
        await self._ask_control({"subtype": "initialize", "hooks": None})
        await self.send(spec.brief)

    async def _read(self) -> None:
        try:
            async for message in self.transport.incoming():
                await self._message(message)
        finally:
            for future in self._control.values():
                if not future.done():
                    future.set_exception(Gone("the program has exited"))
            self._finish(self.transport.returncode, "the program ended")

    async def _ask_control(self, request: dict) -> dict:
        rid = "hooke_" + uuid.uuid4().hex[:12]
        future = asyncio.get_running_loop().create_future()
        self._control[rid] = future
        try:
            await self.transport.send({"type": "control_request", "request_id": rid, "request": request})
            return await asyncio.wait_for(future, CONTROL_S)
        finally:
            self._control.pop(rid, None)

    # --- the verbs ------------------------------------------------------------------------------------------------
    async def send(self, text: str) -> str:
        if self.ended:
            raise Gone("the program has exited")
        steering = self.busy
        message = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]},
                   "parent_tool_use_id": None, "session_id": self.session or ""}
        if steering:
            message["priority"] = "now"
            self._steers += 1
        await self.transport.send(message)
        if steering:
            return "steered"
        self.emit("turn", state="working", turn=None)
        return "started"

    async def interrupt(self) -> bool:
        if not self.busy:
            return False
        self._interrupting = True
        await self._ask_control({"subtype": "interrupt"})
        return True

    async def answer(self, permission: str, allow: bool, *, always: bool = False) -> None:
        request = self._asks.pop(permission)
        self.permissions.pop(permission, None)
        if allow:
            response = {"behavior": "allow", "updatedInput": request.get("input") or {}}
            session_only = [s for s in request.get("permission_suggestions") or []
                            if isinstance(s, dict) and s.get("destination") == "session"]
            if always and session_only:
                response["updatedPermissions"] = session_only  # for this conversation only, never the user's files
        else:
            response = {"behavior": "deny", "message": "The researcher declined this in Hooke."}
        await self.transport.send({"type": "control_response", "response": {
            "subtype": "success", "request_id": request["request_id"], "response": response}})
        self.emit("resolved", id=permission, outcome="allowed" if allow else "declined")

    # --- what Claude Code says ------------------------------------------------------------------------------------
    async def _message(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "control_response":
            response = message.get("response") or {}
            future = self._control.get(response.get("request_id"))
            if future is not None and not future.done():
                future.set_result(response)
        elif kind == "control_request":
            await self._control_request(message)
        elif kind == "control_cancel_request":
            pid = f"claude:{message.get('request_id')}"
            if self._asks.pop(pid, None) is not None:
                self.permissions.pop(pid, None)
                self.emit("resolved", id=pid, outcome="withdrawn")
        elif kind == "system" and message.get("subtype") == "init":
            first = self.session is None
            self.session = message.get("session_id") or self.session
            self.model = message.get("model") or self.model
            self.billing = message.get("apiKeySource") or self.billing
            if first:
                self.emit("session", session=self.session, model=self.model, billing=self.billing)
        elif kind == "stream_event":
            event = message.get("event") or {}
            delta = event.get("delta") or {}
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                self.emit("delta", item=None, text=delta.get("text") or "")
        elif kind == "assistant":
            body = message.get("message") or {}
            for n, block in enumerate(body.get("content") or []):
                if block.get("type") == "text" and block.get("text"):
                    item = f"{body.get('id')}:{n}"
                    self._last_text = (item, block["text"])
                    self.emit("text", item=item, text=block["text"], final=False)
                elif block.get("type") == "tool_use":
                    self.emit("tool", item=block.get("id"), title=f"Using {block.get('name')}",
                              detail=short(_describe(block.get("name"), block.get("input"))), state="running")
        elif kind == "user":
            for block in ((message.get("message") or {}).get("content") or []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    self.emit("tool", item=block.get("tool_use_id"), title=None, detail=None,
                              state="failed" if block.get("is_error") else "done")
        elif kind == "result":
            self._result(message)

    def _result(self, message: dict) -> None:
        self._tokens += _tokens(message.get("usage")) or 0  # each result counts its own turn; Codex counts in total
        self.emit("usage", costUsd=message.get("total_cost_usd"), tokens=self._tokens, billing=self.billing)
        subtype = message.get("subtype")
        aborted = str(message.get("terminal_reason", "")).startswith("aborted")
        if aborted and self._steers and not self._interrupting:
            self._steers -= 1  # the segment a steering message ended: the turn goes on with that message
            self._last_text = None
            return
        if subtype == "success" and not message.get("is_error"):
            if self._last_text is not None:
                self.emit("text", item=self._last_text[0], text=self._last_text[1], final=True)
            state, error = "completed", None
        elif self._interrupting and aborted:
            state, error = "interrupted", None
        elif subtype == "error_max_budget_usd":
            state, error = "failed", "It reached the budget Hooke gave it."
        else:
            errors = message.get("errors") or []
            state, error = "failed", short("; ".join(map(str, errors)) or subtype or "Claude Code stopped")
        self._interrupting = False
        self._steers = 0
        self._last_text = None
        self.emit("turn", state=state, turn=None, error=error)

    async def _control_request(self, message: dict) -> None:
        request = message.get("request") or {}
        rid = message.get("request_id")
        if request.get("subtype") != "can_use_tool":
            await self.transport.send({"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": "Hooke doesn't answer this request"}})
            return
        pid = f"claude:{rid}"
        self._asks[pid] = {**request, "request_id": rid}
        name = request.get("display_name") or request.get("tool_name") or "a tool"
        always = any(isinstance(s, dict) and s.get("destination") == "session"
                     for s in request.get("permission_suggestions") or [])
        ask = Permission(id=pid, title=_title(request.get("tool_name"), name),
                         detail=short(_describe(request.get("tool_name"), request.get("input")), 1200),
                         reason=short(request.get("decision_reason") or request.get("description") or ""),
                         always=always)
        self.permissions[pid] = ask
        self.emit("permission", id=pid, title=ask.title, detail=ask.detail, reason=ask.reason, always=always)


def _title(tool, name) -> str:
    return {"Bash": "Run a command", "Write": "Write a file", "Edit": "Change a file", "NotebookEdit": "Change a file",
            "WebFetch": "Read a web page", "WebSearch": "Search the web"}.get(tool, f"Use {name}")


def _describe(tool, payload) -> str:
    payload = payload if isinstance(payload, dict) else {}
    for key in ("command", "file_path", "url", "query", "pattern", "path"):
        if payload.get(key):
            return str(payload[key])
    return json.dumps(payload)[:600] if payload else ""


def _tokens(usage) -> int | None:
    if not isinstance(usage, dict):
        return None
    return sum(int(usage.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                "cache_read_input_tokens"))
