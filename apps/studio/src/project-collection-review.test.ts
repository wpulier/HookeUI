// Synthetic presentation checks; these do not qualify complete dependency collection.
import {afterEach, expect, test, vi} from 'vitest';
import {collectionReview, type CollectionReview} from './project-collection-review';
import {syntheticDOM} from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());
const ref = {id: 'p' + 'a'.repeat(32), session: '2026-10-30', revision: 1, digest: 'b'.repeat(64)};
function render(value: CollectionReview) {
  vi.stubGlobal('document', syntheticDOM().document);
  return collectionReview(value);
}
test('missing reviews never become a clean dependency assessment', () => {
  const view = render({});
  expect(view.textContent).toContain('Artifact relationship review is unavailable');
  expect(view.textContent).toContain('Historical media review is unavailable');
  expect(view.textContent).toContain('Keep the original Project');
  expect(view.textContent).not.toContain('No unresolved');
});
test('resolved declared links do not certify unassessed recording dependencies', () => {
  const view = render({dependencyReview: {documents: 4, revisions: 9, referencesChecked: 7, issues: []}, sourceReview: {sources: []}});
  expect(view.textContent).toContain('9 accepted revisions inspected');
  expect(view.textContent).toContain('No unresolved links among 7 declared artifact references');
  expect(view.textContent).toContain('Shared recordings and other external dependencies are not fully checked');
});
test('external owners and damaged media remain explicit and untrusted fields render literally', () => {
  const view = render({dependencyReview: {documents: 1, revisions: 1, referencesChecked: 1,
    issues: [{source: ref, field: '<img src=x onerror=alert(1)>', status: 'external-project-inclusion-required', reference: {...ref,session:'2026-09-01'}}]},
    sourceReview: {sources: [{asset:'a1',status:'retained-choices', choices:[{reference:ref,status:'retained-binding'},{reference:ref,status:'invalid-retained-binding'}]}]}});
  expect(view.textContent).toContain('1 historical media sources still need inclusion review');
  expect(view.textContent).toContain('Another Project is required');
  expect(view.textContent).toContain('2026-09-01');
  expect(view.textContent).toContain('1 invalid bindings');
  expect(view.textContent).toContain('<img src=x onerror=alert(1)>');
  expect(view.querySelector('img')).toBeNull();
});
test('a long review exposes every issue through bounded pages', () => {
  const view = render({dependencyReview: {documents: 1,revisions: 1,referencesChecked: 23,
    issues: Array.from({length:23},(_,i)=>({source:ref,field:`field-${i}`,status:'missing-or-unreadable',reference:ref}))}});
  expect(view.querySelectorAll('li')).toHaveLength(20);
  expect(view.textContent).toContain('1–20 of 23');
  const [previous,next] = Array.from(view.querySelectorAll('button'));
  next.dispatchEvent(new Event('click'));
  expect(view.querySelectorAll('li')).toHaveLength(3);
  expect(view.textContent).toContain('21–23 of 23');
  expect(view.textContent).toContain('field-22');
  expect(next.disabled).toBe(true);
  previous.dispatchEvent(new Event('click'));
  expect(view.querySelectorAll('li')).toHaveLength(20);
  expect(previous.disabled).toBe(true);
});
