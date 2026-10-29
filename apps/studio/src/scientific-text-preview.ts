/** Literal, bounded preview of one accepted file. No HTML, scripts or scientific inference. */
type Reference = { id: string; day: string; version: number };
const PAGE_BYTES = 65536;
const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const element = document.createElement(tag); element.textContent = text; return element;
};

export function textPreview(reference: Reference, sha256: string, size: number): HTMLElement {
  const section = node('section'); section.className = 'scientific-text-preview';
  const open = node('button', 'Preview as text'); open.type = 'button';
  const panel = node('div'); panel.hidden = true;
  const description = node('p', 'Plain UTF-8 text · 64 KiB per page. Invalid or split characters appear as �.');
  const status = node('p'); status.setAttribute('role', 'status');
  const content = node('pre'); content.tabIndex = 0; content.setAttribute('aria-label', 'Retained file text');
  const navigation = node('div'); navigation.className = 'scientific-text-navigation';
  const previous = node('button', 'Previous page'), next = node('button', 'Next page'), retry = node('button', 'Retry preview');
  for (const button of [previous, next, retry]) button.type = 'button';
  previous.disabled = true; next.disabled = true; retry.hidden = true;
  navigation.append(previous, next, retry); panel.append(description, status, content, navigation); section.append(open, panel);
  let current = 0, following: number | null = null, attempted = 0, busy = false, loaded = false;
  async function read(offset: number): Promise<void> {
    if (busy) return;
    attempted = offset; busy = true; panel.hidden = false; open.hidden = true;
    previous.disabled = next.disabled = retry.disabled = true; retry.hidden = true;
    status.textContent = 'Reading saved text…'; content.setAttribute('aria-busy', 'true');
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(`/api/scientific-files/${encodeURIComponent(reference.id)}/text?${new URLSearchParams({
        session: reference.day, revision: String(reference.version), offset: String(offset), length: String(PAGE_BYTES),
      })}`, { signal: controller.signal });
      const result: unknown = await response.json();
      if (!response.ok || !object(result) || result.ok !== true)
        throw new Error(object(result) && typeof result.says === 'string' ? result.says : 'Text could not be read. Retry this saved revision.');
      const page = result.content, bytes = Math.min(PAGE_BYTES, size - offset), end = offset + bytes;
      if (result.id !== reference.id || result.session !== reference.day || result.revision !== reference.version
          || result.sha256 !== sha256 || result.verification !== 'displayed-chunks-only'
          || !object(page) || typeof page.text !== 'string' || page.text.length > PAGE_BYTES
          || page.offset !== offset || page.bytes !== bytes || page.totalBytes !== size
          || page.nextOffset !== (end < size ? end : null))
        throw new Error('The preview did not match this file and revision. Reopen the saved source.');
      content.textContent = page.text; // Saved content is data, never markup.
      current = offset; following = end < size ? end : null; loaded = true;
      status.textContent = size ? `Bytes ${(offset + 1).toLocaleString()}–${end.toLocaleString()} of ${size.toLocaleString()} · displayed chunks verified` : 'Empty file';
      content.scrollTop = 0;
    } catch (error) {
      status.textContent = controller.signal.aborted ? 'The preview timed out. Retry this saved revision.' : error instanceof Error ? error.message : 'Text could not be read.';
      retry.hidden = false;
    } finally {
      clearTimeout(timer); busy = false; content.setAttribute('aria-busy', 'false');
      previous.disabled = !loaded || current === 0; next.disabled = !loaded || following === null; retry.disabled = false;
    }
  }
  open.addEventListener('click', () => { void read(0); });
  previous.addEventListener('click', () => { void read(Math.max(0, current - PAGE_BYTES)); });
  next.addEventListener('click', () => { if (following !== null) void read(following); });
  retry.addEventListener('click', () => { void read(attempted); });
  return section;
}
