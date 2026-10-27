// The view follows the session (the user's direction of 2026-10-12; its sources since ADR 0022): what the stage shows
// when a session opens. A new session opens its home over the stage (session-home.ts), where the user chooses the live
// camera, a recording or a picture; the app looks at nothing behind it until then (`view {held}`). A returning session
// lands on its last source, which the server keeps in the session's journal (GET /api/sessions/{sid}/sources,
// `onStage`): a recording paused where it was left, a picture held, the live camera live; with nothing kept, or a source
// taken out since, it lands live. Connecting (or a restart) keeps what plays: that is continuing work, not looking back.
// Between the two (StageGate), nothing of the session left is drawn: the stage is empty and says what is opening until
// the landing's `source` reply, and every landing ends in one (the server looks at nothing of the new session until then).
//
// Only the studio and the bridge know what the feed is (AGENTS.md "Architecture"), and the server knows the session's
// own assets (ADR 0022): this module decides, main.ts applies it (an asset through the server's `source` command, the
// camera through camera.ts). Pure: nothing here is kept in the browser.

/** Why a session was opened (the server's `session-cleared`): started by the user, gone back to, or the app's start
 * (a studio that connects is treated as `start`). */
export type OpenWhy = 'new' | 'switch' | 'start';

/** What a session's sources say about where it was (the server's wire, as much of it as the landing reads). */
export interface StageFacts {
  onStage: string | null;
  sources: readonly { id: string; kind: string; position: number | null }[];
}

/** Where the stage lands: the new session's home, what plays kept, the live camera, or one of the session's assets,
 * always held at first (a recording at `at` seconds, a picture as it is). */
export type Landing = 'home' | 'keep' | 'live' | { asset: string; at: number; paused: true };

/** Where the stage lands when a session is opened. A returning session never plays on by itself: a recording comes
 * back paused where it was left (else at its start); a source no longer the session's (taken out, or its file gone)
 * counts as nothing kept, which is live. */
export function viewOnOpen(why: OpenWhy | null | undefined, facts: StageFacts | null | undefined,
  nothingPlays = false): Landing {
  if (why === 'new') return 'home';
  if (why !== 'switch') {
    // A start keeps what plays (continuing work). After a full restart nothing plays (the camera bridge started again:
    // no asset on it, the camera with no picture), and the session's last source is one of its assets: that comes back
    // held, so returning work is on the stage, not an empty one (Codex's acceptance of f4705871). The live camera is
    // never taken over, and nothing starts by itself.
    const landing = nothingPlays ? viewOnOpen('switch', facts) : 'keep';
    return typeof landing === 'object' ? landing : 'keep';
  }
  const stage = facts?.onStage;
  if (!stage || stage === 'live') return 'live';
  const row = facts!.sources.find((s) => s.id === stage);
  if (!row) return 'live';
  const at = row.kind === 'still' ? 0 : row.position;
  return { asset: row.id, at: typeof at === 'number' && Number.isFinite(at) && at > 0 ? at : 0, paused: true };
}

/** The camera bridge's states that mean it is up with no picture (the server's frames.py): waiting for its first frame,
 * stale, No camera, or reachable but not live. "live" plays; "bridge_offline", a failed or missing health, plays nothing we can
 * land on, so it is never idle. */
export const NO_PICTURE: ReadonlySet<string> = new Set(['waiting', 'stale', 'bridge_not_live', 'no_camera']);

/** What a start decides: 'wait' while the feed or the camera's state is not read yet (looked at again once both are);
 * else viewOnOpen's start, nothing playing only when the feed is the camera, idle, and the server says it has no picture. */
export function onStart(facts: StageFacts | null | undefined, feed: { source: string; busy: boolean } | null,
  health: { state: string } | null, freshFrame = false): Landing | 'wait' {
  if (freshFrame) return 'keep'; // a frame is being drawn now: something plays, whatever the last health read said
  if (!feed || !health) return 'wait';
  return viewOnOpen('start', facts, feed.source === 'camera' && !feed.busy && NO_PICTURE.has(health.state));
}

/** A start's pending second look (onStart said 'wait'). */
export interface StartLook { token: number; sid: string; until: number; choice: number; stage: string | null }

/** Whether a start may still land after its sources loaded: never once the user chose what is on stage meanwhile (its
 * explicit choice, Live camera, Connect, an asset, footage, Reconnect, moved `choiceNow` on), nor once the session's stage is
 * no longer the one it was decided from (`stageNow`: another studio's choice, come over the sources wire while it loaded).
 * Other openings land as before. */
export function startMayLand(why: OpenWhy | null | undefined, choiceAtOpen: number, choiceNow: number,
  stageDecided: string | null = null, stageNow: string | null = null): boolean {
  return why !== 'start' || (choiceAtOpen === choiceNow && stageDecided === stageNow);
}

/** What a pending second look does now: 'drop' it (another opening, another session, a choice since, or its time over),
 * 'hold' (this studio is not in view: only a studio in view lands, so two never fight over the shared feed), or 'look'. */
export function lookState(look: StartLook,
  now: { token: number; sid: string; choice: number; visible: boolean; at: number; stage: string | null }): 'drop' | 'hold' | 'look' {
  if (look.token !== now.token || look.sid !== now.sid || look.choice !== now.choice || now.at > look.until) return 'drop';
  if (look.stage !== now.stage) return 'drop'; // the session's stage changed since (another studio chose): theirs stays
  return now.visible ? 'look' : 'hold';
}

/** Whether a `session` message is a connection's start: a studio loading, or reconnecting after the app restarted. The
 * server sends no `session-cleared` then (it changed no session), so the start's landing runs from the first `session`
 * message after the connection's `hello`, unless a `session-cleared` came first (that opening is landed already).
 * Actual acceptance of 0c061ba1: startup never ran the start's landing at all. */
export class ConnectionStart {
  private armed = false;

  /** A new connection's first message. */
  hello(): void {
    this.armed = true;
  }

  /** A session was opened (new, switched, or the server's own start): landed by that opening. */
  cleared(): void {
    this.armed = false;
  }

  /** A `session` message: true once per connection when it is the connection's start. */
  session(home: 'empty-camera' | 'chosen' | null = null): boolean {
    const start = this.armed && home !== 'chosen';
    this.armed = false;
    return start;
  }
}

// --- the stage between two sessions -------------------------------------------------------------------------------

/** How long a session's opening keeps the stage empty while its source lands; then frames are drawn as they come. */
export const LAND_WAIT_MS = 8000;
/** After the source landed, how long frames the camera bridge took before it are still left out (the last source's,
 * still on their way). */
export const LAND_GRACE_MS = 1000;

/** The stage between two sessions (the first live try of sources, 2026-10-12: after a switch the stage kept the last
 * session's picture, its footage playing on until the landing switched the camera bridge, then its last frame dimmed
 * under "Not live"). From a session's opening (the click, else the server's `session-cleared`) until its landing's
 * `source` reply, or LAND_WAIT_MS, no frame is drawn and the stage says only what is opening; after the reply, frames
 * the bridge took before it are left out for LAND_GRACE_MS. `now` is in ms (the page's clock), `wall` in seconds (the
 * bridge's clock: a frame's `ba`). */
export class StageGate {
  private until = 0;
  private said = '';
  private after: { wall: number; at: number } | null = null;

  /** A session is opening: nothing is drawn until its source lands. `words`: what the stage says meanwhile ('' when a
   * new session's home covers it). */
  hold(now: number, words = ''): void {
    this.until = now + LAND_WAIT_MS;
    this.said = words;
    this.after = null;
  }

  /** Its source is on stage (the `source` reply came at `wall`): frames are drawn again, but not the last source's. */
  settle(now: number, wall: number): void {
    this.until = 0;
    this.said = '';
    this.after = { wall, at: now };
  }

  /** Nothing to wait for (a new session's home covers the stage): frames are drawn as they come. */
  release(): void {
    this.until = 0;
    this.said = '';
    this.after = null;
  }

  holding(now: number): boolean {
    return now < this.until;
  }

  /** What the stage says while it holds ('' when it does not, or says nothing). */
  words(now: number): string {
    return this.holding(now) ? this.said : '';
  }

  /** Whether a frame is drawn. */
  accept(header: { ba?: number | null }, now: number): boolean {
    if (this.holding(now)) return false;
    const after = this.after;
    if (after) {
      if (now - after.at < LAND_GRACE_MS && typeof header.ba === 'number' && header.ba < after.wall) return false;
      this.after = null; // the new source's first frame, or the grace is over
    }
    return true;
  }
}

/** Whether the camera's empty home may open over the stage now: not while a saved result is shown in a window of its
 * own there (the retained specimen view with its outlines, as after a restart's reopen), which it would sit behind as an
 * empty backdrop (actual browser acceptance on 0664d54b). Once that window closes or is tucked away, the home opens
 * again on the next camera check. */
export function cameraHomeWanted(retained: readonly { visible: boolean }[]): boolean {
  return !retained.some((view) => view.visible);
}

/** Camera diagnostics can lag a successful server source change. They describe
 * acquisition, not whether the Project has an accepted scientific input. */
export function emptyCameraHomeWanted(facts: {
  cameraOff: boolean; asset: boolean; footage: boolean; busy: boolean; holding: boolean; onStage: string | null;
}): boolean {
  const selectedAsset = facts.onStage !== null && facts.onStage !== 'live';
  return facts.cameraOff && !selectedAsset && !facts.asset && !facts.footage && !facts.busy && !facts.holding;
}
