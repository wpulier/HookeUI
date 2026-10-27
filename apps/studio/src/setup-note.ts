// The Assistant's key note (2026-10-23 audit, first launch): said only once something that needs the key was asked
// for, never on a fresh launch (hooke-words.md: "never at first launch"), in plain words with the one step to take.
import { liveNote } from './modelspage';
import type { CapabilitiesWire, KeyFacts } from './protocol';

export class SetupNote {
  private closed = '';
  private wire: CapabilitiesWire | null = null;
  private localOnly = true; // unknown until the Project says (its day message): kept quiet, never announced in the example
  private empty = true;
  private readonly root: HTMLElement;
  private readonly addKey: (key: KeyFacts, button: HTMLButtonElement) => void;
  private readonly openModel: (id: string) => void;
  /** Said while nothing is on the Stage too: in the workspace the note sits by the Assistant's box, not over the
   * start page, and the Assistant needs its model before anything is typed. */
  private readonly whenEmpty: boolean;
  /** Something that needs the key was asked for (a question to the Assistant): until then the note stays quiet. */
  private asked = false;
  constructor(root: HTMLElement, addKey: (key: KeyFacts, button: HTMLButtonElement) => void,
    openModel: (id: string) => void, options: { whenEmpty?: boolean } = {}) {
    this.root = root; this.addKey = addKey; this.openModel = openModel; this.whenEmpty = !!options.whenEmpty;
    if (typeof window !== 'undefined') window.addEventListener('hooke-ask-sent', () => { this.asked = true; this.render(); });
  }
  /** Tests and other askers: something needing the key was requested. */
  requested(): void { this.asked = true; this.render(); }

  context(localOnly: boolean, empty: boolean): void {
    this.localOnly = localOnly; this.empty = empty; this.render();
  }
  update(wire: CapabilitiesWire): void { this.wire = wire; this.render(); }
  private render(): void {
    const note = liveNote(this.wire?.models);
    if (this.localOnly || !this.asked || (this.empty && !this.whenEmpty) || !note || note.words === this.closed) {
      this.root.hidden = true; return;
    }
    if (this.root.dataset.words === note.words && !this.root.hidden) return;
    this.root.dataset.words = note.words;
    const need = note.row.word.replace(/^Needs /, '');
    const words = Object.assign(document.createElement('span'), { textContent: `The Assistant needs ${need} to see your specimen.` });
    const key = note.row.next?.kind === 'key' ? Object.values(this.wire?.setup?.keys ?? {}).find(k => k.service === note.row.next?.service) : undefined;
    const act = Object.assign(document.createElement('button'), { type: 'button', className: 'mm-control setup-note-act',
      textContent: key ? 'Add key' : 'How to turn it on' });
    act.addEventListener('click', () => key ? this.addKey(key, act) : this.openModel(note.row.id));
    const close = Object.assign(document.createElement('button'), { type: 'button', className: 'setup-note-close', textContent: '×' });
    close.setAttribute('aria-label', 'Close this note');
    close.addEventListener('click', () => { this.closed = note.words; this.root.hidden = true; });
    this.root.replaceChildren(words, act, close); this.root.hidden = false;
  }
}
