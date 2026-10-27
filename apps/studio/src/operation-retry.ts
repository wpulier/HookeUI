/** One server-retained failed action. Never accepts an ask or tool arguments. */
export type RetryRequest = (fields: { receiptId: string } | { actionId: string }) => Promise<Record<string, unknown>>;
export interface RetryOption { actionId: string; tool: string; available: boolean; reason: string; status: string }

export class FailedStepRetry {
  rows: RetryOption[] = [];
  message = '';
  pending = false;
  private serial = 0;
  private readonly submitted = new Set<string>();
  private readonly request: RetryRequest;
  private readonly changed: () => void;
  constructor(request: RetryRequest, changed: () => void) { this.request = request; this.changed = changed; }
  async load(receiptId: string): Promise<void> {
    const serial = ++this.serial;
    this.rows = []; this.message = '';
    try {
      const result = await this.request({ receiptId });
      if (serial !== this.serial) return;
      this.rows = Array.isArray(result.operations) ? result.operations.filter((row): row is RetryOption =>
        !!row && typeof row === 'object' && typeof row.actionId === 'string' && typeof row.tool === 'string'
        && typeof row.available === 'boolean' && typeof row.reason === 'string') : [];
      this.message = typeof result.unavailable === 'string' ? result.unavailable : '';
    } catch {
      if (serial !== this.serial) return;
      this.message = 'Retry availability could not be checked on this connection.';
    }
    this.changed();
  }
  async retry(actionId: string): Promise<void> {
    if (this.pending || this.submitted.has(actionId) || !this.rows.some(r => r.actionId === actionId && r.available)) return;
    this.submitted.add(actionId);
    this.rows = this.rows.map(row => row.actionId === actionId ? { ...row, available: false } : row);
    this.pending = true; this.message = 'Retrying this failed step…'; this.changed();
    try {
      const result = await this.request({ actionId });
      if (result.ok !== true) throw new Error(typeof result.error === 'string' ? result.error : 'This step failed again.');
      this.message = 'This step completed. Other steps were not repeated.';
    } catch (error) {
      this.message = error instanceof Error ? error.message : 'This step could not be retried.';
    } finally {
      this.pending = false; this.changed();
    }
  }
  close(): void { ++this.serial; }
}
