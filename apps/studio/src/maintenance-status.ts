// An entry's maintenance on its card (ticket 59's status vocabulary; ADR 0025): what is happening to its next version
// in the words the orchestrator gives (maintenance/status.py), the policy a person recorded with its three choices
// (Update when I choose is the default), and the one action the state offers: Apply, Undo, Retry or Stop.
// The words are never made up here: every state's line comes from the wire.

export type UpdateState = 'none' | 'available' | 'preparing' | 'checking' | 'ready-to-apply' | 'applied' | 'needs-review'
  | 'fix-pending' | 'failed' | 'unavailable' | 'pinned';
export type Policy = 'manual' | 'review' | 'auto' | 'pin';

export interface MaintenanceUpdate {
  builtIn?: false;
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
  sharedFile?: boolean; // from a shared file: it never updates itself, so there is no check or policy to offer (status.py)
}

/** An entry that comes with the app (maintenance/status.py `built_in`): updated with Hooke, no policy or check here. */
export interface BuiltInUpdate {
  builtIn: true; state: 'none'; says: string; next: null; version?: string;
  workflowReview?: undefined;
}

/** What the wire's setup.maintenance holds for one entry. */
export type EntryUpdate = MaintenanceUpdate | BuiltInUpdate;

/** What Updates keeps while the page draws it again (one per page, as Add your own keeps its last word, `ownSaid`):
 * whether it is open, and the last reply to Check, a policy or an action, with the update state it was said in: once the
 * state moves on ("Checking … for an update", then Ready to apply), the reply is no longer what is happening and goes. */
export interface MaintenanceMemory { open?: boolean; said?: { words: string; ok: boolean; state?: UpdateState } }

/** A maintenance command's answer: its words and the entry's update state as status.of said it then. */
export type MaintenanceReply = { says?: string; update?: { state?: UpdateState } };

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** A kept time ("2026-10-27T23:42:00", the Mac's local time, as the server writes it) as people read it ("Oct 27,
 * 11:42 pm", the server's own `when_words`); anything else as given. */
export function readableTime(stamp: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(stamp);
  if (!m) return stamp;
  const hour = Number(m[4]), month = MONTHS[Number(m[2]) - 1];
  if (!month) return stamp;
  return `${month} ${Number(m[3])}, ${hour % 12 || 12}:${m[5]} ${hour < 12 ? 'am' : 'pm'}`;
}

export interface MaintenanceCommands {
  policy: (model: string, policy: Policy) => Promise<MaintenanceReply>;
  check: (model: string) => Promise<MaintenanceReply>;
  apply: (model: string, receipt?: {generation:string;digest:string}) => Promise<MaintenanceReply>;
  undo: (model: string) => Promise<MaintenanceReply>;
  stop: (model: string) => Promise<MaintenanceReply>;
  accept: (model: string, fingerprint: string) => Promise<MaintenanceReply>;
  merge: (model: string) => Promise<MaintenanceReply>;
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

/** The facts kept apart from the state, each with its own date: never one ambiguous "last updated". A shared file's
 * entry has no check of its own to report (it never updates itself). */
export function maintenanceFacts(u: MaintenanceUpdate): string[] {
  const out: string[] = [];
  const when = (stamp?: string) => readableTime(stamp ?? '');
  if (u.active) out.push(`Running ${u.active.version}` + (u.active.installedAt ? `, installed ${when(u.active.installedAt)}` : '')
    + (u.active.promotedAt ? `, made active ${when(u.active.promotedAt)}` : ''));
  const check = u.check;
  if (!u.sharedFile) out.push(check.state === 'never' ? 'Not checked for updates yet'
    : check.state === 'off' ? 'Automatic checks are off'
      : check.state === 'failed' ? `Last check failed ${when(check.at)}: ${check.why ?? ''}`.trim()
        : `Last checked ${when(check.at)}${check.state === 'stale' ? ' (a while ago)' : ''}`);
  if (u.maintained?.state === 'unmaintained') out.push(`Not maintained${u.maintained.since ? ` since ${when(u.maintained.since)}` : ''}: ${u.maintained.why ?? ''}`.trim());
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

/** Updates for an entry that comes with the app: its one line, and that it is updated when Hooke is (no policy or
 * check of its own to offer). */
function builtInNode(u: BuiltInUpdate): HTMLElement {
  const box = node('div', 'model-maintenance');
  box.setAttribute('data-state', 'built-in');
  box.append(node('p', 'model-how model-maintenance-says', u.says));
  const details = document.createElement('details');
  details.className = 'model-maintenance-details';
  details.append(node('summary', '', 'Updates'), node('p', 'model-how', 'It comes with Hooke, so it is updated when Hooke is.'));
  box.append(details);
  return box;
}

/** The card's maintenance block, or null for an entry maintenance doesn't cover. `memory` (the page's own) keeps Updates
 * open and its last reply while the page draws it again after Check (ticket 67), until the update state moves on. It is
 * a column the width of Details (modelspage.css `.model-maintenance`), never a card's one-row action. */
export function maintenanceNode(model: string, u: EntryUpdate | undefined, commands: MaintenanceCommands | undefined,
  memory?: Map<string, MaintenanceMemory>): HTMLElement | null {
  if (u?.builtIn) return builtInNode(u);
  if (!u || !commands) return null;
  const view = maintenanceView(u);
  const box = node('div', 'model-maintenance');
  box.setAttribute('data-state', u.state);
  if (view.says) box.append(node('p', 'model-how model-maintenance-says', view.says));
  for (const line of view.review) box.append(node('p', 'model-how model-maintenance-review', line));
  const said = node('span', 'cap-download-why model-maintenance-said');
  const kept = memory?.get(model) ?? {};
  const remember = (more: MaintenanceMemory) => { Object.assign(kept, more); memory?.set(model, kept); };
  const say = (words: string, ok: boolean, state: UpdateState = u.state) => {
    said.textContent = words; said.hidden = !words; said.dataset.ok = String(ok);
    remember({ said: words ? { words, ok, state } : undefined });
  };
  // A reply said in another state than the one shown now is about what was, not what is: it goes.
  if (kept.said?.state && kept.said.state !== u.state) remember({ said: undefined });
  said.hidden = !kept.said;
  if (kept.said) { said.textContent = kept.said.words; said.dataset.ok = String(kept.said.ok); }
  const run = (button: HTMLButtonElement, call: () => Promise<MaintenanceReply>) => {
    button.disabled = true;
    void call().then((r) => say(r.says ?? '', true, r.update?.state ?? u.state))
      .catch((error: unknown) => say(error instanceof Error ? error.message : String(error), false))
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
    void commands.policy(model, choose.value as Policy).then((r) => say(r.says ?? '', true, r.update?.state ?? u.state))
      .catch((error: unknown) => {
        say(error instanceof Error ? error.message : String(error), false);
        choose.value = u.policy.policy;
      })
      .finally(() => { choose.disabled = false; });
  });
  const check = node('button', 'mm-control model-action-quiet', 'Check for an update') as HTMLButtonElement;
  check.type = 'button';
  check.addEventListener('click', () => { remember({ open: true }); run(check, () => commands.check(model)); });
  const details = document.createElement('details');
  details.className = 'model-maintenance-details';
  details.open = kept.open === true; // open as the person left it, never closed by the page drawing again
  details.addEventListener('toggle', () => remember({ open: details.open }));
  // A shared file's entry never updates itself (status.py `SHARED_FILE`): no policy to choose and nothing to check.
  details.append(node('summary', '', 'Updates'), ...(u.sharedFile ? [] : [choose, check]),
    ...view.facts.map((line) => node('p', 'model-how', line)));
  box.append(details, said);
  return box;
}
