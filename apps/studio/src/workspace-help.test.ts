// Synthetic DOM: what Help offers and where each control leads. Browser evidence is in the L7e PR.
import { afterEach, expect, test, vi } from 'vitest';
import { TestElement, fire } from './workspace-windows.test-support';
import { WHEN_SOMETHING_FAILS, WHERE_WORK_BELONGS, failureHelp, workspaceHelp, type HelpPlaces } from './workspace-help';
import { ROLE_SECTIONS, sectionWords } from './data-roles';

afterEach(() => vi.unstubAllGlobals());

function places() {
  return { findTools: vi.fn<(task: string) => void>(), askAgent: vi.fn<(task: string) => void>(), inputs: vi.fn<() => void>(),
    data: vi.fn<() => void>(), activity: vi.fn<() => void>(), models: vi.fn<() => void>() } satisfies HelpPlaces;
}
const all = (root: TestElement, tag: string): TestElement[] => root.querySelectorAll(tag);
const buttonNamed = (root: TestElement, text: string) => all(root, 'button').find(b => b.textContent === text)!;

test('a described task opens Tools with that description, or the Agent with it unsent', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const to = places();
  const help = workspaceHelp(to) as unknown as TestElement;
  const input = all(help, 'input')[0];
  input.value = '  segment the nuclei ';
  fire(buttonNamed(help, 'Find tools'), 'click');
  expect(to.findTools).toHaveBeenCalledWith('segment the nuclei');
  fire(buttonNamed(help, 'Ask the Assistant'), 'click');
  expect(to.askAgent).toHaveBeenCalledWith('segment the nuclei');
  fire(all(help, 'form')[0], 'submit');
  expect(to.findTools).toHaveBeenCalledTimes(2);
});

test('an example task fills the description and searches Tools for it', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const to = places();
  const help = workspaceHelp(to) as unknown as TestElement;
  fire(buttonNamed(help, 'measure cell sizes'), 'click');
  expect(all(help, 'input')[0].value).toBe('measure cell sizes');
  expect(to.findTools).toHaveBeenCalledWith('measure cell sizes');
});

test('help reaches every recovery place and nothing runs or retries work', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const to = places();
  const help = workspaceHelp(to) as unknown as TestElement;
  for (const [name, place] of [['Inputs', to.inputs], ['Open Data', to.data], ['Open Activity', to.activity], ['Open Models', to.models]] as const) {
    fire(buttonNamed(help, name), 'click'); expect(place).toHaveBeenCalledTimes(1);
  }
  expect(all(help, 'button').map(b => b.textContent)).not.toContain('Retry');
});

test('help says where work belongs and what is kept when something fails', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const text = (workspaceHelp(places()) as unknown as TestElement).textContent;
  for (const words of ['Start here', 'Where your work belongs', 'When something goes wrong', 'keeps the input and Project it started with',
    'Closing a Tab or a window keeps its results in Data', 'The example Project stays on this Mac']) expect(text).toContain(words);
  expect(WHEN_SOMETHING_FAILS.join(' ')).not.toMatch(/terminal|make |Traceback|log file/i);
});

test('a failure notice offers Help', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const open = vi.fn();
  const button = failureHelp(open) as unknown as TestElement;
  expect(button.textContent).toBe('Help');
  fire(button, 'click');
  expect(open).toHaveBeenCalledTimes(1);
});

// Ticket 67, fix 12: Help said a researcher's results land in Data "under Research"; Data's headings are its sections.
test('help names the sections Data really shows', () => {
  const said = new Map(WHERE_WORK_BELONGS);
  for (const [, section] of ROLE_SECTIONS) expect(said.get('Data')).toContain(section);
  expect(said.get('Researchers')).toContain('under Write-ups');
  expect([...said.values()].join(' ')).not.toMatch(/under Research\b/);
  expect(sectionWords('writeup')).toBe('Write-ups'); expect(sectionWords('acquired')).toBe('Pictures and recordings');
});

// Thesis question 12 (ticket 67, fix 10): Help says where Projects are saved and where Show in Finder is.
test('help says where Projects are saved', () => {
  const project = new Map(WHERE_WORK_BELONGS).get('Project') ?? '';
  expect(project).toContain('saved on this Mac');
  expect(project).toContain('Show in Finder');
});
