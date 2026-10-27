// The Test footage page (#footage, a developer setting in the gear): every recording in the assets store, as a grid
// of thumbnails by group, and adding a new one (assets/README.md). The user's own (real microscope recordings, files
// added here) open in the session as the assets they are (main.ts, through asset-library.ts `assetFor`);
// the rest is test footage, played into the live feed instead of the camera.
//
// Like the camera menu it reads the camera bridge only (GET /assets/library, /assets/thumb, POST
// /assets/upload): test footage stays invisible to everything downstream. And it costs the live view
// nothing: the library is fetched only while this view is open and only when it changed, thumbnails
// load as cards scroll into sight (made by the bridge one at a time at background priority), and a
// big corpus group shows a page of cards at a time.

import { type CameraFeed, clips, duration, footageGroups, type FootageGroup, type FootageItem, isLibrary, opticsLine, specimenOf } from './camera';
import { isOwnFootage } from './asset-library';

/** The footage item playing: test footage by its name, or one of the user's own put on stage by the server (the
 * bridge's `asset:<its path>`), by the file it is. */
export function playingName(footage: string | null | undefined, library: readonly Pick<FootageItem, 'name'>[]): string | null {
  if (!footage) return null;
  if (!footage.startsWith('asset:')) return footage;
  const path = footage.slice('asset:'.length);
  return library.find((item) => path.endsWith(`/${item.name}`))?.name ?? null;
}

export const PAGE = 48; // cards shown at once in a group; "Show more" adds as many again

/** The groups with only the recordings whose title, group or specimen holds every word of `query`. */
export function filterGroups(groups: FootageGroup[], query: string): FootageGroup[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return groups;
  return groups
    .map((g) => ({
      ...g,
      items: g.items.filter((item) => {
        const text = `${item.title} ${item.group} ${item.specimen ?? ''} ${item.name}`.toLowerCase();
        return words.every((w) => text.includes(w));
      }),
    }))
    .filter((g) => g.items.length > 0);
}

/** "3:43 · 40x objective · not backed up yet" for a card. */
export function cardNote(item: FootageItem): string {
  return [
    duration(item.seconds),
    item.optics?.objective ? opticsLine({ objective: item.optics.objective }) : '',
    item.backedUp === false ? 'not backed up yet' : '',
  ].filter(Boolean).join(' · ');
}

type ThumbLoad = { near: boolean; loading: boolean; tries: number; retryAt: number; timer?: number };

/** One bounded loader for currently mounted footage cards. Offscreen retries pause. */
export class FootageThumbnails {
  private readonly observer: IntersectionObserver;
  private readonly loads = new Map<HTMLImageElement, ThumbLoad>();

  constructor(root: HTMLElement) {
    this.observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const image = entry.target as HTMLImageElement;
        const load = this.loads.get(image);
        if (!load) continue;
        load.near = entry.isIntersecting;
        if (!load.near && load.timer !== undefined) {
          window.clearTimeout(load.timer); load.timer = undefined;
        }
        if (load.near) this.load(image, load);
      }
    }, { root, rootMargin: '400px 0px' });
  }

  observe(image: HTMLImageElement): void {
    this.loads.set(image, { near: false, loading: false, tries: 0, retryAt: 0 });
    this.observer.observe(image);
  }

  reset(): void {
    this.observer.disconnect();
    for (const load of this.loads.values()) if (load.timer !== undefined) window.clearTimeout(load.timer);
    this.loads.clear();
  }

  private load(image: HTMLImageElement, load: ThumbLoad): void {
    if (this.loads.get(image) !== load || !image.isConnected || !load.near || load.loading || load.timer !== undefined || load.tries > 8) return;
    const src = image.dataset.src;
    if (!src) return;
    const wait = load.retryAt - Date.now();
    if (wait > 0) {
      load.timer = window.setTimeout(() => { load.timer = undefined; this.load(image, load); }, wait);
      return;
    }
    load.loading = true;
    image.onerror = () => {
      if (this.loads.get(image) !== load) return;
      load.loading = false; load.tries++;
      load.retryAt = Date.now() + 1500 * load.tries;
      this.load(image, load);
    };
    image.onload = () => {
      if (this.loads.get(image) !== load) return;
      image.classList.add('loaded'); this.observer.unobserve(image); this.loads.delete(image);
    };
    image.src = load.tries ? `${src}&try=${load.tries}` : src;
  }
}

export class AssetsView {
  private readonly root: HTMLElement;
  private readonly feed: CameraFeed;
  private readonly play: (name: string) => Promise<void>;
  private query = '';
  private specimen: string | null = null; // the public-library specimen opened (its clips shown)
  private readonly shown = new Map<string, number>(); // cards shown per group
  private adding = false;
  private uploadState: { share: number; error: string; done: string } = { share: -1, error: '', done: '' };
  private uploadForm: HTMLFormElement | null = null; // keep the browser-owned File and draft across refresh/failure
  private uploadPending = false;
  private renderedKey = '';
  private readonly thumbnails: FootageThumbnails;

  constructor(root: HTMLElement, feed: CameraFeed, play: (name: string) => Promise<void>) {
    this.root = root;
    this.feed = feed;
    this.play = play;
    this.thumbnails = new FootageThumbnails(root);
  }

  /** Called when the view is opened and after every feed refresh while it is open. */
  render(): void {
    const state = this.feed.state;
    const key = JSON.stringify([this.feed.libraryVersion, this.query, this.specimen, [...this.shown], state?.footage,
      this.adding, this.uploadState, Boolean(state)]);
    if (key === this.renderedKey) return; // nothing changed: the grid (and its loaded pictures) stays
    this.renderedKey = key;
    const children: HTMLElement[] = [this.header()];
    if (this.adding) children.push(this.addForm());
    if (!state) {
      children.push(note("The camera bridge isn't answering. Start everything with make start."));
    } else if (!this.feed.library.length) {
      children.push(note(state.libraryCount ? 'Loading the library…' : `No recordings yet in ${home(state.folder)}.`));
    } else {
      const groups = filterGroups(footageGroups(this.feed.library), this.query);
      const playing = state.source === 'footage' ? playingName(state.footage, this.feed.library) : null;
      if (!groups.length) children.push(note('Nothing matches.'));
      for (const group of groups.filter((g) => !isLibrary(g.name))) children.push(this.section(group, playing));
      const library = groups.filter((g) => isLibrary(g.name));
      if (library.length) children.push(this.library(library, playing));
    }
    const scroll = this.root.scrollTop;
    const active = document.activeElement as HTMLInputElement | null;
    const draftFocus = active?.tagName === 'INPUT' && this.uploadForm?.contains(active)
      ? {node:active,start:active.selectionStart,end:active.selectionEnd,direction:active.selectionDirection} : null;
    const searchFocus = active?.classList.contains('assets-search') && this.root.contains(active)
      ? {start:active.selectionStart,end:active.selectionEnd,direction:active.selectionDirection} : null;
    this.thumbnails.reset();
    this.root.replaceChildren(...children);
    this.root.scrollTop = scroll;
    if (draftFocus && this.root.contains(draftFocus.node)) {
      draftFocus.node.focus({preventScroll:true});
      if (draftFocus.start !== null && draftFocus.end !== null) draftFocus.node.setSelectionRange(draftFocus.start,draftFocus.end,draftFocus.direction ?? undefined);
    }
    if (searchFocus) {
      const search = this.root.querySelector<HTMLInputElement>('.assets-search');
      search?.focus({preventScroll:true});
      if (searchFocus.start !== null && searchFocus.end !== null) search?.setSelectionRange(searchFocus.start,searchFocus.end,searchFocus.direction ?? undefined);
    }
    this.root.querySelectorAll<HTMLImageElement>('img[data-src]').forEach((img) => this.thumbnails.observe(img));
  }

  private header(): HTMLElement {
    const head = el('div', 'assets-head');
    const title = el('div', 'assets-title');
    title.append(Object.assign(document.createElement('h2'), { textContent: 'Test footage' }));
    const count = this.feed.library.length;
    title.append(el('p', 'assets-count', count ? `${count.toLocaleString()} recordings: your own open in this project, test footage plays instead of the camera` : ''));
    const add = Object.assign(document.createElement('button'), { type: 'button', className: 'assets-add-open', textContent: this.adding ? 'Close' : 'Add a recording' });
    add.addEventListener('click', () => {
      this.adding = !this.adding;
      this.uploadForm = null; // an explicit close discards the draft; it does not cancel an upload already sent
      if (!this.uploadPending) this.uploadState = { share: -1, error: '', done: '' };
      this.render();
    });
    title.append(add);
    const search = Object.assign(document.createElement('input'), {
      type: 'search', className: 'assets-search', placeholder: 'Find by title or specimen', value: this.query,
    });
    search.setAttribute('aria-label', 'Find a recording');
    search.addEventListener('input', () => {
      const start = search.selectionStart, end = search.selectionEnd, direction = search.selectionDirection;
      this.query = search.value;
      this.render();
      const again = this.root.querySelector<HTMLInputElement>('.assets-search');
      again?.focus();
      again?.setSelectionRange(start ?? this.query.length, end ?? this.query.length, direction ?? undefined);
    });
    head.append(title, search);
    if (this.feed.state) {
      head.append(el('p', 'assets-where', `From ${home(this.feed.state.folder)}, shared by every checkout. Real recordings are backed up to the private GitHub release; the rest is rebuilt from its manifest (assets/README.md).`));
    }
    return head;
  }

  /** Recordings from this microscope (and anything added): a heading and their cards. */
  private section(group: FootageGroup, playing: string | null): HTMLElement {
    const section = el('section', 'assets-group');
    const shown = this.shown.get(group.name) ?? PAGE;
    section.append(el('h3', 'assets-group-name', `${group.name} · ${group.items.length.toLocaleString()}`),
      this.grid(group.items.slice(0, shown), playing), ...this.more(group, shown));
    return section;
  }

  /** The public library: one card per specimen (cover and count); a specimen with several clips
   *  opens to its clips, one with a single clip plays it. A search shows the matching clips at once. */
  private library(groups: FootageGroup[], playing: string | null): HTMLElement {
    const section = el('section', 'assets-group assets-library');
    const total = groups.reduce((n, g) => n + g.items.length, 0);
    const open = this.query ? null : groups.find((g) => g.name === this.specimen) ?? null;
    const head = el('div', 'assets-library-head');
    if (open) {
      const back = Object.assign(document.createElement('button'), { type: 'button', className: 'assets-back', textContent: '← Public library' });
      back.addEventListener('click', () => {
        this.specimen = null;
        this.render();
      });
      head.append(back, el('h3', 'assets-group-name', `${specimenOf(open.name)} · ${clips(open.items.length)}`));
    } else {
      head.append(el('h3', 'assets-group-name', `Public library · ${groups.length} specimens · ${clips(total)}`));
    }
    section.append(head);
    if (!open) {
      section.append(el('p', 'assets-where', 'Public microscope videos and slides (sources and licences in corpus.json), played through a simulated eyepiece so the app sees them as it sees the phone.'));
    }
    if (open) {
      const shown = this.shown.get(open.name) ?? PAGE;
      section.append(this.grid(open.items.slice(0, shown), playing), ...this.more(open, shown));
    } else if (this.query) {
      for (const group of groups) section.append(el('p', 'assets-subhead', specimenOf(group.name)), this.grid(group.items.slice(0, PAGE), playing));
    } else {
      const grid = el('ul', 'assets-grid');
      for (const group of groups) {
        const card = el('li', `assets-card${group.items.some((i) => i.name === playing) ? ' playing' : ''}`);
        const button = Object.assign(document.createElement('button'), { type: 'button', className: 'assets-play' });
        const single = group.items.length === 1;
        button.setAttribute('aria-label', single ? `Play ${specimenOf(group.name)} in the live view` : `Open ${specimenOf(group.name)}: ${clips(group.items.length)}`);
        button.append(thumb(this.feed, group.items[0], 'assets-thumb'), el('span', 'assets-card-title', specimenOf(group.name)),
          el('span', 'assets-card-note', single ? cardNote(group.items[0]) || '1 clip' : clips(group.items.length)));
        button.addEventListener('click', () => {
          if (single) void this.play(group.items[0].name);
          else {
            this.specimen = group.name;
            this.render();
            this.root.scrollTo({ top: section.offsetTop - 12 });
          }
        });
        card.append(button);
        grid.append(card);
      }
      section.append(grid);
    }
    return section;
  }

  private more(group: FootageGroup, shown: number): HTMLElement[] {
    if (group.items.length <= shown) return [];
    const button = Object.assign(document.createElement('button'), {
      type: 'button', className: 'assets-more', textContent: `Show ${Math.min(PAGE, group.items.length - shown)} more of ${group.items.length.toLocaleString()}`,
    });
    button.addEventListener('click', () => {
      const next = group.items[shown]?.name;
      this.shown.set(group.name, shown + PAGE);
      this.render();
      // Continue the keyboard walk where this group's next page begins.
      if (next) [...this.root.querySelectorAll<HTMLButtonElement>('.assets-play')]
        .find(card => card.dataset.footageName === next)?.focus();
    });
    return [button];
  }

  private grid(items: FootageItem[], playing: string | null): HTMLElement {
    const grid = el('ul', 'assets-grid');
    for (const item of items) {
      const card = el('li', `assets-card${item.name === playing ? ' playing' : ''}`);
      const button = Object.assign(document.createElement('button'), { type: 'button', className: 'assets-play' });
      button.dataset.footageName = item.name;
      const own = isOwnFootage(item);
      button.setAttribute('aria-label', own ? `Open ${item.title} in this project` : `Play ${item.title} in the live view`);
      button.title = item.name === playing ? (own ? 'On stage' : 'Playing in the live view')
        : own ? 'Open it in this project: it becomes one of its sources' : 'Play in the live view instead of the camera';
      button.append(thumb(this.feed, item, 'assets-thumb'), el('span', 'assets-card-title', item.title), el('span', 'assets-card-note', item.name === playing ? `Playing · ${cardNote(item)}` : cardNote(item)));
      button.addEventListener('click', () => void this.play(item.name));
      card.append(button);
      grid.append(card);
    }
    return grid;
  }

  private addForm(): HTMLElement {
    if (this.uploadForm) { this.renderUploadState(); return this.uploadForm; }
    const form = Object.assign(document.createElement('form'), { className: 'assets-form' });
    this.uploadForm = form;
    form.append(el('h3', 'assets-group-name', 'Add a recording'));
    form.append(el('p', 'assets-where', 'A video from the iPhone on the microscope (MP4, MOV, M4V or MKV). It goes into the store under "Added here" and plays at once; make assets-adopt puts it in the manifest, and make assets-backup backs it up.'));
    const file = Object.assign(document.createElement('input'), { type: 'file', accept: '.mp4,.mov,.m4v,.mkv,video/*', required: true });
    const specimen = Object.assign(document.createElement('input'), { type: 'text', required: true, placeholder: 'e.g. onion epidermis' });
    const title = Object.assign(document.createElement('input'), { type: 'text', placeholder: 'optional: made from the specimen and date' });
    const microscope = Object.assign(document.createElement('input'), { type: 'checkbox', checked: true });
    const objective = Object.assign(document.createElement('input'), { type: 'number', min: '1', max: '150', step: '0.5', placeholder: 'e.g. 40' });
    const eyepiece = Object.assign(document.createElement('input'), { type: 'number', min: '1', max: '40', step: '0.5', placeholder: 'e.g. 10' });
    const optics = el('div', 'assets-optics');
    optics.append(field('Objective (x), if known', objective), field('Eyepiece (x), if known', eyepiece));
    form.append(field('Video', file), field('What is on the slide', specimen), field('Title', title), optics, field('Through the microscope', microscope, true));
    const submit = Object.assign(document.createElement('button'), { type: 'submit', className: 'assets-upload', textContent: 'Add' });
    const status = el('div', 'assets-upload-status'); status.setAttribute('aria-live', 'polite');
    form.append(submit, status);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (this.uploadPending) return;
      const chosen = file.files?.[0];
      if (!chosen) return;
      this.uploadPending = true;
      this.uploadState = { share: 0, error: '', done: '' };
      this.renderUploadState();
      let last = 0;
      this.feed.upload(chosen, {
        title: title.value.trim(), specimen: specimen.value.trim(), microscope: microscope.checked,
        objective: Number(objective.value) || null, eyepiece: Number(eyepiece.value) || null,
      }, (s) => {
        if (s - last < 0.02 && s < 1) return; // status updates at most every 2%; the form and File stay intact
        last = s;
        this.uploadState = { ...this.uploadState, share: s };
        this.renderUploadState();
      }).then((item) => {
        this.uploadPending = false;
        this.uploadForm = null; // success clears the selected file and metadata, never progress or failure
        this.uploadState = { share: 1, error: '', done: `Added "${item.title}" under Added here.` };
        this.renderedKey = ''; // another upload may finish with the same title/status as the previous one
        this.render();
      }).catch((e: Error) => {
        this.uploadPending = false;
        this.uploadState = { share: -1, error: e.message, done: '' };
        this.renderUploadState();
      });
    });
    this.renderUploadState();
    return form;
  }

  private renderUploadState(): void {
    const form = this.uploadForm;
    if (!form) return;
    for (const input of form.querySelectorAll<HTMLInputElement>('input')) input.disabled = this.uploadPending;
    const submit = form.querySelector<HTMLButtonElement>('.assets-upload')!;
    submit.disabled = this.uploadPending;
    submit.textContent = this.uploadState.error ? 'Try again' : 'Add';
    const status = form.querySelector<HTMLElement>('.assets-upload-status')!;
    const { share, error, done } = this.uploadState;
    status.replaceChildren();
    if (this.uploadPending) {
      const bar = Object.assign(document.createElement('progress'), { max: 1, value: Math.max(0, share) });
      bar.setAttribute('aria-label', 'Upload'); status.append(bar);
    }
    if (error) status.append(el('p', 'assets-error', error));
    if (done) status.append(el('p', 'assets-done', done));
  }
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text = ''): HTMLElementTagNameMap[K] {
  return Object.assign(document.createElement(tag), { className, textContent: text });
}

function note(text: string): HTMLElement {
  return el('p', 'assets-where', text);
}

function home(folder: string): string {
  return folder.replace(/^\/Users\/[^/]+/, '~');
}

function thumb(feed: CameraFeed, item: FootageItem | undefined, className: string): HTMLImageElement {
  const img = Object.assign(document.createElement('img'), { className, alt: '', decoding: 'async' as const });
  img.width = 240;
  img.height = 135;
  if (item) img.dataset.src = feed.thumbUrl(item);
  return img;
}

function field(label: string, input: HTMLInputElement, inline = false): HTMLLabelElement {
  const wrap = el('label', inline ? 'assets-field inline' : 'assets-field');
  wrap.append(el('span', '', label), input);
  return wrap;
}
