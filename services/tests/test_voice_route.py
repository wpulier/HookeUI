"""The browser voice endpoint (voice/route.py): how a session is opened on each link."""
import json
import pytest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.options import Settings
from orchestrator.voice import route
from orchestrator.voice.base import VoiceEvent
from tests.waiting import until


class FakeSession:
    """Stands in for OpenAILiveSession: records how it was opened and what it was told, then closes."""
    made: list = []
    idle = 0.0  # seconds since anyone spoke (idle_since)

    def __init__(self, config, link="relay", **kw):
        self.config, self.link, self.closed = config, link, False
        self.started_with, self.answer_sdp, self.audio = None, None, []
        self.last_commentary_at = 0.0
        self.session_id, self.injected = None, []
        FakeSession.made.append(self)

    async def start(self, offer_sdp=None):
        self.started_with = offer_sdp
        if self.link == "webrtc":
            self.answer_sdp = "v=0 answer"

    async def events(self):
        yield VoiceEvent("closed", data={"reason": "test"})

    async def send_audio(self, pcm):
        self.audio.append(pcm)

    async def send_text(self, text):
        pass

    async def mute(self, muted):
        pass

    async def inject(self, text, respond=False, delegation_id=None, **log):
        self.injected.append((text, respond, log))

    def idle_since(self):
        return self.idle

    async def close(self):
        self.closed = True


def app_with(monkeypatch, moment=None, sight=None):
    FakeSession.made = []
    monkeypatch.setattr(route, "OpenAILiveSession", FakeSession)
    monkeypatch.setattr(route, "voice_prompt", lambda brain: "prompt")
    logged = []
    brain = SimpleNamespace(agent=SimpleNamespace(log=logged.append), voice=None, voice_notes=[], sight=sight,
                            voice_recap=lambda: "Earlier: a leaf.", moment=lambda: moment, registry=SimpleNamespace(tools={}))
    hub = SimpleNamespace(settings=Settings(), latest_info=None)
    app = FastAPI()
    route.add_voice_route(app, hub, brain, dialogue=None)
    return TestClient(app), logged


def test_relay_starts_at_once_and_takes_microphone_audio(monkeypatch):
    client, logged = app_with(monkeypatch)
    with client.websocket_connect("/ws/voice?model=gpt-live-1&rate=24000", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        hello = json.loads(ws.receive_text())
        assert hello["kind"] == "model"
        ws.send_bytes(b"\x00\x00" * 480)
    session = FakeSession.made[0]
    assert session.link == "relay" and session.started_with is None and session.config.input_rate == 24000
    assert session.config.history == [("developer", "Earlier: a leaf.")]
    assert [r for r in logged if r["kind"] == "voice-session"][0]["link"] == "relay"


def test_webrtc_waits_for_the_offer_and_answers_with_the_sdp(monkeypatch):
    client, logged = app_with(monkeypatch)
    with client.websocket_connect("/ws/voice?model=gpt-live-1&link=webrtc", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        ws.send_text(json.dumps({"type": "offer", "sdp": "v=0 offer"}))
        answer = json.loads(ws.receive_text())
        assert answer == {"type": "voice", "kind": "answer", "data": {"sdp": "v=0 answer"}}
        assert json.loads(ws.receive_text())["kind"] == "model"
    session = FakeSession.made[0]
    assert session.link == "webrtc" and session.started_with == "v=0 offer"
    assert [r for r in logged if r["kind"] == "voice-session"][0]["link"] == "webrtc"


def test_installed_voice_binds_to_requesting_workspace_not_another_window(tmp_path, monkeypatch):
    """Actual routes/handshakes; synthetic voice provider, no microphone or paint claim."""
    from tests.test_workspace_transport import api, ready, hello, ORIGIN

    owners = []
    original = route.SpokenRequests

    class ObservedRequests(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            owners.append(getattr(self, 'client', None))

    monkeypatch.setattr(route, 'SpokenRequests', ObservedRequests)
    monkeypatch.setattr(route, 'OpenAILiveSession', FakeSession)
    web, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as a:
        first = ready(a)
        owner, = web.state.hub.clients
        with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as b:
            ready(b, hello('workspace-b', 'client-b', 'page-b'))
            with http.websocket_connect('/ws/voice?model=gpt-live-1', headers={'origin': ORIGIN}) as voice:
                voice.send_json({'type': 'workspace-bind', 'connectionGrant': first['connectionGrant']})
                assert voice.receive_json()['kind'] == 'model'
            assert owners == [owner], 'spoken actions have lost the requesting browser'


def test_retired_voice_binding_cannot_attach_to_reconnected_workspace(tmp_path, monkeypatch):
    from tests.test_workspace_transport import api, ready, ORIGIN
    monkeypatch.setattr(route, 'OpenAILiveSession', FakeSession)
    before = len(FakeSession.made)
    _, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as old:
        retired = ready(old)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as replacement:
        ready(replacement)
        with http.websocket_connect('/ws/voice?model=gpt-live-1', headers={'origin': ORIGIN}) as voice:
            voice.send_json({'type': 'workspace-bind', 'connectionGrant': retired['connectionGrant']})
            assert voice.receive_json()['kind'] == 'error'
    assert len(FakeSession.made) == before, 'an invalid workspace must not open a paid voice session'


def test_the_route_ends_a_reply_only_when_the_voice_went_quiet_and_files_what_is_open_at_the_end(monkeypatch):
    """The review of #200: the user speaking over the voice ends neither line, the voice going quiet ends its
    reply, and the session's end files whatever is still open."""
    calls = []

    class Recorder:
        def __init__(self, brain, dialogue):
            self.voice = None

        def __getattr__(self, name):
            return lambda *a, **k: calls.append((name, *a))

    events = [VoiceEvent("output_transcript", text="Yes,"), VoiceEvent("interrupted"),
              VoiceEvent("turn_complete", data={"reason": "user_spoke"}),
              VoiceEvent("input_transcript", text="the researcher will", data={"interim": False}),
              VoiceEvent("output_transcript", text=" the tools I have"),
              VoiceEvent("turn_complete", data={"reason": "quiet"}),
              VoiceEvent("output_transcript", text="Sure"), VoiceEvent("closed", data={"reason": "test"})]

    async def scripted(self):
        for event in events:
            yield event

    client, logged = app_with(monkeypatch)
    monkeypatch.setattr(route, "SpokenRequests", Recorder)
    monkeypatch.setattr(FakeSession, "events", scripted)
    with client.websocket_connect("/ws/voice?model=gpt-live-1&rate=24000", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        while json.loads(ws.receive_text()).get("kind") != "closed":
            pass
    spoken = [c for c in calls if c[0] in ("said", "heard", "reply_done", "close")]
    assert spoken == [("said", "Yes,"), ("heard", "the researcher will"), ("said", " the tools I have"),
                      ("reply_done",), ("said", "Sure"), ("close",)]


def test_a_session_logs_the_instructions_it_was_given_by_their_hash_and_its_recap_whole(monkeypatch):
    """The demo review of 2026-10-11: what a voice session started knowing could not be read back. Its instructions
    are logged by their hash (the same text every session until a prompt or the tool list changes), its recap whole."""
    import hashlib

    client, logged = app_with(monkeypatch)
    with client.websocket_connect("/ws/voice?model=gpt-live-1&rate=24000", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        assert json.loads(ws.receive_text())["kind"] == "model"
    record = [r for r in logged if r["kind"] == "voice-session"][0]
    assert record["instructions"] == {"sha256": hashlib.sha256(b"prompt").hexdigest()[:12], "chars": len("prompt")}
    assert record["recap"] == "Earlier: a leaf." and record["recapChars"] == len("Earlier: a leaf.")
    assert record["recapSha256"] == hashlib.sha256(b"Earlier: a leaf.").hexdigest()[:12]
    assert FakeSession.made[0].noted == logged.append  # every silent note goes to the interaction log


def test_a_view_note_says_why_it_was_sent_and_how_old_its_look_and_frame_are(monkeypatch):
    """Every view note goes to the voice with its reason and the ages of the look and frame behind it, for its
    voice-note record (the session logs it whole: test_voice_openai)."""
    import time as clock

    from orchestrator.sight import SightView

    look = SightView(7, 0, 0.0, 0.0, 300.0, "A leaf.", "leaf", [], "sharp", at=clock.time() - 12.0,
                     frame_at=clock.time() - 14.0)
    client, _ = app_with(monkeypatch, moment=SimpleNamespace(view=look))
    async def acknowledged(self):
        import asyncio
        yield VoiceEvent('ready')
        await asyncio.Event().wait()
    monkeypatch.setattr(FakeSession, 'events', acknowledged)
    keys = iter(["startup view"])
    monkeypatch.setattr(route.context, "view_key", lambda m: next(keys, "changed view"))
    monkeypatch.setattr(route.context, "view_note", lambda m: "View notes: a leaf.")
    monkeypatch.setattr(FakeSession, "idle", float("inf"))
    with client.websocket_connect("/ws/voice?model=gpt-live-1&rate=24000", headers={"origin": "http://127.0.0.1:8770"}) as ws:
        assert json.loads(ws.receive_text())["kind"] == "model"
        session = FakeSession.made[0]
        until(lambda: session.injected, every=0.05)
    text, respond, log = session.injected[0]
    assert text == "View notes: a leaf." and respond is False and log["source"] == "view"
    facts = log["facts"]
    assert facts["why"] == ["view changed"] and facts["lookFrameId"] == 7 and facts["latestFrameId"] is None
    assert 11.0 <= facts["lookAgeS"] <= 14.0 and 13.0 <= facts["lookFrameAgeS"] <= 16.0


def test_provider_audio_write_failure_reports_recovery_and_closes_owned_session(monkeypatch):
    import asyncio
    client, logged = app_with(monkeypatch)

    async def waiting(self):
        yield VoiceEvent('ready')
        await asyncio.Event().wait()

    async def failed(self, pcm):
        raise ConnectionError('synthetic provider disconnect')

    monkeypatch.setattr(FakeSession, 'events', waiting)
    monkeypatch.setattr(FakeSession, 'send_audio', failed)
    with client.websocket_connect('/ws/voice?model=gpt-live-1&rate=24000', headers={'origin': 'http://127.0.0.1:8770'}) as ws:
        assert ws.receive_json()['kind'] == 'model'
        assert ws.receive_json()['kind'] == 'ready'
        ws.send_bytes(b'\0\0' * 480)
        error = ws.receive_json()
        assert error['kind'] == 'error'
        assert 'start Talk again' in error['data']['message']
    assert FakeSession.made[0].closed
    assert len(FakeSession.made) == 1


def test_response_task_failure_is_reported_instead_of_silently_ending_talk(monkeypatch):
    client, logged = app_with(monkeypatch)
    sent = []
    original_send = route.WebSocket.send_text

    async def record(self, text):
        sent.append(json.loads(text))
        await original_send(self, text)

    async def broken(self):
        yield VoiceEvent('ready')
        raise RuntimeError('SENTINEL private provider details')

    monkeypatch.setattr(route.WebSocket, 'send_text', record)
    monkeypatch.setattr(FakeSession, 'events', broken)
    with client.websocket_connect('/ws/voice?model=gpt-live-1&rate=24000', headers={'origin': 'http://127.0.0.1:8770'}) as ws:
        assert ws.receive_json()['kind'] == 'model'
        assert ws.receive_json()['kind'] == 'ready'
        until(lambda: FakeSession.made[0].closed)
        error = next((e for e in sent if e.get('kind') == 'error'), None)
        assert error is not None, 'response task failure ended Talk without an explanation'
        assert 'response' in error['data']['message']
        assert 'SENTINEL' not in json.dumps(error)
    assert FakeSession.made[0].closed


def test_passive_voice_awareness_does_not_flap_between_workspace_reports(tmp_path):
    import asyncio
    from orchestrator.screen import REPORT_FRESH_S
    from tests.test_screen_workspace import Rig

    rig = Rig(tmp_path)
    rig.accept()
    asyncio.run(rig.ingest())
    first = route.passive_workspace_pin(rig.screen, rig.client)
    rig.now += 3.0  # the normal report gap; no user action or layout change
    later = route.passive_workspace_pin(rig.screen, rig.client)
    assert first.availability == later.availability != 'stale'
    assert first.observation.wire() == later.observation.wire()
    assert rig.pin(request_id='strict-action').availability == 'stale'
    rig.now += REPORT_FRESH_S
    assert route.passive_workspace_pin(rig.screen, rig.client).availability == 'stale'


@pytest.mark.parametrize("requested_look", [False, True])
def test_startup_view_is_not_replayed_after_the_same_recap(monkeypatch, requested_look):
    import asyncio
    look = SimpleNamespace(current=lambda info: True)
    sight = SimpleNamespace(view=look, question_view=look if requested_look else None)
    client, _ = app_with(monkeypatch, moment=SimpleNamespace(view=look), sight=sight)
    key = ["leaf"]
    reads = []
    def view_key(moment):
        reads.append(key[0])
        return key[0]
    async def acknowledged(self):
        yield VoiceEvent('ready')
        await asyncio.Event().wait()
    monkeypatch.setattr(FakeSession, 'events', acknowledged)
    monkeypatch.setattr(FakeSession, 'idle', float('inf'))
    monkeypatch.setattr(route.context, 'view_key', view_key)
    monkeypatch.setattr(route.context, 'view_note', lambda moment: 'Changed selection: guard cell.')
    with client.websocket_connect('/ws/voice?model=gpt-live-1&rate=24000', headers={'origin': 'http://127.0.0.1:8770'}) as ws:
        assert ws.receive_json()['kind'] == 'model'
        session = FakeSession.made[0]
        until(lambda: session.injected or len(reads) >= 2)
        assert not session.injected, 'startup context was already supplied in session.input'
        key[0] = 'guard cell'
        until(lambda: session.injected)
        assert session.injected[0][2]['facts']['why'] == ['view changed']
        assert session.injected[0][0] == 'Changed selection: guard cell.'
        sight.view = sight.question_view = SimpleNamespace(current=lambda info: True)
        until(lambda: len(session.injected) == 2)
        assert session.injected[1][2]['facts']['why'] == ["the look the user's first words asked for"]


def test_every_talk_session_shares_the_brains_record_of_what_was_said(monkeypatch):
    """Part 2.5: an outcome is said once per request, also when Talk is pressed again; and the session knows which
    request each delegation is (`requests`). It is not given `spoken`, which would let any later words, a cough or a
    "thanks" included, turn the narrator's late results into silent context (review defect 4): as before Part 2.5."""
    from orchestrator.brain.narrator import Said

    app_with(monkeypatch)  # the stand-in session in place of OpenAILiveSession
    said = Said()
    made = []
    original = FakeSession.__init__

    def init(self, config, link="relay", **kw):
        original(self, config, link)
        made.append(kw)

    monkeypatch.setattr(FakeSession, "__init__", init)
    brain = SimpleNamespace(agent=SimpleNamespace(log=lambda r: None), voice=None, voice_notes=[], sight=None, said=said,
                            voice_recap=lambda: "", moment=lambda: None, registry=SimpleNamespace(tools={}))
    app = FastAPI()
    route.add_voice_route(app, SimpleNamespace(settings=Settings(), latest_info=None), brain, dialogue=None)
    for _ in range(2):
        with TestClient(app).websocket_connect("/ws/voice?model=gpt-live-1", headers={"origin": "http://127.0.0.1:8770"}) as ws:
            assert json.loads(ws.receive_text())["kind"] == "model"
    assert [kw.get("said") for kw in made] == [said, said]
    assert all(getattr(s, "requests", None) is not None for s in FakeSession.made[-2:])
    assert all(getattr(s, "spoken", None) is None for s in FakeSession.made[-2:])


def test_every_one_brain_talk_session_is_handed_the_brains_one_bus(monkeypatch):
    """Step 4b: the one brain's results owed until heard live on the brain's one bus (events.bus_of), not on a Talk
    session, so a result still owed when Talk ends (or one landing after) is said by the next session."""
    from orchestrator.brain.narrator import Said
    from orchestrator.voice.events import Bus

    made = []

    class Realtime(FakeSession):
        def __init__(self, config, talk="hold", **kw):
            super().__init__(config, link="webrtc")
            self.talk = talk
            made.append(kw)

        def seed_brief(self, text, revision):
            pass

    class FakePartner:
        def __init__(self, hub, brain, dialogue, **kw):
            self.tools = SimpleNamespace(specs=lambda: [])
            self.lines = SimpleNamespace(close=lambda: None, voice=None)

        def bind(self, session):
            self.session = session

        async def brief(self):
            return "Project Leaf", "rev-1"

        def close(self):
            pass

    monkeypatch.setattr(route, "RealtimeSession", Realtime)
    monkeypatch.setattr(route, "Partner", FakePartner)
    monkeypatch.setattr(route, "partner_prompt", lambda brain: "prompt", raising=False)
    said = Said()
    brain = SimpleNamespace(agent=SimpleNamespace(log=lambda r: None), voice=None, voice_notes=[], sight=None, said=said,
                            voice_recap=lambda: "", moment=lambda: None, registry=SimpleNamespace(tools={}))
    app = FastAPI()
    route.add_voice_route(app, SimpleNamespace(settings=Settings(), latest_info=None), brain, dialogue=None)
    for _ in range(2):
        with TestClient(app).websocket_connect("/ws/voice?model=gpt-realtime-2.1&link=webrtc",
                                               headers={"origin": "http://127.0.0.1:8770"}) as ws:
            ws.send_text(json.dumps({"type": "offer", "sdp": "v=0 offer"}))
            assert json.loads(ws.receive_text())["kind"] == "answer"
    assert len(made) == 2 and isinstance(made[0]["bus"], Bus)
    assert made[0]["bus"] is made[1]["bus"] is brain.partner_bus, "one bus for every Talk session"
    assert made[0]["said"] is made[1]["said"] is said
