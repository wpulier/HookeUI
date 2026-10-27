import type { ClientCommand } from './protocol';

export type ResearchSubmission = Extract<ClientCommand, { type: 'research-note' | 'research-followup' }> & {requestId:string};
const KEY = 'research-delivery-outbox';

/** Transport replay only. The server's existing research session owns admission and outcomes. */
export class ResearchOutbox {
  private pending: ResearchSubmission[] = [];
  constructor() {
    try {
      const saved: unknown = JSON.parse(sessionStorage.getItem(KEY) ?? '[]');
      if (Array.isArray(saved)) this.pending = saved.filter((row): row is ResearchSubmission =>
        row && ['research-note','research-followup'].includes(row.type) && typeof row.requestId === 'string'
        && typeof row.rid === 'string' && typeof row.text === 'string' && typeof row.selection?.day === 'string');
    } catch { /* Memory-only replay remains available when browser storage is unavailable. */ }
  }
  admit(command: Omit<ResearchSubmission, 'requestId'>): ResearchSubmission {
    const same = this.pending.find(row => JSON.stringify({...row, requestId:undefined}) === JSON.stringify(command));
    if (same) return same;
    const submission = {...command, requestId:crypto.randomUUID()} as ResearchSubmission;
    this.pending.push(submission); this.save(); return submission;
  }
  acknowledge(day: string, rid: string, ids: string[]): void {
    this.pending = this.pending.filter(row => row.rid !== rid || row.selection?.day !== day || !ids.includes(row.requestId));
    this.save();
  }
  forResearcher(day: string, rid: string): ResearchSubmission[] {
    return this.pending.filter(row => row.rid === rid && row.selection?.day === day);
  }
  private save(): void {
    try { sessionStorage.setItem(KEY, JSON.stringify(this.pending)); } catch { /* Existing in-memory IDs remain stable. */ }
  }
}
