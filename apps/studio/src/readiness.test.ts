import { describe, expect, it } from 'vitest';
import type { Readiness } from './protocol';
import { countLine, laterSteps, optionNote, readinessWords } from './readiness';

const key: Readiness = {
  status: 'needs-setup', word: 'Needs your Google Gemini API key', group: 'Needs setup',
  next: { kind: 'key', says: 'Add your Google Gemini API key', how: 'put GEMINI_API_KEY=... in ~/Desktop/micromind.txt', url: 'https://aistudio.google.com/apikey' },
  steps: [{ kind: 'key', says: 'Add your Google Gemini API key', how: 'put GEMINI_API_KEY=... in ~/Desktop/micromind.txt', url: 'https://aistudio.google.com/apikey' }],
};
const download: Readiness = {
  status: 'needs-setup', word: 'Download 3.5 GB', group: 'Needs setup',
  next: { kind: 'download', says: 'Download 3.5 GB', how: 'it downloads when first used' },
  steps: [{ kind: 'download', says: 'Download 3.5 GB', how: 'it downloads when first used' }],
};
const gated: Readiness = {
  status: 'needs-setup', word: 'Accept its licence on Hugging Face', group: 'Needs setup',
  next: { kind: 'licence', says: 'Accept its licence on Hugging Face', how: 'with the account your key belongs to', url: 'https://huggingface.co/x' },
  steps: [
    { kind: 'licence', says: 'Accept its licence on Hugging Face', how: 'with the account your key belongs to', url: 'https://huggingface.co/x' },
    { kind: 'download', says: 'Download 86 MB', how: 'it downloads when first used' },
  ],
};
const ready: Readiness = { status: 'ready', word: 'Ready', group: 'Ready', next: null, steps: [] };

describe('readiness in words', () => {
  it('says the next step and how to do it, and nothing when ready', () => {
    expect(readinessWords(key)).toBe('Needs your Google Gemini API key: put GEMINI_API_KEY=... in ~/Desktop/micromind.txt');
    expect(readinessWords(ready)).toBe('');
    expect(readinessWords(undefined)).toBe('');
  });

  it('keeps the rest of the checklist after the next step', () => {
    expect(laterSteps(gated)).toEqual(['Download 86 MB']);
    expect(laterSteps(key)).toEqual([]);
  });

  it('lets a model that downloads when first used be chosen, and says so', () => {
    expect(optionNote('~1.4 s per outline', true, download)).toBe('~1.4 s per outline · Download 3.5 GB: it downloads when first used');
    expect(optionNote('~1.4 s per outline', true, ready)).toBe('~1.4 s per outline');
  });

  it('says what an unavailable option needs, falling back on the server\'s why', () => {
    expect(optionNote('fast', false, key)).toBe('fast. Needs your Google Gemini API key: put GEMINI_API_KEY=... in ~/Desktop/micromind.txt.');
    expect(optionNote('fast', false, undefined, 'not in the commercial edition')).toBe('fast. Not in the commercial edition.');
  });

  it('counts what works here and what each missing key adds', () => {
    expect(countLine({ works: 155, total: 172, unlock: [{ key: 'OPENAI_API_KEY', service: 'OpenAI API', getKey: 'https://x', adds: 13 }] }))
      .toBe('155 of 172 can run here · Add your OpenAI API key: +13');
    expect(countLine(undefined)).toBe('');
  });
});
