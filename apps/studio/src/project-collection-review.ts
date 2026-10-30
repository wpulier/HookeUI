/** Present the server's bounded dependency review; never infer collection completeness. */
interface Reference {id: string; session: string; revision: number; digest: string}
export interface CollectionReview {
  dependencyReview?: {
    documents: number; revisions: number; referencesChecked: number;
    issues: {source: Reference; field: string; status: string; reference: Partial<Reference> | null}[];
  };
  sourceReview?: {
    sources: {asset: string; status: 'retained-choices' | 'inclusion-required'; choices: {
      reference: Reference; status: 'retained-binding' | 'invalid-retained-binding';
    }[]}[];
  };
}
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', cls = '') => {
  const element = document.createElement(tag); element.textContent = text; element.className = cls; return element;
};
const issueWords: Record<string, string> = {
  'unsupported-reference': 'Reference needs review', 'external-project-inclusion-required': 'Another Project is required',
  'missing-or-unreadable': 'Referenced evidence is unavailable', 'digest-mismatch': 'Evidence identity does not match',
  'unpinned-execution-locator': 'Execution link does not pin an evidence revision',
};
const referenceWords = (ref: Partial<Reference>) => `${ref.session ?? 'Owner unspecified'} · ${ref.id ?? 'Reference unavailable'}${ref.revision === undefined ? '' : ` · revision ${ref.revision}`}${ref.digest ? ` · ${ref.digest}` : ''}`;

export function collectionReview(plan: CollectionReview): HTMLElement {
  const root = node('section', '', 'project-collection-review');
  const records = plan.dependencyReview, media = plan.sourceReview;
  const issues = records?.issues ?? [], sources = media?.sources ?? [];
  const needsReview = sources.filter(source => source.status === 'inclusion-required'
    || source.choices.some(choice => choice.status === 'invalid-retained-binding'));
  root.append(node('strong', 'Before you rely on this copy'));
  if (records) {
    root.append(node('p', `${records.documents.toLocaleString()} documents · ${records.revisions.toLocaleString()} accepted revisions inspected`));
    root.append(node('p', issues.length ? `${issues.length.toLocaleString()} artifact relationships need review.`
      : `No unresolved links among ${records.referencesChecked.toLocaleString()} declared artifact references.`));
  } else root.append(node('p', 'Artifact relationship review is unavailable.'));
  if (media) root.append(node('p', needsReview.length
    ? `${needsReview.length.toLocaleString()} historical media sources still need inclusion review.`
    : sources.length ? `${sources.length.toLocaleString()} historical media sources have retained choices.`
      : 'No historical media sources were found in the reviewed source events.'));
  else root.append(node('p', 'Historical media review is unavailable.'));
  root.append(node('p', 'Shared recordings and other external dependencies are not fully checked. Keep the original Project.', 'project-collection-limit'));
  if (!issues.length && !sources.length) return root;
  const details = node('details'), summary = node('summary', 'Inspect dependency details');
  const rows = [
    ...issues.map(issue => ({title: issueWords[issue.status] ?? 'Relationship needs review',
      detail: `${referenceWords(issue.source)} · ${issue.field}`,
      reference: issue.reference ? referenceWords(issue.reference) : 'Reference unavailable'})),
    ...sources.flatMap(source => [{title: source.status === 'inclusion-required' ? 'Media inclusion required' : 'Retained media choices',
      detail: `Historical source ${source.asset} · ${source.choices.filter(choice => choice.status === 'retained-binding').length} retained choices · ${source.choices.filter(choice => choice.status === 'invalid-retained-binding').length} invalid bindings`,
      reference: 'A retained choice does not establish complete recording or metadata history.'},
      ...source.choices.map(choice => ({title: choice.status === 'retained-binding' ? 'Valid retained binding' : 'Invalid retained binding',
        detail: `Historical source ${source.asset}`, reference: referenceWords(choice.reference)}))]),
  ];
  const list = node('ul'), navigation = node('div', '', 'project-collection-pages');
  const previous = node('button', 'Previous'), next = node('button', 'Next'), page = node('span');
  previous.type = next.type = 'button'; previous.setAttribute('aria-label', 'Previous dependency page'); next.setAttribute('aria-label', 'Next dependency page');
  page.setAttribute('aria-live', 'polite');
  let offset = 0;
  const render = () => {
    list.replaceChildren(...rows.slice(offset, offset + 20).map(row => {
      const item = node('li'); item.append(node('strong', row.title), node('p', row.detail), node('p', row.reference)); return item;
    }));
    page.textContent = `${offset + 1}–${Math.min(offset + 20, rows.length)} of ${rows.length}`;
    previous.disabled = offset === 0; next.disabled = offset + 20 >= rows.length;
  };
  previous.addEventListener('click', () => { offset = Math.max(0, offset - 20); render(); });
  next.addEventListener('click', () => { if (offset + 20 < rows.length) offset += 20; render(); });
  navigation.append(previous, page, next); navigation.hidden = rows.length <= 20;
  details.append(summary, list, navigation); root.append(details); render(); return root;
}
