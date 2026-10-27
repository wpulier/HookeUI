"""Independent windows core contract, synthetic data only; no Studio/model acceptance.

The lead owns execution at the shared gate. These tests cross the proposed public
Presentations admit/commit/observe/read/restore interface. Resource resolution is an
injected read seam; commit is called by the existing apply seam. Observation is a
trusted renderer projection, not a new Screen completion policy or browser oracle.
"""
from __future__ import annotations

import copy
import importlib
import json
from uuid import UUID

import pytest


@pytest.fixture
def api():
    # Import per test so the lead can collect exact RED nodes before source exists.
    # Missing production module must fail, never skip or substitute a canned registry.
    return importlib.import_module("orchestrator.presentations")


def saved_wire(key="source-a", member="c17", *, media="raw", availability="retained"):
    return {
        "key": key,
        "kind": "saved-image",
        "media": media,
        "identity": {
            "resource": {"day": "2026-10-08", "id": member, "version": 0, "picture": 0},
            "population": "e3", "member": member, "revision": 0,
            "capture": "capture-a", "crop": [124, 80, 96, 110],
        },
        "provenance": {
            "run": "run-a", "boot": "bridge-a", "seq": 321,
            "cropSpace": "capture", "transforms": [], "scale": None,
        },
        "availability": availability,
    }


def live_wire(key="live-a", obj="o3", run="run-a"):
    return {
        "key": key, "kind": "live", "media": "raw",
        "identity": {"run": run, "objectId": obj, "anchorCapture": "capture-a", "epoch": 7},
        "provenance": {"source": "camera-stream", "pixelsImmutable": False},
        "availability": "temporary",
    }


@pytest.fixture
def setup(api):
    a = api.SourceRef.from_wire(saved_wire())
    b = api.SourceRef.from_wire(saved_wire("source-b", "c18"))
    composite = api.SourceRef.from_wire({
        "key": "comparison", "kind": "composite", "media": "derived",
        "identity": {"resource": {"day": "2026-10-08", "id": "d1", "version": 1, "picture": 0}},
        "provenance": {"operation": "compare", "parents": ["source-a", "source-b"]},
        "components": [a.to_wire(), b.to_wire()], "availability": "retained",
    })
    sources = {s.key: s for s in (a, b, composite)}

    def resolve(ref):
        if ref.key not in sources:
            raise api.PresentationError("source_missing", "exact source is not retained", next="choose a retained source")
        return sources[ref.key]

    registry = api.Presentations(resolve_source=resolve, app_run="run-a", max_windows=12, max_live=4)
    ctx = api.PresentationContext(
        workspace_id="workspace-a", client_id="client-a", client_epoch="epoch-a",
        request_id="request-a", owner="request:request-a",
    )
    registry.connect(ctx)
    return registry, ctx, sources


def action(api, op, *, instance=None, source=None, **values):
    return api.PresentationAction(op=op, instance=instance, source=source, values=values)


def change(api, registry, ctx, op, *, instance=None, source=None, **values):
    prepared = registry.admit(action(api, op, instance=instance, source=source, **values), ctx)
    return registry.commit(prepared)


def opened(api, registry, ctx, source):
    return change(api, registry, ctx, "open", source=source).targets[0]


def user_context(api, ctx):
    return api.PresentationContext(
        workspace_id=ctx.workspace_id, client_id=ctx.client_id, client_epoch=ctx.client_epoch,
        request_id="manual-a", owner="user",
    )


def observation(api, registry, ctx, effect, iid, *, phase="drawn", **override):
    instance = registry.get(ctx.workspace_id, iid)
    values = {
        "instance_id": iid, "command_id": effect.command_id,
        "source_key": instance.source.key, "source_generation": instance.source_generation,
        "lifetime_generation": instance.lifetime_generation, "revision": instance.revision,
        "phase": phase, "visible": True,
        "root_id": "root-" + iid, "render_token": "draw-" + effect.command_id,
        "rect": (520.25, 80.5, 320.0, 390.0), "content_rect": (520.25, 110.5, 320.0, 360.0),
        "natural_size": (96, 110), "property_versions": dict(instance.property_versions),
    }
    values.update(override)
    return api.PresentationObservation(**values)


def test_two_views_of_one_source_have_uuid_instances_and_independent_zoom(api, setup):
    registry, ctx, sources = setup
    first = opened(api, registry, ctx, sources["source-a"])
    second = opened(api, registry, ctx, sources["source-a"])
    assert first != second
    UUID(first.removeprefix("w-"))
    UUID(second.removeprefix("w-"))
    change(api, registry, ctx, "zoom", instance=first, level=4)
    assert registry.get(ctx.workspace_id, first).view.zoom == 4
    assert registry.get(ctx.workspace_id, second).view.zoom == 1
    assert registry.get(ctx.workspace_id, first).source.to_wire() == sources["source-a"].to_wire()


def test_source_identity_keeps_producing_member_even_for_identical_pixels(api, setup):
    registry, ctx, sources = setup
    wa, wb = saved_wire(), saved_wire("source-b", "c18")
    wa["digest"] = wb["digest"] = "same-byte-digest"
    sources["source-a"] = api.SourceRef.from_wire(wa)
    sources["source-b"] = api.SourceRef.from_wire(wb)
    a = opened(api, registry, ctx, sources["source-a"])
    b = opened(api, registry, ctx, sources["source-b"])
    assert registry.get(ctx.workspace_id, a).source.to_wire()["identity"]["member"] == "c17"
    assert registry.get(ctx.workspace_id, b).source.to_wire()["identity"]["member"] == "c18"


def test_source_and_returned_state_cannot_be_mutated_through_input_aliases(api, setup):
    registry, ctx, sources = setup
    wire = saved_wire("copied")
    source = api.SourceRef.from_wire(wire)
    sources[source.key] = source
    wire["identity"]["resource"]["version"] = 99
    iid = opened(api, registry, ctx, source)
    returned = registry.get(ctx.workspace_id, iid).source.to_wire()
    returned["identity"]["resource"]["version"] = 88
    assert registry.get(ctx.workspace_id, iid).source.to_wire()["identity"]["resource"]["version"] == 0


def test_close_one_view_keeps_same_source_sibling_and_its_pending_action(api, setup):
    registry, ctx, sources = setup
    a = opened(api, registry, ctx, sources["source-a"])
    b = opened(api, registry, ctx, sources["source-a"])
    pending = registry.admit(action(api, "zoom", instance=b, level=3), ctx)
    change(api, registry, user_context(api, ctx), "close", instance=a)
    registry.commit(pending)
    assert not registry.get(ctx.workspace_id, a).open
    assert registry.get(ctx.workspace_id, b).open
    assert registry.get(ctx.workspace_id, b).view.zoom == 3


def test_user_geometry_change_blocks_pending_agent_move(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    pending = registry.admit(action(api, "move", instance=iid, position=(100, 100)), ctx)
    change(api, registry, user_context(api, ctx), "move", instance=iid, position=(300, 200))
    with pytest.raises(api.PresentationError) as failed:
        registry.commit(pending)
    assert failed.value.code == "target_superseded"
    state = registry.get(ctx.workspace_id, iid)
    assert tuple(state.geometry_intent.position) == (300, 200)
    assert state.owners["geometry"] == "user"


def test_unrelated_front_change_does_not_cancel_pending_zoom(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    pending = registry.admit(action(api, "zoom", instance=iid, level=3), ctx)
    change(api, registry, user_context(api, ctx), "front", instance=iid)
    registry.commit(pending)
    state = registry.get(ctx.workspace_id, iid)
    assert state.view.zoom == 3
    assert state.owners["front"] == "user"


def test_source_replacement_invalidates_zoom_lease_even_when_geometry_unchanged(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    pending = registry.admit(action(api, "zoom", instance=iid, level=3), ctx)
    before = registry.get(ctx.workspace_id, iid).source_generation
    change(api, registry, user_context(api, ctx), "open", instance=iid, source=sources["source-b"])
    with pytest.raises(api.PresentationError) as failed:
        registry.commit(pending)
    assert failed.value.code == "target_superseded"
    assert registry.get(ctx.workspace_id, iid).source.key == "source-b"
    assert registry.get(ctx.workspace_id, iid).source_generation > before


def test_missing_exact_source_refuses_before_any_window_is_created(api, setup):
    registry, ctx, _ = setup
    missing = api.SourceRef.from_wire(saved_wire("missing", availability="missing"))
    with pytest.raises(api.PresentationError) as failed:
        registry.admit(action(api, "open", source=missing), ctx)
    assert failed.value.code == "source_missing"
    assert registry.list(ctx.workspace_id) == ()


def test_resolver_cannot_substitute_another_source_for_requested_identity(api, setup):
    registry, ctx, sources = setup
    sources["source-a"] = sources["source-b"]
    with pytest.raises(api.PresentationError) as failed:
        registry.admit(action(api, "open", source=api.SourceRef.from_wire(saved_wire())), ctx)
    assert failed.value.code == "source_identity_mismatch"
    assert registry.list(ctx.workspace_id) == ()


def test_capacity_includes_admitted_opens_without_evicting_user_windows(api, setup):
    _, ctx, sources = setup
    registry = api.Presentations(resolve_source=lambda ref: sources[ref.key], app_run="run-a", max_windows=2, max_live=1)
    registry.connect(ctx)
    user = user_context(api, ctx)
    a = opened(api, registry, user, sources["source-a"])
    pending = registry.admit(action(api, "open", source=sources["source-b"]), ctx)
    with pytest.raises(api.PresentationError) as failed:
        registry.admit(action(api, "open", source=sources["source-a"]), ctx)
    assert failed.value.code == "capacity_unavailable"
    assert registry.get(ctx.workspace_id, a).open
    registry.cancel(pending, reason="request canceled")
    b = opened(api, registry, ctx, sources["source-b"])
    assert b != a


def test_live_lens_limit_does_not_prohibit_saved_images(api, setup):
    _, ctx, sources = setup
    sources["live-a"] = api.SourceRef.from_wire(live_wire())
    sources["live-b"] = api.SourceRef.from_wire(live_wire("live-b", "o4"))
    registry = api.Presentations(resolve_source=lambda ref: sources[ref.key], app_run="run-a", max_windows=3, max_live=1)
    registry.connect(ctx)
    live = opened(api, registry, ctx, sources["live-a"])
    with pytest.raises(api.PresentationError) as failed:
        registry.admit(action(api, "open", source=sources["live-b"]), ctx)
    assert failed.value.code == "capacity_unavailable"
    saved = opened(api, registry, ctx, sources["source-a"])
    assert registry.get(ctx.workspace_id, live).open and registry.get(ctx.workspace_id, saved).open


def test_identical_instance_ids_are_not_shared_between_workspace_arrangements(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    other = api.PresentationContext(workspace_id="workspace-b", client_id="client-b", client_epoch="epoch-b", request_id="b", owner="user")
    registry.connect(other)
    with pytest.raises(api.PresentationError) as failed:
        registry.admit(action(api, "close", instance=iid), other)
    assert failed.value.code == "target_missing"
    assert registry.get(ctx.workspace_id, iid).open


def test_commit_is_desired_state_not_proof_of_media_loaded_or_drawn(api, setup):
    registry, ctx, sources = setup
    effect = change(api, registry, ctx, "open", source=sources["source-a"])
    state = registry.get(ctx.workspace_id, effect.targets[0])
    assert effect.phase == "committed"
    assert state.observed is None


@pytest.mark.parametrize("op", ["open", "zoom"])
def test_visible_media_operations_still_require_a_draw_token(api, setup, op):
    registry, ctx, sources = setup
    if op == "open":
        effect = change(api, registry, ctx, op, source=sources["source-a"])
    else:
        iid = opened(api, registry, ctx, sources["source-a"])
        effect = change(api, registry, ctx, op, instance=iid, level=2)
    iid = effect.targets[0]
    report = observation(api, registry, ctx, effect, iid, phase="loaded", render_token="")
    result = registry.observe(ctx, (report,))
    assert result.rejected == (iid,) and result.accepted == ()


def test_delivery_replay_of_one_prepared_open_does_not_create_another_window(api, setup):
    registry, ctx, sources = setup
    prepared = registry.admit(action(api, "open", source=sources["source-a"]), ctx)
    first = registry.commit(prepared)
    replayed = registry.commit(prepared)
    assert replayed.command_id == first.command_id and replayed.targets == first.targets
    assert tuple(state.id for state in registry.list(ctx.workspace_id)) == first.targets


def test_cancelled_pending_open_cannot_commit_late(api, setup):
    registry, ctx, sources = setup
    prepared = registry.admit(action(api, "open", source=sources["source-a"]), ctx)
    registry.cancel(prepared, reason="request canceled")
    with pytest.raises(api.PresentationError) as failed:
        registry.commit(prepared)
    assert failed.value.code == "request_canceled"
    assert registry.list(ctx.workspace_id) == ()


@pytest.mark.parametrize("mismatch", ["source_key", "source_generation", "lifetime_generation", "command_id"])
def test_stale_renderer_observation_cannot_attach_to_new_source(api, setup, mismatch):
    registry, ctx, sources = setup
    effect = change(api, registry, ctx, "open", source=sources["source-a"])
    iid = effect.targets[0]
    wrong = {"source_key": "source-b", "source_generation": -1, "lifetime_generation": "old-life", "command_id": "old-command"}[mismatch]
    report = observation(api, registry, ctx, effect, iid, **{mismatch: wrong})
    result = registry.observe(ctx, (report,))
    assert result.accepted == () and result.rejected == (iid,)
    assert registry.get(ctx.workspace_id, iid).observed is None


def test_new_connection_epoch_invalidates_pending_command_and_old_observation(api, setup):
    registry, ctx, sources = setup
    effect = change(api, registry, ctx, "open", source=sources["source-a"])
    iid = effect.targets[0]
    pending = registry.admit(action(api, "zoom", instance=iid, level=3), ctx)
    reconnected = api.PresentationContext(workspace_id=ctx.workspace_id, client_id=ctx.client_id, client_epoch="epoch-new", request_id="new", owner="user")
    registry.connect(reconnected)
    with pytest.raises(api.PresentationError) as failed:
        registry.commit(pending)
    assert failed.value.code == "client_epoch_changed"
    report = observation(api, registry, ctx, effect, iid)
    result = registry.observe(ctx, (report,))
    assert result.accepted == ()


def test_loaded_hidden_media_is_not_promoted_to_visible_drawn_state(api, setup):
    registry, ctx, sources = setup
    effect = change(api, registry, ctx, "open", source=sources["source-a"])
    iid = effect.targets[0]
    registry.observe(ctx, (observation(api, registry, ctx, effect, iid, phase="loaded", visible=False),))
    observed = registry.get(ctx.workspace_id, iid).observed
    assert observed.phase == "loaded" and not observed.visible


@pytest.mark.parametrize("op,values", [("hide", {}), ("opacity", {"value": 0})])
def test_requested_hidden_state_accepts_unpainted_geometry_without_claiming_draw(api, setup, op, values):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    effect = change(api, registry, ctx, op, instance=iid, **values)
    report = observation(api, registry, ctx, effect, iid, visible=False,
                         rect=(0, 0, 0, 0), content_rect=(0, 0, 0, 0),
                         natural_size=(), render_token="")
    assert registry.observe(ctx, (report,)).accepted == (iid,)
    observed = registry.get(ctx.workspace_id, iid).observed
    assert observed.phase == "committed" and not observed.visible
    restored = change(api, registry, ctx, "restore" if op == "hide" else "opacity", instance=iid,
                      **({} if op == "hide" else {"value": 1}))
    missing = observation(api, registry, ctx, restored, iid, visible=False,
                          rect=(0, 0, 0, 0), content_rect=(0, 0, 0, 0), render_token="")
    assert registry.observe(ctx, (missing,)).rejected == (iid,)


def test_split_children_use_retained_component_sources_and_parent_waits_for_draw(api, setup):
    registry, ctx, sources = setup
    parent = opened(api, registry, ctx, sources["comparison"])
    effect = change(api, registry, ctx, "split", instance=parent)
    assert len(set(effect.targets)) == 2
    assert tuple(registry.get(ctx.workspace_id, iid).source.key for iid in effect.targets) == ("source-a", "source-b")
    assert registry.get(ctx.workspace_id, parent).open
    registry.observe(ctx, tuple(observation(api, registry, ctx, effect, iid, phase="loaded") for iid in effect.targets))
    with pytest.raises(api.PresentationError) as failed:
        registry.retire_group(effect, ctx)
    assert failed.value.code == "group_not_drawn"
    assert registry.get(ctx.workspace_id, parent).open
    registry.observe(ctx, tuple(observation(api, registry, ctx, effect, iid) for iid in effect.targets))
    retired = registry.retire_group(effect, ctx)
    assert not registry.get(ctx.workspace_id, parent).open
    assert retired.targets == effect.targets
    assert all(registry.get(ctx.workspace_id, iid).open for iid in effect.targets)


def test_two_drawn_panes_in_one_root_cannot_retire_split_parent(api, setup):
    registry, ctx, sources = setup
    parent = opened(api, registry, ctx, sources["comparison"])
    effect = change(api, registry, ctx, "split", instance=parent)
    registry.observe(ctx, tuple(observation(api, registry, ctx, effect, iid, root_id="same-panel-root") for iid in effect.targets))
    with pytest.raises(api.PresentationError) as failed:
        registry.retire_group(effect, ctx)
    assert failed.value.code == "group_not_independent"
    assert registry.get(ctx.workspace_id, parent).open


def test_composite_without_component_bytes_stays_open_and_is_not_guessed_split(api, setup):
    registry, ctx, sources = setup
    legacy = api.SourceRef.from_wire({
        "key": "legacy", "kind": "composite", "media": "derived",
        "identity": {"resource": {"day": "2026-10-08", "id": "d2", "version": None, "picture": 0}},
        "provenance": {"components": "not-retained"}, "availability": "retained",
    })
    sources[legacy.key] = legacy
    parent = opened(api, registry, ctx, legacy)
    with pytest.raises(api.PresentationError) as failed:
        registry.admit(action(api, "split", instance=parent), ctx)
    assert failed.value.code == "components_unavailable"
    assert tuple(state.id for state in registry.list(ctx.workspace_id)) == (parent,)
    assert registry.get(ctx.workspace_id, parent).open


def test_one_missing_split_component_creates_no_partial_children(api, setup):
    registry, ctx, sources = setup
    parent = opened(api, registry, ctx, sources["comparison"])
    del sources["source-b"]
    with pytest.raises(api.PresentationError) as failed:
        registry.admit(action(api, "split", instance=parent), ctx)
    assert failed.value.code == "source_missing"
    assert tuple(state.id for state in registry.list(ctx.workspace_id)) == (parent,)


def test_parent_user_move_blocks_split_retirement_and_cleanup_preserves_touched_child(api, setup):
    registry, ctx, sources = setup
    parent = opened(api, registry, ctx, sources["comparison"])
    effect = change(api, registry, ctx, "split", instance=parent)
    a, b = effect.targets
    registry.observe(ctx, tuple(observation(api, registry, ctx, effect, iid) for iid in effect.targets))
    user = user_context(api, ctx)
    change(api, registry, user, "move", instance=parent, position=(200, 120))
    change(api, registry, user, "zoom", instance=a, level=2)
    with pytest.raises(api.PresentationError) as failed:
        registry.retire_group(effect, ctx)
    assert failed.value.code == "target_superseded"
    cleanup = registry.rollback(effect, ctx)
    assert cleanup.preserved == (a,)
    assert registry.get(ctx.workspace_id, parent).open
    assert registry.get(ctx.workspace_id, a).open and registry.get(ctx.workspace_id, a).view.zoom == 2
    assert not registry.get(ctx.workspace_id, b).open


def test_rollback_restores_only_untouched_owned_property(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    moved = change(api, registry, ctx, "move", instance=iid, position=(100, 60))
    user = user_context(api, ctx)
    change(api, registry, user, "move", instance=iid, position=(200, 120))
    registry.rollback(moved, ctx)
    assert tuple(registry.get(ctx.workspace_id, iid).geometry_intent.position) == (200, 120)
    zoomed = change(api, registry, ctx, "zoom", instance=iid, level=3)
    change(api, registry, user, "front", instance=iid)
    registry.rollback(zoomed, ctx)
    state = registry.get(ctx.workspace_id, iid)
    assert state.view.zoom == 1
    assert tuple(state.geometry_intent.position) == (200, 120)
    assert state.owners["front"] == "user"


def test_restart_restores_saved_identity_and_view_but_old_live_is_unavailable(api, setup):
    registry, ctx, sources = setup
    sources["live-a"] = api.SourceRef.from_wire(live_wire())
    saved = opened(api, registry, user_context(api, ctx), sources["source-a"])
    live = opened(api, registry, ctx, sources["live-a"])
    change(api, registry, user_context(api, ctx), "zoom", instance=saved, level=2)
    snapshot = registry.snapshot(ctx.workspace_id)
    # Minimal local preference is JSON data, never embedded pixel/media payloads.
    serialized = json.dumps(snapshot)
    assert "jpeg" not in serialized and "base64" not in serialized
    restored = api.Presentations(resolve_source=lambda ref: sources[ref.key], app_run="run-b", max_windows=12, max_live=4)
    restored.connect(ctx)
    restored.restore_snapshot(copy.deepcopy(snapshot), ctx)
    assert restored.get(ctx.workspace_id, saved).source.to_wire() == sources["source-a"].to_wire()
    assert restored.get(ctx.workspace_id, saved).view.zoom == 2
    assert restored.get(ctx.workspace_id, live).source.to_wire()["identity"]["run"] == "run-a"
    assert restored.get(ctx.workspace_id, live).source.availability == "earlier-run"
    assert restored.get(ctx.workspace_id, live).observed is None


@pytest.mark.parametrize("level", [True, float("nan"), float("inf"), 0, -2])
def test_invalid_zoom_refuses_without_mutating_instance(api, setup, level):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    before = registry.get(ctx.workspace_id, iid).revision
    with pytest.raises(api.PresentationError) as failed:
        registry.admit(action(api, "zoom", instance=iid, level=level), ctx)
    assert failed.value.code == "invalid_action"
    assert registry.get(ctx.workspace_id, iid).revision == before
    assert registry.get(ctx.workspace_id, iid).view.zoom == 1


def test_same_valued_user_edit_takes_ownership_and_invalidates_pending_edit(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    before = registry.get(ctx.workspace_id, iid).property_versions["zoom"]
    pending = registry.admit(action(api, "zoom", instance=iid, level=3), ctx)
    change(api, registry, user_context(api, ctx), "zoom", instance=iid, level=1)
    with pytest.raises(api.PresentationError) as failed:
        registry.commit(pending)
    assert failed.value.code == "target_superseded"
    state = registry.get(ctx.workspace_id, iid)
    assert state.view.zoom == 1 and state.owners["zoom"] == "user"
    assert state.property_versions["zoom"] > before


def test_pending_source_replacement_reserves_live_lens_capacity(api, setup):
    registry, ctx, sources = setup
    registry.max_live = 1
    sources["live-a"] = api.SourceRef.from_wire(live_wire())
    sources["live-b"] = api.SourceRef.from_wire(live_wire("live-b", "o4"))
    iid = opened(api, registry, ctx, sources["source-a"])
    pending = registry.admit(action(api, "open", instance=iid, source=sources["live-a"]), ctx)
    with pytest.raises(api.PresentationError) as failed:
        registry.admit(action(api, "open", source=sources["live-b"]), ctx)
    assert failed.value.code == "capacity_unavailable"
    registry.commit(pending)
    assert registry.get(ctx.workspace_id, iid).source.key == "live-a"


def test_connection_epoch_is_distinct_from_page_epoch_and_releases_pending_lease(api):
    source = api.SourceRef.from_wire(saved_wire())
    releases = []
    registry = api.Presentations(resolve_source=lambda ref: api.ResolvedSource(source, lambda: releases.append(ref.key)), app_run="run-a")
    ctx = api.PresentationContext("workspace-a", "client-a", "page-a", "request-a", "request:request-a", connection_epoch="socket-a")
    registry.connect(ctx)
    pending = registry.admit(action(api, "open", source=source), ctx)
    newer = api.PresentationContext("workspace-a", "client-a", "page-a", "request-b", "user", connection_epoch="socket-b")
    registry.connect(newer)
    assert releases == [source.key]
    with pytest.raises(api.PresentationError) as failed:
        registry.commit(pending)
    assert failed.value.code == "client_epoch_changed"
    assert registry.list(ctx.workspace_id) == ()


def test_old_command_cannot_claim_newer_user_revision(api, setup):
    registry, ctx, sources = setup
    effect = change(api, registry, ctx, "open", source=sources["source-a"])
    iid = effect.targets[0]
    change(api, registry, user_context(api, ctx), "zoom", instance=iid, level=2)
    forged = observation(api, registry, ctx, effect, iid)
    assert registry.observe(ctx, (forged,)).rejected == (iid,)
    assert registry.get(ctx.workspace_id, iid).observed is None


def test_split_draws_from_separate_reports_do_not_form_coherent_group(api, setup):
    registry, ctx, sources = setup
    parent = opened(api, registry, ctx, sources["comparison"])
    effect = change(api, registry, ctx, "split", instance=parent)
    for iid in effect.targets:
        registry.observe(ctx, (observation(api, registry, ctx, effect, iid),))
    with pytest.raises(api.PresentationError) as failed:
        registry.retire_group(effect, ctx)
    assert failed.value.code == "group_not_coherent"
    assert registry.get(ctx.workspace_id, parent).open


def test_retirement_has_causal_close_receipt_and_split_rollback_restores_parent(api, setup):
    registry, ctx, sources = setup
    parent = opened(api, registry, ctx, sources["comparison"])
    effect = change(api, registry, ctx, "split", instance=parent)
    registry.observe(ctx, tuple(observation(api, registry, ctx, effect, iid) for iid in effect.targets))
    retired = registry.retire_group(effect, ctx)
    assert retired.phase == "committed" and retired.retirement.targets == (parent,)
    assert retired.retirement.command_id != effect.command_id
    assert registry.get(ctx.workspace_id, parent).observed is None
    closed = observation(api, registry, ctx, retired.retirement, parent, phase="closed", visible=False, connected=False,
                         root_id="", render_token="", rect=(0, 0, 0, 0), content_rect=(0, 0, 0, 0))
    assert registry.observe(ctx, (closed,)).accepted == (parent,)
    result = registry.rollback(retired, ctx)
    assert parent in result.restored and result.errors == ()
    assert registry.get(ctx.workspace_id, parent).open
    assert registry.get(ctx.workspace_id, parent).source.key == "comparison"
    assert registry.get(ctx.workspace_id, parent).observed is None
    assert all(not registry.get(ctx.workspace_id, iid).open for iid in effect.targets)


def test_canceled_request_can_cleanup_untouched_created_window(api):
    source = api.SourceRef.from_wire(saved_wire())
    canceled = []
    registry = api.Presentations(resolve_source=lambda ref: source, app_run="run-a", check_request=lambda ctx: "canceled" if canceled else None)
    ctx = api.PresentationContext("workspace-a", "client-a", "page-a", "request-a", "request:request-a")
    registry.connect(ctx)
    effect = change(api, registry, ctx, "open", source=source)
    canceled.append(True)
    result = registry.rollback(effect, ctx)
    assert result.restored == effect.targets
    assert not registry.get(ctx.workspace_id, effect.targets[0]).open


def test_resource_lease_transfers_once_and_replay_close_does_not_release_twice(api):
    source = api.SourceRef.from_wire(saved_wire())
    releases = []
    registry = api.Presentations(resolve_source=lambda ref: api.ResolvedSource(source, lambda: releases.append(ref.key)), app_run="run-a")
    ctx = api.PresentationContext("workspace-a", "client-a", "page-a", "request-a", "request:request-a")
    registry.connect(ctx)
    pending = registry.admit(action(api, "open", source=source), ctx)
    effect = registry.commit(pending)
    assert registry.commit(pending) == effect and releases == []
    close = registry.admit(action(api, "close", instance=effect.targets[0]), ctx)
    registry.commit(close)
    registry.commit(close)
    assert releases == [source.key]


def test_failed_store_cleanup_is_retained_as_bounded_diagnostic(api):
    source = api.SourceRef.from_wire(saved_wire())

    def failed_release():
        raise RuntimeError("private source path must not be logged")

    registry = api.Presentations(resolve_source=lambda ref: api.ResolvedSource(source, failed_release), app_run="run-a")
    ctx = api.PresentationContext("workspace-a", "client-a", "page-a", "request-a", "request:request-a")
    registry.connect(ctx)
    iid = opened(api, registry, ctx, source)
    change(api, registry, ctx, "close", instance=iid)
    assert registry.cleanup_errors == ("RuntimeError",)
    assert not registry.get(ctx.workspace_id, iid).open


def test_replacement_rollback_preserves_user_zoom_on_new_source(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    effect = change(api, registry, ctx, "open", instance=iid, source=sources["source-b"])
    change(api, registry, user_context(api, ctx), "zoom", instance=iid, level=3)
    result = registry.rollback(effect, ctx)
    assert result.preserved == (iid,)
    state = registry.get(ctx.workspace_id, iid)
    assert state.source.key == "source-b" and state.view.zoom == 3


def test_replacement_rollback_does_not_reconstruct_missing_original(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    effect = change(api, registry, ctx, "open", instance=iid, source=sources["source-b"])
    del sources["source-a"]
    result = registry.rollback(effect, ctx)
    assert result.errors == ((iid, "source_missing"),) and result.preserved == (iid,)
    state = registry.get(ctx.workspace_id, iid)
    assert state.open and state.source.key == "source-b"


def test_restore_malformed_snapshot_is_atomic_and_releases_prepared_sources(api):
    source = api.SourceRef.from_wire(saved_wire())
    releases = []
    registry = api.Presentations(resolve_source=lambda ref: api.ResolvedSource(source, lambda: releases.append(ref.key)), app_run="run-a")
    ctx = api.PresentationContext("workspace-a", "client-a", "page-a", "request-a", "request:request-a")
    registry.connect(ctx)
    snapshot = {"version": 1, "workspaceId": ctx.workspace_id, "items": [{
        "id": "w-627623e8-c7d5-4805-9146-8dc6c0592f05", "source": source.to_wire(),
        "view": {"zoom": 1, "pan": [0.5, 0.5]}, "geometry": {"position": [0, 0]}, "owners": [],
    }]}
    with pytest.raises(api.PresentationError) as failed:
        registry.restore_snapshot(snapshot, ctx)
    assert failed.value.code == "invalid_snapshot"
    assert registry.list(ctx.workspace_id) == () and releases == [source.key]


def test_source_change_invalidates_pending_geometry_even_when_geometry_version_unchanged(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    before = registry.get(ctx.workspace_id, iid).property_versions["geometry"]
    pending = registry.admit(action(api, "move", instance=iid, position=(100, 100)), ctx)
    change(api, registry, user_context(api, ctx), "open", instance=iid, source=sources["source-b"])
    assert registry.get(ctx.workspace_id, iid).property_versions["geometry"] == before
    with pytest.raises(api.PresentationError) as failed:
        registry.commit(pending)
    assert failed.value.code == "target_superseded"
    assert registry.get(ctx.workspace_id, iid).geometry_intent.position == (0, 0)


def test_observation_of_zoom_survives_later_unrelated_front_property(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, sources["source-a"])
    zoom = change(api, registry, ctx, "zoom", instance=iid, level=3)
    change(api, registry, user_context(api, ctx), "front", instance=iid)
    observed = observation(api, registry, ctx, zoom, iid)
    assert observed.revision > zoom.expected[0].revision
    assert registry.observe(ctx, (observed,)).accepted == (iid,)
    assert registry.get(ctx.workspace_id, iid).view.zoom == 3


def test_composite_snapshot_omits_child_locators_but_restores_exact_producers(api, setup):
    registry, ctx, sources = setup
    wire = sources["comparison"].to_wire()
    wire["locator"] = "temporary-parent-transport"
    wire["components"][0]["locator"] = "temporary-child-transport"
    sources["comparison"] = api.SourceRef.from_wire(wire)
    iid = opened(api, registry, ctx, sources["comparison"])
    snapshot = registry.snapshot(ctx.workspace_id)
    assert "locator" not in snapshot["items"][0]["source"]
    assert "locator" not in snapshot["items"][0]["source"]["components"][0]
    restored = api.Presentations(resolve_source=lambda ref: sources[ref.key], app_run="run-a")
    restored.connect(ctx)
    restored.restore_snapshot(snapshot, ctx)
    assert restored.get(ctx.workspace_id, iid).source.components[0].identity["member"] == "c17"
    assert restored.get(ctx.workspace_id, iid).source.same_identity(sources["comparison"])


@pytest.mark.parametrize("max_live", [1, 4])
@pytest.mark.parametrize("finish", ["cancel", "supersede"])
def test_pending_live_demotion_cannot_release_capacity_before_commit(api, setup, max_live, finish):
    _, ctx, sources = setup
    registry = api.Presentations(resolve_source=lambda ref: sources[ref.key], app_run="run-a", max_live=max_live)
    registry.connect(ctx)
    live_ids = []
    for n in range(max_live + 1):
        ref = api.SourceRef.from_wire(live_wire(f"live-{n}", f"o{n}"))
        sources[ref.key] = ref
        if n < max_live:
            live_ids.append(opened(api, registry, ctx, ref))
    target = live_ids[0]
    demotion = registry.admit(action(api, "open", instance=target, source=sources["source-a"]), ctx)
    # The old live source remains active; a future release is not a reservation credit.
    with pytest.raises(api.PresentationError) as refused:
        registry.admit(action(api, "open", source=sources[f"live-{max_live}"]), ctx)
    assert refused.value.code == "capacity_unavailable"
    assert sum(s.open and s.source.kind == "live" for s in registry.list(ctx.workspace_id)) == max_live
    if finish == "cancel":
        registry.cancel(demotion, reason="user canceled replacement")
        code = "request_canceled"
    else:
        change(api, registry, user_context(api, ctx), "open", instance=target, source=sources["live-0"])
        code = "target_superseded"
    with pytest.raises(api.PresentationError) as stopped:
        registry.commit(demotion)
    assert stopped.value.code == code
    with pytest.raises(api.PresentationError) as still_full:
        registry.admit(action(api, "open", source=sources[f"live-{max_live}"]), ctx)
    assert still_full.value.code == "capacity_unavailable"
    # Only an actual replacement commit releases the old lens, without evicting siblings.
    change(api, registry, ctx, "open", instance=target, source=sources["source-a"])
    newcomer = opened(api, registry, ctx, sources[f"live-{max_live}"])
    assert registry.get(ctx.workspace_id, target).source.key == "source-a"
    assert registry.get(ctx.workspace_id, newcomer).open
    assert all(registry.get(ctx.workspace_id, iid).open for iid in live_ids)
    assert sum(s.open and s.source.kind == "live" for s in registry.list(ctx.workspace_id)) == max_live


@pytest.mark.parametrize("overlap", ["same-target", "different-target"])
def test_overlapping_pending_live_demotions_cannot_credit_active_lenses(api, setup, overlap):
    _, ctx, sources = setup
    registry = api.Presentations(resolve_source=lambda ref: sources[ref.key], app_run="run-a", max_live=2)
    registry.connect(ctx)
    for n in range(4):
        sources[f"live-{n}"] = api.SourceRef.from_wire(live_wire(f"live-{n}", f"o{n}"))
    a = opened(api, registry, ctx, sources["live-0"])
    b = opened(api, registry, ctx, sources["live-1"])
    first = registry.admit(action(api, "open", instance=a, source=sources["source-a"]), ctx)
    other_target = a if overlap == "same-target" else b
    other = registry.admit(action(api, "open", instance=other_target, source=sources["source-b"]), ctx)
    with pytest.raises(api.PresentationError) as refused:
        registry.admit(action(api, "open", source=sources["live-2"]), ctx)
    assert refused.value.code == "capacity_unavailable"
    registry.commit(first)
    if overlap == "same-target":
        with pytest.raises(api.PresentationError) as stale:
            registry.commit(other)
        assert stale.value.code == "target_superseded"
    else:
        registry.cancel(other, reason="keep the second lens live")
    c = opened(api, registry, ctx, sources["live-2"])
    with pytest.raises(api.PresentationError) as full:
        registry.admit(action(api, "open", source=sources["live-3"]), ctx)
    assert full.value.code == "capacity_unavailable"
    assert registry.get(ctx.workspace_id, a).source.key == "source-a"
    assert registry.get(ctx.workspace_id, b).source.key == "live-1"
    assert registry.get(ctx.workspace_id, c).source.key == "live-2"


def test_pending_live_promotions_still_reserve_capacity_until_cancel_or_commit(api, setup):
    _, ctx, sources = setup
    registry = api.Presentations(resolve_source=lambda ref: sources[ref.key], app_run="run-a", max_live=2)
    registry.connect(ctx)
    for n in range(3):
        sources[f"live-{n}"] = api.SourceRef.from_wire(live_wire(f"live-{n}", f"o{n}"))
    a = opened(api, registry, ctx, sources["source-a"])
    b = opened(api, registry, ctx, sources["source-b"])
    first = registry.admit(action(api, "open", instance=a, source=sources["live-0"]), ctx)
    canceled = registry.admit(action(api, "open", instance=b, source=sources["live-1"]), ctx)
    with pytest.raises(api.PresentationError) as reserved:
        registry.admit(action(api, "open", source=sources["live-2"]), ctx)
    assert reserved.value.code == "capacity_unavailable"
    registry.cancel(canceled, reason="keep the saved sibling")
    c = opened(api, registry, ctx, sources["live-2"])
    registry.commit(first)
    assert registry.get(ctx.workspace_id, a).source.key == "live-0"
    assert registry.get(ctx.workspace_id, b).source.key == "source-b"
    assert registry.get(ctx.workspace_id, c).source.key == "live-2"


def geometry_effect(api, registry, ctx, op, a, b):
    if op == "move":
        return change(api, registry, ctx, op, instance=a, position=(100, 60))
    if op == "resize":
        return change(api, registry, ctx, op, instance=a, size=(360, 240))
    return change(api, registry, ctx, op, targets=[a, b], relation={"kind": "row", "gapPx": 20, "align": "top"})


@pytest.mark.parametrize("op", ["move", "resize", "arrange"])
@pytest.mark.parametrize("replacement", ["new-source", "renewed-lifetime"])
def test_old_geometry_rollback_preserves_new_source_or_lifetime_and_valid_sibling(api, setup, op, replacement):
    registry, ctx, sources = setup
    a = opened(api, registry, ctx, sources["source-a"])
    b = opened(api, registry, ctx, sources["source-a"])
    sibling_before = registry.get(ctx.workspace_id, b).geometry_intent
    effect = geometry_effect(api, registry, ctx, op, a, b)
    old = registry.get(ctx.workspace_id, a)
    if replacement == "new-source":
        change(api, registry, user_context(api, ctx), "open", instance=a, source=sources["source-b"])
    else:
        close = change(api, registry, ctx, "close", instance=a)
        reopened = registry.rollback(close, ctx)
        assert reopened.restored == (a,) and reopened.errors == ()
    current = registry.get(ctx.workspace_id, a)
    # No geometry edit/version or ownership transfer hides the source/lifetime defect.
    assert current.geometry_intent == old.geometry_intent
    assert current.property_versions["geometry"] == old.property_versions["geometry"]
    assert current.owners["geometry"] == old.owners["geometry"]
    assert current.source_generation != old.source_generation
    if replacement == "new-source":
        assert current.source.key == "source-b"
        assert current.lifetime_generation == old.lifetime_generation
    else:
        assert current.source.key == "source-a"
        assert current.lifetime_generation != old.lifetime_generation
    restored = registry.rollback(effect, ctx)
    after = registry.get(ctx.workspace_id, a)
    assert after.geometry_intent == current.geometry_intent
    assert after.property_versions["geometry"] == current.property_versions["geometry"]
    assert after.revision == current.revision and after.source.same_identity(current.source)
    assert a in restored.preserved and restored.errors == ((a, "target_superseded"),)
    if op == "arrange":
        assert restored.restored == (b,)
        assert registry.get(ctx.workspace_id, b).geometry_intent == sibling_before
    else:
        assert restored.restored == ()
        assert registry.get(ctx.workspace_id, b).geometry_intent == sibling_before


@pytest.mark.parametrize("op", ["move", "resize", "arrange"])
def test_geometry_rollback_with_unchanged_committed_source_and_lifetime_remains_valid(api, setup, op):
    registry, ctx, sources = setup
    a = opened(api, registry, ctx, sources["source-a"])
    b = opened(api, registry, ctx, sources["source-a"])
    before = {iid: registry.get(ctx.workspace_id, iid).geometry_intent for iid in (a, b)}
    effect = geometry_effect(api, registry, ctx, op, a, b)
    restored = registry.rollback(effect, ctx)
    assert restored.restored == effect.targets and restored.preserved == () and restored.errors == ()
    for iid in effect.targets:
        assert registry.get(ctx.workspace_id, iid).geometry_intent == before[iid]


def test_rollback_own_source_replacement_uses_its_rightful_committed_fence(api, setup):
    registry, ctx, sources = setup
    a = opened(api, registry, ctx, sources["source-a"])
    change(api, registry, user_context(api, ctx), "zoom", instance=a, level=2)
    before = registry.get(ctx.workspace_id, a)
    replacement = change(api, registry, ctx, "open", instance=a, source=sources["source-b"])
    committed = registry.get(ctx.workspace_id, a)
    assert committed.source.key == "source-b" and committed.source_generation != before.source_generation
    restored = registry.rollback(replacement, ctx)
    after = registry.get(ctx.workspace_id, a)
    assert restored.restored == (a,) and restored.preserved == () and restored.errors == ()
    assert after.source.same_identity(before.source) and after.view == before.view
    assert after.geometry_intent == before.geometry_intent and after.source_generation != committed.source_generation


@pytest.mark.parametrize("op", ["open", "duplicate", "split", "replacement"])
def test_contextual_source_leases_use_each_actual_admit_origin_and_request(api, setup, op):
    """Synthetic native-grant seam; actual core admission/commit/close own leases."""
    _, _, sources = setup
    a = api.PresentationContext("workspace-a", "client-a", "page-a", "seed-a", "seed:a", "socket-a")
    b = api.PresentationContext("workspace-b", "client-b", "page-b", "seed-b", "seed:b", "socket-b")
    # Same canonical source, distinct origin pins. This is not a latest-source map.
    holds = {(origin.client, source): 0 for origin in (a, b) for source in sources.values()}
    permitted = {a.request_id: a, b.request_id: b}

    def unscoped(ref):
        raise AssertionError("contextual admission must not borrow an unscoped origin pin")

    def scoped(ref, ctx):
        if permitted.get(ctx.request_id) != ctx or (ctx.client, ref) not in holds:
            raise api.PresentationError("context_mismatch", "no exact prepared grant for this admission")
        pin = (ctx.client, ref)
        holds[pin] += 1

        def release():
            assert holds[pin] > 0, "each borrowed origin lease releases only once"
            holds[pin] -= 1

        return api.ResolvedSource(ref, release)

    registry = api.Presentations(resolve_source=unscoped, resolve_source_for=scoped, app_run="run-a")
    registry.connect(a)
    registry.connect(b)  # the latest registration is deliberately the other origin
    initial = sources["comparison"] if op == "split" else sources["source-a"]
    originals = {ctx.workspace_id: opened(api, registry, ctx, initial) for ctx in (a, b)}
    effects = []
    for ctx in (a, b):
        actual = api.PresentationContext(ctx.workspace_id, ctx.client_id, ctx.client_epoch,
                                         "operation-" + ctx.client_id, "operation:" + ctx.client_id,
                                         ctx.connection_epoch)
        permitted.clear()  # neither an old request nor the other origin can borrow here
        permitted[actual.request_id] = actual
        iid = originals[ctx.workspace_id]
        if op == "open":
            effect = change(api, registry, actual, "open", source=sources["source-a"])
        elif op == "replacement":
            effect = change(api, registry, actual, "open", instance=iid, source=sources["source-b"])
        else:
            effect = change(api, registry, actual, op, instance=iid)
        assert effect.context == actual
        expected = ["source-a", "source-b"] if op == "split" else ["source-b" if op == "replacement" else "source-a"]
        assert [registry.get(ctx.workspace_id, target).source.key for target in effect.targets] == expected
        assert all(registry.get(ctx.workspace_id, target).owners["source"] == actual.owner
                   for target in effect.targets)
        effects.append((actual, effect))
    # The first origin cannot release the equally named second origin's pin.
    peer_before = {source: holds[(b.client, source)] for source in sources.values()}
    for ctx, effect in effects:
        for target in effect.targets:
            change(api, registry, ctx, "close", instance=target)
        if ctx.client == a.client:
            assert {source: holds[(b.client, source)] for source in sources.values()} == peer_before
            assert all(registry.get(b.workspace_id, target).open for target in effects[1][1].targets)
    for ctx in (a, b):
        original = originals[ctx.workspace_id]
        if registry.get(ctx.workspace_id, original).open:
            change(api, registry, ctx, "close", instance=original)
    assert all(count == 0 for count in holds.values())


@pytest.mark.parametrize("op", ["open", "duplicate", "split", "replacement"])
def test_legacy_one_argument_source_resolver_remains_usable_for_all_source_admissions(api, setup, op):
    registry, ctx, sources = setup  # setup's resolver accepts only ref
    initial = sources["comparison"] if op == "split" else sources["source-a"]
    original = opened(api, registry, ctx, initial)
    if op == "open":
        effect = change(api, registry, ctx, "open", source=sources["source-a"])
    elif op == "replacement":
        effect = change(api, registry, ctx, "open", instance=original, source=sources["source-b"])
    else:
        effect = change(api, registry, ctx, op, instance=original)
    expected = ["source-a", "source-b"] if op == "split" else ["source-b" if op == "replacement" else "source-a"]
    assert [registry.get(ctx.workspace_id, target).source.key for target in effect.targets] == expected
    assert all(registry.get(ctx.workspace_id, target).open for target in effect.targets)


def test_window_group_visibility_and_opacity_preserve_sources(api, setup):
    registry, ctx, sources = setup
    a = change(api, registry, ctx, 'open', source=sources['source-a']).targets[0]
    b = change(api, registry, ctx, 'open', source=sources['source-b']).targets[0]
    change(api, registry, ctx, 'move', instance=b, position=[400, 40])
    change(api, registry, ctx, 'group', targets=[a, b])
    moved = change(api, registry, ctx, 'move', instance=a, position=[100, 80])
    assert set(moved.targets) == {a, b}
    assert registry.get(ctx.workspace_id, b).geometry_intent.position == (500, 120)
    change(api, registry, ctx, 'opacity', instance=a, value=.5)
    change(api, registry, ctx, 'hide', instance=a)
    hidden = registry.get(ctx.workspace_id, a)
    assert hidden.open and hidden.view.hidden and hidden.view.opacity == .5
    assert hidden.source == sources['source-a']
    change(api, registry, ctx, 'restore', instance=a)
    assert not registry.get(ctx.workspace_id, a).view.hidden
    change(api, registry, ctx, 'ungroup', instance=a)
    assert registry.get(ctx.workspace_id, b).view.group is None


def test_reconnect_preserves_windows_view_and_source_but_renews_lifetime(api, setup):
    registry, ctx, sources = setup
    first = opened(api, registry, ctx, sources['source-a'])
    second = opened(api, registry, ctx, sources['source-b'])
    # Reconnecting a full workspace replaces its leases, not its window count.
    for _ in range(registry.max_windows - 2):
        opened(api, registry, ctx, sources['source-a'])
    change(api, registry, ctx, 'group', targets=[first, second])
    change(api, registry, ctx, 'opacity', instance=first, value=.5)
    change(api, registry, ctx, 'hide', instance=second)
    original = registry.get(ctx.workspace_id, first)
    saved = registry.snapshot(ctx.workspace_id)
    from dataclasses import replace
    resumed = replace(ctx, client_epoch='new-page', request_id='reconnect', owner='restore')
    registry.connect(resumed)
    registry.restore_snapshot(saved, resumed, reconnect=True)
    effect = registry.reconnect_effect(resumed, first)
    restored = registry.get(ctx.workspace_id, first)
    assert restored.source.same_identity(original.source)
    assert restored.view == original.view
    assert restored.lifetime_generation != original.lifetime_generation
    assert restored.owners['source'] == 'restore'
    assert effect.targets == (first,)
    assert registry.get(ctx.workspace_id, second).view.hidden
    assert registry.get(ctx.workspace_id, second).view.group == restored.view.group
    assert len(registry.list(ctx.workspace_id)) == registry.max_windows


def test_fit_atomically_resets_pan_zoom_and_optional_frame_size(api, setup):
    registry, ctx, sources = setup
    iid = opened(api, registry, ctx, next(iter(sources.values())))
    change(api, registry, ctx, 'zoom', instance=iid, level=3)
    change(api, registry, ctx, 'pan', instance=iid, center=[0.2, 0.8])
    before = registry.get(ctx.workspace_id, iid)
    change(api, registry, ctx, 'zoom', instance=iid, fit=True, size=[420, 252])
    after = registry.get(ctx.workspace_id, iid)
    assert after.view.zoom == 1 and after.view.pan == (0.5, 0.5)
    assert after.geometry_intent.size == (420, 252)
    for prop in ('zoom', 'pan', 'geometry'):
        assert after.property_versions[prop] > before.property_versions[prop]


def test_fit_snapshot_does_not_restore_size_from_an_older_observation(api, setup):
    registry, ctx, sources = setup
    effect = change(api, registry, ctx, 'open', source=next(iter(sources.values())))
    iid = effect.targets[0]
    report = observation(api, registry, ctx, effect, iid)
    assert registry.observe(ctx, (report,)).accepted == (iid,)
    change(api, registry, ctx, 'zoom', instance=iid, fit=True, size=[420, 252])
    saved = registry.snapshot(ctx.workspace_id)['items'][0]
    assert saved['geometry']['size'] == [420, 252]
    assert saved['geometry']['position'] == list(report.rect[:2])
    assert saved['view']['zoom'] == 1 and saved['view']['pan'] == [0.5, 0.5]


def test_manual_fit_transport_preserves_atomic_frame_size():
    from orchestrator.commands import _manual_proposal
    from orchestrator.workspace_observation import ObservationError
    packet = {'type': 'present', 'schema': 'presentation-manual-v2', 'manualSeq': 1,
              'op': 'zoom', 'targets': ['window-a'], 'values': {'fit': True, 'size': [420, 252]},
              'client': {'workspaceId': 'w', 'clientId': 'c', 'pageEpoch': 'p', 'connectionEpoch': 'e'},
              'appRunId': 'run', 'requestId': 'r', 'actionId': 'a', 'commandId': 'cmd',
              'base': {'window-a': {'sourceGeneration': 1, 'lifetimeGeneration': 'life',
                                    'propertyVersions': {'zoom': 1, 'pan': 1, 'geometry': 1}}}}
    args, *_ = _manual_proposal(packet)
    assert args['zoom'] == {'fit': True, 'size': [420, 252]}
    for values in ({'factor': 2, 'size': [420, 252]}, {'fit': True, 'size': [0, 252]}, {'fit': True, 'size': [420]}):
        with pytest.raises(ObservationError):
            _manual_proposal({**packet, 'values': values})
