import type { LiveSample, WindowState, WorkspaceWindows } from './workspace-windows';

interface Options {
  windows: WorkspaceWindows; run: string; fetch: typeof fetch;
  createObjectURL(blob: Blob): string; revokeObjectURL(url: string): void;
}
interface Stream {
  token: string; url: string; held: boolean; owners: Map<string, WindowState>; decodes: Map<string, AbortController>; abort: AbortController;
  timer: ReturnType<typeof setTimeout> | null; currentURL: string | null; pendingURL: string | null;
}
const MAX_BYTES = 10 * 1024 * 1024;
const INTERVAL_MS = 200;
const problem = (message: string) => Object.assign(new Error(message), { code: 'source_mismatch' });

/** One bounded snapshot request per live lease, shared by its native windows.
 * The authenticated producer supplies pixels and their sample together. This
 * module decodes them; only WorkspaceWindows' completed paint publishes facts.
 */
export class LiveWindowDelivery {
  private readonly streams = new Map<string, Stream>();
  private readonly bindings = new Map<string, WindowState>();
  private readonly options: Options;
  constructor(options: Options) { this.options = options; }
  sync(states: readonly WindowState[]): void {
    for (const state of states) {
      for (const stream of this.streams.values()) {
        const old = stream.owners.get(state.id);
        if (old && (!state.open || state.source.kind !== 'live' || state.delivery.sourceToken !== stream.token
          || old.sourceGeneration !== state.sourceGeneration || old.lifetimeGeneration !== state.lifetimeGeneration)) {
          stream.owners.delete(state.id); stream.decodes.get(state.id)?.abort(); stream.decodes.delete(state.id);
        }
      }
      this.bindings.delete(state.id);
      if (!state.open || state.source.kind !== 'live') continue;
      this.bindings.set(state.id, structuredClone(state));
      const token = state.delivery.sourceToken;
      let stream = this.streams.get(token);
      if (!stream) {
        stream = { token, url: state.delivery.url, held: false, owners: new Map(), decodes: new Map(), abort: new AbortController(), timer: null, currentURL: null, pendingURL: null };
        this.streams.set(token, stream);
        // The owner is installed before starting asynchronous transport.
        stream.owners.set(state.id, structuredClone(state)); void this.next(stream);
      } else {
        const newOwner = !stream.owners.has(state.id);
        stream.owners.set(state.id, structuredClone(state));
        if (newOwner && stream.held) { stream.held = false; void this.next(stream); }
      }
    }
    for (const stream of this.streams.values()) if (!stream.owners.size) this.stop(stream);
  }
  retry(id: string): void {
    const state = this.bindings.get(id);
    if (state) this.sync([state]);
  }
  private active(stream: Stream): boolean { return this.streams.get(stream.token) === stream && !stream.abort.signal.aborted; }
  private stop(stream: Stream): void {
    this.streams.delete(stream.token); stream.abort.abort();
    if (stream.timer !== null) clearTimeout(stream.timer);
    for (const url of new Set([stream.currentURL, stream.pendingURL])) if (url) this.options.revokeObjectURL(url);
    stream.currentURL = stream.pendingURL = null;
  }
  private sample(response: Response): { sample: LiveSample; size: number[]; digest: string } {
    let sample: LiveSample, size: number[];
    try { sample = JSON.parse(response.headers.get('X-Micromind-Live-Sample') ?? 'null'); size = JSON.parse(response.headers.get('X-Micromind-Image-Size') ?? 'null'); }
    catch { throw problem('Live image sample metadata is unreadable'); }
    if (!sample || sample.run !== this.options.run || typeof sample.boot !== 'string' || !sample.boot
      || !Number.isSafeInteger(sample.seq) || sample.seq! < 0
      || Object.keys(sample).some((key) => !['run', 'boot', 'seq', 'frame', 'epoch'].includes(key))
      || ['frame', 'epoch'].some((key) => { const value = sample[key as 'frame' | 'epoch']; return value !== undefined && value !== null && (!Number.isSafeInteger(value) || value < 0); })) throw problem('Live image belongs to an unknown camera sample');
    if (!Array.isArray(size) || size.length !== 2 || size.some((value) => !Number.isSafeInteger(value) || value <= 0 || value > 16384)) throw problem('Live image dimensions are invalid');
    const digest = response.headers.get('X-Micromind-Content-Sha256') ?? '';
    if (!/^[a-f0-9]{64}$/.test(digest)) throw problem('Live image byte identity is missing');
    return { sample, size, digest };
  }
  private async next(stream: Stream): Promise<void> {
    if (!this.active(stream)) return;
    try {
      const response = await this.options.fetch(stream.url, { credentials: 'same-origin', redirect: 'error', cache: 'no-store', signal: stream.abort.signal });
      if (!this.active(stream)) return;
      if (response.status === 425) return; // An owned producer has not supplied a frame yet.
      if (!response.ok || response.redirected || response.headers.get('Content-Type')?.split(';')[0] !== 'image/jpeg'
        || response.headers.get('X-Micromind-Source-Token') !== stream.token) throw problem('Live image lease is unavailable or changed');
      const { sample, size, digest } = this.sample(response);
      const state = response.headers.get('X-Micromind-Live-State') ?? 'live';
      if (!['live', 'held'].includes(state)) throw problem('Live image state is unknown');
      const held = state === 'held';
      if (!response.body) throw problem('Live image has no bytes');
      const reader = response.body.getReader(), chunks: Uint8Array<ArrayBuffer>[] = []; let total = 0;
      try {
        while (true) {
          const part = await reader.read(); if (part.done) break;
          total += part.value.byteLength;
          if (total > MAX_BYTES || !this.active(stream)) { await reader.cancel(); throw problem('Live image exceeded its active byte allowance'); }
          chunks.push(new Uint8Array(part.value));
        }
      } finally { reader.releaseLock(); }
      if (!total) throw problem('Live image is empty');
      const bytes = new Uint8Array(total); let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      const actual = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), (byte) => byte.toString(16).padStart(2, '0')).join('');
      if (actual !== digest) throw problem('Live image bytes differ from their sample');
      if (!this.active(stream)) return;
      const url = this.options.createObjectURL(new Blob([bytes], { type: 'image/jpeg' })); stream.pendingURL = url;
      const delivered = new Map<string, WindowState>();
      await Promise.all([...stream.owners.values()].map(async (owner) => {
        const image = document.createElement('img'); image.alt = owner.title; image.draggable = false; image.src = url;
        const decode = new AbortController(); stream.decodes.set(owner.id, decode);
        const signal = AbortSignal.any([stream.abort.signal, decode.signal]);
        let cancel: (() => void) | undefined;
        try {
          await Promise.race([image.decode(), new Promise<never>((_resolve, reject) => {
            cancel = () => reject(new DOMException('Live delivery retired', 'AbortError'));
            signal.addEventListener('abort', cancel, { once: true });
          })]);
        } catch (error) {
          if (signal.aborted || !this.active(stream)) return;
          this.options.windows.failLiveSource(owner.id, stream.token, owner.sourceGeneration, owner.lifetimeGeneration,
            error instanceof Error ? error.message : 'Live image could not decode');
          stream.owners.delete(owner.id); return;
        } finally {
          if (cancel) signal.removeEventListener('abort', cancel);
          if (stream.decodes.get(owner.id) === decode) stream.decodes.delete(owner.id);
        }
        if (!this.active(stream)) return;
        const current = stream.owners.get(owner.id);
        if (!current || current.sourceGeneration !== owner.sourceGeneration || current.lifetimeGeneration !== owner.lifetimeGeneration) return;
        if (image.naturalWidth !== size[0] || image.naturalHeight !== size[1]) throw problem('Decoded live image dimensions differ from its sample');
        this.options.windows.drawLiveFrame(owner.id, { image, sample, held, sourceToken: stream.token, sourceGeneration: owner.sourceGeneration, lifetimeGeneration: owner.lifetimeGeneration });
        delivered.set(owner.id, owner);
      }));
      if (!this.active(stream)) return;
      if (!stream.owners.size) { this.stop(stream); return; }
      if (stream.currentURL) this.options.revokeObjectURL(stream.currentURL);
      stream.currentURL = url; stream.pendingURL = null;
      // An owner can join while an earlier owner's image is decoding. Give it
      // one bounded follow-up request before stopping a held stream's polling.
      stream.held = held && [...stream.owners.values()].every(owner => {
        const drawn = delivered.get(owner.id);
        return drawn?.sourceGeneration === owner.sourceGeneration && drawn.lifetimeGeneration === owner.lifetimeGeneration;
      });
    } catch (error) {
      if (!this.active(stream)) return;
      for (const owner of stream.owners.values()) this.options.windows.failLiveSource(owner.id, owner.delivery.sourceToken,
        owner.sourceGeneration, owner.lifetimeGeneration, error instanceof Error ? error.message : 'Live image could not load');
      this.stop(stream); return;
    } finally {
      if (this.active(stream) && !stream.held) stream.timer = setTimeout(() => { stream.timer = null; void this.next(stream); }, INTERVAL_MS);
    }
  }
  dispose(): void { for (const stream of this.streams.values()) this.stop(stream); this.bindings.clear(); }
}
