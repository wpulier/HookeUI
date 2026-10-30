/** Explicit partial-folder export over the same tracked operation used by agents. */
export interface ExportPlan {files: number; bytes: number; requiredFreeBytes: number; collection: {state: 'unverified'; reason: string}}
export interface ExportReceipt {location: string; files: number; bytes: number; manifestSha256: string; collection: {state: 'unverified'; reason: string}}
export interface ExportOperation {id: string; state: string; progress?: {says?: string} | null; outcome?: {export?: ExportReceipt; says?: string; next?: string} | null}
export interface ExportActions {
  prepare(): Promise<ExportPlan>;
  start(requestKey: string): Promise<ExportOperation>;
  read(operation: string): Promise<ExportOperation>;
  stop(operation: string): Promise<ExportOperation>;
  reveal(operation: string): Promise<void>;
}
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', cls = '') => {
  const e = document.createElement(tag); e.textContent = text; e.className = cls; return e;
};
const bytes = (n: number) => n < 1024 ** 2 ? `${Math.ceil(n / 1024)} KiB` : `${(n / 1024 ** 2).toFixed(1)} MiB`;

export function projectExport(actions: ExportActions, current: () => boolean, origin: string): {element: HTMLElement; dispose(): void} {
  const element = node('section', '', 'project-export');
  const status = node('p', '', 'project-folder-status'); status.setAttribute('role', 'status');
  const content = node('div'), controls = node('div', '', 'project-export-actions');
  element.append(node('h3', 'Export copy'), content, status, controls);
  let disposed = false, timer: ReturnType<typeof setTimeout> | undefined, busy = false;
  let readEpoch = 0;
  let requestKey = `project-export-${crypto.randomUUID()}`, operation = '';
  const storageKey = `hooke.project-export.${origin}`;
  const pendingKey = `${storageKey}.pending`;
  let pending = '';
  try {
    operation = localStorage.getItem(storageKey) ?? '';
    pending = localStorage.getItem(pendingKey) ?? '';
    if (pending) requestKey = pending;
  } catch { /* optional presentation state */ }
  const active = () => !disposed && current();
  const button = (label: string, fn: () => Promise<void> | void, primary = false) => {
    const b = node('button', label, primary ? 'study-primary' : 'study-quiet'); b.type = 'button';
    b.addEventListener('click', () => { if (active() && !busy) void fn(); }); controls.append(b); return b;
  };
  const showError = (error: unknown) => { status.textContent = error instanceof Error ? error.message : String(error); };
  const acknowledge = (id: string, admittedKey: string) => {
    try {
      // Another panel can have recovered this request and started a newer one.
      // Its recovery identity must survive this older panel's delayed reply.
      if (localStorage.getItem(pendingKey) === admittedKey) {
        localStorage.setItem(storageKey, id); localStorage.removeItem(pendingKey);
      }
    } catch { /* retained pending identity remains safe to retry */ }
  };
  const render = (row: ExportOperation) => {
    if (!active()) return;
    if (timer) clearTimeout(timer);
    operation = row.id; controls.replaceChildren(); content.replaceChildren();
    if (row.state === 'succeeded' && row.outcome?.export) {
      const result = row.outcome.export;
      status.textContent = 'Folder copy saved and verified.';
      content.append(node('p', `${result.files.toLocaleString()} files · ${bytes(result.bytes)}`),
        node('p', result.collection.reason), node('p', result.location, 'project-folder-path'));
      button('Reveal copy', async () => {
        busy = true;
        try { await actions.reveal(operation); } catch (error) { if (active()) showError(error); }
        finally { busy = false; }
      }, true);
      button('New copy', () => { requestKey = `project-export-${crypto.randomUUID()}`; void prepare(); });
      return;
    }
    if (['failed', 'cancelled', 'interrupted'].includes(row.state)) {
      status.textContent = row.outcome?.says ?? `Export ${row.state}.`;
      if (row.outcome?.next) content.append(node('p', row.outcome.next));
      content.append(node('p', 'Your original remains unchanged. Incomplete copies are kept for inspection.'));
      button('Prepare another copy', () => { requestKey = `project-export-${crypto.randomUUID()}`; void prepare(); });
      return;
    }
    status.textContent = row.state === 'cancelRequested' ? 'Stopping the copy…' : row.progress?.says ?? 'Export admitted. Waiting for its result…';
    content.append(node('p', 'You can close this panel. The admitted copy stays with this Project’s activity.'));
    if (row.state !== 'cancelRequested') button('Stop export', async () => {
      busy = true; readEpoch++;
      try { const result = await actions.stop(operation); if (active()) render(result); }
      catch (error) { if (active()) showError(error); }
      finally { busy = false; }
    });
    timer = setTimeout(() => void poll(), 1000);
  };
  const poll = async () => {
    if (!active()) return;
    if (timer) clearTimeout(timer);
    if (busy) { timer = setTimeout(() => void poll(), 1000); return; }
    const epoch = ++readEpoch;
    try { const result = await actions.read(operation); if (epoch === readEpoch) render(result); }
    catch (error) {
      if (!active() || epoch !== readEpoch) return;
      showError(error); controls.replaceChildren();
      button('Check export', poll);
      button('Prepare a new copy', () => { requestKey = `project-export-${crypto.randomUUID()}`; void prepare(); });
    }
  };
  const start = async () => {
    if (!active() || busy) return;
    // Save admission identity before sending. A lost reply followed by closing the
    // panel must recover the same operation, not create a second snapshot.
    try { localStorage.setItem(pendingKey, requestKey); }
    catch { status.textContent = 'Export recovery cannot be saved in this browser. Enable local storage and retry.'; return; }
    busy = true; readEpoch++; status.textContent = 'Admitting the export…'; controls.replaceChildren();
    const admittedKey = requestKey;
    try {
      const result = await actions.start(admittedKey);
      // Keep the locator even if navigation closed this view during admission.
      acknowledge(result.id, admittedKey); render(result);
    }
    catch (error) { if (active()) { showError(error); button('Retry this export', start); } }
    finally { busy = false; }
  };
  const prepare = async () => {
    if (!active()) return;
    if (timer) clearTimeout(timer);
    busy = true; readEpoch++; controls.replaceChildren(); status.textContent = 'Checking saved files and required space…';
    try {
      const plan = await actions.prepare();
      if (!active()) return;
      content.replaceChildren(node('p', `${plan.files.toLocaleString()} files · ${bytes(plan.bytes)} · needs ${bytes(plan.requiredFreeBytes)} free`), node('p', plan.collection.reason));
      status.textContent = 'This is a folder snapshot, not a fully collected Project.';
      button('Export this partial copy', start, true);
    } catch (error) { if (active()) { showError(error); button('Retry preparation', prepare); } }
    finally { busy = false; }
  };
  if (pending) {
    status.textContent = 'The previous export reply was not received. Check that same request before making another copy.';
    button('Recover export', start, true);
  }
  else if (operation) void poll();
  else { content.append(node('p', 'Create an immutable copy of the files saved in this Project folder.')); button('Review export', prepare); }
  return {element, dispose() { disposed = true; if (timer) clearTimeout(timer); }};
}
