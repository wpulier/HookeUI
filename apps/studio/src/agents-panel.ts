// The Agents panel (ADR 0029): every worker in this Project, Hooke's researchers and the outside agents Hooke started
// on this Mac (Codex, Claude Code, Grok Build), grouped Needs you, Working, Done, one 56 px row each with its lit cell.
// An outside agent's row opens to a peek at its live transcript and a reply box; an ask it makes sits in its row with
// Allow, Allow for this session and Decline, and is answered only there, by the person. ⋯ holds Interrupt and Stop.
// Start an agent: which program (only those installed here), and what it should do. The panel reads the server's
// rows and changes nothing itself: each button asks, says it is sending, and the list says what happened.
import './agents-panel.css';
import { litCell, type LitCellState } from './lit-cell';
import { moreButton, type MenuItem } from './drawer-menu';
import { clock } from './working-strip';

export type AgentStatus = 'working' | 'needs-you' | 'done' | 'failed' | 'stopped';
export type AgentAsk = { id: string; title: string; detail: string; reason?: string; always?: boolean; at?: number };
export type AgentEntry = { n: number; role: 'hooke' | 'agent' | 'tool' | 'ask' | 'note'; text: string; at: number;
  final?: boolean; state?: string | null; how?: string };
export type AgentRow = {
  id: string; program: string; label: string; task: string; status: AgentStatus; state: string; line: string;
  project: string; projectName: string; study: string; assignment: string; by: string; at: number;
  endedAt: number | null; model: string | null; billing: string | null; budgetUsd: number | null;
  costUsd: number | null; tokens: number | null; why: string; report: { returned?: boolean; claims?: number };
  asks: AgentAsk[]; transcript: AgentEntry[]; live: string;
};
export type AgentProgram = { id: string; label: string; installed: boolean; version?: string | null; next?: string | null };
export type AgentsMessage = { type: 'agents'; agents: AgentRow[]; programs: AgentProgram[] };
/** One of Hooke's own researchers, as the workspace reads it (researchers-board.ts's words and cell). */
export type ResearcherWorker = { rid: string; name: string; title: string; group: 'needs' | 'working' | 'done';
  cell: LitCellState; words: string };

type Options = { request?: typeof fetch; researchers?: () => ResearcherWorker[]; openResearcher?: (rid: string) => void;
  project?: () => string; changed?: () => void; openStudy?: (id: string) => void };

const STATUSES: AgentStatus[] = ['working', 'needs-you', 'done', 'failed', 'stopped'];
const SECTIONS: { id: 'needs' | 'working' | 'done'; title: string }[] = [
  { id: 'needs', title: 'Needs you' }, { id: 'working', title: 'Working' }, { id: 'done', title: 'Done' }];
const CELL: Record<AgentStatus, LitCellState> = { working: 'working', 'needs-you': 'needs', done: 'ready', failed: 'attention',
  stopped: 'stopped' };
const STATUS_WORDS: Record<AgentStatus, string> = { working: 'Working', 'needs-you': 'Needs you', done: 'Done',
  failed: 'Failed', stopped: 'Stopped' };

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); if (className) node.className = className; if (text) node.textContent = text; return node;
};
const isObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object';
const str = (value: unknown): string => (typeof value === 'string' ? value : '');

/** The rows a message carries; anything unreadable is left out, never guessed. */
export function parseAgents(value: unknown): { agents: AgentRow[]; programs: AgentProgram[] } | null {
  if (!isObject(value) || !Array.isArray(value.agents)) return null;
  const agents = value.agents.filter((row): row is AgentRow => isObject(row) && typeof row.id === 'string' &&
    typeof row.label === 'string' && STATUSES.includes(row.status as AgentStatus) && Array.isArray(row.asks) &&
    Array.isArray(row.transcript));
  const programs = (Array.isArray(value.programs) ? value.programs : []).filter((p): p is AgentProgram =>
    isObject(p) && typeof p.id === 'string' && typeof p.label === 'string' && typeof p.installed === 'boolean');
  return { agents, programs };
}

/** Which section an outside agent's row is in. */
export function agentGroup(row: AgentRow): 'needs' | 'working' | 'done' {
  return row.status === 'needs-you' ? 'needs' : row.status === 'working' ? 'working' : 'done';
}

/** Its quiet line: "Codex · Asks to run a command", with what it reported spending by its own count. */
export function agentSub(row: AgentRow): string {
  const cost = row.costUsd != null ? ` · about $${row.costUsd.toFixed(2)} by its own count` : '';
  return `${row.label} · ${row.line || STATUS_WORDS[row.status]}${cost}`;
}

/** Its time words: "Working 1:12" while it works (from its start), else nothing. */
export function agentTime(row: AgentRow, now = Date.now() / 1000): string {
  return row.status === 'working' ? `Working ${clock((now - row.at) * 1000)}` : '';
}

/** An entry of the transcript, in words: whose it is first. Hooke's own are marked as Hooke's, never as yours. A
 * command is said as itself ("Ran · hooke status"), not as "Ran a command: …". */
export function entryWords(entry: AgentEntry): { who: string; text: string } {
  const text = entry.text.replace(/^\[Hooke\]\s*/, '');
  if (entry.role === 'hooke') return { who: entry.how === 'steered' ? 'Hooke, while it worked' : 'Hooke', text };
  if (entry.role === 'tool') {
    const files = /^(Changed|Changing) files: /.test(text);
    const bare = text.replace(/^(Ran|Running) a command: /, '').replace(/^(Changed|Changing) files: /, '')
      .replace(/^Using (Bash|Read|Write|Edit): /, '$1 ');
    const done = files ? 'Changed' : 'Ran';
    return { who: entry.state === 'failed' ? 'Failed' : entry.state === 'running' ? (files ? 'Changing' : 'Running') : done, text: bare };
  }
  if (entry.role === 'ask') return { who: 'Asked', text };
  if (entry.role === 'note') return { who: '', text };
  return { who: entry.final ? 'Answer' : 'Said', text };
}

/** An agent's words with its **bold** and `code` drawn as such, never as markup characters. */
export function inline(text: string): Node[] {
  const out: Node[] = [];
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');  // a link is said as its words
  for (const part of text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)) {
    if (!part) continue;
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) out.push(el('strong', '', part.slice(2, -2)));
    else if (part.startsWith('`') && part.endsWith('`') && part.length > 2) out.push(el('code', '', part.slice(1, -1)));
    else out.push(document.createTextNode?.(part) ?? el('span', '', part));
  }
  return out;
}

/** A refusal as the panel says it: what happened, then what to do. */
export function refusalWords(body: unknown, fallback: string): string {
  if (!isObject(body)) return fallback;
  const says = str(body.says), next = str(body.next);
  return says ? (next ? `${says} ${next.charAt(0).toUpperCase()}${next.slice(1)}.` : says) : fallback;
}

export class AgentsPanel {
  private built: HTMLElement | null = null;
  private readonly request: typeof fetch;
  private options: Options;
  private agents: AgentRow[] = [];
  private programs: AgentProgram[] = [];
  private full = new Map<string, AgentEntry[]>();  // the whole transcript of an opened row, read when it opens
  private opened = new Set<string>();
  private sending = new Set<string>();  // asks and verbs sent, until the list says what happened
  private list!: HTMLElement;
  private status!: HTMLElement;
  private program!: HTMLSelectElement;
  private task!: HTMLTextAreaElement;
  private start!: HTMLButtonElement;
  private signature = '';
  private asksBuilt: HTMLElement | null = null;
  private asksSignature = '';
  private readonly times = new Map<string, HTMLElement>();
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(options: Options = {}) {
    this.options = options;
    this.request = options.request ?? ((...args) => fetch(...args));
  }

  /** The panel, made the first time it is needed (the module is imported before any page exists). */
  get root(): HTMLElement {
    if (this.built) return this.built;
    const root = this.build(); this.draw(true); return root;
  }

  private build(): HTMLElement {
    const root = el('div', 'agents-panel');
    this.built = root;
    const form = el('form', 'agents-start');
    form.setAttribute('aria-label', 'Start an outside agent');
    this.program = el('select', 'mm-control agents-program'); this.program.setAttribute('aria-label', 'Which agent');
    this.task = el('textarea', 'agents-task'); this.task.setAttribute('aria-label', 'What it should do');
    this.task.placeholder = 'What should it do?';
    this.task.title = 'It works in this Project, in a Study of its own, and reports back for your review';
    this.task.rows = 3;
    this.start = el('button', 'mm-primary agents-go', 'Start'); this.start.type = 'submit';
    form.append(this.program, this.task, this.start);
    form.addEventListener('submit', (event) => { event.preventDefault(); void this.begin(); });
    this.status = el('p', 'agents-status'); this.status.setAttribute('role', 'status');
    this.list = el('div', 'agents-list');
    const note = el('p', 'mm-drawer-note agents-note',
      'Agents run on this Mac with your own sign-in. What they ask to do comes here for you to allow or decline; Hooke never answers for you.');
    root.append(form, this.status, this.list, note);
    return root;
  }

  /** Redrawn only when what it shows changed: the workspace calls this on every refresh. */
  configure(options: Options): void { this.options = { ...this.options, ...options }; this.draw(false); }

  private headers(): Record<string, string> {
    const token = (typeof window === 'undefined' ? undefined : window as unknown as { hookeStudioToken?: string })?.hookeStudioToken;
    return { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'agents', ...(token ? { 'X-Hooke-Studio': token } : {}) };
  }

  /** The server's `agents` message (and the list it answers on refresh). */
  message(value: unknown): void {
    const parsed = parseAgents(value); if (!parsed) return;
    this.agents = parsed.agents;
    if (parsed.programs.length) this.programs = parsed.programs;
    for (const row of this.agents) {
      const known = this.full.get(row.id);
      if (known && row.transcript.length) {  // keep the whole transcript growing with each peek
        const last = known[known.length - 1]?.n ?? 0;
        const kept = known.filter((e) => !row.transcript.some((t) => t.n === e.n));
        this.full.set(row.id, [...kept, ...row.transcript].sort((a, b) => a.n - b.n));
        if (row.transcript[0].n > last + 1) void this.read(row.id);  // a gap: read it whole again
      }
    }
    this.sending.clear();
    this.draw(false);
    this.drawAsks();
    this.tickWhileWorking();
    this.options.changed?.();
  }

  /** The approval card over the composer (Hooke's ask card): every ask waiting, answered here, on the Agents panel's
   * row, or by the person's own spoken yes or no. Hidden while nothing waits. */
  get asksRoot(): HTMLElement {
    if (this.asksBuilt) return this.asksBuilt;
    const root = el('div', 'agents-asks'); root.setAttribute('role', 'region');
    root.setAttribute('aria-label', 'Agents asking you'); root.hidden = true;
    this.asksBuilt = root; this.drawAsks(); return root;
  }

  private drawAsks(): void {
    const root = this.asksBuilt; if (!root) return;
    // How many work and how many are done, on the card's root: what a journey's harness waits for.
    root.dataset.working = String(this.agents.filter((row) => row.status === 'working').length);
    root.dataset.done = String(this.agents.filter((row) => row.status === 'done').length);
    const waiting = this.agents.flatMap((row) => row.asks.map((ask) => ({ row, ask })));
    const signature = JSON.stringify([waiting.map(({ row, ask }) => [row.id, ask.id, ask.detail]), [...this.sending]]);
    if (signature === this.asksSignature) return;
    this.asksSignature = signature;
    root.hidden = !waiting.length;
    root.replaceChildren(...waiting.slice(0, 3).map(({ row, ask }) => {
      const card = el('div', 'agents-asks-card'); card.dataset.ask = ask.id;
      const head = el('div', 'agents-asks-head');
      head.append(litCell('needs', { size: 12, label: `${row.label} needs you` }),
        el('span', 'agents-asks-title', `${row.label} asks to ${ask.title.toLowerCase()}`));
      card.append(head, this.ask(row, ask, true));
      return card;
    }));
    if (waiting.length > 3) root.append(el('p', 'agents-asks-more', `${waiting.length - 3} more in Agents`));
  }

  private tickWhileWorking(): void {
    const working = this.agents.some((row) => row.status === 'working');
    if (working && this.timer === null) {
      this.timer = setInterval(() => {
        const now = Date.now() / 1000;
        for (const row of this.agents) { const span = this.times.get(row.id); if (span) span.textContent = agentTime(row, now); }
      }, 1000);
    } else if (!working && this.timer !== null) { clearInterval(this.timer); this.timer = null; }
  }

  /** How many outside agents work, and how many wait for you (the rail's mark and badge). */
  counts(): { working: number; needs: number } {
    return { working: this.agents.filter((r) => r.status === 'working').length,
      needs: this.agents.filter((r) => r.status === 'needs-you').length };
  }

  async refresh(fresh = false): Promise<void> {
    try {
      const reply = await this.request(`/api/agents${fresh ? '?programs=fresh' : ''}`, { cache: 'no-store', headers: this.headers() });
      const body = await reply.json();
      if (!reply.ok) { this.say(refusalWords(body, 'The Agents list could not be read.')); return; }
      this.message({ type: 'agents', ...body });
    } catch { this.say('The Agents list could not be read: Hooke is not answering.'); }
  }

  private say(words: string): void { void this.root; this.status.textContent = words; this.status.hidden = !words; }

  private async post(path: string, body: unknown, failed: string): Promise<boolean> {
    try {
      const reply = await this.request(path, { method: 'POST', headers: this.headers(), body: JSON.stringify(body) });
      const data = await reply.json().catch(() => null);
      if (!reply.ok) { this.say(`Not sent: ${refusalWords(data, failed)}`); return false; }
      this.say('');
      return true;
    } catch { this.say(`Not sent: ${failed} Hooke is not answering.`); return false; }
  }

  private async begin(): Promise<void> {
    const program = this.program.value, task = this.task.value.trim();
    if (!program || !task) { this.say('Choose an agent and say what it should do.'); return; }
    this.start.disabled = true; this.say('Starting…');
    const project = this.options.project?.();
    if (await this.post('/api/agents', { program, task, ...(project ? { project } : {}) }, 'The agent did not start.')) {
      this.task.value = '';
      this.say('');
    }
    this.start.disabled = false;
  }

  async answer(row: AgentRow, ask: AgentAsk, answer: 'allow' | 'deny', always = false): Promise<void> {
    if (this.sending.has(ask.id)) return;
    this.sending.add(ask.id); this.draw(true); this.drawAsks();
    if (!await this.post(`/api/agents/${encodeURIComponent(row.id)}/answer`, { permission: ask.id, answer, always },
      'Your answer did not reach the agent.')) { this.sending.delete(ask.id); this.draw(true); this.drawAsks(); }
  }

  async reply(row: AgentRow, text: string): Promise<boolean> {
    if (!text.trim()) return false;
    return this.post(`/api/agents/${encodeURIComponent(row.id)}/note`, { text }, `${row.label} did not get it.`);
  }

  private async verb(row: AgentRow, verb: 'stop' | 'interrupt'): Promise<void> {
    const key = `${row.id}:${verb}`; if (this.sending.has(key)) return;
    this.sending.add(key); this.draw(true);
    if (!await this.post(`/api/agents/${encodeURIComponent(row.id)}/${verb}`, {}, `${row.label} was not ${verb === 'stop' ? 'stopped' : 'interrupted'}.`)) {
      this.sending.delete(key); this.draw(true);
    }
  }

  private async read(lid: string): Promise<void> {
    try {
      const reply = await this.request(`/api/agents/${encodeURIComponent(lid)}`, { cache: 'no-store', headers: this.headers() });
      const body = await reply.json();
      if (reply.ok && isObject(body.agent) && Array.isArray(body.agent.transcript)) {
        this.full.set(lid, body.agent.transcript as AgentEntry[]); this.draw(true);
      }
    } catch { /* the peek stays: the next message carries the newest lines */ }
  }

  private toggle(row: AgentRow): void {
    if (this.opened.has(row.id)) this.opened.delete(row.id);
    else { this.opened.add(row.id); if (!this.full.has(row.id)) { this.full.set(row.id, row.transcript); void this.read(row.id); } }
    this.draw(true);
  }

  private draw(force: boolean): void {
    if (!this.built) return;  // drawn when it is first shown
    const researchers = this.options.researchers?.() ?? [];
    const signature = JSON.stringify([this.agents, this.programs, researchers, [...this.opened], [...this.sending],
      [...this.full].map(([k, v]) => [k, v.length])]);
    if (!force && signature === this.signature) return;
    this.signature = signature;
    this.drawPrograms();
    this.times.clear();
    const parts: HTMLElement[] = [];
    if (!this.agents.length && !researchers.length) {
      parts.push(el('p', 'mm-drawer-empty agents-empty', 'No agents yet. Start one here, or ask the Assistant ("ask Codex to…").'));
    }
    for (const section of SECTIONS) {
      const mine = this.agents.filter((row) => agentGroup(row) === section.id);
      const theirs = researchers.filter((r) => r.group === section.id);
      if (!mine.length && !theirs.length) continue;
      const head = el('div', 'mm-dsection agents-group'); head.dataset.group = section.id;
      head.append(section.title, ' ', el('span', 'mm-num', String(mine.length + theirs.length)));
      parts.push(head, ...mine.map((row) => this.row(row)), ...theirs.map((r) => this.researcher(r)));
    }
    this.list.replaceChildren(...parts);
  }

  private drawPrograms(): void {
    const chosen = this.program.value;
    const options = this.programs.map((p) => {
      const o = el('option', '', p.installed ? p.label : `${p.label} (not installed)`); o.value = p.id;
      o.disabled = !p.installed; if (!p.installed && p.next) o.title = p.next;
      return o;
    });
    this.program.replaceChildren(...options);
    const first = this.programs.find((p) => p.installed);
    this.program.value = this.programs.some((p) => p.id === chosen && p.installed) ? chosen : first?.id ?? '';
    this.start.disabled = !first;
    if (this.programs.length && !first) this.say('No outside agent is installed on this Mac. Install Codex, Claude Code or Grok Build, sign in once, then open this again.');
  }

  private row(row: AgentRow): HTMLElement {
    const wrap = el('div', 'mm-row-wrap agents-row'); wrap.dataset.agent = row.id; wrap.dataset.status = row.status;
    const open = el('button', 'mm-row agents-open'); open.type = 'button';
    open.setAttribute('aria-expanded', String(this.opened.has(row.id)));
    open.setAttribute('aria-label', `${row.label}: ${row.task}. ${STATUS_WORDS[row.status]}`);
    open.title = [row.task, row.model ? `Model: ${row.model}` : '', row.projectName ? `In ${row.projectName}` : '',
      row.budgetUsd != null ? `Held to $${row.budgetUsd.toFixed(2)}` : '', row.tokens != null ? `${row.tokens} tokens by its own count` : '']
      .filter(Boolean).join('\n');
    const thumb = el('span', 'mm-row-thumb agents-thumb'); thumb.dataset.program = row.program;
    thumb.append(el('span', 'agents-initial', row.label.charAt(0)), litCell(CELL[row.status], { size: 12, label: `${row.label}: ${STATUS_WORDS[row.status]}` }));
    const words = el('span', 'mm-row-words');
    const sub = el('span', 'mm-row-sub', agentSub(row));
    const time = el('span', 'agents-time', agentTime(row)); this.times.set(row.id, time);
    if (time.textContent) sub.prepend(time, ' · ');
    words.append(el('span', 'mm-row-title', row.task), sub);
    open.append(thumb, words);
    open.addEventListener('click', () => this.toggle(row));
    wrap.append(open, moreButton(`More for ${row.label}`, () => this.menu(row)));
    // Its report, kept in the Project and awaiting the person's review in its Study.
    if (row.report?.returned && row.study && this.options.openStudy) {
      const review = el('button', 'agents-review data-plain', row.report.claims
        ? `Review its report and ${row.report.claims === 1 ? 'claim' : `${row.report.claims} claims`} ›` : 'Review its report ›');
      review.type = 'button'; review.title = 'Open its Study, where its report and claim wait for your review';
      review.addEventListener('click', () => this.options.openStudy?.(row.study));
      wrap.append(review);
    }
    for (const ask of row.asks) wrap.append(this.ask(row, ask));
    if (this.opened.has(row.id)) wrap.append(this.peek(row));
    return wrap;
  }

  private ask(row: AgentRow, ask: AgentAsk, card = false): HTMLElement {
    const box = el('div', card ? 'agents-ask is-card' : 'mm-row-question agents-ask'); box.dataset.ask = ask.id;
    if (ask.detail) box.append(el('code', 'agents-ask-detail', ask.detail));
    if (ask.reason) box.append(el('span', 'agents-ask-reason', ask.reason));
    const sending = this.sending.has(ask.id);
    const buttons = el('div', 'agents-ask-buttons');
    const make = (words: string, cls: string, act: () => void) => {
      const b = el('button', cls, words); b.type = 'button'; b.disabled = sending; b.addEventListener('click', act); return b;
    };
    buttons.append(make('Allow', 'mm-primary', () => void this.answer(row, ask, 'allow')));
    if (ask.always) buttons.append(make('Allow for this session', 'mm-control', () => void this.answer(row, ask, 'allow', true)));
    buttons.append(make('Decline', 'mm-control', () => void this.answer(row, ask, 'deny')));
    box.append(buttons);
    if (sending) box.append(el('span', 'agents-sending', 'Sending your answer…'));
    else if (card) box.append(el('span', 'agents-ask-voice', 'Or say “allow it” or “no”.'));
    return box;
  }

  private peek(row: AgentRow): HTMLElement {
    const peek = el('div', 'agents-peek');
    const lines = el('ol', 'agents-transcript'); lines.setAttribute('aria-label', `${row.label}'s transcript`);
    const entries = (this.full.get(row.id) ?? row.transcript).slice(-60);
    for (const entry of entries) {
      const words = entryWords(entry);
      const li = el('li', `agents-line is-${entry.role}`);
      const body = el('span', 'agents-text');
      if (entry.role === 'agent') body.append(...inline(words.text)); else body.textContent = words.text;
      if (entry.role === 'tool') body.title = words.text;  // a long command on one line; all of it on hover
      li.append(el('span', 'agents-who', words.who), body);
      lines.append(li);
    }
    if (row.live) { const li = el('li', 'agents-line is-live'); li.append(el('span', 'agents-who', 'Writing'), el('span', 'agents-text', row.live)); lines.append(li); }
    peek.append(lines);
    // A live transcript reads from its newest line: kept at the bottom as it grows.
    queueMicrotask(() => { if (typeof lines.scrollHeight === 'number') lines.scrollTop = lines.scrollHeight; });
    if (row.status === 'working' || row.status === 'needs-you' || row.status === 'done') {
      const form = el('form', 'agents-reply');
      const input = el('input', 'agents-reply-text'); input.type = 'text';
      input.setAttribute('aria-label', `Tell ${row.label}`); input.placeholder = `Tell ${row.label}… (sent as Hooke's note)`;
      const send = el('button', 'mm-control', 'Send'); send.type = 'submit';
      form.append(input, send);
      form.addEventListener('submit', (event) => { event.preventDefault(); const text = input.value; send.disabled = true;
        void this.reply(row, text).then((ok) => { send.disabled = false; if (ok) input.value = ''; }); });
      peek.append(form);
    }
    const facts = [row.study ? 'Works in a Study of its own in this Project' : '', row.report?.returned ? 'Report returned' : '',
      row.report?.claims ? `${row.report.claims} claim${row.report.claims === 1 ? '' : 's'} awaiting your review` : ''].filter(Boolean);
    if (facts.length) peek.append(el('p', 'agents-facts', facts.join(' · ')));
    return peek;
  }

  private menu(row: AgentRow): MenuItem[] {
    const items: MenuItem[] = [];
    const live = row.status === 'working' || row.status === 'needs-you' || row.status === 'done';
    if (row.status === 'working' || row.status === 'needs-you') {
      const sent = this.sending.has(`${row.id}:interrupt`);
      items.push({ label: sent ? 'Interrupt requested' : 'Interrupt', disabled: sent, title: 'Stop its current step; it waits for your reply', act: () => void this.verb(row, 'interrupt') });
    }
    if (live) {
      const sent = this.sending.has(`${row.id}:stop`);
      items.push({ label: sent ? 'Stop requested' : 'Stop', disabled: sent, danger: true, title: 'End it and revoke its connection; what it kept stays', act: () => void this.verb(row, 'stop') });
    }
    items.push({ label: this.opened.has(row.id) ? 'Hide transcript' : 'Show transcript', act: () => this.toggle(row) });
    return items;
  }

  private researcher(r: ResearcherWorker): HTMLElement {
    const wrap = el('div', 'mm-row-wrap agents-row is-researcher'); wrap.dataset.rid = r.rid;
    const open = el('button', 'mm-row'); open.type = 'button';
    open.setAttribute('aria-label', `${r.name}: ${r.title}. ${r.words}`);
    const thumb = el('span', 'mm-row-thumb agents-thumb'); thumb.dataset.program = 'researcher';
    thumb.append(el('span', 'agents-initial', 'H'), litCell(r.cell, { size: 12, label: `${r.name}: ${r.words}` }));
    const words = el('span', 'mm-row-words');
    words.append(el('span', 'mm-row-title', r.title), el('span', 'mm-row-sub', `Hooke ${r.name} · ${r.words}`));
    open.append(thumb, words);
    open.addEventListener('click', () => this.options.openResearcher?.(r.rid));
    wrap.append(open);
    return wrap;
  }
}

export const agentsPanel = new AgentsPanel();
