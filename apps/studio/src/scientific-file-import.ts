import { selectedFolder, selectedStack, type Folder } from './scientific-folder';
import { nativeFolderBridge, chooseNativeFolder, isNativeFolder, uploadNativeFolder, type NativeFolder, recoverNativeFolder, NativeFolderRecoveryRequired } from './scientific-folder-native';
import { renderedMovieDescription, validMovieFile } from './scientific-movie';
import { StudioSheet } from './sheet';
import { sourceReaders } from './scientific-readers';

type SavedSource = { id: string; session: string; revision: number; title: string };
type Sample = { capability: string; reader: string; id: string; name: string; sha256: string; size: number; licence: string; readerReason: string; canOpen: boolean; reason: string };
type Options = { session: string; title: string; open(source: SavedSource): Promise<void | (() => void)>; saved(): void };
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => { const item = document.createElement(tag); item.textContent = text; return item; };
const MAX_BYTES = 64 * 1024 * 1024;
const sample = (value: unknown): Sample | null => object(value) && typeof value.capability === 'string' && typeof value.reader === 'string' && typeof value.id === 'string'
  && typeof value.name === 'string' && typeof value.sha256 === 'string' && /^[0-9a-f]{64}$/.test(value.sha256) && Number.isSafeInteger(value.size)
  && typeof value.licence === 'string' && typeof value.readerReason === 'string'
  && typeof value.canOpen === 'boolean' && typeof value.reason === 'string'
  ? { capability: value.capability, reader: value.reader, id: value.id, name: value.name, sha256: value.sha256, size: Number(value.size), licence: value.licence, readerReason: value.readerReason, canOpen: value.canOpen, reason: value.reason } : null;
const imports = new Map<string, ScientificFileImport>();

/** One retained sheet owns the selected file and request identity through hide/reopen. */
export class ScientificFileImport {
  readonly content = node('section');
  private readonly sheet = new StudioSheet('Import scientific source');
  private readonly picker = node('input');
  private readonly mode = node('select');
  private readonly nativePicker = node('button', 'Choose folder');
  private choosing = false;
  private selection: AbortController | null = null;
  private releaseFolder(): void { if (this.folder && isNativeFolder(this.folder)) void this.folder.release(); }
  private folder: Folder | NativeFolder | null = null;
  private readonly status = node('p', 'Choose an original scientific file to keep in this Project.');
  private readonly start = node('button', 'Save source to Project');
  private readonly stop = node('button', 'Stop import');
  private readonly openButton = node('button', 'View saved source');
  private readonly progress = node('progress');
  private readonly address = node('input');
  private readonly digest = node('input');
  private readonly remote = node('div');
  private readonly axis = node('select');
  private readonly axisLabel = node('label', 'New stack axis');
  private readonly samples = node('div');
  private readonly sampleRequests = new Map<string, string>();
  private picked: (files: File[]) => void = () => {};
  private selected: File | null = null;
  private identity = '';
  private recovery: {name:string;size:number} | null = null;
  private controller: AbortController | null = null;
  private saved: SavedSource | null = null;

  private readonly options: Options;
  constructor(options: Options) {
    this.options=options;
    this.content.className = 'scientific-file-import';
    const destination = node('p', `Destination: ${options.title}`); destination.className = 'scientific-import-destination';
    this.content.append(destination, node('p', 'Keep the original file in this Project, then choose a reader to open its scientific contents.'));
    const sourceType = node('label', 'Source type');
    for (const [value, text] of [['file', 'File'], ['directory', 'Folder'], ['stack', 'Stack of files'], ['url', 'Web address'], ['sample', 'Sample data']]) { const option = node('option', text); option.value = value; this.mode.append(option); }
    this.mode.value = 'file'; sourceType.append(this.mode);
    const bridge = nativeFolderBridge();
    this.nativePicker.hidden = true;
    this.mode.addEventListener('change', () => {
      this.releaseFolder(); this.picker.value = ''; this.selected = null; this.folder = null; this.saved = null; this.openButton.hidden = true; this.start.disabled = true;
      const url = this.mode.value === 'url', samples = this.mode.value === 'sample'; label.hidden = url || samples; this.remote.hidden = !url; this.axisLabel.hidden = this.mode.value !== 'stack'; this.samples.hidden = !samples;
      if (samples) { this.start.hidden = true; void this.listSamples(); return; }
      this.start.hidden = false;
      if (url) { this.identity = crypto.randomUUID(); this.start.disabled = !this.validAddress(); this.status.textContent = 'Paste the https address of one scientific file. Hooke downloads its exact bytes; nothing is converted.'; return; }
      if (this.mode.value === 'directory') this.picker.setAttribute('webkitdirectory', ''); else this.picker.removeAttribute('webkitdirectory');
      this.picker.multiple = this.mode.value === 'directory' || this.mode.value === 'stack';
      this.nativePicker.hidden = this.mode.value !== 'directory' || !bridge;
      this.picker.hidden = !this.nativePicker.hidden;
      this.picker.setAttribute('aria-label', this.mode.value === 'directory' ? 'Scientific source folder' : 'Scientific source file');
      this.status.textContent = this.mode.value === 'directory' ? 'Choose a folder. All selected files and their relative names will be retained together.'
        : this.mode.value === 'stack' ? 'Choose two or more files of one type. They are stacked in name order (2 before 10) along the new axis you name.' : 'Choose an original scientific file.';
    });
    const label = node('label', 'Choose source');
    const addressLabel = node('label', 'Web address'), digestLabel = node('label', 'Expected SHA-256 (optional)');
    this.address.type = 'url'; this.address.placeholder = 'https://'; this.address.setAttribute('aria-label', 'Scientific source web address');
    this.digest.type = 'text'; this.digest.setAttribute('aria-label', 'Expected SHA-256 of the file'); this.digest.spellcheck = false;
    addressLabel.append(this.address); digestLabel.append(this.digest);
    for (const [value, text] of [['z', 'z (depth)'], ['t', 't (time)'], ['c', 'c (channel)']]) { const option = node('option', text); option.value = value; this.axis.append(option); }
    this.axis.value = 'z'; this.axisLabel.append(this.axis); this.axisLabel.hidden = true;
    this.axis.addEventListener('change', () => { if (this.controller) return; this.identity = crypto.randomUUID(); this.saved = null; this.openButton.hidden = true; this.start.disabled = !this.folder; this.start.textContent = 'Save source to Project'; });
    this.remote.className = 'scientific-import-remote'; this.remote.hidden = true; this.remote.append(addressLabel, digestLabel);
    const edited = () => {
      if (this.controller) return;
      this.identity = crypto.randomUUID(); this.saved = null; this.openButton.hidden = true; this.start.textContent = 'Save source to Project';
      this.start.disabled = !this.validAddress();
      this.status.textContent = this.validAddress() ? 'Save downloads the file’s exact bytes into this Project. Web content is kept as data and never run.'
        : this.digest.value.trim() && !/^[0-9a-fA-F]{64}$/.test(this.digest.value.trim()) ? 'The expected SHA-256 has 64 hexadecimal characters.'
        : 'Paste an https address. Hooke downloads only from public https addresses.';
    };
    this.address.addEventListener('input', edited); this.digest.addEventListener('input', edited);
    this.picker.type = 'file'; this.picker.setAttribute('aria-label', 'Scientific source file'); label.append(this.picker, this.nativePicker);
    this.start.className = this.openButton.className = 'mm-primary';
    this.status.setAttribute('role', 'status'); this.status.setAttribute('aria-live', 'polite');
    this.progress.setAttribute('aria-label', 'Saving and verifying source'); this.progress.hidden = true;
    this.stop.hidden = true; this.openButton.hidden = true; this.start.disabled = true;
    this.picker.addEventListener('change', () => this.picked(Array.from(this.picker.files ?? [])));
    this.picked = (files: File[]) => {
      if (this.controller || this.recovery) return;
      this.releaseFolder(); this.folder = null;
      this.selected = files[0] ?? null; this.identity = crypto.randomUUID(); this.saved = null; this.openButton.hidden = true;
      if (this.mode.value === 'stack') {
        try { const chosen = selectedStack(files); this.folder = chosen; this.start.disabled = false; this.start.textContent = 'Save source to Project';
          this.status.textContent = `${chosen.count} files · ${chosen.size.toLocaleString()} bytes, stacked in this order: ${chosen.order.slice(0, 5).join(', ')}${chosen.count > 5 ? ', …' : ''}. Original bytes are retained.`; }
        catch (error) { this.start.disabled = true; this.selected = null; this.status.textContent = String(error instanceof Error ? error.message : error); }
        return;
      }
      if (this.mode.value === 'directory') {
        try { this.folder = selectedFolder(files); this.start.disabled = false; this.start.textContent = 'Save source to Project'; this.status.textContent = `${this.folder.name} · ${this.folder.count} files · ${this.folder.size.toLocaleString()} bytes. Original bytes and relative names are retained.`; }
        catch (error) { this.start.disabled = true; this.selected = null; this.status.textContent = String(error instanceof Error ? error.message : error); }
        return;
      }
      const tooLarge = this.selected !== null && this.selected.size > MAX_BYTES;
      this.start.disabled = !this.selected || tooLarge; this.start.textContent='Save source to Project';
      this.status.textContent = tooLarge ? 'This import currently supports files up to 64 MiB. Your file has not been uploaded.'
        : this.selected ? `${this.selected.name} · ${this.selected.size.toLocaleString()} bytes. Save keeps the original without converting it.` : 'Choose an original scientific file.';
    };
    this.nativePicker.addEventListener('click', () => {
      if (!bridge || this.choosing || this.controller || this.recovery) return;
      const selection=new AbortController(); this.selection=selection; this.stop.hidden=false;
      this.choosing = true; this.nativePicker.disabled = true; this.mode.disabled = true; this.start.disabled = true;
      this.status.textContent = 'Choose a folder. Its complete contents, including hidden metadata, will be read.';
      void chooseNativeFolder(bridge,selection.signal).then(folder => {
        if (folder) {
          this.releaseFolder(); this.folder = folder; this.selected = null; this.identity = crypto.randomUUID(); this.saved = null; this.openButton.hidden = true;
          this.status.textContent = `${folder.name} · ${folder.count} files · ${folder.size.toLocaleString()} bytes. Original bytes and relative names are retained.`;
        } else this.status.textContent = this.folder ? `${this.folder.name} is still selected.` : 'No folder selected.';
      }).catch(error => {
        this.status.textContent = `${String(error instanceof Error ? error.message : error)}${this.folder ? ` ${this.folder.name} is still selected.` : ''}`;
      }).finally(() => {
        this.selection=null; this.stop.hidden=true; this.choosing = false; this.nativePicker.disabled = false; this.mode.disabled = false;
        this.start.disabled = !this.folder || this.saved !== null; this.start.textContent = this.saved ? 'Source saved' : 'Save source to Project';
      });
    });
    this.start.addEventListener('click', () => void this.run());
    this.stop.addEventListener('click', () => { this.controller?.abort(); this.selection?.abort(); });
    this.openButton.addEventListener('click', () => {
      if (!this.saved) return;
      void options.open(this.saved).then(afterClose => { this.sheet.close(); afterClose?.(); }).catch(error => { this.status.textContent = String(error instanceof Error ? error.message : error); });
    });
    const actions = node('div'); actions.className = 'scientific-import-actions';
    actions.append(this.start, this.stop, this.openButton);
    const limits = node('p', bridge ? 'Native folders transfer in bounded chunks into immutable Project storage (up to 64 GiB and 4,096 files, subject to free disk space). Reader and result limits are separate. Files, browser folders and web imports remain limited to 64 MiB.' : 'Up to 64 MiB total; folders may contain up to 4,096 files; web addresses must be public https. Larger and lazily read remote data remain unavailable here.'); limits.className = 'scientific-import-note';
    const background = node('p', 'You can close this sheet while importing. Your completed source will be in Project Data.'); background.className = 'scientific-import-note';
    this.samples.className = 'scientific-import-samples'; this.samples.hidden = true;
    // The sheet owns drops over it: the stage's window handlers (live-view assets) never see them.
    for (const type of ['dragenter', 'dragleave']) this.content.addEventListener(type, event => event.stopPropagation());
    this.content.addEventListener('dragover', event => { const drag = event as DragEvent; drag.preventDefault(); drag.stopPropagation(); if (drag.dataTransfer) drag.dataTransfer.dropEffect = 'copy'; });
    this.content.addEventListener('drop', event => {
      const drag = event as DragEvent; drag.preventDefault(); drag.stopPropagation(); const data = drag.dataTransfer; if (!data) return;
      const folder = Array.from(data.items ?? []).some(item => item.kind === 'file' && (item as DataTransferItem & { webkitGetAsEntry?: () => { isDirectory: boolean } | null }).webkitGetAsEntry?.()?.isDirectory === true);
      this.accept({ files: Array.from(data.files ?? []), uri: data.getData('text/uri-list') || '', folder });
    });
    this.content.append(sourceType, label, this.axisLabel, this.remote, this.samples, limits, this.progress, this.status, actions, background);
  }
  private validAddress(): boolean {
    const digest = this.digest.value.trim();
    return /^https:\/\/\S+$/i.test(this.address.value.trim()) && (!digest || /^[0-9a-fA-F]{64}$/.test(digest));
  }
  show(): void { this.sheet.open(this.content); }
  /** A drop onto the sheet fills the same choice the controls would: never uploads by itself. */
  accept(drop: { files: File[]; uri: string; folder: boolean }): void {
    if (this.recovery) { this.status.textContent='Check the original import outcome before choosing another source.'; return; }
    if (this.controller || this.choosing) { this.status.textContent = 'Wait for the current import to finish, or stop it, before dropping another source.'; return; }
    const choose = (mode: string) => { this.mode.value = mode; this.mode.dispatchEvent(new Event('change')); };
    if (drop.folder) { this.status.textContent = 'Dropped folders are not read here, because the browser leaves out hidden metadata such as .zattrs. Choose Folder and select it instead.'; return; }
    if (drop.files.length > 1) {
      choose('stack'); this.picker.value = ''; this.picked(drop.files);
      if (this.folder) this.status.textContent += ' Dropped as a stack; choose File to keep only one.'; return;
    }
    if (drop.files.length === 1) { choose('file'); this.picker.value = ''; this.picked(drop.files); return; }
    const uri = drop.uri.split(/\r?\n/).map(line => line.trim()).find(line => line && !line.startsWith('#')) ?? '';
    if (uri) { choose('url'); this.address.value = uri; this.address.dispatchEvent(new Event('input')); return; }
    this.status.textContent = 'Drop a file, several files of one type, or a link to a file.';
  }
  finished(): boolean { return this.saved !== null; }
  private async run(): Promise<void> {
    if (this.mode.value === 'url') return this.download();
    const file = this.folder ?? this.selected ?? this.recovery;
    if (!file || this.controller || this.choosing || (!this.recovery && !('read' in file) && file.size > MAX_BYTES)) return;
    const directory = this.folder !== null || this.recovery !== null, stacked = directory && this.mode.value === 'stack', controller = new AbortController(); this.controller = controller;
    this.picker.disabled = true; this.nativePicker.disabled = true; this.mode.disabled = true; this.axis.disabled = true; this.start.disabled = true; this.stop.hidden = false; this.progress.hidden = false;
    this.status.textContent = `Saving ${file.name} to ${this.options.title} and verifying the original bytes…`;
    try {
      const query = new URLSearchParams({ session: this.options.session, name: file.name, size: String(file.size), request: this.identity, ...(stacked ? { axis: this.axis.value } : {}) });
      const kind = stacked ? 'stack' : directory ? 'directory' : 'file';
      let result: unknown;
      if (this.recovery) {
        const recovered=await recoverNativeFolder(this.options.session,this.identity);
        if (recovered.project===null) { this.recovery=null; throw new Error('The stopped import was not accepted. Choose the folder again to retry.'); }
        result=recovered; this.recovery=null;
      } else if (this.folder && isNativeFolder(this.folder)) {
        result=await uploadNativeFolder(this.folder,{session:this.options.session,request:this.identity,signal:controller.signal,
          progress:(done,total)=>{ this.progress.max=total; this.progress.value=done; this.status.textContent=done===total
            ? 'All bytes transferred. Verifying and saving the source…' : `Saving ${file.name}: ${done.toLocaleString()} of ${total.toLocaleString()} bytes`; }});
      } else {
      const response = await fetch(`/api/${{ stack: 'scientific-stacks', directory: 'scientific-directories', file: 'scientific-files' }[kind]}?${query}`, { method: 'POST', headers: { 'X-MicroscopeMind': `import-scientific-${kind}` }, body: this.folder && !isNativeFolder(this.folder) ? this.folder.body : this.selected, signal: controller.signal });
      result = await response.json();
      if (!object(result) || !response.ok || result.ok !== true) throw new Error(object(result) ? `${String(result.says ?? 'Source could not be saved.')} ${String(result.next ?? '')}` : 'Import returned an invalid response. Retry the same file to recover its saved status.');
      }
      if (!object(result)) throw new Error('Import returned an invalid response.');
      const project = result.project;
      if (!object(project) || typeof project.id !== 'string' || !/^p[0-9a-f]{32}$/.test(project.id) || project.session !== this.options.session || project.revision !== 1 || !object(project.document) || project.document.kind !== (directory ? 'source-directory' : 'source-file') || project.document.title !== file.name)
        throw new Error('The response does not match this source and Project. Check Project Data before retrying.');
      this.saved = { id: project.id, session: this.options.session, revision: 1, title: file.name };
      this.status.textContent = `Source saved in ${this.options.title}. Original bytes are retained. Reader readiness has not been established.`;
      this.openButton.hidden = false; this.folder = null; this.selected = null; this.picker.value = ''; this.options.saved();
    } catch (error) {
      if (error instanceof NativeFolderRecoveryRequired) this.recovery={name:file.name,size:file.size};
      if (controller.signal.aborted && this.folder && isNativeFolder(this.folder)) this.folder=null;
      this.status.textContent = this.recovery ? String(error instanceof Error ? error.message : error) : controller.signal.aborted
        ? 'Import stopped. Choose the folder again if its native selection was released. If acceptance had already completed, the source remains in Project Data. Retry this same file to recover its status without duplicating it.'
        : String(error instanceof Error ? error.message : error);
    } finally {
      this.controller = null; this.picker.disabled = this.nativePicker.disabled = this.mode.disabled = this.axis.disabled = this.recovery!==null; this.start.disabled = this.saved !== null || (!this.folder && !this.selected && !this.recovery);
      this.start.textContent = this.recovery ? 'Check import outcome' : this.saved ? 'Source saved' : 'Retry saving source'; this.stop.hidden = true; this.progress.hidden = true;
    }
  }
  private async listSamples(): Promise<void> {
    this.samples.replaceChildren(); this.status.textContent = 'Checking the sample data installed readers declare…';
    try {
      const response = await fetch('/api/scientific-samples'), result: unknown = await response.json();
      if (!object(result) || !response.ok || result.ok !== true || !Array.isArray(result.samples)) throw new Error('The sample list could not be read. Check readers in Models, then retry.');
      const rows = result.samples.map(sample);
      if (rows.some(row => row === null)) throw new Error('Sample declarations changed. Choose Sample data again.');
      if (this.mode.value !== 'sample') return;
      this.status.textContent = rows.length ? 'Choose a sample. Hooke downloads its pinned bytes and refuses any that differ from what its reader declares.' : 'No installed reader declares sample data. Add a reader integration in Models.';
      for (const item of rows as Sample[]) {
        const row = node('article'), choose = node('button', `Download ${item.name}`);
        row.append(node('h4', item.name), node('p', `${item.reader} · ${item.size.toLocaleString()} bytes${item.licence ? ` · ${item.licence}` : ''}`));
        if (item.readerReason) row.append(node('p', item.readerReason));
        if (!item.canOpen) { row.append(node('p', item.reason)); choose.disabled = true; }
        choose.addEventListener('click', () => void this.download(item)); row.append(choose); this.samples.append(row);
      }
    } catch (error) { this.status.textContent = String(error instanceof Error ? error.message : error); }
  }
  private async download(chosen?: Sample): Promise<void> {
    const url = this.address.value.trim(), digest = chosen ? chosen.sha256 : this.digest.value.trim().toLowerCase();
    if (this.controller || (!chosen && !this.validAddress())) return;
    if (chosen) { // one request per sample, so a retry after a lost reply recovers the same source
      const key = `${chosen.capability}\u0000${chosen.id}`; this.identity = this.sampleRequests.get(key) ?? crypto.randomUUID(); this.sampleRequests.set(key, this.identity);
    }
    const controller = new AbortController(); this.controller = controller;
    this.address.disabled = true; this.digest.disabled = true; this.mode.disabled = true; this.start.disabled = true; this.stop.hidden = false; this.progress.hidden = false;
    this.status.textContent = `Downloading into ${this.options.title} and verifying the exact bytes…`;
    try {
      const response = chosen
        ? await fetch('/api/scientific-samples', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'open-scientific-sample' },
          body: JSON.stringify({ session: this.options.session, capability: chosen.capability, sample: chosen.id, request: this.identity }), signal: controller.signal })
        : await fetch('/api/scientific-urls', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'import-scientific-url' },
          body: JSON.stringify({ session: this.options.session, url, request: this.identity, sha256: digest || null, name: null }), signal: controller.signal });
      const result: unknown = await response.json();
      if (!object(result) || !response.ok || result.ok !== true) throw new Error(object(result) ? `${String(result.says ?? 'The source could not be downloaded.')} ${String(result.next ?? '')}` : 'The download returned an invalid response. Retry the same address to recover its saved status.');
      const project = result.project, document = object(project) ? project.document : null, state = object(document) ? document.state : null;
      const receipt = object(state) ? state.sourceRemote : null;
      if (!object(project) || typeof project.id !== 'string' || !/^p[0-9a-f]{32}$/.test(project.id) || project.session !== this.options.session || project.revision !== 1
          || !object(document) || document.kind !== 'source-file' || typeof document.title !== 'string' || !object(receipt)
          || (chosen ? !object(receipt.sample) || receipt.sample.capability !== chosen.capability || receipt.sample.id !== chosen.id : receipt.url !== url)
          || typeof receipt.sha256 !== 'string' || (digest && receipt.sha256 !== digest))
        throw new Error('The response does not match this address and Project. Check Project Data before retrying.');
      this.saved = { id: project.id, session: this.options.session, revision: 1, title: document.title };
      this.status.textContent = `Downloaded ${document.title} into ${this.options.title}. Its exact bytes are retained (SHA-256 ${receipt.sha256}). Reader readiness has not been established.`;
      this.openButton.hidden = false; this.options.saved();
    } catch (error) {
      this.status.textContent = controller.signal.aborted
        ? 'Download stopped. If it had already been accepted, the source remains in Project Data. Retry this same address to recover its status without downloading it again.'
        : String(error instanceof Error ? error.message : error);
    } finally {
      this.controller = null; this.address.disabled = false; this.digest.disabled = false; this.mode.disabled = false; this.start.disabled = this.saved !== null;
      this.start.textContent = this.saved ? 'Source saved' : 'Retry saving source'; this.stop.hidden = true; this.progress.hidden = true;
    }
  }
}
export function openScientificFileImport(options: Options): void {
  let current = imports.get(options.session);
  if (!current || current.finished()) { current = new ScientificFileImport(options); imports.set(options.session, current); }
  current.show();
}

export function showScientificSource(root: HTMLElement, state: unknown, reference: { id: string; day: string; version: number }, openReader?: (target:{id:string;version:number;title:string})=>Promise<void>, resources?:unknown): void {
  const source = object(state) ? state.sourceFile : null;
  if (!object(source) || source.format !== 1 || typeof source.name !== 'string' || !Number.isSafeInteger(source.size) || Number(source.size) < 0 || typeof source.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(source.sha256))
    throw new Error('The saved source metadata is invalid. Keep its original revision and retry.');
  const movie = renderedMovieDescription(root, object(state) ? state.movieExport : undefined);
  if (movie && !validMovieFile(source, {id:reference.id,session:reference.day}, resources)) throw new Error('The rendered movie has an incomplete retained file reference. Keep the saved document and check its storage.');
  root.append(node('h3', source.name), node('p', `${Number(source.size).toLocaleString()} bytes · ${movie ? 'rendered movie saved' : 'original source saved'}`));
  const receipt = object(state) ? state.sourceRemote : null;
  if (object(receipt) && typeof receipt.url === 'string' && typeof receipt.final === 'string' && typeof receipt.retrieved === 'string')
    root.append(node('p', `Downloaded from ${receipt.url}${receipt.final !== receipt.url ? ` (served by ${receipt.final})` : ''} on ${receipt.retrieved}. Web content is kept as exact bytes and never run.`));
  if (!movie) root.append(node('p', 'These original bytes are retained. A compatible reader integration is still needed to inspect, edit or process this source.'));
  const download = node('a', movie ? 'Download rendered movie' : 'Download original file');
  download.href = `/api/scientific-files/${encodeURIComponent(reference.id)}?${new URLSearchParams({ session: reference.day, revision: String(reference.version) })}`;
  download.download = source.name; download.className = 'scientific-source-download';
  const detail = node('details'); detail.append(node('summary', 'Content identity'), node('code', source.sha256));
  if(openReader && !movie)sourceReaders(root,reference,openReader);
  root.append(download, detail);
}


export function showScientificFolder(root: HTMLElement, state: unknown, reference: { id: string; day: string; version: number }, openReader: (target:{id:string;version:number;title:string})=>Promise<void>): void {
  const source = object(state) ? state.sourceFile : null, directory = object(state) ? state.sourceDirectory : null;
  if (!object(source) || typeof source.name !== 'string' || !Number.isSafeInteger(source.size) || Number(source.size) < 0
      || !object(directory) || directory.format !== 'source-directory-v1' || !Array.isArray(directory.entries) || !directory.entries.length || directory.entries.length > 4096)
    throw new Error('This folder format is unsupported. Keep its saved revision and use a compatible Hooke version.');
  let offset = 0;
  const entries = directory.entries.map(value => {
    if (!object(value) || typeof value.path !== 'string' || value.path.length > 512 || !value.path || value.path.split('/').some(part => !part || part === '.' || part === '..')
        || /[\\:\u0000-\u001f\u007f]/.test(value.path) || value.offset !== offset || !Number.isSafeInteger(value.size) || Number(value.size) < 0)
      throw new Error('The retained folder index is invalid. Keep its saved revision.');
    offset += Number(value.size); return { path: value.path, size: Number(value.size) };
  });
  if (offset !== source.size || new Set(entries.map(entry => entry.path)).size !== entries.length) throw new Error('The retained folder entries do not match their saved bytes.');
  const stack = object(state) ? state.sourceStack : null;
  const stacked = object(stack) && typeof stack.axis === 'string' && stack.count === entries.length;
  root.append(node('h3', source.name), node('p', stacked
    ? `${entries.length.toLocaleString()} files stacked along a new ${String(stack.axis)} axis, in the order listed · ${Number(source.size).toLocaleString()} bytes · original files retained`
    : `${entries.length.toLocaleString()} files · ${Number(source.size).toLocaleString()} bytes · original folder retained`));
  sourceReaders(root, reference, openReader);
  const detail = node('details'); detail.append(node('summary', 'Original files'));
  const list = node('ul');
  for (const entry of entries.slice(0, 50)) {
    const row = node('li'), download = node('a', entry.path);
    download.href = `/api/scientific-directories/${encodeURIComponent(reference.id)}/file?${new URLSearchParams({session:reference.day,revision:String(reference.version),path:entry.path})}`;
    download.download = entry.path.split('/').at(-1) ?? entry.path; row.append(download, node('span', ` · ${entry.size.toLocaleString()} bytes`)); list.append(row);
  }
  detail.append(list);
  if (entries.length > 50) detail.append(node('p', `Showing the first 50 of ${entries.length} retained files.`));
  root.append(detail);
}
