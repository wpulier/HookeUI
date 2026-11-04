import './investigation-launcher.css';
import {LIGHT_MY_CELLS_URL, type ResearchStarter} from './research-starter';
import onionDiagram from './investigation-launcher-onion.svg';
import rosetteDiagram from './investigation-launcher-rosette.svg';
import cellsDiagram from './investigation-launcher-cells.svg';

export type ProjectStart = 'files' | 'library' | 'feed' | 'example' | 'blank';
type ActionResult = Promise<unknown> | void;
export interface LauncherActions {
  start(kind: ProjectStart): ActionResult;
  openProject(): ActionResult;
  openRecent(id: string): ActionResult;
  dropFiles(files: File[]): ActionResult;
  previewResearch(url: string, signal?: AbortSignal): Promise<ResearchStarter>;
  createResearch(brief: ResearchStarter): ActionResult;
  cancel?(): void;
}
interface RecentProject {id: string; name: string; thumb?: string | null}

const investigations = [
  {id: 'light-my-cells-2', title: 'Light My Cells 2', kind: 'Research challenge', status: 'Start Project',
    question: 'Can we predict what would fluoresce from an unlabeled image of a cell?', artwork: cellsDiagram},
  {id: 'onion-root', title: 'Prepared onion root tip', kind: 'Teaching lab microscope', status: 'Coming soon',
    question: 'Which cells might be dividing?', artwork: onionDiagram},
  {id: 'rosette', title: 'Rosette Nebula', kind: 'Remote telescope', status: 'Coming soon',
    question: 'Which exposures would bring out the faint outer gas?', artwork: rosetteDiagram},
] as const;
const instruments = [
  {id: 'own-microscope', title: 'Connect your microscope', description: 'A supported motorized microscope could focus, move between fields, and capture images for this Project. The instrument and its calibration would determine the controls.'},
  {id: 'own-telescope', title: 'Connect your telescope', description: 'Plan an observation on your own telescope, review the target and exposure sequence, and retain the captured frames in this Project.'},
  {id: 'remote-microscope', title: 'Use a remote microscope', description: 'Choose a specimen at a participating lab and review the fields to capture during your reserved session.'},
  {id: 'remote-telescope', title: 'Use a remote telescope', description: 'Choose an observatory and a target, review the requested exposures, and bring the returned frames into this Project.'},
] as const;

function element<K extends keyof HTMLElementTagNameMap>(tag: K, text = '', className = '') {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}

function button(label: string, action: () => void, className = 'inv-button') {
  const control = element('button', label, className);
  control.type = 'button';
  control.addEventListener('click', action);
  return control;
}

function labeled(label: string, field: HTMLInputElement | HTMLTextAreaElement) {
  const wrapper = element('label', '', 'inv-field');
  wrapper.append(element('span', label), field);
  return wrapper;
}

/** One owner of the launcher's transient UI. Browsing, including research previews, does not start a Project. */
export class InvestigationLauncher {
  readonly root = element('section', '', 'investigation-launcher');
  readonly assistantHost = element('div', '', 'inv-assistant');
  private readonly recent = element('nav', '', 'inv-recent');
  private readonly back: HTMLButtonElement;
  private readonly url = element('input');
  private readonly status = element('p', '', 'inv-status');
  private readonly cards = new Map<string, HTMLButtonElement>();
  private recentRows: readonly RecentProject[] = [];
  private preview?: HTMLDialogElement;
  private reader?: AbortController;
  private workingBrief?: () => Omit<ResearchStarter, 'receipt' | 'status'> & {status: ResearchStarter['status'] | 'loading'};
  private opening = 0;
  private busy = false;

  private readonly actions: LauncherActions;

  constructor(actions: LauncherActions) {
    this.actions = actions;
    const header = element('header', '', 'inv-header');
    const identity = element('div');
    identity.append(element('div', 'New Project', 'inv-eyebrow'), element('h1', 'What would you like to investigate?'));
    const controls = element('div', '', 'inv-controls');
    this.back = button('Back', () => actions.cancel?.());
    this.setCanCancel(!!actions.cancel);
    controls.append(this.back, this.actionButton('open-project', 'Open Project…'));
    header.append(identity, controls);

    const practical = element('section', '', 'inv-practical');
    const work = element('div', '', 'inv-start');
    work.append(element('h2', 'Start with what you have'), element('p', 'Drop images, videos, or research files here to begin.', 'inv-secondary'));
    const ways = element('div', '', 'inv-ways');
    const quickStarts = [
      ['files', 'Open files'], ['library', 'Choose from Library'], ['feed', 'Start a live feed'],
      ['example', 'Try an example'], ['blank', 'Start blank'],
    ] as const;
    for (const [id, label] of quickStarts) ways.append(this.actionButton(id, label));
    work.append(ways);

    const form = element('form', '', 'inv-link');
    this.url.type = 'url';
    this.url.placeholder = 'https://…';
    this.url.required = true;
    this.url.id = 'inv-research-url';
    const label = element('label', 'Paste a challenge or research link');
    label.htmlFor = this.url.id;
    const row = element('div', '', 'inv-link-row');
    const read = element('button', 'Preview', 'inv-button'); read.type = 'submit';
    row.append(this.url, read);
    form.append(label, row, element('p', 'Review the brief before creating a Project.', 'inv-secondary'));
    form.addEventListener('submit', event => {event.preventDefault(); void this.readLink(this.url.value, read);});
    practical.append(work, form);

    const explore = element('section', '', 'inv-explore');
    explore.append(element('h2', 'Explore an investigation'));
    const collection = element('div', '', 'inv-collection');
    for (const item of investigations) {
      const card = this.actionButton(item.id, '', 'inv-card');
      const artwork = element('img', '', 'inv-art'); artwork.src = item.artwork; artwork.alt = '';
      const body = element('span', '', 'inv-card-body');
      const meta = element('span', '', 'inv-meta');
      meta.append(element('span', item.kind), element('span', item.status, item.status === 'Start Project' ? 'inv-live' : 'inv-soon'));
      body.append(meta, element('strong', item.title), element('span', item.question, 'inv-question'));
      card.append(artwork, body);
      collection.append(card);
    }
    explore.append(collection);

    const devices = element('section', '', 'inv-devices');
    devices.append(element('h2', 'Work with an instrument'));
    const choices = element('div', '', 'inv-instruments');
    for (const instrument of instruments) {
      const choice = this.actionButton(instrument.id, instrument.title);
      choice.append(element('span', 'Coming soon', 'inv-soon'));
      choices.append(choice);
    }
    devices.append(choices);
    this.recent.setAttribute('aria-label', 'Recent Projects');
    this.recent.hidden = true;
    this.status.setAttribute('role', 'status');
    this.root.append(header, this.recent, this.assistantHost, practical, this.status, explore, devices);
    this.root.addEventListener('dragover', event => {
      if (event.dataTransfer?.types.includes('Files')) {
        event.preventDefault(); event.stopPropagation(); event.dataTransfer.dropEffect = 'copy';
        this.root.classList.add('inv-dragging');
      }
    });
    this.root.addEventListener('dragleave', event => {
      if (!this.root.contains(event.relatedTarget as Node | null)) this.root.classList.remove('inv-dragging');
    });
    this.root.addEventListener('drop', event => {
      this.root.classList.remove('inv-dragging');
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (!files.length) return;
      event.preventDefault(); event.stopPropagation();
      if (!this.preview) void this.run(() => actions.dropFiles(files));
    });
  }

  private actionButton(id: string, title: string, className?: string) {
    const control = button(title, () => {void this.activate(id);}, className);
    control.dataset.starter = id;
    this.cards.set(id, control);
    return control;
  }

  setCanCancel(show: boolean) { this.back.hidden = !show; }

  setRecent(rows: readonly RecentProject[]) {
    const visible = rows.slice(0, 8);
    if (visible.length === this.recentRows.length && visible.every((row, index) =>
      row.id === this.recentRows[index].id && row.name === this.recentRows[index].name)) return;
    for (const row of this.recentRows) this.cards.delete('recent:' + row.id);
    this.recentRows = visible.map(({id, name}) => ({id, name}));
    this.recent.hidden = !visible.length;
    this.recent.replaceChildren(element('span', 'Recent Projects', 'inv-eyebrow'),
      ...visible.map(row => this.actionButton('recent:' + row.id, row.name, 'inv-recent-link')));
  }

  reset() {
    this.opening++;
    this.reader?.abort();
    this.preview?.close();
    this.workingBrief = undefined;
    this.busy = false;
    this.url.value = '';
    this.status.textContent = '';
    this.root.classList.remove('inv-dragging');
  }

  assistantChoices(): Record<string, string> {
    if (this.busy) return {};
    if (this.preview) {
      const choices: Record<string, string> = {'close-preview': 'Close preview'};
      if (this.preview.querySelector('.inv-preview-form')) choices['create-research'] = 'Create Project with the reviewed brief';
      return choices;
    }
    const choices: Record<string, string> = {'research-link': 'Paste a challenge or research link'};
    for (const [id, control] of this.cards) choices[id] = control.textContent ?? id;
    for (const item of investigations) choices[item.id] = [item.title, item.kind, item.status, item.question].join(' · ');
    for (const item of instruments) choices[item.id] = item.title + ' · Coming soon';
    return choices;
  }

  /** The assistant dispatches the same starter IDs as the buttons. */
  async activate(id: string) {
    if (this.busy) return;
    if (this.preview) {
      if (id === 'close-preview') this.preview.close();
      if (id === 'create-research') this.preview.querySelector<HTMLFormElement>('.inv-preview-form')?.requestSubmit();
      return;
    }
    if (id === 'research-link') { this.url.focus(); return; }
    if (id === 'light-my-cells-2') return this.readLink(LIGHT_MY_CELLS_URL, this.cards.get(id)!);
    const instrument = instruments.find(item => item.id === id);
    if (instrument) { this.showComingSoon(id, instrument.title, [instrument.description]); return; }
    if (id === 'onion-root') {
      this.showComingSoon(id, 'Prepared onion root tip', [
        'Review a scan of six adjacent fields on the lab’s mounted root tip. Each original image would keep its position, scale, and focus settings.',
        'Inspect the cells Hooke flags as possibly dividing. If the slide is still mounted and its position can be recovered, revisit one cell at several focus depths.',
      ]);
      return;
    }
    if (id === 'rosette') {
      this.showComingSoon(id, 'Rosette Nebula', [
        'Choose a telescope and frame the nebula, then review the proposed filters, exposure times, and observing window.',
        'Once the observatory completes the request, inspect its original exposures beside the combined image in your Project.',
      ]);
      return;
    }
    if (id === 'open-project') return this.run(() => this.actions.openProject());
    if (id.startsWith('recent:')) {
      const project = this.recentRows.find(row => 'recent:' + row.id === id);
      if (project) await this.run(() => this.actions.openRecent(project.id));
      return;
    }
    if (id === 'files' || id === 'feed' || id === 'library' || id === 'example' || id === 'blank') {
      await this.run(() => this.actions.start(id));
    }
  }

  researchContext() { return this.workingBrief?.() ?? null; }

  private async run(action: () => ActionResult) {
    if (this.busy) return;
    const opening = this.opening;
    this.busy = true;
    this.status.textContent = '';
    try { await action(); }
    catch (error) {
      if (this.opening === opening) this.status.textContent = error instanceof Error ? error.message : 'Could not open this Project. Try again.';
    } finally { if (this.opening === opening) this.busy = false; }
  }

  private dialog(title: string, label: string, opener: HTMLElement) {
    const dialog = element('dialog', '', 'inv-dialog');
    const heading = element('h2', title);
    heading.id = 'inv-preview-title';
    dialog.setAttribute('aria-labelledby', heading.id);
    const close = button('Close', () => dialog.close());
    const header = element('header');
    header.append(element('span', label, 'inv-eyebrow'), close);
    dialog.append(header, heading);
    this.preview = dialog;
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      if (!this.busy) dialog.close();
    });
    dialog.addEventListener('cancel', event => { if (this.busy) event.preventDefault(); });
    dialog.addEventListener('close', () => {
      if (dialog.contains(this.assistantHost)) this.recent.after(this.assistantHost);
      if (this.preview === dialog) {
        this.preview = undefined;
        this.workingBrief = undefined;
        this.reader?.abort();
      }
      dialog.remove();
      if (opener.isConnected && !this.root.closest('[hidden]')) opener.focus();
    }, {once: true});
    this.root.append(dialog);
    dialog.showModal();
    return dialog;
  }

  private showComingSoon(id: string, title: string, paragraphs: string[]) {
    const dialog = this.dialog(title, 'Coming soon', this.cards.get(id)!);
    dialog.append(...paragraphs.map(paragraph => element('p', paragraph)));
  }

  private async readLink(input: string, opener: HTMLElement) {
    if (this.busy || this.preview) return;
    let url: URL;
    try {
      url = new URL(input.trim());
      if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) throw Error();
    } catch {
      this.status.textContent = 'Enter a public http or https URL without sign-in details.';
      this.url.focus(); return;
    }
    const dialog = this.dialog('Research brief', 'New Project', opener);
    const body = element('div', '', 'inv-preview-body');
    const status = element('p', 'Reading the source…', 'inv-secondary');
    status.setAttribute('role', 'status');
    body.append(status);
    this.workingBrief = () => ({source: url.href, title: '', question: '', context: '', original: '', status: 'loading'});
    dialog.append(body, this.assistantHost);
    const reader = new AbortController(); this.reader = reader;
    try {
      const brief = await this.actions.previewResearch(url.href, reader.signal);
      if (reader.signal.aborted || !dialog.open) return;
      this.renderBrief(dialog, body, brief);
    } catch (error) {
      if (reader.signal.aborted || !dialog.open) return;
      status.textContent = error instanceof Error ? error.message : 'The research preview could not be loaded. Close and try again.';
    }
  }

  private renderBrief(dialog: HTMLDialogElement, body: HTMLElement, brief: ResearchStarter) {
    const form = element('form', '', 'inv-preview-form');
    const source = element('p', brief.source, 'inv-source');
    const title = element('input'); title.value = brief.title; title.required = true;
    const question = element('textarea'); question.value = brief.question; question.rows = 2;
    const context = element('textarea'); context.value = brief.context; context.rows = 3;
    context.placeholder = 'Add what you want to investigate.';
    this.workingBrief = () => ({
      status: brief.status, title: title.value, original: brief.original, source: brief.source,
      question: question.value, context: context.value,
    });
    form.append(source, labeled('Project title', title), labeled('Research question', question));
    if (brief.original) {
      const details = element('details', '', 'inv-original');
      details.append(element('summary', 'Original brief'), element('p', brief.original));
      form.append(details);
    }
    if (brief.status === 'unreadable') {
      form.append(element('p', 'The page could not be read. Paste a brief below, or create the Project with the link alone.', 'inv-secondary'));
    }
    form.append(labeled('Working brief', context));
    const errorMessage = element('p', '', 'inv-status'); errorMessage.setAttribute('role', 'status');
    const submit = element('button', 'Create Project', 'inv-button inv-primary'); submit.type = 'submit';
    form.append(errorMessage, submit);
    body.replaceChildren(form);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!title.value.trim() || this.preview !== dialog || this.busy) return;
      const opening = this.opening;
      this.busy = true; submit.disabled = true;
      const close = dialog.querySelector('header button') as HTMLButtonElement | null;
      if (close) close.disabled = true;
      try {
        await this.actions.createResearch({...brief, title: title.value.trim(), question: question.value.trim(), context: context.value.trim()});
        if (this.preview === dialog) dialog.close();
      } catch (error) {
        errorMessage.textContent = error instanceof Error ? error.message : 'The Project could not be created. Try again.';
      } finally {
        if (this.opening === opening) this.busy = false;
        submit.disabled = false;
        if (close) close.disabled = false;
      }
    });
  }
}
