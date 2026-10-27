// Descriptions of the things on screen (orchestrator/enrich.py): one per kind of thing,
// looked up ahead of time by a quick researcher. A short line goes under the object's name
// once it is "described" (the user asked about it, selected it or rested the pointer on it);
// the full description shows in a card floating over the view when the thing is clicked.

import { isVisible, statusWords, taughtWords } from './overlay';
import { dragBy, type Panel } from './panels';
import type { ClientCommand, Correction, NoteEntry, ResearchSession, SceneObject } from './protocol';
import type { View } from './view';

/** What "Look deeper" asks, in the user's words. It is sent with the card's outline (`objectId`), which the
 * server makes the selection, so "this one" means that outline and no other with the same name. It
 * names no effort (no "deep"): a closer look or a quick researcher is what the press authorizes, and a
 * deep or standard run, a lesson or a video still needs the user to ask for one (research/policy.py). */
export const DEEPER_TEXT = 'Look into this one more closely';

export class Notes {
  readonly entries = new Map<string, NoteEntry>();
  private readonly described = new Set<string>(); // object ids whose description is shown
  research: () => ResearchSession[] = () => [];
  onChange: () => void = () => {};

  apply(message: { full?: Record<string, NoteEntry>; upsert?: Record<string, NoteEntry> }): void {
    if (message.full) {
      this.entries.clear();
      for (const [k, v] of Object.entries(message.full)) this.entries.set(k, v);
    }
    for (const [k, v] of Object.entries(message.upsert ?? {})) this.entries.set(k, v);
    this.onChange();
  }

  describeObjects(ids: Iterable<string>): void {
    for (const id of ids) this.described.add(id);
  }

  /** Suggested relabellings of this object (or of every outline with its name) still open. */
  pending(obj: SceneObject): { correction: Correction; session: ResearchSession }[] {
    return this.research().flatMap((session) =>
      session.corrections
        .filter((c) => c.status === 'proposed' && (c.object_id === obj.id || (!c.object_id && c.label === obj.label)))
        .map((correction) => ({ correction, session })),
    );
  }

  /** Researchers' notes about this object or its kind of thing, newest first. */
  findings(obj: SceneObject): { text: string; from: string; rid: string }[] {
    return this.research()
      .filter((s) => s.purpose === 'research' && (s.objectId === obj.id || (s.label && s.label === obj.label)))
      .sort((a, b) => b.created - a.created)
      .flatMap((s) => [...s.findings].reverse().map((f) => ({ text: f.text, from: s.name, rid: s.rid })));
  }

  /** The line under the name: a pinned note, an open suggestion, or a more descriptive name
   * ("leaf surface pore") with how sure the label is. */
  line(obj: SceneObject, selected = false): string | null {
    if (obj.pinned) return obj.pinned;
    const suggestion = this.pending(obj)[0];
    if (suggestion) return `Suggested: ${suggestion.correction.to_label.slice(0, 32)}?`;
    if (!selected && !this.described.has(obj.id)) return null;
    const entry = this.entries.get(obj.labelKey);
    const title = entry?.status === 'ready' ? entry.description?.title?.trim() : '';
    if (!title) return null;
    const words = statusWords(obj.status);
    const sure = words && obj.status !== 'confirmed' ? `${words}: ` : '';
    return sure + lowerFirst(title);
  }
}

function lowerFirst(text: string): string {
  // "A stoma is…" -> "a stoma is…", but keep abbreviations and names ("ABA", "DNA").
  const word = text.split(/\s/)[0] ?? '';
  const plain = word.length > 0 && word.slice(1) === word.slice(1).toLowerCase();
  return plain ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

export class DescriptionCard implements Panel {
  readonly id = 'card';
  private readonly root: HTMLElement;
  private readonly title: HTMLElement;
  private readonly meta: HTMLElement;
  private readonly sure = document.createElement('span');
  private readonly seen: HTMLElement;
  private readonly taught: HTMLElement;
  private readonly summary: HTMLElement;
  private readonly more: HTMLButtonElement;
  private readonly deeper: HTMLButtonElement;
  private readonly extra: HTMLElement;
  private readonly refs: HTMLElement;
  private readonly review: HTMLElement;
  private readonly notesBox: HTMLDetailsElement;
  private readonly notesCount: HTMLElement;
  private readonly notesList: HTMLOListElement;
  private readonly open: HTMLAnchorElement;
  private readonly view: View;
  private readonly notes: Notes;
  private readonly send: (command: ClientCommand) => void;
  private readonly revealed = new Set<string>();
  private objects = new Map<string, SceneObject>();
  private previousFocus: HTMLElement | null = null;
  private opened: string | null = null; // the thing whose details the user opened (by clicking it)
  private shownKey = '';
  private awaySince = new Map<string, number>(); // when a selected thing left the view

  constructor(view: View, notes: Notes, send: (command: ClientCommand) => void) {
    const el = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
    this.root = el('description');
    this.title = el('description-title');
    this.meta = el('description-meta');
    this.sure.id = 'details-sureness'; this.sure.className = 'description-sure'; this.sure.hidden = true; this.title.after(this.sure);
    this.seen = el('description-seen');
    this.taught = el('description-taught');
    this.summary = el('description-summary');
    this.more = el<HTMLButtonElement>('description-more');
    this.deeper = el<HTMLButtonElement>('description-deeper');
    this.extra = el('description-extra');
    this.refs = el('description-refs');
    this.review = el('description-review');
    this.notesBox = el<HTMLDetailsElement>('description-notes');
    this.notesCount = el('description-notes-count');
    this.notesList = el<HTMLOListElement>('description-notes-list');
    this.open = el<HTMLAnchorElement>('description-open');
    this.view = view;
    this.notes = notes;
    this.send = send;
    el<HTMLButtonElement>('description-close').addEventListener('click', () => this.close());
    // One escalation: it asks in the user's words, and the app chooses what deeper means (a closer
    // look, a measurement, following it, a quick researcher), so nobody picks a model first.
    this.deeper.addEventListener('click', () => {
      const obj = this.opened ? this.objects.get(this.opened) : undefined;
      if (!obj) return;
      this.send({ type: 'ask', text: DEEPER_TEXT, objectId: obj.id, frameId: this.view.header?.id }); // the frame on screen, as the chat's ask sends
      // Not pressed twice by accident; nothing here says it was asked: the chat's line is that (ADR 0006).
      this.deeper.disabled = true;
      window.setTimeout(() => (this.deeper.disabled = false), 2500);
    });
    this.draggable(el('description-head'));
    this.more.addEventListener('click', () => {
      const expand = this.extra.hidden;
      this.extra.hidden = !expand;
      this.more.setAttribute('aria-expanded', String(expand));
      this.more.textContent = expand ? 'Less' : 'More';
    });
  }

  setObjects(objects: SceneObject[]): void {
    this.objects = new Map(objects.map((o) => [o.id, o]));
    this.render();
  }

  /** Open the details of what the user clicked (only a click opens them). */
  openFor(id: string): void {
    if (!this.isOpen()) this.previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    this.opened = id;
    this.render();
    this.send({ type: 'card-open', id }); // logged, so whether descriptions get read can be measured
  }

  close(): void {
    this.opened = null;
    this.render();
    if (this.previousFocus?.isConnected) this.previousFocus.focus({preventScroll:true});
  }

  frame(): void { if (this.isOpen() && window.innerWidth > 960) this.fitHeight(); }

  isVisible(): boolean { return this.root.dataset.resultTucked !== 'true'; }

  isOpen(): boolean {
    return !this.root.hidden;
  }

  /** Where it floats, so the magnifier keeps clear of it. */
  footprint(): DOMRect | null {
    return this.root.hidden ? null : this.root.getBoundingClientRect();
  }

  /** Whose details are open (null if none). */
  get openId(): string | null {
    return this.isOpen() ? this.opened : null;
  }

  /** Moved by its title bar, kept inside the view, and remembered. */
  private draggable(handle: HTMLElement): void {
    try {
      const saved = JSON.parse(localStorage.getItem('card-pos') || 'null') as [number, number] | null;
      if (saved) this.place(saved[0], saved[1]);
    } catch {
      // not remembered
    }
    dragBy(handle, this.root, (x, y) => this.place(x, y), () => {
      try {
        localStorage.setItem('card-pos', JSON.stringify([this.root.offsetLeft, this.root.offsetTop]));
      } catch {
        // not remembered
      }
    });
  }

  /** Keep it inside the view where it sits (it scrolls inside when long). */
  private fitHeight(): void {
    if (window.innerWidth <= 960) { this.root.style.maxHeight = ''; return; }
    const parent = this.root.offsetParent as HTMLElement | null;
    if (!parent) return;
    if (this.root.offsetTop > parent.clientHeight - 80) this.place(this.root.offsetLeft, parent.clientHeight - 200);
    this.root.style.maxHeight = `${Math.max(120, parent.clientHeight - this.root.offsetTop - 12)}px`;
  }

  private place(x: number, y: number): void {
    const parent = this.root.offsetParent as HTMLElement | null;
    const w = this.root.offsetWidth || 320;
    const maxX = Math.max(0, (parent?.clientWidth ?? window.innerWidth) - w - 8);
    const maxY = Math.max(0, (parent?.clientHeight ?? window.innerHeight) - 80);
    const top = Math.min(maxY, Math.max(8, y));
    this.root.style.left = `${Math.min(maxX, Math.max(8, x))}px`;
    this.root.style.top = `${top}px`;
    this.root.style.right = 'auto';
    // Never past the bottom of the view: it scrolls inside instead.
    this.root.style.maxHeight = `${Math.max(120, (parent?.clientHeight ?? window.innerHeight) - top - 12)}px`;
  }

  render(): void {
    const id = this.opened;
    const obj = id ? this.objects.get(id) : undefined;
    if (id && !obj) this.opened = null; // it was removed
    if (!obj || !obj.label || obj.trial || this.away(obj)) {
      this.hide();
      return;
    }
    const entry = this.notes.entries.get(obj.labelKey);
    const usable = entry && entry.status !== 'failed' && entry.status !== 'unavailable' ? entry : undefined;
    const pending = this.notes.pending(obj);
    const findings = this.notes.findings(obj);
    const taught = taughtWords(obj);
    if (!usable && !pending.length && !findings.length && !obj.revisedBy && !obj.seen && !taught) {
      this.hide();
      return;
    }
    const wasHidden = this.root.hidden;
    this.root.hidden = false;
    if (wasHidden) this.fitHeight();
    const key = JSON.stringify([obj.id, obj.label, obj.seen, taught, obj.pinned, usable?.status, usable?.refs?.length,
      pending.map((p) => p.correction.id), findings.length]);
    if (key === this.shownKey) return;
    const sameThing = JSON.parse(this.shownKey || '[]')[0] === obj.id;
    this.shownKey = key;
    this.title.textContent = obj.label;
    // What the AI saw in this one (when it was clicked or pointed at), before the general
    // description of its kind.
    this.seen.hidden = !obj.seen;
    this.seen.textContent = obj.seen;
    // The closest of the user's own labels: a match on how it looks, apart from the model's name.
    this.taught.hidden = !taught;
    this.taught.textContent = taught ? `${taught.charAt(0).toUpperCase()}${taught.slice(1)}, from ${obj.taught?.examples ?? 0} examples you named` : '';
    this.renderReview(obj, pending);
    this.renderNotes(obj, findings);
    const revised = obj.revisedBy ? ` Relabelled after ${obj.revisedBy}'s suggestion.` : '';
    if (!usable?.description) {
      this.sure.hidden = true;
      this.meta.textContent = (usable ? 'Looking up what this kind of thing is…' : '') + revised;
      this.summary.textContent = '';
      this.more.hidden = true;
      this.extra.hidden = true;
      this.refs.replaceChildren();
      this.open.hidden = true;
      return;
    }
    const entryReady = usable;
    const d = entryReady.description!;
    // A match for the user's words (SAM 3.1) is named by those words, not recognised (M3.1 re-check note).
    const sure = namingConfidence(obj);
    this.sure.textContent = sure.text; this.sure.dataset.tone = sure.tone; this.sure.hidden = false;
    this.meta.textContent = `This describes a typical ${entryReady.key}.${revised}`;
    this.summary.textContent = d.summary;
    this.more.hidden = !(d.details?.length || d.look_for || d.sources?.length);
    if (!sameThing) {
      this.extra.hidden = true;
      this.more.setAttribute('aria-expanded', 'false');
      this.more.textContent = 'More';
    }
    const parts: HTMLElement[] = [];
    if (d.look_for) {
      const p = document.createElement('p');
      p.className = 'look-for';
      p.textContent = `Look for: ${d.look_for}`;
      parts.push(p);
    }
    if (d.details?.length) {
      const ul = document.createElement('ul');
      for (const text of d.details) {
        const li = document.createElement('li');
        li.textContent = text;
        ul.append(li);
      }
      parts.push(ul);
    }
    if (d.sources?.length) {
      const sources = document.createElement('p');
      sources.className = 'sources';
      sources.append('Sources: ');
      d.sources.slice(0, 4).forEach((url, i) => {
        if (i) sources.append(', ');
        sources.append(link(url, hostOf(url)));
      });
      parts.push(sources);
    }
    this.extra.replaceChildren(...parts);
    this.refs.replaceChildren(
      ...(entryReady.refs ?? []).map((ref) => {
        const figure = document.createElement('figure');
        const a = link(ref.page, '');
        const img = document.createElement('img');
        img.src = ref.url;
        img.alt = `Reference picture: ${ref.title || entryReady.key}`;
        img.loading = 'lazy';
        a.append(img);
        const caption = document.createElement('figcaption');
        caption.textContent = ref.credit ? `Reference, ${ref.credit}` : 'Reference picture';
        figure.append(a, caption);
        return figure;
      }),
    );
    this.open.hidden = !entryReady.rid;
    if (entryReady.rid) this.open.href = `#research/${entryReady.rid}`;
    if (!this.revealed.has(entryReady.key)) {
      this.revealed.add(entryReady.key);
      this.send({ type: 'reveal', key: entryReady.key }); // fetch its reference pictures, once
    }
  }

  /** A selected thing that has been out of view a few seconds: its card steps aside until it
   * is back, so the column does not describe something no longer there. */
  private away(obj: SceneObject): boolean {
    const h = this.view.header;
    if (!h) return false;
    if (isVisible(obj, h) && obj.anchorEpoch === h.epoch) {
      this.awaySince.delete(obj.id);
      return false;
    }
    const since = this.awaySince.get(obj.id) ?? performance.now();
    this.awaySince.set(obj.id, since);
    return performance.now() - since > 3000;
  }

  private hide(): void {
    this.root.hidden = true;
    this.shownKey = '';
  }

  /** Suggested relabellings: nothing changes until the user accepts. */
  private renderReview(obj: SceneObject, pending: { correction: Correction; session: ResearchSession }[]): void {
    this.review.replaceChildren(
      ...pending.map(({ correction, session }) => {
        const box = document.createElement('div');
        box.className = 'suggestion';
        const p = document.createElement('p');
        const strong = document.createElement('strong');
        strong.textContent = `${session.name} suggests: ${correction.to_label}`;
        const reason = correction.reason ? correction.reason.charAt(0).toUpperCase() + correction.reason.slice(1) : '';
        p.append(strong, document.createTextNode(reason ? `. ${reason}` : ''));
        const accept = document.createElement('button');
        accept.type = 'button';
        accept.textContent = `Call it ${correction.to_label}`;
        accept.addEventListener('click', () => this.send({ type: 'accept-correction', id: correction.id }));
        const dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.textContent = `Keep ${obj.label}`;
        dismiss.addEventListener('click', () => this.send({ type: 'dismiss-correction', id: correction.id }));
        box.append(p, accept, dismiss);
        return box;
      }),
    );
  }

  /** Researchers' notes, newest first; one can be pinned under the label. */
  private renderNotes(obj: SceneObject, findings: { text: string; from: string; rid: string }[]): void {
    this.notesBox.hidden = !findings.length;
    this.notesCount.textContent = `${findings.length} researcher note${findings.length === 1 ? '' : 's'}`;
    this.notesList.replaceChildren(
      ...findings.slice(0, 12).map((f) => {
        const li = document.createElement('li');
        li.append(document.createTextNode(`${f.text} `));
        const from = document.createElement('a');
        from.href = `#research/${f.rid}`;
        from.textContent = f.from;
        const pin = document.createElement('button');
        pin.type = 'button';
        const pinned = obj.pinned === f.text;
        pin.textContent = pinned ? 'Unpin' : 'Pin under the label';
        pin.setAttribute('aria-pressed', String(pinned));
        pin.addEventListener('click', () => this.send({ type: 'pin-note', id: obj.id, text: pinned ? null : f.text }));
        li.append(from, document.createTextNode(' '), pin);
        return li;
      }),
    );
  }
}

function link(url: string, text: string): HTMLAnchorElement {
  const a = document.createElement('a');
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.textContent = text;
  return a;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}
import { namingConfidence } from './layout-rules';
