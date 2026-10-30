/** Owner-only whole Record inclusion. The reviewed digest pins content, not access. */
export interface RecordReview {
  session: string; origin: string; dataset: string; title: string; reviewDigest: string;
  files: number; bytes: number; requiredFreeBytes: number; scope: 'whole-record-folder';
  captureCheck: {ok?: boolean; problems?: unknown[]; notes?: unknown[]}; reason: string;
}
export interface RecordOperation {
  id: string; project: string; state: string; progress?: {says?: string};
  outcome?: {says?: string; inclusion?: {reference: {id: string; session: string; revision: number; digest: string}}};
}
export interface RecordActions {
  list(): Promise<{id: string; name?: string; status: string}[]>;
  review(id: string): Promise<RecordReview>;
  start(id: string, digest: string, requestKey: string): Promise<RecordOperation>;
  read(id: string): Promise<RecordOperation>;
  stop(id: string): Promise<RecordOperation>;
}
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', cls = '') => {
  const e = document.createElement(tag); e.textContent = text; e.className = cls; return e;
};
const size = (value: number) => `${(value / 1024 ** 2).toLocaleString(undefined, {maximumFractionDigits: 1})} MiB`;

export function recordInclusion(actions: RecordActions, session: string, origin: string, current: () => boolean): {element: HTMLElement; dispose(): void} {
  const element = node('section', '', 'project-export');
  const body = node('div'), status = node('p', '', 'project-folder-status'), buttons = node('div', '', 'project-export-actions');
  status.setAttribute('role', 'status');
  element.append(node('h3', 'Include a recorded dataset'), body, status, buttons);
  let disposed = false, busy = false, timer: ReturnType<typeof setTimeout> | undefined;
  let operation = '', requestKey = '', selection = '', digest = '', readEpoch = 0;
  const key = `hooke.record-inclusion.${origin}`;
  const active = () => !disposed && current();
  const message = (error: unknown) => { status.textContent = error instanceof Error ? error.message : String(error); };
  const save = (admitting = false) => { try {
    const previous = JSON.parse(localStorage.getItem(key) ?? 'null');
    if (admitting || previous?.requestKey === requestKey) localStorage.setItem(key, JSON.stringify({operation, requestKey, selection, digest}));
  } catch {
    if (admitting) throw new Error('This browser cannot retain the retry identity. No copy was started. Enable local storage and retry.');
    // If acknowledgement persistence fails, the already saved pending request
    // remains recoverable with the same identity.
  } };
  const button = (title: string, action: () => void | Promise<void>, primary = false) => {
    const b = node('button', title, primary ? 'study-primary' : 'study-quiet'); b.type = 'button';
    b.addEventListener('click', () => { if (active() && !busy) void action(); }); buttons.append(b); return b;
  };
  const render = (row: RecordOperation) => {
    if (!active()) return;
    if (row.project !== session || row.id !== operation) { message('Recording status does not match this Project operation.'); return; }
    if (timer) clearTimeout(timer);
    buttons.replaceChildren(); body.replaceChildren();
    if (row.state === 'succeeded' && row.outcome?.inclusion) {
      const ref = row.outcome.inclusion.reference;
      status.textContent = 'Recorded dataset copied into this Project.';
      body.append(node('p', 'Find the retained copy in Inputs and Data. The original recording is unchanged.'),
        node('p', `Retained revision ${ref.revision} · ${ref.id}`, 'project-folder-path'));
      button('Include another recording', choose); return;
    }
    if (['failed', 'cancelled', 'interrupted'].includes(row.state)) {
      status.textContent = row.outcome?.says ?? `Inclusion ${row.state}.`;
      button('Review another inclusion', choose); return;
    }
    status.textContent = row.state === 'cancelRequested' ? 'Stopping inclusion…' : row.progress?.says ?? 'Inclusion admitted. Waiting for its result…';
    body.append(node('p', 'You can close this panel. The operation stays with this Project’s activity.'));
    if (row.state !== 'cancelRequested') button('Stop inclusion', async () => {
      busy = true; readEpoch++;
      try { render(await actions.stop(operation)); } catch (error) { if (active()) message(error); }
      finally { busy = false; }
    });
    timer = setTimeout(() => void poll(), 1000);
  };
  const poll = async () => {
    if (!active()) return;
    if (busy) { timer = setTimeout(() => void poll(), 1000); return; }
    const epoch = ++readEpoch;
    try { const row = await actions.read(operation); if (epoch === readEpoch) render(row); }
    catch (error) { if (active() && epoch === readEpoch) { message(error); buttons.replaceChildren(); button('Refresh status', poll); } }
  };
  const start = async () => {
    busy = true; readEpoch++; buttons.replaceChildren(); status.textContent = 'Admitting the reviewed whole dataset…';
    try {
      save(true);
      const row = await actions.start(selection, digest, requestKey);
      if (row.project !== session) throw new Error('Inclusion acknowledgement belongs to another Project.');
      operation = row.id; save(); render(row);
    } catch (error) {
      if (active()) { message(error); button('Retry this inclusion', start); }
    } finally { busy = false; }
  };
  const review = async (identity: string) => {
    busy = true; buttons.replaceChildren(); status.textContent = 'Checking the entire recording and required space…';
    try {
      const plan = await actions.review(identity);
      if (!active()) return;
      if (plan.session !== session || plan.dataset !== identity) throw new Error('The inclusion review does not match this Project and recording.');
      selection = identity; digest = plan.reviewDigest; operation = ''; requestKey = `record-${crypto.randomUUID()}`;
      body.replaceChildren(node('strong', plan.title), node('p', `${plan.files.toLocaleString()} files · ${size(plan.bytes)} · needs ${size(plan.requiredFreeBytes)} free`), node('p', plan.reason));
      if (plan.captureCheck.ok !== true) body.append(node('p', 'The capture reported problems. Copying preserves them; it does not repair missing data.'));
      for (const problem of plan.captureCheck.problems ?? []) if (typeof problem === 'string') body.append(node('p', problem));
      for (const note of plan.captureCheck.notes ?? []) if (typeof note === 'string') body.append(node('p', note));
      status.textContent = 'Confirm inclusion of all material, including any captured work from other Projects.';
      button('Include whole dataset', start, true); button('Choose another', choose);
    } catch (error) { if (active()) { message(error); button('Choose another', choose); } }
    finally { busy = false; }
  };
  const choose = async () => {
    if (timer) clearTimeout(timer);
    busy = true; readEpoch++; buttons.replaceChildren(); body.replaceChildren(); status.textContent = 'Reading recorded datasets…';
    try {
      const rows = await actions.list(); if (!active()) return;
      const available = rows.filter(row => row.status === 'complete');
      if (!available.length) { status.textContent = 'No completed recorded datasets are available on this host.'; button('Refresh recordings', choose); return; }
      const select = node('select'); select.setAttribute('aria-label', 'Recorded dataset to include');
      for (const row of available) { const option = node('option', row.name || row.id); option.value = row.id; select.append(option); }
      body.append(node('p', 'Copy one entire recording into this Project. Review its contents and space before adding it.'), select);
      status.textContent = ''; button('Review inclusion', () => review(select.value), true);
    } catch (error) { if (active()) { message(error); button('Refresh recordings', choose); } }
    finally { busy = false; }
  };
  try {
    const saved = JSON.parse(localStorage.getItem(key) ?? 'null');
    if (saved && ['operation', 'requestKey', 'selection', 'digest'].every(field => typeof saved[field] === 'string')) {
      ({operation, requestKey, selection, digest} = saved);
    }
  } catch { /* unavailable presentation state */ }
  if (operation) void poll();
  else if (requestKey && selection && digest) {
    body.append(node('p', `A previously confirmed inclusion of ${selection} has no acknowledged result. Retrying uses the same request identity.`));
    button('Retry this inclusion', start);
  } else button('Choose recording', choose);
  return {element, dispose() { disposed = true; if (timer) clearTimeout(timer); }};
}
