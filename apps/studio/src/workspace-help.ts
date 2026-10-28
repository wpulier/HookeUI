import { ROLE_SECTIONS, sectionWords } from './data-roles';

/** Help as a newcomer needs it (L7e, rows 6, 23, 24): find a tool by describing the task, the four steps of
 * ordinary work, where work belongs, and what to do when something goes wrong. Every button opens an existing
 * place (Tools, the Assistant, Inputs, Data, Activity, Models); none runs, retries or prepares anything. */
export interface HelpPlaces {
  /** Tools, its search filled with the researcher's description. */
  findTools(task: string): void;
  /** The Assistant, with the description as an unsent question. */
  askAgent(task: string): void;
  inputs(): void;
  data(): void;
  activity(): void;
  models(): void;
}

const node = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const made = document.createElement(tag); made.className = className; made.textContent = text; return made;
};
const action = (text: string, click: () => void, className = 'study-guide-action') => {
  const made = node('button', className, text); made.type = 'button'; made.addEventListener('click', click); return made;
};

/** Plain words a newcomer can try, each a task a ready or preparable tool does. */
export const TASK_EXAMPLES = ['segment the nuclei', 'measure cell sizes', 'track moving cells', 'make a clearer picture'] as const;

const SECTIONS = ROLE_SECTIONS.map(([, words]) => words);

export const WHERE_WORK_BELONGS: readonly (readonly [string, string])[] = [
  ['Project', 'A Project owns its inputs, results, notes and conversations. Open Projects are along the top; Projects ▾ and All Projects return to earlier ones. Every Project is saved on this Mac: All Projects says where, with Show in Finder.'],
  ['Tab', 'Tabs arrange windows inside one Project. Closing a Tab or a window keeps its results in Data; All Tabs (⌄ at the end of the Tab row) reopens it.'],
  ['Runs', 'A run keeps the input and Project it started with, even when you switch tabs or Projects. Its Project shows Running, then Done, and the result is placed in the tab it started from.'],
  ['Data', `Everything this Project holds, under ${SECTIONS.slice(0, -1).join(', ')} and ${SECTIONS[SECTIONS.length - 1]}. Opening a result shows the saved one; nothing is rerun.`],
  ['Library', 'Your recordings and pictures across every Project. Open in this Project makes one an input here without changing other Projects.'],
  ['Researchers', `Researchers work in the background, each as a Tab in the row. Closing its Tab never stops its work; what it writes lands in Data under ${sectionWords('writeup')}, what it measures under ${sectionWords('analysis')} and the pages it cites under ${sectionWords('source')}.`],
];

export const WHEN_SOMETHING_FAILS: readonly string[] = [
  'Your data and earlier results are kept. A step that did not work says what failed, and the ! beside an answer opens Activity at that step.',
  'Activity lists every request and run in this Project with what happened. A run’s Execution details can download a diagnostics file for whoever is helping you; it leaves out your words, notes, recordings, settings and keys.',
  'A tool that is not ready says what it needs in Models: a download, an API key or a setup step. Setup explains each step before anything runs.',
  'The example Project stays on this Mac, so the Assistant and cloud models are off there. Start a new Project (Projects ▾ › New Project) to ask the Assistant.',
  'When the monthly limit in Spending is reached, cloud models stop until you raise or remove it there. Tools on this Mac keep working.',
  'If the window says Reconnecting, your drafts are kept. Check Activity before sending a request again: it may still be running. Saved work reopens without rerunning after you quit and reopen Hooke.',
];

export function workspaceHelp(places: HelpPlaces): HTMLElement {
  const help = node('section', 'study-guide study-help');
  help.append(node('p', 'study-guide-intro', 'Hooke shows your microscope images, runs analysis tools on them, and keeps every result with the Project it came from.'));

  const find = node('form', 'study-help-find');
  find.setAttribute('aria-label', 'Find a tool for your task');
  const label = node('label', 'study-help-label', 'Describe what you want to do');
  const task = node('input'); task.type = 'search'; task.placeholder = `For example: ${TASK_EXAMPLES[0]}`;
  task.setAttribute('aria-label', 'Describe what you want to do');
  label.append(task);
  const said = () => task.value.trim();
  const toTools = () => places.findTools(said());
  find.addEventListener('submit', event => { event.preventDefault(); toTools(); });
  const buttons = node('div', 'study-help-buttons');
  buttons.append(action('Find tools', toTools, 'study-primary'), action('Ask the Assistant', () => places.askAgent(said())));
  const examples = node('div', 'study-help-examples'); examples.setAttribute('aria-label', 'Example tasks');
  for (const example of TASK_EXAMPLES) examples.append(action(example, () => { task.value = example; places.findTools(example); }, 'study-help-example'));
  find.append(node('h3', '', 'Find a tool for your task'), label, buttons,
    node('p', 'study-note', 'Use a word for the job, such as segment, measure or track. When Tools has nothing for it, it offers to search every model and package. The Assistant can also suggest a tool for your data.'), examples);
  help.append(find);

  const steps = node('article', 'study-guide-card');
  const list = node('ol', 'study-help-steps');
  for (const [title, words, button] of [
    ['Open your data', 'Import an image or video, or a scientific file such as OME-Zarr, or try the example.', action('Inputs', places.inputs)],
    ['Find a tool', 'Describe the task above, or open Tools and choose a category such as Segment & find.', null],
    ['Run it', 'Each tool shows the input it will use and where its result goes before you run it.', null],
    ['See the results', 'Data lists what this Project made. Open, measure or continue with another tool.', action('Open Data', places.data)],
  ] as const) {
    const item = node('li'); item.append(node('strong', '', title), node('span', '', ` ${words}`));
    if (button) item.append(button);
    list.append(item);
  }
  steps.append(node('h3', '', 'Start here'), list);
  help.append(steps);

  const where = node('article', 'study-guide-card');
  where.append(node('h3', '', 'Where your work belongs'));
  const terms = node('dl', 'study-help-terms');
  for (const [term, words] of WHERE_WORK_BELONGS) terms.append(node('dt', '', term), node('dd', '', words));
  where.append(terms);
  help.append(where);

  const fails = node('article', 'study-guide-card study-help-recovery');
  fails.append(node('h3', '', 'When something goes wrong'));
  const reasons = node('ul');
  for (const words of WHEN_SOMETHING_FAILS) reasons.append(node('li', '', words));
  const recover = node('div', 'study-help-buttons');
  recover.append(action('Open Activity', places.activity), action('Open Models', places.models));
  fails.append(reasons, recover);
  help.append(fails);
  return help;
}

/** What a failure notice offers beside its words: the place that explains what to do next. */
export function failureHelp(open: () => void): HTMLButtonElement {
  const button = action('Help', open, 'study-notice-help');
  button.setAttribute('aria-label', 'Help: what to do when something goes wrong');
  return button;
}
