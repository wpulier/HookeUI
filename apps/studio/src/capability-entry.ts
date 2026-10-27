import { findByTask } from './task-search';
import type { ModelRow } from './protocol';
import { sizeWords } from './size-words';
import { licenceName } from './model-status';
import { tabMark, type TabMark } from './tab-marks';

export type CapabilityEntryRoute = 'find' | 'add' | 'create';
export type CapabilityJob = 'find' | 'name' | 'measure' | 'clear';
export interface CapabilityEntryIntent {
  route: CapabilityEntryRoute;
  query: string;
  source: string;
  description: string;
  job: CapabilityJob;
  method: 'builder' | 'coder';
  runner: 'python-env' | 'fiji';
  output: 'table.objects' | 'image.2d.rgb8' | 'points.2d' | 'text' | 'labels.instance.2d';
}
export type CapabilitySource = { kind: 'link'; url: string } | { kind: 'model-file' | 'capability-package'; file: File };
export type CapabilityInspection =
  | { kind: 'candidate'; candidate: string; name: string; says: string }
  | { kind: 'integration'; source: string; says: string }
  | { kind: 'unsupported'; says: string };
export type CapabilityCreation =
  | { method: 'builder'; description: string; source: string; job: CapabilityJob; picture: File; budgetUsd: 2; minutes: 15 }
  | { method: 'coder'; description: string; source: string; job: CapabilityJob; runner: 'python-env' | 'fiji'; input: 'image.2d.rgb8'; output: CapabilityEntryIntent['output'] };
export interface CapabilityEntryActions {
  select(model: string): Promise<void>;
  inspect(source: CapabilitySource, progress?: (fraction: number) => void): Promise<CapabilityInspection>;
  create(request: CapabilityCreation): Promise<void>;
  changed?(intent: CapabilityEntryIntent): void;
}
export interface CapabilityEntryOptions {
  /** Open on the three ways in, as plain choices (Add model or tool from a header), rather than on one route. */
  choose?: boolean;
  /** The older Advanced contribution controls (Models' own section): offered under the three choices. */
  advanced?: () => void;
}
export interface CapabilityEntry {
  element: HTMLElement;
  /** The link or file step's footer (Review), which a sheet may place beside its own Close, on one row. */
  footer: HTMLElement;
  update(rows: readonly ModelRow[]): void;
  setRoute(route: CapabilityEntryRoute): void;
  dispose(): void;
}

const JOBS: [CapabilityJob, string][] = [['find', 'Find objects'], ['name', 'Identify what is in a picture'], ['measure', 'Measure objects'], ['clear', 'Improve a picture']];
const OUTPUTS: [CapabilityEntryIntent['output'], string][] = [['table.objects', 'A measurements table'], ['image.2d.rgb8', 'A picture'], ['text', 'Written results'], ['labels.instance.2d', 'Object outlines']];
const INPUT_WORDS: Record<string, string> = { 'image.2d.rgb8': 'a 2D picture', 'volume.3d': 'a 3D volume' };
function node<K extends keyof HTMLElementTagNameMap>(tag: K, words = '', className = ''): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag); element.textContent = words; element.className = className; return element;
}

/** The three ways in, each said as what it is (the mock's addSheet start). */
const WAYS: readonly (readonly [CapabilityEntryRoute, string, string, TabMark])[] = [
  ['find', 'Find it in the catalog', 'Models and tools others have packaged', 'models'],
  ['add', 'I have a link or file', 'Paste a link, or choose a file on this Mac', 'link'],
  ['create', 'Have AI build it', 'Describe what it should do', 'spark'],
];

/** What an added entry takes, before anything runs (the mock's add-review): its size, where pictures go, its licence and
 * what it costs, from its catalog row; what the row does not say is said as not known, never guessed. */
export function reviewFacts(row: ModelRow): { mark: TabMark; words: string; fact: string }[] {
  const cloud = row.runs === 'cloud';
  const plan = row.setups?.find((s) => s.plan)?.plan;
  return [
    { fact: 'size', mark: 'download', words: row.size ? `${sizeWords(row.size)} on this Mac` : cloud ? 'Nothing to download' : 'Its size isn’t known yet' },
    { fact: 'where', mark: 'eyepiece', words: plan?.privacy || (cloud ? `Pictures go to ${row.where === 'in the cloud' ? 'the cloud' : row.where}` : 'Pictures stay on this Mac') },
    { fact: 'licence', mark: 'output', words: row.licence ? `Licence ${licenceName(row.licence, row.tier)}` : 'Its licence isn’t stated in the catalog' },
    { fact: 'cost', mark: 'cost', words: plan?.cost || (cloud ? 'Its cost isn’t recorded in the catalog' : 'Free to run on this Mac') },
  ];
}

/** Where a reviewed candidate came from, as a fact ("From bioimage.io · diplomatic-bug", "From github.com · lab/tool"):
 * the link's site and what it names there (a bioimage.io id, else its path), or the file's name. */
export function sourceWords(source: { kind: 'link'; url: string } | { kind: 'file'; name: string }): string {
  if (source.kind === 'file') return `From your file · ${source.name}`;
  try {
    const url = new URL(source.url);
    const site = url.hostname.replace(/^www\./, '');
    const id = new URLSearchParams(url.hash.replace(/^#\/?\??/, '')).get('id') ?? url.searchParams.get('id');
    const path = decodeURIComponent(url.pathname).replace(/^\/+|\/+$/g, '');
    const named = id || path;
    return named ? `From ${site} · ${named}` : `From ${site}`;
  } catch { return `From ${source.url}`; }
}

/** The words a catalog entry declares about itself, which the search reads. */
export function entryFacts(row: ModelRow): string {
  return [row.name, row.what, ...row.usedFor.map(use => use.title),
    ...(row.abilities ?? []).flatMap(ability => [ability.title, ability.what, ability.say]),
    row.newKind?.operation ?? '', ...(row.memberKinds ?? []).flatMap(member => [member.name, member.what, member.operation])].join(' ');
}

export function capabilityEntry(actions: CapabilityEntryActions, initial: Partial<CapabilityEntryIntent> = {}, options: CapabilityEntryOptions = {}): CapabilityEntry {
  const intent: CapabilityEntryIntent = { route: initial.route ?? 'find', query: initial.query ?? '', source: initial.source ?? '', description: initial.description ?? '', job: initial.job ?? 'find', method: initial.method ?? 'builder', runner: initial.runner ?? 'python-env', output: initial.output ?? 'table.objects' };
  const element = node('section', '', 'capability-entry');
  const content = node('div', '', 'capability-entry-content');
  const importActions = node('footer', '', 'capability-entry-actions');
  let importing = true;
  const navigation = node('nav'); navigation.setAttribute('aria-label', 'Add model or tool');
  const status = node('p', '', 'shelf-said'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.setAttribute('tabindex', '-1');
  const sections = { find: node('section'), add: node('section'), create: node('section') };
  const controls: (HTMLButtonElement | HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement)[] = [];
  const routeButtons = new Map<CapabilityEntryRoute, HTMLButtonElement>();
  let rows: readonly ModelRow[] = [], pending = false, disposed = false, choosing = !!options.choose;
  let reviewing: { candidate: string; list: HTMLElement; what: HTMLElement; source: string } | null = null;
  let selectedFile: File | null = null, picture: File | null = null;
  const changed = () => actions.changed?.({ ...intent });
  const showRoute = () => {
    // Choosing: the three ways as plain choices, nothing else; then the chosen way, the others a quiet switch above it.
    element.dataset.view = choosing ? 'choose' : intent.route === 'add' && !importing ? 'review' : intent.route;
    importActions.hidden = choosing || intent.route !== 'add' || !importing;
    // Reviewing a candidate: its own page, Change source the way back (the mock's add-review has no switch above it).
    navigation.hidden = !choosing && intent.route === 'add' && !importing;
    for (const route of ['find', 'add', 'create'] as const) {
      sections[route].hidden = choosing || route !== intent.route;
      routeButtons.get(route)?.setAttribute('aria-pressed', String(!choosing && route === intent.route));
    }
    if (advanced) advanced.hidden = !choosing;
  };
  const run = async (label: string, work: () => Promise<void>) => {
    if (pending || disposed) return;
    pending = true; status.textContent = label; status.dataset.state = 'working'; element.setAttribute('aria-busy', 'true');
    for (const control of controls) control.disabled = true;
    try { await work(); if (!disposed) status.textContent = ''; }
    catch (error) { if (!disposed) { status.dataset.state = 'failed'; status.textContent = `${error instanceof Error ? error.message : 'The app did not answer.'} Correct the problem and try again here.`; status.focus(); } }
    finally { pending = false; if (!disposed) { element.setAttribute('aria-busy', 'false'); for (const control of controls) control.disabled = false; syncProceed(); } }
  };
  // Review asks for something to review: a link or a file (set up below, once both exist).
  let syncProceed = () => {};
  const button = (words: string, click: () => void) => {
    const b = node('button', words, 'mm-control'); b.type = 'button'; b.disabled = pending; b.addEventListener('click', () => { if (!pending && !disposed) click(); }); controls.push(b); return b;
  };
  for (const [route, title, sub, mark] of WAYS) {
    const b = button('', () => { intent.route = route; choosing = false; status.textContent = ''; showRoute(); changed(); });
    b.classList.add('capability-way'); b.dataset.route = route;
    const words = node('span', '', 'capability-way-words'); words.append(node('span', title, 'capability-way-title'), node('span', sub, 'capability-way-sub'));
    const icon = node('span', '', 'capability-way-icon'); icon.append(tabMark(mark, 'capability-way-mark'));
    b.append(icon, words, tabMark('right', 'capability-way-chevron'));
    b.setAttribute('aria-label', title);
    routeButtons.set(route, b); navigation.append(b);
  }
  // The older Advanced contribution controls (Models' own section) stay one press away from the three choices.
  const advanced = options.advanced ? button('Advanced contribution controls', () => options.advanced!()) : null;
  advanced?.classList.add('capability-advanced');
  const field = (label: string, input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, target: HTMLElement) => {
    const wrapper = node('label', label, 'capability-entry-field'); input.className = 'models-own-input'; wrapper.append(input); target.append(wrapper); controls.push(input); return input;
  };
  const textInput = (label: string, key: 'query' | 'source' | 'description', target: HTMLElement, multiline = false) => {
    const input = multiline ? node('textarea') : node('input'); input.value = intent[key]; input.maxLength = key === 'description' ? 4000 : 2048;
    field(label, input, target); input.addEventListener('input', () => { intent[key] = input.value; changed(); }); return input;
  };
  const select = <T extends string>(label: string, choices: readonly (readonly [T, string])[], value: T, target: HTMLElement, choose: (value: T) => void) => {
    const input = node('select'); for (const [id, words] of choices) { const option = node('option', words); option.value = id; input.append(option); }
    input.value = value; field(label, input, target); input.addEventListener('change', () => { const chosen = choices.find(([id]) => id === input.value); if (chosen) { choose(chosen[0]); changed(); } }); return input;
  };
  const fileInput = (label: string, accept: string, target: HTMLElement, choose: (file: File | null) => void) => {
    const input = node('input'); input.type = 'file'; input.accept = accept; field(label, input, target);
    input.addEventListener('change', () => { choose(input.files?.[0] ?? null); }); return input;
  };

  sections.find.append(node('h3', 'What do you want to do?'), node('p', 'Search the available tools. Open one to see its requirements, choose settings and try it.'));
  const query = textInput('Search by job or tool name', 'query', sections.find);
  const results = node('div', '', 'capability-entry-results'); sections.find.append(results);
  const renderResults = () => {
    // Word by word over each entry's declared facts (task-search.ts): its name and what it does, the roles it fills, its
    // capabilities' own words (catalog_view.abilities), its kind's job; the closest first.
    const found = findByTask(rows, entryFacts, intent.query), matching = found.rows;
    // Keep existing buttons when facts have not changed so polling does not displace keyboard focus.
    const signature = JSON.stringify([found.partial, matching.map(row => [row.id, row.name, row.what, row.word, row.where, row.execution, row.newKind, row.memberKinds, row.steps])]);
    if (results.dataset.signature === signature) return;
    results.dataset.signature = signature;
    const focused = document.activeElement;
    const focusedModel = focused && results.contains(focused) ? (focused as HTMLElement).dataset.model : undefined;
    for (const old of results.querySelectorAll('button')) { const index = controls.indexOf(old); if (index >= 0) controls.splice(index, 1); }
    results.replaceChildren();
    if (!matching.length) results.append(node('p', rows.length ? 'No matching tool. Try another search, or import a model or tool.' : 'No tools are available yet. Wait for the catalog, or import a model or tool.'));
    // Closest guesses are said to be guesses: none of these does every word asked for.
    else if (found.partial) results.append(node('p', `Closest matches: none does all of “${intent.query.trim()}”. Try other words, or import a model or tool.`, 'capability-entry-partial'));
    for (const row of matching) {
      const card = node('article', '', 'capability-entry-result'); card.append(node('h4', row.name), node('p', row.what), node('p', `${row.word} · ${row.where}`));
      if (row.newKind) card.append(node('p', `Uses ${INPUT_WORDS[row.newKind.input] ?? row.newKind.input}. Produces ${row.newKind.gives}.`));
      for (const member of row.memberKinds ?? []) card.append(node('p', `${member.name}. Uses ${INPUT_WORDS[member.input] ?? member.input}. Produces ${member.gives}.`));
      if (row.execution) card.append(node('p', row.execution.why));
      for (const step of row.steps) card.append(node('p', step.says));
      const open = button(row.execution?.action === 'unsupported' || row.status === 'unavailable' ? 'Inspect limitations' : 'Open setup and try', () => { void run('Opening this tool…', () => actions.select(row.id)); });
      open.dataset.model = row.id; card.append(open); results.append(card);
      if (focusedModel === row.id) open.focus();
    }
    if (focusedModel && !matching.some(row => row.id === focusedModel)) query.focus();
  };
  query.addEventListener('input', renderResults);

  // The link or a file, nothing ahead of them (the sheet's header already says Add model or tool).
  const importForm = node('div', '', 'capability-import-form');
  const source = textInput('Model or GitHub link', 'source', importForm);
  source.placeholder = 'Paste a GitHub, model or package link'; source.setAttribute('inputmode', 'url'); source.spellcheck = false;
  const divider = node('p', 'or add a file', 'capability-import-divider');
  const drop = node('div', '', 'capability-import-drop');
  const pick = fileInput('Model or tool package', '', drop, file => chooseFile(file));
  pick.classList.add('capability-file-input'); pick.setAttribute('tabindex', '-1');
  const fileName = node('strong', 'Drop a model or tool package here');
  const fileHint = node('p', 'Model weights or a shared capability package', 'capability-entry-intro');
  const choose = button('Choose a file', () => pick.click());
  const remove = button('Remove file', () => chooseFile(null)); remove.hidden = true;
  drop.append(fileName, fileHint, choose, remove);
  const fileKind = node('select');
  for (const [value, label] of [['', 'Choose what this file contains'], ['model-file', 'Model weights'], ['capability-package', 'A shared capability package']]) {
    const option = node('option', label); option.value = value; fileKind.append(option);
  }
  field('What kind of file is this?', fileKind, importForm);
  const kindField = fileKind.parentElement!; kindField.hidden = true;
  const chooseFile = (file: File | null) => {
    if (pending || disposed) return;
    selectedFile = file; pick.value = ''; clearInspection(); status.textContent = ''; syncProceed();
    remove.hidden = !file;
    fileName.textContent = file?.name ?? 'Drop a model or tool package here';
    fileHint.textContent = file ? `${file.size < 1024 ? `${file.size} bytes` : file.size < 1024 * 1024 ? `${Math.ceil(file.size / 1024)} KB` : `${(file.size / (1024 * 1024)).toFixed(1)} MB`} · Checked before setup` : 'Model weights or a shared capability package';
    // This routes the upload only. The server validates its actual contents before creating a candidate.
    fileKind.value = file && /\.zip$/i.test(file.name) ? 'capability-package'
      : file && /\.(pt|pth|ckpt|safetensors)$/i.test(file.name) ? 'model-file' : '';
    kindField.hidden = !file || !!fileKind.value;
    if (file) { source.value = ''; intent.source = ''; changed(); }
    syncProceed();
  };
  drop.addEventListener('dragover', event => { event.preventDefault(); if (!pending) drop.dataset.over = 'true'; });
  drop.addEventListener('dragleave', () => { delete drop.dataset.over; });
  drop.addEventListener('drop', event => {
    event.preventDefault(); event.stopPropagation(); delete drop.dataset.over;
    if (pending || disposed) return;
    if (event.dataTransfer?.files.length !== 1) { status.textContent = 'Add one model or tool package at a time.'; return; }
    chooseFile(event.dataTransfer.files[0]);
  });
  const inspection = node('section', '', 'capability-inspection'); inspection.hidden = true; inspection.setAttribute('aria-live', 'polite');
  // The candidate's facts come with its catalog row, which may arrive after the reply: drawn again on each update.
  const drawReview = () => {
    if (!reviewing) return;
    const row = rows.find((r) => r.id === reviewing!.candidate);
    // What it does under its name, only once its catalog row says it (never the importer's own words).
    reviewing.what.textContent = row?.what ?? ''; reviewing.what.hidden = !row?.what;
    const facts = [{ fact: 'source', mark: 'link' as TabMark, words: reviewing.source }, ...(row ? reviewFacts(row) : [])];
    const signature = JSON.stringify([facts, !!row]);
    if (reviewing.list.dataset.signature === signature) return;
    reviewing.list.dataset.signature = signature;
    reviewing.list.replaceChildren(...facts.map((f) => {
      const item = node('li'); item.dataset.fact = f.fact; item.append(tabMark(f.mark, 'capability-review-icon'), node('span', f.words)); return item;
    }), ...(row ? [] : [node('li', 'Reading what it takes…')]));
  };
  const clearInspection = () => {
    reviewing = null;
    for (const old of inspection.querySelectorAll('button')) { const index = controls.indexOf(old); if (index >= 0) controls.splice(index, 1); }
    inspection.replaceChildren(); inspection.hidden = true; importForm.hidden = false; importing = true; showRoute();
  };
  const inspect = (request: CapabilitySource) => {
    void run(request.kind === 'link' ? 'Checking the source and its supported tools…' : 'Uploading and checking your file…', async () => {
      clearInspection();
      const response = await actions.inspect(request, fraction => {
        if (!disposed && pending && Number.isFinite(fraction)) status.textContent = `Uploading your file · ${Math.floor(Math.max(0, Math.min(1, fraction)) * 100)}%`;
      });
      if (disposed) return;
      importForm.hidden = true; importing = false; showRoute(); inspection.hidden = false; inspection.dataset.kind = response.kind;
      const from = sourceWords(request.kind === 'link' ? request : { kind: 'file', name: request.file.name });
      if (response.kind === 'candidate') {
        // The candidate first (the mock's add-review): its name, what it does, then what it takes, its source a fact
        // among them, before anything runs.
        const what = node('p', '', 'capability-review-what'); what.hidden = true;
        const list = node('ul', '', 'capability-review-facts'); list.setAttribute('aria-label', 'What it takes');
        inspection.append(node('h3', response.name), what, list);
        reviewing = { candidate: response.candidate, list, what, source: from }; drawReview();
        const prepare = button('Review setup', () => { void run('Opening this candidate…', () => actions.select(response.candidate)); });
        prepare.classList.add('mm-primary'); inspection.append(prepare);
      } else if (response.kind === 'integration') {
        inspection.append(node('h3', 'This needs an integration before it can run'), node('p', from, 'capability-source-identity'), node('p', response.says));
        for (const [method, title] of [['builder', 'Have AI build the integration here'], ['coder', 'Use my own coding tool']] as const) inspection.append(button(title, () => {
          intent.source = response.source; source.value = response.source; createSource.value = response.source; syncProceed();
          intent.method = method; methodInput.value = method; intent.route = 'create'; showMethod(); showRoute(); changed(); sections.create.querySelector('textarea')?.focus();
        }));
      } else inspection.append(node('h3', 'Couldn’t add this source'), node('p', from, 'capability-source-identity'), node('p', response.says));
      const back = button('Change source', () => { clearInspection(); source.focus(); }); back.classList.add('capability-secondary'); inspection.append(back);
      inspection.setAttribute('tabindex', '-1'); inspection.focus();
    });
  };
  const proceed = button('Review', () => {
    if (selectedFile) {
      if (fileKind.value !== 'model-file' && fileKind.value !== 'capability-package') { status.textContent = 'Choose whether this file contains model weights or a shared capability package.'; fileKind.focus(); return; }
      inspect({ kind: fileKind.value, file: selectedFile }); return;
    }
    const url = source.value.trim();
    try { const parsed = new URL(url); if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error(); }
    catch { status.textContent = 'Paste an http or https link without a password, or choose a file.'; source.focus(); return; }
    inspect({ kind: 'link', url });
  });
  proceed.classList.add('mm-primary', 'capability-import-continue');
  syncProceed = () => { proceed.disabled = pending || disposed || !(source.value.trim() || selectedFile); };
  source.addEventListener('keydown', event => { if ((event as KeyboardEvent).key === 'Enter') { event.preventDefault(); if (!proceed.disabled) proceed.click(); } });
  source.addEventListener('input', () => { if (selectedFile) chooseFile(null); syncProceed(); });
  importForm.append(divider, drop, kindField);
  importActions.append(proceed);
  sections.add.append(importForm, inspection);

  sections.create.append(node('h3', 'What should your tool do?'));
  textInput('Describe the job and the result you need', 'description', sections.create, true);
  const createSource = textInput('Existing model or GitHub link, optional', 'source', sections.create);
  source.addEventListener('input', () => { createSource.value = source.value; clearInspection(); });
  createSource.addEventListener('input', () => { source.value = createSource.value; clearInspection(); syncProceed(); });
  const methodInput = select('Where to create it', [['builder', 'Build with AI here'], ['coder', 'Use my own coding tool']] as const, intent.method, sections.create, value => { intent.method = value; showMethod(); });
  const builder = node('section'), coder = node('section');
  select('Job', JOBS, intent.job, builder, value => { intent.job = value; });
  builder.append(node('p', 'The AI builder can spend up to $2 and work for up to 15 minutes. It starts only when you press Build with AI. You review the result before enabling it.'));
  fileInput('Test picture for the AI builder', '.png,.jpg,.jpeg,image/png,image/jpeg', builder, file => { picture = file; });
  builder.append(node('p', 'Choose a PNG or JPEG picture showing what this tool should work on. It stays separate from your model file and capability package.'));
  select('Tool runtime', [['python-env', 'Python'], ['fiji', 'Fiji script']] as const, intent.runner, coder, value => { intent.runner = value; });
  select('Result to produce', OUTPUTS, intent.output, coder, value => { intent.output = value; });
  coder.append(node('p', 'Fiji scripts can return a picture or a measurements table. Python supports all the listed results.'), node('p', 'The app prepares a private workspace and instructions for your coding tool. Return its completed capability package here for checks and review. This path supports a 2D picture as input.'));
  const validateDescription = () => {
    if (intent.description.trim()) return true;
    status.textContent = 'Describe what the tool should do before creating it.'; return false;
  };
  builder.append(button('Build with AI, up to $2', () => {
    if (!validateDescription()) return;
    if (!picture || !/\.(png|jpe?g)$/i.test(picture.name) || picture.type && !['image/png', 'image/jpeg'].includes(picture.type)) { status.textContent = 'Choose a PNG or JPEG test picture before building.'; return; }
    const request: CapabilityCreation = { method: 'builder', description: intent.description.trim(), source: intent.source.trim(), job: intent.job, picture, budgetUsd: 2, minutes: 15 };
    void run('Starting your AI build…', () => actions.create(request));
  }));
  coder.append(button('Prepare instructions for my coding tool', () => {
    if (!validateDescription()) return;
    if (intent.output === 'points.2d') { status.textContent = 'Point results cannot yet complete the required comparison. Choose a picture, measurements table, outlines or written results before creating this tool.'; return; }
    if (intent.runner === 'fiji' && !['image.2d.rgb8', 'table.objects'].includes(intent.output)) { status.textContent = 'For a Fiji script, choose a picture or a measurements table as the result. Choose Python for the other result types.'; return; }
    const request: CapabilityCreation = { method: 'coder', description: intent.description.trim(), source: intent.source.trim(), job: intent.job, runner: intent.runner, input: 'image.2d.rgb8', output: intent.output };
    void run('Preparing your private workspace…', () => actions.create(request));
  }));
  const showMethod = () => { builder.hidden = intent.method !== 'builder'; coder.hidden = intent.method !== 'coder'; };
  sections.create.append(builder, coder);
  for (const action of [builder, coder]) action.querySelector('button')?.classList.add('mm-primary');
  content.append(navigation, ...(advanced ? [advanced] : []), sections.find, sections.add, sections.create, status);
  element.append(content, importActions); showRoute(); showMethod(); renderResults(); syncProceed();
  return {
    element,
    footer: importActions,
    update(next) { if (!disposed) { rows = next; renderResults(); drawReview(); } },
    setRoute(route) { if (!disposed && !pending) { intent.route = route; choosing = false; showRoute(); changed(); } },
    dispose() { disposed = true; selectedFile = picture = null; for (const input of element.querySelectorAll('input')) if (input.type === 'file') input.value = ''; },
  };
}
