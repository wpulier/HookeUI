/** Browser playback from copied evidence, never this host's Library alias or Stage. */
export function showRetainedMedia(body: HTMLElement, state: unknown, ref: {id: string; day: string; version: number}, signal: AbortSignal): void {
  if (!state || typeof state !== 'object') return;
  const saved = state as Record<string, unknown>;
  const media = saved.librarySource as {format?: number; scope?: string; asset?: {kind?: string}} | undefined;
  const file = saved.sourceFile as {sha256?: string} | undefined;
  if (media?.format !== 1 || media.scope !== 'selected-media' || !['still', 'recording'].includes(media.asset?.kind ?? '') || !/^[a-f0-9]{64}$/.test(file?.sha256 ?? '')) return;
  const host = document.createElement('section'); host.className = 'rd-retained-media';
  const note = document.createElement('p'); note.className = 'rd-note'; note.textContent = 'Loading retained media…'; note.setAttribute('role', 'status');
  const url = `/api/scientific-files/${encodeURIComponent(ref.id)}/media?${new URLSearchParams({session: ref.day, revision: String(ref.version), sha256: file!.sha256!})}`;
  const video = media.asset?.kind === 'recording' ? document.createElement('video') : null;
  const image = video ? null : document.createElement('img');
  const view = video ?? image!;
  const ready = () => { if (!signal.aborted) note.textContent = 'Retained original · Preview leaves the Stage unchanged.'; };
  const failed = () => { if (!signal.aborted) { view.hidden = true; note.textContent = 'This media could not be previewed. Download the retained original to inspect it in a compatible viewer.'; retry.hidden = false; } };
  const retry = document.createElement('button'); retry.className = 'mm-control'; retry.type = 'button'; retry.textContent = 'Retry preview'; retry.hidden = true;
  retry.addEventListener('click', () => { if (signal.aborted) return; retry.hidden = true; view.hidden = false; note.textContent = 'Loading retained media…'; view.src = url; video?.load(); });
  view.addEventListener('error', failed);
  if (video) { video.controls = true; video.preload = 'metadata'; video.className = 'rd-video'; video.addEventListener('loadedmetadata', ready); }
  if (image) { image.alt = 'Retained Project image'; image.decoding = 'async'; image.className = 'rd-retained-image'; image.addEventListener('load', ready); }
  signal.addEventListener('abort', () => { video?.pause(); view.removeAttribute('src'); video?.load(); }, {once: true});
  host.append(view, note, retry); body.prepend(host); view.src = url;
}
