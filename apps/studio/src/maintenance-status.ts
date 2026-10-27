// An entry's maintenance on its card (ticket 59's status vocabulary; ADR 0025): what is happening to its next version
// in the words the orchestrator gives (maintenance/status.py), the policy a person recorded with its three choices
// (Update when I choose is the default), and the one action the state offers: Apply, Undo, Retry or Stop.
// The words are never made up here: every state's line comes from the wire.

export type UpdateState = 'none' | 'available' | 'preparing' | 'checking' | 'ready-to-apply' | 'applied' | 'needs-review'
  | 'fix-pending' | 'failed' | 'unavailable' | 'pinned';
export type Policy = 'manual' | 'review' | 'auto' | 'pin';

export interface MaintenanceUpdate {
  state: UpdateState;
  says: string;
  next: 'apply' | 'undo' | 'retry' | 'stop' | 'accept' | 'merge' | null;
  policy: { policy: Policy; words: string; effective: Policy; less?: string; held?: string; at?: string; version?: string };
  check: { state: 'ok' | 'failed' | 'stale' | 'never' | 'off'; at?: string; why?: string };
  active: { generation: string; version: string; installedAt?: string; promotedAt?: string; chosenBy?: string } | null;
  candidate: { generation: string; version: string; workflow?: boolean; digest?: string } | null;
  held: { generation: string; kind: string; owner: string; at: string }[];
  review?: { what: string; kind: string; from: string; to: string; says: string }[];
  why?: string;
  setup?: string; // a step the update newly needs before it can run here (a key, a licence)
  fingerprint?: string; // exactly what Needs review lists: Accept sends it back (maintenance-accept)
  workflowReview?: string; // exact candidate generation whose retained result needs explicit review
  prepared?: boolean; // built and proven already; accepting only promotes it
  derived?: string; // your version's merged candidate folder (maintenance/adapt.py)
  conflicts?: string[];
  maintained?: { state: 'unmaintained' | 'attention'; why?: string; since?: string } | null;
  defect?: { version: string; why: string; link: string; severity: 'warn' | 'block' } | null;
}

export interface MaintenanceCommands {
  policy: (model: string, policy: Policy) => Promise<{ says?: string }>;
  check: (model: string) => Promise<{ says?: string }>;
  apply: (model: string, receipt?: {generation:string;digest:string}) => Promise<{ says?: string }>;
  undo: (model: string) => Promise<{ says?: string }>;
  stop: (model: string) => Promise<{ says?: string }>;
  accept: (model: string, fingerprint: string) => Promise<{ says?: string }>;
  merge: (model: string) => Promise<{ says?: string }>;
}

export const POLICIES: { id: Policy; label: string }[] = [
  { id: 'manual', label: 'Update when I choose' },
  { id: 'review', label: 'Review updates' },
  { id: 'auto', label: 'Automatically maintain compatible updates' },
  { id: 'pin', label: 'Pin this version' },
];

const ACTIONS: Record<NonNullable<MaintenanceUpdate['next']>, string> = {
  apply: 'Apply', undo: 'Undo', retry: 'Retry', stop: 'Stop', accept: 'Accept and continue', merge: 'Use the merged version',
};

/** The facts kept apart from the state, each with its own date: never one ambiguous "last updated". */
export function maintenanceFacts(u: MaintenanceUpdate): string[] {
  const out: string[] = [];
  if (u.active) out.push(`Running ${u.active.version}` + (u.active.installedAt ? `, installed ${u.active.installedAt}` : '')
    + (u.active.promotedAt ? `, made active ${u.active.promotedAt}` : ''));
  const check = u.check;
  out.push(check.state === 'never' ? 'Not checked for updates yet'
    : check.state === 'off' ? 'Automatic checks are off'
      : check.state === 'failed' ? `Last check failed ${check.at ?? ''}: ${check.why ?? ''}`.trim()
        : `Last checked ${check.at ?? ''}${check.state === 'stale' ? ' (a while ago)' : ''}`);
  if (u.maintained?.state === 'unmaintained') out.push(`Not maintained${u.maintained.since ? ` since ${u.maintained.since}` : ''}: ${u.maintained.why ?? ''}`.trim());
  if (u.defect) out.push(`Known problem in ${u.defect.version}: ${u.defect.why}${u.defect.link ? ` (${u.defect.link})` : ''}`);
  if (u.setup) out.push(`Before it can run: ${u.setup}`);
  if (u.conflicts?.length && u.derived) out.push(`Resolve ${u.conflicts.join(', ')} in ${u.derived}`);
  const kept = u.held.filter((h) => h.kind !== 'rollback');
  if (kept.length) out.push(`Kept for ${kept.map((h) => `${h.kind} ${h.owner}`).join(', ')}`);
  if (u.policy.less) out.push(`For now it waits for you: ${u.policy.less}`);
  return out;
}

/** What the card shows, decided once: its line, what needs review, its one action and the policy choice. */
export function maintenanceView(u: MaintenanceUpdate): { says: string; review: string[]; action: string | null;
  policies: { id: Policy; label: string; chosen: boolean }[]; facts: string[] } {
  return {
    says: u.says, review: (u.review ?? []).map((r) => r.says), action: u.next ? ACTIONS[u.next] : null,
    policies: POLICIES.map((p) => ({ ...p, chosen: p.id === u.policy.policy })), facts: maintenanceFacts(u),
  };
}

function node(tag: string, cls: string, text = ''): HTMLElement {
  const one = document.createElement(tag);
  one.className = cls;
  if (text) one.textContent = text;
  return one;
}

/** The card's maintenance block, or null for an entry maintenance doesn't cover. */
export function maintenanceNode(model: string, u: MaintenanceUpdate | undefined, commands: MaintenanceCommands | undefined)
  : HTMLElement | null {
  if (!u || !commands) return null;
  const view = maintenanceView(u);
  const box = node('div', 'model-card-action model-maintenance');
  box.setAttribute('data-state', u.state);
  if (view.says) box.append(node('p', 'model-how model-maintenance-says', view.says));
  for (const line of view.review) box.append(node('p', 'model-how model-maintenance-review', line));
  const said = node('span', 'cap-download-why');
  said.hidden = true;
  const run = (button: HTMLButtonElement, call: () => Promise<{ says?: string }>) => {
    button.disabled = true;
    void call().then((r) => { said.textContent = r.says ?? ''; said.hidden = !r.says; })
      .catch((error: unknown) => { said.textContent = error instanceof Error ? error.message : String(error); said.hidden = false; })
      .finally(() => { button.disabled = false; });
  };
  if (u.next && view.action) {
    const action = u.next;
    const button = node('button', 'mm-control model-action-quiet', view.action) as HTMLButtonElement;
    button.type = 'button';
    button.addEventListener('click', () => run(button, () =>
      action === 'retry' ? commands.check(model)
        : action === 'accept' ? commands.accept(model, u.fingerprint ?? '') : action === 'apply' ? commands.apply(model, u.candidate?.workflow && u.candidate.digest ? {generation:u.candidate.generation,digest:u.candidate.digest} : undefined) : commands[action](model)));
    box.append(button);
  }
  const choose = node('select', 'mm-control model-maintenance-policy') as HTMLSelectElement;
  choose.setAttribute('aria-label', 'How its updates are kept');
  for (const p of view.policies) {
    const option = document.createElement('option');
    option.value = p.id;
    option.textContent = p.label;
    option.selected = p.chosen;
    choose.append(option);
  }
  choose.addEventListener('change', () => {
    choose.disabled = true;
    void commands.policy(model, choose.value as Policy).then((r) => { said.textContent = r.says ?? ''; said.hidden = !r.says; })
      .catch((error: unknown) => {
        said.textContent = error instanceof Error ? error.message : String(error); said.hidden = false;
        choose.value = u.policy.policy;
      })
      .finally(() => { choose.disabled = false; });
  });
  const check = node('button', 'mm-control model-action-quiet', 'Check for an update') as HTMLButtonElement;
  check.type = 'button';
  check.addEventListener('click', () => run(check, () => commands.check(model)));
  const details = document.createElement('details');
  details.className = 'model-maintenance-details';
  details.append(node('summary', '', 'Updates'), choose, check,
    ...view.facts.map((line) => node('p', 'model-how', line)));
  box.append(details, said);
  return box;
}
