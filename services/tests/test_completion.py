"""Synthetic observed-state contracts for the pure workspace completion core.

These reports are data, not fake Studio callbacks: no renderer/browser/model has run.
The future public seam accepts closed JSON-shaped records and returns JSON-shaped
confirmation/outcome/fact records. Adapters to Screen/ToolRun belong to a later slice.
Each assertion uses hand-checked observations rather than a production report builder.
"""
from copy import deepcopy
from importlib import import_module

import pytest


A = "583af585-47ab-4358-b21b-d45cf11043ac"
B = "27cb2f28-e4f6-4096-8d62-ae8ca575b39b"
PARENT = "8da0c56e-f252-40f3-90f4-a3c74e530a08"
REQUEST = "run-8:r7"
ACTION = REQUEST + ":a1"
COMMAND = "c-run-8-12"


def core():
    # Missing feature is a per-test failure, not a collection/import error.
    try:
        return import_module("orchestrator.completion")
    except ModuleNotFoundError as exc:
        if exc.name != "orchestrator.completion":
            raise
        pytest.fail("Workspace completion core is not implemented yet", pytrace=False)


def source(key="saved-a", *, member="c17", capture="capture-321"):
    """Canonical windows SourceRef; two producing refs may have identical bytes."""
    return {
        "key": key, "kind": "saved-image", "media": "raw",
        "identity": {
            "resource": {"day": "2026-10-08", "id": member, "version": 0, "picture": 0},
            "population": "e3", "member": member, "revision": 0,
            "capture": capture, "crop": [124, 80, 96, 110],
        },
        "provenance": {"run": "run-8", "boot": "bridge-1", "seq": 321,
                       "cropSpace": "capture-image", "transforms": [], "scale": None},
        "availability": "retained", "digest": "same-original-digest",
    }


def item(pid=A, *, bound_source=None, rect=None):
    return {
        "id": pid, "workspaceId": "workspace-1", "rootId": "root-" + pid,
        "revision": 4, "lifetimeGeneration": 1, "sourceGeneration": 2,
        "sourceRef": source() if bound_source is None else deepcopy(bound_source),
        "sourceToken": "payload-7", "open": True, "visible": True,
        "connected": True, "staged": False, "phase": "drawn",
        "rect": [20.0, 30.0, 320.0, 260.0] if rect is None else rect,
        "contentRect": [28.0, 60.0, 304.0, 210.0], "z": 5, "zoom": 2.0,
        "mediaSize": [96, 110], "drawRef": "paint-4",
        "payloadBinding": {"decodedToken": "payload-7", "servedDigest": "served-7",
                           "originalDigest": "same-original-digest", "transform": "jpeg-transcode"},
        "propertyVersions": {"source": 4, "open": 4, "rect": 4, "zoom": 4},
        "propertyOwners": {key: "request:" + REQUEST for key in ("source", "open", "rect", "zoom")},
        "controls": {name: {"enabled": True, "target": pid, "stateRevision": 4}
                     for name in ("move", "resize", "close", "zoom")},
    }


def fence(*pids):
    return {
        "requestId": REQUEST, "actionId": ACTION, "commandId": COMMAND,
        "appRunId": "run-8",
        "client": {"workspaceId": "workspace-1", "clientId": "tab-1",
                   "clientEpoch": "page-1", "connectionEpoch": "socket-1"},
        "afterReportSeq": 20, "issuedMono": 100.0, "deadlineMono": 104.0,
        "targets": {
            pid: {"lifetimeGeneration": 1, "sourceGeneration": 2,
                  "committedRevision": 4,
                  "basePropertyVersions": {"source": 3, "open": 3, "rect": 3, "zoom": 3},
                  "committedPropertyVersions": {"source": 4, "open": 4, "rect": 4, "zoom": 4},
                  "owner": "request:" + REQUEST,
                  "sourceToken": "payload-7", "payloadBinding": {"servedDigest": "served-7"}}
            for pid in (pids or (A,))
        },
    }


def report(*items):
    shown = list(items) or [item()]
    return {
        "client": {"workspaceId": "workspace-1", "clientId": "tab-1",
                   "clientEpoch": "page-1", "connectionEpoch": "socket-1"},
        "appRunId": "run-8", "seq": 21, "receivedAtMono": 101.0,
        "visible": True, "complete": True,
        "viewport": {"layout": [1280, 900], "dpr": 1, "visual": [0, 0, 1280, 900]},
        "items": shown,
        "commandResults": [
            {"commandId": COMMAND, "requestId": REQUEST, "actionId": ACTION,
             "presentationId": i["id"], "revision": 4, "lifetimeGeneration": 1,
             "sourceGeneration": 2, "committedPropertyVersions": {
                 "source": 4, "open": 4, "rect": 4, "zoom": 4},
             "phase": "drawn", "sourceToken": "payload-7", "drawRef": "paint-4"}
            for i in shown
        ],
    }


def expect(predicate, targets=(A,), value=None, *, phase="drawn", key=None, essential=True):
    return {"id": key or predicate, "predicate": predicate, "targets": list(targets),
            "expected": {} if value is None else value, "essential": essential,
            "requiredPhase": phase, "tolerance": None}


def picture_expectations(pid=A, bound_source=None):
    return (
        expect("source_exact", (pid,), {"sourceRef": source() if bound_source is None else bound_source}),
        expect("media_ready", (pid,), {"decoded": True, "drawn": True}),
        expect("visible", (pid,), {"value": True}),
    )


def compare(expected=None, observed=None, command=None, *, now=101.0):
    return core().compare(picture_expectations() if expected is None else expected,
                          report() if observed is None else observed,
                          fence() if command is None else command, now=now)


def codes(result):
    return {v["code"] for v in result["unmatched"]}


def test_exact_drawn_payload_confirms_requested_source():
    result = compare()
    assert result["status"] == "confirmed"
    assert set(result["matched"]) == {"source_exact", "media_ready", "visible"}
    assert result["unmatched"] == []
    assert result["report"]["seq"] == 21
    assert (result["requestId"], result["actionId"], result["commandId"]) == (REQUEST, ACTION, COMMAND)


def test_live_source_binding_does_not_require_a_saved_resource_or_fixed_pixels():
    live = {"key": "live-o3", "kind": "live", "media": "raw",
            "identity": {"run": "run-8", "objectId": "o3", "anchorCapture": "capture-321", "epoch": 7},
            "provenance": {"source": "camera-stream", "pixelsImmutable": False,
                           "sampleFactsInReport": True}, "availability": "temporary"}
    observed = report(item(bound_source=live))
    observed["items"][0]["drawRef"] = {"run": "run-8", "boot": "bridge-1", "seq": 350}
    observed["commandResults"][0]["drawRef"] = {"run": "run-8", "boot": "bridge-1", "seq": 350}
    result = compare(picture_expectations(bound_source=live), observed)
    assert result["status"] == "confirmed"
    assert result["observed"][0]["drawRef"]["seq"] == 350


@pytest.mark.parametrize("field,wrong", [
    ("workspaceId", "workspace-other"), ("clientId", "tab-other"),
    ("clientEpoch", "page-old"), ("connectionEpoch", "socket-old"),
], ids=["workspace", "client", "page_epoch", "socket_epoch"])
def test_another_origin_cannot_confirm_a_matching_looking_window(field, wrong):
    observed = report()
    observed["client"][field] = wrong
    result = compare(observed=observed)
    assert result["status"] == "unobserved"
    assert not result["matched"]
    assert result["error"]["boundary"] == "observe"


@pytest.mark.parametrize("change", ["run", "replayed_seq", "command", "action", "generation"],
                         ids=["run", "replayed_seq", "command", "action", "generation"])
def test_old_or_unrelated_ack_does_not_certify_new_effect(change):
    observed = report()
    if change == "run":
        observed["appRunId"] = "run-7"
    elif change == "replayed_seq":
        observed["seq"] = 20
    elif change in ("command", "action"):
        observed["commandResults"][0][change + "Id"] = "old-occurrence"
    else:
        observed["items"][0]["sourceGeneration"] = 1
    result = compare(observed=observed)
    assert result["status"] != "confirmed"
    assert result["error"]["boundary"] in ("observe", "commit")


@pytest.mark.parametrize("change", ["key", "member", "version", "kind"],
                         ids=["key", "member", "version", "kind"])
def test_identical_bytes_do_not_license_a_different_producing_source(change):
    observed = report()
    other = observed["items"][0]["sourceRef"]
    if change == "key":
        other["key"] = "saved-b"
    elif change == "member":
        other["identity"]["member"] = "c18"
    elif change == "version":
        other["identity"]["resource"]["version"] = 1
    else:
        other["media"] = "generated"
    result = compare(observed=observed)
    assert result["status"] == "failed"
    assert "SOURCE_MISMATCH" in codes(result)


def test_desired_source_token_cannot_hide_wrong_decoded_payload():
    observed = report()
    observed["items"][0]["payloadBinding"]["servedDigest"] = "bytes-from-another-source"
    result = compare(observed=observed)
    assert result["status"] == "failed"
    assert "SOURCE_MISMATCH" in codes(result)


def test_same_source_can_have_two_independent_windows():
    observed = report(item(A), item(B))
    expected = picture_expectations(A) + picture_expectations(B) + (
        expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),
        expect("control", (A, B), {"each": ["move", "resize", "close", "zoom"], "independent": True}),
    )
    result = compare(expected, observed, fence(A, B))
    assert result["status"] == "confirmed"
    assert {i["id"] for i in result["observed"]} == {A, B}


def test_two_pictures_in_one_root_are_not_two_windows():
    observed = report(item(A), item(B))
    observed["items"][1]["rootId"] = observed["items"][0]["rootId"]
    expected = (expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),)
    result = compare(expected, observed, fence(A, B))
    assert result["status"] == "failed"
    assert "TOPOLOGY_MISMATCH" in codes(result)


def test_control_targeting_sibling_window_prevents_independent_success():
    observed = report(item(A), item(B))
    observed["items"][1]["controls"]["zoom"]["target"] = A
    expected = (expect("control", (A, B), {"each": ["close", "zoom"], "independent": True}),)
    result = compare(expected, observed, fence(A, B))
    assert result["status"] == "failed"
    assert "CONTROL_UNAVAILABLE" in codes(result)


@pytest.mark.parametrize("change", ["hidden", "detached", "staged", "zero_dimensions"],
                         ids=["hidden", "detached", "staged", "zero_dimensions"])
def test_open_metadata_and_chrome_do_not_prove_visible_content(change):
    observed = report()
    bad = observed["items"][0]
    if change == "hidden":
        bad["visible"] = False
    elif change == "detached":
        bad["connected"] = False
    elif change == "staged":
        bad["staged"] = True
    else:
        bad["mediaSize"] = [0, 0]
    result = compare(observed=observed)
    assert result["status"] != "confirmed"
    assert result["unmatched"]


def test_decoded_image_remains_pending_until_its_pixels_are_drawn():
    observed = report()
    observed["items"][0].update(phase="loaded", drawRef=None)
    observed["commandResults"][0].update(phase="loaded", drawRef=None)
    result = compare(observed=observed)
    assert result["status"] == "pending"
    assert "media_ready" not in result["matched"]


def test_expired_draw_wait_is_timeout_without_asserting_image_absence():
    observed = report()
    observed["items"][0].update(phase="loaded", drawRef=None)
    observed["commandResults"][0].update(phase="loaded", drawRef=None)
    result = compare(observed=observed, now=104.0)
    assert result["status"] == "timed_out"
    assert result["error"]["code"] == "CONFIRM_TIMEOUT"
    assert result["observed"][0]["phase"] == "loaded"


def background(observed):
    """The studio in the background (another app or tab in front): its page reports itself hidden, and the
    server's projection marks every item not visible (screen.Screen._completion). The browser paints nothing
    there; it paints what it was given once the studio is in view again (live 2026-10-11: a photo shown in the
    studio was answered "could not pull up ... the workspace window was hidden")."""
    observed["visible"] = False
    for shown in observed["items"]:
        if shown.get("visible") is True:  # what the layout shows is "tab-hidden" (workspace-windows.ts)
            shown["visibility"] = "tab-hidden"
        shown["visible"] = False
    return observed


def not_drawn_yet(observed):
    observed["items"][0].update(phase="loaded", drawRef=None)
    observed["commandResults"][0].update(phase="loaded", drawRef=None)
    return observed


def test_a_studio_in_the_background_is_waiting_to_be_seen_not_failed():
    result = compare(observed=background(not_drawn_yet(report())))
    assert result["status"] == "pending"
    assert result["error"]["code"] == "STUDIO_IN_BACKGROUND"
    assert not result["matched"]  # nothing is claimed of a picture not drawn yet


def test_a_studio_in_the_background_never_times_the_draw_out_or_goes_stale():
    """Its last report stands until it reports again: a studio in the background changes only by what it is sent,
    and it reports at once when it is shown again (screen.Screen._current_client)."""
    result = compare(observed=background(not_drawn_yet(report())), now=500.0)  # deadline 104, report 399 s old
    assert result["status"] == "pending"
    assert result["error"]["code"] == "STUDIO_IN_BACKGROUND"


def test_a_draw_painted_before_the_studio_went_to_the_background_is_confirmed():
    """The command's own paint (its drawRef) was on screen; the studio going to the background after it does not
    undo it."""
    result = compare(observed=background(report()))
    assert result["status"] == "confirmed"


def test_a_window_hidden_while_the_studio_is_in_the_background_is_confirmed_hidden():
    """"Hide that photo" with the studio in the background: the window is hidden in the layout ("css-hidden"), so
    it is not shown, whatever it drew before (review of 2026-10-11: drawn items were taken as shown in the
    background, so a hide or a fade to nothing read as failed)."""
    observed = report()
    observed["items"][0].update(visible=False, visibility="css-hidden")
    result = compare((expect("visible", value={"value": False}),), background(observed))
    assert result["status"] == "confirmed"


def test_a_close_is_confirmed_while_the_studio_is_in_the_background():
    closed = item()
    closed.update(open=False, visible=False, connected=False, phase="closed", drawRef=None)
    observed = background(report(closed))
    observed["commandResults"][0].update(phase="closed", drawRef=None)
    result = compare((expect("closed", value={"value": True}),), observed)
    assert result["status"] == "confirmed"


def test_a_command_not_yet_acknowledged_by_a_studio_in_the_background_is_waiting():
    """A live window acknowledges its command as it draws (live 2026-10-11 second stack: "zoom in on the other
    stoma" in the background failed "The target has no matching command acknowledgment")."""
    observed = background(not_drawn_yet(report()))
    observed["commandResults"] = []
    result = compare(observed=observed, now=500.0)
    assert result["status"] == "pending"
    assert result["error"]["code"] == "STUDIO_IN_BACKGROUND"


def test_a_user_change_still_fails_while_the_studio_is_in_the_background():
    observed = background(report())
    observed["items"][0]["propertyOwners"]["rect"] = "user"
    result = compare(observed=observed)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "OWNERSHIP_STALE"


def test_a_wrong_picture_fails_even_while_the_studio_is_in_the_background():
    observed = background(report())
    observed["items"][0]["sourceRef"]["key"] = "saved-b"
    result = compare(observed=observed)
    assert result["status"] == "failed"
    assert "SOURCE_MISMATCH" in codes(result)


def test_another_origin_is_still_unobserved_while_the_studio_is_in_the_background():
    observed = background(report())
    observed["client"]["clientId"] = "tab-other"
    result = compare(observed=observed)
    assert result["status"] == "unobserved"
    assert result["error"]["code"] == "REPORT_STALE"


def test_a_fresh_sequence_with_stale_receive_time_is_not_current_evidence():
    observed = report()
    observed["receivedAtMono"] = 70.0
    result = compare(observed=observed, now=101.0)
    assert result["status"] == "unobserved"
    assert result["error"]["code"] == "REPORT_STALE"


def test_no_studio_is_unobserved_even_when_backend_has_finished():
    result = core().compare(picture_expectations(), None, fence(), now=101.0)
    assert result["status"] == "unobserved"
    assert not result["matched"]


def test_matching_source_with_wrong_geometry_does_not_fulfill_resize():
    expected = (expect("rect", value={"rect": [20, 30, 500, 360]}),)
    result = compare(expected)
    assert result["status"] == "failed"
    assert "GEOMETRY_MISMATCH" in codes(result)
    assert result["unmatched"][0]["observed"] == [20.0, 30.0, 320.0, 260.0]


def test_content_size_checks_pixels_area_instead_of_window_chrome():
    expected = (expect("content_size", value={"minWidth": 240, "minHeight": 180}),)
    observed = report()
    observed["items"][0]["contentRect"] = [28, 60, 70, 50]
    result = compare(expected, observed)
    assert result["status"] == "failed"
    assert "GEOMETRY_MISMATCH" in codes(result)


def test_beside_requires_actual_nonoverlapping_source_windows():
    observed = report(item(A), item(B, rect=[100, 30, 320, 260]))
    expected = (expect("relative_geometry", (A, B), {"relation": "left", "gap": 10}),)
    result = compare(expected, observed, fence(A, B))
    assert result["status"] == "failed"
    assert "GEOMETRY_MISMATCH" in codes(result)


def test_split_cannot_finish_while_the_original_parent_remains_open():
    observed = report(item(A), item(B), item(PARENT))
    expected = (
        expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),
        expect("closed", (PARENT,), {"value": True}, key="parent_retired"),
    )
    result = compare(expected, observed, fence(A, B, PARENT))
    assert result["status"] == "failed"
    assert "parent_retired" not in result["matched"]
    assert "independent_windows" in result["matched"]


def test_explicit_parent_removal_can_confirm_complete_split():
    observed = report(item(A), item(B))
    observed["commandResults"].append({
        **deepcopy(observed["commandResults"][0]), "presentationId": PARENT,
        "phase": "closed", "drawRef": None,
    })
    expected = (
        expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),
        expect("closed", (PARENT,), {"value": True}, key="parent_retired"),
    )
    result = compare(expected, observed, fence(A, B, PARENT))
    assert result["status"] == "confirmed"
    assert "parent_retired" in result["matched"]


def test_truncated_missing_target_is_not_evidence_it_was_closed():
    observed = report(item(A), item(B))
    observed["complete"] = False
    expected = (expect("closed", (PARENT,), {"value": True}),)
    result = compare(expected, observed, fence(PARENT))
    assert result["status"] != "confirmed"
    assert not result["matched"]


def test_same_valued_user_edit_supersedes_request_property_ownership():
    observed = report()
    observed["items"][0]["propertyVersions"]["zoom"] = 5
    observed["items"][0]["propertyOwners"]["zoom"] = "user"
    expected = (expect("control", value={"each": ["zoom"], "value": 2.0}),)
    result = compare(expected, observed)
    assert result["status"] != "confirmed"
    assert result["error"]["code"] == "OWNERSHIP_STALE"
    assert observed["items"][0]["propertyOwners"]["zoom"] == "user"


def test_unrelated_later_property_revision_does_not_cancel_valid_draw():
    observed = report()
    observed["items"][0]["revision"] = 5
    observed["items"][0]["propertyVersions"]["caption"] = 5
    observed["items"][0]["propertyOwners"]["caption"] = "user"
    assert compare(observed=observed)["status"] == "confirmed"


def test_two_windows_cannot_be_confirmed_from_ack_for_only_one():
    observed = report(item(A), item(B))
    observed["commandResults"] = observed["commandResults"][:1]
    expected = (expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),)
    result = compare(expected, observed, fence(A, B))
    assert result["status"] != "confirmed"


def test_group_topology_requires_both_windows_drawn_in_the_same_report():
    observed = report(item(A), item(B))
    observed["items"][1].update(phase="loaded", drawRef=None)
    observed["commandResults"][1].update(phase="loaded", drawRef=None)
    expected = (expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),)
    result = compare(expected, observed, fence(A, B))
    assert result["status"] == "pending"
    assert not result["matched"]


def test_group_topology_does_not_count_a_hidden_sibling_as_visible_second_window():
    observed = report(item(A), item(B))
    observed["items"][1]["visible"] = False
    expected = (expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),)
    result = compare(expected, observed, fence(A, B))
    assert result["status"] != "confirmed"


def test_command_receipt_must_name_the_actual_committed_property_version():
    observed = report()
    observed["commandResults"][0]["committedPropertyVersions"]["zoom"] = 3
    result = compare(observed=observed)
    assert result["status"] != "confirmed"


def test_unsupported_closed_predicate_is_admission_failure_not_silent_noop():
    expected = (expect("execute_javascript", value={"script": "return true"}),)
    result = compare(expected)
    assert result["status"] == "failed"
    assert result["error"]["boundary"] == "admission"
    assert result["error"]["code"] == "CAPABILITY_UNSUPPORTED"


@pytest.mark.parametrize("bad_rect", [[20, 30, 0, 260], [20, 30, -1, 260], [20, 30, float("nan"), 260]],
                         ids=["zero", "negative", "nonfinite"])
def test_unusable_observed_geometry_never_confirms_requested_window(bad_rect):
    observed = report(item(rect=bad_rect))
    expected = (expect("rect", value={"rect": [20, 30, 320, 260]}),)
    result = compare(expected, observed)
    assert result["status"] == "failed"
    assert "GEOMETRY_MISMATCH" in codes(result)


def outcome(aid=ACTION, *, status="confirmed", ordinal=1, error=None, depends=()):
    row = {
        "requestId": REQUEST, "actionId": aid, "round": 1, "plannedStep": ordinal,
        "operation": "open", "dependsOn": list(depends), "status": status,
        "lastPhase": "drawn" if status == "confirmed" else "dispatched",
        "completionScope": "workspace", "revision": 1, "error": error,
        "sourceRefs": [source()], "presentationIds": [A], "facts": [],
        "confirmation": None,
    }
    if status == "confirmed":
        # Positive projection fixtures need actual predicate proof, not a fabricated
        # confirmed label. Preserve the original open/partial/fallback behaviors.
        expected = picture_expectations() + (expect("open", value={"value": True}),)
        observed, command = report(), fence()
        command["actionId"] = aid
        observed["commandResults"][0]["actionId"] = aid
        row.update(expected=deepcopy(expected), commandIds=[COMMAND],
                   confirmation=compare(expected, observed, command))
        assert row["confirmation"]["status"] == "confirmed"
    return row


def failure(aid, boundary="source", code="SOURCE_MISSING", cause=None):
    return {"actionId": aid, "boundary": boundary, "code": code,
            "causeActionId": cause, "reason": "The second saved image is not available.",
            "next": "Choose another retained image.", "retryable": False}


@pytest.mark.parametrize("terminal", ["canceled", "timed_out"], ids=["canceled", "timed_out"])
def test_late_draw_cannot_rewrite_terminal_cancellation_or_timeout(terminal):
    before = outcome(status=terminal, error=failure(ACTION, "observe", "CONFIRM_TIMEOUT"))
    original = deepcopy(before)
    observed = compare()
    after = core().observe_late(before, observed, at=105.0)
    assert after["status"] == terminal
    assert after["error"] == original["error"]
    assert after["lateObservation"]["status"] == "confirmed"
    assert before == original


def test_pending_outcome_can_advance_on_its_correlated_draw():
    before = outcome(status="pending")
    after = core().observe_late(before, compare(), at=102.0)
    assert after["status"] == "confirmed"
    assert before["status"] == "pending"


def test_late_ack_for_another_action_cannot_advance_pending_outcome():
    before = outcome(status="pending")
    other = compare()
    other["actionId"] = REQUEST + ":a2"
    after = core().observe_late(before, other, at=102.0)
    assert after["status"] == "pending"
    assert after.get("lateObservation") is None


def test_first_failure_is_dependency_cause_not_earliest_arriving_error():
    missing = REQUEST + ":a2"
    blocked = REQUEST + ":a3"
    independent = REQUEST + ":a4"
    rows = (
        outcome(independent, status="failed", ordinal=4, error=failure(independent, "execute", "TOOL_FAILED")),
        outcome(blocked, status="blocked", ordinal=3, depends=(missing,),
                error=failure(blocked, "admission", "PREREQUISITE_FAILED", missing)),
        outcome(ACTION),
        outcome(missing, status="failed", ordinal=2, error=failure(missing)),
    )
    selected = core().first_failed_boundary(rows)
    assert selected["actionId"] == missing
    assert selected["boundary"] == "source"
    assert selected["code"] == "SOURCE_MISSING"


def test_blocked_child_points_to_actual_cause_even_when_numbered_first():
    cause = REQUEST + ":a2"
    rows = (
        outcome(ACTION, status="blocked", ordinal=1, depends=(cause,),
                error=failure(ACTION, "admission", "PREREQUISITE_FAILED", cause)),
        outcome(cause, status="failed", ordinal=2, error=failure(cause, "load", "MEDIA_DECODE_FAILED")),
    )
    assert core().first_failed_boundary(rows)["actionId"] == cause


def test_harmless_duplicate_diagnostic_is_not_first_failed_boundary():
    row = outcome(error=failure(ACTION, "lifecycle", "DUPLICATE_IGNORED"))
    assert core().first_failed_boundary((row,)) is None


def test_projection_keeps_partial_failure_with_bounded_success_facts():
    rows = []
    for n in range(1, 6):
        row = outcome(REQUEST + f":a{n}", ordinal=n)
        row["facts"] = [{"id": f"fact-{n}", "kind": "opened", "scope": "current",
                         "actionId": row["actionId"], "sourceRefs": [source()],
                         "presentationIds": [A], "text": f"Close-up {n} is open in its own window."}]
        row["summary"] = "RAW INTERNAL RECEIPT SECRET_DO_NOT_PROJECT"
        rows.append(row)
    broken = outcome(REQUEST + ":a6", status="failed", ordinal=6,
                     error=failure(REQUEST + ":a6", "load", "MEDIA_DECODE_FAILED"))
    broken["facts"] = [{"id": "wrong", "kind": "opened", "text": "Everything is done."}]
    rows.append(broken)
    projected = core().supported_facts(tuple(rows), report(), max_facts=3)
    assert 0 < len(projected) <= 3
    assert any(f["kind"] == "failure" and f["actionId"] == REQUEST + ":a6" for f in projected)
    assert any(f["kind"] == "opened" for f in projected)
    assert all("SECRET_DO_NOT_PROJECT" not in str(f) and "Everything is done" not in str(f)
               for f in projected)


def test_projection_downgrades_current_claim_when_source_has_changed():
    row = outcome()
    row["facts"] = [{"id": "shown", "kind": "opened", "scope": "current",
                     "actionId": ACTION, "sourceRefs": [source()], "presentationIds": [A],
                     "text": "The first close-up is open."}]
    current = report(item(bound_source=source("saved-b", member="c18", capture="capture-322")))
    projected = core().supported_facts((row,), current, max_facts=3)
    assert projected
    assert not any(f["kind"] == "opened" and f.get("scope") == "current" for f in projected)
    assert any(f.get("scope") == "historical" or f["kind"] == "limitation" for f in projected)


def test_compute_success_without_display_confirmation_is_not_opened_fact():
    row = outcome(status="unobserved")
    row["completionScope"] = "compute"
    row["confirmation"] = None
    row["facts"] = [{"id": "false-done", "kind": "opened", "scope": "current",
                     "actionId": ACTION, "sourceRefs": [source()], "presentationIds": [A]}]
    projected = core().supported_facts((row,), None, max_facts=3)
    assert projected
    assert not any(f["kind"] in ("opened", "drawn", "arranged") for f in projected)


def test_pending_and_canceled_work_are_useful_facts_not_completed_claims():
    rows = (outcome(status="pending"), outcome(REQUEST + ":a2", status="canceled", ordinal=2))
    projected = core().supported_facts(rows, None, max_facts=3)
    assert any(f["kind"] == "pending" for f in projected)
    assert any(f["kind"] == "limitation" and f["actionId"] == REQUEST + ":a2" for f in projected)
    assert not any(f["kind"] in ("opened", "drawn", "arranged") for f in projected)


def test_source_backed_data_read_needs_no_studio_or_display_claim():
    row = outcome(status="data_complete")
    row.update(completionScope="data_read", lastPhase="data_verified", confirmation=None)
    row["facts"] = [{"id": "note-read", "kind": "read", "scope": "data",
                     "actionId": ACTION, "sourceRefs": [source()], "presentationIds": [],
                     "text": "Both note versions refer to the same retained capture."}]
    projected = core().supported_facts((row,), None, max_facts=3)
    assert any(f["kind"] == "read" and f["scope"] == "data" for f in projected)
    assert not any(f["kind"] in ("opened", "drawn", "arranged") for f in projected)


def test_confirming_and_projecting_does_not_mutate_input_or_alias_observation():
    observed = report()
    command = fence()
    expected = picture_expectations()
    original = deepcopy((observed, command, expected))
    result = compare(expected, observed, command)
    assert (observed, command, expected) == original
    observed["items"][0]["sourceRef"]["identity"]["member"] = "c999"
    assert result["observed"][0]["sourceRef"]["identity"]["member"] == "c17"


# Peer-review regressions: same public functions, synthetic evidence only.
def proven_workspace_outcome(expected, observed, command, *, kind, targets=(A,)):
    confirmation = compare(expected, observed, command)
    assert confirmation["status"] == "confirmed"
    sources = [deepcopy(i["sourceRef"]) for i in observed["items"] if i["id"] in targets]
    if not sources:  # Complete absence is still tied to an admitted source.
        sources = [deepcopy(command["targets"][pid]["sourceRef"]) for pid in targets]
    return {
        "requestId": REQUEST, "actionId": ACTION, "commandIds": [COMMAND],
        "operation": kind, "status": "confirmed", "completionScope": "workspace",
        "revision": 1, "lastPhase": "drawn", "error": None,
        "expected": deepcopy(expected), "confirmation": confirmation,
        "sourceRefs": sources, "presentationIds": list(targets),
        "facts": [{"kind": kind, "actionId": ACTION, "sourceRefs": deepcopy(sources),
                   "presentationIds": list(targets),
                   "predicateIds": [e["id"] for e in expected]}],
    }


def arrangement_proof():
    observed = report(item(A), item(B, rect=[400, 30, 320, 260]))
    observed["items"][1]["contentRect"] = [408, 60, 304, 210]
    expected = []
    for pid in (A, B):
        for condition in picture_expectations(pid):
            condition["id"] = pid + ":" + condition["id"]
            expected.append(condition)
    expected.extend((
        expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),
        expect("relative_geometry", (A, B), {"relation": "left", "gap": 10}),
        expect("content_size", (A, B), {"minWidth": 240, "minHeight": 180}),
        expect("control", (A, B), {"each": ["move", "resize", "close", "zoom"], "independent": True}),
    ))
    return expected, observed, fence(A, B)


@pytest.mark.parametrize("media", ["capture", "labelled", "reference"], ids=["capture", "labelled", "reference"])
def test_p5_media_words_confirm_without_reclassification(media):
    retained = source()
    retained["media"] = media
    observed = report(item(bound_source=retained))
    command = fence()
    command["targets"][A]["sourceRef"] = deepcopy(retained)
    result = compare(picture_expectations(bound_source=retained), observed, command)
    assert result["status"] == "confirmed"
    assert result["observed"][0]["sourceRef"]["media"] == media


@pytest.mark.parametrize("media", ["capture", "labelled", "reference"], ids=["capture", "labelled", "reference"])
def test_p5_media_words_project_retained_data_without_studio(media):
    retained = source()
    retained["media"] = media
    row = outcome(status="data_complete")
    row.update(completionScope="data_read", sourceRefs=[deepcopy(retained)])
    row["facts"] = [{"kind": "read", "actionId": ACTION, "sourceRefs": [deepcopy(retained)],
                     "presentationIds": [], "text": "The exact retained source was read."}]
    facts = core().supported_facts((row,), None)
    assert any(f["kind"] == "read" and f["sourceRefs"][0]["media"] == media for f in facts)


def composite_source():
    parent = source("comparison", member="d1")
    parent.update(kind="composite", media="derived")
    nested = source("nested-comparison", member="d2")
    nested.update(kind="composite", media="derived")
    nested["components"] = [source("child-b", member="c18"), source("child-c", member="c19")]
    parent["components"] = [source("child-a"), nested]
    return parent


@pytest.mark.parametrize("change", ["nested_revision", "nested_producer", "order", "missing_child"],
                         ids=["nested_revision", "nested_producer", "order", "missing_child"])
def test_identical_composite_bytes_do_not_replace_ordered_recursive_child_identity(change):
    wanted = composite_source()
    actual = deepcopy(wanted)
    if change == "nested_revision":
        actual["components"][1]["components"][0]["identity"]["revision"] = 1
    elif change == "nested_producer":
        actual["components"][1]["components"][0]["provenance"]["run"] = "replacement-producer-run"
    elif change == "order":
        actual["components"].reverse()
    else:
        actual["components"][1]["components"].pop()
    result = compare(picture_expectations(bound_source=wanted), report(item(bound_source=actual)))
    assert result["status"] == "failed"
    assert "SOURCE_MISMATCH" in codes(result)
    row = outcome(status="data_complete")
    row.update(completionScope="data_read", sourceRefs=[wanted])
    row["facts"] = [{"kind": "read", "actionId": ACTION, "sourceRefs": [actual], "presentationIds": []}]
    assert not any(f["kind"] == "read" for f in core().supported_facts((row,), None))


def test_recursive_composite_locator_renewal_preserves_exact_source_and_data_projection():
    wanted = composite_source()
    wanted["locator"] = "old-parent-url"
    wanted["components"][1]["components"][0]["locator"] = "old-child-url"
    actual = deepcopy(wanted)
    actual["locator"] = "renewed-parent-url"
    actual["components"][1]["components"][0]["locator"] = "renewed-child-url"
    actual["availability"] = "temporary"
    assert compare(picture_expectations(bound_source=wanted), report(item(bound_source=actual)))["status"] == "confirmed"
    row = outcome(status="data_complete")
    row.update(completionScope="data_read", sourceRefs=[wanted])
    row["facts"] = [{"kind": "read", "actionId": ACTION, "sourceRefs": [actual], "presentationIds": []}]
    assert any(f["kind"] == "read" for f in core().supported_facts((row,), None))


@pytest.mark.parametrize("kind", ["closed", "arranged"], ids=["closed", "arranged"])
def test_picture_confirmation_cannot_support_unproved_completion_kind(kind):
    expected, observed, command = picture_expectations(), report(), fence()
    row = proven_workspace_outcome(expected, observed, command, kind=kind)
    facts = core().supported_facts((row,), observed)
    assert facts
    assert not any(f["kind"] == kind for f in facts)
    assert any(f["kind"] in ("drawn", "opened", "limitation") for f in facts)


def test_actual_open_predicate_supports_narrow_current_open_fact():
    expected = picture_expectations() + (expect("open", value={"value": True}),)
    observed = report()
    row = proven_workspace_outcome(expected, observed, fence(), kind="opened")
    assert any(f["kind"] == "opened" and f["scope"] == "current"
               for f in core().supported_facts((row,), observed))


def test_actual_closed_predicate_supports_closed_fact_without_an_open_picture():
    closed = item()
    closed.update(open=False, visible=False, connected=False, phase="closed", drawRef=None)
    observed = report(closed)
    observed["commandResults"][0].update(phase="closed", drawRef=None)
    expected = (expect("closed", value={"value": True}),)
    row = proven_workspace_outcome(expected, observed, fence(), kind="closed")
    facts = core().supported_facts((row,), observed)
    assert any(f["kind"] == "closed" for f in facts)
    assert not any(f["kind"] in ("opened", "drawn", "arranged") for f in facts)


def test_matched_arrangement_predicates_support_current_arranged_fact():
    expected, observed, command = arrangement_proof()
    row = proven_workspace_outcome(expected, observed, command, kind="arranged", targets=(A, B))
    assert any(f["kind"] == "arranged" and f["scope"] == "current"
               for f in core().supported_facts((row,), observed))


@pytest.mark.parametrize("change", ["clamped_overlap", "shared_root", "disabled_control", "small_content"],
                         ids=["clamped_overlap", "shared_root", "disabled_control", "small_content"])
def test_current_arrangement_rechecks_actual_geometry_topology_and_controls(change):
    expected, observed, command = arrangement_proof()
    row = proven_workspace_outcome(expected, observed, command, kind="arranged", targets=(A, B))
    current = deepcopy(observed)
    current.update(seq=22, receivedAtMono=102.0)
    second = current["items"][1]
    if change == "clamped_overlap":
        current["viewport"]["layout"] = [500, 900]
        second["rect"] = [100, 30, 320, 260]
        second["contentRect"] = [108, 60, 304, 210]
    elif change == "shared_root":
        second["rootId"] = current["items"][0]["rootId"]
    elif change == "disabled_control":
        second["controls"]["zoom"]["enabled"] = False
    else:
        second["contentRect"] = [408, 60, 70, 50]
    facts = core().supported_facts((row,), current)
    assert facts
    assert not any(f["kind"] == "arranged" and f["scope"] == "current" for f in facts)
    assert any(f.get("scope") == "historical" or f["kind"] == "limitation" for f in facts)


@pytest.mark.parametrize("order", [(A, B), (B, A)], ids=["pending_first", "failed_first"])
@pytest.mark.parametrize("now", [101.0, 105.0], ids=["before_deadline", "after_deadline"])
def test_group_pending_sibling_never_hides_explicit_load_failure(order, now):
    observed = report(item(A), item(B))
    observed["items"][0].update(phase="loaded", drawRef=None)
    observed["commandResults"][0].update(phase="loaded", drawRef=None)
    error = {"code": "MEDIA_LOAD_FAILED", "reason": "The second exact source returned 404."}
    observed["items"][1].update(phase="error", drawRef=None, error=deepcopy(error))
    observed["commandResults"][1].update(phase="error", drawRef=None, error=deepcopy(error))
    expected = (
        expect("source_exact", (A,), {"sourceRef": source()}, phase="loaded", key="retained_a"),
        expect("independent_windows", order, {"count": 2, "distinctRoots": True}),
    )
    result = compare(expected, observed, fence(A, B), now=now)
    assert result["status"] == "failed"
    assert result["error"]["boundary"] == "load"
    assert result["error"]["code"] == "MEDIA_LOAD_FAILED"
    assert "MEDIA_LOAD_FAILED" in codes(result)
    assert "retained_a" in result["matched"]
    assert {i["id"]: i["phase"] for i in result["observed"]} == {A: "loaded", B: "error"}


@pytest.mark.parametrize("drawn", [False, True], ids=["both_pending", "both_drawn"])
def test_group_readiness_positive_controls_remain_pending_or_confirmed(drawn):
    observed = report(item(A), item(B))
    if not drawn:
        for values in observed["items"] + observed["commandResults"]:
            values.update(phase="loaded", drawRef=None)
    expected = (expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),)
    result = compare(expected, observed, fence(A, B))
    assert result["status"] == ("confirmed" if drawn else "pending")


def split_removal_report(*, complete):
    observed = report(item(A), item(B))
    observed.update(complete=complete, seq=22, receivedAtMono=102.0)
    historical_close = {**deepcopy(observed["commandResults"][0]), "presentationId": PARENT,
                        "phase": "closed", "drawRef": None, "reportSeq": 21}
    observed["commandResults"].append(historical_close)
    expected = (
        expect("independent_windows", (A, B), {"count": 2, "distinctRoots": True}),
        expect("closed", (PARENT,), {"value": True}, key="parent_retired"),
    )
    return expected, observed, fence(A, B, PARENT)


@pytest.mark.parametrize("parent_present", [False, True], ids=["omitted_reopened_parent", "visible_reopened_parent"])
def test_historical_close_receipt_does_not_certify_reopened_or_omitted_parent(parent_present):
    expected, observed, command = split_removal_report(complete=False)
    if parent_present:
        reopened = item(PARENT)
        reopened["propertyVersions"]["open"] = 5
        reopened["propertyOwners"]["open"] = "user"
        observed["items"].append(reopened)
    else:
        observed["omitted"] = [PARENT]
    result = compare(expected, observed, command, now=102.0)
    assert result["status"] != "confirmed"
    assert "parent_retired" not in result["matched"]
    assert result["error"]["code"] in ("REPORT_STALE", "OWNERSHIP_STALE", "TOPOLOGY_MISMATCH")


@pytest.mark.parametrize("proof", ["closed_item", "current_removal", "complete_absence"],
                         ids=["closed_item", "current_removal", "complete_absence"])
def test_current_parent_closure_or_complete_absence_can_confirm_split(proof):
    expected, observed, command = split_removal_report(complete=proof == "complete_absence")
    if proof == "closed_item":
        closed = item(PARENT)
        closed.update(open=False, visible=False, connected=False, phase="closed", drawRef=None)
        observed["items"].append(closed)
    elif proof == "current_removal":
        # Existing commandResults is the normalized explicit removal/tombstone seam.
        # These observed versions are this report's facts, unlike the historical ack.
        removal = observed["commandResults"][-1]
        removal.update(reportSeq=22, observedPropertyVersions={"source": 4, "open": 4, "rect": 4, "zoom": 4},
                       observedPropertyOwners={p: "request:" + REQUEST for p in ("source", "open", "rect", "zoom")})
    result = compare(expected, observed, command, now=102.0)
    assert result["status"] == "confirmed"
    assert "parent_retired" in result["matched"]


@pytest.mark.parametrize("change", ["stale_removal_sequence", "user_open_revision"],
                         ids=["stale_removal_sequence", "user_open_revision"])
def test_explicit_parent_removal_must_observe_current_owned_properties(change):
    expected, observed, command = split_removal_report(complete=False)
    removal = observed["commandResults"][-1]
    removal.update(reportSeq=22, observedPropertyVersions={"source": 4, "open": 4, "rect": 4, "zoom": 4},
                   observedPropertyOwners={p: "request:" + REQUEST for p in ("source", "open", "rect", "zoom")})
    if change == "stale_removal_sequence":
        removal["reportSeq"] = 21
    else:
        removal["observedPropertyVersions"]["open"] = 5
        removal["observedPropertyOwners"]["open"] = "user"
    result = compare(expected, observed, command, now=102.0)
    assert result["status"] != "confirmed"
    assert "parent_retired" not in result["matched"]
    assert result["error"]["code"] in ("REPORT_STALE", "OWNERSHIP_STALE")


@pytest.mark.parametrize("status", ["pending", "canceled", "timed_out"], ids=["pending", "canceled", "timed_out"])
def test_older_same_command_late_report_cannot_erase_newer_user_ownership(status):
    newer_report = report()
    newer_report.update(seq=22, receivedAtMono=102.0)
    newer_report["items"][0]["propertyVersions"]["zoom"] = 5
    newer_report["items"][0]["propertyOwners"]["zoom"] = "user"
    newer = compare(observed=newer_report, now=102.0)
    assert newer["error"]["code"] == "OWNERSHIP_STALE"
    terminal_code = "REQUEST_CANCELED" if status == "canceled" else "CONFIRM_TIMEOUT"
    original_error = failure(ACTION, "observe", terminal_code) if status != "pending" else None
    before = outcome(status=status, error=original_error)
    before["commandIds"] = [COMMAND]
    newest = core().observe_late(before, newer, at=102.5)
    old = compare()
    after = core().observe_late(newest, old, at=103.0)
    assert after == newest
    retained = after.get("lateObservation") if status != "pending" else after["confirmation"]
    assert retained["report"]["seq"] == 22
    assert retained["error"]["code"] == "OWNERSHIP_STALE"
    if status != "pending":
        assert after["status"] == status and after["error"] == original_error


def test_late_report_replay_does_not_replace_first_observation_or_advance_revision():
    before = outcome(status="canceled")
    before["commandIds"] = [COMMAND]
    first = core().observe_late(before, compare(), at=102.0)
    replay = core().observe_late(first, compare(), at=103.0)
    assert replay == first


def test_same_report_sequence_is_replay_despite_changed_confirmation_revision():
    before = outcome(status="canceled")
    before["commandIds"] = [COMMAND]
    observed = compare()
    first = core().observe_late(before, observed, at=102.0)
    replay = deepcopy(observed)
    replay["revision"] = 99
    assert core().observe_late(first, replay, at=103.0) == first


def test_newer_same_command_draw_advances_pending_evidence_in_sequence_order():
    loaded = report()
    loaded["items"][0].update(phase="loaded", drawRef=None)
    loaded["commandResults"][0].update(phase="loaded", drawRef=None)
    first = core().observe_late(outcome(status="pending"), compare(observed=loaded), at=101.5)
    drawn = report()
    drawn.update(seq=22, receivedAtMono=102.0)
    after = core().observe_late(first, compare(observed=drawn, now=102.0), at=102.5)
    assert after["status"] == "confirmed"
    assert after["confirmation"]["report"]["seq"] == 22


@pytest.mark.parametrize("field,other", [("connectionEpoch", "socket-other"), ("clientEpoch", "page-other")],
                         ids=["socket", "page"])
def test_incomparable_origin_cannot_replace_retained_late_observation(field, other):
    before = outcome(status="canceled")
    before["commandIds"] = [COMMAND]
    newest = core().observe_late(before, compare(), at=102.0)
    foreign = report()
    foreign.update(seq=22, receivedAtMono=102.5)
    foreign["client"][field] = other
    rejected = compare(observed=foreign, now=102.5)
    assert rejected["status"] == "unobserved"
    after = core().observe_late(newest, rejected, at=103.0)
    assert after["lateObservation"] == newest["lateObservation"]
    assert after["status"] == "canceled"


@pytest.mark.parametrize("value,status", [(1.0, "confirmed"), (1.0000005, "confirmed"),
                                          (1.000001, "confirmed"), (1.0000011, "failed")],
                         ids=["exact", "within_tolerance", "at_tolerance", "beyond_tolerance"])
def test_numeric_control_value_uses_frozen_one_millionth_tolerance(value, status):
    observed = report()
    observed["items"][0]["zoom"] = value
    expected = (expect("control", value={"each": ["zoom"], "value": 1.0}),)
    result = compare(expected, observed)
    assert result["status"] == status
    if status == "failed":
        assert "CONTROL_UNAVAILABLE" in codes(result)


# Contract recheck: row-level source membership is not target/source evidence.
def two_source_proof(mode):
    sources = {A: source("saved-a", member="c17"), B: source("saved-b", member="c18")}
    observed = report(item(A, bound_source=sources[A]), item(B, bound_source=sources[B], rect=[400, 30, 320, 260]))
    observed["items"][1]["contentRect"] = [408, 60, 304, 210]
    command = fence(A, B)
    expected = []
    for pid in (A, B):
        if mode == "source_exact":
            conditions = picture_expectations(pid, sources[pid])
        elif mode == "geometry":
            command["targets"][pid]["sourceRef"] = deepcopy(sources[pid])
            conditions = (
                expect("rect", (pid,), {"rect": [20, 30, 320, 260] if pid == A else [400, 30, 320, 260]}),
                expect("visible", (pid,), {"value": True}),
            )
        else:
            command["targets"][pid]["sourceRef"] = deepcopy(sources[pid])
            conditions = (expect("closed", (pid,), {"value": True}),)
        for condition in conditions:
            condition["id"] = pid + ":" + condition["id"]
            expected.append(condition)
    if mode == "close":
        observed["items"] = []  # Only exact frozen lease identities survive closure.
        for ack in observed["commandResults"]:
            ack.update(phase="closed", drawRef=None)
    kind = {"source_exact": "drawn", "geometry": "arranged", "close": "closed"}[mode]
    row = proven_workspace_outcome(expected, observed, command, kind=kind, targets=(A, B))
    # Deliberately reverse broad row source order. Association is keyed by exact
    # target proof, never the row/DOM list's position, titles or equal JPEG hashes.
    row["sourceRefs"] = [deepcopy(sources[B]), deepcopy(sources[A])]
    return row, observed, sources


def target_fact(row, targets, sources):
    return {"kind": row["operation"], "actionId": ACTION,
            "presentationIds": list(targets), "sourceRefs": deepcopy(list(sources)),
            "predicateIds": [e["id"] for e in row["expected"]]}


@pytest.mark.parametrize("mode", ["source_exact", "geometry", "close"], ids=["source_exact", "geometry_lease", "close_lease"])
@pytest.mark.parametrize("current", [False, True], ids=["no_current_report", "current_report"])
def test_sibling_source_cannot_be_attributed_to_another_proven_target(mode, current):
    row, observed, sources = two_source_proof(mode)
    row["facts"] = [target_fact(row, (A,), (sources[B],))]
    projected = core().supported_facts((row,), observed if current else None)
    assert projected
    for fact in projected:
        if fact["kind"] in ("opened", "drawn", "arranged", "closed"):
            # A correctly narrowed fallback may recover A/S1; it cannot invent
            # A/S2 history, or silently substitute the valid sibling B/S2.
            assert fact["presentationIds"] == [A]
            assert fact["sourceRefs"] == [sources[A]]


@pytest.mark.parametrize("mode", ["source_exact", "geometry", "close"], ids=["source_exact", "geometry_lease", "close_lease"])
@pytest.mark.parametrize("state", ["current", "no_current", "source_changed"], ids=["current", "no_current", "source_changed"])
def test_genuine_target_source_proof_survives_as_current_or_historical_fact(mode, state):
    row, observed, sources = two_source_proof(mode)
    row["facts"] = [target_fact(row, (A,), (sources[A],))]
    current = deepcopy(observed)
    if state == "no_current":
        current = None
    elif state == "source_changed":
        replacement = item(A, bound_source=source("saved-new", member="c99"))
        replacement.update(sourceGeneration=3, lifetimeGeneration="new-source-lifetime")
        replacement["propertyVersions"]["source"] = 5
        replacement["propertyOwners"]["source"] = "user"
        current.update(seq=22, receivedAtMono=102.0)
        current["items"] = [replacement] + [i for i in current["items"] if i["id"] != A]
    projected = core().supported_facts((row,), current)
    genuine = [f for f in projected if f["kind"] == row["operation"]]
    assert len(genuine) == 1
    assert genuine[0]["presentationIds"] == [A]
    assert genuine[0]["sourceRefs"] == [sources[A]]
    assert genuine[0]["scope"] == ("current" if state == "current" else "historical")


@pytest.mark.parametrize("mode", ["source_exact", "geometry", "close"], ids=["source_exact", "geometry_lease", "close_lease"])
@pytest.mark.parametrize("scope", ["sibling", "compound"], ids=["sibling", "compound"])
def test_valid_sibling_and_compound_source_scopes_do_not_depend_on_row_order(mode, scope):
    row, observed, sources = two_source_proof(mode)
    targets = (B,) if scope == "sibling" else (A, B)
    supplied_sources = (sources[B],) if scope == "sibling" else (sources[B], sources[A])
    row["facts"] = [target_fact(row, targets, supplied_sources)]
    projected = core().supported_facts((row,), observed)
    genuine = [f for f in projected if f["kind"] == row["operation"]]
    assert len(genuine) == 1
    assert set(genuine[0]["presentationIds"]) == set(targets)
    assert {s["key"] for s in genuine[0]["sourceRefs"]} == {s["key"] for s in supplied_sources}
    assert all(s in supplied_sources for s in genuine[0]["sourceRefs"])
    assert genuine[0]["scope"] == "current"


@pytest.mark.parametrize("mode", ["geometry", "close"], ids=["geometry", "close"])
@pytest.mark.parametrize("current", [False, True], ids=["no_current_report", "current_report"])
def test_missing_target_source_association_is_a_limitation_not_an_absent_capability(mode, current):
    row, observed, sources = two_source_proof(mode)
    command = fence(A, B)  # No admitted SourceRefs, unlike the valid lease controls.
    unattributed = deepcopy(observed)
    for shown in unattributed["items"]:
        shown.pop("sourceRef")
    result = compare(row["expected"], unattributed, command)
    assert result["status"] == "confirmed"  # Geometry/close itself happened.
    row["confirmation"] = result
    row["facts"] = [target_fact(row, (A,), (sources[A],))]
    projected = core().supported_facts((row,), unattributed if current else None)
    assert projected
    assert not any(f["kind"] in ("opened", "drawn", "arranged", "closed") for f in projected)
    assert any(f["kind"] == "limitation" for f in projected)
    assert all(f.get("code") != "CAPABILITY_UNSUPPORTED" and "unsupported in this build" not in f["text"].lower()
               for f in projected)


@pytest.mark.parametrize("scope", ["extraneous_sibling_source", "missing_target_source"],
                         ids=["extraneous_sibling_source", "missing_target_source"])
def test_compound_fact_cannot_add_unrelated_sources_or_leave_target_source_unproved(scope):
    row, _, sources = two_source_proof("source_exact")
    if scope == "extraneous_sibling_source":
        row["facts"] = [target_fact(row, (A,), (sources[A], sources[B]))]
    else:
        row["facts"] = [target_fact(row, (A, B), (sources[B],))]
    projected = core().supported_facts((row,), None)
    assert projected
    for fact in projected:
        if fact["kind"] in ("opened", "drawn", "arranged", "closed"):
            assert set(fact["presentationIds"]).issubset(row["facts"][0]["presentationIds"])
            justified = [sources[pid] for pid in fact["presentationIds"]]
            assert all(s in justified for s in fact["sourceRefs"])
            assert all(s in fact["sourceRefs"] for s in justified)


# Next slice: proposed closed observation fields are documented in the handoff.
# These are synthetic normalized renderer/server facts, not desired assignments.
def observed_view_effect(mode):
    sources = {A: source("saved-a", member="c17"), B: source("saved-b", member="c18")}
    observed = report(item(A, bound_source=sources[A]), item(B, bound_source=sources[B]))
    command = fence(A, B)
    expected = []
    for shown in observed["items"]:
        pid = shown["id"]
        shown.update(view={"zoom": 3.2 if pid == A else 1.0, "pan": [0.25, 0.75] if pid == A else [0.5, 0.5]},
                     zoom=3.2 if pid == A else 1.0, pan=[0.25, 0.75] if pid == A else [0.5, 0.5],
                     z=8 if pid == A else 5, viewDrawRef="paint-view-4-" + pid)
        shown["propertyVersions"].update(pan=4, front=4)
        shown["propertyOwners"].update(pan="request:" + REQUEST, front="request:" + REQUEST)
        for name in ("zoom", "pan", "front"):
            shown["controls"][name] = {"id": pid + ":" + name, "role": name, "target": pid,
                                       "present": True, "enabled": True, "editable": False, "stateRevision": 4}
        shown["controls"]["zoom"]["observedValue"] = shown["view"]["zoom"]
        shown["controls"]["front"]["observedValue"] = shown["z"]
        touched = ("source", "open", mode) if pid == A else ("source", "open")
        lease = command["targets"][pid]
        lease.update(sourceRef=deepcopy(sources[pid]), baseViewDrawRef="paint-view-3-" + pid,
                     basePropertyVersions={p: 3 for p in touched},
                     committedPropertyVersions={p: 4 for p in touched})
        for condition in picture_expectations(pid, sources[pid]):
            condition["id"] = pid + ":" + condition["id"]
            expected.append(condition)
    for ack in observed["commandResults"]:
        pid = ack["presentationId"]
        ack.update(appRunId="run-8", reportSeq=21, sourceKey=sources[pid]["key"], viewDrawRef="paint-view-4-" + pid,
                   committedPropertyVersions=deepcopy(command["targets"][pid]["committedPropertyVersions"]),
                   committedPropertyOwners={p: "request:" + REQUEST for p in command["targets"][pid]["committedPropertyVersions"]})
    value = {"zoom": {"value": 3.2}, "pan": {"center": [0.25, 0.75]}, "front": {"above": [B]}}[mode]
    expected.append(expect(mode, (A,), value, key="view-effect"))
    return tuple(expected), observed, command, sources


@pytest.mark.parametrize("mode", ["zoom", "pan", "front"])
def test_observed_independent_window_view_effect_confirms_its_original_occurrence(mode):
    expected, observed, command, _ = observed_view_effect(mode)
    before = deepcopy((expected, observed, command))
    result = compare(expected, observed, command)
    assert result["status"] == "confirmed"
    assert "view-effect" in result["matched"]
    assert (result["requestId"], result["actionId"], result["commandId"]) == (REQUEST, ACTION, COMMAND)
    assert (expected, observed, command) == before
    observed["items"][0]["view"]["pan"][0] = 0.9
    assert result["observed"][0]["view"]["pan"] == [0.25, 0.75]


@pytest.mark.parametrize("mode", ["zoom", "pan", "front"])
def test_unrelated_sibling_view_ownership_does_not_cancel_target_effect(mode):
    expected, observed, command, _ = observed_view_effect(mode)
    sibling = observed["items"][1]
    sibling["propertyVersions"].update(zoom=9, pan=9, front=9)
    sibling["propertyOwners"].update(zoom="user", pan="user", front="user")
    for name in ("zoom", "pan", "front"):
        sibling["controls"][name]["stateRevision"] = 9
    assert compare(expected, observed, command)["status"] == "confirmed"


@pytest.mark.parametrize("mode", ["zoom", "pan", "front"])
@pytest.mark.parametrize("change", [
    "assigned_only", "wrong_value", "wrong_source", "wrong_draw", "reused_draw", "no_view_draw", "old_report",
    "wrong_command", "wrong_action", "wrong_page", "wrong_connection", "wrong_run",
    "source_generation", "lifetime", "same_value_user_touch", "user_owner_only",
    "control_revision", "control_wrong_target", "unregistered_control", "disabled_control", "control_not_present",
    "editable_control", "ack_run", "ack_sequence", "ack_owner", "ack_source", "hidden",
])
def test_view_effect_cannot_confirm_desired_stale_or_unowned_renderer_state(mode, change):
    expected, observed, command, sources = observed_view_effect(mode)
    target, ack = observed["items"][0], observed["commandResults"][0]
    if change == "assigned_only":
        target["desiredValues"] = {"zoom": 3.2, "pan": [0.25, 0.75], "front": 999}
        target["view"][mode if mode != "front" else "zoom"] = None
        target["z" if mode == "front" else mode] = None
        target["controls"][mode].pop("observedValue", None)
    elif change == "wrong_value":
        if mode == "front":
            target["z"] = target["controls"][mode]["observedValue"] = 4
        else:
            target[mode] = target["view"][mode] = 1.0 if mode == "zoom" else [0.5, 0.5]
            if mode == "zoom":
                target["controls"][mode]["observedValue"] = 1.0
    elif change == "wrong_source":
        target["sourceRef"] = deepcopy(sources[B])  # Equal bytes, distinct producer.
    elif change == "wrong_draw":
        ack["viewDrawRef"] = "paint-from-another-view"
    elif change == "reused_draw":
        target["viewDrawRef"] = ack["viewDrawRef"] = command["targets"][A]["baseViewDrawRef"]
    elif change == "no_view_draw":
        target.pop("viewDrawRef")
        ack.pop("viewDrawRef")  # Loaded source pixels alone do not prove the new view.
    elif change == "old_report":
        observed["seq"] = 20
    elif change in ("wrong_command", "wrong_action"):
        ack["commandId" if change == "wrong_command" else "actionId"] = "another-occurrence"
    elif change in ("wrong_page", "wrong_connection"):
        observed["client"]["clientEpoch" if change == "wrong_page" else "connectionEpoch"] = "another-epoch"
    elif change == "wrong_run":
        observed["appRunId"] = "run-9"
    elif change in ("source_generation", "lifetime"):
        target["sourceGeneration" if change == "source_generation" else "lifetimeGeneration"] = "another-generation"
    elif change in ("same_value_user_touch", "user_owner_only"):
        target["propertyOwners"][mode] = "user"
        if change == "same_value_user_touch":
            target["propertyVersions"][mode] = 5
            target["controls"][mode]["stateRevision"] = 5
    elif change == "control_revision":
        target["controls"][mode]["stateRevision"] = 3
    elif change == "control_wrong_target":
        target["controls"][mode]["target"] = B
    elif change == "unregistered_control":
        target["controls"].pop(mode)
    elif change == "disabled_control":
        target["controls"][mode]["enabled"] = False
    elif change == "control_not_present":
        target["controls"][mode]["present"] = None
    elif change == "editable_control":
        target["controls"][mode]["editable"] = True
    elif change == "ack_run":
        ack["appRunId"] = "run-9"
    elif change == "ack_sequence":
        ack["reportSeq"] = 20
    elif change == "ack_owner":
        ack["committedPropertyOwners"][mode] = "user"
    elif change == "ack_source":
        ack["sourceKey"] = sources[B]["key"]
    else:
        target["visible"] = False
    result = compare(expected, observed, command)
    assert result["status"] != "confirmed"
    assert "CAPABILITY_UNSUPPORTED" not in codes(result)
    assert (result.get("error") or {}).get("code") != "CAPABILITY_UNSUPPORTED"


@pytest.mark.parametrize("mode", ["zoom", "pan", "front"])
def test_same_valued_noop_still_needs_and_accepts_fresh_causal_observed_view_proof(mode):
    expected, observed, command, _ = observed_view_effect(mode)
    command["targets"][A]["baseObservedView"] = {"zoom": 3.2, "pan": [0.25, 0.75], "z": 8}
    assert compare(expected, observed, command)["status"] == "confirmed"


@pytest.mark.parametrize("mode", ["zoom", "pan", "front"])
@pytest.mark.parametrize("unknown", [None, True, "3.2"], ids=["missing", "boolean", "string"])
def test_unknown_actual_view_values_are_unverified_not_missing_capabilities(mode, unknown):
    expected, observed, command, _ = observed_view_effect(mode)
    target = observed["items"][0]
    target["z" if mode == "front" else mode] = unknown
    if mode != "front":
        target["view"][mode] = unknown
    if mode != "pan":
        target["controls"][mode]["observedValue"] = unknown
    result = compare(expected, observed, command)
    assert result["status"] != "confirmed"
    assert (result.get("error") or {}).get("code") != "CAPABILITY_UNSUPPORTED"


@pytest.mark.parametrize("mode", ["zoom", "front"])
def test_registered_scalar_control_cannot_disagree_with_the_observed_drawn_view(mode):
    expected, observed, command, _ = observed_view_effect(mode)
    observed["items"][0]["controls"][mode]["observedValue"] = 1.0
    result = compare(expected, observed, command)
    assert result["status"] != "confirmed"
    assert (result.get("error") or {}).get("code") != "CAPABILITY_UNSUPPORTED"


@pytest.mark.parametrize("mode", ["zoom", "pan"])
@pytest.mark.parametrize("delta,status", [(0.0000005, "confirmed"), (0.0000011, "failed")],
                         ids=["within_tolerance", "beyond_tolerance"])
def test_observed_zoom_and_pan_use_the_frozen_numeric_tolerance(mode, delta, status):
    expected, observed, command, _ = observed_view_effect(mode)
    target = observed["items"][0]
    if mode == "zoom":
        target["zoom"] = target["view"]["zoom"] = target["controls"]["zoom"]["observedValue"] = 3.2 + delta
    else:
        target["pan"] = target["view"]["pan"] = [0.25 + delta, 0.75]
    assert compare(expected, observed, command)["status"] == status


@pytest.mark.parametrize("change", ["tie", "other_window_above", "incomplete_scope", "unknown_sibling_order"])
def test_front_requires_actual_foreground_order_in_a_complete_registered_workspace(change):
    expected, observed, command, _ = observed_view_effect("front")
    if change == "tie":
        observed["items"][1]["z"] = 8
    elif change == "other_window_above":
        other = item(PARENT, bound_source=source("saved-third", member="c19"))
        other["z"] = 12
        observed["items"].append(other)
    elif change == "incomplete_scope":
        observed["complete"] = False
    else:
        observed["items"][1]["z"] = None
    result = compare(expected, observed, command)
    assert result["status"] != "confirmed"
    assert (result.get("error") or {}).get("code") != "CAPABILITY_UNSUPPORTED"


def view_effect_outcome(mode):
    expected, observed, command, sources = observed_view_effect(mode)
    result = compare(expected, observed, command)
    assert result["status"] == "confirmed"
    kind = {"zoom": "zoomed", "pan": "panned", "front": "fronted"}[mode]
    row = {"requestId": REQUEST, "actionId": ACTION, "commandIds": [COMMAND], "operation": mode,
           "status": "confirmed", "completionScope": "workspace", "expected": deepcopy(expected),
           "confirmation": result, "sourceRefs": [sources[B], sources[A]], "presentationIds": [A, B],
           "facts": [{"kind": kind, "actionId": ACTION, "sourceRefs": [sources[A]], "presentationIds": [A],
                      "predicateIds": ["view-effect"], "value": "untrusted producer assignment"}]}
    return row, observed, kind, sources


@pytest.mark.parametrize("mode", ["zoom", "pan", "front"])
@pytest.mark.parametrize("state", ["current", "no_current", "user_superseded"])
def test_effect_facts_retain_observed_values_and_distinguish_history_from_current(mode, state):
    row, observed, kind, sources = view_effect_outcome(mode)
    if state == "no_current":
        observed = None
    elif state == "user_superseded":
        observed["seq"], observed["receivedAtMono"] = 22, 102.0
        observed["items"][0]["propertyVersions"][mode] = 5
        observed["items"][0]["propertyOwners"][mode] = "user"
        observed["items"][0]["controls"][mode]["stateRevision"] = 5
    facts = core().supported_facts((row,), observed)
    effect = [f for f in facts if f["kind"] == kind]
    assert len(effect) == 1
    assert effect[0]["sourceRefs"] == [sources[A]]
    assert effect[0]["presentationIds"] == [A]
    assert effect[0]["value"] == {"zoom": 3.2, "pan": [0.25, 0.75], "front": {"above": [B]}}[mode]
    assert effect[0]["scope"] == ("current" if state == "current" else "historical")


@pytest.mark.parametrize("mode", ["zoom", "pan", "front"])
def test_effect_facts_cannot_cross_a_valid_siblings_source(mode):
    row, observed, kind, sources = view_effect_outcome(mode)
    row["facts"][0]["sourceRefs"] = [sources[B]]
    facts = core().supported_facts((row,), observed)
    assert not any(f["kind"] == kind for f in facts)
    assert any(f["kind"] == "limitation" for f in facts)


def live_overlay_case():
    original = source("population-e3-r0", member="e3")
    original["media"] = "labelled"
    original["identity"].pop("member")
    original["identity"]["crop"] = [0, 0, 640, 480]
    origin = {"captureId": "capture-321", "run": "run-8", "boot": "bridge-1", "seq": 321,
              "epoch": 7, "objective": 40, "scaleId": "calibration-1", "umPerPx": 0.75,
              "imageSize": [640, 480], "disc": [320, 240, 200], "offset": [0.0, 0.0]}
    original["provenance"].update(epoch=7, objective=40,
        scale={"id": "calibration-1", "umPerPx": 0.75, "objective": 40})
    live = {"key": "live-camera", "kind": "live", "media": "raw",
            "identity": {"run": "run-8", "objectId": "camera-view", "anchorCapture": "capture-321", "epoch": 7},
            "provenance": {"source": "camera-stream", "pixelsImmutable": False, "sampleFactsInReport": True},
            "availability": "temporary"}
    frame = {"run": "run-8", "boot": "bridge-1", "seq": 350}
    target = item(A, bound_source=live)
    target.update(drawRef=deepcopy(frame), mediaSize=[640, 480])
    target["propertyVersions"]["overlay"] = 4
    target["propertyOwners"]["overlay"] = "request:" + REQUEST
    target["controls"]["overlay"] = {"id": A + ":overlay", "role": "overlay", "target": A,
                                      "present": True, "enabled": True, "editable": False, "stateRevision": 4}
    observed, command = report(target), fence(A)
    command["targets"][A].update(sourceRef=deepcopy(live),
        basePropertyVersions={"source": 3, "open": 3, "overlay": 3},
        committedPropertyVersions={"source": 4, "open": 4, "overlay": 4})
    observed["commandResults"][0].update(drawRef=deepcopy(frame), appRunId="run-8", reportSeq=21,
        committedPropertyVersions={"source": 4, "open": 4, "overlay": 4},
        committedPropertyOwners={p: "request:" + REQUEST for p in ("source", "open", "overlay")})
    members = ["c" + str(i) for i in range(1, 161)]
    live_basis = deepcopy(origin)
    live_basis.update(captureId="capture-350", seq=350, offset=[2.0, 3.0])
    receipt = {"presentationId": A, "requestId": REQUEST, "actionId": ACTION, "commandId": COMMAND,
               "populationId": "e3", "revision": 0, "sourceRef": deepcopy(original), "origin": deepcopy(origin),
               "liveSourceRef": deepcopy(live), "liveBasis": live_basis, "drawRef": deepcopy(frame),
               "registration": {"state": "verified", "basis": "per-member-pixel-match", "reason": None},
               "verifiedCount": 160, "drawnCount": 160,
               "members": [{"memberId": mid, "verified": True, "drawn": True, "drawRef": deepcopy(frame),
                            "checkedAtMono": 100.9, "checkedBasis": deepcopy(live_basis)} for mid in members]}
    observed["liveEvidence"] = [receipt]
    goal = expect("evidence_on_live", (A,), {"populationId": "e3", "revision": 0, "sourceRef": deepcopy(original),
        "origin": deepcopy(origin), "liveSourceRef": deepcopy(live), "memberIds": members, "requiredCount": 160}, key="on-live")
    operation = picture_expectations(A, live)
    expected = operation + (goal,)
    return expected, observed, command, original, live


def live_goal_outcome(expected, observed, command, original, live):
    operation = expected[:-1]
    operation_proof = compare(operation, observed, command)
    assert operation_proof["status"] == "confirmed"  # Drawing the feed is a narrower effect.
    goal_proof = compare(expected, observed, command)
    row = {"requestId": REQUEST, "actionId": ACTION, "commandIds": [COMMAND], "operation": "open",
           "status": "confirmed", "completionScope": "workspace", "expected": deepcopy(operation),
           "confirmation": operation_proof, "sourceRefs": [deepcopy(original), deepcopy(live)], "presentationIds": [A],
           "goal": {"id": REQUEST + ":g-live", "expected": deepcopy(expected), "confirmation": goal_proof},
           "facts": [{"kind": "drawn", "actionId": ACTION, "sourceRefs": [deepcopy(live)], "presentationIds": [A]},
                     {"kind": "on_live", "goalId": REQUEST + ":g-live", "actionId": ACTION,
                      "sourceRefs": [deepcopy(original), deepcopy(live)], "presentationIds": [A], "predicateIds": ["on-live"]}]}
    return row, goal_proof


@pytest.mark.parametrize("checks", ["same_frame", "valid_prior_frame"])
def test_live_goal_requires_all_original_members_verified_and_drawn_on_the_actual_live_frame(checks):
    expected, observed, command, original, live = live_overlay_case()
    if checks == "valid_prior_frame":
        for member in observed["liveEvidence"][0]["members"]:
            member["checkedBasis"].update(captureId="capture-349", seq=349, offset=[0.0, 0.0])
    row, result = live_goal_outcome(expected, observed, command, original, live)
    assert result["status"] == "confirmed"
    facts = core().supported_facts((row,), observed)
    fulfilled = [f for f in facts if f["kind"] == "on_live" and f["scope"] == "current"]
    assert len(fulfilled) == 1
    assert fulfilled[0]["goalId"] == REQUEST + ":g-live"
    assert fulfilled[0]["goalStatus"] == "confirmed"
    assert fulfilled[0]["value"] == {"requested": 160, "verified": 160, "drawn": 160}
    assert fulfilled[0]["sourceRefs"] == [original, live]


@pytest.mark.parametrize("change", [
    "origin_capture", "origin_run", "origin_epoch", "objective", "scale", "scale_value", "image_geometry", "disc_geometry",
    "live_run", "live_epoch", "wrong_population", "wrong_revision", "wrong_original_source", "wrong_member",
    "duplicate_member", "unverified_member", "undrawn_member", "expired_check", "excess_travel",
    "foreign_check", "wrong_draw", "inflated_counts", "wrong_action", "wrong_command", "unregistered_overlay",
])
def test_live_goal_cannot_be_fulfilled_by_incompatible_or_incomplete_member_evidence(change):
    expected, observed, command, _, _ = live_overlay_case()
    receipt = observed["liveEvidence"][0]
    if change in ("origin_capture", "origin_run", "origin_epoch"):
        receipt["origin"][{"origin_capture": "captureId", "origin_run": "run", "origin_epoch": "epoch"}[change]] = "another-origin"
    elif change in ("objective", "scale", "scale_value", "image_geometry", "disc_geometry"):
        receipt["liveBasis"][{"objective": "objective", "scale": "scaleId", "scale_value": "umPerPx",
                              "image_geometry": "imageSize", "disc_geometry": "disc"}[change]] = {
            "objective": 20, "scale": "changed-eyepiece-scale", "scale_value": 0.5,
            "image_geometry": [800, 600], "disc_geometry": [320, 240, 210]}[change]
    elif change in ("live_run", "live_epoch"):
        receipt["liveSourceRef"]["identity"]["run" if change == "live_run" else "epoch"] = "another-live-basis"
    elif change in ("wrong_population", "wrong_revision"):
        receipt["populationId" if change == "wrong_population" else "revision"] = "another-population-or-revision"
    elif change == "wrong_original_source":
        receipt["sourceRef"]["identity"]["revision"] = 1  # Original bytes do not replace the accepted revision.
    elif change in ("wrong_member", "duplicate_member"):
        receipt["members"][0]["memberId"] = "c999" if change == "wrong_member" else "c2"
    elif change in ("unverified_member", "undrawn_member"):
        receipt["members"][0]["verified" if change == "unverified_member" else "drawn"] = False
        receipt["verifiedCount" if change == "unverified_member" else "drawnCount"] = 159
    elif change == "expired_check":
        receipt["members"][0]["checkedAtMono"] = 98.0
    elif change == "excess_travel":
        receipt["members"][0]["checkedBasis"]["offset"] = [-100.0, 0.0]
    elif change == "foreign_check":
        receipt["members"][0]["checkedBasis"]["boot"] = "another-camera-boot"
    elif change == "wrong_draw":
        receipt["members"][0]["drawRef"]["seq"] = 340
    elif change == "inflated_counts":
        receipt["members"][0]["drawn"] = False  # Claimed 160 cannot replace the actual membership.
    elif change in ("wrong_action", "wrong_command"):
        receipt["actionId" if change == "wrong_action" else "commandId"] = "another-occurrence"
    else:
        observed["items"][0]["controls"].pop("overlay")
    result = compare(expected, observed, command)
    assert result["status"] != "confirmed"
    assert "CAPABILITY_UNSUPPORTED" not in codes(result)
    assert (result.get("error") or {}).get("code") != "CAPABILITY_UNSUPPORTED"


@pytest.mark.parametrize("evidence", ["missing", "partial", "zero"])
def test_supported_live_action_without_fulfillment_stays_unmet_and_keeps_narrow_operation_fact(evidence):
    expected, observed, command, original, live = live_overlay_case()
    if evidence == "missing":
        observed.pop("liveEvidence")
    else:
        count = 80 if evidence == "partial" else 0
        receipt = observed["liveEvidence"][0]
        receipt.update(verifiedCount=count, drawnCount=count)
        for member in receipt["members"][count:]:
            member.update(verified=False, drawn=False)
    row, result = live_goal_outcome(expected, observed, command, original, live)
    assert result["status"] != "confirmed"
    assert (result.get("error") or {}).get("code") != "CAPABILITY_UNSUPPORTED"
    facts = core().supported_facts((row,), observed)
    assert any(f["kind"] == "drawn" and f["sourceRefs"] == [live] for f in facts)
    assert any(f["kind"] == "limitation" and f.get("goalId") == REQUEST + ":g-live"
               and f.get("goalStatus") == "unmet" for f in facts)
    assert not any(f["kind"] == "on_live" and f["scope"] == "current" for f in facts)


@pytest.mark.parametrize("at", [101.0, 105.0], ids=["before_deadline", "after_deadline"])
def test_unknown_live_registration_is_unverified_or_timed_out_without_claiming_capability_absence(at):
    expected, observed, command, _, _ = live_overlay_case()
    receipt = observed["liveEvidence"][0]
    receipt.update(verifiedCount=None, drawnCount=None,
                   registration={"state": "unknown", "basis": None, "reason": "no current member checks"})
    receipt["members"] = []
    result = compare(expected, observed, command, now=at)
    assert result["status"] in (("pending", "unobserved") if at == 101.0 else ("timed_out",))
    assert (result.get("error") or {}).get("code") != "CAPABILITY_UNSUPPORTED"


def test_zero_of_160_after_scale_change_preserves_actual_capture_fallback_and_unmet_live_goal():
    expected, observed, command, original, live = live_overlay_case()
    receipt = observed["liveEvidence"][0]
    receipt["liveBasis"].update(scaleId="changed-eyepiece-scale", umPerPx=0.5)
    receipt.update(verifiedCount=0, drawnCount=0,
                   registration={"state": "incompatible", "basis": "per-member-pixel-match", "reason": "eyepiece scale changed"})
    for member in receipt["members"]:
        member.update(verified=False, drawn=False)
    row, result = live_goal_outcome(expected, observed, command, original, live)
    assert result["status"] == "failed"
    assert "eyepiece scale changed" in result["error"]["reason"]
    assert any(u.get("observed", {}).get("drawnCount") == 0 and u["observed"]["verifiedCount"] == 0
               for u in result["unmatched"] if isinstance(u.get("observed"), dict))
    held = report(item(B, bound_source=original))
    held["items"][0]["mediaSize"] = [640, 480]
    held_command = fence(B)
    held_command.update(actionId=REQUEST + ":a2", commandId=COMMAND + ":fallback")
    held["commandResults"][0].update(actionId=REQUEST + ":a2", commandId=COMMAND + ":fallback")
    held_proof = compare(picture_expectations(B, original), held, held_command)
    assert held_proof["status"] == "confirmed"
    fallback = {"requestId": REQUEST, "actionId": REQUEST + ":a2", "commandIds": [COMMAND + ":fallback"],
        "operation": "open_capture", "status": "confirmed", "completionScope": "workspace",
        "expected": picture_expectations(B, original), "confirmation": held_proof,
        "sourceRefs": [original], "presentationIds": [B],
        "facts": [{"kind": "drawn", "actionId": REQUEST + ":a2", "sourceRefs": [original], "presentationIds": [B]}]}
    facts = core().supported_facts((row, fallback), held)
    assert any(f["kind"] == "drawn" and f["sourceRefs"] == [original] and f["presentationIds"] == [B] for f in facts)
    assert any(f["kind"] == "limitation" and f.get("goalStatus") == "unmet" and "scale" in f["text"] for f in facts)
    assert not any(f["kind"] == "on_live" and f["scope"] == "current" for f in facts)


@pytest.mark.parametrize("change", ["member_unregistered", "new_user_overlay", "no_current_report"])
def test_original_live_goal_proof_does_not_certify_changed_or_unobserved_current_overlay(change):
    expected, observed, command, original, live = live_overlay_case()
    row, result = live_goal_outcome(expected, observed, command, original, live)
    assert result["status"] == "confirmed"
    if change == "no_current_report":
        observed = None
    else:
        observed["seq"], observed["receivedAtMono"] = 22, 102.0
        if change == "member_unregistered":
            observed["liveEvidence"][0]["members"][0].update(verified=False, drawn=False)
            observed["liveEvidence"][0].update(verifiedCount=159, drawnCount=159)
        else:
            observed["items"][0]["propertyVersions"]["overlay"] = 5
            observed["items"][0]["propertyOwners"]["overlay"] = "user"
            observed["items"][0]["controls"]["overlay"]["stateRevision"] = 5
    facts = core().supported_facts((row,), observed)
    assert not any(f["kind"] == "on_live" and f["scope"] == "current" for f in facts)
    assert any(f.get("goalId") == REQUEST + ":g-live" and f.get("goalStatus") == "unmet" for f in facts)


def test_live_goal_member_receipt_is_copied_and_cannot_alias_a_later_renderer_change():
    expected, observed, command, _, _ = live_overlay_case()
    result = compare(expected, observed, command)
    assert result["status"] == "confirmed"
    assert result["liveEvidence"][0]["drawnCount"] == 160
    assert result["liveEvidence"][0]["members"][0]["drawn"] is True
    before = deepcopy(result)
    observed["liveEvidence"][0]["members"][0].update(verified=False, drawn=False)
    assert result == before


def test_camera_availability_cannot_replace_actual_current_live_overlay_drawing():
    expected, observed, command, _, live = live_overlay_case()
    observed["items"][0].update(visible=False, visibility={"state": "occluded", "scope": "workspace"})
    observed["liveEvidence"][0]["drawnCount"] = 0
    for member in observed["liveEvidence"][0]["members"]:
        member["drawn"] = False
    result = compare(expected, observed, command)
    assert result["status"] != "confirmed"
    assert result["observed"][0]["sourceRef"] == live
    assert (result.get("error") or {}).get("code") != "CAPABILITY_UNSUPPORTED"


def test_live_goal_fact_cannot_substitute_a_sibling_original_producing_source():
    expected, observed, command, original, live = live_overlay_case()
    row, result = live_goal_outcome(expected, observed, command, original, live)
    assert result["status"] == "confirmed"
    sibling = deepcopy(original)
    sibling["identity"]["capture"] = "another-original-capture"
    row["sourceRefs"].append(sibling)
    row["facts"][1]["sourceRefs"] = [sibling, live]
    facts = core().supported_facts((row,), observed)
    for fact in facts:
        if fact["kind"] == "on_live":
            assert fact["sourceRefs"] == [original, live]


def test_real_capability_build_gap_is_distinct_from_a_supported_missing_verifier():
    row = outcome(status="blocked", error={"actionId": ACTION, "requestId": REQUEST,
        "boundary": "admission", "code": "CAPABILITY_UNSUPPORTED", "buildId": "test-build",
        "capabilityId": "present.not-installed", "goalId": REQUEST + ":g-missing",
        "reason": "This capability is unsupported in this build; administrator implementation is required."})
    boundary = core().first_failed_boundary((row,))
    assert boundary["code"] == "CAPABILITY_UNSUPPORTED"
    assert boundary["buildId"] == "test-build"
    assert boundary["goalId"] == REQUEST + ":g-missing"
    assert boundary["capabilityId"] == "present.not-installed"
    assert "unsupported in this build" in core().supported_facts((row,), None)[0]["text"]


# Two-axis review at 438e: unknown observations are not negative facts.
def third_foreground_root():
    other = item(PARENT, bound_source=source("saved-third", member="c19"))
    other["z"] = 12
    return other


@pytest.mark.parametrize("scope", ["original", "current"])
@pytest.mark.parametrize("field", ["staged", "open", "visible", "connected"])
@pytest.mark.parametrize("unknown", ["absent", "null"])
def test_unknown_registered_root_participation_cannot_establish_front(scope, field, unknown):
    row, observed, kind, _ = view_effect_outcome("front")
    other = third_foreground_root()
    if unknown == "absent":
        other.pop(field)
    else:
        other[field] = None
    observed["items"].append(other)
    if scope == "original":
        _, _, command, _ = observed_view_effect("front")
        result = compare(row["expected"], observed, command)
        assert result["status"] == "pending"
        assert result["error"]["boundary"] == "observe"
        assert result["error"]["code"] != "CAPABILITY_UNSUPPORTED"
        row.update(status=result["status"], confirmation=result)
        assert not any(f["kind"] == kind for f in core().supported_facts((row,), observed))
    else:
        observed.update(seq=22, receivedAtMono=102.0)
        facts = core().supported_facts((row,), observed)
        assert not any(f["kind"] == kind and f["scope"] == "current" for f in facts)
        assert any(f["kind"] == kind and f["scope"] == "historical" for f in facts)


@pytest.mark.parametrize("scope", ["original", "current"])
@pytest.mark.parametrize("state", ["participating", "staged", "closed", "hidden", "disconnected"])
def test_front_scope_distinguishes_known_higher_roots_from_proven_exclusions(scope, state):
    row, observed, kind, _ = view_effect_outcome("front")
    other = third_foreground_root()
    exclusions = {"staged": ("staged", True), "closed": ("open", False),
                  "hidden": ("visible", False), "disconnected": ("connected", False)}
    if state in exclusions:
        field, value = exclusions[state]
        other[field] = value
    observed["items"].append(other)
    if scope == "original":
        _, _, command, _ = observed_view_effect("front")
        result = compare(row["expected"], observed, command)
        assert result["status"] == ("failed" if state == "participating" else "confirmed")
        if state == "participating":
            assert result["error"]["code"] == "TOPOLOGY_MISMATCH"
    else:
        observed.update(seq=22, receivedAtMono=102.0)
        current = any(f["kind"] == kind and f["scope"] == "current" for f in core().supported_facts((row,), observed))
        assert current is (state != "participating")


@pytest.mark.parametrize("field,value", [("staged", True), ("open", False), ("visible", False), ("connected", False)],
                         ids=["staged", "closed", "hidden", "disconnected"])
def test_proven_root_exclusion_survives_other_unknown_participation_fields(field, value):
    expected, observed, command, _ = observed_view_effect("front")
    other = third_foreground_root()
    other.update(staged=None, open=None, visible=None, connected=None)
    other[field] = value
    observed["items"].append(other)
    assert compare(expected, observed, command)["status"] == "confirmed"


def test_known_foreground_contradiction_outranks_an_unknown_root():
    expected, observed, command, _ = observed_view_effect("front")
    unknown = third_foreground_root()
    unknown.pop("staged")
    known = item("a96816ae-3ae8-4c62-8ad5-0c439324f548", bound_source=source("saved-fourth", member="c20"))
    known["z"] = 20
    observed["items"].extend((unknown, known))
    result = compare(expected, observed, command)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "TOPOLOGY_MISMATCH"


def independently_confirmed_view_goal(mode):
    row, observed, kind, sources = view_effect_outcome(mode)
    expected = deepcopy(row["expected"])
    expected[-1]["id"] = "goal-view"
    _, _, command, _ = observed_view_effect(mode)
    result = compare(expected, observed, command)
    assert result["status"] == "confirmed"
    gid = REQUEST + ":g-" + mode
    row["goal"] = {"id": gid, "expected": expected, "confirmation": result}
    goal_fact = deepcopy(row["facts"][0])
    goal_fact.update(goalId=gid, predicateIds=["goal-view"])
    row["facts"].append(goal_fact)
    return row, observed, kind, sources, gid


@pytest.mark.parametrize("mode", ["zoom", "pan", "front"])
def test_independently_confirmed_non_live_goal_is_current_and_fulfilled(mode):
    row, observed, kind, sources, gid = independently_confirmed_view_goal(mode)
    facts = core().supported_facts((row,), observed)
    goal_facts = [f for f in facts if f.get("goalId") == gid and f.get("goalStatus") == "confirmed"]
    assert len(goal_facts) == 1
    assert goal_facts[0]["kind"] == kind and goal_facts[0]["scope"] == "current"
    assert goal_facts[0]["sourceRefs"] == [sources[A]] and goal_facts[0]["presentationIds"] == [A]
    assert goal_facts[0]["value"] == {"zoom": 3.2, "pan": [0.25, 0.75], "front": {"above": [B]}}[mode]
    assert not any(f["kind"] == "limitation" and f.get("goalId") == gid for f in facts)


@pytest.mark.parametrize("mode", ["zoom", "pan", "front"])
@pytest.mark.parametrize("state", ["no_current_report", "same_value_user_supersession"])
def test_original_non_live_goal_history_remains_unmet_after_current_evidence_is_lost(mode, state):
    row, observed, kind, sources, gid = independently_confirmed_view_goal(mode)
    if state == "no_current_report":
        observed = None
    else:
        observed.update(seq=22, receivedAtMono=102.0)
        target = observed["items"][0]
        target["propertyVersions"][mode] = 5
        target["propertyOwners"][mode] = "user"
        target["controls"][mode]["stateRevision"] = 5
    facts = core().supported_facts((row,), observed)
    assert not any(f.get("goalId") == gid and f.get("goalStatus") == "confirmed" for f in facts)
    assert any(f.get("goalId") == gid and f.get("goalStatus") == "unmet" for f in facts)
    assert any(f["kind"] == kind and f["scope"] == "historical" and f["sourceRefs"] == [sources[A]] for f in facts)
    assert all("live" not in f["text"].lower() for f in facts if f.get("goalId") == gid)


def reveal_later_report(observed):
    later = deepcopy(observed)
    later.update(seq=22, receivedAtMono=102.0)
    for ack in later["commandResults"]:
        ack["reportSeq"] = 22
    return later


@pytest.mark.parametrize("mode", ["zoom", "front"])
@pytest.mark.parametrize("unknown", ["absent", "null"])
@pytest.mark.parametrize("phase", ["pending", "later_complete", "timeout"])
def test_missing_scalar_observation_can_complete_later_without_becoming_terminal_failure(mode, unknown, phase):
    expected, complete, command, _ = observed_view_effect(mode)
    observed = deepcopy(complete)
    control = observed["items"][0]["controls"][mode]
    if unknown == "absent":
        control.pop("observedValue")
    else:
        control["observedValue"] = None
    first = compare(expected, observed, command)
    assert first["status"] == "pending"
    assert first["error"]["boundary"] == "observe" and first["error"]["code"] == "VERIFICATION_UNAVAILABLE"
    if phase == "later_complete":
        result = compare(expected, reveal_later_report(complete), command, now=102.0)
        assert result["status"] == "confirmed"
        row = {"requestId": REQUEST, "actionId": ACTION, "commandIds": [COMMAND],
               "status": first["status"], "confirmation": first, "revision": 1}
        folded = core().observe_late(row, result, at=102.0)
        assert folded["status"] == "confirmed" and folded["confirmation"]["report"]["seq"] == 22
        assert row["status"] == "pending"
    elif phase == "timeout":
        result = compare(expected, observed, command, now=105.0)
        assert result["status"] == "timed_out" and result["error"]["code"] == "CONFIRM_TIMEOUT"


@pytest.mark.parametrize("mode", ["zoom", "front"])
@pytest.mark.parametrize("at", [101.0, 105.0], ids=["before_deadline", "after_deadline"])
def test_known_actual_value_contradiction_is_not_hidden_by_missing_control_scalar(mode, at):
    expected, observed, command, _ = observed_view_effect(mode)
    target = observed["items"][0]
    target["controls"][mode].pop("observedValue")
    if mode == "zoom":
        target["zoom"] = target["view"]["zoom"] = 1.0
    else:
        target["z"] = 4
    result = compare(expected, observed, command, now=at)
    assert result["status"] == "failed"
    assert result["error"]["code"] == ("CONTROL_UNAVAILABLE" if mode == "zoom" else "TOPOLOGY_MISMATCH")
    assert "disagree" not in result["error"]["reason"]  # No scalar was observed to compare.


@pytest.mark.parametrize("mode", ["zoom", "front"])
@pytest.mark.parametrize("at", [101.0, 105.0], ids=["before_deadline", "after_deadline"])
def test_pending_scalar_gap_cannot_hide_definitive_sibling_load_failure(mode, at):
    expected, observed, command, _ = observed_view_effect(mode)
    expected = (expected[-1],) + expected[:-1]  # Pending effect comes first.
    observed["items"][0]["controls"][mode].pop("observedValue")
    observed["items"][1].update(phase="error", error={"code": "MEDIA_LOAD_FAILED", "reason": "source decoder failed"})
    result = compare(expected, observed, command, now=at)
    assert result["status"] == "failed" and result["error"]["code"] == "MEDIA_LOAD_FAILED"


@pytest.mark.parametrize("unknown", ["absent", "null"])
@pytest.mark.parametrize("phase", ["pending", "later_complete", "timeout"])
def test_unknown_registration_with_missing_live_basis_can_complete_before_its_deadline(unknown, phase):
    expected, complete, command, _, _ = live_overlay_case()
    observed = deepcopy(complete)
    receipt = observed["liveEvidence"][0]
    if unknown == "absent":
        receipt.pop("liveBasis")
    else:
        receipt["liveBasis"] = None
    receipt.update(registration={"state": "unknown", "basis": None, "reason": "current registration not observed"},
                   verifiedCount=None, drawnCount=None)
    first = compare(expected, observed, command)
    assert first["status"] == "pending"
    assert first["error"]["boundary"] == "observe" and first["error"]["code"] == "VERIFICATION_UNAVAILABLE"
    if phase == "later_complete":
        later = reveal_later_report(complete)
        frame = {"run": "run-8", "boot": "bridge-1", "seq": 351}
        later["items"][0]["drawRef"] = deepcopy(frame)
        later["commandResults"][0]["drawRef"] = deepcopy(frame)
        later["liveEvidence"][0].update(drawRef=deepcopy(frame))
        later["liveEvidence"][0]["liveBasis"].update(captureId="capture-351", seq=351)
        for member in later["liveEvidence"][0]["members"]:
            member["drawRef"] = deepcopy(frame)  # Earlier pixel check is still within 2 s / 16 px.
        result = compare(expected, later, command, now=102.0)
        assert result["status"] == "confirmed"
        row = {"requestId": REQUEST, "actionId": ACTION, "commandIds": [COMMAND],
               "status": first["status"], "confirmation": first, "revision": 1}
        folded = core().observe_late(row, result, at=102.0)
        assert folded["status"] == "confirmed" and folded["confirmation"]["report"]["seq"] == 22
        assert row["status"] == "pending"
    elif phase == "timeout":
        result = compare(expected, observed, command, now=105.0)
        assert result["status"] == "timed_out" and result["error"]["code"] == "CONFIRM_TIMEOUT"


@pytest.mark.parametrize("contradiction", ["original_source", "measured_scale"])
def test_known_live_contradiction_survives_unknown_registration(contradiction):
    expected, observed, command, _, _ = live_overlay_case()
    receipt = observed["liveEvidence"][0]
    receipt["registration"] = {"state": "unknown", "basis": None, "reason": "current registration not observed"}
    if contradiction == "original_source":
        receipt["sourceRef"]["identity"]["revision"] = 1
        receipt["liveBasis"] = None
    else:
        receipt["liveBasis"].update(scaleId="changed-eyepiece-scale", umPerPx=0.5)
    result = compare(expected, observed, command)
    assert result["status"] == "failed"
    assert result["error"]["code"] == ("SOURCE_MISMATCH" if contradiction == "original_source" else "ALIGNMENT_FAILED")


# C4 at immutable ec34: an unrelated unknown must not erase a measured conflict.
@pytest.mark.parametrize("change", ["run", "boot", "epoch", "objective", "scale_id", "scale_value",
                                    "image_size", "disc_geometry", "basis_seq", "receipt_draw"])
@pytest.mark.parametrize("unknown", ["absent", "null"])
@pytest.mark.parametrize("registration", ["verified", "unknown"])
@pytest.mark.parametrize("at", [101.0, 105.0], ids=["before_deadline", "after_deadline"])
def test_partial_live_basis_preserves_known_alignment_failure_and_goal_cause(change, unknown, registration, at):
    expected, observed, command, original, live = live_overlay_case()
    receipt = observed["liveEvidence"][0]
    missing = "offset" if change == "boot" else "boot"  # Independent of the contradictory field.
    if unknown == "absent":
        receipt["liveBasis"].pop(missing)
    else:
        receipt["liveBasis"][missing] = None
    changes = {"run": ("run", "another-run"), "boot": ("boot", "another-camera-boot"),
               "epoch": ("epoch", 8), "objective": ("objective", 20),
               "scale_id": ("scaleId", "changed-eyepiece-scale"), "scale_value": ("umPerPx", 0.5),
               "image_size": ("imageSize", [800, 600]), "disc_geometry": ("disc", [320, 240, 210]),
               "basis_seq": ("seq", 351)}
    if change == "receipt_draw":
        receipt["drawRef"]["seq"] = 349  # The actual source draw and command still identify frame 350.
    else:
        field, value = changes[change]
        receipt["liveBasis"][field] = value
    receipt["registration"] = {"state": registration,
        "basis": "per-member-pixel-match" if registration == "verified" else None, "reason": None}
    row, _ = live_goal_outcome(expected, observed, command, original, live)
    result = compare(expected, observed, command, now=at)
    row["goal"]["confirmation"] = result  # A real independent comparison, never a fabricated confirmation.
    assert result["status"] == "failed"
    assert result["error"]["code"] == "ALIGNMENT_FAILED" and result["error"]["boundary"] == "draw"
    assert "unverified" not in result["error"]["reason"].lower()
    unmatched = next(u for u in result["unmatched"] if u["predicateId"] == "on-live")
    assert unmatched["observed"]["liveBasis"] == receipt["liveBasis"]
    assert unmatched["observed"]["drawRef"] == receipt["drawRef"]
    facts = core().supported_facts((row,), observed)
    limitations = [f for f in facts if f.get("goalId") == REQUEST + ":g-live" and f["kind"] == "limitation"]
    assert len(limitations) == 1 and limitations[0]["goalStatus"] == "unmet"
    assert limitations[0]["code"] == "ALIGNMENT_FAILED" and limitations[0]["text"] == result["error"]["reason"]
    assert limitations[0]["sourceRefs"] == [original, live]
    assert any(f["kind"] == "drawn" and f["sourceRefs"] == [live] for f in facts)
    assert not any(f["kind"] == "on_live" for f in facts)


@pytest.mark.parametrize("missing", ["boot", "captureId", "offset"])
@pytest.mark.parametrize("unknown", ["absent", "null"])
@pytest.mark.parametrize("registration", ["verified", "unknown"])
@pytest.mark.parametrize("phase", ["pending", "timeout", "later_complete"])
def test_partial_compatible_live_basis_stays_unverified_until_genuine_later_completion(missing, unknown, registration, phase):
    expected, complete, command, original, live = live_overlay_case()
    observed = deepcopy(complete)
    receipt = observed["liveEvidence"][0]
    if unknown == "absent":
        receipt["liveBasis"].pop(missing)
    else:
        receipt["liveBasis"][missing] = None
    receipt["registration"] = {"state": registration,
        "basis": "per-member-pixel-match" if registration == "verified" else None, "reason": None}
    row, first = live_goal_outcome(expected, observed, command, original, live)
    assert first["status"] == "pending"
    assert first["error"]["code"] == "VERIFICATION_UNAVAILABLE" and first["error"]["boundary"] == "observe"
    facts = core().supported_facts((row,), observed)
    assert any(f.get("goalId") == REQUEST + ":g-live" and f.get("goalStatus") == "unmet" for f in facts)
    assert any(f["kind"] == "drawn" and f["sourceRefs"] == [live] for f in facts)
    assert not any(f["kind"] == "on_live" for f in facts)
    if phase == "timeout":
        result = compare(expected, observed, command, now=105.0)
        assert result["status"] == "timed_out" and result["error"]["code"] == "CONFIRM_TIMEOUT"
        row["goal"]["confirmation"] = result
        facts = core().supported_facts((row,), observed)
        assert any(f.get("goalStatus") == "unmet" and f.get("code") == "CONFIRM_TIMEOUT" for f in facts)
        assert not any(f["kind"] == "on_live" for f in facts)
    elif phase == "later_complete":
        later = reveal_later_report(complete)
        frame = {"run": "run-8", "boot": "bridge-1", "seq": 351}
        later["items"][0]["drawRef"] = deepcopy(frame)
        later["commandResults"][0]["drawRef"] = deepcopy(frame)
        later["liveEvidence"][0]["drawRef"] = deepcopy(frame)
        later["liveEvidence"][0]["liveBasis"].update(captureId="capture-351", seq=351)
        for member in later["liveEvidence"][0]["members"]:
            member["drawRef"] = deepcopy(frame)  # Existing checks remain within the unchanged 2 s / 16 px limits.
        result = compare(expected, later, command, now=102.0)
        assert result["status"] == "confirmed"
        pending = {"requestId": REQUEST, "actionId": ACTION, "commandIds": [COMMAND],
                   "status": first["status"], "confirmation": first, "revision": 1}
        folded = core().observe_late(pending, result, at=102.0)
        assert folded["status"] == "confirmed" and folded["confirmation"]["report"]["seq"] == 22
        assert pending["status"] == "pending"
        row["goal"]["confirmation"] = folded["confirmation"]
        facts = core().supported_facts((row,), later)
        fulfilled = [f for f in facts if f["kind"] == "on_live" and f["scope"] == "current"]
        assert len(fulfilled) == 1 and fulfilled[0]["goalStatus"] == "confirmed"
        assert fulfilled[0]["value"] == {"requested": 160, "verified": 160, "drawn": 160}
        assert fulfilled[0]["sourceRefs"] == [original, live]
        assert not any(f["kind"] == "limitation" and f.get("goalId") == REQUEST + ":g-live" for f in facts)


# C5 at immutable ec34: originally matching an optional condition does not adopt it as essential.
def independently_confirmed_zoom_goal_with_sibling_rect(*, optional_matched=True, essential_rect=False):
    row, observed, kind, sources, gid = independently_confirmed_view_goal("zoom")
    rect = deepcopy(observed["items"][1]["rect"])
    if not optional_matched:
        rect[2] += 80.0
    condition = expect("rect", (B,), {"rect": rect}, key="sibling-rect", essential=essential_rect)
    expected = tuple(row["goal"]["expected"]) + (condition,)
    _, _, command, _ = observed_view_effect("zoom")
    result = compare(expected, observed, command)
    assert result["status"] == "confirmed"
    assert ("sibling-rect" in result["matched"]) is optional_matched
    row["goal"].update(expected=expected, confirmation=result)
    return row, observed, kind, sources, gid


@pytest.mark.parametrize("optional_matched", [True, False], ids=["originally_matched", "originally_unmatched"])
@pytest.mark.parametrize("change", ["unchanged", "resized", "same_value_user_revision", "resized_by_user"])
def test_optional_sibling_rect_does_not_revoke_essential_zoom_goal(optional_matched, change):
    row, observed, kind, sources, gid = independently_confirmed_zoom_goal_with_sibling_rect(optional_matched=optional_matched)
    observed.update(seq=22, receivedAtMono=102.0)
    sibling = observed["items"][1]
    if change in ("resized", "resized_by_user"):
        sibling["rect"][2] += 40.0
    if change in ("same_value_user_revision", "resized_by_user"):
        sibling["propertyVersions"]["rect"] = 5
        sibling["propertyOwners"]["rect"] = "user"
    facts = core().supported_facts((row,), observed)
    fulfilled = [f for f in facts if f.get("goalId") == gid and f["kind"] == kind and f.get("goalStatus") == "confirmed"]
    assert len(fulfilled) == 1 and fulfilled[0]["scope"] == "current"
    assert fulfilled[0]["value"] == 3.2 and fulfilled[0]["sourceRefs"] == [sources[A]]
    assert not any(f["kind"] == "limitation" and f.get("goalId") == gid for f in facts)


@pytest.mark.parametrize("change", ["unchanged", "resized", "same_value_user_revision"])
def test_optional_geometry_fact_is_revalidated_separately_from_essential_zoom_goal(change):
    row, observed, kind, sources, gid = independently_confirmed_zoom_goal_with_sibling_rect()
    row["facts"].append({"kind": "arranged", "goalId": gid, "actionId": ACTION,
        "sourceRefs": [sources[B]], "presentationIds": [B], "predicateIds": ["sibling-rect"]})
    observed.update(seq=22, receivedAtMono=102.0)
    sibling = observed["items"][1]
    if change == "resized":
        sibling["rect"][2] += 40.0
    elif change == "same_value_user_revision":
        sibling["propertyVersions"]["rect"] = 5
        sibling["propertyOwners"]["rect"] = "user"
    facts = core().supported_facts((row,), observed)
    assert any(f["kind"] == kind and f.get("goalId") == gid and f.get("goalStatus") == "confirmed" for f in facts)
    geometry = [f for f in facts if f["kind"] == "arranged" and f["sourceRefs"] == [sources[B]]]
    assert len(geometry) == 1 and geometry[0]["scope"] == ("current" if change == "unchanged" else "historical")
    assert not any(f["kind"] == "limitation" and f.get("goalId") == gid for f in facts)


@pytest.mark.parametrize("change", ["essential_rect", "essential_rect_user_revision", "sibling_source",
                                    "sibling_source_user_revision", "sibling_open_user_revision", "sibling_hidden",
                                    "zoom_user_revision", "zoom_value"])
def test_essential_goal_conditions_and_source_ownership_dependencies_still_govern_fulfillment(change):
    row, observed, kind, sources, gid = independently_confirmed_zoom_goal_with_sibling_rect(
        essential_rect=change in ("essential_rect", "essential_rect_user_revision"))
    observed.update(seq=22, receivedAtMono=102.0)
    target, sibling = observed["items"]
    if change == "essential_rect":
        sibling["rect"][2] += 40.0
    elif change == "essential_rect_user_revision":
        sibling["propertyVersions"]["rect"] = 5
        sibling["propertyOwners"]["rect"] = "user"
    elif change == "sibling_source":
        sibling["sourceRef"]["identity"]["revision"] = 1
    elif change in ("sibling_source_user_revision", "sibling_open_user_revision"):
        field = "source" if change == "sibling_source_user_revision" else "open"
        sibling["propertyVersions"][field] = 5
        sibling["propertyOwners"][field] = "user"
    elif change == "sibling_hidden":
        sibling["visible"] = False
    elif change == "zoom_user_revision":
        target["propertyVersions"]["zoom"] = 5
        target["propertyOwners"]["zoom"] = "user"
        target["controls"]["zoom"]["stateRevision"] = 5
    else:
        target["zoom"] = target["view"]["zoom"] = target["controls"]["zoom"]["observedValue"] = 1.0
    facts = core().supported_facts((row,), observed)
    assert not any(f.get("goalId") == gid and f.get("goalStatus") == "confirmed" for f in facts)
    assert any(f["kind"] == "limitation" and f.get("goalId") == gid and f.get("goalStatus") == "unmet" for f in facts)
    assert any(f["kind"] == kind and f["sourceRefs"] == [sources[A]] and f["value"] == 3.2 for f in facts)


@pytest.mark.parametrize("missing", ["absent_basis", "null_basis", "unknown_boot"])
@pytest.mark.parametrize("at", [101.0, 105.0], ids=["before_deadline", "after_deadline"])
def test_known_non_pixel_registration_failure_is_not_hidden_by_missing_live_basis(missing, at):
    expected, observed, command, original, live = live_overlay_case()
    receipt = observed["liveEvidence"][0]
    receipt["registration"] = {"state": "verified", "basis": "geometry-only", "reason": None}
    if missing == "absent_basis":
        receipt.pop("liveBasis")
    elif missing == "null_basis":
        receipt["liveBasis"] = None
    else:
        receipt["liveBasis"]["boot"] = None
    row, _ = live_goal_outcome(expected, observed, command, original, live)
    result = compare(expected, observed, command, now=at)
    row["goal"]["confirmation"] = result
    assert result["status"] == "failed" and result["error"]["code"] == "ALIGNMENT_FAILED"
    assert result["error"]["boundary"] == "draw" and "unverified" not in result["error"]["reason"].lower()
    facts = core().supported_facts((row,), observed)
    assert any(f.get("goalId") == REQUEST + ":g-live" and f.get("goalStatus") == "unmet"
               and f.get("code") == "ALIGNMENT_FAILED" for f in facts)
    assert any(f["kind"] == "drawn" and f["sourceRefs"] == [live] for f in facts)
    assert not any(f["kind"] == "on_live" for f in facts)


def scoped_front_case():
    expected, observed, command, sources = observed_view_effect('front')
    observed['complete'] = False
    observed['foreground'] = {'scope': 'workspace-windows', 'hostId': 'stage', 'complete': True,
        'roots': [{'rootId': row['rootId'], 'z': row['z']} for row in observed['items']]}
    expected[-1]['expected'] = {'scope': 'workspace-windows', 'hostId': 'stage', 'above': ['root-' + B]}
    return expected, observed, command, sources


def test_scoped_foreground_proof_does_not_require_complete_global_inventory():
    expected, observed, command, _ = scoped_front_case()
    result = compare(expected, observed, command)
    assert result['status'] == 'confirmed'
    assert result['report']['complete'] is False
    assert result['report']['foreground'] == observed['foreground']


@pytest.mark.parametrize('fault', ['missing', 'incomplete', 'host', 'missing_target', 'missing_sibling',
    'higher_new_root', 'equal_new_root', 'wrong_z', 'sibling_z', 'duplicate', 'stale_paint', 'old_report', 'wrong_browser'])
def test_scoped_front_rejects_incomplete_or_noncausal_order(fault):
    expected, observed, command, _ = scoped_front_case()
    scope = observed['foreground']
    if fault == 'missing': observed.pop('foreground')
    elif fault == 'incomplete': scope['complete'] = False
    elif fault == 'host': scope['hostId'] = 'another-stage'
    elif fault == 'missing_target': scope['roots'].pop(0)
    elif fault == 'missing_sibling': scope['roots'].pop()
    elif fault in ('higher_new_root', 'equal_new_root'):
        scope['roots'].append({'rootId': 'late-window', 'z': 9 if fault == 'higher_new_root' else 8})
    elif fault == 'wrong_z': scope['roots'][0]['z'] = 100
    elif fault == 'sibling_z': observed['items'][1]['z'] = 100
    elif fault == 'duplicate': scope['roots'].append(deepcopy(scope['roots'][0]))
    elif fault == 'stale_paint':
        observed['items'][0]['viewDrawRef'] = observed['commandResults'][0]['viewDrawRef'] = command['targets'][A]['baseViewDrawRef']
    elif fault == 'old_report': observed['seq'] = 20
    elif fault == 'wrong_browser': observed['client']['clientEpoch'] = 'other-browser'
    assert compare(expected, observed, command)['status'] != 'confirmed'


def tab_front_case():
    """will-leaf, 30 Oct: in the Project workspace the windows live inside the study artifacts, so the stage's
    foreground lists only `side` and `study-artifacts` (incomplete) and never a window's own root. In front is then the
    window's order on its Tab: above every window shown there, by the z the studio measured."""
    expected, observed, command, sources = observed_view_effect('front')
    observed['complete'] = False
    observed['foreground'] = {'scope': 'workspace-windows', 'hostId': 'stage', 'complete': False,
                              'roots': [{'rootId': 'side', 'z': 25}, {'rootId': 'study-artifacts', 'z': 35}]}
    expected[-1]['expected'] = {'scope': 'tab-windows', 'above': [B]}
    return expected, observed, command, sources


def test_front_on_a_tab_is_proved_by_the_windows_order_there_without_a_complete_inventory():
    expected, observed, command, _ = tab_front_case()
    assert core().valid_postconditions([e for e in expected if e['predicate'] == 'front'])
    result = compare(expected, observed, command)
    assert result['status'] == 'confirmed', result


@pytest.mark.parametrize('fault', ['sibling_above', 'sibling_equal', 'sibling_z_unknown', 'late_window_above',
                                   'control_disagrees', 'stale_paint', 'old_report'])
def test_front_on_a_tab_is_not_confirmed_while_a_window_there_is_over_it(fault):
    expected, observed, command, sources = tab_front_case()
    sibling = observed['items'][1]
    if fault == 'sibling_above': sibling['z'] = 9
    elif fault == 'sibling_equal': sibling['z'] = 8
    elif fault == 'sibling_z_unknown': sibling.pop('z')
    elif fault == 'late_window_above':
        late = deepcopy(sibling)
        late.update(id='w-late', rootId='root-late', z=12)
        observed['items'].append(late)
    elif fault == 'control_disagrees': observed['items'][0]['controls']['front']['observedValue'] = 3
    elif fault == 'stale_paint':
        observed['items'][0]['viewDrawRef'] = observed['commandResults'][0]['viewDrawRef'] = command['targets'][A]['baseViewDrawRef']
    elif fault == 'old_report': observed['seq'] = 20
    assert compare(expected, observed, command)['status'] != 'confirmed'


def test_a_window_closed_since_is_no_longer_over_the_one_brought_to_front():
    expected, observed, command, _ = tab_front_case()
    gone = deepcopy(observed['items'][1])
    gone.update(id='w-closed', rootId='root-closed', z=99, open=False, visible=False)
    observed['items'].append(gone)
    expected[-1]['expected']['above'] = [B, 'w-closed']
    assert compare(expected, observed, command)['status'] == 'confirmed'
