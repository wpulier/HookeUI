// The composer (the workspace-feel plan, "The chat"; design/workspace-feel/researcher-spec.md section 6; the mock's
// composer()): one component for the Assistant and every researcher. A raised block: a chip line only when something
// is attached or pointed at (the Assistant's specimen and where its answers go), a field that grows from two lines to
// eight (Enter sends, Shift+Enter starts a new line), then one row: + where attaching exists, the model chip, a spacer,
// any extra control (the Assistant's Talk), and Send, grey while there is nothing to send and blue with words. No form
// title and no hint inside the box (R-17). Send turns into no stop: no command cancels an answer being written, so
// none is offered.
import { popMenu, type MenuItem } from './drawer-menu';
import { lineIcon } from './chat-scroll';

/** Two lines to eight, at the field's 20 px line height. */
export const FIELD_MIN = 40;
export const FIELD_MAX = 160;

export interface ComposerOptions {
  /** The field's id (the Assistant's is "question", which the studio's other parts address). */
  fieldId?: string;
  /** Its accessible name and its placeholder ("Ask anything"). */
  label: string;
  placeholder: string;
  /** "+": the ways to attach, read when pressed. Absent where attaching does not exist (a researcher's Tab). */
  attach?: { label: string; items: () => MenuItem[] } | null;
  /** The model chip: its words, its tooltip, and what it opens. With no `open` it is a static fact (a researcher's
   * engine, fixed when it started). */
  model?: { words: string; title?: string; open?: (chip: HTMLButtonElement) => void } | null;
  /** Controls between the spacer and Send (the Assistant's Talk, which keeps its own states). */
  extra?: HTMLElement[];
}

const make = <K extends keyof HTMLElementTagNameMap>(tag: K, className = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); if (className) node.className = className; return node;
};

/** Whether a key press sends: Enter alone, not while an input method is composing a character. */
export function sendsOnEnter(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'altKey' | 'ctrlKey' | 'metaKey' | 'isComposing'> & { keyCode?: number }): boolean {
  return event.key === 'Enter' && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey && !event.isComposing && event.keyCode !== 229;
}

/** The field's height for its content: two lines at least, eight at most (then it scrolls inside). */
export function fieldHeight(contentHeight: number): number {
  return Math.round(Math.min(FIELD_MAX, Math.max(FIELD_MIN, Number.isFinite(contentHeight) ? contentHeight : FIELD_MIN)));
}

export class Composer {
  readonly root: HTMLFormElement;
  readonly chips: HTMLElement;
  readonly field: HTMLTextAreaElement;
  readonly actions: HTMLElement;
  readonly send: HTMLButtonElement;
  readonly attachButton: HTMLButtonElement | null = null;
  readonly modelChip: HTMLButtonElement | null = null;
  private modelNeeds: HTMLElement | null = null;
  private readonly modelWords: HTMLElement | null = null;

  /** `root`: a form to build into (the Assistant's #ask, whose Talk is passed in `extra`), or none for a new one. */
  constructor(root: HTMLFormElement | null, options: ComposerOptions) {
    this.root = root ?? make('form');
    this.root.classList.add('mm-composer');
    this.root.setAttribute('novalidate', '');
    this.chips = make('div', 'mm-composer-chips');
    this.chips.hidden = true;
    this.field = make('textarea', 'mm-composer-field');
    if (options.fieldId) { this.field.id = options.fieldId; this.field.name = options.fieldId; }
    this.field.rows = 2;
    this.field.placeholder = options.placeholder;
    this.field.setAttribute('aria-label', options.label);
    this.field.setAttribute('autocomplete', 'off');
    this.actions = make('div', 'mm-composer-actions');
    if (options.attach) {
      const attach = options.attach;
      const button = make('button', 'mm-composer-icon mm-composer-attach'); button.type = 'button';
      button.setAttribute('aria-label', attach.label); button.title = attach.label;
      button.setAttribute('aria-haspopup', 'menu'); button.setAttribute('aria-expanded', 'false');
      button.append(lineIcon('M8 3v10M3 8h10', 'mm-composer-glyph'));
      button.addEventListener('click', () => { popMenu(button, attach.items(), attach.label, { above: true, start: true }); });
      this.actions.append(button);
      this.attachButton = button;
    }
    if (options.model) {
      const model = options.model;
      const chip = make('button', 'mm-composer-model'); chip.type = 'button';
      const words = make('span', 'mm-composer-model-words');
      const needs = make('span', 'mm-composer-model-needs'); needs.hidden = true;
      chip.append(words, needs);
      if (model.open) {
        chip.setAttribute('aria-haspopup', 'dialog'); chip.setAttribute('aria-expanded', 'false');
        chip.append(lineIcon('M4 6.5 8 10.5 12 6.5', 'mm-composer-caret'));
        chip.addEventListener('click', () => model.open!(chip));
      } else chip.dataset.static = 'true';
      this.actions.append(chip);
      this.modelChip = chip; this.modelWords = words; this.modelNeeds = needs;
      this.setModel(model.words, model.title);
    }
    const spacer = make('span', 'mm-composer-spacer');
    this.actions.append(spacer, ...(options.extra ?? []));
    this.send = make('button', 'mm-composer-send'); this.send.type = 'submit';
    this.send.setAttribute('aria-label', 'Send'); this.send.title = 'Send (Enter)';
    this.send.append(lineIcon('M8 13V3.5M4 7.5 8 3.5l4 4', 'mm-composer-glyph'));
    this.actions.append(this.send);
    this.root.replaceChildren(this.chips, this.field, this.actions);
    this.field.addEventListener('input', () => this.refresh());
    this.field.addEventListener('keydown', (event) => {
      if (!sendsOnEnter(event)) return;
      event.preventDefault();
      if (this.send.disabled || !this.field.value.trim()) return; // a request still being answered, or nothing to send
      if (typeof this.root.requestSubmit === 'function') this.root.requestSubmit(this.send); else this.send.click();
    });
    this.refresh();
  }

  /** Put words in the field (a restored draft, a starter's beginning, a refused request's words), or clear it. */
  set(text: string): void {
    this.field.value = text;
    this.refresh();
  }

  /** The field's height and Send's look follow its words. */
  refresh(): void {
    const empty = !this.field.value.trim();
    this.send.setAttribute('aria-disabled', String(empty));
    this.root.dataset.empty = String(empty);
    const style = this.field.style;
    if (!style) return;
    style.height = 'auto';
    const height = fieldHeight(this.field.scrollHeight);
    style.height = `${height}px`;
    style.overflowY = this.field.scrollHeight > FIELD_MAX ? 'auto' : 'hidden';
  }

  /** The chip line: shown only with something in it. */
  setChips(...nodes: (Node | string)[]): void {
    this.chips.replaceChildren(...nodes);
    this.chips.hidden = nodes.length === 0;
  }

  /** The model chip's words ("Gemini 3.8 Flash") and its tooltip; `needs`, what it lacks before it can answer ("needs a
   *  key"), said beside its name so a first question is not the way to find out. */
  setModel(words: string, title?: string, needs = ''): void {
    if (!this.modelChip || !this.modelWords) return;
    if (this.modelWords.textContent !== words) this.modelWords.textContent = words;
    if (this.modelNeeds) { if (this.modelNeeds.textContent !== needs) this.modelNeeds.textContent = needs; this.modelNeeds.hidden = !needs; }
    const tip = title ?? words;
    if (this.modelChip.title !== tip) this.modelChip.title = tip;
    this.modelChip.setAttribute('aria-label', tip);
    this.modelChip.hidden = !words;
  }
}
