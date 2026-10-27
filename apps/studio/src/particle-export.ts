/** Saved evidence download. Keep cancellation and the inspected revision attached until the whole ZIP arrives. */
type Selection = {id: string; session?: string; revision: number; status: string};
export function bindParticleExport(host: HTMLElement, selected: () => Selection | null): {sync(): void} {
  const button = document.createElement('button'); button.type = 'button'; button.className = 'particle-export'; button.textContent = 'Export masks and measurements';
  const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'particle-export-cancel'; cancel.textContent = 'Cancel export'; cancel.hidden = true;
  const link = document.createElement('a'); link.textContent = 'Save exchange ZIP'; link.hidden = true;
  const state = document.createElement('span'); state.setAttribute('role', 'status');
  host.append(button, cancel, link, state);
  let request: AbortController | null = null, stamp = '', url = '';
  const identity = () => {const p = selected(); return p ? JSON.stringify([p.session, p.id, p.revision, p.status]) : '';};
  const clearLink = () => {if (url) URL.revokeObjectURL(url); url = ''; link.hidden = true;};
  function sync(): void {
    const next = identity();
    if (next !== stamp) {request?.abort(); request = null; clearLink(); state.textContent = ''; stamp = next;}
    button.disabled = !!request || selected()?.status !== 'saved' || !selected()?.session;
    cancel.hidden = !request;
  }
  cancel.addEventListener('click', () => {request?.abort(); request = null; state.textContent = 'Export cancelled'; sync();});
  button.addEventListener('click', async () => {
    const p = selected(); if (!p?.session || p.status !== 'saved' || request) return;
    const key = identity(), active = new AbortController(); request = active; clearLink(); sync(); state.textContent = 'Preparing export…';
    try {
      const response = await fetch(`/api/resources/${encodeURIComponent(p.id)}/exchange?session=${encodeURIComponent(p.session)}&revision=${p.revision}`, {signal: active.signal, cache: 'no-store'});
      if (!response.ok) throw new Error((await response.json()).error ?? 'The export could not be prepared');
      const blob = await response.blob();
      if (active.signal.aborted || key !== identity() || request !== active) return;
      url = URL.createObjectURL(blob); link.href = url; link.download = `particles-${p.id}-r${p.revision}.zip`; link.hidden = false;
      state.textContent = `Revision ${p.revision} ready · original image, labels, measurements and provenance`;
    } catch (error) {
      if (!active.signal.aborted && key === identity()) state.textContent = (error as Error).message;
    } finally {if (request === active) request = null; sync();}
  });
  return {sync};
}
