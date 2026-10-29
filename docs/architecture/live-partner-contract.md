# The one-brain live partner: shared contract (server, studio, bench builders)

This is the builders' contract of 29 October 2026, kept as written because the code cites it.
The live partner spec in hooke-web (`docs/planning/open-source-successor/implementation/part-2.5-live-partner-spec.md`)
governs where the two differ. In particular, the voice runs at the reasoning effort with the best answers, words come
before a tool only when they mean something, the partner's stack is Hooke's default, and timing lines are reported
rather than gating. The budget tool and the builders' common brief it mentions lived in the build session's scratch
folder and are not part of the repo.

## Why

Will (founder), 29 Oct: Hooke needs a live research partner **better than HeyClicky**, and the live experience is the
demo's "lean in" moment.

HeyClicky's insight is that the screen is the truth. One model hears you, sees fresh pixels, calls tools with your
actual words, reads the results and speaks, all in one context.

Hooke today passes a text description of the app between two models. `gpt-live-1` can't take images and delegates
through `ask_app` by id only, so `brain/speech.py` must guess which words the id belonged to. Every failure in the
28 Oct chat log (`~/Desktop/chatlog.rtf`) sits in a gap between those hops.

Hooke can beat HeyClicky because it owns its UI and specimen. It can point at things by **exact id** (an outline on a
moving live specimen, a window, a Tab, a researcher card), not by guessed pixels.

## Verified facts (OpenAI docs, read 2026-10-29)

**`gpt-realtime-2.1`** (`v1/realtime`):
- Input: text, audio and **image**. An image goes in a `conversation.item.create` user message as an `input_image` part, with `image_url` set to a base64 `data:` URL (PNG or JPEG) and `detail` set to `low`, `high` or `auto`.
- Function tools: set `session.tools` or `response.tools`. Return results with `conversation.item.create` of type `function_call_output` (with the `call_id`), then `response.create`. `parallel_tool_calls` is available only on reasoning models.
- `reasoning.effort`: minimal, low, medium, high or xhigh. Start at `low`.
- 128K context. `truncation.retention_ratio` (use 0.8) and `token_limits.post_instructions` are available.

**Turn detection:**
- **Push-to-talk:** set `turn_detection: null`, send `input_audio_buffer.clear` on press, then `input_audio_buffer.commit` and `response.create` on release.
- **Open mic:** `semantic_vad`, with `eagerness` set to `auto` or low/medium/high, plus `interrupt_response` and `create_response`.

**Noise reduction:** `audio.input.noise_reduction` takes `{type: "far_field"}` for laptop and room mics, or `near_field`.

**WebRTC with a sideband:**
- The server, which keeps the key, posts the browser's SDP offer and session config to `POST /v1/realtime/calls`. That returns the SDP answer, and the call id is in the `Location` header.
- The server then opens the sideband WebSocket `wss://api.openai.com/v1/realtime?call_id=rtc_…`, which receives the same events and can send `session.update`, `conversation.item.create`, `response.create` and so on.
- Over WebRTC the server truncates unplayed audio when the user interrupts.

**Pricing per 1M tokens:**
- Text: $4 in, $24 out.
- Audio: $32 in, $64 out. User audio is 1 token per 100 ms; assistant audio is 1 token per 50 ms.
- Image: $5 in.

**OpenAI Python SDK** (installed in the venv) is version 3.41 on PyPI. Check what `client.realtime` offers (`calls`, `connect(call_id=…)`) before writing raw HTTP.

## The option

`VOICE` in `services/orchestrator/options.py` gains `gpt-realtime-2.1`, labelled "One brain (hears, sees and acts)", beside `gpt-live-1`, which stays the default until the coordinator flips it after the journey bench. It has a catalog entry like `catalog/models/gpt-live-1/model.toml`.

A new option group `TALK` has two choices:
- `hold`: hold to talk. The default for the one-brain voice.
- `open`: open mic with `semantic_vad`. Its note says to use headphones.

GPT-Live keeps working unchanged.

## Wire contract (studio ↔ orchestrator, over the existing `/ws/voice` socket)

**Studio → server:**
- `{type:"talk", down:true}`: the talk key or button is pressed.
- `{type:"talk", down:false}`: it is released.
- `{type:"talk", cancel:true}`: Escape while holding. Discard the audio; no answer.
- `{type:"snapshot", id, jpeg, w, h, at, how}`: a reply to `snapshot-request`. `jpeg` is base64. `how` is `"tab-share"` or `"composed"`.
- `{type:"marks-shown", id, resolved:[{target, ok, rect?:[x,y,w,h], why?}]}`: a reply to `marks`.

**Server → studio:**
- `{type:"snapshot-request", id, maxSide}`: the studio answers within 1.5 s or the server proceeds without it.
- `{type:"marks", id, marks:[{target, style, label?}], until}`.
  - `style` is `"box"`, `"point"` or `"underline"`.
  - `until` is `"speech-end"` or a number of milliseconds.
  - The studio draws on a full-window overlay layer, keeps the marks while the voice is speaking, and clears them about 1.5 s after it stops (`VoiceClient` phase). It answers `marks-shown`.
- `{type:"marks-clear", id?}`.

**Target grammar** (resolved by the studio to a pixel-exact rect each animation frame, so a mark follows a moving outline):

| Target | Resolves to |
|---|---|
| `outline:<oId>` | `View.objectCanvasBox(id)`, guarded by `objectInView` |
| `member:<popId>:<memberId>` | the member's box |
| `window:<presentationId>` | `section.workspace-window[data-presentation-id=…]` |
| `document:<placementId>` | `#study-artifacts > [data-placement-id=…]` |
| `tab:<viewId or exact title>` | `button#work-tab-<id>` (by title through the `WorkspaceViews` list; an ambiguous title is `ok:false`, `why:"ambiguous"`) |
| `researcher:<number or rid>` | `.researchers-board-row[data-rid=…]` (a number maps through the board's rows) |
| `panel:<name>` | one of `SCREEN_PANELS`: magnifier, viewer, slidemap, card, made-today, sidebar, live, workspace-tabs |

**Mark style:** hand-drawn feel, dashed rounded box or an arrow pointer, in the overlay accent colour from the design
tokens. Marks never block clicks (`pointer-events: none`).

## Per-turn perception (server)

On release (hold) or on `speech_stopped` (open), before `response.create`:
1. **Camera:** `voice/scene_tools.py` `view_jpeg(hub, max_side=768)`.
2. **Window:** the studio's window snapshot via `snapshot-request`. Reuse the `screen.ts` capture: a granted tab share's `sampleTab`, or else `composeScreen()`.
3. **Screen now, as text:** from the workspace report (`workspace_observation.front()`, items with titles and visible/front flags, at most about 1 KB).

These go in one user item (`input_text` plus up to two `input_image`s). Image items from earlier turns are deleted (`conversation.item.delete`). The model's own words carry the memory, as HeyClicky does.

The **Project brief** (`project_state`'s bounded text, about 1.5–3 KB) is one developer item. It is replaced, never appended: delete the old one and create the new one when the Project state's revision changed.

## Tools (server)

Expose a curated subset of the brain's registry (`Registry.responses_tools()`, `brain/registry.py`, reshaped for Realtime), executed through the same path the Actions panel uses: `commands.run_tool` and `Registry.start` with a voice `ToolContext` and a ticket. The subset is about 15–20 tools:
- `present`
- `tabs`
- `show_measurements` (with its `tab` argument)
- `highlight`
- outline/segment
- select and zoom
- `start_researcher`
- `research_status`
- `researcher_note`
- `resources`
- `models_in_use`
- `workflow`
- `new_project`

Add three tools of its own:
- **`mark(targets, style, label?)`:** draws marks as above and returns which ones were shown.
- **`look_hard(question)`:** sends the camera frame (and the window snapshot if relevant) with the explicit question to the brain's frontier vision model (`options.Settings.vision`, via `vision.py`), and returns the answer as text.
- **`do_in_hooke(request)`:** hands a multi-step request, in the user's full words, to the existing brain (`Brain.turn(text, "voice")`). Everything the brain can do stays reachable, and its findings come back as the result. No id-guessing.

**Act, then look:** after a tool that changes the screen, the server waits for the studio's report (the existing screen check) and adds a fresh snapshot plus the "screen now" text. Screen-changing tools are present, tabs, `show_measurements`, highlight, select, zoom, outline and `new_project`. The model then confirms or corrects before saying done.

**Instructions** (a short new prompt, `PARTNER` in `prompts.py`): you are Hooke's research partner; you see the camera and Hooke's window each turn; act with tools using the user's own words; point with `mark` while explaining; say done only when the picture confirms it; keep answers short and spoken; start a researcher only when asked for one; failures are said once, with what to try next; never tell the user to restart the app.

## Spending

Usage from `response.done` is reported to the app's API-usage meter (`api_usage`). Paid smoke tests during building use the
budget tool (`budget.py`, pool `pr`); the server builder's limit is $10, the others $0 unless stated.

## Amendments (Will, 29 Oct ~02:00): these override anything above

**1. Perception at key-down.**
- When the talk key goes down, the studio immediately captures its window snapshot and sends `{type:"snapshot", id:"talk-<n>", …}` unsolicited, right after `{type:"talk", down:true, n}`.
- The server grabs the camera frame (`view_jpeg`) at the same moment.
- On release, the server commits and sends `response.create` at once, with the images already in the conversation item. Nothing is fetched after release.
- `snapshot-request` remains for act-then-look only.

**2. Barge-in.** A key-down while the voice is speaking does all of this:
- the **studio** mutes the remote audio element immediately (silent within 150 ms) and clears the marks;
- the **server** sends `response.cancel` and `output_audio_buffer.clear` (WebRTC), then starts the new turn.

**3. Spoken preambles** are on during tool calls and during the act-then-look wait ("pulling that up…"). No silence over 1.5 s while it works.

**4. Proactive events.** These are added to the conversation as they happen:
- a researcher finishes or fails;
- a long tool completes;
- a capability turns on;
- a reconnect.

If the user isn't talking (no key held, no speech in the last 1.5 s), the server sends `response.create` for **one** sentence, with a `mark` on the card (for example `researcher:3`). It never speaks over the user, and there's at most one line per event.

**5. Named tools,** directly, mapped from the registry by name (check `services/tests/golden/tool_schemas.json`):
- web search;
- running a capability by name (`run_capability` or the registry's equivalent, covering BioCLIP, Cellpose and anything turned on);
- images and reference photos;
- `present` for documents and presentations.

`do_in_hooke` is a rare fallback (under 5% of calls). Every call is logged with its tool name, so the bench can count.

**6. Region marks.** `mark` also takes `{target:"frame", region:[x,y,w,h]}` or `{target:"frame", polygon:[[x,y],…]}`, in normalised coordinates (0–1) of the camera frame the model saw. The studio maps them to the canvas through the same source → canvas transform as outlines, so they follow the specimen.

**7. One conversation.**
- The partner's turns (user transcript, assistant transcript, tool cards) go into Hooke's shared conversation, the same journal lines and chat events the GPT-Live path uses.
- A new session starts seeded with the last 8 exchanges and the brief, so you can type, talk, then type.

**8. Reconnect.**
- The 60-minute session limit and Wi-Fi drops are handled by a new call seeded with the brief, the recent turns and the latest per-turn notes.
- It reconnects proactively at about 55 minutes when idle, and immediately on any close or error. Target: 2 s or less.
- If it happens mid-speech, one quiet line.

**9. A text memory of what it saw.** Each turn keeps a short text note of the screen and frame (from the model's own description, or the "screen now" text). Images are deleted, the notes stay, so "go back to what we were looking at" works.

**10. The talk key** is a setting. The default is Control+Option, hold-only, with Hooke in front, and it works while focus is in a text field (handle `keydown`/`keyup` at the window level, in the capture phase). Holding the on-screen Talk button is always available. Control+Option is the VoiceOver modifier: note that in the setting's help, and offer an alternative key.

**11. Attribution and conventions.**
- The partner's tool calls are attributed to the in-app Assistant by voice, like the brain's.
- Build on the shared Project → Study → capabilities/workflows model.
- Preserve the research preview, its data, its connections and the shared compute slot.
- No contest submission.

## The acceptance standard (Will, 29 Oct): measured on the packaged build

| Area | Standard |
|---|---|
| Feel | Release to first audio ≤ 0.8 s median and ≤ 1.5 s p95. Barge-in silent ≤ 150 ms. Single-step actions visible and spoken ≤ 3 s median, ≤ 5 s p95. Multi-step ≤ 8 s, with progress spoken. No silence over 1.5 s while working. Each outcome said exactly once. Nothing meant for the model is ever spoken. |
| Seeing | The window and frame as at key-down. Answers match the screen. Never "I can't see your screen". |
| Acting | The right tool on the first try. `do_in_hooke` under 5%. Zero false "done". One researcher per request, started ≤ 2 s. Status names the right researcher. |
| Pointing | A mark ≤ 200 ms after speech about the thing starts. Id marks correct every time and following the target. Region marks cover what was named. Hand-drawn and calm. Cleared at speech end. |
| Knowing | Events spoken ≤ 2 s when idle, never over you, in one line, pointing at the card. Project questions answered from `project.state`, correctly. |
| Conversation | One thread with chat. "That one", "the second researcher" and "go back to before" resolve correctly over 20 turns. Reconnect ≤ 2 s, unnoticed. |
| Robustness | The journey 10 runs in a row with zero step failures. No crashes or stuck states. Live-view frame rate and delay unchanged. |
| Elegance | One or two plain sentences by default. Nothing on screen that wasn't asked for. |

Every miss becomes a bench case before it's fixed.
