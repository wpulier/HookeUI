/** The left rail's badges and marks (workspace-feel plan, step 4; the mock's rail()), from the app's own state only. A
 * count that is not known yet shows nothing, never a guess or a zero; the full words go in the button's description so
 * a screen reader hears "Models, 2 models need you", not a bare number.
 *
 * - Data: how many results are New (not yet seen in Data), as a quiet grey count.
 * - Researchers: a blue count of those that need you; otherwise the lit cell while any researcher works.
 * - Models: a blue count of the entries that need you.
 * - Tools: the lit cell while a run works, else a small dot while a run page is parked, only while Tools is not the place
 *   showing (left-side.ts `toolsMark`; workspace-study.ts puts it on with `applyRailMark`). */
export type RailBadgeTone = 'quiet' | 'needs';
export interface RailBadge { text: string; label: string; tone: RailBadgeTone }
export interface RailCounts {
  /** Results retained in this Project; null while they have not been read for it. */
  results: number | null;
  /** Of those, not yet seen in Data (new since you last looked). */
  resultsNew?: number;
  /** Researchers working now (this Project's and those still working in others). */
  researchers: number;
  /** Researchers waiting on the person (a question to answer). */
  researchersNeedYou?: number;
  /** Models the user's own choices use that need them (the header's setup chip: a key, a download, a licence). */
  modelsNeedYou: number;
}
export type RailBadgeKey = 'data' | 'researchers' | 'models';
/** A mark on a rail item that is not a count: a lit cell (something at work) or a dot (something kept for you). */
export type RailMark = { kind: 'cell'; label: string } | { kind: 'dot'; label: string } | null;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
const shown = (n: number) => (n > 99 ? '99+' : String(n));

export function railBadges(counts: RailCounts): Partial<Record<RailBadgeKey, RailBadge>> {
  const badges: Partial<Record<RailBadgeKey, RailBadge>> = {};
  if (counts.results !== null && counts.resultsNew) badges.data = { text: shown(counts.resultsNew), label: plural(counts.resultsNew, 'new result in this Project', 'new results in this Project'), tone: 'quiet' };
  if (counts.researchersNeedYou) badges.researchers = { text: shown(counts.researchersNeedYou), label: plural(counts.researchersNeedYou, 'researcher needs you', 'researchers need you'), tone: 'needs' };
  if (counts.modelsNeedYou > 0) badges.models = { text: shown(counts.modelsNeedYou), label: plural(counts.modelsNeedYou, 'model needs you', 'models need you'), tone: 'needs' };
  return badges;
}

/** The Researchers item's lit cell: while any researcher works and none needs you (the blue count says that). */
export function researchersMark(counts: Pick<RailCounts, 'researchers' | 'researchersNeedYou'>): RailMark {
  if (counts.researchersNeedYou || !counts.researchers) return null;
  return { kind: 'cell', label: plural(counts.researchers, 'researcher working', 'researchers working') };
}

/** Puts a badge on a rail button (or takes it off). The button's name stays just its name (what it is called, found
 * by voice control and tests alike); the count is its description, said after the name. */
export function applyRailBadge(button: HTMLElement, name: string, badge: RailBadge | undefined): void {
  let mark = button.querySelector<HTMLElement>('.study-rail-badge');
  if (button.getAttribute('aria-label') !== name) button.setAttribute('aria-label', name);
  if (!badge) { mark?.remove(); describe(button); return; }
  if (!mark) {
    mark = document.createElement('span');
    mark.className = 'study-rail-badge';
    mark.setAttribute('aria-hidden', 'true');
    button.append(mark);
  }
  if (mark.textContent !== badge.text) mark.textContent = badge.text;
  if (mark.dataset.tone !== badge.tone) mark.dataset.tone = badge.tone;
  mark.title = badge.label;
  describe(button);
}

/** Puts a lit cell or a dot on a rail button's corner (or takes it off), said in its description too. The cell is
 * made once and kept, so its breathing (CSS only) never restarts. */
export function applyRailMark(button: HTMLElement, mark: RailMark, cell: () => Element): void {
  let node = button.querySelector<HTMLElement>('.study-rail-mark');
  if (!mark) { node?.remove(); describe(button); return; }
  if (!node || node.dataset.kind !== mark.kind) {
    node?.remove();
    node = document.createElement('span');
    node.className = 'study-rail-mark';
    node.dataset.kind = mark.kind;
    node.setAttribute('aria-hidden', 'true');
    if (mark.kind === 'cell') node.append(cell());
    button.append(node);
  }
  node.title = mark.label;
  node.dataset.label = mark.label;
  describe(button);
}

/** The button's description: its badge's words, then its mark's. */
function describe(button: HTMLElement): void {
  const words = [button.querySelector<HTMLElement>('.study-rail-badge')?.title, button.querySelector<HTMLElement>('.study-rail-mark')?.dataset.label].filter(Boolean).join('. ');
  if (!words) { button.removeAttribute('aria-description'); return; }
  if (button.getAttribute('aria-description') !== words) button.setAttribute('aria-description', words);
}
