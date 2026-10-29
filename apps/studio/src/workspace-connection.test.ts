// TEST-only Connection precursor: actual public Link/Connection/decodeFrame methods.
// External SYNTHETIC WebSocket, bitmap decoder and timer facts; no browser pixels,
// accepted App/Screen authority, Runtime installation, model or server execution.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { Connection } from './protocol';
import type { Frame, FrameHeader, ServerMessage } from './protocol';

class SyntheticSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static readonly instances: SyntheticSocket[] = [];
  readonly sent: string[] = [];
  readonly url: string;
  binaryType = '';
  readyState = SyntheticSocket.CONNECTING;
  onopen: ((event: Event) => unknown) | null = null;
  onclose: ((event: Event) => unknown) | null = null;
  onmessage: ((event: MessageEvent) => unknown) | null = null;
  constructor(url: string) { this.url = url; SyntheticSocket.instances.push(this); }
  send(data: string) { this.sent.push(data); }
  opened() { this.readyState = SyntheticSocket.OPEN; return this.onopen?.(new Event('open')); }
  disconnected() { this.readyState = SyntheticSocket.CLOSED; return this.onclose?.(new Event('close')); }
  close() { return this.disconnected(); }
  text(message: unknown) { return this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent); }
  binary(data: ArrayBuffer) { return this.onmessage?.({ data } as MessageEvent); }
}
const proposal = { schema: 'workspace-origin-v2', workspaceId: 'synthetic-workspace', clientId: 'synthetic-client', pageEpoch: 'synthetic-page' } as const;
type Ready = {
  type: 'workspace-ready'; schema: 'workspace-origin-v2'; ok: true; requestId: string;
  client: { workspaceId: string; clientId: string; pageEpoch: string; connectionEpoch: string };
  appRunId: string; connectionGrant: string;
};
// Frozen hello wire is missing from ClientCommand at this TEST checkpoint. This assertion
// only permits calling the existing real method; it adds no settlement or handshake policy.
function hello(connection: Connection) {
  const request = connection.request.bind(connection) as unknown as (type: 'workspace-hello', fields: typeof proposal) => Promise<Ready>;
  return request('workspace-hello', proposal);
}
function ready(requestId: string, incarnation = 'first'): Ready {
  return { type: 'workspace-ready', schema: 'workspace-origin-v2', ok: true, requestId,
    client: { workspaceId: proposal.workspaceId, clientId: proposal.clientId, pageEpoch: proposal.pageEpoch, connectionEpoch: `synthetic-${incarnation}` },
    appRunId: 'synthetic-run', connectionGrant: `synthetic-grant-${incarnation}` };
}
function watch<T>(promise: Promise<T>) {
  const resolved = vi.fn<(value: T) => void>(), rejected = vi.fn<(error: unknown) => void>();
  void promise.then(resolved, rejected); return { resolved, rejected };
}
async function microtasks() { await Promise.resolve(); await Promise.resolve(); }
function socket(index = SyntheticSocket.instances.length - 1) {
  const result = SyntheticSocket.instances[index]; expect(result).toBeDefined(); return result;
}
function lastRequest(native: SyntheticSocket): string {
  const command = JSON.parse(native.sent.at(-1)!); expect(typeof command.requestId).toBe('string'); return command.requestId;
}
function nativeBitmap() {
  const close = vi.fn(); return { bitmap: { width: 64, height: 48, close } as unknown as ImageBitmap, close };
}
function deferredBitmap() {
  let resolve!: (value: ImageBitmap) => void;
  const promise = new Promise<ImageBitmap>((done) => { resolve = done; });
  return { promise, resolve };
}
function packet(id: number): ArrayBuffer {
  const header: FrameHeader = { type: 'frame', id, w: 64, h: 48, dx: 0, dy: 0, epoch: 2, tracked: true, steady: true,
    speed: 0, t: 1, sent: 1, fov: null };
  const encoded = new TextEncoder().encode(JSON.stringify(header)), jpeg = new Uint8Array([97, 98, 99]);
  const bytes = new Uint8Array(8 + encoded.length + jpeg.length);
  bytes.set(new TextEncoder().encode('MMF1')); new DataView(bytes.buffer).setUint32(4, encoded.length);
  bytes.set(encoded, 8); bytes.set(jpeg, 8 + encoded.length); return bytes.buffer;
}
function connectionWorld() {
  const frames: Frame[] = [], messages: ServerMessage[] = [], status: boolean[] = [];
  const connection = new Connection((frame) => frames.push(frame), (message) => messages.push(message), (connected) => status.push(connected));
  connection.open(); return { connection, frames, messages, status, first: socket() };
}
let decode: ReturnType<typeof vi.fn<typeof createImageBitmap>>;
beforeEach(() => {
  SyntheticSocket.instances.length = 0; vi.useFakeTimers();
  vi.stubGlobal('WebSocket', SyntheticSocket); vi.stubGlobal('location', { protocol: 'https:', host: 'synthetic.invalid' });
  decode = vi.fn<typeof createImageBitmap>(async () => nativeBitmap().bitmap); vi.stubGlobal('createImageBitmap', decode);
});
afterEach(() => {
  for (const native of SyntheticSocket.instances) native.onopen = native.onclose = native.onmessage = null;
  vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals();
});

describe('actual Connection request and current-frame positive controls', () => {
  test('an action before connection is refused clearly and never replayed on reconnect', async () => {
    const { connection, first } = connectionWorld();
    await expect(connection.request('scores', {})).rejects.toThrow('This action was not sent. Wait for the workspace to reconnect, then try again.');
    expect(first.sent).toEqual([]);
    first.opened(); await microtasks(); expect(first.sent).toEqual([]);
    const retried = connection.request('scores', {});
    await first.text({type:'result',for:'scores',requestId:lastRequest(first),ok:true});
    await expect(retried).resolves.toMatchObject({ok:true});
  });

  test('ordinary matching result and server rejection still settle through the public request method', async () => {
    const { connection, first, messages } = connectionWorld(); first.opened();
    expect(first.url).toBe('wss://synthetic.invalid/ws'); expect(first.binaryType).toBe('arraybuffer');
    const request = watch(connection.request('scores', {})), requestId = lastRequest(first);
    expect(JSON.parse(first.sent.at(-1)!)).toEqual({ type: 'scores', requestId });
    await first.text({ type: 'result', for: 'scores', requestId: 'unrelated', ok: true }); await microtasks();
    expect(request.resolved).not.toHaveBeenCalled();
    const reply = { type: 'result', for: 'scores', requestId, ok: true, scores: { votes: 2, kinds: [] } };
    await first.text(reply); await microtasks(); expect(request.resolved).toHaveBeenCalledExactlyOnceWith(reply);
    const refused = watch(connection.request('arena', { arena: { enabled: true } }));
    await first.text({ type: 'result', for: 'arena', requestId: lastRequest(first), ok: false, error: 'synthetic refusal' }); await microtasks();
    expect(refused.rejected).toHaveBeenCalledTimes(1); expect(refused.rejected.mock.calls[0][0]).toBeInstanceOf(Error);
    expect((refused.rejected.mock.calls[0][0] as Error).message).toBe('synthetic refusal'); expect(messages).toContainEqual(reply);
  });
  test('current native binary delivery uses actual decodeFrame and delivers its decoded bitmap', async () => {
    const { first, frames } = connectionWorld(); first.opened(); const current = nativeBitmap(); decode.mockResolvedValueOnce(current.bitmap);
    await first.binary(packet(42));
    expect(decode).toHaveBeenCalledTimes(1); const blob = decode.mock.calls[0][0]; expect(blob).toBeInstanceOf(Blob);
    expect((blob as Blob).type).toBe('image/jpeg'); expect(Array.from(new Uint8Array(await (blob as Blob).arrayBuffer()))).toEqual([97, 98, 99]);
    expect(frames).toHaveLength(1); expect(frames[0].header).toMatchObject({ id: 42, w: 64, h: 48, epoch: 2 });
    expect(frames[0].bitmap).toBe(current.bitmap); expect(current.close).not.toHaveBeenCalled();
  });
  test('frames decode one at a time, the newest waiting wins, and an older frame is never shown', async () => {
    // The audit of 2026-10-12: every frame started its own decode, so a busy browser piled them up and fell behind.
    const { first, frames } = connectionWorld(); first.opened();
    const slow = deferredBitmap();
    decode.mockReturnValueOnce(slow.promise);
    const firstDone = first.binary(packet(1)); // decoding
    void first.binary(packet(2)); // waits
    void first.binary(packet(3)); // replaces 2 while 1 decodes
    slow.resolve(nativeBitmap().bitmap);
    await firstDone; await microtasks(); await microtasks();
    expect(frames.map((f) => f.header.id)).toEqual([1, 3]);
    expect(decode).toHaveBeenCalledTimes(2); // 2 was never decoded
    const stale = nativeBitmap(); decode.mockResolvedValueOnce(stale.bitmap);
    await first.binary(packet(2)); await microtasks(); // older than the one shown
    expect(frames.map((f) => f.header.id)).toEqual([1, 3]); expect(stale.close).toHaveBeenCalled();
  });
  test('workspace-hello is resolved only by its matching actual workspace-ready reply', async () => {
    const { connection, first, messages } = connectionWorld(); first.opened(); const pending = watch(hello(connection));
    const requestId = lastRequest(first); expect(JSON.parse(first.sent.at(-1)!)).toEqual({ type: 'workspace-hello', ...proposal, requestId });
    await first.text(ready('unrelated')); await microtasks(); expect(pending.resolved).not.toHaveBeenCalled();
    const accepted = ready(requestId); await first.text(accepted); await microtasks();
    expect(pending.resolved).toHaveBeenCalledExactlyOnceWith(accepted); expect(pending.rejected).not.toHaveBeenCalled();
    expect(messages).toContainEqual(accepted);
  });
  test('same-page explicit reconnect preserves proposal fields and current ordinary requests', async () => {
    const { connection, first, status } = connectionWorld(); first.opened(); watch(hello(connection));
    const original = JSON.parse(first.sent.at(-1)!); first.disconnected(); connection.open(); const current = socket(); current.opened();
    watch(hello(connection)); const next = JSON.parse(current.sent.at(-1)!);
    expect(next).toMatchObject({ type: 'workspace-hello', ...proposal }); expect(original).toMatchObject({ type: 'workspace-hello', ...proposal });
    expect(next.requestId).not.toBe(original.requestId); expect(status).toEqual([true, false, true]);
    const pending = watch(connection.request('scores', {})), reply = { type: 'result', for: 'scores', requestId: lastRequest(current), ok: true };
    await current.text(reply); await microtasks(); expect(pending.resolved).toHaveBeenCalledExactlyOnceWith(reply);
    expect(first.sent).toHaveLength(1); expect(current.sent).toHaveLength(2);
  });
  test('current disconnect schedules the existing retry and its current socket delivers replies', async () => {
    const { connection, first, status } = connectionWorld(); first.opened(); first.disconnected();
    vi.advanceTimersByTime(499); expect(SyntheticSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1); expect(SyntheticSocket.instances).toHaveLength(2); const current = socket(); current.opened();
    expect(status).toEqual([true, false, true]);
    const pending = watch(connection.request('scores', {})), reply = { type: 'result', for: 'scores', requestId: lastRequest(current), ok: true };
    await current.text(reply); await microtasks(); expect(pending.resolved).toHaveBeenCalledExactlyOnceWith(reply);
  });
});

describe('actual Connection socket incarnation and awaited decoder boundaries', () => {
  test('stale socket open and close callbacks cannot change the newer socket status or retry', () => {
    const { connection, first, status } = connectionWorld(); first.opened(); connection.open(); const current = socket(); current.opened();
    const before = status.slice(); first.opened(); first.disconnected();
    expect(status).toEqual(before); vi.advanceTimersByTime(500); expect(SyntheticSocket.instances).toHaveLength(2);
    connection.send({ type: 'scores' }); expect(JSON.parse(current.sent.at(-1)!)).toEqual({ type: 'scores' });
  });
  test('stale text reply cannot settle a newer request or enter its message stream', async () => {
    const { connection, first, messages } = connectionWorld(); first.opened(); connection.open(); const current = socket(); current.opened();
    const pending = watch(connection.request('scores', {})), requestId = lastRequest(current);
    const stale = { type: 'result', for: 'scores', requestId, ok: true, marker: 'stale-socket' };
    await first.text(stale); await microtasks(); expect(pending.resolved).not.toHaveBeenCalled(); expect(messages).not.toContainEqual(stale);
    const actual = { type: 'result', for: 'scores', requestId, ok: true, marker: 'current-socket' };
    await current.text(actual); await microtasks(); expect(pending.resolved).toHaveBeenCalledExactlyOnceWith(actual);
  });
  test('an awaited old binary decode closes its bitmap after replacement and never installs that frame', async () => {
    const { connection, first, frames } = connectionWorld(); first.opened(); const held = deferredBitmap(), stale = nativeBitmap(), current = nativeBitmap();
    decode.mockImplementationOnce(() => held.promise).mockResolvedValueOnce(current.bitmap);
    const oldDelivery = first.binary(packet(10)); expect(decode).toHaveBeenCalledTimes(1);
    connection.open(); const newest = socket(); newest.opened(); await newest.binary(packet(11));
    expect(frames.map((frame) => frame.header.id)).toEqual([11]); expect(frames[0].bitmap).toBe(current.bitmap);
    held.resolve(stale.bitmap); await oldDelivery;
    expect(frames.map((frame) => frame.header.id)).toEqual([11]); expect(stale.close).toHaveBeenCalledTimes(1); expect(current.close).not.toHaveBeenCalled();
  });
  test('a queued old retry cannot reopen over an explicitly installed newer socket', () => {
    const { connection, first, status } = connectionWorld(); first.opened(); first.disconnected(); connection.open();
    const current = socket(); current.opened(); const before = status.slice(); vi.advanceTimersByTime(500);
    expect(SyntheticSocket.instances).toHaveLength(2); expect(status).toEqual(before);
    connection.send({ type: 'scores' }); expect(current.sent).toEqual([JSON.stringify({ type: 'scores' })]);
  });
  test('disconnect rejects original pending requests and another incarnation cannot settle them', async () => {
    const { connection, first } = connectionWorld(); first.opened(); const old = watch(connection.request('scores', {})), oldId = lastRequest(first);
    first.disconnected(); await microtasks(); expect(old.rejected).toHaveBeenCalledTimes(1); expect(old.rejected.mock.calls[0][0]).toBeInstanceOf(Error);
    expect(old.resolved).not.toHaveBeenCalled(); vi.advanceTimersByTime(500); const current = socket(); current.opened();
    const fresh = watch(connection.request('scores', {})), freshId = lastRequest(current); expect(freshId).not.toBe(oldId);
    await current.text({ type: 'result', for: 'scores', requestId: oldId, ok: true }); await microtasks();
    expect(old.resolved).not.toHaveBeenCalled(); expect(fresh.resolved).not.toHaveBeenCalled(); expect(old.rejected).toHaveBeenCalledTimes(1);
    const reply = { type: 'result', for: 'scores', requestId: freshId, ok: true }; await current.text(reply); await microtasks();
    expect(fresh.resolved).toHaveBeenCalledExactlyOnceWith(reply);
  });
  test('explicit socket replacement retires pending requests before any old close arrives', async () => {
    const { connection, first } = connectionWorld(); first.opened(); const old = watch(connection.request('scores', {})), oldId = lastRequest(first);
    connection.open(); const current = socket(); current.opened(); await microtasks();
    expect(old.rejected).toHaveBeenCalledTimes(1); expect(old.rejected.mock.calls[0][0]).toBeInstanceOf(Error);
    await first.text({ type: 'result', for: 'scores', requestId: oldId, ok: true }); await current.text({ type: 'result', for: 'scores', requestId: oldId, ok: true }); await microtasks();
    expect(old.resolved).not.toHaveBeenCalled(); expect(old.rejected).toHaveBeenCalledTimes(1);
  });
});


test('Project layout restoration needs an exact acknowledgement; legacy readiness cannot enable a second owner', async()=>{
  const {connection,first}=connectionWorld();first.opened();
  const pending=watch(connection.request('workspace-hello',{...proposal,restoration:'project-layout'}));
  await microtasks();
  expect(pending.rejected).not.toHaveBeenCalled();
  const requestId=lastRequest(first);
  await first.text(ready(requestId));await microtasks();
  expect(pending.resolved).not.toHaveBeenCalled();
  expect(pending.rejected).toHaveBeenCalledOnce();
  const retry=watch(connection.request('workspace-hello',{...proposal,restoration:'project-layout'}));
  const accepted={...ready(lastRequest(first)),restoration:'project-layout'};
  await first.text(accepted);await microtasks();
  expect(retry.resolved).toHaveBeenCalledExactlyOnceWith(accepted);
});
