"""Bounded, original-owner retries of failed window controls and photo lookups.

This is an in-process continuation of one Registry operation, never a replay of
an ask or a durable command grant. Ambiguous dispatched effects are not retried.
"""
from collections import OrderedDict
from dataclasses import dataclass, replace
import asyncio
import copy
import time

from . import execution, ticket as tickets
from .brain.registry import Steps, ToolContext, ToolFailed, ToolOutput, ToolRun


@dataclass
class Attempt:
    root: str
    run: ToolRun
    context: ToolContext | None
    ticket: tickets.Ticket | None
    at: float
    reason: str
    args: dict
    baseline: dict | None = None
    driver: asyncio.Task[dict] | None = None


class OperationRetries:
    LIMIT = 64
    AGE = 300
    CONTROLS = {"fit", "front", "move", "resize", "opacity", "zoom", "pan"}
    MAX_PREDECESSORS = 32

    def __init__(self, hub, registry):
        self.hub, self.registry = hub, registry
        self.entries = OrderedDict()

    def _prune(self):
        for key, item in list(self.entries.items()):
            if item.driver is not None and not item.driver.done():
                continue
            if time.monotonic() - item.at > self.AGE or len(self.entries) > self.LIMIT:
                self.entries.pop(key, None)

    def capture(self, run, ctx):
        original = tickets.current()
        root = execution.root_id()
        if not root or run.occurrence is None:
            return None
        args = run.args
        target = args.get("what")
        exact = (isinstance(target, dict) and set(target) == {"instanceId"}
                 and isinstance(target["instanceId"], str) and bool(target["instanceId"]))
        supported = ((run.tool == "present" and args.get("do") in self.CONTROLS and exact)
                     or (run.tool == "reference_photo" and isinstance(args.get("subject"), str)))
        if not supported:
            return None
        reason = "This operation has no safe individual retry. Successful steps will not be repeated."
        saved = None
        baseline = None
        if original is not None and ctx.admission is not None and ctx.client is not None and ctx.request_snapshot is not None:
            saved = replace(ctx, objects=[], targets=[], aliases={}, magnified=[], inspection_slots=[],
                            steps=Steps(), step=0, seen_view=None, camera_selection=None, sight_selection=None)
            reason = ""
            # Immutable receipts of successful predecessors, never their tasks,
            # captures or a mutable continuation of the original compound plan.
            predecessors = {}
            number = run.step[0] if run.step else ctx.step
            candidates = [prior for steps in ctx.prior_steps for prior in steps.runs.values()]
            candidates.extend(prior for n, prior in ctx.steps.runs.items() if n < number)
            for prior in candidates:
                if (prior.ok is True and prior.occurrence is not None
                        and prior.occurrence.request_id == run.occurrence.request_id):
                    predecessors[prior.occurrence.action_id] = prior
            if len(predecessors) > self.MAX_PREDECESSORS:
                reason = "This request has too many predecessor receipts for a safe individual retry."
            else:
                ancestry = Steps()
                for n, prior in enumerate(predecessors.values(), 1):
                    # Only the target's presentation identity/fences are needed
                    # by native validation; never retain source payloads here.
                    outputs = []
                    for output in prior.outputs:
                        if output.get("kind") == "presentation" and exact and output.get("instanceId") == target["instanceId"]:
                            facts = output.wire()
                            outputs.append(ToolOutput.capture({key: facts[key] for key in
                                ("kind", "instanceId", "workspaceId", "lifetimeGeneration", "sourceGeneration", "propertyVersions")
                                if key in facts}))
                    ancestry.runs[n] = ToolRun(prior.tool, {}, ok=True, occurrence=prior.occurrence,
                        outputs=outputs)
                saved.prior_steps = (ancestry,)
            if run.tool == "present" and not reason:
                try:
                    self.hub.screen.request_report(ctx.request_snapshot, ctx.client,
                        request_id=ctx.admission.request_id)
                    target = args["what"]["instanceId"]
                    baseline = next((row for row in self.hub.screen.observation(ctx.client).wire()["items"]
                                     if row["id"] == target), None)
                    pinned = next((row for row in ctx.request_snapshot.observation.wire()["items"]
                                   if row["id"] == target), None)
                    if pinned is None:
                        produced = [output.wire() for prior in ancestry.runs.values() for output in prior.outputs
                                    if output.get("kind") == "presentation" and output.get("instanceId") == target
                                    and output.get("workspaceId") == ctx.workspace_id]
                        pinned = produced[-1] if produced else None
                    owners = set(predecessors)
                    changed = (baseline or {}).get("propertyOwners", {})
                    if (baseline is None or pinned is None
                            or (pinned.get("lifetimeGeneration") != baseline.get("lifetimeGeneration") and changed.get("open") not in owners)
                            or (pinned.get("sourceGeneration") != baseline.get("sourceGeneration") and changed.get("source") not in owners)
                            or any(pinned.get("propertyVersions", {}).get(prop) != version and changed.get(prop) not in owners
                                   for prop, version in baseline.get("propertyVersions", {}).items())):
                        reason = "The target changed outside this request before the failed step; retry is unavailable."
                    baseline = ({key: copy.deepcopy(baseline.get(key)) for key in
                        ("sourceGeneration", "lifetimeGeneration", "sourceKey", "propertyVersions")}
                        if baseline is not None else None)
                except Exception:
                    reason = "The failed step's original browser observation is unavailable."
        else:
            reason = "The original browser and request were not retained; retry is unavailable."
        return Attempt(root, run, saved, original, time.monotonic(), reason, copy.deepcopy(args), baseline)

    def settled(self, item):
        if item is None or item.run.ok is not False:
            return
        self._prune()
        if len(self.entries) >= self.LIMIT:
            return
        self.entries[item.run.occurrence.action_id] = item

    def _reason(self, item, client):
        if item.reason:
            return item.reason
        ctx, run, original = item.context, item.run, item.ticket
        if ctx.client is not client:
            return "Retry is only available in the original requesting browser."
        if run.ok is not False or run.outputs or run.resource or run.pictures:
            return "This step produced retained work; repeating it could duplicate an effect."
        if run.error:
            return "This step failed unexpectedly; its effects cannot be safely replayed."
        if (run.outcome and (run.outcome.get("commandId") or run.outcome.get("receipts"))) or run._facets:
            return "A window command was already issued. Check its result before making a new explicit change."
        if run.tool == "reference_photo" and run.values.get("referenceLookup") != {"status": "failed"}:
            return "Only a failed photo lookup with no retained image can be retried here."
        if original is None or original.expired() or time.monotonic() - item.at > self.AGE:
            return "The original retry context expired."
        if any(original.superseded(scope) for scope in original.issued):
            return "The original request was cancelled or its scene/day changed."
        try:
            self.hub.screen.request_report(ctx.request_snapshot, client,
                request_id=ctx.admission.request_id)
            if run.tool == "reference_photo":
                return ""  # subject, restrictions and previous sources come from the immutable original request
            target = item.args["what"]["instanceId"]
            before = item.baseline
            after = next((row for row in self.hub.screen.observation(client).wire()["items"] if row["id"] == target), None)
            if before is None or after is None or any(before.get(key) != after.get(key)
                    for key in ("sourceGeneration", "lifetimeGeneration", "sourceKey", "propertyVersions")):
                return "The original window or its controls changed; make a new explicit change instead."
        except Exception:
            return "The original browser connection or target observation is no longer available."
        return ""

    def inspect(self, root, client):
        self._prune()
        rows = []
        for action, item in self.entries.items():
            if item.root != root:
                continue
            reason = self._reason(item, client)
            rows.append({"actionId": action, "tool": item.run.tool, "available": not reason and item.driver is None,
                         "reason": reason or ("This retry was already submitted." if item.driver else ""),
                         "status": "running" if item.driver and not item.driver.done() else "settled"})
        return {"operations": rows, "unavailable": "" if rows else
                "No safely retryable failed step is retained for this receipt. Historical logs cannot authorize a retry."}

    async def retry(self, action, client):
        self._prune()
        item = self.entries.get(action)
        if item is None:
            raise ToolFailed("The original failed step is no longer retained; no work was repeated.", next=None)  # no next: historical diagnostics grant no authority to resubmit work
        if item.driver is not None and item.context.client is client:
            # Repeated transport delivery returns the same occurrence; no new
            # authority/effect is issued even after the result changes the view.
            return await asyncio.shield(item.driver)
        reason = self._reason(item, client)
        if reason:
            raise ToolFailed(reason, next=None)  # no next: the refusal explains the exact original-owner or duplicate-effect boundary
        # One driver per original failure: double clicks/replayed messages join.
        if item.driver is None:
            item.driver = asyncio.create_task(self._drive(item))
        return await asyncio.shield(item.driver)

    async def _drive(self, item):
        original, ctx = item.ticket, item.context
        request = tickets.issue(self.hub, "command", asked=ctx.asked, channel=ctx.channel)
        request.admission = ctx.admission  # original allowance and request identity
        request.issued = dict(original.issued)
        request.choices = copy.deepcopy(original.choices)
        request.overrides = dict(original.overrides)
        retry_ctx = replace(ctx, step=0)
        retry_ctx.prior_steps = ctx.prior_steps
        try:
            with tickets.using(request), execution.request(self.hub, "operation-retry"):
                execution.bind(turn_id=ctx.turn_id, words=ctx.question,
                    day=getattr(ctx.asked, "day", None), thread=getattr(ctx.asked, "thread", None),
                    generation=getattr(ctx.asked, "routing_generation", None),
                    owner=ctx.request_snapshot.observation.client.wire())
                execution.note(retryOf=item.run.occurrence.action_id)
                run = self.registry.start(item.run.tool, copy.deepcopy(item.args), retry_ctx)
                request.track(run.task)
                if run.task is not None:
                    await run.task
                if run.ok is not True:
                    execution.finish("failed")
                return {"ok": run.ok is True, "operation": run.wire(), "receiptId": execution.root_id(),
                        "error": run.summary if run.ok is not True else None}
        finally:
            request.seal()
