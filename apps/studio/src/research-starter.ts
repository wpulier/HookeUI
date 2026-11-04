import './research-starter.css';

export const LIGHT_MY_CELLS_URL = 'https://lightmycells2.grand-challenge.org/';
const organizerUrl = 'https://france-bioimaging.org/services/data-services/challenge/light-my-cells-2-description/';

export interface ResearchStarter {
  source: string;
  title: string;
  question: string;
  original: string;
  receipt?: string;
  context: string;
  status: 'featured' | 'read' | 'unreadable';
}

function windowHeaders(): Record<string, string> {
  const token = (globalThis as {hookeStudioToken?: string}).hookeStudioToken;
  return token ? {'X-Hooke-Studio': token} : {};
}

export async function previewResearchStarter(url: string, signal?: AbortSignal): Promise<ResearchStarter> {
  const response = await fetch('/api/research-starter/preview', {
    method: 'POST', headers: {...windowHeaders(), 'Content-Type': 'application/json', 'X-MicroscopeMind': 'preview-research-link'},
    body: JSON.stringify({url}), signal,
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || 'The link could not be read. Try again.');
  return value;
}

function element<K extends keyof HTMLElementTagNameMap>(tag: K, words = ''): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.textContent = words;
  return node;
}

function link(words: string, url: string): HTMLAnchorElement {
  const anchor = element('a', words);
  // The folder may have been edited or moved from another machine.
  try {
    const parsed = new URL(url);
    if (['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password) anchor.href = parsed.href;
  } catch { /* A damaged citation stays readable. */ }
  anchor.target = '_blank';
  anchor.rel = 'noopener noreferrer';
  return anchor;
}

export class ResearchStarterDashboard {
  private readonly panel = element('section');
  private session = '';
  private load?: AbortController;
  private inputs = element('p');

  private readonly addFiles: () => void;

  constructor(host: HTMLElement, addFiles: () => void) {
    this.addFiles = addFiles;
    this.panel.className = 'research-starter-dashboard';
    this.panel.setAttribute('aria-label', 'Research brief');
    this.panel.hidden = true;
    host.prepend(this.panel);
  }

  clear(): void {
    this.load?.abort();
    this.session = '';
    this.panel.hidden = true;
    this.panel.replaceChildren();
  }

  update(session: string, materialCount: number | null, inputTitles: readonly string[] = []): void {
    this.inputs.textContent = materialCount === null ? 'Waiting for the Project input inventory.'
      : materialCount > 0 ? `${materialCount} ${materialCount === 1 ? 'item' : 'items'} in this Project. Open Inputs to inspect what you have added.`
      : 'No inputs added yet.';
    if (materialCount && inputTitles.length) {
      const visible = inputTitles.slice(0, 6);
      this.inputs.textContent = `Inputs (${materialCount}): ${visible.join(' · ')}${materialCount > visible.length ? ' …' : ''}`;
    }
    if (!session) { this.clear(); return; }
    if (this.session === session) return;
    this.clear();
    this.session = session;
    this.load = new AbortController();
    void this.read(session, this.load.signal);
  }

  private async read(session: string, signal: AbortSignal): Promise<void> {
    try {
      const response = await fetch(`/api/research-starter?session=${encodeURIComponent(session)}`, {signal, headers: windowHeaders(), cache: 'no-store'});
      if (!response.ok) throw new Error();
      const {session: owner, starter} = await response.json();
      if (signal.aborted || this.session !== session || owner !== session || !starter) return;
      this.show(starter);
    } catch {
      if (signal.aborted || this.session !== session) return;
      const retry = element('button', 'Retry');
      retry.type = 'button';
      retry.onclick = () => { retry.disabled = true; void this.read(session, signal); };
      this.panel.replaceChildren(element('p', 'Could not read this Project’s research brief.'), retry);
      this.panel.hidden = false;
    }
  }

  private show(brief: ResearchStarter): void {
    const title = element('h2', brief.title);
    const question = element('p', brief.question || 'No research question specified yet.');
    question.className = 'starter-question';
    const source = element('div');
    source.className = 'starter-sources';
    const featured = brief.status === 'featured' && brief.source === LIGHT_MY_CELLS_URL;
    if (featured) {
      source.append(element('span', 'France-BioImaging'), link('Organizer’s brief ↗', organizerUrl),
        link('Challenge database ↗', new URL('database/', LIGHT_MY_CELLS_URL).href));
    } else {
      source.append(element('span', 'Source'), link(brief.source, brief.source));
    }
    const actions = element('div');
    actions.className = 'starter-next-actions';
    if (featured) {
      actions.append(link('Get the training data ↗', new URL('participants/registration/create/', LIGHT_MY_CELLS_URL).href));
    }
    const add = element('button', featured ? 'Add image files' : 'Add files');
    add.type = 'button';
    add.onclick = this.addFiles;
    actions.append(add);
    const aim = element('section');
    aim.append(element('h3', featured ? 'Challenge tasks' : 'Original brief · source excerpt'),
      element('p', brief.original || 'No source excerpt available. Open the link to read the original.'));
    const interpretation = element('section');
    interpretation.append(element('h3', 'Your working brief'),
      element('p', brief.context || 'No working interpretation recorded yet.'));
    const compare = element('div');
    compare.className = 'starter-briefs';
    compare.append(aim, interpretation);
    this.panel.replaceChildren(element('span', 'RESEARCH PROJECT'), title, question, source, compare, this.inputs, actions);
    this.panel.hidden = false;
  }
}
