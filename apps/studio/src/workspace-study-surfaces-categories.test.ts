import { expect, test } from 'vitest';
import { cardIn, sheetAlone, SURFACE_CATEGORIES, unavailableFoldOpens } from './workspace-study-surfaces';

// A Models card as the page draws it: its section in data-section (run-spec.ts modelSection, the one Tools By model
// uses), where it runs in data-runs, its words.
const card = (section: string, runs = 'mac') => ({ dataset: { section, runs } }) as unknown as HTMLElement;

test('categories follow a card\'s section, never its words, so Models\' counts are Tools By model\'s', () => {
  expect(cardIn(card('ready'), 'ready', 'cellpose-sam ready')).toBe(true);
  expect(cardIn(card('unchecked'), 'ready', 'not checked')).toBe(false); // can run here, but Not checked is its own section
  expect(cardIn(card('unchecked'), 'unchecked', 'cellpose-sam ready')).toBe(true); // its words never decide
  expect(cardIn(card('needs'), 'needs', '')).toBe(true);
  expect(cardIn(card('needs'), 'setup', 'add key')).toBe(false);
  expect(cardIn(card('setup'), 'setup', 'setting up · 40%')).toBe(true);
  expect(cardIn(card('away'), 'away', '')).toBe(true);
  expect(cardIn(card('away'), 'setup', '')).toBe(false); // listed under Not on this Mac instead
  expect(cardIn(card('ready', 'cloud'), 'cloud', 'cloud model, google')).toBe(true);
  expect(cardIn(card('ready', 'cloud'), 'mac', 'on this mac')).toBe(false); // where it runs, not its words
  expect(cardIn(card('ready'), 'mac', 'built-in method')).toBe(true);
  expect(cardIn(card('ready'), 'all', '')).toBe(true);
});

test('choosing "Not on this Mac" on the side opens its fold, so one press shows its cards (0.3.0 did)', () => {
  const away = SURFACE_CATEGORIES.find(([, name]) => name === 'Not on this Mac')![0];
  expect(away).toBe('away'); // the key the cards' data-section and the fold carry (run-spec.ts modelSection)
  expect(unavailableFoldOpens(away, '')).toBe(true);
  expect(unavailableFoldOpens('all', '')).toBe(false); // it stays as the person left it
  expect(unavailableFoldOpens('setup', '')).toBe(false);
  expect(unavailableFoldOpens('all', 'fiji')).toBe(true); // a search looks through every card
});

test('a Models card\'s setup button names the first step a person meets', async () => {
  const { firstStepWords } = await import('./modelspage');
  expect(firstStepWords([{ kind: 'key', by: 'you' }, { kind: 'check', by: 'app' }])).toBe('Add key');
  expect(firstStepWords([{ kind: 'licence', by: 'you' }, { kind: 'download', by: 'app' }])).toBe('Accept licence');
  expect(firstStepWords([{ kind: 'download', by: 'app' }, { kind: 'check', by: 'app' }])).toBe('Download and turn on');
  expect(firstStepWords([{ kind: 'install', by: 'app' }])).toBe('Turn on');
});

test('a setup sheet opened from outside Models, and Add model or tool from Tools, are sheets alone over the Project', () => {
  for (const hash of ['#models/micro-sam-lm/setup', '#toolbox/add', '#toolbox/add-find', '#models/add-link', '#models/add']) expect(sheetAlone(hash), hash).toBe(true);
  for (const hash of ['#models', '#models/micro-sam-lm', '#toolbox', '#toolbox/all', '#models/addition']) expect(sheetAlone(hash), hash).toBe(false);
});
