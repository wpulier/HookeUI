// The words a newcomer typed in the fresh-profile walk of 2026-10-20 (L7e), against tools' declared facts.
import { expect, test } from 'vitest';
import { TASK_VOCABULARY, findByTask, stem, taskScore, type TaskVocabulary } from './task-search';

/** Declared facts as the catalog and capabilities give them: name, what it does, its capabilities' words, aliases. */
const catalog = [
  { name: 'Cellpose-SAM', facts: 'Outlines every cell in a view · Outline every cell: A cell finder segments every cell' },
  { name: 'InstanSeg (brightfield nuclei)', facts: 'Outlines every nucleus on a brightfield slide · instanseg nuclei' },
  { name: 'micro-sam (light microscopy)', facts: 'Outlines what you click, or every cell in a view' },
  { name: 'DINOv2 small', facts: 'Turns a picture into numbers that compare how things look' },
  { name: 'Trackastra', facts: 'Links outlined cells across frames into tracks' },
  { name: 'Swin2SR enlarge', facts: 'A clearer, larger picture' },
  { name: 'Spot measure', facts: 'Measures the size and brightness of each spot' },
];
const text = (row: typeof catalog[number]) => `${row.name} ${row.facts}`;
const names = (query: string, vocabulary: TaskVocabulary = []) => findByTask(catalog, text, query, vocabulary).rows.map(row => row.name);

test('stems join the forms a researcher writes', () => {
  expect(stem('segmentation')).toBe('segment');
  expect(stem('segment')).toBe('segment');
  expect(stem('Outlines')).toBe('outline');
  expect(stem('nuclei')).toBe('nucleus');
  expect(stem('boundaries')).toBe('boundary');
  expect(stem('labelled')).toBe('label');
  expect(stem('measurements')).toBe('measure');
  expect(stem('cells')).toBe('cell');
  expect(stem('class')).toBe('class');
});

test.each([
  ['segmentation', 'Cellpose-SAM'],
  ['segment the nuclei', 'Cellpose-SAM'],
  ['count nuclei', 'InstanSeg (brightfield nuclei)'],
  ['outline every cell', 'Cellpose-SAM'],
  ['separate touching cells', 'Cellpose-SAM'],
  ['cellpose', 'Cellpose-SAM'],
  ['segm', 'Cellpose-SAM'],
])('facts only: "%s" finds %s first, by its declared words', (query, first) => {
  expect(names(query)[0]).toBe(first);
  expect(names(query)).not.toContain('DINOv2 small');
});

test('facts only: a word no tool declares finds nothing, so it belongs in that tool\'s catalog entry', () => {
  expect(names('mask')).toEqual([]);
  expect(names('masks', TASK_VOCABULARY)[0]).toBe('Cellpose-SAM');
});

test('other tasks find their own tools, not segmentation', () => {
  expect(names('track cells over time')[0]).toBe('Trackastra');
  expect(names('measure the size of spots')[0]).toBe('Spot measure');
  expect(names('clearer picture')).toEqual(['Swin2SR enlarge']);
});

test('a description that matches nothing finds nothing; an empty one keeps every row in order', () => {
  expect(findByTask(catalog, text, 'quantum chromodynamics').rows).toEqual([]);
  expect(findByTask(catalog, text, '  ').rows).toEqual(catalog);
  for (const punctuation of ['?', '...', ' - ', '!?']) expect(findByTask(catalog, text, punctuation).rows).toEqual(catalog);
});

test('when no tool has every word, the closest are offered and said to be partial', () => {
  const found = findByTask(catalog, text, 'outline nuclei in zebrafish');
  expect(found.partial).toBe(true);
  expect(found.rows[0].name).toBe('InstanSeg (brightfield nuclei)');
  expect(findByTask(catalog, text, 'outline nuclei').partial).toBe(false);
});

test('"find" describes the search, not the task; alone it still finds tools named for finding', () => {
  const rows = [{ name: 'Bright objects', facts: 'Find bright regions in the whole image' }, ...catalog];
  expect(findByTask(rows, text, 'find cell boundaries').rows.map(row => row.name)).toContain('Cellpose-SAM');
  expect(findByTask(rows, text, 'find').rows[0].name).toBe('Bright objects');
});

test('the vocabulary option: a synonym counts, less than its own word; facts only, it does not', () => {
  expect(taskScore('Outlines every nucleus', 'nuclei', TASK_VOCABULARY).score).toBeGreaterThan(taskScore('Outlines every cell', 'nuclei', TASK_VOCABULARY).score);
  expect(taskScore('Outlines every cell', 'nuclei', TASK_VOCABULARY).matched).toBe(1);
  expect(taskScore('Outlines every cell', 'nuclei').matched).toBe(0);
});
