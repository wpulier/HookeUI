import { describe, expect, it } from 'vitest';
import { runChipWords } from './run-chip-words';

describe('runChipWords', () => {
  it('names each state on its own instead of folding them together', () => {
    const label = (status: Parameters<typeof runChipWords>[0]['status'], interrupted = false) => runChipWords({ status, message: '', interrupted }).label;
    expect(label('queued')).toBe('Queued');
    expect(label('running')).toBe('Running');
    expect(label('stopping')).toBe('Stop requested');
    expect(label('unknown')).toBe('Status unknown');
    expect(label('completed')).toBe('Done');
    expect(label('stopped')).toBe('Stopped');
    expect(label('failed')).toBe('Failed');
    expect(label('failed', true)).toBe('Interrupted');
  });
  it('keeps the full message as the title, and explains an unknown status', () => {
    expect(runChipWords({ status: 'failed', message: 'Cellpose ran out of memory' }).title).toBe('Cellpose ran out of memory');
    expect(runChipWords({ status: 'unknown', message: '' }).title).toMatch(/could not confirm/);
  });
  it('marks problems and busy work with distinct tones', () => {
    expect(runChipWords({ status: 'failed', message: '' }).tone).toBe('problem');
    expect(runChipWords({ status: 'queued', message: '' }).tone).toBe('busy');
    expect(runChipWords({ status: 'completed', message: '' }).tone).toBe('done');
  });
});

describe('the Project row shows its run chip only when it matters', () => {
  it('in front: Failed, Interrupted, Stop requested; not in front: Running and Queued too; never Done', async () => {
    const { projectChipShown } = await import('./run-chip-words');
    const shown = (status: Parameters<typeof projectChipShown>[0]['status'], front: boolean) => projectChipShown({ status }, front);
    expect(['failed', 'stopping'].map((s) => shown(s as never, true))).toEqual([true, true]);
    expect(['running', 'queued', 'completed', 'stopped', 'unknown'].map((s) => shown(s as never, true))).toEqual([false, false, false, false, false]);
    expect(['running', 'queued', 'failed', 'stopping'].map((s) => shown(s as never, false))).toEqual([true, true, true, true]);
    expect(shown('completed', false)).toBe(false);
  });
});
