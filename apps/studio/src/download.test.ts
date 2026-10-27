// A model's Download on the Capabilities page (capabilities.ts downloadView; orchestrator/downloads.py): what it shows
// before, while and after a download, from the wire's `setup.downloads` row.
import { describe, expect, it } from 'vitest';
import { downloadView } from './capabilities';
import type { DownloadRow } from './protocol';

const row = (over: Partial<DownloadRow> = {}): DownloadRow => ({
  model: 'sam2.1-tiny', name: 'SAM 2.1 tiny', state: 'downloading', got: 41, total: 100, says: 'Downloading SAM 2.1 tiny', ...over,
});

describe('downloadView', () => {
  it('offers the download with its size until one starts', () => {
    expect(downloadView(undefined, 'Download 156 MB')).toEqual({ busy: false, button: 'Download 156 MB', why: '' });
  });

  it('says how far it has got while it downloads, then that it checks the files', () => {
    expect(downloadView(row({ state: 'queued', got: 0 }), 'x')).toEqual({ busy: true, words: 'Waiting to download' });
    expect(downloadView(row(), 'x')).toEqual({ busy: true, words: 'Downloading 41%' });
    expect(downloadView(row({ got: 99.9 }), 'x')).toEqual({ busy: true, words: 'Downloading 99%' }); // never 100% before checked
    expect(downloadView(row({ state: 'checking' }), 'x')).toEqual({ busy: true, words: 'Checking the files' });
  });

  it('says why it failed and offers to try again; a cancel offers the download again', () => {
    const why = 'the connection dropped while downloading model.safetensors. Try again: it carries on where it stopped';
    expect(downloadView(row({ state: 'failed', says: why }), 'Download 156 MB')).toEqual({ busy: false, button: 'Try again', why });
    const cancelled = 'Download of SAM 2.1 tiny cancelled: what it got is kept for next time';
    expect(downloadView(row({ state: 'cancelled', says: cancelled }), 'Download 156 MB'))
      .toEqual({ busy: false, button: 'Download 156 MB', why: cancelled });
  });
});
