"""One outbound projection of a committed presentation and its native handles.

This module owns no instances, sources, observations or command history. Inputs
are the immutable snapshots taken immediately after the original commit.
"""
from __future__ import annotations

import copy
import json

from .presentations import PresentationError
from .workspace_observation import ObservationError, normalize_source


def command(effect, *, op, states, deliveries, before, occurrence,
            after_report_seq, issued_mono, deadline_mono, before_report=None):
    ctx = effect.context
    if (ctx is None or occurrence.request_id != ctx.request_id
            or occurrence.action_id != ctx.owner or tuple(s.id for s in states) != effect.targets
            or not 1 <= len(states) <= 8):
        raise PresentationError("invalid_action", "command does not belong to its original occurrence")
    expected = {target.instance_id: target for target in effect.expected}
    observed_before = {row["id"]: row for row in before_report["items"]} if before_report is not None else {}
    instances, targets = [], {}
    for state in states:
        target, delivery = expected[state.id], deliveries[state.id]
        if (state.revision != target.revision or state.source_generation != target.source_generation
                or state.lifetime_generation != target.lifetime_generation
                or state.source.key != target.source_key
                or any(state.property_versions.get(k) != v for k, v in target.property_versions.items())
                or not state.source.same_identity(delivery.source)):
            raise PresentationError("target_superseded", "committed source or target changed before delivery")
        native = delivery.to_wire()
        native.pop("sourceRef")
        native["url"] = "/api/presentation-sources/" + delivery.token
        try:
            source = normalize_source(state.source.to_wire())
        except ObservationError as exc:
            raise PresentationError("source_identity_mismatch", "source has no canonical wire projection") from exc
        geometry = state.geometry_intent
        instances.append({"delivery": native, "id": state.id, "workspaceId": state.workspace_id,
                          "sourceRef": source,
                          "revision": state.revision, "sourceGeneration": state.source_generation,
                          "lifetimeGeneration": state.lifetime_generation,
                          "geometryIntent": {"position": list(geometry.position),
                              "size": list(geometry.size) if geometry.size else None,
                              "relation": json.loads(geometry.relation_json) if geometry.relation_json else None},
                          "propertyOwners": dict(state.owners),
                          "propertyVersions": dict(state.property_versions),
                          "view": {"zoom": state.view.zoom, "pan": list(state.view.pan),
                                   "opacity": state.view.opacity, "hidden": state.view.hidden, "group": state.view.group},
                          "open": state.open, "staged": state.staged, "title": state.title})
        base = before.get(state.id)
        observed = observed_before.get(state.id)
        paint = None
        # Only the originating accepted pre-command observation can supply a
        # baseline. A new target, unknown paint or a different source/property
        # lifetime stays unknown; media paint is never a fallback for view paint.
        if (base is not None and observed is not None
                and observed.get("sourceRef") == source and observed.get("sourceToken") == delivery.token
                and observed.get("lifetimeGeneration") == base.lifetime_generation
                and observed.get("sourceGeneration") == base.source_generation
                and all(observed.get("propertyVersions", {}).get("rect" if k == "geometry" else k) == v
                        and observed.get("propertyOwners", {}).get("rect" if k == "geometry" else k) == base.owners.get(k)
                        for k, v in base.property_versions.items())):
            actual = observed.get("viewDrawRef")
            if isinstance(actual, str) and 0 < len(actual) <= 256:
                paint = actual
        targets[state.id] = {"sourceRef": copy.deepcopy(source), "sourceToken": delivery.token,
                            "payloadBinding": dict(delivery.payload_binding),
                            "sourceGeneration": target.source_generation,
                            "lifetimeGeneration": target.lifetime_generation,
                            "committedRevision": target.revision,
                            "basePropertyVersions": {k: base.property_versions.get(k, 0) if base else 0
                                                     for k in target.property_versions},
                            "committedPropertyVersions": dict(target.property_versions), "owner": ctx.owner,
                            "baseViewDrawRef": paint}
    client = {"workspaceId": ctx.workspace_id, "clientId": ctx.client_id,
              "pageEpoch": ctx.client_epoch, "connectionEpoch": ctx.connection_epoch}
    fence = {"commandId": effect.command_id, "requestId": occurrence.request_id,
             "actionId": occurrence.action_id, "client": client, "appRunId": effect.app_run,
             "afterReportSeq": after_report_seq, "issuedMono": issued_mono,
             "deadlineMono": deadline_mono, "targets": targets}
    return {"type": "present", "schema": "presentation-command-v2", "op": op,
            "commandId": effect.command_id, "requestId": occurrence.request_id,
            "actionId": occurrence.action_id, "client": dict(client), "appRunId": effect.app_run,
            "targets": list(effect.targets), "instances": instances, "fence": fence}


def completion_fence(message, screen, report):
    """Use Screen's accepted completion aliases, never raw browser client fields."""
    fence = copy.deepcopy(message["fence"])
    canonical = message["client"]
    accepted = report["client"]
    if (accepted.get("workspaceId") != canonical["workspaceId"]
            or accepted.get("clientId") != canonical["clientId"]
            or accepted.get("clientEpoch") != canonical["pageEpoch"]
            or accepted.get("connectionEpoch") != canonical["connectionEpoch"]
            or report["appRunId"] != message["appRunId"]):
        raise PresentationError("target_superseded", "completion report belongs to another accepted connection")
    fence["client"] = dict(accepted)
    for target in fence["targets"].values():
        for key in ("basePropertyVersions", "committedPropertyVersions"):
            target[key] = screen._property_map(target[key])
    return fence


def command_facet(message, expected, *, step, round, goal_id):
    """Detached command attribution and narrow fact proposals, never proof.

    The producer fills confirmation from the actual accepted report. Split
    children and retirement call this separately with their own command IDs.
    """
    sources = [copy.deepcopy(state["sourceRef"]) for state in message["instances"]]
    targets = list(message["targets"])
    kind = "closed" if message["op"] == "close" else "drawn"
    # The existing prose projector has a four-source bound. Its allowance never
    # truncates the command/goal conditions or determines root fulfillment.
    fact = {"kind": kind, "actionId": message["actionId"],
            "sourceRefs": copy.deepcopy(sources[:4]), "presentationIds": targets[:4]}
    return {"requestId": message["requestId"], "actionId": message["actionId"],
            "plannedStep": step, "round": round, "operation": message["op"],
            "completionScope": "workspace", "commandIds": [message["commandId"]],
            "sourceRefs": sources, "presentationIds": targets,
            "status": "pending", "expected": copy.deepcopy(expected),
            "confirmation": None, "error": None,
            "goal": {"id": goal_id, "expected": [], "confirmation": None}, "facts": [fact]}
