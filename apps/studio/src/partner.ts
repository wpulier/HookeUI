// The studio's half of the one-brain voice's own messages (voice.ts onPartner): the window as it is now when the
// server asks (act, then look) or when a turn starts, and the marks it asks for, each answered at once. The window is
// taken in the task the request arrives in and composed in short tasks after it (screen.ts captureWindow), so the live
// view never waits for it.
import type { ControlMessage, DriveEnd, DriveMessage, MarksMessage, MarkResolution, PartnerCommand, PartnerMessage } from './protocol';

export type WindowPicture = { jpeg: string; w: number; h: number };
export interface WindowCapture {
  /** A granted tab share's newest frame, or null without one. */
  share(maxSide: number): Promise<WindowPicture | null>;
  /** The window composed from the page (screen.ts captureWindow). */
  compose(maxSide: number): Promise<WindowPicture>;
}

/** The snapshot message for `id`: the shared tab when there is one, else the composed window; an error said, never a
 * silence the server waits 1.5 s on. At most 1280 px on its longer side. */
export async function windowSnapshot(id: string, maxSide: number, capture: WindowCapture, now: () => number = Date.now): Promise<PartnerCommand> {
  const side = Math.max(256, Math.min(1280, Number.isFinite(maxSide) ? maxSide : 1280));
  try {
    const at = now();
    // Without a share this resolves at once, so the window's pictures are still taken in the asking task.
    const shared = await capture.share(side);
    const picture = shared ?? await capture.compose(side);
    if (!picture.jpeg) throw new Error('nothing to draw');
    return { type: 'snapshot', id, jpeg: picture.jpeg, w: picture.w, h: picture.h, at, how: shared ? 'tab-share' : 'composed' };
  } catch (error) {
    return { type: 'snapshot', id, error: `The window could not be captured: ${error instanceof Error ? error.message : String(error)}`.slice(0, 200) };
  }
}

/** Answers each partner message: a snapshot request with the window now, marks with which were drawn. */
export function partnerHandler(options: {
  reply: (command: PartnerCommand) => boolean;
  capture: WindowCapture;
  marks: { show(message: MarksMessage): MarkResolution[]; clear(id?: string): void };
  /** Hands (drive.ts): the partner cursor and who has control. A cue planned against an older control version (the
   * user took control back since) is never drawn. */
  drive?: { cursor: { show(message: DriveMessage): void; end(message: DriveEnd): void };
    takeover: { readonly version: number; control(message: ControlMessage): void } };
}): (message: PartnerMessage) => void {
  return (message) => {
    if (message.type === 'control') { options.drive?.takeover.control(message); return; }
    if (message.type === 'drive') {
      if (options.drive && Number(message.version) >= options.drive.takeover.version) options.drive.cursor.show(message);
      else if (options.drive) options.reply({ type: 'drive-shown', id: String(message.id), ok: false, why: 'stale' });
      return;
    }
    if (message.type === 'drive-end') { options.drive?.cursor.end(message); return; }
    if (message.type === 'snapshot-request') void windowSnapshot(String(message.id), Number(message.maxSide ?? 1280), options.capture).then(options.reply);
    else if (message.type === 'marks') options.reply({ type: 'marks-shown', id: String(message.id), resolved: options.marks.show(message) });
    else if (message.type === 'marks-clear') options.marks.clear(message.id);
  };
}
