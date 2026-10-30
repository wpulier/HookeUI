// The microphone moment (Will's feel check, 29 Oct: "a much sexier enable audio IF it's not enabled, as that makes the
// first time attempt not great"). When the microphone is not allowed yet, Talk first shows one calm card in the
// Assistant column: what happens, in one line, and one button. Its click starts Talk, so the browser's own prompt comes
// as the answer to a click the user meant, and the connection follows at once (voice.ts). While that prompt is up the
// card says so; it is never shown as "Connecting". Blocked, the card says how to turn the microphone back on.

/** The microphone permission as the browser says it; "unknown" where it cannot say (no Permissions API for it). */
export type MicPermission = 'granted' | 'prompt' | 'denied' | 'unknown';

/** What the card shows: the first ask, the browser's prompt being up, or a blocked microphone. */
export type MicCardState = 'ask' | 'asking' | 'blocked';

type PermissionsLike = { query(descriptor: { name: string }): Promise<{ state: string; onchange?: (() => void) | null }> };

/** The microphone permission now, and each change of it after (`changed`), where the browser can say. */
export async function micPermission(permissions: PermissionsLike | undefined,
  changed?: (state: MicPermission) => void): Promise<MicPermission> {
  if (!permissions?.query) return 'unknown';
  try {
    const status = await permissions.query({ name: 'microphone' });
    const read = (): MicPermission => (['granted', 'prompt', 'denied'].includes(status.state) ? status.state as MicPermission : 'unknown');
    if (changed) status.onchange = () => changed(read());
    return read();
  } catch {
    return 'unknown'; // a browser that does not know the name (Firefox): it asks itself
  }
}

/** Whether Talk shows the card before starting: blocked, or not allowed yet. A browser that cannot say, or the Mac app
 * once it has heard you (its web view may keep answering "prompt"), starts at once and asks itself. */
export function needsMicCard(permission: MicPermission, heardBefore: boolean, macApp: boolean): MicCardState | null {
  if (permission === 'denied') return 'blocked';
  if (permission === 'prompt' && !(macApp && heardBefore)) return 'ask';
  return null;
}

export interface MicCardWords { title: string; line: string; button: string | null; foot?: string }

/** The card's words for its state: one line of what happens, then what to do. */
export function micCardWords(state: MicCardState, keyWords: string, macApp: boolean): MicCardWords {
  if (state === 'asking') {
    return { title: 'Allow the microphone', line: macApp ? 'Choose Allow when your Mac asks.' : 'Choose Allow in the browser’s prompt, at the top of the window.', button: null };
  }
  if (state === 'blocked') {
    return {
      title: 'The microphone is off for Hooke',
      line: macApp ? 'Turn it on in System Settings → Privacy & Security → Microphone, then try again.'
        : 'Turn it on from the site settings: the icon at the left of the address bar, then Microphone → Allow. Then try again.',
      button: 'Try again',
    };
  }
  return { title: 'Talk with Hooke', line: `Hold ${keyWords} and talk; Hooke listens only while you hold it.`, button: 'Turn on the microphone',
    foot: 'Your browser will ask once.' };
}

/** The card itself, in the Assistant column above the composer. `act` is its button: it starts Talk in that click. */
export class MicCard {
  readonly root: HTMLElement;
  state: MicCardState | null = null;
  private readonly title: HTMLElement;
  private readonly line: HTMLElement;
  private readonly foot: HTMLElement;
  private readonly button: HTMLButtonElement;
  private readonly close: HTMLButtonElement;
  private readonly words: () => { keyWords: string; macApp: boolean };

  constructor(options: { act: () => void; words: () => { keyWords: string; macApp: boolean }; doc?: Document }) {
    const doc = options.doc ?? document;
    this.words = options.words;
    this.root = doc.createElement('section');
    this.root.className = 'mic-card';
    this.root.hidden = true;
    this.root.setAttribute('role', 'status');
    this.root.setAttribute('aria-live', 'polite');
    const icon = doc.createElement('span');
    icon.className = 'mic-card-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = '<svg viewBox="0 0 16 16"><rect x="5.5" y="1.75" width="5" height="8" rx="2.5"/><path d="M3.25 7.5a4.75 4.75 0 0 0 9.5 0M8 12.25v2"/></svg>';
    const body = doc.createElement('div');
    body.className = 'mic-card-body';
    this.title = Object.assign(doc.createElement('strong'), { className: 'mic-card-title' });
    this.line = Object.assign(doc.createElement('p'), { className: 'mic-card-line' });
    this.foot = Object.assign(doc.createElement('p'), { className: 'mic-card-foot' });
    this.button = Object.assign(doc.createElement('button'), { type: 'button', className: 'mic-card-act' });
    this.button.addEventListener('click', () => options.act());
    this.close = Object.assign(doc.createElement('button'), { type: 'button', className: 'mic-card-close', textContent: '×' });
    this.close.setAttribute('aria-label', 'Not now');
    this.close.addEventListener('click', () => this.hide());
    body.append(this.title, this.line, this.button, this.foot);
    this.root.append(icon, body, this.close);
  }

  show(state: MicCardState): void {
    this.state = state;
    const { keyWords, macApp } = this.words();
    const words = micCardWords(state, keyWords, macApp);
    this.root.dataset.state = state;
    this.title.textContent = words.title;
    this.line.textContent = words.line;
    this.button.hidden = !words.button;
    this.button.textContent = words.button ?? '';
    this.foot.hidden = !words.foot;
    this.foot.textContent = words.foot ?? '';
    this.root.hidden = false;
  }

  hide(): void {
    this.state = null;
    this.root.hidden = true;
  }
}
