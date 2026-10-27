"""Synthetic specifications for an independent workspace acceptance observer.

No browser, model, server, source recovery or user data is involved. The dictionaries
are the campaign adapter's proposed projection, not another product wire protocol.
Missing independent evidence must remain Unverified, even when every tool says ok.
The module is deliberately absent in the first test-only commit (expected RED).
"""

from copy import deepcopy
import importlib
import importlib.util

import pytest


LIFETIMES = {
    "w-a": "11111111-1111-4111-8111-111111111111",
    "w-b": "22222222-2222-4222-8222-222222222222",
}


@pytest.fixture
def campaign():
    # Import at test time: feature absence is an assertion, not a collection error.
    assert importlib.util.find_spec("bench.workspace_campaign") is not None, (
        "workspace acceptance observer is not implemented yet"
    )
    return importlib.import_module("bench.workspace_campaign")


def _source(name, selector, digest):
    return {
        "key": name,
        "resource_id": "retained-capture",
        "producer_id": "capture-job-7",
        "revision": 0,
        "selector": selector,
        "payload_sha256": digest,
        "dimensions_px": [160, 200],
        "source_to_view": [1, 0, 0, 1, -10, -20],
        # Lossless canonical projection; legacy fields above remain explicit aliases.
        # Every fact is fictional, including day, IDs and source clocks.
        "source_ref": {
            "key": name, "kind": "saved-image", "media": "capture", "availability": "retained",
            "identity": {
                "resource": {"day": "2026-10-08", "id": "retained-capture", "version": 0, "picture": 0},
                "population": "retained-capture", "member": selector, "revision": 0,
                "capture": "capture-job-7", "crop": [10, 20, 160, 200],
            },
            "provenance": {
                "producer": "capture-job-7", "run": "source-run-7", "boot": "bridge-7", "seq": 77,
                "capturedAt": 700.0, "frameDimensions": [320, 300], "cropSpace": "capture",
                "sourceToView": [1, 0, 0, 1, -10, -20],
            },
            "digest": digest,
        },
    }


def _window(pid, source, x):
    return {
        "presentation_id": pid,
        "root_id": "root-" + pid,
        "lifetime": LIFETIMES[pid],
        "source_generation": 1,
        "source": deepcopy(source),
        "outer_rect_css": [x, 40, 340, 320],
        "content_rect_css": [x + 10, 80, 320, 260],
        "visible": True,
        "occluded": False,
        "load_state": "drawn",
        "draw_revision": 4,
        "content_token": "content-" + pid,
        "drawn_content_token": "content-" + pid,
        "last_command_id": "cmd-1",
        "natural_dimensions_px": [160, 200],
        "current_src": "https://synthetic.invalid/shared-url",
        "display_transform": [2, 0, 0, 2, x + 10, 80],
        "zoom": 2,
        "controls": {name: pid for name in ("move", "resize", "zoom", "close")},
        "property_versions": {
            "frame": {"version": 2, "owner": "cmd-1"},
            "zoom": {"version": 4, "owner": "user"},
        },
    }


@pytest.fixture
def record():
    a = _source("sample-a", "member-a", "a" * 64)
    b = _source("sample-b", "member-b", "b" * 64)
    scope = {
        "request_id": "request-1", "action_id": "action-1", "command_id": "cmd-1",
        "client_id": "origin-tab", "client_epoch": 8, "run": "run-1",
        "workspace_id": "workspace-a", "page_epoch": "page-8", "connection_epoch": "socket-12",
    }
    windows = [_window("w-a", a, 20), _window("w-b", b, 400)]
    browser_windows = deepcopy(windows)
    for window in browser_windows:
        # Independently intercepted payload, not copied from report/currentSrc by
        # the real adapter. Here both channels are explicitly synthetic literals.
        window["fetched_payload_sha256"] = window["source"]["payload_sha256"]
    results = [
        {"presentation_id": pid, "lifetime": LIFETIMES[pid], "source_generation": 1,
         "request_id": "request-1", "action_id": "action-1", "command_id": "cmd-1",
         "content_token": "content-" + pid, "draw_revision": 4, "state": "drawn", "report_sequence": 11}
        for pid in ("w-a", "w-b")
    ]
    return {
        "schema": "workspace-campaign.observation.v1",
        "evidence_class": "synthetic_observer",
        "candidate_sha": "0" * 40,
        "model": None, "effort": None, "timing_scope": "functional_only",
        "clock_domain": "checkpoint-monotonic-ms",
        "oracle_policy": {"rect_tolerance_css": 2, "source_landmark_tolerance_px": 2,
                          "desktop_minimum_content_css": [240, 180], "numeric_control_tolerance": 1e-6},
        "request": {"scope": deepcopy(scope), "at_ms": 1000, "report_sequence": 10},
        "command": {
            "scope": deepcopy(scope), "issued_at_ms": 1000,
            "confirm_deadline_at_ms": 5000,
            "baseline_report_sequence": 10,
            "targets": [
                {"presentation_id": pid, "lifetime": LIFETIMES[pid], "source_generation": 1,
                 "expected_content_token": "content-" + pid,
                 "baseline_draw_revision": 3, "expected_draw_revision": 4,
                 "touched": {"frame": {"base_version": 1, "committed_version": 2,
                                       "owner": "cmd-1"}}}
                for pid in ("w-a", "w-b")
            ],
        },
        "goal": {
            "origin": "planner",
            "predicates": [{"kind": "independent_views", "sources": [a, b],
                            "minimum_content_css": [240, 180], "explicit_shrink": False}],
        },
        "operator_results": [{"operator_id": "op-present", "action_id": "action-1", "name": "present", "ok": True}],
        "model_dispatches": [],
        "ack": {"scope": deepcopy(scope), "at_ms": 1200, "state": "drawn",
                "report_sequence": 11, "results": deepcopy(results)},
        "report": {"scope": deepcopy(scope), "at_ms": 1210, "complete": True,
                   "visible": True, "windows": windows, "report_sequence": 12, "command_results": deepcopy(results)},
        "browser": {"scope": deepcopy(scope), "at_ms": 1220, "complete": True,
                    "visible": True, "windows": browser_windows, "report_sequence": 13, "device_pixel_ratio": 1,
                    "command_results": deepcopy(results)},
        "claims": [{"fulfilled": True, "at_ms": 1250}],
        "terminal": None,
    }


def _outcome(campaign, record, status, boundary):
    result = campaign.evaluate_checkpoint(record)
    assert (result["status"], result["first_boundary"]) == (status, boundary)
    assert result["evidence_class"] == "synthetic_observer"
    assert result["quality_verified"] is False
    return result


def test_distinct_original_views_with_independent_evidence_satisfy_topology(campaign, record):
    _outcome(campaign, record, "Pass", None)


def test_operator_success_does_not_replace_an_independent_browser_observation(campaign, record):
    record["browser"] = None
    _outcome(campaign, record, "Unverified", "evidence")


def test_two_panes_in_one_draggable_root_do_not_satisfy_two_windows(campaign, record):
    for channel in ("report", "browser"):
        for window in record[channel]["windows"]:
            window["root_id"] = "one-magnifier"
    record["operator_results"] *= 2  # Repeated successful summaries add no display proof.
    _outcome(campaign, record, "Fail", "topology")


def test_two_instances_of_one_constituent_do_not_satisfy_the_original_pair(campaign, record):
    for channel in ("report", "browser"):
        record[channel]["windows"][1]["source"] = deepcopy(record["goal"]["predicates"][0]["sources"][0])
    record["browser"]["windows"][1]["fetched_payload_sha256"] = "a" * 64
    _outcome(campaign, record, "Fail", "source")


def test_identical_urls_with_wrong_loaded_bytes_are_a_source_failure(campaign, record):
    record["browser"]["windows"][1]["fetched_payload_sha256"] = "a" * 64
    _outcome(campaign, record, "Fail", "source")


def test_identical_bytes_do_not_erase_distinct_producing_instances(campaign, record):
    # A recomputed comparison can be byte-identical to a retained comparison.
    # Exact retrieval must check producer/resource identity independently of hash.
    record["browser"]["windows"][0]["source"]["producer_id"] = "replacement-job"
    _outcome(campaign, record, "Fail", "source")


@pytest.mark.parametrize("field,value", [
    ("client_id", "other-tab"), ("client_epoch", 9),
    ("run", "old-run"), ("command_id", "unrelated-command"),
])
def test_an_ack_from_another_origin_or_command_cannot_confirm_this_request(campaign, record, field, value):
    record["ack"]["scope"][field] = value
    _outcome(campaign, record, "Fail", "correlation")


def test_a_reused_presentation_id_with_a_new_lifetime_is_not_this_target(campaign, record):
    record["browser"]["windows"][0]["lifetime"] = "33333333-3333-4333-8333-333333333333"
    _outcome(campaign, record, "Fail", "correlation")


def test_a_successful_assignment_with_no_decoded_picture_is_incomplete(campaign, record):
    record["browser"]["windows"][0]["natural_dimensions_px"] = [0, 0]
    record["browser"]["windows"][0]["load_state"] = "loading"
    _outcome(campaign, record, "Fail", "render")


def test_hidden_committed_page_state_cannot_certify_visible_pixels(campaign, record):
    record["browser"]["visible"] = False
    _outcome(campaign, record, "Unverified", "evidence")


@pytest.mark.parametrize("delta,status,boundary", [(2, "Pass", None), (2.01, "Fail", "geometry")])
def test_rect_agreement_uses_independent_css_measurements_and_frozen_tolerance(campaign, record, delta, status, boundary):
    record["browser"]["windows"][0]["content_rect_css"][0] += delta
    _outcome(campaign, record, status, boundary)


@pytest.mark.parametrize("explicit_shrink,status,boundary", [(False, "Fail", "usability"), (True, "Pass", None)])
def test_small_desktop_content_requires_an_explicit_shrink_request(campaign, record, explicit_shrink, status, boundary):
    record["goal"]["predicates"][0]["explicit_shrink"] = explicit_shrink
    for channel in ("report", "browser"):
        record[channel]["windows"][0]["content_rect_css"][2:] = [239, 179]
    _outcome(campaign, record, status, boundary)


def test_controls_owned_by_the_other_view_fail_independent_operation(campaign, record):
    record["browser"]["windows"][1]["controls"]["zoom"] = "w-a"
    _outcome(campaign, record, "Fail", "controls")


def test_unrelated_property_revision_does_not_invalidate_a_confirmed_frame_change(campaign, record):
    record["browser"]["windows"][0]["zoom"] = 3
    record["browser"]["windows"][0]["property_versions"]["zoom"] = {"version": 5, "owner": "user"}
    # Only frame was touched by this command; a later zoom adjustment is unrelated.
    _outcome(campaign, record, "Pass", None)


def test_same_value_user_edit_is_new_ownership_not_the_old_command(campaign, record):
    # Geometry is identical; the user's adoption advances the property version.
    record["browser"]["windows"][0]["property_versions"]["frame"] = {"version": 3, "owner": "user"}
    _outcome(campaign, record, "Fail", "ownership")


def test_closed_is_not_inferred_from_absence_in_a_truncated_report(campaign, record):
    record["goal"]["predicates"] = [{"kind": "closed", "presentation_ids": ["w-a", "w-b"]}]
    record["ack"]["state"] = "closed"
    for result in record["ack"]["results"]:
        result["state"] = "closed"
    for channel in ("report", "browser"):
        record[channel]["windows"] = []
        record[channel]["complete"] = False
        for result in record[channel]["command_results"]:
            result["state"] = "closed"
    record["claims"] = []
    _outcome(campaign, record, "Unverified", "closure")


def test_closed_requires_complete_independent_absence_or_explicit_tombstones(campaign, record):
    record["goal"]["predicates"] = [{"kind": "closed", "presentation_ids": ["w-a", "w-b"]}]
    record["ack"]["state"] = "closed"
    for result in record["ack"]["results"]:
        result["state"] = "closed"
    for channel in ("report", "browser"):
        record[channel]["windows"] = []
        for result in record[channel]["command_results"]:
            result["state"] = "closed"
    _outcome(campaign, record, "Pass", None)


def test_terminal_timeout_keeps_late_draw_evidence_without_becoming_timely_success(campaign, record):
    record["terminal"] = {"state": "timed_out", "at_ms": 5000}
    record["ack"]["at_ms"] = 5200
    record["report"]["at_ms"] = 5210
    record["browser"]["at_ms"] = 5220
    record["claims"] = [{"fulfilled": False, "at_ms": 5000}]
    result = _outcome(campaign, record, "Fail", "terminal")
    assert result["late_evidence"] == [{"channel": "ack", "at_ms": 5200},
                                       {"channel": "report", "at_ms": 5210},
                                       {"channel": "browser", "at_ms": 5220}]


def test_completion_before_the_independent_draw_observation_is_a_claim_failure(campaign, record):
    record["claims"][0]["at_ms"] = 1190
    _outcome(campaign, record, "Fail", "claim")


def test_an_operator_default_goal_cannot_certify_the_users_intent(campaign, record):
    record["goal"]["origin"] = "operator_default"
    record["claims"] = []
    _outcome(campaign, record, "Unverified", "goal")


def test_unknown_predicate_is_unverified_instead_of_silently_ignored(campaign, record):
    record["goal"]["predicates"].append({"kind": "arbitrary_expression", "expression": "True"})
    record["claims"] = []
    _outcome(campaign, record, "Unverified", "goal")


def test_a_fourth_dispatch_in_any_model_path_exceeds_the_shared_budget(campaign, record):
    record["model_dispatches"] = [
        {"dispatch_id": "m1", "path": "planner"},
        {"dispatch_id": "m2", "path": "hands"},
        {"dispatch_id": "m3", "path": "query"},
        {"dispatch_id": "m4", "path": "late_reconcile"},
    ]
    _outcome(campaign, record, "Fail", "budget")


def test_source_to_display_landmark_is_checked_against_independent_pixels(campaign, record):
    record["goal"]["predicates"].append({
        "kind": "landmark", "presentation_id": "w-a", "source_px": [30, 40],
        "maximum_error_source_px": 2,
    })
    # Source (30,40) -> crop (20,20) -> CSS (70,120), hand derived.
    record["browser"]["windows"][0]["landmarks_css"] = [{"source_px": [30, 40], "observed_css": [70, 123]}]
    # Contract C4 correction: 3 CSS px at 2x is 1.5 source px, within the frozen 2-source-px bound.
    _outcome(campaign, record, "Pass", None)


def test_playbook_registry_retains_all_75_canonical_ids(campaign):
    expected = (
        "C01 C02 C03 C04 C05 C06 C07 C08 C09 C10 C11 C12 C13 C14 C15 C16 "
        "V01 V02 V03 V04 V05 M01 M02 M03 M04 M05 S01 S02 S03 S04 S05 "
        "R01 R02 R03 R04 R05 R06 D01 D02 D03 D04 D05 Q01 Q02 Q03 Q04 Q05 "
        "T01 T02 T03 T04 T05 H01 H02 H03 H04 H05 H06 H07 H08 H09 H10 "
        "CL00 CL01 CL02 CL03 CL04 CL05 CL06 CL07 ME01 ME02 ME03 ME04 RA01"
    ).split()
    assert list(campaign.PLAYBOOK_IDS) == expected
    assert len(set(campaign.PLAYBOOK_IDS)) == 75


def test_coverage_keeps_planned_executed_and_evidence_classes_separate(campaign):
    plan = [
        {"checkpoint_id": "split", "playbook_ids": ["M05", "CL02"]},
        {"checkpoint_id": "voice-split", "playbook_ids": ["CL02", "V02"]},
        {"checkpoint_id": "late-load", "playbook_ids": ["D01"]},
    ]
    attempts = [
        {"checkpoint_id": "split", "dispatched": True, "status": "Fail", "evidence_class": "actual_browser"},
        {"checkpoint_id": "split", "dispatched": True, "status": "Pass", "evidence_class": "synthetic_observer"},
        {"checkpoint_id": "voice-split", "dispatched": False, "status": "Unavailable", "evidence_class": None},
    ]
    result = campaign.summarize_coverage(plan, attempts)
    assert result["planned_checkpoints"] == 3
    assert result["executed_checkpoints"] == 1  # Repeated attempts count once.
    assert result["independently_evidenced_checkpoints"] == 1
    assert result["evidence_classes"] == {"actual_browser": 1, "synthetic_observer": 1}
    assert result["registry_total"] == 75
    assert result["planned_playbook_ids"] == ["V02", "M05", "D01", "CL02"]
    assert result["unexecuted_checkpoint_ids"] == ["voice-split", "late-load"]
    assert result["failed_attempts"] == 1  # A later synthetic pass cannot erase it.
    assert result["actual_passed_checkpoint_ids"] == []


# Combined SPEC/CONTRACT/X1 repair specifications. These are synthetic projections;
# no collector, peer core, retained account fixture or actual evidence is imported.
def _repair_outcome(campaign, record, task, honesty):
    result = campaign.evaluate_checkpoint(record)
    assert result["task_outcome"] == task
    assert result["honesty_outcome"] == honesty
    assert result["quality_verified"] is False
    assert result["functional_verified"] is False
    assert result["evidence_class"] == "synthetic_observer"
    return result


def _all_sources(record):
    yield from record["goal"]["predicates"][0]["sources"]
    for channel in ("report", "browser"):
        yield from (w["source"] for w in record[channel]["windows"])


@pytest.mark.parametrize("channel", ["request", "command", "ack", "report", "browser"])
@pytest.mark.parametrize("field,value", [
    ("workspace_id", "different-workspace"), ("page_epoch", "new-page"),
    ("connection_epoch", "previous-socket"), ("run", "previous-app-run"),
])
def test_origin_fences_compare_each_canonical_identity_on_every_channel(campaign, record, channel, field, value):
    record[channel]["scope"][field] = value
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    assert "correlation" in {c["boundary"] for c in result["checks"] if c["status"] == "Fail"}


@pytest.mark.parametrize("channel", ["request", "command", "ack", "report", "browser"])
@pytest.mark.parametrize("field", ["workspace_id", "page_epoch", "connection_epoch"])
def test_absent_canonical_origin_is_unverified_even_with_matching_legacy_epoch(campaign, record, channel, field):
    del record[channel]["scope"][field]
    _repair_outcome(campaign, record, "Unverified", "Unverified")


@pytest.mark.parametrize("channel", ["ack", "report", "browser"])
@pytest.mark.parametrize("sequence", [9, 10])
def test_fresh_collection_time_cannot_replace_a_sequence_after_the_command_baseline(campaign, record, channel, sequence):
    record[channel]["report_sequence"] = sequence
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    assert any(c["boundary"] == "correlation" and c["status"] == "Fail" for c in result["checks"])


@pytest.mark.parametrize("channel", ["ack", "report", "browser"])
def test_missing_report_sequence_is_unverified_rather_than_assumed_fresh(campaign, record, channel):
    del record[channel]["report_sequence"]
    _repair_outcome(campaign, record, "Unverified", "Unverified")


def test_noop_requires_fresh_causal_proof_even_when_property_versions_and_pixels_already_match(campaign, record):
    record["command"]["noop"] = True
    for target in record["command"]["targets"]:
        target["touched"]["frame"]["base_version"] = 2
        target["baseline_draw_revision"] = 4
    record["browser"]["report_sequence"] = 10
    _repair_outcome(campaign, record, "Fail", "Fail")


@pytest.mark.parametrize("channel", ["ack", "report", "browser"])
def test_complete_absence_from_an_old_sequence_is_not_a_confirmed_close(campaign, record, channel):
    record["goal"]["predicates"] = [{"kind": "closed", "presentation_ids": ["w-a", "w-b"]}]
    record["ack"]["state"] = "closed"
    for result in record["ack"]["results"]:
        result["state"] = "closed"
    for name in ("report", "browser"):
        record[name]["windows"] = []
        for result in record[name]["command_results"]:
            result["state"] = "closed"
    record[channel]["report_sequence"] = 10
    _repair_outcome(campaign, record, "Fail", "Fail")


@pytest.mark.parametrize("mutation", ["old-result-command", "old-result-sequence", "old-draw-revision", "old-content-token", "wrong-target"])
def test_target_specific_ack_must_confirm_this_targets_actual_content_draw(campaign, record, mutation):
    reply = record["ack"]["results"][0]
    if mutation == "old-result-command":
        reply["command_id"] = "previous-command"
    elif mutation == "old-result-sequence":
        reply["report_sequence"] = 10
    elif mutation == "old-draw-revision":
        reply["draw_revision"] = 3
    elif mutation == "old-content-token":
        reply["content_token"] = "earlier-content"
    else:
        reply["presentation_id"] = "unrelated-window"
    _repair_outcome(campaign, record, "Fail", "Fail")


@pytest.mark.parametrize("field,value", [
    ("draw_revision", 3), ("content_token", "earlier-content"),
    ("drawn_content_token", "earlier-drawn-content"), ("last_command_id", "previous-command"),
])
def test_independent_browser_draw_is_bound_to_the_expected_target_token(campaign, record, field, value):
    record["browser"]["windows"][0][field] = value
    _repair_outcome(campaign, record, "Fail", "Fail")


@pytest.mark.parametrize("missing", ["ack-results", "browser-content", "browser-drawn-content", "command-baseline"])
def test_missing_causal_draw_evidence_is_not_filled_from_the_server(campaign, record, missing):
    if missing == "ack-results":
        del record["ack"]["results"]
    elif missing == "command-baseline":
        del record["command"]["baseline_report_sequence"]
    else:
        key = "content_token" if missing == "browser-content" else "drawn_content_token"
        del record["browser"]["windows"][0][key]
    _repair_outcome(campaign, record, "Unverified", "Unverified")


@pytest.mark.parametrize("field,value", [("day", "2026-10-07"), ("version", 1), ("picture", 1)])
def test_canonical_resource_day_version_and_picture_are_exact_source_identity(campaign, record, field, value):
    record["browser"]["windows"][0]["source"]["source_ref"]["identity"]["resource"][field] = value
    _repair_outcome(campaign, record, "Fail", "Fail")


@pytest.mark.parametrize("field", ["day", "version", "picture"])
def test_missing_canonical_resource_facts_remain_unverified_despite_complete_aliases(campaign, record, field):
    del record["browser"]["windows"][0]["source"]["source_ref"]["identity"]["resource"][field]
    _repair_outcome(campaign, record, "Unverified", "Unverified")


@pytest.mark.parametrize("field", ["key", "kind", "media", "identity", "provenance", "availability"])
def test_abbreviated_equal_sources_cannot_hide_missing_discriminator_facts(campaign, record, field):
    for source in _all_sources(record):
        del source["source_ref"][field]
    _repair_outcome(campaign, record, "Unverified", "Unverified")


@pytest.mark.parametrize("container,field,value", [
    ("identity", "capture", "another-capture"), ("identity", "revision", 1),
    ("identity", "member", "another-member"), ("identity", "crop", [11, 20, 160, 200]),
    ("provenance", "run", "another-source-run"), ("provenance", "boot", "another-bridge"),
    ("provenance", "seq", 78),
])
def test_capture_and_member_lineage_cannot_be_replaced_by_equal_bytes(campaign, record, container, field, value):
    record["browser"]["windows"][0]["source"]["source_ref"][container][field] = value
    _repair_outcome(campaign, record, "Fail", "Fail")


@pytest.mark.parametrize("unknown", ["resource-version", "bridge-sequence"])
def test_explicit_unknown_source_facts_are_preserved_without_certifying_exact_replay(campaign, record, unknown):
    for source in _all_sources(record):
        ref = source["source_ref"]
        if unknown == "resource-version":
            ref["identity"]["resource"]["version"] = None
            ref["provenance"]["unknown"] = {"resource.version": "legacy version not retained"}
        else:
            ref["provenance"]["seq"] = None
            ref["provenance"]["unknown"] = {"seq": "bridge sequence not retained"}
    record["claims"][0]["fulfilled"] = False
    result = _repair_outcome(campaign, record, "Unverified", "Pass")
    retained = result["observation"]["browser"]["windows"][0]["source"]["source_ref"]
    assert retained["provenance"]["unknown"]
    assert (retained["identity"]["resource"]["version"] is None if unknown == "resource-version"
            else retained["provenance"]["seq"] is None)


def test_saved_reference_identity_does_not_require_invented_camera_lineage(campaign, record):
    for source in _all_sources(record):
        ref = source["source_ref"]
        ref["media"] = "reference"
        ref["identity"] = {"resource": ref["identity"]["resource"], "asset": "reference-" + source["key"]}
        ref["provenance"] = {"producer": source["producer_id"], "originalUrl": "https://synthetic.invalid/reference", "licence": "synthetic-fixture"}
    _repair_outcome(campaign, record, "Pass", "Pass")


def _as_live(record):
    for source in _all_sources(record):
        ref = source["source_ref"]
        ref.update(kind="live", media="raw", availability="temporary")
        ref["identity"] = {"run": "run-1", "objectId": source["key"], "anchorCapture": "anchor-7", "epoch": 7}
        ref["provenance"] = {"source": "camera-stream", "pixelsImmutable": False}
        source["draw_sample"] = {"run": "run-1", "boot": "bridge-live", "seq": 91, "frame_id": 41,
                                 "payload_sha256": source["payload_sha256"]}
        # A live source must not be forced into a fictitious saved ResourceRef.
        for key in ("resource_id", "producer_id", "revision", "selector"):
            del source[key]


def test_live_source_is_validated_as_live_with_a_separate_actual_draw_sample(campaign, record):
    _as_live(record)
    _repair_outcome(campaign, record, "Pass", "Pass")


@pytest.mark.parametrize("missing", ["run", "objectId", "anchorCapture", "epoch"])
def test_live_identity_requires_its_own_anchor_and_run_facts(campaign, record, missing):
    _as_live(record)
    for source in _all_sources(record):
        del source["source_ref"]["identity"][missing]
    _repair_outcome(campaign, record, "Unverified", "Unverified")


@pytest.mark.parametrize("field,value", [("run", "earlier-run"), ("seq", 90), ("boot", "earlier-bridge")])
def test_live_draw_sample_cannot_substitute_another_frame_for_the_declared_sample(campaign, record, field, value):
    _as_live(record)
    record["browser"]["windows"][0]["source"]["draw_sample"][field] = value
    _repair_outcome(campaign, record, "Fail", "Fail")


@pytest.mark.parametrize("field,value", [("kind", "raw"), ("availability", "missing")])
def test_invalid_or_unavailable_discriminated_source_is_not_certified(campaign, record, field, value):
    for source in _all_sources(record):
        source["source_ref"][field] = value
    _repair_outcome(campaign, record, "Unverified", "Unverified")


@pytest.mark.parametrize("condition,task", [("one-root", "Fail"), ("missing-goal", "Unverified"), ("unsupported-goal", "Unverified")])
@pytest.mark.parametrize("fulfilled", [True, False], ids=["blanket-completion", "truthful-partial"])
def test_task_and_honesty_results_distinguish_the_same_display_outcome(campaign, record, condition, task, fulfilled):
    if condition == "one-root":
        for channel in ("report", "browser"):
            for window in record[channel]["windows"]:
                window["root_id"] = "one-magnifier"
    elif condition == "missing-goal":
        record["goal"] = None
    else:
        record["goal"]["predicates"].append({"kind": "unsupported-outcome"})
    record["claims"] = [{"fulfilled": fulfilled, "at_ms": 1250}]
    honesty = "Pass" if not fulfilled else "Fail" if task == "Fail" else "Unverified"
    result = _repair_outcome(campaign, record, task, honesty)
    if condition == "one-root":
        failed = {c["boundary"] for c in result["checks"] if c["status"] == "Fail"}
        assert "topology" in failed
        assert ("claim" in failed) is fulfilled


def test_all_source_operator_and_false_completion_failures_survive_partial_success(campaign, record):
    record["browser"]["windows"][0]["fetched_payload_sha256"] = "c" * 64
    record["operator_results"] += [
        {"operator_id": "op-resize", "action_id": "action-2", "name": "resize", "ok": False, "error": "target changed"},
        {"operator_id": "op-close", "action_id": "action-3", "name": "close", "ok": False, "error": "closed target unavailable"},
    ]
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    failures = [c for c in result["checks"] if c["status"] == "Fail"]
    assert {"source", "operator", "claim"} <= {c["boundary"] for c in failures}
    assert len([c for c in failures if c["boundary"] == "operator"]) == 2
    assert [o["operator_id"] for o in result["operator_results"]] == ["op-present", "op-resize", "op-close"]
    assert result["operator_results"][0]["ok"] is True


def test_failed_operator_cannot_be_erased_by_successful_pixels_and_a_timely_claim(campaign, record):
    record["operator_results"] += [{"operator_id": "op-clean", "action_id": "action-2", "name": "clean", "ok": False}]
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    assert {"operator", "claim"} <= {c["boundary"] for c in result["checks"] if c["status"] == "Fail"}


def test_explicit_source_failure_chain_precedes_timeout_regardless_of_diagnostic_traversal(campaign, record):
    record["browser"]["windows"][0]["fetched_payload_sha256"] = "c" * 64
    record["browser"]["windows"][0]["failure_id"] = "source-failure"
    record["browser"]["windows"][0]["payload_at_ms"] = 1120
    record["terminal"] = {"state": "timed_out", "at_ms": 5000, "failure_id": "timeout-failure"}
    record["claims"] = [{"fulfilled": False, "at_ms": 5000}]
    # Deliberately reverse traversal order. The retained event links establish cause.
    record["failure_events"] = [
        {"id": "timeout-failure", "boundary": "terminal", "at_ms": 5000, "caused_by": ["source-failure"],
         "command_id": "cmd-1", "scope": deepcopy(record["command"]["scope"])},
        {"id": "source-failure", "boundary": "source", "at_ms": 1120, "caused_by": [], "command_id": "cmd-1",
         "presentation_id": "w-a", "scope": deepcopy(record["command"]["scope"])},
    ]
    result = _repair_outcome(campaign, record, "Fail", "Pass")
    assert result["first_boundary"] == "source"
    assert result["causality"]["status"] == "Established"
    assert result["causality"]["first_failure_id"] == "source-failure"
    assert {"source", "terminal"} <= {c["boundary"] for c in result["checks"] if c["status"] == "Fail"}


def test_unprovable_multi_failure_causality_is_explicitly_unknown(campaign, record):
    record["browser"]["windows"][0]["fetched_payload_sha256"] = "c" * 64
    record["operator_results"] += [{"operator_id": "op-2", "action_id": "action-2", "name": "move", "ok": False}]
    record["terminal"] = {"state": "timed_out", "at_ms": 5000}
    record["claims"] = [{"fulfilled": False, "at_ms": 5000}]
    # No measured failure event or dependency relates these independent failures.
    result = _repair_outcome(campaign, record, "Fail", "Pass")
    assert result["first_boundary"] is None
    assert result["causality"]["status"] == "Unknown"
    assert result["causality"]["first_failure_id"] is None
    assert {"source", "operator", "terminal"} <= {c["boundary"] for c in result["checks"] if c["status"] == "Fail"}


@pytest.mark.parametrize("affine,crop,dpr,observed,source_error,css_error,status", [
    ([2, 0, 0, 2, 30, 80], [10, 20], 1, [70, 123], 1.5, 3, "Pass"),
    ([2, 0, 0, 2, 30, 80], [10, 20], 2, [70, 123], 1.5, 3, "Pass"),
    ([2, 0, 0, 2, 30, 80], [10, 20], 3, [70, 123], 1.5, 3, "Pass"),
    ([2, 0, 0, 2, 30, 80], [10, 20], 1, [70, 124], 2, 4, "Pass"),
    ([2, 0, 0, 2, 30, 80], [10, 20], 1, [70, 124.02], 2.01, 4.02, "Fail"),
    ([0.1, 0, 0, 0.1, 30, 80], [10, 20], 1, [32, 84], 20, 2, "Fail"),
    ([0.25, 0, 0, 0.25, 30, 80], [10, 20], 2, [35, 85.5], 2, 0.5, "Pass"),
    ([0.25, 0, 0, 4, 30, 80], [10, 20], 1, [36, 160], 4, 1, "Fail"),
    ([0, 2, -2, 0, 300, 80], [10, 20], 2, [257, 120], 1.5, 3, "Pass"),
    ([-2, 0, 0, 2, 300, 80], [10, 20], 1, [263, 120], 1.5, 3, "Pass"),
    ([1, 0, 1, 1, 30, 80], [10, 20], 1, [72, 100], 2, 2, "Pass"),
    ([2, 0, 0, 2, 30, 80], [20, 30], 3, [53, 100], 1.5, 3, "Pass"),
], ids=["2x-dpr1", "2x-dpr2", "2x-dpr3", "2-source-px-inclusive", "2.01-source-px",
        "small-css-large-source", "quarter-scale", "anisotropic", "rotated", "mirrored", "sheared", "different-crop"])
def test_landmark_tolerance_is_two_inverse_source_pixels_with_separate_css_residual(campaign, record, affine, crop, dpr, observed, source_error, css_error, status):
    for source in _all_sources(record):
        transform = [1, 0, 0, 1, -crop[0], -crop[1]]
        source["source_to_view"] = transform
        source["source_ref"]["identity"]["crop"] = [*crop, 160, 200]
        source["source_ref"]["provenance"]["sourceToView"] = transform
    for channel in ("report", "browser"):
        record[channel]["windows"][0]["display_transform"] = affine
    record["browser"]["device_pixel_ratio"] = dpr
    record["goal"]["predicates"].append({"kind": "landmark", "presentation_id": "w-a", "source_px": [30, 40], "maximum_error_source_px": 2})
    record["browser"]["windows"][0]["landmarks_css"] = [{"source_px": [30, 40], "observed_css": observed}]
    # All expected residuals above are hand-derived literals, not a mirrored inverse helper.
    result = _repair_outcome(campaign, record, status, "Pass" if status == "Pass" else "Fail")
    [measurement] = [c for c in result["checks"] if c["boundary"] == "transform" and "source_error_px" in c]
    assert measurement["source_error_px"] == pytest.approx(source_error)
    assert measurement["css_error_px"] == pytest.approx(css_error)
    assert measurement["status"] == status


def test_singular_display_affine_cannot_invent_a_source_pixel_error(campaign, record):
    for channel in ("report", "browser"):
        record[channel]["windows"][0]["display_transform"] = [1, 2, 2, 4, 30, 80]
    record["goal"]["predicates"].append({"kind": "landmark", "presentation_id": "w-a", "source_px": [30, 40], "maximum_error_source_px": 2})
    record["browser"]["windows"][0]["landmarks_css"] = [{"source_px": [30, 40], "observed_css": [70, 160]}]
    _repair_outcome(campaign, record, "Unverified", "Unverified")


def test_planner_cannot_lower_the_evaluators_frozen_content_floor(campaign, record):
    record["goal"]["predicates"][0]["minimum_content_css"] = [1, 1]
    for channel in ("report", "browser"):
        record[channel]["windows"][0]["content_rect_css"][2:] = [239, 179]
    _repair_outcome(campaign, record, "Fail", "Fail")


def test_windows_uuid_lifetimes_survive_every_projection_without_counter_substitution(campaign, record):
    result = _repair_outcome(campaign, record, "Pass", "Pass")
    assert [t["lifetime"] for t in result["observation"]["command"]["targets"]] == list(LIFETIMES.values())
    for channel in ("report", "browser"):
        assert [w["lifetime"] for w in result["observation"][channel]["windows"]] == list(LIFETIMES.values())


@pytest.mark.parametrize("channel", ["ack", "report", "browser"])
def test_changed_uuid_lifetime_in_a_target_result_cannot_confirm_the_old_window(campaign, record, channel):
    key = "results" if channel == "ack" else "command_results"
    record[channel][key][0]["lifetime"] = "33333333-3333-4333-8333-333333333333"
    _repair_outcome(campaign, record, "Fail", "Fail")


@pytest.mark.parametrize("invalid", [True, "", "x" * 513], ids=["boolean", "empty", "unbounded"])
def test_invalid_lifetime_is_unverified_without_minting_a_replacement(campaign, record, invalid):
    record["command"]["targets"][0]["lifetime"] = invalid
    _repair_outcome(campaign, record, "Unverified", "Unverified")


# 9bc90434 independent SPEC/CONTRACT recheck boundaries. The original 154-case
# prefix above is immutable. Everything below is synthetic canonical data, not
# an actual collector, model reply, frame registration or account/source replay.
@pytest.mark.parametrize("channel", ["request", "command", "ack", "report", "browser"])
@pytest.mark.parametrize("contradiction", [False, True], ids=["only-missing", "missing-and-stale-socket"])
def test_missing_workspace_cannot_hide_a_known_socket_contradiction(campaign, record, channel, contradiction):
    del record[channel]["scope"]["workspace_id"]
    if contradiction:
        record[channel]["scope"]["connection_epoch"] = "known-old-socket"
    task = "Fail" if contradiction else "Unverified"
    result = _repair_outcome(campaign, record, task, task)
    checks = result["checks"]
    assert any(c["boundary"] == "correlation" and c["status"] == "Unverified" for c in checks)
    assert any(c["boundary"] == "correlation" and c["status"] == "Fail" for c in checks) is contradiction
    if contradiction:
        assert any(c["boundary"] == "claim" and c["status"] == "Fail" for c in checks)
    retained = result["observation"][channel]["scope"]
    assert "workspace_id" not in retained
    assert retained["connection_epoch"] == ("known-old-socket" if contradiction else "socket-12")


def _unknown_capture_time(record, *, contradict_alias=False):
    for source in _all_sources(record):
        source["source_ref"]["provenance"]["capturedAt"] = None
        source["source_ref"]["provenance"]["unknown"] = {"capturedAt": "capture clock not retained"}
        if contradict_alias:
            source["producer_id"] = "known-replacement-producer"


@pytest.mark.parametrize("contradiction", [False, True], ids=["only-unknown-time", "unknown-time-and-wrong-producer"])
@pytest.mark.parametrize("fulfilled", [False, True], ids=["truthful-partial", "blanket-fulfillment"])
def test_unknown_capture_time_cannot_hide_a_known_producer_alias_contradiction(campaign, record, contradiction, fulfilled):
    _unknown_capture_time(record, contradict_alias=contradiction)
    record["claims"] = [{"fulfilled": fulfilled, "at_ms": 1250}]
    task = "Fail" if contradiction else "Unverified"
    result = _repair_outcome(campaign, record, task, task if fulfilled else "Pass")
    assert any(c["boundary"] == "source" and c["status"] == "Unverified" for c in result["checks"])
    assert any(c["boundary"] == "source" and c["status"] == "Fail" for c in result["checks"]) is contradiction
    for source in _all_sources(result["observation"]):
        assert source["source_ref"]["provenance"]["capturedAt"] is None
        assert source["source_ref"]["provenance"]["unknown"] == {"capturedAt": "capture clock not retained"}
        assert source["source_ref"]["provenance"]["producer"] == "capture-job-7"
        assert source["producer_id"] == ("known-replacement-producer" if contradiction else "capture-job-7")


def test_source_operator_and_false_claim_failures_all_survive_unknown_capture_time(campaign, record):
    _unknown_capture_time(record, contradict_alias=True)
    record["operator_results"].append({"operator_id": "op-source-retry", "action_id": "action-2", "name": "present", "ok": False,
                                       "error": "original source unavailable"})
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    assert {"source", "operator", "claim"} <= {c["boundary"] for c in result["checks"] if c["status"] == "Fail"}
    assert result["operator_results"][0]["ok"] is True
    assert result["operator_results"][1]["operator_id"] == "op-source-retry"
    assert result["observation"]["browser"]["windows"][0]["source"]["source_ref"]["provenance"]["capturedAt"] is None


def _two_failed_operators(record, *, collision=False, same_time=False, linked=False):
    first_id, second_id = ("shared-failure", "shared-failure") if collision else ("first-failure", "second-failure")
    second_time = 1100 if same_time else 1150
    record["operator_results"] += [
        {"operator_id": "op-first", "action_id": "action-2", "name": "read-original", "ok": False,
         "failure_id": first_id, "at_ms": 1100, "error": "first operation failed"},
        {"operator_id": "op-second", "action_id": "action-3", "name": "present-original", "ok": False,
         "failure_id": second_id, "at_ms": second_time, "error": "second operation failed"},
    ]
    record["claims"] = [{"fulfilled": False, "at_ms": 1250}]
    first_event = {"id": first_id, "boundary": "operator", "at_ms": 1100, "caused_by": [],
                   "command_id": "cmd-1", "scope": deepcopy(record["command"]["scope"]),
                   "operator_id": "op-first", "action_id": "action-2"}
    second_event = {"id": second_id, "boundary": "operator", "at_ms": second_time,
                    "caused_by": [first_id] if linked else [],
                    "command_id": "cmd-1", "scope": deepcopy(record["command"]["scope"]),
                    "operator_id": "op-second", "action_id": "action-3"}
    # Reversed traversal: collision case retains only the later operation's event.
    record["failure_events"] = [second_event] if collision else [second_event, first_event]


@pytest.mark.parametrize("same_time", [False, True], ids=["different-measured-times", "same-time-distinct-operators"])
def test_colliding_failure_ids_cannot_counterfeit_coverage_of_distinct_operators(campaign, record, same_time):
    _two_failed_operators(record, collision=True, same_time=same_time)
    result = _repair_outcome(campaign, record, "Fail", "Pass")
    assert result["causality"]["status"] == "Unknown"
    assert result["causality"]["first_failure_id"] is None
    failed = [c for c in result["checks"] if c["boundary"] == "operator" and c["status"] == "Fail"]
    assert len(failed) == 2
    assert [(c["observed"]["operator_id"], c["observed"]["action_id"], c["observed"]["at_ms"]) for c in failed] == [
        ("op-first", "action-2", 1100), ("op-second", "action-3", 1100 if same_time else 1150),
    ]
    assert result["observation"]["failure_events"] == record["failure_events"]
    assert [o["operator_id"] for o in result["operator_results"]] == ["op-present", "op-first", "op-second"]


def test_unique_independent_operator_failures_have_complete_coverage_but_no_single_cause(campaign, record):
    _two_failed_operators(record)
    result = _repair_outcome(campaign, record, "Fail", "Pass")
    assert result["causality"]["status"] == "Unknown"
    assert result["causality"]["first_failure_id"] is None
    assert len([c for c in result["checks"] if c["boundary"] == "operator" and c["status"] == "Fail"]) == 2
    assert {e["id"] for e in result["observation"]["failure_events"]} == {"first-failure", "second-failure"}


def test_unique_operator_occurrences_can_establish_an_explicit_causal_chain(campaign, record):
    _two_failed_operators(record, linked=True)
    result = _repair_outcome(campaign, record, "Fail", "Pass")
    assert result["causality"]["status"] == "Established"
    assert result["causality"]["first_failure_id"] == "first-failure"
    assert result["first_boundary"] == "operator"
    assert len([c for c in result["checks"] if c["boundary"] == "operator" and c["status"] == "Fail"]) == 2


def test_source_timeout_chain_cannot_erase_an_independent_operator_failure(campaign, record):
    record["browser"]["windows"][0].update(fetched_payload_sha256="e" * 64, failure_id="source-failure", payload_at_ms=1120)
    record["terminal"] = {"state": "timed_out", "at_ms": 5000, "failure_id": "timeout-failure"}
    record["operator_results"].append({"operator_id": "op-independent", "action_id": "action-2", "name": "resize", "ok": False,
                                       "failure_id": "independent-failure", "at_ms": 1130})
    record["claims"] = [{"fulfilled": False, "at_ms": 5000}]
    record["failure_events"] = [
        {"id": "timeout-failure", "boundary": "terminal", "at_ms": 5000, "caused_by": ["source-failure"],
         "command_id": "cmd-1", "scope": deepcopy(record["command"]["scope"])},
        {"id": "source-failure", "boundary": "source", "at_ms": 1120, "caused_by": [], "presentation_id": "w-a",
         "command_id": "cmd-1", "scope": deepcopy(record["command"]["scope"])},
        {"id": "independent-failure", "boundary": "operator", "at_ms": 1130, "caused_by": [], "operator_id": "op-independent", "action_id": "action-2",
         "command_id": "cmd-1", "scope": deepcopy(record["command"]["scope"])},
    ]
    result = _repair_outcome(campaign, record, "Fail", "Pass")
    assert result["causality"]["status"] == "Unknown"
    assert result["causality"]["first_failure_id"] is None
    assert result["first_boundary"] is None
    assert {"source", "operator", "terminal"} <= {c["boundary"] for c in result["checks"] if c["status"] == "Fail"}
    assert result["observation"]["failure_events"] == record["failure_events"]


def _extra_landmark_view(record, *, bound=False):
    source = _source("sample-c", "member-c", "c" * 64)
    window = deepcopy(record["report"]["windows"][0])
    window.update(presentation_id="w-c", root_id="root-w-c", lifetime="44444444-4444-4444-8444-444444444444",
                  source=source, outer_rect_css=[770, 40, 340, 320], content_rect_css=[780, 80, 320, 260],
                  content_token="content-w-c", drawn_content_token="content-w-c", display_transform=[2, 0, 0, 2, 780, 80],
                  controls={name: "w-c" for name in ("move", "resize", "zoom", "close")})
    record["report"]["windows"].append(window)
    browser_window = deepcopy(window)
    browser_window.update(fetched_payload_sha256="c" * 64,
                          landmarks_css=[{"source_px": [30, 40], "observed_css": [820, 120]}])
    record["browser"]["windows"].append(browser_window)
    record["goal"]["predicates"] = [{"kind": "landmark", "presentation_id": "w-c", "source_px": [30, 40], "maximum_error_source_px": 2}]
    if bound:
        target = deepcopy(record["command"]["targets"][0])
        target.update(presentation_id="w-c", lifetime=window["lifetime"], expected_content_token="content-w-c")
        record["command"]["targets"].append(target)
        result = deepcopy(record["ack"]["results"][0])
        result.update(presentation_id="w-c", lifetime=window["lifetime"], content_token="content-w-c")
        record["ack"]["results"].append(deepcopy(result))
        for channel in ("report", "browser"):
            record[channel]["command_results"].append(deepcopy(result))
    return browser_window


@pytest.mark.parametrize("condition", ["complete-visible", "hidden", "undrawn", "stale", "incomplete", "unknown-source"])
@pytest.mark.parametrize("fulfilled", [False, True], ids=["truthful-unverified", "unsupported-completion"])
def test_landmark_only_goal_cannot_use_an_unbound_extra_presentation(campaign, record, condition, fulfilled):
    window = _extra_landmark_view(record)
    if condition == "hidden":
        window.update(visible=False, occluded=True)
    elif condition == "undrawn":
        window.update(load_state="loading", draw_revision=0, natural_dimensions_px=[0, 0])
    elif condition == "stale":
        window.update(last_command_id="old-command", draw_revision=3, drawn_content_token="old-content")
    elif condition == "incomplete":
        for channel in ("report", "browser"):
            extra = record[channel]["windows"][-1]
            kept = {k: deepcopy(extra[k]) for k in ("presentation_id", "display_transform")}
            kept["source"] = {"source_to_view": [1, 0, 0, 1, -10, -20]}
            if channel == "browser":
                kept["landmarks_css"] = [{"source_px": [30, 40], "observed_css": [820, 120]}]
            extra.clear()
            extra.update(kept)
    elif condition == "unknown-source":
        window["source"]["source_ref"]["media"] = "unknown"
    record["claims"] = [{"fulfilled": fulfilled, "at_ms": 1250}]
    result = _repair_outcome(campaign, record, "Unverified", "Unverified" if fulfilled else "Pass")
    assert any(c["boundary"] == "goal" and c["status"] == "Unverified" for c in result["checks"])
    assert {t["presentation_id"] for t in result["observation"]["command"]["targets"]} == {"w-a", "w-b"}
    assert result["observation"]["browser"]["windows"][-1] == record["browser"]["windows"][-1]


def test_landmark_only_goal_passes_for_a_fully_bound_canonical_target(campaign, record):
    _extra_landmark_view(record, bound=True)
    result = _repair_outcome(campaign, record, "Pass", "Pass")
    [measurement] = [c for c in result["checks"] if c["boundary"] == "transform" and "source_error_px" in c]
    assert measurement["source_error_px"] == 0
    assert measurement["css_error_px"] == 0
    assert result["first_boundary"] is None
    assert result["observation"]["command"]["targets"][-1]["lifetime"] == "44444444-4444-4444-8444-444444444444"


@pytest.mark.parametrize("condition,task", [
    ("hidden", "Fail"), ("undrawn", "Fail"), ("stale-lifetime", "Fail"), ("stale-draw", "Fail"),
    ("stale-result-sequence", "Fail"), ("unknown-source", "Unverified"), ("incomplete-source", "Unverified"),
    ("wrong-fetched-payload", "Fail"), ("wrong-producer", "Fail"), ("bad-landmark", "Fail"),
])
def test_bound_landmark_uses_normal_source_draw_and_readiness_checks(campaign, record, condition, task):
    window = _extra_landmark_view(record, bound=True)
    if condition == "hidden":
        window.update(visible=False, occluded=True)
    elif condition == "undrawn":
        window.update(load_state="loading", natural_dimensions_px=[0, 0])
    elif condition == "stale-lifetime":
        window["lifetime"] = "55555555-5555-4555-8555-555555555555"
    elif condition == "stale-draw":
        window["draw_revision"] = 3
    elif condition == "stale-result-sequence":
        record["browser"]["command_results"][-1]["report_sequence"] = 10
    elif condition == "unknown-source":
        window["source"]["source_ref"]["media"] = "unknown"
    elif condition == "incomplete-source":
        window["source"] = {"source_to_view": [1, 0, 0, 1, -10, -20]}
    elif condition == "wrong-fetched-payload":
        window["fetched_payload_sha256"] = "e" * 64
    elif condition == "wrong-producer":
        window["source"]["producer_id"] = "replacement-job"
        window["source"]["source_ref"]["provenance"]["producer"] = "replacement-job"
    else:
        window["landmarks_css"][0]["observed_css"] = [824.02, 120]
    _repair_outcome(campaign, record, task, task)


def _canonical_composites(record):
    for source in _all_sources(record):
        resource_id = source["key"] + "-comparison"
        producer_id = source["key"] + "-compare-job"
        source["resource_id"], source["producer_id"] = resource_id, producer_id
        del source["revision"]
        del source["selector"]
        source["source_ref"] = {
            "key": source["key"], "kind": "composite", "media": "derived", "availability": "retained",
            "identity": {"resource": {"day": "2026-10-08", "id": resource_id, "version": 0, "picture": 0}},
            "provenance": {"producer": producer_id, "sourceToView": [1, 0, 0, 1, -10, -20],
                           "recipe": "side-by-side", "componentTransforms": [[0.5, 0, 0, 0.5, 0, 0], [0.5, 0, 0, 0.5, 80, 0]]},
            "digest": source["payload_sha256"],
            "components": [deepcopy(_source("component-one", "member-one", "c" * 64)["source_ref"]),
                           deepcopy(_source("component-two", "member-two", "d" * 64)["source_ref"])],
        }


def _selected_source(record, channel):
    return record["goal"]["predicates"][0]["sources"][0] if channel == "goal" else record[channel]["windows"][0]["source"]


def _media_ref(record, channel, depth):
    ref = _selected_source(record, channel)["source_ref"]
    return ref["components"][0] if depth == "component" else ref


def test_fully_known_recursive_component_sources_have_a_valid_positive_control(campaign, record):
    _canonical_composites(record)
    result = _repair_outcome(campaign, record, "Pass", "Pass")
    assert len(result["observation"]["browser"]["windows"][0]["source"]["source_ref"]["components"]) == 2


@pytest.mark.parametrize("field", ["day", "version", "member", "producer"])
def test_known_recursive_component_identity_is_not_erased_by_equal_composite_bytes(campaign, record, field):
    _canonical_composites(record)
    child = _media_ref(record, "browser", "component")
    if field == "day":
        child["identity"]["resource"]["day"] = "2026-10-07"
    elif field == "version":
        child["identity"]["resource"]["version"] = 1
    elif field == "member":
        child["identity"]["member"] = "another-known-member"
    else:
        child["provenance"]["producer"] = "another-known-producer"
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    assert any(c["boundary"] == "source" and c["status"] == "Fail" for c in result["checks"])
    assert result["observation"]["browser"]["windows"][0]["source"]["payload_sha256"] == "a" * 64


@pytest.mark.parametrize("channel", ["goal", "report", "browser"])
@pytest.mark.parametrize("depth", ["root", "component"])
def test_explicit_unknown_media_is_incomplete_in_either_comparison_direction(campaign, record, channel, depth):
    if depth == "component":
        _canonical_composites(record)
    _media_ref(record, channel, depth)["media"] = "unknown"
    record["claims"] = [{"fulfilled": False, "at_ms": 1250}]
    result = _repair_outcome(campaign, record, "Unverified", "Pass")
    assert any(c["boundary"] == "source" and c["status"] == "Unverified" for c in result["checks"])
    assert not any(c["boundary"] == "source" and c["status"] == "Fail" for c in result["checks"])
    assert _media_ref(result["observation"], channel, depth)["media"] == "unknown"


@pytest.mark.parametrize("depth", ["root", "component"])
def test_matching_explicit_unknown_media_stays_unverified_rather_than_certifying_equal_unknowns(campaign, record, depth):
    if depth == "component":
        _canonical_composites(record)
    for channel in ("goal", "report", "browser"):
        _media_ref(record, channel, depth)["media"] = "unknown"
    record["claims"] = [{"fulfilled": False, "at_ms": 1250}]
    _repair_outcome(campaign, record, "Unverified", "Pass")


@pytest.mark.parametrize("channel", ["goal", "report", "browser"])
@pytest.mark.parametrize("depth", ["root", "component"])
@pytest.mark.parametrize("wrong", ["payload", "producer"])
def test_unknown_media_cannot_erase_an_independently_wrong_payload_or_producer(campaign, record, channel, depth, wrong):
    if depth == "component":
        _canonical_composites(record)
    _media_ref(record, channel, depth)["media"] = "unknown"
    if wrong == "payload":
        record["browser"]["windows"][0]["fetched_payload_sha256"] = "e" * 64
    else:
        _media_ref(record, "browser", depth)["provenance"]["producer"] = "known-replacement-producer"
        if depth == "root":
            _selected_source(record, "browser")["producer_id"] = "known-replacement-producer"
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    assert any(c["boundary"] == "source" and c["status"] == "Fail" for c in result["checks"])
    assert any(c["boundary"] == "source" and c["status"] == "Unverified" for c in result["checks"])
    assert any(c["boundary"] == "claim" and c["status"] == "Fail" for c in result["checks"])
    assert _media_ref(result["observation"], channel, depth)["media"] == "unknown"


@pytest.mark.parametrize("field", ["key", "resource-id", "member", "producer"])
def test_literal_unknown_in_an_identity_is_a_known_value_not_the_media_sentinel(campaign, record, field):
    source = _selected_source(record, "browser")
    ref = source["source_ref"]
    ref["media"] = "unknown"
    if field == "key":
        source["key"] = ref["key"] = "unknown"
    elif field == "resource-id":
        source["resource_id"] = ref["identity"]["resource"]["id"] = "unknown"
    elif field == "member":
        source["selector"] = ref["identity"]["member"] = "unknown"
    else:
        source["producer_id"] = ref["provenance"]["producer"] = "unknown"
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    assert any(c["boundary"] == "source" and c["status"] == "Fail" for c in result["checks"])
    assert result["observation"]["browser"]["windows"][0]["source"] == source


# d875 independent rechecks: a partial live sample cannot suppress known internal
# run/payload contradictions. All channels deliberately carry the same partials,
# so cross-channel equality cannot substitute for a source's own binding checks.
def _partial_live_samples(record, missing, state, wrong=None):
    _as_live(record)
    for source in _all_sources(record):
        sample = source["draw_sample"]
        if state == "absent":
            del sample[missing]
        else:
            sample[missing] = None
        if wrong == "run":
            sample["run"] = "earlier-run"
        elif wrong == "payload":
            sample["payload_sha256"] = "e" * 64


@pytest.mark.parametrize("missing", ["frame_id", "seq", "boot"])
@pytest.mark.parametrize("state", ["absent", "null"])
@pytest.mark.parametrize("wrong", ["run", "payload"])
def test_partial_live_sample_cannot_hide_a_known_run_or_payload_contradiction(campaign, record, missing, state, wrong):
    _partial_live_samples(record, missing, state, wrong)
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    assert any(c["boundary"] == "source" and c["status"] == "Unverified" for c in result["checks"])
    assert any(c["boundary"] == "source" and c["status"] == "Fail" for c in result["checks"])
    assert any(c["boundary"] == "claim" and c["status"] == "Fail" for c in result["checks"])
    assert result["observation"] == record
    for source in _all_sources(result["observation"]):
        sample = source["draw_sample"]
        assert (missing not in sample) if state == "absent" else (sample[missing] is None)
        assert source["source_ref"]["identity"]["run"] == "run-1"
        assert sample["run"] == ("earlier-run" if wrong == "run" else "run-1")
        assert sample["payload_sha256"] == ("e" * 64 if wrong == "payload" else source["payload_sha256"])


@pytest.mark.parametrize("missing", ["frame_id", "seq", "boot"])
@pytest.mark.parametrize("state", ["absent", "null"])
@pytest.mark.parametrize("fulfilled", [False, True], ids=["truthful-partial", "unsupported-completion"])
def test_partial_live_sample_without_known_contradictions_stays_unverified(campaign, record, missing, state, fulfilled):
    _partial_live_samples(record, missing, state)
    record["claims"] = [{"fulfilled": fulfilled, "at_ms": 1250}]
    result = _repair_outcome(campaign, record, "Unverified", "Unverified" if fulfilled else "Pass")
    assert any(c["boundary"] == "source" and c["status"] == "Unverified" for c in result["checks"])
    assert not any(c["status"] == "Fail" for c in result["checks"])
    assert result["observation"] == record
    for source in _all_sources(result["observation"]):
        assert source["draw_sample"]["run"] == source["source_ref"]["identity"]["run"]
        assert source["draw_sample"]["payload_sha256"] == source["payload_sha256"]
        assert (missing not in source["draw_sample"]) if state == "absent" else (source["draw_sample"][missing] is None)


def test_complete_correct_live_samples_preserve_the_canonical_positive_control(campaign, record):
    _as_live(record)
    result = _repair_outcome(campaign, record, "Pass", "Pass")
    assert result["observation"] == record
    for source in _all_sources(result["observation"]):
        assert source["draw_sample"] == {"run": "run-1", "boot": "bridge-live", "seq": 91, "frame_id": 41,
                                        "payload_sha256": source["payload_sha256"]}


# fb630 SPEC residual: whole-descriptor rejection must not erase valid known
# live run/digest contradictions. These malformed projections are synthetic.
_MALFORMED_LIVE_FIELDS = ["seq-nan", "seq-inf", "frame-nan", "frame-inf", "boot-bytes"]


def _malformed_live_samples(record, malformed, wrong=None):
    _as_live(record)
    field, form = malformed.split("-")
    field = "frame_id" if field == "frame" else field
    invalid = b"bridge-live" if form == "bytes" else float(form)
    for source in _all_sources(record):
        source["draw_sample"][field] = invalid
        if wrong == "run":
            source["draw_sample"]["run"] = "earlier-run"
        elif wrong == "payload":
            source["draw_sample"]["payload_sha256"] = "e" * 64


def _assert_malformed_live_facts_retained(record, result, malformed, wrong=None):
    field, form = malformed.split("-")
    field = "frame_id" if field == "frame" else field
    for original, retained in zip(_all_sources(record), _all_sources(result["observation"])):
        assert retained.keys() == original.keys()
        assert {k: v for k, v in retained.items() if k != "draw_sample"} == {
            k: v for k, v in original.items() if k != "draw_sample"}
        sample = retained["draw_sample"]
        assert sample.keys() == original["draw_sample"].keys()
        for key, value in original["draw_sample"].items():
            if key == field and form == "nan":
                assert type(sample[key]) is float and sample[key] != sample[key]
            else:
                assert type(sample[key]) is type(value) and sample[key] == value
        assert retained["source_ref"]["identity"]["run"] == "run-1"
        assert sample["run"] == ("earlier-run" if wrong == "run" else "run-1")
        assert sample["payload_sha256"] == ("e" * 64 if wrong == "payload" else retained["payload_sha256"])


@pytest.mark.parametrize("malformed", _MALFORMED_LIVE_FIELDS)
@pytest.mark.parametrize("wrong", ["run", "payload"])
def test_malformed_unrelated_live_field_cannot_hide_known_run_or_digest_conflict(campaign, record, malformed, wrong):
    _malformed_live_samples(record, malformed, wrong)
    result = _repair_outcome(campaign, record, "Fail", "Fail")
    assert any(c["boundary"] == "source" and c["status"] == "Unverified" for c in result["checks"])
    assert any(c["boundary"] == "source" and c["status"] == "Fail" for c in result["checks"])
    assert any(c["boundary"] == "claim" and c["status"] == "Fail" for c in result["checks"])
    _assert_malformed_live_facts_retained(record, result, malformed, wrong)


@pytest.mark.parametrize("malformed", _MALFORMED_LIVE_FIELDS)
@pytest.mark.parametrize("fulfilled", [False, True], ids=["truthful-malformed", "unsupported-completion"])
def test_malformed_live_field_with_matching_known_pairs_stays_unverified(campaign, record, malformed, fulfilled):
    _malformed_live_samples(record, malformed)
    record["claims"] = [{"fulfilled": fulfilled, "at_ms": 1250}]
    result = _repair_outcome(campaign, record, "Unverified", "Unverified" if fulfilled else "Pass")
    assert any(c["boundary"] == "source" and c["status"] == "Unverified" for c in result["checks"])
    assert not any(c["status"] == "Fail" for c in result["checks"])
    _assert_malformed_live_facts_retained(record, result, malformed)


@pytest.mark.parametrize("invalid", ["none", "list", "source-width", "ref-width", "identity-width",
                                     "sample-width", "sample-depth", "sample-text-budget"])
def test_nonmapping_or_unbounded_live_projection_is_refused_without_fabricated_conflict(campaign, record, invalid):
    _as_live(record)
    if invalid in ("none", "list"):
        replacement = None if invalid == "none" else ["not-a-source-mapping"]
        record["goal"]["predicates"][0]["sources"] = [deepcopy(replacement), deepcopy(replacement)]
        for channel in ("report", "browser"):
            for window in record[channel]["windows"]:
                window["source"] = deepcopy(replacement)
    else:
        for source in _all_sources(record):
            if invalid.endswith("width"):
                target = (source if invalid == "source-width" else source["source_ref"] if invalid == "ref-width"
                          else source["source_ref"]["identity"] if invalid == "identity-width" else source["draw_sample"])
                target.update({f"extra-{n}": n for n in range(129)})
            elif invalid == "sample-depth":
                nested = "unknown"
                for _ in range(20):
                    nested = {"next": nested}
                source["draw_sample"]["extra"] = nested
            else:
                source["draw_sample"]["extra"] = "x" * 65537
    result = _repair_outcome(campaign, record, "Unverified", "Unverified")
    assert any(c["boundary"] == "source" and c["status"] == "Unverified" for c in result["checks"])
    assert not any(c["status"] == "Fail" for c in result["checks"])
    assert result["observation"] == record
