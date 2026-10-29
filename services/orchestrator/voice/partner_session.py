"""What `Partner` (voice/partner.py) and the voice route need of a one-brain voice session, whichever model it is:
`RealtimeSession` (voice/openai_realtime.py, `gpt-realtime-2.1` over WebRTC) and `GeminiPartnerSession`
(voice/gemini_partner.py, `gemini-3.8-live` over the app's relay) both are one. The app's other users of `brain.voice`
(the narrator, researchers' delivery through `voice_owner`, the door) read the `VoiceSession` members they always did
(`inject`, `idle_since`, `last_speech_at`, `closed`, `spoken_actions`).

A Protocol, not a base class: each session keeps its own machinery (OpenAI's one-response gate and item ids, Gemini's
activity turns and resumption handles); only these members are shared, so `Partner` never reaches past them.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, runtime_checkable

from .base import ToolSpec, VoiceConfig, VoiceEvent
from .openai_realtime import Perception

__all__ = ["PartnerSession", "Perception"]


@runtime_checkable
class PartnerSession(Protocol):
    config: VoiceConfig
    talk: str  # "hold" | "open" (option TALK)
    link: str  # "webrtc": audio goes browser <-> provider, a new call needs the browser's offer; "relay": through the app
    n: int  # the user's turn number (talk presses, or speech in open mic)
    closed: bool
    answer_sdp: str | None  # WebRTC only: the SDP answer for the browser
    session_id: str | None
    browser_client: Any
    noted: Callable[[dict], None] | None  # the interaction log (the route sets it)
    last_commentary_at: float
    spoken_actions: set[str]
    usage_seconds: float | None
    termination: str

    # --- the partner's hooks, set by Partner.bind (which also gives the session `happened`: the app's own events,
    # App.turned_on, reach the partner through brain.voice) ------------------------------------------------
    turn_items: Callable[[int], Perception]  # turn n's key-down perception not yet sent, as it is now
    on_speech_started: Callable[[int], None]  # open mic: the user began turn n; capture now
    history: Callable[[], list[tuple[str, str]]]  # the shared conversation's recent exchanges, for a new connection
    tag_notice: Callable[[str], dict | None]  # what a line the app asks to be said is about
    running_facts: Callable[[], str]  # what is still running, for a progress line
    on_point: Callable[[list[str]], None]  # point at what a tool put on screen as its answer is heard

    def on(self, callback: Callable[[VoiceEvent], None]) -> None: ...
    def events(self) -> AsyncIterator[VoiceEvent]: ...

    # --- lifecycle --------------------------------------------------------------------------------------
    async def start(self, offer_sdp: str | None = None) -> None: ...
    async def close(self) -> None: ...

    async def reconnect(self, offer_sdp: str | None = None, *, why: str = "close") -> float:
        """Back on the same session object: the same call reattached or resumed (no offer), or a new call from the
        browser's new offer (WebRTC). Returns the seconds it took; raises when it could not."""

    def connected(self) -> bool:
        """The provider's connection is open now (False while reconnecting)."""

    async def drop_connection(self) -> None:
        """The bench's fault (MICROMIND_BENCH_FAULTS=1): the provider's connection closed under the session, as a
        network drop would."""

    # --- talking ----------------------------------------------------------------------------------------
    async def talk_down(self, n: int | None = None) -> None: ...
    async def talk_up(self, n: int | None = None) -> bool: ...
    async def talk_cancel(self, n: int | None = None) -> None: ...
    def speaking(self) -> bool: ...
    def studio_phase(self, phase: str) -> None: ...
    async def early(self, n: int, *, note: str = "", image: tuple[bytes, str] | None = None,
                    detail: str = "auto") -> bool: ...

    # --- context -----------------------------------------------------------------------------------------
    def seed_brief(self, text: str, revision: str) -> None: ...
    async def set_brief(self, text: str, revision: str) -> None: ...
    async def update_tools(self, tools: list[ToolSpec]) -> None: ...
    async def proactive(self, text: str, *, event: str, ref: str | None = None,
                        before: Callable[[], Awaitable[None]] | None = None, mark: str | None = None) -> None: ...
    async def inject(self, text: str, *, respond: bool = False, delegation_id: str | None = None,
                     source: str = "app", facts: dict | None = None) -> None: ...

    # --- input and timing, as every VoiceSession -----------------------------------------------------------
    async def send_audio(self, pcm: bytes) -> None: ...
    async def send_text(self, text: str) -> None: ...
    async def mute(self, muted: bool) -> None: ...
    def idle_since(self) -> float: ...
    def last_speech_at(self) -> float: ...
