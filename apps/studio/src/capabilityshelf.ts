// What it can do, by job, and what's new (docs/design/2026-10-13-capabilities-for-everyone.md, C1 and C2): the top of
// the Models page. One card per job with one colour (green on, yellow on its way, red one press away, grey not on this
// Mac), where your pictures go and what it costs, and one button: Turn on (red), its progress and Cancel (yellow), Try it
// (green). Turn on's steps are listed on the card: the app's own done by the app, yours asked for with their own control
// (Add key opens the key sheet; nothing asks for a key in the conversation). New for you lists what others added to the
// catalog since you last looked: its picture, what it does, who added it, reviewed or community; never turned on by
// itself. The shelf's facts are the server's (orchestrator/capability_shelf.py, catalog_news.py).
import './capabilityshelf.css';
import { openAddKey } from './addkey';
import type { KeyFacts } from './protocol';

export type Colour = 'green' | 'yellow' | 'red' | 'grey';
export interface ShelfStep { kind: string; says: string; how?: string; url?: string; service?: string; model?: string; channel?: string; recheck?: boolean; by: 'app' | 'you'; bytes?: number /* a download's bytes still to get */; onAgain?: boolean /* an install that only turns a kept version back on */ }
export interface Turning { attempt?: string; job: string; option: string; label: string; model: string; state: 'starting' | 'running' | 'waiting' | 'failed'; says: string; step?: ShelfStep; privacy?: string; cost?: string }
export interface ShelfCheck { state: string; ms?: number; detail?: unknown; at?: string; earlierBuild?: string }
export interface ShelfCard {
  id: string; title: string; what: string; colour: Colour; word: string; using: string; privacy: string; cost: string;
  service: string; try: string; tryOption?: string;
  options: { id: string; label: string; status: string; word: string; where: string; cost?: string; default?: boolean; check?: ShelfCheck | null }[];
  demonstration?: { url: string; caption: string; kind: 'image' | 'video' } | null;
  check?: ShelfCheck | null; outcomes?: string[];
  turnOn: { option: string; label: string; model: string; steps: ShelfStep[]; privacy?: string; cost?: string; recipe?: string; service?: string /* the cloud service whose key it uses */ } | null;
  turning?: Turning;
}
export interface Shelf { cards: ShelfCard[]; behind: ShelfCard[]; counts: Record<Colour, number> }
export interface NewItem {
  key: string; name: string; what: string; kind: string; tags: string[]; by: string; byLink: string; makers: string;
  link: string; badge: 'reviewed' | 'community'; channel: string; trusted: boolean; example: string; relevant: boolean;
  arrived: string; fetched: boolean; // false: only looked at (its manifest and picture); Get it fetches it on your press
  commit?: string; offer?: string; version?: string; update?: boolean; requirements?: string[];
}
export interface News { new: NewItem[]; last: { checked?: string; ok?: boolean; says?: string }; automatic: boolean }

export interface ShelfCommands {
  turnOn: (job: string, plan: NonNullable<ShelfCard['turnOn']>, recheck?: boolean) => Promise<unknown>;
  cancel: (job: string, attempt: string) => Promise<unknown>;
  tryIt: (job: string, option: string, prepareExample?: boolean) => Promise<void>;
  open: (model: string) => void; // its model's card on this page
  confirm?: (model: string) => Promise<unknown>; // "I've accepted it" for a licence no probe can read
  trust?: (channel: string) => Promise<unknown>; // allow code from a community catalog
  seen: () => Promise<unknown>;
  dismiss: (key: string) => Promise<unknown>;
  get: (key: string, commit: string, offer: string) => Promise<unknown>; // the exact displayed catalog/content
}

/** "3 on · 1 on its way · 4 one press away": the shelf's line, never mentioning what can't run here. */
export function countsWords(counts: Record<Colour, number>): string {
  return [counts.green ? `${counts.green} on` : '', counts.yellow ? `${counts.yellow} on its way` : '',
    counts.red ? `${counts.red} one press away` : ''].filter(Boolean).join(' · ') || 'Nothing on yet';
}

/** What a card's button is: Turn on (red, not yet pressed), the press's progress (yellow, or red while it waits for
 * you), Try it (green, with something to say), or nothing. */
export function cardAction(card: ShelfCard): 'turn-on' | 'turning' | 'try' | 'none' {
  if (card.turning && card.turning.state !== 'failed') return 'turning';
  if (card.colour === 'red' && card.turnOn) return 'turn-on';
  if (card.colour === 'yellow') return 'turning';
  if (card.colour === 'green' && card.tryOption) return 'try';
  return 'none';
}

/** One step of Turn on in words: whose it is, and whether it is the one waiting now. */
export function stepWords(step: ShelfStep, turning?: Turning): { words: string; whose: string; now: boolean } {
  const now = Boolean(turning && turning.state === 'waiting' && turning.step?.kind === step.kind);
  return { words: step.says, whose: step.by === 'app' ? 'The app does this' : 'You do this', now };
}

/** "Added by Maya Okafor · reviewed": who brought a new one and whether it was reviewed. */
export function newsByline(n: NewItem): string {
  return [n.by ? `Added by ${n.by}` : n.makers ? `By ${n.makers}` : '',
    n.badge === 'community' ? `community catalog ${n.channel}, not reviewed here` : 'reviewed'].filter(Boolean).join(' · ');
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string | boolean> = {},
  ...children: (Node | string | null | undefined | false)[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false) continue;
    if (k === 'class') node.className = String(v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

function busy(button: HTMLButtonElement, act: () => Promise<unknown>, said: HTMLElement): void {
  button.addEventListener('click', () => {
    button.disabled = true;
    void act().catch((error: unknown) => {
      said.textContent = error instanceof Error ? error.message : String(error);
      said.hidden = false;
    }).finally(() => { button.disabled = false; });
  });
}

/** A step only you can take, with its own control: Add key, the licence's page (and "I've accepted it" where no probe
 * can read it), Allow code from a community catalog. */
export function yourControl(step: ShelfStep, keys: Record<string, KeyFacts> | undefined, commands: ShelfCommands,
  said: HTMLElement): HTMLElement | null {
  if (step.kind === 'key' && step.service) {
    const key = Object.values(keys ?? {}).find((k) => k.service === step.service);
    if (!key) return null;
    const b = el('button', { type: 'button', class: 'mm-control shelf-step-act' }, 'Add key');
    b.addEventListener('click', () => openAddKey(key, b));
    return b;
  }
  if (step.kind === 'licence' && step.url) {
    const wrap = el('span', { class: 'shelf-step-acts' },
      el('a', { class: 'shelf-step-act', href: step.url, target: '_blank', rel: 'noopener noreferrer' }, 'Open its page'));
    if (commands.confirm && step.model) {
      const b = el('button', { type: 'button', class: 'mm-control shelf-step-act' }, 'I’ve accepted it');
      busy(b, () => commands.confirm!(step.model!), said);
      wrap.append(b);
    }
    return wrap;
  }
  if (step.kind === 'trust' && step.channel && commands.trust) {
    const channel = step.channel;
    const b = el('button', { type: 'button', class: 'mm-control shelf-step-act' }, step.says);
    busy(b, () => commands.trust!(channel), said);
    return b;
  }
  return null;
}

export function shelfCard(card: ShelfCard, keys: Record<string, KeyFacts> | undefined, commands: ShelfCommands): HTMLElement {
  const said = el('p', { class: 'shelf-said', role: 'status', hidden: true });
  const admitted = (card.turning?.state !== 'failed' ? card.turning : undefined) ?? card.turnOn;
  const node = el('article', { class: 'shelf-card', 'data-job': card.id, 'data-colour': card.colour },
    el('header', { class: 'shelf-card-head' },
      el('span', { class: 'shelf-dot', 'aria-hidden': 'true' }),
      el('h4', {}, card.title)),
    el('p', { class: 'shelf-word' }, card.turning?.state === 'failed' ? `Didn’t turn on: ${card.turning.says}` : card.word),
    el('p', { class: 'shelf-what' }, card.what),
    el('p', { class: 'shelf-meta' }, [card.using ? `Using ${card.using}` : '', admitted?.privacy ?? card.privacy, admitted?.cost ?? card.cost].filter(Boolean).join(' · ')));
  const acts = el('div', { class: 'shelf-acts' });
  const what = cardAction(card);
  if (what === 'turn-on') {
    const b = el('button', { type: 'button', class: 'mm-control mm-primary shelf-turn-on' }, 'Turn on');
    busy(b, () => commands.turnOn(card.id, card.turnOn!), said);
    acts.append(b);
  } else if (what === 'turning') {
    acts.append(el('span', { class: 'shelf-progress' }, card.turning?.says || card.word));
    if (card.turning) {
      const b = el('button', { type: 'button', class: 'mm-control shelf-cancel' }, 'Stop');
      busy(b, () => commands.cancel(card.id, card.turning?.attempt ?? ''), said);
      acts.append(b);
    }
  } else if (what === 'try') {
    const b = el('button', { type: 'button', class: 'mm-control shelf-try' }, 'Try it');
    busy(b, () => commands.tryIt(card.id, card.tryOption!), said);
    acts.append(b);
  }
  node.append(acts);
  const steps = card.turnOn?.steps ?? [];
  if (steps.length && card.colour !== 'green') {
    const list = el('ol', { class: 'shelf-steps', 'aria-label': `What turning on ${card.title} takes` });
    for (const step of steps) {
      const w = stepWords(step, card.turning);
      const control = step.by === 'you' ? yourControl(step, keys, commands, said) : null;
      list.append(el('li', { class: 'shelf-step', 'data-by': step.by, 'data-now': w.now ? 'true' : 'false' },
        el('span', { class: 'shelf-step-words' }, w.words), el('small', {}, w.whose), control));
    }
    node.append(list);
  }
  if (card.options.length > 1) {
    node.append(el('details', { class: 'shelf-options' }, el('summary', {}, `${card.options.length} ways to do it`),
      el('ul', {}, ...card.options.map((o) => el('li', { 'data-status': o.status },
        el('strong', {}, o.label), ` · ${o.word}`, o.where ? ` · ${o.where}` : '')))));
  }
  node.append(said);
  return node;
}

export function shelfSection(shelf: Shelf, keys: Record<string, KeyFacts> | undefined, commands: ShelfCommands): HTMLElement {
  const grid = el('div', { class: 'shelf-grid' }, ...shelf.cards.map((c) => shelfCard(c, keys, commands)));
  const section = el('section', { class: 'shelf', 'aria-labelledby': 'shelf-heading' },
    el('header', { class: 'models-group-head' },
      el('h3', { id: 'shelf-heading' }, 'What it can do'),
      el('p', {}, `${countsWords(shelf.counts)}. Green is on; red turns on with one press.`)),
    grid);
  if (shelf.behind.length) {
    section.append(el('details', { class: 'shelf-behind' }, el('summary', {}, 'Behind the scenes'),
      el('div', { class: 'shelf-grid' }, ...shelf.behind.map((c) => shelfCard(c, keys, commands)))));
  }
  return section;
}

export function newsSection(news: News, commands: ShelfCommands): HTMLElement | null {
  if (!news.new.length) return null;
  const said = el('p', { class: 'shelf-said', role: 'status', hidden: true });
  const got = el('button', { type: 'button', class: 'mm-control' }, 'Got it');
  busy(got, () => commands.seen(), said);
  const items = news.new.map((n) => {
    const look = el('button', { type: 'button', class: 'mm-control mm-primary' }, n.fetched ? 'See it' : 'Get it');
    if (n.fetched) look.addEventListener('click', () => commands.open(n.key));
    else busy(look, () => commands.get(n.key, n.commit ?? '', n.offer ?? ''), said);
    const not = el('button', { type: 'button', class: 'mm-control news-not' }, 'Not for me');
    busy(not, () => commands.dismiss(n.key), said);
    return el('li', { class: 'news-item', 'data-key': n.key, 'data-relevant': n.relevant ? 'true' : 'false' },
      n.example ? el('img', { class: 'news-picture', src: n.example, alt: `${n.name} at work`, loading: 'lazy' }) : el('span', { class: 'news-picture news-picture-none', 'aria-hidden': 'true' }),
      el('div', { class: 'news-body' },
        el('strong', {}, n.name), n.relevant ? el('span', { class: 'news-fits' }, 'Fits what you’re looking at') : null,
        el('p', { class: 'shelf-what' }, n.what), el('p', { class: 'shelf-meta' }, newsByline(n)),
        el('p', { class: 'shelf-meta' }, n.update ? 'Update available' : 'New in the catalog', n.version ? ` · ${n.version}` : '',
          n.arrived ? ` · ${n.arrived.slice(0, 10)}` : ''),
        el('ul', { class: 'news-requirements' }, ...(n.requirements ?? []).map(r => el('li', {}, r)))),
      el('div', { class: 'news-acts' }, look, not));
  });
  return el('section', { class: 'news', 'aria-labelledby': 'news-heading' },
    el('header', { class: 'models-group-head news-head' },
      el('h3', { id: 'news-heading' }, 'New for you', el('span', { class: 'models-group-count' }, String(news.new.length))),
      el('p', {}, 'What others added to the catalog since you last looked. Nothing turns on by itself.'), got),
    el('ul', { class: 'news-list' }, ...items), said);
}
