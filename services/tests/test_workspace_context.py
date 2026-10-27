"""I6 model-input awareness: synthetic adapter facts over real existing context.

These fixtures describe server-owned capability/capture metadata at the Screen and
Services boundaries. They do NOT prove an actual tab-capture/window adapter exists.
Only the parent gate executes these tests. No model replies or screenshot oracles are
inserted into context. Moment.awareness is one captured, bounded facts projection.
"""
import json
from types import SimpleNamespace

import pytest

from orchestrator.brain import context
from orchestrator.brain.registry import Tool, schema
from orchestrator.brain.toolbox import Services, build
from orchestrator.frames import Frame
from orchestrator.magnifier import Magnifier
from orchestrator.screen import REPORT_FRESH_S, Screen
from tests.test_brain import make


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _world(*, camera=True):
    brain, hub, agent = make([])
    clock = Clock()
    screen = Screen(clock=clock)
    hub.screen = screen
    # Synthetic originating-client metadata supplied by the app/voice pairing adapter.
    hub.workspace_client = "client-A"
    hub.workspace_identity = {"client": "client-A", "pageEpoch": "page-A",
                              "connectionEpoch": "socket-A", "appRun": "run-A"}
    if camera:
        frame = Frame(7, b"synthetic-camera-jpeg", 50.0, 1000.0, seq=71, boot="bridge-A")
        hub.source.get = lambda frame_id: frame if frame_id == 7 else None
        hub.source.is_fresh = lambda: True
    else:
        hub.latest_info = None
        hub.source.is_fresh = lambda: False
    magnifier = Magnifier(hub)
    brain.services = Services(hub=hub, agent=agent, magnifier=magnifier)
    brain.registry = build(brain.services)
    # Complete, synthetic catalogue boundary wire; current registered tools are real.
    wire = {"type": "capabilities", "preferences": {}, "build": "synthetic-boundary", "agentText": "",
            "actors": {"you": "You", "agent": "Agent", "auto": "On its own", "researcher": "Researcher"},
            "families": [{"id": "display", "title": "Display", "what": "Presentation capabilities"}],
            "roles": [], "parts": [], "version": "synthetic-boundary", "pending": 0,
            "capabilities": [
                {"id": "zoom", "family": "display", "actor": "agent", "title": "Close-up",
                 "what": "Open the existing singleton magnifier", "how": {"tool": "zoom"},
                 "roles": [], "needs": {}, "parts": [], "available": True, "why": "", "verdict": None},
                {"id": "workspace-split", "family": "display", "actor": "agent", "title": "Independent views",
                 "what": "Split into independent source-bound windows", "how": {"tool": "present_instances"},
                 "roles": [], "needs": {}, "parts": [], "available": False,
                 "why": "independent presentation adapter not installed", "verdict": None},
            ]}
    brain.services.capabilities = lambda: wire
    return brain, hub, screen, clock, wire


def _report(screen, *, client="client-A", visible=True, renderer=None, extra=None):
    item = {"id": "magnifier", "kind": "magnifier", "open": False,
            "rect": [0, 0, 320, 240], "style": {}, "shows": None,
            "capabilities": renderer or {"maxInstances": 1, "independentControls": False,
                                         "verbs": ["open", "close", "move", "resize", "zoom"]}}
    message = {"items": [item], "viewport": [1280, 720], "visible": visible, "seq": 9,
               "applied": [], "user": [], **(extra or {})}
    screen.report(message, client=client)


def _facts(brain):
    facts = getattr(brain.moment(), "awareness", {})
    assert isinstance(facts, dict)
    return facts


def _observation(brain, kind):
    row = _facts(brain).get("observations", {}).get(kind)
    assert isinstance(row, dict), f"missing source-specific {kind} facts"
    return row


def _inputs(brain, moment):
    return {"typed": "\n".join(context.scene_table(moment)),
            "fast": "\n".join(str(v) for v in context.fast_fields(moment).values()),
            "voice_note": context.view_note(moment), "voice_recap": brain.voice_recap()}


def test_camera_pixels_are_available_without_claiming_workspace_pixels_or_independent_windows():
    brain, _, _, _, _ = _world()
    camera = _observation(brain, "camera")
    assert camera.get("available") is True and camera.get("current") is True
    assert camera.get("provenance") == "raw-camera"
    assert (camera.get("frame"), camera.get("boot"), camera.get("seq")) == (7, "bridge-A", 71)
    assert _observation(brain, "workspace_report").get("available") is False
    image = _observation(brain, "workspace_image")
    assert image.get("available") is False and image.get("verified") is False
    presentation = _facts(brain).get("presentation", {})
    assert presentation.get("independent_windows") is False and presentation.get("split") is False


def test_missing_camera_stays_missing_even_when_a_workspace_report_says_live():
    brain, _, screen, _, _ = _world(camera=False)
    _report(screen, extra={"items": [{"id": "live", "kind": "live", "open": True}]})
    assert _observation(brain, "camera").get("available") is False
    assert _observation(brain, "workspace_report").get("available") is True
    assert _observation(brain, "workspace_image").get("verified") is False


def test_paired_structured_report_is_current_but_is_not_a_workspace_image():
    brain, _, screen, _, _ = _world()
    _report(screen)
    report = _observation(brain, "workspace_report")
    assert report.get("available") is True and report.get("current") is True
    assert report.get("provenance") == "structured-report" and report.get("client") == "client-A"
    assert report.get("seq") == 9
    assert _observation(brain, "workspace_image").get("verified") is False


@pytest.mark.parametrize("reason", ["stale", "hidden", "other-client"])
def test_stale_hidden_or_other_client_report_cannot_be_current_originating_workspace(reason):
    brain, _, screen, clock, _ = _world()
    _report(screen, client="client-B" if reason == "other-client" else "client-A", visible=reason != "hidden")
    if reason == "stale":
        clock.now += REPORT_FRESH_S + 1
    assert _observation(brain, "workspace_report").get("current") is False
    assert _observation(brain, "workspace_image").get("verified") is False


def _capture(*, fidelity="approximate-composition", client="client-A", verified=False, at=1000.0):
    """Synthetic trusted capture-adapter output; no capture is performed by this fixture."""
    return {"id": "capture-A", "client": client, "pageEpoch": "page-A", "connectionEpoch": "socket-A",
            "appRun": "run-A", "layoutSeq": 9, "at": at, "fidelity": fidelity,
            "verified": verified, "authority": "authorized-originating-tab" if verified else "composition",
            "coverage": ["rendered-tab"] if verified else ["media", "panel-titles"],
            "omissions": [] if verified else ["DOM-text", "controls", "occlusion"],
            "width": 1280, "height": 720, "bytes": 1200}


def test_approximate_composition_is_labeled_with_missing_coverage_for_every_reader():
    brain, _, screen, _, _ = _world()
    _report(screen)
    screen.workspace_capture = _capture()
    image = _observation(brain, "workspace_image")
    assert image.get("available") is True and image.get("verified") is False
    assert image.get("provenance") == "approximate-composition"
    assert set(image.get("omissions", [])) >= {"DOM-text", "controls", "occlusion"}
    for reader, text in _inputs(brain, brain.moment()).items():
        assert "approximate-composition" in text and "controls" in text, (reader, text)
        assert "structured-report" in text and "raw-camera" in text, (reader, text)


def test_browser_claim_of_verified_capture_does_not_become_trusted_pixels():
    brain, _, screen, _, _ = _world()
    _report(screen, extra={"workspace_capture": _capture(fidelity="actual-tab-capture", verified=True),
                           "verified": True, "wholeScreen": True})
    assert _observation(brain, "workspace_image").get("verified") is False


def test_trusted_current_originating_tab_capture_is_distinct_from_camera_and_composition():
    brain, _, screen, _, _ = _world()
    _report(screen)
    screen.workspace_capture = _capture(fidelity="actual-tab-capture", verified=True)
    image = _observation(brain, "workspace_image")
    assert image.get("available") is True and image.get("current") is True and image.get("verified") is True
    assert image.get("provenance") == "actual-tab-capture" and image.get("client") == "client-A"
    assert image.get("coverage") == ["rendered-tab"]
    assert _observation(brain, "camera").get("provenance") == "raw-camera"
    for reader, text in _inputs(brain, brain.moment()).items():
        assert "actual-tab-capture" in text and "capture-A" in text, (reader, text)


@pytest.mark.parametrize("fault", ["other-client", "stale", "wrong-layout", "wrong-page", "wrong-socket", "wrong-run", "unverified-authority", "missing-client", "missing-pageEpoch",
                                   "missing-connectionEpoch", "missing-appRun", "missing-layoutSeq"])
def test_capture_manifest_cannot_verify_wrong_surface_age_layout_or_authority(fault):
    brain, _, screen, clock, _ = _world()
    _report(screen)
    manifest = _capture(fidelity="actual-tab-capture", verified=True)
    if fault.startswith("missing-"):
        manifest.pop(fault.removeprefix("missing-"))
    elif fault == "other-client":
        manifest["client"] = "client-B"
    elif fault == "stale":
        manifest["at"] = clock.now - REPORT_FRESH_S - 1
    elif fault == "wrong-layout":
        manifest["layoutSeq"] = 8
    elif fault == "wrong-page":
        manifest["pageEpoch"] = "page-B"
    elif fault == "wrong-socket":
        manifest["connectionEpoch"] = "socket-B"
    elif fault == "wrong-run":
        manifest["appRun"] = "run-B"
    else:
        manifest["authority"] = "unverified-surface"
    screen.workspace_capture = manifest
    assert _observation(brain, "workspace_image").get("verified") is False


def test_catalogue_claim_alone_cannot_advertise_unregistered_independent_window_support():
    brain, _, screen, _, wire = _world()
    _report(screen, renderer={"maxInstances": 4, "independentControls": True,
                              "verbs": ["open", "split", "duplicate", "close"]})
    wire["capabilities"][1]["available"] = True  # no actual registered adapter
    facts = _facts(brain)
    assert facts.get("capabilities", {}).get("workspace-split", {}).get("available") is False
    assert facts.get("presentation", {}).get("independent_windows") is False
    assert facts.get("presentation", {}).get("split") is False


def test_registered_schema_and_renderer_support_change_shared_facts_without_word_matching():
    brain, _, screen, _, wire = _world()

    async def never_dispatch(ctx, args):
        raise AssertionError("context capture must not execute a tool")

    brain.registry.add(Tool("present_instances", "Generic independent presentations", schema({
        "do": {"type": "string", "enum": ["open", "duplicate", "split", "close"]},
        "instance": {"type": "string"}}, ["do"]), never_dispatch, family="display"))
    wire["capabilities"][1].update(available=True, why="")
    _report(screen, renderer={"maxInstances": 4, "independentControls": True,
                              "verbs": ["open", "split", "duplicate", "close"]})
    facts = _facts(brain)
    assert facts.get("capabilities", {}).get("workspace-split", {}).get("available") is True
    assert facts.get("presentation", {}).get("independent_windows") is True
    assert facts.get("presentation", {}).get("split") is True
    for request in ("open to magnifiers one for each", "place each crop in its own controllable view", "unrelated question"):
        moment = brain.moment(request=request)
        assert getattr(moment, "awareness", {}).get("presentation") == facts["presentation"]
        for reader, text in _inputs(brain, moment).items():
            assert "workspace-split" in text, (reader, text)


def test_unavailable_registered_capability_reason_reaches_typed_fast_and_voice_inputs():
    brain, _, _, _, wire = _world()
    wire["capabilities"][0].update(available=False, why="source capture unavailable")
    moment = brain.moment()
    capability = getattr(moment, "awareness", {}).get("capabilities", {}).get("zoom", {})
    assert capability.get("available") is False and capability.get("why") == "source capture unavailable"
    for reader, text in _inputs(brain, moment).items():
        assert "zoom" in text and "source capture unavailable" in text, (reader, text)


def test_captured_facts_are_bounded_and_do_not_retroactively_gain_a_later_observation():
    brain, _, screen, _, wire = _world()
    before = brain.moment()
    saved = json.dumps(getattr(before, "awareness", {}), sort_keys=True)
    _report(screen)
    screen.workspace_capture = _capture(fidelity="actual-tab-capture", verified=True)
    wire["capabilities"][0]["why"] = "x" * 20000
    after = _facts(brain)
    assert json.dumps(getattr(before, "awareness", {}), sort_keys=True) == saved
    assert after.get("observations", {}).get("workspace_image", {}).get("verified") is True
    assert len(json.dumps(after)) <= 8192, "bounded model facts must not dump the catalogue/report graph"
    assert all(len(text) <= 6000 for text in _inputs(brain, brain.moment()).values())


def test_camera_feed_availability_does_not_claim_pixels_in_text_only_fast_or_voice_context():
    """Feed/Sight facts are not an actual provider input manifest (later I3/I6 seam)."""
    from orchestrator.brain.fast import decide
    import asyncio

    brain, _, screen, _, _ = _world()
    _report(screen, extra={"items": [{"id": "live", "kind": "live", "open": True,
                                     "visible": False, "occluded": True}]})
    assert _observation(brain, "camera").get("available") is True
    moment = brain.moment()
    basis = getattr(moment, "awareness", {}).get("input", {})
    assert basis.get("basis") == "context-only"
    assert basis.get("camera_pixels") is False and basis.get("workspace_pixels") is False
    calls = []

    class TextOnlyModelBoundary:
        async def ask(self, images, prompt, max_output_tokens=0):
            calls.append((images, prompt))
            return SimpleNamespace(data={"answer": "", "needs_look": True, "actions": []}, latency_ms=0)

    asyncio.run(decide(moment, "can you see the whole screen live and wahts happening on it",
                       TextOnlyModelBoundary(), brain.registry.prompt_section(), brain.hub.settings.fast))
    assert calls[0][0] == []  # actual fast.decide provider boundary is text-only
    assert "context-only" in calls[0][1] and "raw-camera" in calls[0][1]
    assert "context-only" in brain.voice_recap()
    assert "context-only" in context.view_note(moment)


def test_zero_registered_live_members_cannot_be_projected_as_success_from_saved_view_visibility():
    brain, _, screen, _, _ = _world()
    _report(screen, extra={"items": [
        {"id": "layer:e-old", "kind": "population", "open": True, "shows": "e-old",
         "visible": True, "style": {}, "opacity": 1, "z": 1,
         "placement": "0 of 160 verified in place", "source": {"kind": "capture", "id": "capture-old", "run": "run-old", "frame": 2}},
        {"id": "magnifier", "kind": "magnifier", "open": True, "shows": "capture-old",
         "source": {"kind": "capture", "id": "capture-old", "run": "run-old", "frame": 2}}
    ]})
    report = _observation(brain, "workspace_report")
    assert report.get("placements", {}).get("layer:e-old") == "0 of 160 verified in place"
    assert _observation(brain, "camera").get("available") is True
    # A saved display and available feed remain distinct; the shared facts state the unmet live coverage.
    for reader, text in _inputs(brain, brain.moment()).items():
        assert "capture-old" in text and "0 of 160 verified in place" in text, (reader, text)
