// The empty stage (docs/design/2026-10-12-workspace-and-assets.md; Will's design of 2026-10-12): a new or empty session
// is the workspace with nothing on its stage, and one box asks what it will look at, under the session's name. It
// replaces both the new session's home and the no-camera box, so the two never follow one another.
//
// - The live camera: its state in a line, Use live camera (a new session's choice), and while it has no picture, Connect a
//   camera (the camera page, where it is set up), Reconnect and, quietly, Just use pictures and videos (No camera). With
//   No camera chosen (Will, 2026-10-15) it only says so, with Choose a camera (the header's camera menu): no Connect,
//   Reconnect or Use live camera, and nothing about a camera to fix.
// - Add a recording or picture: dropped anywhere on the stage, or chosen from this Mac. It joins your assets and this
//   session, and goes on stage.
// - From your assets: the newest few, and All assets (the library's page).
// - Troubleshoot, a quiet line: the camera's checklist.
//
// It shows in two cases. `choose`: a new session opened, and the app looks at nothing until something is chosen (main.ts
// says `view {held: true}`). `camera`: the live camera is on stage and has no picture, so the box is what the stage shows
// (the app looks as usual: nothing arrives to look at). It only asks: what a choice does is main.ts's, through
// HomeActions.
import type { AssetMeta } from './protocol';
import { libraryUrl, readLibrary } from './asset-library';
import { assetThumb, sourceMeta, type LiveState } from './sources';
import { starterOffer } from './starter-models';

export interface HomeActions {
  /** Start or resume the ordinary local example session. */
  example(): void;
  /** The live camera on stage. */
  live(): void;
  /** One of the user's assets on stage (which makes it the session's). */
  open(asset: AssetMeta): void;
  /** Files from this Mac (chosen, or dropped on the stage): uploaded, then opened. */
  addFiles(files: File[]): void;
  /** All assets: the library's page. */
  library(): void;
  /** All projects: open an existing Project instead of filling this one. */
  projects?(): void;
  /** Start with a question instead of a specimen: a researcher on it, through the Assistant (its own words, admitted
   * there as any request to start one). Nothing needs to be on the Stage. */
  research?(question: string): boolean | void;
  /** Reconnect the camera: what happened, in words. */
  reconnect(): Promise<string>;
  /** Troubleshoot: the camera's checklist (also Choose a camera: the menu has the source switch). */
  troubleshoot(): void;
  /** No camera: pictures and videos only, nothing connects. */
  noCamera(): void;
  /** The camera page, where the camera is set up. */
  cameraPage(): string;
}

/** Why it shows: a new session choosing, or the live camera on stage with no picture. */
export type HomeMode = 'choose' | 'camera';

/** How many of the newest assets it offers. */
export const RECENT = 6;

function node<K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text) el.textContent = text;
  return el;
}

function icon(name: string, className = 'home-icon'): Element {
  const svg = document.createElementNS?.('http://www.w3.org/2000/svg', 'svg') ?? document.createElement('svg');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('class', className);
  const use = document.createElementNS?.('http://www.w3.org/2000/svg', 'use') ?? document.createElement('use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

/** What the camera's line says when it has no picture: the checklist's headline ("Blackmagic Camera isn't open"), else
 * the bridge's; "Not live: no picture is arriving" reads "No picture is arriving" (the line already says it is the
 * live camera). */
export function cameraLine(words: string): string {
  const said = words.replace(/^not live:\s*/i, '').replace(/\.$/, '').trim();
  return said ? said[0].toUpperCase() + said.slice(1) : 'Not connected';
}

export class SessionHome {
  onVisibilityChange?: () => void;
  readonly root: HTMLElement;
  readonly title = node('h2', 'home-title', 'Untitled project');
  readonly liveButton = node('button', 'mm-primary home-go-live', 'Use live camera');
  readonly connect = node('a', 'home-connect', 'Connect a camera');
  readonly exampleButton = node('button', 'home-example-button', 'Try the guided example');
  readonly reconnectButton = node('button', 'home-reconnect', 'Reconnect');
  readonly chooseCamera = node('button', 'mm-primary home-choose-camera', 'Choose a camera');
  readonly filesOnly = node('button', 'home-files-only', 'Just use pictures and videos');
  readonly add = node('button', 'home-add');
  readonly fileInput = node('input', 'home-file');
  readonly libraryButton = node('button', 'home-library', 'Library ›');
  private readonly cameraWords = node('span', 'home-camera-words');
  private readonly cameraHelp = node('span', 'home-camera-help');
  private readonly camera = node('div', 'home-camera');
  private readonly recent = node('section', 'home-recent');
  private readonly recentList = node('ol', 'home-recent-list');
  private readonly status = node('span', 'home-status');
  private readonly actions: HomeActions;
  private readonly fetcher: typeof fetch | undefined;
  private readonly clock: () => number;
  private live: LiveState = { tone: 'off', words: '', help: '' };
  /** What is wrong with the camera, while it is on stage with no picture (the checklist's first failing step). */
  private problem: { words: string; help: string; detail: string } | null = null;
  mode: HomeMode | null = null;
  /** What was chosen and is being put on stage ('live', or an asset's id). */
  private busy: string | null = null;
  private reads = 0;

  constructor(root: HTMLElement, actions: HomeActions, options: { fetcher?: typeof fetch; clock?: () => number } = {}) {
    this.root = root;
    this.actions = actions;
    this.fetcher = options.fetcher;
    this.clock = options.clock ?? (() => Date.now());
    root.classList?.add('session-home');
    root.setAttribute('aria-labelledby', 'session-home-title');
    root.hidden = true;
    this.title.id = 'session-home-title';
    const lead = node('p', 'home-lead', 'What will you look at?');
    const example = node('section', 'home-example');
    const preview = node('img', 'home-example-image');
    preview.src = '/api/onboarding/example';
    preview.alt = 'Microscope image of human cells, by David Root';
    preview.width = 96; preview.height = 96;
    preview.addEventListener('error', () => { preview.hidden = true; });
    const exampleText = node('div', 'home-example-text');
    this.exampleButton.type = 'button';
    this.exampleButton.addEventListener('click', () => this.actions.example());
    exampleText.append(node('h3', '', 'New here?'),
      node('p', '', 'Try a real microscope image, with a short walkthrough. No account or API key needed.'));
    example.append(preview, exampleText, this.exampleButton);

    // The live camera: its state, then what can be done.
    const cameraHead = node('span', 'home-camera-head');
    cameraHead.append(icon('camera', 'home-camera-icon'), node('span', 'home-camera-title', 'Live camera'));
    const cameraText = node('span', 'home-camera-text');
    cameraText.append(this.cameraWords, this.cameraHelp);
    const cameraButtons = node('div', 'home-camera-buttons');
    this.liveButton.type = 'button';
    this.liveButton.addEventListener('click', () => { if (!this.busy) { this.setBusy('live'); this.actions.live(); } });
    this.connect.target = '_blank';
    this.connect.rel = 'noopener';
    this.connect.title = 'Opens the camera page, where the iPhone or Blackmagic camera is set up';
    this.connect.addEventListener('click', () => { this.connect.href = this.actions.cameraPage(); });
    this.reconnectButton.type = 'button';
    this.reconnectButton.addEventListener('click', async () => {
      this.reconnectButton.disabled = true;
      this.reconnectButton.textContent = 'Connecting…';
      const said = await this.actions.reconnect();
      this.reconnectButton.disabled = false;
      this.reconnectButton.textContent = 'Reconnect';
      this.setStatus(said, 'error');
    });
    // The camera menu opens from a click here, which must not reach the menu's own close-on-a-click-outside.
    this.chooseCamera.type = 'button';
    this.chooseCamera.addEventListener('click', (event) => { event.stopPropagation(); this.actions.troubleshoot(); });
    this.filesOnly.type = 'button';
    this.filesOnly.title = 'No camera: stop looking for one, and use pictures and videos you open or drop here';
    this.filesOnly.addEventListener('click', () => this.actions.noCamera());
    cameraButtons.append(this.liveButton, this.chooseCamera, this.connect, this.reconnectButton, this.filesOnly);
    this.camera.append(cameraHead, cameraText, cameraButtons);

    // Add a recording or picture: chosen here, or dropped anywhere on the stage (main.ts takes the drop).
    this.add.type = 'button';
    this.add.setAttribute('aria-label', 'Open a picture or video: choose files from this Mac, or drop them on the stage');
    this.add.append(icon('add-file', 'home-add-icon'), node('span', 'home-add-title', 'Open a picture or video'),
      node('span', 'home-add-sub', 'Drop it here, or click to choose. It joins your Library and this Project.'), this.status);
    this.add.addEventListener('click', () => this.fileInput.click());
    this.fileInput.type = 'file';
    this.fileInput.multiple = true;
    this.fileInput.accept = 'video/*,image/*,.mov,.mp4,.m4v,.mkv,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp';
    this.fileInput.hidden = true;
    this.fileInput.addEventListener('change', () => {
      const files = [...(this.fileInput.files ?? [])];
      this.fileInput.value = '';
      if (files.length) this.actions.addFiles(files);
    });
    this.status.setAttribute('role', 'status');

    // From your assets: the newest few, and the library.
    const recentHead = node('div', 'home-recent-head');
    recentHead.append(node('h3', 'home-recent-title', 'From your Library'), this.libraryButton);
    this.libraryButton.type = 'button';
    this.libraryButton.title = 'Library: every recording and picture of yours, across all Projects, and the examples';
    this.libraryButton.addEventListener('click', () => this.actions.library());
    this.recentList.setAttribute('aria-label', 'Your newest recordings and pictures');
    this.recent.append(recentHead, this.recentList);

    const more = node('p', 'home-more');
    const troubleshoot = node('button', 'home-troubleshoot', 'Troubleshoot the camera ›');
    troubleshoot.type = 'button';
    troubleshoot.addEventListener('click', (event) => { event.stopPropagation(); this.actions.troubleshoot(); });
    // A new, empty Project is often not what was wanted: the existing ones are one quiet step away.
    if (this.actions.projects) {
      const projects = node('button', 'home-projects', 'Open an existing Project →');
      projects.type = 'button';
      projects.title = 'All projects: open one of your existing Projects instead';
      projects.addEventListener('click', () => this.actions.projects?.());
      more.append(projects);
    }
    more.append(troubleshoot);
    // What will you look at first: a picture or video and the live camera side by side, the two real choices; the example
    // a quiet strip under them for someone new (Will, 2026-10-15: "could be a little clearer"; two blue buttons competed).
    const pair = node('div', 'home-pair');
    pair.append(this.add, this.camera);
    // Or a question (Will, 2026-10-23): research needs no picture or camera; a researcher works from the Project's data
    // and models, and keeps going while you do. Its own row, under the two ways to put a specimen on the Stage.
    const research = node('form', 'home-research');
    if (this.actions.research) {
      const question = node('textarea', 'home-research-question');
      question.rows = 2; question.placeholder = 'What do you want to find out?'; question.setAttribute('aria-label', 'Your research question');
      const start = node('button', 'home-research-start', 'Start'); start.type = 'submit';
      const head = node('div', 'home-research-head');
      head.append(icon('researchers', 'home-research-icon'), node('span', 'home-research-title', 'New researcher · start with a question'));
      research.append(head, node('p', 'home-research-sub', 'A researcher works on it with this Project’s data and models, and keeps going while you do.'), question, start);
      const sync = () => { start.disabled = !question.value.trim(); };
      question.addEventListener('input', sync); sync();
      question.addEventListener('keydown', (event) => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) research.requestSubmit(); });
      research.addEventListener('submit', (event) => {
        event.preventDefault();
        const asked = question.value.trim(); if (!asked) return;
        // Cleared once it is sent; if it could not be (no connection), the question stays to send again.
        if (this.actions.research?.(asked) !== false) { question.value = ''; sync(); }
      });
    } else research.hidden = true;
    // Under the ways in, once: a few small models to add, or Not now (starter-models.ts; Will, 2026-10-26). A fresh profile
    // lands here, on its current Project's empty Stage, so this is where a first launch sees it.
    root.replaceChildren(this.title, lead, pair, research, example, starterOffer('stage'), this.recent, more, this.fileInput);
  }

  isOpen(): boolean {
    return !this.root.hidden;
  }

  /** Shown for `mode`: its choices drawn, and the newest assets read afresh when it was not showing. */
  open(mode: HomeMode): void {
    const was = this.isOpen();
    this.mode = mode;
    this.root.hidden = false;
    this.root.dataset.mode = mode;
    this.onVisibilityChange?.();
    if (!was) {
      this.busy = null;
      this.setStatus('');
      void this.loadRecent();
    }
    this.draw();
  }

  close(): void {
    this.root.hidden = true;
    this.mode = null;
    this.busy = null;
    this.problem = null;
    delete this.root.dataset.mode;
    this.onVisibilityChange?.();
  }

  /** The session's name over the box ("Untitled session" for a new one). */
  setTitle(name: string): void {
    this.title.textContent = name || 'Untitled project';
  }

  setLive(live: LiveState): void {
    this.live = live;
    this.draw();
  }

  /** What is wrong with the camera, while it is on stage with no picture (null: nothing said beyond its state). */
  setProblem(problem: { words: string; help: string; detail: string } | null): void {
    this.problem = problem;
    this.draw();
  }

  /** A choice being put on stage (null: it settled, or failed and the box stays). */
  setBusy(target: string | null): void {
    this.busy = target;
    this.draw();
  }

  setStatus(text: string, tone: '' | 'busy' | 'error' = ''): void {
    this.status.textContent = text;
    this.status.dataset.tone = tone;
  }

  private draw(): void {
    const live = this.live.tone === 'live';
    const none = this.live.tone === 'none'; // No camera chosen: calm, nothing to connect
    const problem = this.mode === 'camera' && !none ? this.problem : null;
    this.camera.dataset.tone = live ? 'live' : none ? 'none' : 'off';
    this.cameraWords.textContent = this.busy === 'live' ? 'Going live…' : none ? this.live.words
      : problem ? cameraLine(problem.words) : live ? this.live.words : cameraLine(this.live.tone === 'off' ? this.live.words : 'No picture yet');
    const help = this.busy === 'live' ? '' : none ? this.live.help : problem ? problem.help : live ? '' : this.live.help;
    this.cameraHelp.textContent = help;
    this.cameraHelp.hidden = !help;
    this.camera.title = problem?.detail ? `The camera said: ${problem.detail}` : '';
    // A new session goes live by choosing it; a camera with no picture is connected or reconnected.
    this.liveButton.hidden = this.mode !== 'choose' || none;
    this.liveButton.disabled = this.busy !== null;
    this.chooseCamera.hidden = !none;
    this.connect.hidden = live || none;
    this.connect.href = this.actions.cameraPage();
    this.connect.className = this.mode === 'choose' ? 'home-connect' : 'mm-primary home-connect';
    this.reconnectButton.hidden = live || none;
    // The camera has no picture: the way out is offered, quietly, beside the ways to fix it.
    this.filesOnly.hidden = live || none || this.mode !== 'camera';
    this.add.disabled = this.busy !== null;
    for (const tile of this.recentList.querySelectorAll<HTMLButtonElement>('.home-recent-item')) {
      tile.disabled = this.busy !== null;
      tile.dataset.opening = tile.dataset.asset === this.busy ? 'true' : '';
    }
  }

  /** The user's newest few, as tiles (none: the section says so and offers the library). */
  private async loadRecent(): Promise<void> {
    const read = ++this.reads;
    let assets: AssetMeta[] = [];
    try {
      const response = await (this.fetcher ?? fetch)(libraryUrl({ scope: 'mine', limit: RECENT }), { cache: 'no-store' });
      if (response.ok) assets = readLibrary(await response.json()).assets.slice(0, RECENT);
    } catch {
      assets = []; // the other choices still work: these are only a shortcut
    }
    if (read !== this.reads) return;
    const now = this.clock() / 1000;
    this.recentList.replaceChildren(...assets.map((asset) => {
      const item = node('li');
      const tile = node('button', 'home-recent-item');
      tile.type = 'button';
      tile.dataset.asset = asset.id;
      tile.setAttribute('aria-label', `Open ${asset.title}`);
      tile.title = [asset.title, asset.specimen ?? '', sourceMeta(asset, now)].filter(Boolean).join('\n');
      tile.append(assetThumb(asset), node('span', 'home-recent-name', asset.title), node('span', 'home-recent-meta', sourceMeta(asset, now)));
      tile.addEventListener('click', () => { if (!this.busy) { this.setBusy(asset.id); this.actions.open(asset); } });
      item.append(tile);
      return item;
    }));
    if (!assets.length) this.recentList.append(node('li', 'home-recent-empty', 'Nothing yet: add one above, or open the examples in the Library.'));
    this.draw();
  }
}
