/** Finding a tool by describing the task (F031, row 6): word by word, not one literal phrase.
 *
 * Discovery only. It narrows and orders lists the researcher reads; it never chooses, runs or prepares a tool,
 * and a model's own judgment is untouched. By default it matches only each tool's declared facts (its name, what it
 * does, its capabilities' words, its `search_aliases` and category), word by word and by stem, so "segmentation"
 * finds "A cell finder segments every cell". Task words a tool should be found by belong in its catalog entry.
 * `TASK_VOCABULARY` is an option measured against facts-only matching (task-search-facts.test.ts), not the default.
 * When nothing matches every word, the closest partial matches are offered instead of an empty list. */

/** Words with no task meaning in a description such as "I want to count the nuclei in my image". */
const STOP = new Set(['a', 'an', 'the', 'my', 'our', 'this', 'that', 'these', 'those', 'in', 'on', 'of', 'for', 'to',
  'and', 'or', 'with', 'each', 'every', 'all', 'i', 'we', 'want', 'need', 'would', 'like', 'how', 'do', 'can', 'please',
  'some', 'me', 'it', 'them', 'is', 'are', 'be', 'from', 'into', 'by', 'using', 'use', 'tool', 'tools', 'method', 'image',
  'images', 'picture', 'pictures', 'photo', 'data', 'here', 'which', 'should', 'help', 'way', 'make', 'get', 'see', 'find', 'show']);

/** An option, off by default: plain task words and the words researchers use for them, one entry per task, every
 * word a stem. Measured against facts-only matching in task-search-facts.test.ts; ship it only where it beats that. */
export type TaskVocabulary = readonly (readonly string[])[];
export const TASK_VOCABULARY: TaskVocabulary = [
  ['outline', 'segment', 'mask', 'label', 'boundary', 'border', 'contour', 'delineate', 'instance', 'separate', 'split', 'region'],
  ['count', 'many', 'quantify', 'tally'],
  ['cell', 'nucleus', 'nuclei'],
  ['measure', 'size', 'area', 'intensity', 'brightness', 'length', 'diameter', 'shape', 'table', 'big'],
  ['track', 'motion', 'move', 'follow', 'trajectory', 'speed', 'fast', 'where'],
  ['clear', 'denoise', 'noise', 'sharpen', 'enhance', 'blur', 'smooth', 'background', 'enlarge', 'upscale', 'filter'],
  ['identify', 'name', 'classify', 'recognise', 'recognize', 'what'],
  ['find', 'detect', 'locate', 'spot'],
  ['calibrate', 'scale', 'micron', 'objective', 'magnification'],
  ['snapshot', 'capture', 'export', 'save', 'record'],
];

const IRREGULAR: Record<string, string> = { nuclei: 'nucleus', mitochondria: 'mitochondrion', bacteria: 'bacterium',
  vertices: 'vertex', indices: 'index', matrices: 'matrix', analyses: 'analysis' };

/** A word's stem: enough to make "segmentation", "segmenting" and "segments" one word. */
export function stem(word: string): string {
  let w = word.toLowerCase();
  if (IRREGULAR[w]) return IRREGULAR[w];
  // "measurement" → "measure", never "segment" → "seg": what remains must still be a word.
  for (const [ending, replacement, rest] of [['ations', '', 4], ['ation', '', 4], ['ments', '', 5], ['ment', '', 5],
    ['ies', 'y', 3], ['ing', '', 3], ['ed', '', 3], ['es', '', 3], ['s', '', 3]] as const) {
    if (w.endsWith(ending) && w.length - ending.length >= rest && !(ending === 's' && w.endsWith('ss'))) {
      w = w.slice(0, -ending.length) + replacement;
      // "labelled" → "labell" → "label"; "mapping" → "mapp" → "map".
      if ((ending === 'ed' || ending === 'ing') && /([^aeiou])\1$/.test(w)) w = w.slice(0, -1);
      // "outlines" → "outlin"? keep the e: "es" only drops after s, x, z, ch and sh.
      if (ending === 'es' && !/(s|x|z|ch|sh)$/.test(w)) w += 'e';
      break;
    }
  }
  return w;
}

const words = (text: string): string[] => text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);

/** A task word's alternatives: its own stem and, with a vocabulary, every word of each task it belongs to (or starts,
 * while typing). */
function alternatives(word: string, vocabulary: TaskVocabulary): string[] {
  const own = stem(word);
  const found = new Set([own]);
  for (const task of vocabulary) {
    if (task.some(member => member === own || (own.length >= 4 && member.startsWith(own)))) task.forEach(member => found.add(member));
  }
  return [...found];
}

/** The description's task words, each a group of alternatives. */
export function taskTerms(query: string, vocabulary: TaskVocabulary = []): string[][] {
  const all = words(query);
  const meaningful = all.filter(word => !STOP.has(word));
  return (meaningful.length ? meaningful : all).map(word => alternatives(word, vocabulary));
}

function textStems(text: string): string[] { return words(text).map(stem); }

/** One stem for two spellings the stemmer leaves apart ("measur" from "measured", "measure"). */
const near = (a: string, b: string) => a === b ||
  (Math.min(a.length, b.length) >= 5 && Math.abs(a.length - b.length) <= 2 && (a.startsWith(b) || b.startsWith(a)));

/** How well one text answers the description: each task word found counts, its own stem more than a synonym. */
export function taskScore(text: string, query: string, vocabulary: TaskVocabulary = []): { matched: number; terms: number; score: number } {
  const terms = taskTerms(query, vocabulary), stems = textStems(text);
  let matched = 0, score = 0;
  for (const group of terms) {
    const own = group[0];
    // A word still being typed ("segm") finds what it starts.
    const direct = stems.some(token => near(token, own) || (own.length >= 3 && token.startsWith(own)));
    const related = direct || stems.some(token => group.some(alt => near(token, alt) || (alt.length >= 4 && token.startsWith(alt))));
    if (related) { matched++; score += direct ? 2 : 1; }
  }
  return { matched, terms: terms.length, score };
}

/** The rows a description finds, best first. Every task word when any row has them all, else the rows with the most;
 * an empty description keeps every row in its order. `partial` says the second case, so a list can say so. */
export function findByTask<T>(rows: readonly T[], text: (row: T) => string, query: string,
  vocabulary: TaskVocabulary = []): { rows: T[]; partial: boolean } {
  // No words at all ("", "  ", "?!"): nothing to narrow by, so every row stays, in order.
  if (!taskTerms(query).length) return { rows: [...rows], partial: false };
  const scored = rows.map((row, index) => ({ row, index, ...taskScore(text(row), query, vocabulary) }));
  const best = Math.max(0, ...scored.map(item => item.matched));
  if (best === 0) return { rows: [], partial: false };
  const all = scored[0]?.terms ?? 0;
  const kept = scored.filter(item => item.matched === best).sort((a, b) => b.score - a.score || a.index - b.index);
  return { rows: kept.map(item => item.row), partial: best < all };
}
