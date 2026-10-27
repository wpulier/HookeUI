/** The scientist admits pinned operations; agents can only use the resulting scoped grant. */
import { dollars, StudyRefused, type StudyInputRef } from './research-efforts';

export interface ScientificPolicy {
  id: string; title: string; capability: string; generation: string; environment: Record<string, unknown>;
  selection: StudyInputRef; inputTitle: string; component: string; region: number[][];
  params: Record<string, unknown>; conversion?: { kind: string; inputRange: [number, number] }; maximumMicros: number; seconds: number;
}
export interface ScientificJob { job: string; status: string; operation: string; reservedMicros: number; completionVerified: false; retained?: StudyInputRef; problem?: string }
export interface ScientificAdmission {
  ready: boolean; reason?: string; reviewSha256?: string; claudeSetupReady?: boolean; claudeSetupReason?: string;
  policies: ScientificPolicy[];
  grants: { request: string; status: string; policies: string[]; maximumMicros: number; expiresAt: number; claudeReady: boolean; claudeAccessVerified?: false; claudeConnectionStatus?: string; claudeConnectionRequest?: string; reviewSha256?: string }[];
  jobs: ScientificJob[];
}
const endpoint = (id: string) => `/api/research-efforts/${encodeURIComponent(id)}/scientific/admission`;
async function reply(response: Response): Promise<ScientificAdmission> {
  const value = await response.json().catch(() => ({}));
  if (!response.ok || value.ok !== true) throw new StudyRefused(value.says ?? value.error ?? 'Scientific admission could not be read.', value.next ?? null);
  return value as ScientificAdmission;
}
export async function readScientificAdmission(id: string, session: string, fetcher: typeof fetch = fetch): Promise<ScientificAdmission> {
  return reply(await fetcher(`${endpoint(id)}?session=${encodeURIComponent(session)}`, { cache: 'no-store' }));
}
export async function admitScientificPolicies(id: string, session: string, request: string, policies: string[], reviewSha256: string, fetcher: typeof fetch = fetch): Promise<ScientificAdmission> {
  return post(`${endpoint(id)}/admit`, { session, request, policies, reviewSha256 }, fetcher);
}
async function post(url: string, body: Record<string, unknown>, fetcher: typeof fetch): Promise<ScientificAdmission> {
  return reply(await fetcher(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'research-effort' }, body: JSON.stringify(body) }));
}
export function scientificJobAction(id: string, session: string, job: string, action: 'status' | 'stop' | 'retrieve', fetcher: typeof fetch = fetch): Promise<ScientificAdmission> {
  return post(`${endpoint(id)}/jobs/${encodeURIComponent(job)}/${action}`, { session }, fetcher);
}
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => { const element = document.createElement(tag); element.textContent = text; return element; };
const disclosure = (title: string, value: unknown) => { const element = node('details'); element.className = 'study-evidence-details'; element.append(node('summary', title), node('pre', JSON.stringify(value, null, 2))); return element; };
const STATES: Record<string, string> = { launching: 'Starting', uncertain: 'Needs checking', running: 'Running', stop_requested: 'Stop requested · waiting to confirm', cancelled: 'Stopped', failed: 'Failed', succeeded: 'Finished · look at its result' };
export function mountScientificAdmission(root: HTMLElement, owner: { id: string; session: string; current(): boolean; openResult(ref: StudyInputRef): Promise<void> }, fetcher: typeof fetch = fetch): () => void {
  const section = node('section'); section.className = 'study-view-start study-scientific-admission';
  const status = node('p', 'Checking which tools this Study may use…'); status.setAttribute('role', 'status');
  const selectedCost = node('p', 'No tools selected.');
  const policies = node('div'), grants = node('div'), jobs = node('div');
  const note = node('p', 'Choose which prepared tools the researcher may run on these inputs, and the most each may spend. Allowing starts nothing; it runs one GPU job at a time.'); note.className = 'study-view-empty';
  const allow = node('button', 'Allow selected tools'), refresh = node('button', 'Check again'), refreshJobs = node('button', 'Check for jobs');
  refresh.setAttribute('aria-label', 'Check allowed tools again');
  for (const button of [allow, refresh, refreshJobs]) button.type = 'button';
  allow.disabled = refreshJobs.disabled = true;
  const jobsHeading = node('h3', 'GPU jobs from this Study');
  section.append(node('h3', 'Tools this Study may use'), note, status, policies, selectedCost, allow, refresh, grants, jobsHeading, refreshJobs, jobs); root.append(section);
  let disposed = false, pending = false, generation = 0, snapshot: ScientificAdmission | null = null;
  const choices = new Set<string>(); let request: string = crypto.randomUUID(), captured: { policies: string[]; reviewSha256: string } | null = null;
  let connectionRequest: string = crypto.randomUUID();
  const current = () => !disposed && owner.current();
  const showError = (cause: unknown) => { if (current()) status.textContent = cause instanceof StudyRefused ? `${cause.message}${cause.next ? ` Next: ${cause.next}` : ''}` : cause instanceof Error ? cause.message : String(cause); };
  const apply = async (work: () => Promise<ScientificAdmission>, accepting = false) => {
    if (!current() || pending) return;
    pending = true; ++generation; section.dataset.pending = 'true'; status.textContent = 'Waiting for confirmation…';
    section.querySelectorAll('button').forEach(button => { button.disabled = true; }); section.querySelectorAll('input').forEach(input => { input.disabled = true; });
    try { const value = await work(); pending = false; if (current()) { if(accepting){choices.clear();delete section.dataset.draft;}render(value); } }
    catch (cause) { pending = false; if (current() && snapshot) render(snapshot); showError(cause); }
    finally { pending = false; delete section.dataset.pending; }
  };
  const button = (title: string, action: () => Promise<ScientificAdmission>) => { const control = node('button', title); control.type = 'button'; control.addEventListener('click', () => { void apply(action); }); return control; };
  const render = (value: ScientificAdmission) => {
    if (!current()) return;
    if(captured&&!value.grants.length&&value.reviewSha256!==captured.reviewSha256){captured=null;request=crypto.randomUUID();choices.clear();delete section.dataset.draft;}
    snapshot = value; root.dispatchEvent(new Event('study-preparation-changed'));
    const retryGrant = value.grants.find(grant => ['prepared', 'uncertain'].includes(grant.status));
    if(retryGrant){request=retryGrant.request;captured={policies:[...retryGrant.policies],reviewSha256:retryGrant.reviewSha256??value.reviewSha256??''};choices.clear();retryGrant.policies.forEach(id=>choices.add(id));}
    status.textContent = value.reason || (value.ready ? 'Review a prepared operation before allowing it.' : 'Scientific execution preparation is unavailable.');
    policies.replaceChildren();
    for (const policy of value.policies) {
      const item = node('section'); item.className = 'study-view-task-problem';
      const check = node('input'); check.type = 'checkbox'; check.checked = choices.has(policy.id) || value.grants.some(grant => grant.policies.includes(policy.id)); check.disabled = !value.ready || value.grants.length > 0 || captured !== null;
      const label = node('label'); label.append(check, node('strong', policy.title));
      check.addEventListener('input', event => { event.stopPropagation(); if(check.checked)choices.add(policy.id);else choices.delete(policy.id); if(choices.size)section.dataset.draft='true';else delete section.dataset.draft; allow.disabled = !value.ready || !value.reviewSha256 || !choices.size || value.grants.length > 0; selectedCost.textContent=choices.size?`Most these may spend: ${dollars(value.policies.filter(item=>choices.has(item.id)).reduce((total,item)=>total+item.maximumMicros,0))}`:'No tools selected.'; });
      item.append(label, node('p', `${policy.inputTitle} · revision ${policy.selection.revision} · ${policy.component}`),
        node('p', `Window: ${policy.region.map(([start, stop], axis) => `Axis ${axis+1}: ${start}–${stop} (end excluded)`).join(', ')}`),
        node('p', `Allowance ${dollars(policy.maximumMicros)} · runtime limit ${policy.seconds} seconds`),
        disclosure('Exact tool, input and settings', { capability: policy.capability, generation: policy.generation, conversion: policy.conversion ?? null, environment: { runner: policy.environment.runner, generation: policy.environment.generation, receiptSha256: policy.environment.receiptSha256 }, selection: policy.selection, component: policy.component, region: policy.region, settings: policy.params }));
      if(policy.conversion)item.append(node('p', `Explicit conversion: ${policy.conversion.kind}, input range ${policy.conversion.inputRange.join(' to ')}. The selected window is converted for this tool; original native values remain retained.`));
      policies.append(item);
    }
    if (!value.policies.length) policies.append(node('p', 'No prepared tool fits these inputs yet.'));
    allow.disabled = !value.ready || !value.reviewSha256 || !choices.size || (value.grants.length > 0 && !retryGrant);
    selectedCost.textContent=choices.size?`Most these may spend: ${dollars(value.policies.filter(item=>choices.has(item.id)).reduce((total,item)=>total+item.maximumMicros,0))}`:'No tools selected.';
    allow.textContent = retryGrant ? 'Retry same tool permission' : 'Allow selected tools';
    refresh.disabled = false; refreshJobs.disabled = !value.grants.length;
    grants.replaceChildren();
    for (const grant of value.grants) {
      grants.append(node('p', `Tool permission: ${grant.status} · ${dollars(grant.maximumMicros)} reserved · ${grant.policies.map(id => value.policies.find(p => p.id === id)?.title ?? id).join(', ')}`));
      if (grant.claudeConnectionRequest) connectionRequest = grant.claudeConnectionRequest;
      const connection = grant.claudeConnectionStatus;
      if (grant.claudeReady && connection === 'connected') {
        grants.append(node('p', 'Claude tool credentials are configured. Live access is not yet verified; starting research remains a separate action.'));
      } else if (connection?.startsWith('pending-') || connection?.startsWith('uncertain-')) {
        grants.append(node('p', 'Claude tool connection is unconfirmed. Reconcile the original provider attempt before trying again; no connection write will be repeated.'));
      } else {
        grants.append(node('p', 'Claude tool access is not yet verified. Connect the allowed tools before starting scientific research with Claude. This creates private credentials only; it starts no model or GPU job.'));
        if (value.claudeSetupReason) grants.append(node('p', value.claudeSetupReason));
        const connect = button(connection === 'vault-created' ? 'Continue Claude tool connection' : 'Connect Claude tools',
          () => post(`${endpoint(owner.id)}/connect-claude`, { session: owner.session, request: connectionRequest }, fetcher));
        connect.disabled = grant.status !== 'ready' || !value.claudeSetupReady;
        grants.append(connect);
      }
    }
    if(value.grants.length) grants.append(node('p', 'This Study keeps its admitted inputs and tools. Create a new Study to change them.'));
    // Nothing prepared, allowed or started: one line and Check again, not three disabled buttons and their explanations.
    const nothing = !value.policies.length && !value.grants.length && !value.jobs.length; section.dataset.nothing = String(nothing);
    for (const item of [note, selectedCost, allow, jobsHeading, refreshJobs, jobs]) item.hidden = nothing;
    status.hidden = nothing && !value.reason;
    jobs.replaceChildren();
    if(!value.jobs.length) jobs.append(node('p', 'No GPU jobs yet. Check for jobs collects any this Study’s tools started; it never starts or retries one.'));
    for (const job of value.jobs) {
      const row = node('section'); row.className = 'study-view-task-problem';
      row.append(node('h4', value.policies.find(policy => policy.id === job.operation)?.title ?? job.operation), node('p', `${STATES[job.status] ?? job.status} · ${dollars(job.reservedMicros)} reserved`));
      if(job.problem)row.append(node('p', job.problem));
      const controls = node('div'); controls.className = 'study-task-actions';
      controls.append(button('Check original job', () => scientificJobAction(owner.id, owner.session, job.job, 'status', fetcher)));
      if(!['succeeded', 'failed', 'cancelled'].includes(job.status)) { const stop = button('Stop original job', () => scientificJobAction(owner.id, owner.session, job.job, 'stop', fetcher)); stop.disabled = job.status === 'stop_requested'; controls.append(stop); }
      if(job.status === 'succeeded' && !job.retained) controls.append(button('Retrieve native result', () => scientificJobAction(owner.id, owner.session, job.job, 'retrieve', fetcher)));
      if(job.retained) {
        const kept = job.retained, open = node('button', 'Open retained result'); open.type = 'button';
        open.addEventListener('click', () => { if(!current()||pending)return;void owner.openResult(kept).catch(showError); }); controls.append(open);
        row.append(node('p', 'Its result is in this Project. Look at it before you accept what the researcher concluded from it.'));
      }
      row.append(controls, disclosure('Technical details', { job: job.job, operation: job.operation, retained: job.retained ?? null })); jobs.append(row);
    }
  };
  const read = async () => { if(!current()||pending)return;const sequence=++generation;try{const value=await readScientificAdmission(owner.id, owner.session, fetcher);if(sequence===generation&&!pending)render(value);}catch(cause){if(sequence===generation)showError(cause);} };
  allow.addEventListener('click', () => { if(allow.disabled)return;captured??={policies:[...choices],reviewSha256:snapshot?.reviewSha256??''};void apply(() => admitScientificPolicies(owner.id, owner.session, request, captured!.policies, captured!.reviewSha256, fetcher), true); });
  refresh.addEventListener('click', () => { void read(); });
  refreshJobs.addEventListener('click', () => { if(refreshJobs.disabled)return;void apply(() => post(`${endpoint(owner.id)}/refresh-jobs`, { session: owner.session }, fetcher)); });
  const staged = () => { if(!choices.size)void read(); }; root.addEventListener('study-scientific-staged', staged);
  void read();
  return () => { disposed=true;root.removeEventListener('study-scientific-staged',staged);section.remove(); };
}
