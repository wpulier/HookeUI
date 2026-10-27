// Deliberate archive inspection, separate from the conversation journal and replay.
import { ExecutionDisclosure } from './executionlog';
import type { ExecutionReference } from './protocol';

interface ArchivedRequest extends ExecutionReference {
  words?: string;
  interrupted?: boolean;
}
interface HistoryPage {
  entries: ArchivedRequest[];
  nextCursor: string | null;
  archive: { storedBytes: number; dropped: number; writeErrors: number; recoveryGaps: number };
}

export class ExecutionHistory {
  readonly root = document.createElement('details');
  private readonly query = document.createElement('input');
  private readonly status = document.createElement('p');
  private readonly list = document.createElement('ol');
  private readonly more = document.createElement('button');
  private readonly search = document.createElement('button');
  private disclosures: ExecutionDisclosure[] = [];
  private cursor: string | null = null;
  private lastQuery = '';
  private abort: AbortController | null = null;

  constructor() {
    this.root.className = 'execution-history';
    const summary = document.createElement('summary'); summary.textContent = 'Earlier requests';
    const controls = document.createElement('form');
    this.query.type = 'search'; this.query.maxLength = 200;
    this.query.placeholder = 'Find a request'; this.query.setAttribute('aria-label', 'Search execution history');
    this.search.type = 'submit'; this.search.textContent = 'Search';
    controls.append(this.query, this.search);
    this.status.setAttribute('role', 'status');
    this.list.setAttribute('aria-label', 'Retained requests');
    this.more.type = 'button'; this.more.textContent = 'More requests'; this.more.hidden = true;
    this.root.append(summary, controls, this.status, this.list, this.more);
    controls.addEventListener('submit', event => { event.preventDefault(); void this.load(false); });
    this.more.addEventListener('click', () => void this.load(true));
    this.root.addEventListener('toggle', () => {
      if (this.root.open && !this.list.children.length) void this.load(false);
      if (!this.root.open) {
        this.abort?.abort(); this.abort = null;
        this.search.disabled = this.more.disabled = false;
      }
    });
  }

  private async load(append: boolean): Promise<void> {
    this.abort?.abort();
    const abort = this.abort = new AbortController();
    this.search.disabled = this.more.disabled = true;
    this.status.textContent = 'Loading retained requests…';
    if (!append) {
      for (const disclosure of this.disclosures) disclosure.dispose();
      this.disclosures = []; this.list.replaceChildren(); this.cursor = null;
      this.more.hidden = true;
      this.lastQuery = this.query.value.trim();
    }
    const params = new URLSearchParams({ q: this.lastQuery });
    if (append && this.cursor) params.set('cursor', this.cursor);
    try {
      const response = await fetch(`/api/execution/history/search?${params}`, { signal: abort.signal, cache: 'no-store' });
      if (!response.ok) throw new Error('unavailable');
      const page = await response.json() as HistoryPage;
      if (abort.signal.aborted) return;
      for (const entry of page.entries) {
        const item = document.createElement('li');
        const words = document.createElement('p'); words.textContent = entry.words || `${entry.kind} · words not retained`;
        const when = document.createElement('small');
        when.textContent = `${new Date(entry.at * 1000).toISOString()}${entry.interrupted ? ' · interrupted' : ''}`;
        const disclosure = new ExecutionDisclosure(entry);
        this.disclosures.push(disclosure);
        item.append(words, when, disclosure.element); this.list.append(item);
      }
      this.cursor = page.nextCursor; this.more.hidden = !this.cursor;
      const gaps = page.archive.dropped + page.archive.writeErrors + page.archive.recoveryGaps;
      this.status.textContent = `${this.list.children.length} requests shown · ${(page.archive.storedBytes / 1048576).toFixed(1)} MB retained${gaps ? ` · ${gaps} archive gaps` : ''}. Execution facts; recordings are separate.`;
    } catch {
      if (!abort.signal.aborted) this.status.textContent = 'History unavailable or changed. Search again to reload; your conversation is preserved.';
    } finally {
      if (this.abort === abort) this.search.disabled = this.more.disabled = false;
    }
  }
}
