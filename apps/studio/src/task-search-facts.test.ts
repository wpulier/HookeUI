// Facts-only task search against the vocabulary option, on the repository's own declared facts (L7e, row 6).
// Actions rows: contract/capabilities.json title, what and say. Catalog rows: each catalog/models entry's name, what,
// description, category and search_aliases, plus the capabilities of the roles it implements (approximating
// catalog_view.abilities by its operations). The queries are the newcomer's own words from the walk of 2026-10-20.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import { TASK_VOCABULARY, findByTask, type TaskVocabulary } from './task-search';

const root = join(__dirname, '../../..');
interface Capability { id: string; actor: string; title: string; what: string; how?: { say?: string }; roles?: string[] }
const capabilities = (JSON.parse(readFileSync(join(root, 'contract/capabilities.json'), 'utf8')) as { capabilities: Capability[] }).capabilities;
const actions = capabilities.filter(c => c.actor === 'agent').map(c => ({ id: c.id, text: `${c.title} ${c.what} ${c.how?.say ?? ''}` }));

const field = (toml: string, key: string) => [...toml.matchAll(new RegExp(`^\\s*${key}\\s*=\\s*"([^"]*)"`, 'gm'))].map(m => m[1]);
const list = (toml: string, key: string) => [...toml.matchAll(new RegExp(`^\\s*${key}\\s*=\\s*\\[([^\\]]*)\\]`, 'gm'))]
  .flatMap(m => [...m[1].matchAll(/"([^"]*)"/g)].map(v => v[1]));
const catalog = readdirSync(join(root, 'catalog/models')).flatMap(id => {
  let toml: string; try { toml = readFileSync(join(root, 'catalog/models', id, 'model.toml'), 'utf8'); } catch { return []; }
  const operations = field(toml, 'operation');
  const abilities = capabilities.filter(c => c.roles?.some(role => operations.includes(role))).map(c => `${c.title} ${c.what} ${c.how?.say ?? ''}`);
  return [{ id, text: [field(toml, 'name')[0], field(toml, 'what')[0], ...field(toml, 'description'), ...field(toml, 'category'),
    ...list(toml, 'search_aliases'), ...operations, ...abilities].filter(Boolean).join(' ') }];
});

/** [query, ids any of which in the first three is a hit]. */
const ACTION_QUERIES: [string, string[]][] = [
  ['segmentation', ['cells']], ['segment the nuclei', ['cells']], ['count nuclei', ['cells', 'count', 'query-population']],
  ['find cell boundaries', ['cells']], ['outline every cell', ['cells']], ['separate touching cells', ['cells']],
  ['label every cell', ['cells']], ['mask', ['cells']], ['measure cell sizes', ['measure', 'query-population', 'show-measurements']],
  ['track moving cells', ['track-motion', 'follow-cells']],
];
const CATALOG_QUERIES: [string, string[]][] = [
  ['segmentation', ['cellpose-sam', 'instanseg-brightfield', 'micro-sam-lm']],
  ['segment the nuclei', ['instanseg-brightfield', 'cellpose-sam']], ['count nuclei', ['instanseg-brightfield', 'cellpose-sam']],
  ['outline every cell', ['cellpose-sam', 'micro-sam-lm']], ['find cell boundaries', ['cellpose-sam', 'micro-sam-lm', 'instanseg-brightfield']],
  ['label every cell', ['cellpose-sam', 'micro-sam-lm']], ['mask', ['cellpose-sam', 'instanseg-brightfield', 'micro-sam-lm']],
  ['cellpose', ['cellpose-sam']], ['track cells over time', ['trackastra']],
];

function measure(rows: { id: string; text: string }[], queries: [string, string[]][], vocabulary: TaskVocabulary) {
  return queries.map(([query, wanted]) => {
    const top = findByTask(rows, row => row.text, query, vocabulary).rows.slice(0, 3).map(row => row.id);
    return { query, top, hit: top.some(id => wanted.includes(id)) };
  });
}

test('facts-only matching finds segmentation from the declared facts; the vocabulary is measured beside it', () => {
  const report: string[] = [];
  for (const [name, rows, queries] of [['Actions', actions, ACTION_QUERIES], ['Catalog', catalog, CATALOG_QUERIES]] as const) {
    const facts = measure(rows, queries, []), words = measure(rows, queries, TASK_VOCABULARY);
    report.push(`${name}: facts-only ${facts.filter(r => r.hit).length}/${queries.length}, with vocabulary ${words.filter(r => r.hit).length}/${queries.length}`);
    facts.forEach((r, i) => report.push(`  ${r.hit ? '✓' : '✗'}/${words[i].hit ? '✓' : '✗'} ${r.query}: ${r.top.join(', ')} | ${words[i].top.join(', ')}`));
  }
  console.log(report.join('\n'));
  // Row 6's own words find segmentation from declared facts alone.
  const facts = Object.fromEntries(measure(actions, ACTION_QUERIES, []).map(r => [r.query, r.hit]));
  for (const query of ['segmentation', 'segment the nuclei', 'outline every cell', 'separate touching cells'])
    expect(facts[query], query).toBe(true);
  const entries = Object.fromEntries(measure(catalog, CATALOG_QUERIES, []).map(r => [r.query, r.hit]));
  for (const query of ['segmentation', 'segment the nuclei', 'outline every cell', 'cellpose']) expect(entries[query], query).toBe(true);
});
