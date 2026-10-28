import type { ExecutionDetail, ExecutionPower, ExecutionStep } from './protocol';

const jobs: Record<string, string> = {
  brain: 'Handling the request',
  'function-calling': 'Handling the request',
  hands: 'Handling the spoken request',
  look: 'Looking at the view',
  'look-in': 'Looking inside the outline',
  name: 'Naming',
  'outline-check': 'Checking the outline',
  sight: 'Running look',
};

// Only actual identity fields match catalogue labels across calls, never selected settings.
const identity = (s: ExecutionStep): string => JSON.stringify([s.operation, s.model, s.provider, s.adapter, s.role, s.optionId]);

/** Browser projection only; these fields are not added to the recorder or wire. */
export interface ExecutionContribution extends ExecutionPower {
  objectId?: string;
  note?: string;
}

/** Presentation only, called after an explicit inspection fetch. The recorder appends
 * steps when they start; durations do not establish completion order or recovery. */
export function executionContributions(data: ExecutionDetail): ExecutionContribution[] {
  if (data.status !== 'available') return [];
  const steps = data.steps.slice(0, 64);
  const powers = (data.powers ?? []).slice(0, 64);
  if (!steps.length) return powers; // older receipts: keep their summary, without an order claim
  const byId = new Map(steps.map(s => [s.id, s]));
  const powerById = new Map(powers.map(p => [p.stepId, p]));
  function ancestors(step: ExecutionStep): ExecutionStep[] {
    const parents: ExecutionStep[] = [];
    const seen = new Set([step.id]);
    let parent = byId.get(step.parent);
    while (parent && !seen.has(parent.id)) {
      seen.add(parent.id);
      parents.push(parent);
      parent = byId.get(parent.parent);
    }
    return parents;
  }
  const parents = new Map(steps.map(s => [s.id, ancestors(s)]));
  // The server's capability title also depends on the nearest tool. Share labels by
  // model identity, but share purposes only when that recorded tool context matches.
  const jobKey = (s: ExecutionStep): string => JSON.stringify([identity(s),
    parents.get(s.id)?.find(p => p.operation.startsWith('tool:'))?.operation]);
  const labelled = new Map<string, string>();
  const purposes = new Map<string, string>();
  for (const power of powers) {
    const step = byId.get(power.stepId);
    if (step) { labelled.set(identity(step), power.label); purposes.set(jobKey(step), power.capability); }
  }
  const modelStep = (s: ExecutionStep): boolean => !!s.model || s.provider === 'algorithm';
  const modelParents = new Set(steps.filter(modelStep).flatMap(s => parents.get(s.id)!.map(p => p.id)));
  const positions = new Map(steps.map((s, i) => [s.id, i]));
  const rows: ExecutionContribution[] = [];
  for (const step of steps) {
    const tool = step.operation.startsWith('tool:');
    const reusedOutline = step.outcome === 'reused' && ['reuse-mask', 'target'].includes(step.operation);
    const unidentifiedFailure = step.operation === 'gpu' && step.outcome === 'failed' && !modelStep(step);
    // A failed tool remains an attempt even if one of its model calls completed.
    if (!modelStep(step) && !reusedOutline && !unidentifiedFailure && !(tool && (!modelParents.has(step.id) || step.outcome === 'failed'))) continue;
    const exact = powerById.get(step.id);
    const lineage = parents.get(step.id)!;
    const checking = lineage.find(p => p.operation === 'outline-check');
    let capability = exact?.capability ?? purposes.get(jobKey(step)) ?? 'Model call (purpose not recorded)';
    if (capability === 'Model') capability = 'Model call (purpose not recorded)';
    if (step.operation === 'vision' || step.operation === 'function-calling') {
      // The nearest recorded job wins. Stop at a tool boundary rather than borrowing
      // the broad brain label or another call's purpose from the deduplicated summary.
      capability = 'Model call (purpose not recorded)';
      for (const parent of [step, ...lineage]) {
        if (jobs[parent.operation]) { capability = jobs[parent.operation]; break; }
        if (parent.operation.startsWith('tool:')) {
          capability = parent.operation.slice(5).replaceAll('_', ' ');
          capability = capability[0].toUpperCase() + capability.slice(1);
          break;
        }
      }
    }
    if (tool) {
      const name = step.operation.slice(5).replaceAll('_', ' ');
      const title = name[0].toUpperCase() + name.slice(1);
      capability = step.outcome === 'failed' ? `${title} attempt` : exact?.capability ?? title;
    }
    if (reusedOutline) capability = 'Reusing an outline';
    if (unidentifiedFailure) capability = 'GPU attempt';
    // An executed role names this model's job, not the enclosing user-facing tool.
    if (step.role === 'outline') capability = checking ? 'Try outline repair' : 'Draw outline';
    if (step.role === 'embed') capability = 'Appearance fingerprint';
    const checksRepair = step.operation === 'vision' && checking && step.parent === checking.id
      && steps.some(s => s.operation === 'segment' && s.parent === checking.id && positions.get(s.id)! < positions.get(step.id)!);
    if (checksRepair) capability = 'Check repair';
    const label = unidentifiedFailure ? 'Model identity not recorded'
      : exact?.label ?? labelled.get(identity(step)) ?? step.model ?? (tool || step.provider === 'app' ? 'HookeUI' : step.operation);
    const row: ExecutionContribution = { capability, label,
      provider: step.provider ?? '', outcome: step.outcome, cache: step.cache, stepId: step.id };
    const objectId = step.objectId ?? lineage.find(p => p.objectId)?.objectId;
    if (objectId) row.objectId = objectId;
    if (checksRepair && checking.policy === 'repair-not-accepted' && checking.outcome === 'retained') {
      row.note = 'Repair not accepted; original outline kept.';
    }
    rows.push(row);
  }
  return rows;
}
