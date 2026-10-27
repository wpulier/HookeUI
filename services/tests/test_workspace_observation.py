"""Workspace observation core contracts: synthetic input, no Studio/model/camera acceptance.

These tests exercise the proposed public value interfaces; Screen transport, source-store
authority, rendering and call admission remain integration obligations. Initial test-only
commit deliberately precedes workspace_observation.py; the parent serial gate observes RED.
"""
from copy import deepcopy
import json

import pytest

from orchestrator.workspace_observation import (
    ClientRef,
    Limits,
    ObservationError,
    check_target_current,
    normalize_capture_reply,
    normalize_observation,
    pin_request,
    workspace_to_source,
)


CLIENT = ClientRef(
    workspace_id="workspace-a", client_id="client-a", page_epoch="page-a",
    connection_epoch="socket-a",
)


def source(key="source-a", *, kind="saved-image", media="raw", version=1):
    """Canonical windows SourceRef, not the old media-kind-as-source-kind sketch."""
    return {
        "key": key, "kind": kind, "media": media, "availability": "retained",
        "identity": {
            "resource": {"day": "2026-10-08", "id": "d1", "version": version, "picture": 0},
            "capture": {"run": "run-a", "bootId": "bridge-a", "seq": 90, "frameId": 92},
            "crop": {"space": "capture", "rect": [20, 30, 640, 480]},
        },
        "provenance": {"parents": [], "transform": "original-pixel crop", "producer": "snapshot"},
        "digest": "a" * 64,
    }


def item(pid="window-a", source_key="source-a"):
    return {
        "id": pid, "rootId": f"root:{pid}", "workspaceId": "workspace-a",
        "kind": "image", "lifetimeGeneration": 1, "revision": 2,
        "sourceGeneration": 1, "sourceKey": source_key, "sourceToken": f"served:{source_key}",
        "open": True, "connected": True, "visible": True, "z": 10,
        "rect": [100.25, 40.5, 360.5, 300.25],
        "contentRect": [120.25, 60.5, 320, 240], "space": "capture",
        "clipRect": [120.25, 60.5, 320, 240],
        "viewTransform": {
            "sourceToContent": [0.5, 0, 0, 0.5, 0, 0],
            "contentOrigin": [120.25, 60.5],
            "sourceToWorkspace": [0.5, 0, 0, 0.5, 120.25, 60.5],
            "originalSize": [640, 480], "transformRevision": 1,
        },
        "load": {"state": "drawn", "sourceKey": source_key, "sourceGeneration": 1,
                 "naturalSize": [640, 480], "drawToken": "draw-a"},
        "propertyVersions": {"rect": 2, "source": 1},
        "propertyOwners": {"rect": "user", "source": "request-a"},
        "controls": [{"id": f"close:{pid}", "targetId": pid, "role": "button", "enabled": True}],
    }


def report(*, seq=10):
    return {
        "schema": "screen-observation-v2",
        "client": {"workspaceId": "workspace-a", "clientId": "client-a",
                   "pageEpoch": "page-a", "connectionEpoch": "socket-a"},
        "appRunId": "run-a", "seq": seq, "layoutRevision": 3,
        "capturedMonoMs": 5000.25,
        "viewport": {"layoutSize": [1440, 900], "visualRect": [0, 0, 1440, 900],
                     "visualScale": 1, "documentScroll": [0, 0], "dpr": 2,
                     "visible": True, "documentFocused": True, "activeSurface": "live"},
        "items": [item()], "sources": [source()],
        "targets": [{"id": "target-a", "type": "presentation", "presentationId": "window-a",
                     "sourceKey": "source-a", "basis": "selected"}],
        "edges": [{"type": "shows", "from": "window-a", "to": "source-a"}],
        "interaction": {
            "pointer": {"modality": "mouse", "state": "present", "css": [200.25, 120.5],
                        "eventMonoMs": 4900.25, "sampledMonoMs": 5000.25,
                        "buttons": 0, "hitPath": ["window-a"], "targetCandidates": ["target-a"]},
            "contacts": [],
            "focus": {"controlId": "ask-input", "presentationId": None,
                      "role": "textbox", "editable": True},
            "lastContentPointer": {"presentationId": "window-a", "sourceKey": "source-a",
                                   "css": [200.25, 120.5], "ageMs": 100, "historical": True},
            "lastContentFocus": {"presentationId": "window-a", "controlId": "window-a",
                                 "role": "group", "editable": False, "ageMs": 100},
            "selected": ["target-a"], "availability": "observed", "unknown": [],
        },
        "complete": True, "omitted": {"items": 0, "sources": 0, "targets": 0, "edges": 0},
        "applied": [], "user": [], "commandResults": [],
    }


def normalize(message=None, *, client=CLIENT, previous=None, received_at=10.0, limits=None):
    return normalize_observation(
        report() if message is None else message,
        client=client, app_run_id="run-a", received_at=received_at,
        previous=previous, limits=limits or Limits(),
    )


def pinned(observation=None):
    return pin_request(
        observation or normalize(), snapshot_id="run-a:request-a:observation-10",
        timing="client-send", requested_at=10.05, max_report_age_ms=1000,
    )


def assert_refused(code, call):
    with pytest.raises(ObservationError) as error:
        call()
    assert error.value.code == code


def test_same_source_two_independent_windows_preserve_float_geometry_and_identity():
    message = report()
    second = item("window-b")
    second["rect"] = [600.125, 40.5, 360.5, 300.25]
    second["contentRect"] = [620.125, 60.5, 320, 240]
    second["clipRect"] = [620.125, 60.5, 320, 240]
    second["viewTransform"]["contentOrigin"] = [620.125, 60.5]
    second["viewTransform"]["sourceToWorkspace"] = [0.5, 0, 0, 0.5, 620.125, 60.5]
    message["items"].append(second)
    message["edges"].append({"type": "shows", "from": "window-b", "to": "source-a"})
    observed = normalize(message).wire()
    assert [i["id"] for i in observed["items"]] == ["window-a", "window-b"]
    assert observed["items"][0]["rect"] == [100.25, 40.5, 360.5, 300.25]
    assert len(observed["sources"]) == 1
    assert observed["sources"][0]["kind"] == "saved-image"
    assert observed["sources"][0]["media"] == "raw"


def test_request_snapshot_cannot_be_changed_by_input_or_wire_mutation():
    message = report()
    observation = normalize(message)
    snapshot = pinned(observation)
    message["items"][0]["rect"][0] = 999
    message["sources"][0]["identity"]["resource"]["version"] = 9
    exported = snapshot.wire()
    exported["observation"]["items"][0]["sourceKey"] = "wrong-source"
    assert snapshot.wire()["observation"]["items"][0]["rect"][0] == 100.25
    assert snapshot.wire()["observation"]["sources"][0]["identity"]["resource"]["version"] == 1
    assert snapshot.wire()["observation"]["items"][0]["sourceKey"] == "source-a"


def test_send_editor_focus_keeps_content_pointer_without_carrying_editor_values():
    message = report()
    message["interaction"]["focus"].update(value="PRIVATE INPUT", text="PRIVATE INPUT")
    wire = pinned(normalize(message)).wire()
    interaction = wire["observation"]["interaction"]
    assert interaction["focus"]["controlId"] == "ask-input"
    assert interaction["lastContentPointer"]["presentationId"] == "window-a"
    assert interaction["selected"] == ["target-a"]
    assert "PRIVATE INPUT" not in json.dumps(wire)


@pytest.mark.parametrize("field,changed", [
    ("workspaceId", "workspace-b"), ("clientId", "client-b"),
    ("pageEpoch", "page-b"), ("connectionEpoch", "socket-b"),
])
def test_accepted_connection_refuses_other_workspace_client_or_incarnation(field, changed):
    message = report()
    message["client"][field] = changed
    assert_refused("CLIENT_MISMATCH", lambda: normalize(message))


def test_app_restart_report_is_not_this_run_even_with_same_client_and_seq():
    message = report()
    message["appRunId"] = "earlier-run"
    assert_refused("RUN_MISMATCH", lambda: normalize(message))


@pytest.mark.parametrize("seq", [9, 10])
def test_replayed_or_out_of_order_report_cannot_replace_previous(seq):
    previous = normalize(report(seq=10))
    assert_refused("REPORT_REPLAYED", lambda: normalize(report(seq=seq), previous=previous))


def test_new_connection_needs_its_own_baseline_not_a_prior_socket_report():
    client = ClientRef(workspace_id="workspace-a", client_id="client-a", page_epoch="page-a",
                       connection_epoch="socket-b")
    message = report(seq=1)
    message["client"]["connectionEpoch"] = "socket-b"
    assert normalize(message, client=client).wire()["seq"] == 1
    assert_refused("CLIENT_MISMATCH", lambda: normalize(message, client=client, previous=normalize()))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True])
def test_nonfinite_or_boolean_css_geometry_is_refused(value):
    message = report()
    message["items"][0]["rect"][0] = value
    assert_refused("INVALID_GEOMETRY", lambda: normalize(message))


def test_inconsistent_source_to_content_and_workspace_mapping_is_refused():
    message = report()
    message["items"][0]["viewTransform"]["sourceToWorkspace"][4] = 300
    assert_refused("TRANSFORM_MISMATCH", lambda: normalize(message))


def test_source_space_hit_uses_css_floats_not_dpr_or_chrome():
    observed_item = normalize().wire()["items"][0]
    assert workspace_to_source((200.25, 120.5), observed_item) == (160.0, 120.0)
    assert workspace_to_source((110.25, 50.5), observed_item) is None
    assert workspace_to_source((450.25, 120.5), observed_item) is None


def test_singular_transform_has_no_invented_source_hit():
    message = report()
    transform = message["items"][0]["viewTransform"]
    transform["sourceToContent"] = [0, 0, 0, 0, 0, 0]
    transform["sourceToWorkspace"] = [0, 0, 0, 0, 120.25, 60.5]
    observed_item = normalize(message).wire()["items"][0]
    assert workspace_to_source((200.25, 120.5), observed_item) is None


def test_dangling_source_and_duplicate_instance_ids_are_refused():
    missing = report()
    missing["items"][0]["sourceKey"] = "not-reported"
    assert_refused("SOURCE_UNRESOLVED", lambda: normalize(missing))
    repeated = report()
    repeated["items"].append(deepcopy(repeated["items"][0]))
    assert_refused("DUPLICATE_ID", lambda: normalize(repeated))


def test_source_union_keeps_renderer_kind_distinct_from_media_provenance():
    message = report()
    message["sources"][0]["kind"] = "composite"
    message["sources"][0]["media"] = "derived"
    message["sources"][0]["components"] = []
    observed = normalize(message).wire()["sources"][0]
    assert observed["kind"] == "composite" and observed["media"] == "derived"
    assert observed["components"] == []  # never infer originals from pixels/title
    message["sources"][0]["kind"] = "raw"  # provenance is not a SourceRef kind
    assert_refused("INVALID_SOURCE", lambda: normalize(message))


@pytest.mark.parametrize("media", ["capture", "labelled", "reference"])
def test_authoritative_p5_media_values_are_preserved_without_reclassification(media):
    message = report()
    message["sources"][0]["media"] = media
    observed = normalize(message).wire()["sources"][0]
    assert observed["kind"] == "saved-image"
    assert observed["media"] == media
    assert observed["identity"] == message["sources"][0]["identity"]
    assert observed["provenance"] == message["sources"][0]["provenance"]


def test_overflow_keeps_selected_target_and_reports_total_omission():
    message = report()
    message["items"] = [item(f"background-{n}") for n in range(4)] + [item()]
    message["edges"] = [{"type": "shows", "from": i["id"], "to": "source-a"} for i in message["items"]]
    observed = normalize(message, limits=Limits(items=2)).wire()
    assert len(observed["items"]) == 2
    assert "window-a" in {i["id"] for i in observed["items"]}
    assert observed["complete"] is False
    assert observed["totals"]["items"] == 5
    assert observed["omitted"]["items"] == 3
    assert all(e["from"] in {i["id"] for i in observed["items"]} for e in observed["edges"])


def test_truncated_sender_report_never_becomes_complete_or_proves_absence():
    message = report()
    message["complete"] = False
    message["omitted"]["items"] = 7
    observed = normalize(message).wire()
    assert observed["complete"] is False
    assert observed["totals"]["items"] == 8
    assert observed["omitted"]["items"] == 7


def test_required_target_cannot_be_silently_cut_to_fit_limit():
    assert_refused("TARGET_NOT_REPRESENTABLE", lambda: normalize(limits=Limits(items=0)))


def test_oversized_or_deep_untrusted_payload_is_bounded_before_copy():
    message = report()
    message["arbitrary"] = "x" * 2048
    assert_refused("REPORT_TOO_LARGE", lambda: normalize(message, limits=Limits(wire_bytes=1024)))
    message = report()
    nested = {}
    message["arbitrary"] = nested
    for _ in range(80):
        nested["child"] = {}
        nested = nested["child"]
    assert_refused("INVALID_REPORT", lambda: normalize(message))


def test_snapshot_reports_receive_age_without_subtracting_browser_monotonic_clock():
    snapshot = pin_request(normalize(received_at=100.0), snapshot_id="request-b", timing="server-final-transcript",
                           requested_at=102.0, max_report_age_ms=1000).wire()
    assert snapshot["reportAgeMs"] == 2000.0
    assert snapshot["availability"] == "stale"
    assert snapshot["timing"] == "server-final-transcript"
    assert snapshot["observation"]["interaction"]["pointer"]["eventAgeMs"] == 100.0


def test_explicit_target_remains_pinned_when_new_pointer_and_selection_arrive():
    original = pinned()
    message = report(seq=11)
    message["items"].append(item("window-b", "source-b"))
    message["sources"].append(source("source-b", version=2))
    message["interaction"]["pointer"]["hitPath"] = ["window-b"]
    message["interaction"]["selected"] = []
    binding = check_target_current(original, "target-a", normalize(message))
    assert binding.wire()["presentationId"] == "window-a"
    assert binding.wire()["sourceKey"] == "source-a"


@pytest.mark.parametrize("change,code", [
    ("source-generation", "SOURCE_CHANGED"), ("source-version", "SOURCE_CHANGED"),
    ("lifetime", "TARGET_GONE"), ("closed", "TARGET_GONE"),
])
def test_target_source_revision_or_lifetime_change_refuses_without_retargeting(change, code):
    message = report(seq=11)
    if change == "source-generation":
        message["items"][0]["sourceGeneration"] = 2
        message["items"][0]["load"]["sourceGeneration"] = 2
    elif change == "source-version":
        message["sources"][0]["identity"]["resource"]["version"] = 2
    elif change == "lifetime":
        message["items"][0]["lifetimeGeneration"] = 2
    else:
        message["items"][0].update(open=False, connected=False, visible=False)
    assert_refused(code, lambda: check_target_current(pinned(), "target-a", normalize(message)))


def test_only_touched_property_revision_blocks_action_not_unrelated_state_revision():
    original = pinned()
    message = report(seq=11)
    message["items"][0]["revision"] = 3
    message["items"][0]["propertyVersions"]["rect"] = 3
    current = normalize(message)
    assert check_target_current(original, "target-a", current, properties=("source",)).wire()["sourceKey"] == "source-a"
    assert_refused("OWNERSHIP_STALE", lambda: check_target_current(original, "target-a", current, properties=("rect",)))


def test_target_name_is_an_explicit_id_not_language_or_latest_window_rule():
    assert_refused("TARGET_UNRESOLVED", lambda: check_target_current(pinned(), "that one", normalize(report(seq=11))))


def test_new_page_cannot_act_on_old_page_snapshot_even_with_reused_window_ids():
    client = ClientRef(workspace_id="workspace-a", client_id="client-a", page_epoch="page-b",
                       connection_epoch="socket-b")
    message = report(seq=1)
    message["client"].update(pageEpoch="page-b", connectionEpoch="socket-b")
    current = normalize(message, client=client)
    assert_refused("CLIENT_MISMATCH", lambda: check_target_current(pinned(), "target-a", current))


def capture_reply():
    return {
        "lookId": "look-a", "requestId": "request-a", "client": report()["client"],
        "appRunId": "run-a", "originSnapshotId": "run-a:request-a:observation-10",
        "capturedObservationId": "observation-11", "layoutRevisionBefore": 3, "layoutRevisionAfter": 3,
        "fidelity": "actual-tab-capture", "captureMethod": "one-shot-tab-track",
        "surfaceProof": "same-origin-page-handle", "originalSize": [1440, 900], "inputSize": [1440, 900],
        "workspaceToImage": [1, 0, 0, 1, 0, 0], "captureTimeMonoMs": 5100,
        "uncertaintyMs": 16, "coverage": "complete", "redactedRects": [],
        "jpeg": b"\xff\xd8\xff\xd9",  # synthetic framing only; decoding is capture integration's duty
    }


def expected_capture():
    return {"lookId": "look-a", "requestId": "request-a", "appRunId": "run-a",
            "originSnapshotId": "run-a:request-a:observation-10", "layoutRevision": 3}


def capture(message=None, *, client=CLIENT, limits=None):
    return normalize_capture_reply(capture_reply() if message is None else message, client=client,
                                   expected=expected_capture(), limits=limits or Limits())


def test_late_capture_reply_binds_request_client_and_does_not_pretend_send_time_pixels():
    reply = capture().wire()
    assert reply["originSnapshotId"] == "run-a:request-a:observation-10"
    assert reply["capturedObservationId"] == "observation-11"
    assert reply["captureTimeMonoMs"] == 5100
    assert "jpeg" not in reply  # no image bytes in serialized observation/receipt metadata


@pytest.mark.parametrize("field", ["lookId", "requestId", "originSnapshotId", "appRunId"])
def test_capture_for_another_request_or_run_cannot_settle_pending_look(field):
    message = capture_reply()
    message[field] = "wrong"
    assert_refused("CAPTURE_MISMATCH", lambda: capture(message))


def test_old_connection_capture_reply_cannot_settle_new_connection_even_in_same_page():
    message = capture_reply()
    message["client"]["connectionEpoch"] = "old-socket"
    assert_refused("CLIENT_MISMATCH", lambda: capture(message))


def test_capture_layout_change_is_not_a_coherent_workspace_observation():
    message = capture_reply()
    message["layoutRevisionAfter"] = 4
    assert_refused("LAYOUT_CHANGED", lambda: capture(message))


def test_internally_stable_capture_of_another_layout_cannot_settle_pending_look():
    message = capture_reply()
    message.update(layoutRevisionBefore=4, layoutRevisionAfter=4)
    assert_refused("LAYOUT_CHANGED", lambda: capture(message))


def test_approximate_composition_remains_labeled_and_cannot_be_promoted_to_actual_pixels():
    message = capture_reply()
    message.update(fidelity="approximate-composition", captureMethod="approximate-dom-media-compositor",
                   surfaceProof=None, coverage="partial", missing=["DOM text", "CSS clipping"])
    normalized = capture(message).wire()
    assert normalized["fidelity"] == "approximate-composition"
    assert normalized["coverage"] == "partial"
    assert normalized["missing"] == ["DOM text", "CSS clipping"]


def test_unverified_actual_capture_surface_is_refused():
    message = capture_reply()
    message["surfaceProof"] = None
    assert_refused("CAPTURE_WRONG_SURFACE", lambda: capture(message))


@pytest.mark.parametrize("change", ["bytes", "pixels", "dimension", "mapping"])
def test_capture_input_bounds_refuse_without_decode(change):
    message = capture_reply()
    if change == "bytes":
        message["jpeg"] += b"x" * 32
        assert_refused("IMAGE_TOO_LARGE", lambda: capture(message, limits=Limits(image_bytes=8)))
    elif change == "pixels":
        message["originalSize"] = [100_000, 100_000]
        assert_refused("IMAGE_TOO_LARGE", lambda: capture(message))
    elif change == "dimension":
        message["inputSize"] = [True, 900]
        assert_refused("INVALID_GEOMETRY", lambda: capture(message))
    else:
        message["workspaceToImage"][0] = float("nan")
        assert_refused("INVALID_GEOMETRY", lambda: capture(message))


# Review regressions: synthetic value admission only. Parent owns observed RED/GREEN.

@pytest.mark.parametrize("value", [0.25, 0.5])
def test_noneditable_control_actual_value_presence_and_state_survive_normalization(value):
    message = report()
    control = {"id": "fade:window-a", "targetId": "window-a", "role": "slider",
               "editable": False, "present": True, "enabled": True,
               "observedValue": value, "stateRevision": 8}
    message["items"][0]["controls"].append(control)
    observed = normalize(message).wire()["items"][0]["controls"][1]
    assert observed == control
    assert type(observed["observedValue"]) is float


def test_noneditable_checkbox_value_is_boolean_and_absent_control_is_explicit():
    message = report()
    control = {"id": "layer:window-a", "targetId": "window-a", "role": "checkbox",
               "editable": False, "present": False, "enabled": False,
               "observedValue": True, "stateRevision": 3}
    message["items"][0]["controls"].append(control)
    observed = normalize(message).wire()["items"][0]["controls"][1]
    assert observed["present"] is False and observed["enabled"] is False
    assert observed["observedValue"] is True and observed["stateRevision"] == 3


@pytest.mark.parametrize("field,value,code", [
    ("observedValue", float("nan"), "INVALID_GEOMETRY"),
    ("observedValue", {"arbitrary": "DOM object"}, "INVALID_REPORT"),
    ("stateRevision", True, "INVALID_REPORT"),
    ("present", "yes", "INVALID_REPORT"),
])
def test_registered_control_observation_is_typed_not_arbitrary_dom_data(field, value, code):
    message = report()
    control = {"id": "fade:window-a", "targetId": "window-a", "role": "slider",
               "editable": False, "present": True, "enabled": True,
               "observedValue": 0.5, "stateRevision": 8}
    control[field] = value
    message["items"][0]["controls"].append(control)
    assert_refused(code, lambda: normalize(message))


def test_editable_control_values_stay_private_while_registered_presence_survives():
    message = report()
    message["items"][0]["controls"].append({
        "id": "notes:window-a", "targetId": "window-a", "role": "textbox",
        "editable": True, "present": True, "enabled": True, "stateRevision": 2,
        "value": "PRIVATE DRAFT", "text": "PRIVATE DRAFT", "observedValue": "PRIVATE DRAFT",
    })
    observed = normalize(message).wire()
    control = observed["items"][0]["controls"][1]
    assert control["id"] == "notes:window-a" and control["present"] is True
    assert control["editable"] is True and control["stateRevision"] == 2
    assert "observedValue" not in control and "PRIVATE DRAFT" not in json.dumps(observed)


def test_actual_drawn_style_payload_binding_and_visibility_unknowns_survive_one_observation():
    message = report()
    drawn = message["items"][0]
    drawn["style"] = {"opacity": 0.5, "colour": "rgb(0, 255, 0)", "fill": "outline", "emphasis": "selected"}
    drawn["payloadBinding"] = {"decodedToken": "served:source-a", "servedDigest": "b" * 64,
                               "originalDigest": "a" * 64, "transform": "jpeg-transcode"}
    drawn["visibility"] = {"state": "unknown", "scope": "logical-browser",
                           "reasons": ["opaque-frame"], "unknown": ["physical-occlusion"]}
    drawn["unknown"] = ["physical-occlusion", "opaque-frame-contents"]
    drawn["load"]["token"] = "served:source-a"
    observed = normalize(message).wire()["items"][0]
    assert observed["style"] == drawn["style"]
    assert observed["payloadBinding"] == drawn["payloadBinding"]
    assert observed["visibility"] == drawn["visibility"] and observed["unknown"] == drawn["unknown"]
    assert observed["load"]["state"] == "drawn" and observed["load"]["drawToken"] == "draw-a"
    assert observed["load"]["token"] == "served:source-a"


def test_decode_error_and_unknown_draw_token_survive_without_inventing_drawn_success():
    message = report()
    message["items"][0]["load"].update(state="error", drawToken=None, token="served:source-a",
                                      errorCode="MEDIA_DECODE_FAILED")
    load = normalize(message).wire()["items"][0]["load"]
    assert load["state"] == "error" and load["errorCode"] == "MEDIA_DECODE_FAILED"
    assert load["token"] == "served:source-a" and load["drawToken"] is None


def interaction_only_report():
    """Two registered windows without selection/history accidentally protecting A."""
    message = report()
    second = item("window-b", "source-b")
    second.update(rect=[600.125, 40.5, 360.5, 300.25], contentRect=[620.125, 60.5, 320, 240],
                  clipRect=[620.125, 60.5, 320, 240])
    second["viewTransform"].update(contentOrigin=[620.125, 60.5],
                                 sourceToWorkspace=[0.5, 0, 0, 0.5, 620.125, 60.5])
    message["items"].append(second)
    message["sources"].append(source("source-b"))
    message["targets"] = []
    message["edges"].append({"type": "shows", "from": "window-b", "to": "source-b"})
    message["interaction"].update(
        pointer={"modality": "none", "state": "none", "css": None, "hitPath": [], "targetCandidates": []},
        focus={"presentationId": None, "controlId": "ask-input", "role": "textbox", "editable": True},
        selected=[], lastContentPointer=None, lastContentFocus=None,
    )
    return message


def content_interaction(message, kind):
    if kind == "focus":
        message["items"][1]["controls"].append({"id": "notes:window-b", "targetId": "window-b",
                                                "role": "textbox", "enabled": True, "editable": True, "present": True})
        message["interaction"]["focus"] = {"presentationId": "window-b", "controlId": "notes:window-b",
                                            "role": "textbox", "editable": True}
    else:
        point = {"pointerId": 12, "modality": "touch" if kind == "contact" else "mouse",
                 "state": "present", "css": [610.125, 50.5], "buttons": 1,
                 "presentationId": "window-b", "sourceKey": None, "sourcePoint": None,
                 "hitPath": ["close:window-b", "window-b"], "targetCandidates": []}
        if kind == "contact":
            message["interaction"]["contacts"] = [point]
        else:
            message["interaction"]["pointer"] = point


@pytest.mark.parametrize("kind", ["focus", "pointer", "contact"])
def test_current_interaction_dependencies_are_kept_before_background_under_item_bound(kind):
    message = interaction_only_report()
    content_interaction(message, kind)
    observed = normalize(message, limits=Limits(items=1)).wire()
    assert [i["id"] for i in observed["items"]] == ["window-b"]
    assert observed["omitted"]["items"] == 1 and observed["complete"] is False
    fact = observed["interaction"]["contacts"][0] if kind == "contact" else observed["interaction"][kind]
    assert fact["presentationId"] == "window-b"
    if kind != "focus":
        assert fact["sourcePoint"] is None and fact["hitPath"] == ["close:window-b", "window-b"]


@pytest.mark.parametrize("kind", ["focus", "pointer", "contact"])
def test_insufficient_interaction_dependency_room_never_leaves_actionable_dangling_facts(kind):
    message = interaction_only_report()
    content_interaction(message, kind)
    message["targets"] = report()["targets"]
    message["interaction"]["selected"] = ["target-a"]
    try:
        observed = normalize(message, limits=Limits(items=1)).wire()
    except ObservationError as error:
        assert error.code == "TARGET_NOT_REPRESENTABLE"
        return
    represented = {i["id"] for i in observed["items"]}
    assert len(observed["items"]) <= 1 and "window-a" in represented
    if kind == "contact" and not observed["interaction"]["contacts"]:
        assert observed["interaction"]["availability"] == "partial" and observed["interaction"]["unknown"]
        assert observed["interaction"]["omittedContacts"] > 0
        return
    fact = observed["interaction"]["contacts"][0] if kind == "contact" else observed["interaction"][kind]
    if fact.get("presentationId") not in represented:
        assert fact.get("available") is False or fact.get("presentationId") is None
        assert observed["interaction"]["availability"] == "partial" and observed["interaction"]["unknown"]
        if kind != "focus":
            assert fact["hitPath"] == [] and fact["targetCandidates"] == [] and fact.get("sourcePoint") is None


def test_content_focus_cannot_name_an_unregistered_control_inside_a_registered_window():
    message = interaction_only_report()
    message["interaction"]["focus"] = {"presentationId": "window-b", "controlId": "unregistered-control",
                                        "role": "button", "editable": False}
    try:
        observed = normalize(message).wire()
    except ObservationError as error:
        assert error.code == "TARGET_UNRESOLVED"
        return
    focus = observed["interaction"]["focus"]
    assert focus.get("available") is False or focus.get("controlId") is None
    assert observed["interaction"]["availability"] == "partial" and observed["interaction"]["unknown"]


def nested_composite_report(*, seq=10):
    message = report(seq=seq)
    root = message["sources"][0]
    root.update(kind="composite", media="derived", availability="temporary", locator="https://example.invalid/root-1")
    child = source("component-a", kind="composite", media="derived")
    child.update(availability="temporary", locator="https://example.invalid/child-1")
    leaf = source("component-b")
    leaf["identity"].update(member="c17", revision=1)
    leaf.update(availability="temporary", locator="https://example.invalid/leaf-1")
    child["components"] = [leaf]
    root["components"] = [child]
    return message


def component_at(message, depth):
    component = message["sources"][0]
    for _ in range(depth):
        component = component["components"][0]
    return component


@pytest.mark.parametrize("depth", [0, 1, 2])
@pytest.mark.parametrize("metadata", ["locator", "availability"])
def test_source_identity_ignores_renewable_metadata_at_every_component_depth(depth, metadata):
    original = pinned(normalize(nested_composite_report()))
    message = nested_composite_report(seq=11)
    changed = component_at(message, depth)
    changed[metadata] = "https://example.invalid/renewed" if metadata == "locator" else "retained"
    current = normalize(message)
    binding = check_target_current(original, "target-a", current)
    assert binding.wire()["sourceKey"] == "source-a"
    assert component_at(current.wire(), depth)[metadata] == changed[metadata]  # retained fact, excluded from identity only


@pytest.mark.parametrize("change", ["producer", "version", "member", "revision", "crop", "digest"])
def test_nested_component_producing_identity_change_still_refuses_after_metadata_renewal(change):
    original = pinned(normalize(nested_composite_report()))
    message = nested_composite_report(seq=11)
    leaf = component_at(message, 2)
    leaf.update(locator="https://example.invalid/renewed", availability="retained")
    if change == "producer":
        leaf["provenance"]["producer"] = "different-producer"
    elif change == "version":
        leaf["identity"]["resource"]["version"] = 2
    elif change in ("member", "revision"):
        leaf["identity"][change] = "c18" if change == "member" else 2
    elif change == "crop":
        leaf["identity"]["crop"]["rect"][0] = 25
    else:
        leaf["digest"] = "c" * 64
    assert_refused("SOURCE_CHANGED", lambda: check_target_current(original, "target-a", normalize(message)))


@pytest.mark.parametrize("timing", ["server-handoff-arrival", "server-receive-legacy"])
def test_honest_fallback_pin_timing_and_age_cannot_be_upgraded_by_later_send_snapshot(timing):
    original = pin_request(normalize(received_at=10), snapshot_id="fallback-a", timing=timing,
                           requested_at=12, max_report_age_ms=1000)
    later = pin_request(normalize(report(seq=11), received_at=13), snapshot_id="later-send", timing="client-send",
                        requested_at=13.05, max_report_age_ms=1000)
    assert later.wire()["timing"] == "client-send"
    assert original.wire()["timing"] == timing and original.wire()["reportAgeMs"] == 2000
    assert original.wire()["availability"] == "stale" and original.wire()["observation"]["seq"] == 10


def test_unrecognized_pin_timing_does_not_gain_send_time_precision():
    assert_refused("INVALID_REPORT", lambda: pin_request(normalize(), snapshot_id="wrong-time", timing="claimed-send-time",
                                                        requested_at=10.05, max_report_age_ms=1000))


LIFETIME_A = "f7d1d5f0-5953-4b90-891f-1ad63c5a427e"
LIFETIME_B = "b189b680-55c8-4962-888e-3657d7a247a1"


def test_opaque_windows_uuid_lifetime_roundtrips_through_observation_pin_and_current_binding():
    message = report()
    message["items"][0]["lifetimeGeneration"] = LIFETIME_A
    observed = normalize(message)
    original = pinned(observed)
    current_message = deepcopy(message)
    current_message["seq"] = 11
    binding = check_target_current(original, "target-a", normalize(current_message))
    assert observed.wire()["items"][0]["lifetimeGeneration"] == LIFETIME_A
    assert original.wire()["observation"]["items"][0]["lifetimeGeneration"] == LIFETIME_A
    assert binding.wire()["lifetimeGeneration"] == LIFETIME_A
    assert type(binding.wire()["lifetimeGeneration"]) is str


def test_a_different_opaque_lifetime_refuses_the_original_target_without_counter_conversion():
    message = report()
    message["items"][0]["lifetimeGeneration"] = LIFETIME_A
    original = pinned(normalize(message))
    message["seq"] = 11
    message["items"][0]["lifetimeGeneration"] = LIFETIME_B
    assert_refused("TARGET_GONE", lambda: check_target_current(original, "target-a", normalize(message)))


def test_legacy_numeric_lifetime_keeps_its_type_and_does_not_alias_an_opaque_uuid():
    original = pinned()
    assert type(original.wire()["observation"]["items"][0]["lifetimeGeneration"]) is int
    message = report(seq=11)
    message["items"][0]["lifetimeGeneration"] = LIFETIME_A
    assert_refused("TARGET_GONE", lambda: check_target_current(original, "target-a", normalize(message)))


@pytest.mark.parametrize("value", [True, "", "x" * 257, 1.0])
def test_invalid_lifetime_values_remain_refused_without_loosening_numeric_revisions(value):
    message = report()
    message["items"][0]["lifetimeGeneration"] = value
    assert_refused("INVALID_REPORT", lambda: normalize(message))


@pytest.mark.parametrize("field", ["revision", "sourceGeneration", "propertyVersion"])
@pytest.mark.parametrize("value", [True, "2"])
def test_numeric_state_and_source_revisions_stay_strict_when_lifetime_becomes_opaque(field, value):
    message = report()
    if field == "propertyVersion":
        message["items"][0]["propertyVersions"]["rect"] = value
    else:
        message["items"][0][field] = value
    assert_refused("INVALID_REPORT", lambda: normalize(message))


@pytest.mark.parametrize("extra", [0, 1])
def test_encoded_capture_admission_retains_six_mib_boundary(extra):
    message = capture_reply()
    size = 6 * 1024 * 1024 + extra
    message["jpeg"] = message["jpeg"] + b"x" * (size - len(message["jpeg"]))
    if extra:
        assert_refused("IMAGE_TOO_LARGE", lambda: capture(message))
    else:
        assert capture(message).wire()["inputSize"] == [1440, 900]


@pytest.mark.parametrize("over", [False, True])
def test_capture_decoded_pixel_budget_remains_four_million_before_model_downsample(over):
    message = capture_reply()
    # Original decoded dimensions remain bounded; a separate header/acquisition
    # descriptor must not silently enlarge this decoded-image allowance.
    message["originalSize"] = [4_000_001, 1] if over else [2000, 2000]
    message["inputSize"] = [1920, 1] if over else [1920, 1920]
    if over:
        assert_refused("IMAGE_TOO_LARGE", lambda: capture(message))
    else:
        assert capture(message).wire()["originalSize"] == [2000, 2000]


@pytest.mark.parametrize("edge", [1920, 1921])
def test_capture_model_input_longest_edge_is_1920_even_below_pixel_budget(edge):
    message = capture_reply()
    message["originalSize"] = [edge, 1080]
    message["inputSize"] = [edge, 1080]
    if edge > 1920:
        assert_refused("IMAGE_TOO_LARGE", lambda: capture(message))
    else:
        assert capture(message).wire()["inputSize"] == [1920, 1080]


@pytest.mark.parametrize("override", ["image_bytes", "image_pixels"])
def test_capture_limit_configuration_cannot_raise_approved_admission(override):
    value = 6 * 1024 * 1024 + 1 if override == "image_bytes" else 4_000_001
    assert_refused("INVALID_REPORT", lambda: Limits(**{override: value}))


def source_availability_report(availability, *, seq=10):
    """Registered identity with no read/draw evidence; availability is only a fact."""
    message = report(seq=seq)
    message["sources"][0]["availability"] = availability
    message["items"][0]["visible"] = False
    message["items"][0]["load"] = {"state": "empty", "sourceKey": "source-a", "sourceGeneration": 1}
    message["interaction"].update(
        pointer={"modality": "none", "state": "none", "css": None, "hitPath": [], "targetCandidates": []},
        lastContentPointer=None, lastContentFocus=None,
    )
    return message


def test_canonical_unknown_availability_roundtrips_through_pin_and_identity_binding_without_readiness():
    message = source_availability_report("unknown")
    observed = normalize(message)
    original = pinned(observed)
    current = normalize(source_availability_report("unknown", seq=11))
    binding = check_target_current(original, "target-a", current)
    assert observed.wire()["sources"][0]["availability"] == "unknown"
    assert original.wire()["observation"]["sources"][0]["availability"] == "unknown"
    assert binding.wire()["source"]["availability"] == "unknown"
    assert current.wire()["items"][0]["load"]["state"] == "empty"
    assert current.wire()["items"][0]["visible"] is False
    payload = current.wire()["items"][0].get("payloadBinding")
    assert payload is None or (not payload.get("decodedToken") and not payload.get("servedDigest"))
    assert binding.wire().get("status") not in {"admitted", "loaded", "drawn", "confirmed", "data_complete"}


def test_retained_to_unknown_availability_is_current_metadata_not_a_producer_change_or_read_success():
    original = pinned(normalize(source_availability_report("retained")))
    current = normalize(source_availability_report("unknown", seq=11))
    binding = check_target_current(original, "target-a", current)
    assert binding.wire()["sourceKey"] == "source-a"
    assert original.wire()["observation"]["sources"][0]["availability"] == "retained"  # dated pin remains unchanged
    assert current.wire()["sources"][0]["availability"] == "unknown"
    assert current.wire()["items"][0]["load"]["state"] == "empty"
    assert current.wire()["items"][0]["visible"] is False


@pytest.mark.parametrize("change", ["version", "crop"])
def test_unknown_availability_does_not_hide_an_immutable_producing_source_change(change):
    original = pinned(normalize(source_availability_report("unknown")))
    message = source_availability_report("unknown", seq=11)
    if change == "version":
        message["sources"][0]["identity"]["resource"]["version"] = 2
    else:
        message["sources"][0]["identity"]["crop"]["rect"][0] = 25
    assert_refused("SOURCE_CHANGED", lambda: check_target_current(original, "target-a", normalize(message)))


# Adapter-wire v2 regressions: parent owns RED; normalizer/Screen stay unchanged.

@pytest.mark.parametrize("staged", [True, False, None])
def test_explicit_staging_true_false_or_unknown_survives_normalization_and_immutable_pin(staged):
    message = report()
    message["items"][0]["staged"] = staged
    observation = normalize(message)
    original = pinned(observation)
    observed = observation.wire()["items"][0]
    assert "staged" in observed and observed["staged"] is staged
    assert original.wire()["observation"]["items"][0]["staged"] is staged
    message["items"][0]["staged"] = not staged
    observed["staged"] = not staged
    assert original.wire()["observation"]["items"][0]["staged"] is staged


def test_missing_staging_is_not_defaulted_to_false_by_open_visible_or_drawn_desired_state():
    message = report()
    assert "staged" not in message["items"][0]
    observed = normalize(message).wire()["items"][0]
    assert observed["open"] and observed["visible"] and observed["load"]["state"] == "drawn"
    assert "staged" not in observed, "missing staging cannot establish independent visible children"


@pytest.mark.parametrize("staged", [0, 1, "false", "unknown", {}, [], 0.0])
def test_staging_rejects_untyped_or_truthy_values_instead_of_silently_erasing_them(staged):
    message = report()
    message["items"][0]["staged"] = staged
    assert_refused("INVALID_REPORT", lambda: normalize(message))


def live_draw_report(*, seq=10):
    """Literal rendered sample, not a frame read or a sample minted from SourceRef.

    The immutable live anchor is capture-90/epoch 2. Actual drawn sample is frame
    351/bridge seq 350/epoch 3. Neither is the report sequence (10).
    """
    message = report(seq=seq)
    message["sources"] = [{
        "key": "source-a", "kind": "live", "media": "raw", "availability": "temporary",
        "identity": {"run": "run-a", "objectId": "o3", "anchorCapture": "capture-90", "epoch": 2},
        "provenance": {"source": "camera-stream", "pixelsImmutable": False, "sampleFactsInReport": True},
    }]
    message["items"][0]["kind"] = "magnifier"
    message["items"][0]["load"]["drawToken"] = "opaque-live-paint-351"
    message["items"][0]["liveSample"] = {
        "run": "run-a", "boot": "bridge-a", "seq": 350, "frame": 351, "epoch": 3,
    }
    return message


def test_actual_live_sample_roundtrips_separately_from_opaque_draw_token_and_desired_anchor():
    message = live_draw_report()
    observed = normalize(message).wire()
    drawn = observed["items"][0]
    assert drawn.get("liveSample") == message["items"][0]["liveSample"]
    assert drawn["load"]["drawToken"] == "opaque-live-paint-351"
    assert type(drawn["load"]["drawToken"]) is str
    assert observed["sources"] == message["sources"]
    assert drawn["liveSample"]["seq"] != observed["seq"]
    assert drawn["liveSample"]["epoch"] != observed["sources"][0]["identity"]["epoch"]
    assert observed["sources"][0]["identity"]["anchorCapture"] == "capture-90"


def test_held_original_sample_is_separate_from_current_live_drawing():
    message = live_draw_report()
    row = message['items'][0]
    row['heldSample'] = row.pop('liveSample')
    observed = normalize(message).wire()['items'][0]
    assert observed['heldSample'] == row['heldSample']
    assert observed.get('liveSample') is None
    row['liveSample'] = row['heldSample']
    assert_refused('INVALID_REPORT', lambda: normalize(message))
    row.pop('liveSample')
    row['heldSample']['run'] = 'another-run'
    assert_refused('SOURCE_CHANGED', lambda: normalize(message))


@pytest.mark.parametrize("sample", [
    {"run": "run-a", "boot": "bridge-a", "seq": 350},
    {"run": "run-a", "boot": None, "seq": None, "frame": 351, "epoch": 3},
    {"run": "run-a", "boot": "bridge-a", "seq": 350, "frame": None, "epoch": None},
    {"run": "run-a", "boot": "b" * 256, "seq": 2**53 - 1, "frame": 0, "epoch": 0},
])
def test_live_sample_keeps_bounded_known_fields_and_explicit_unknowns_without_invention(sample):
    message = live_draw_report()
    message["items"][0]["liveSample"] = sample
    observed = normalize(message).wire()["items"][0]
    assert observed.get("liveSample") == sample
    assert observed["load"]["drawToken"] == "opaque-live-paint-351"
    if sample["boot"] is None or sample["seq"] is None:
        assert observed["liveSample"]["boot"] is None and observed["liveSample"]["seq"] is None
    if "frame" not in sample:
        assert "frame" not in observed["liveSample"] and "epoch" not in observed["liveSample"]


@pytest.mark.parametrize("explicit_unknown", [False, True])
def test_absent_or_unknown_live_sample_is_never_recovered_from_source_or_load_token(explicit_unknown):
    message = live_draw_report()
    if explicit_unknown:
        message["items"][0]["liveSample"] = None
    else:
        message["items"][0].pop("liveSample")
    drawn = normalize(message).wire()["items"][0]
    if explicit_unknown:
        assert "liveSample" in drawn and drawn["liveSample"] is None
    else:
        assert "liveSample" not in drawn
    assert drawn["load"]["drawToken"] == "opaque-live-paint-351"
    assert "run" not in drawn["load"] and "seq" not in drawn["load"]


def test_live_sample_pin_is_immutable_while_current_draw_advances_on_the_same_source_binding():
    message = live_draw_report()
    original = pinned(normalize(message))
    initial = deepcopy(message["items"][0]["liveSample"])
    message["items"][0]["liveSample"]["seq"] = 999
    exported = original.wire()
    assert exported["observation"]["items"][0].get("liveSample") == initial
    exported["observation"]["items"][0]["liveSample"]["frame"] = 999
    current_message = live_draw_report(seq=11)
    current_message["items"][0]["liveSample"].update(seq=351, frame=352)
    current_message["items"][0]["load"]["drawToken"] = "opaque-live-paint-352"
    current = normalize(current_message)
    binding = check_target_current(original, "target-a", current)
    assert binding.wire()["source"] == current_message["sources"][0]
    assert original.wire()["observation"]["items"][0]["liveSample"] == initial
    assert current.wire()["items"][0]["liveSample"]["seq"] == 351
    assert original.wire()["observation"]["items"][0]["load"]["drawToken"] == "opaque-live-paint-351"


@pytest.mark.parametrize("field,value", [
    ("record", True), ("record", "drawn"), ("record", [350]),
    ("run", None), ("run", True), ("run", ""), ("run", "r" * 257), ("run", float("nan")),
    ("boot", False), ("boot", ""), ("boot", "b" * 257),
    ("seq", -1), ("seq", True), ("seq", 350.0), ("seq", float("nan")),
    ("seq", float("inf")), ("seq", "350"), ("seq", 2**53),
    ("frame", -1), ("frame", False), ("frame", float("inf")),
    ("epoch", -1), ("epoch", True), ("epoch", float("nan")),
    ("missing-run", None), ("missing-boot", None), ("missing-seq", None),
    ("extra-fields", None),
])
def test_live_sample_refuses_malformed_nonfinite_boolean_or_oversized_records(field, value):
    message = live_draw_report()
    sample = message["items"][0]["liveSample"]
    if field == "record":
        message["items"][0]["liveSample"] = value
    elif field.startswith("missing-"):
        sample.pop(field.removeprefix("missing-"))
    elif field == "extra-fields":
        sample.update({f"extra-{n}": n for n in range(64)})
    else:
        sample[field] = value
    assert_refused("INVALID_REPORT", lambda: normalize(message))


@pytest.mark.parametrize("state", ["empty", "loading", "decoded", "error", "unknown"])
def test_known_live_draw_sample_requires_actual_drawn_state_not_scheduled_or_decoded_state(state):
    message = live_draw_report()
    message["items"][0]["load"]["state"] = state
    assert_refused("INVALID_REPORT", lambda: normalize(message))


@pytest.mark.parametrize("change,code", [
    ("sample-run", "SOURCE_CHANGED"), ("source-run", "SOURCE_CHANGED"),
    ("saved-source", "INVALID_SOURCE"), ("load-source", "SOURCE_CHANGED"),
    ("load-generation", "SOURCE_CHANGED"),
])
def test_live_sample_must_belong_to_the_exact_reported_live_source_and_load_binding(change, code):
    message = live_draw_report()
    if change == "sample-run":
        message["items"][0]["liveSample"]["run"] = "different-producing-run"
    elif change == "source-run":
        message["sources"][0]["identity"]["run"] = "different-producing-run"
    elif change == "saved-source":
        message["sources"] = [source()]
    elif change == "load-source":
        message["sources"].append(source("source-b"))
        message["items"][0]["load"]["sourceKey"] = "source-b"
    else:
        message["items"][0]["load"]["sourceGeneration"] = 2
    assert_refused(code, lambda: normalize(message))


def test_live_frame_record_cannot_be_encoded_as_the_opaque_load_draw_token():
    message = live_draw_report()
    message["items"][0]["load"]["drawToken"] = deepcopy(message["items"][0]["liveSample"])
    assert_refused("INVALID_REPORT", lambda: normalize(message))


def test_foreground_coverage_is_independent_and_survives_normalization():
    value = report()
    value['complete'] = False
    value['foreground'] = {'scope': 'workspace-windows', 'hostId': 'stage', 'complete': True,
        'roots': [{'rootId': 'root:window-a', 'z': 10}]}
    result = normalize(value).wire()
    assert result['complete'] is False
    assert result['foreground'] == value['foreground']
    value['omitted'] = {'items': 1}
    value.pop('totals', None)
    assert normalize(value).wire()['foreground']['complete'] is False


@pytest.mark.parametrize('fault', ['scope', 'duplicate', 'non_numeric', 'excess', 'extra_field'])
def test_foreground_admission_rejects_ambiguous_or_unbounded_scope(fault):
    value = report()
    scope = {'scope': 'workspace-windows', 'hostId': 'stage', 'complete': True,
        'roots': [{'rootId': 'root:window-a', 'z': 10}]}
    value['foreground'] = scope
    if fault == 'scope': scope['scope'] = 'whole-desktop'
    elif fault == 'duplicate': scope['roots'] *= 2
    elif fault == 'non_numeric': scope['roots'][0]['z'] = True
    elif fault == 'excess': scope['roots'] = [{'rootId': str(i), 'z': i} for i in range(65)]
    elif fault == 'extra_field': scope['roots'][0]['trusted'] = True
    with pytest.raises(ObservationError): normalize(value)


def test_native_arrangement_context_is_pinned_at_send_and_closed_shape():
    message=report();message['items'][0]['view']={'arrangement':{'project':'2026-10-16','tab':'main','placement':'p1','focused':True}}
    snapshot=pinned(normalize(message));message['items'][0]['view']['arrangement']['tab']='later'
    assert snapshot.wire()['observation']['items'][0]['view']['arrangement']['tab']=='main'
    message['items'][0]['view']['arrangement']['nativePaint']='invented'
    assert_refused('INVALID_REPORT',lambda:normalize(message))
