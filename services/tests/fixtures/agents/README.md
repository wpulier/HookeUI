# Recorded outside-agent streams

What the real programs said, line by line, for the adapters in `services/orchestrator/agents/` (ADR 0029). Each file
is JSON lines: `{"dir": "out" | "in", "msg": …}`, Hooke's messages out and the program's in, in order. The first line
(`"dir": "note"`) says where it came from. `tests/agent_streams.py` plays one back as the program: each message the
adapter sends must be the next `out` of the same kind, then the `in` lines up to the next `out` are delivered.

| File | Program | How it was recorded |
|---|---|---|
| `codex-app-server.jsonl` | codex-cli 0.163.0 (`codex app-server`) | live, 2026-11-03, ChatGPT sign-in |
| `claude-stream-json.jsonl` | Claude Code 2.1.310 (`claude -p`, stream-json) | live, 2026-11-03, subscription sign-in |
| `claude-steer.jsonl` | Claude Code 2.1.310 | live, 2026-11-03 |
| `gemini-acp-refused.jsonl` | Gemini CLI 0.65.0 (`gemini --acp`) | live, 2026-11-03: the service refuses the session |
| `grok-acp-permission.jsonl` | Grok Build 1.0.41 (`grok agent stdio`) | live by T3 Code, converted |
| `grok-acp-steer.jsonl` | Grok Build 1.0.41 | live by T3 Code, converted |

Removed from the live recordings: account and plan notices, the user's own Codex config warnings, hooks, MCP servers,
Claude Code commands, skills and plugins, thinking signatures, and home paths (replaced with `/work` and
`/Users/researcher`). Nothing else was changed.

The two Grok Build streams are converted from T3 Code's test fixtures
(`apps/server/src/orchestration-v2/testkit/fixtures`, commit 55ea365): request ids numbered, the client's own request
bodies replaced (Hooke sends its own), thought chunks and `_x.ai/*` extension notifications removed. T3 Code's licence:

```text
MIT License

Copyright (c) 2026 T3 Tools Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
