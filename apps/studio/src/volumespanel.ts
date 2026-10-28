// Brain volumes on the Models page (orchestrator/registrations.py; phase 3's E5): add a whole-brain volume (a TIFF or
// OME-TIFF), say what its file doesn't state (voxel sizes) and its orientation (BrainGlobe's three letters, never
// guessed), and register it to the Allen mouse atlas with brainreg: a job of minutes, cancelled whole, run again when it
// was interrupted, opened from here or Made in this session once done. The lists are the session's index's.

import { filePicker } from './file-picker';

/** A volume or a job as the session's index lists it (GET /api/resources?kind=volume|job). */
export interface VolumeEntry {
  id: string; kind: 'volume' | 'job'; title: string; state: string; summary: string; day?: string;
  values?: {
    facts?: Record<string, { value: unknown; source: string | null }>;
    series?: { axes: string; shape: number[]; channels?: number; times?: number }[];
    volume?: { id: string; name: string };
  };
}

/** Read one session's volumes and jobs, including the facts the picker needs. Every request keeps that owner even
 * when the user switches sessions while the list is loading. */
export async function listVolumes(session: string, request: typeof fetch = fetch): Promise<VolumeEntry[]> {
  if (!session) return [];
  const owner = encodeURIComponent(session);
  const read = async (kind: string) => {
    const response = await request(`/api/resources?kind=${kind}&day=${owner}`, { cache: 'no-store' });
    if (!response.ok) throw new Error(`the list could not be read (${response.status})`);
    const body = await response.json() as { entries: VolumeEntry[] };
    return body.entries.filter(e => e.day === session);
  };
  const [listed, jobs] = await Promise.all([read('volume'), read('job')]);
  const volumes = await Promise.all(listed.map(async (v) => {
    const response = await request(`/api/resources/${encodeURIComponent(v.id)}?day=${owner}`, { cache: 'no-store' });
    if (!response.ok) return v;
    const full = await response.json() as { detail?: { values?: VolumeEntry['values'] } };
    return { ...v, values: full.detail?.values ?? undefined };
  }));
  return [...volumes, ...jobs.reverse()];
}

export interface VolumeCommands {
  upload: (file: File) => Promise<{ ok: boolean; says?: string }>;
  /** Its reply's words say what was added (commands.py volume-add `says`); a stand-in may reply nothing. */
  add: (path: string) => Promise<VolumeAdded | undefined>;
  describe: (id: string, facts: VolumeFacts) => Promise<unknown>;
  start: (volume: string) => Promise<unknown>;
  cancel: (job: string) => Promise<unknown>;
  again: (job: string) => Promise<unknown>;
  open: (id: string) => void;
  /** Claimed as an action starts: whether the page still shows the session it was started in. A reply that comes after
   * a switch says nothing in another session (the Models page's; without it, always). */
  owner?: () => () => boolean;
}

/** What the server says of a volume added by its path (commands.py volume-add): the studio reads its words. */
export interface VolumeAdded { says?: string }

export interface VolumeFacts { session?: string; registration?: boolean; voxelUm?: number[]; orientation?: string; series?: number; channel?: number; time?: number }

/** What a volume still needs to be told before it can be registered (the server's `missing`, from its facts). A file of
 * several series is told which first: its channels, times and scale are that series' own (registrations.py). */
export function needs(entry: VolumeEntry, registration = true): { series: number; voxel: boolean; orientation: boolean; channels: number; times: number } {
  const facts = entry.values?.facts ?? {};
  const series = entry.values?.series ?? [];
  if (facts.series?.value == null && series.length > 1) {
    return { series: series.length, voxel: false, orientation: false, channels: 0, times: 0 };
  }
  const chosen = series[Number(facts.series?.value ?? 0)] ?? series[0];
  const channels = chosen?.channels ?? 1;
  const times = chosen?.times ?? 1;
  return { series: 0, voxel: !facts.voxel_um?.value, orientation: registration && !facts.orientation?.value,
    channels: channels > 1 && facts.channel?.value == null ? channels : 0,
    times: times > 1 && facts.time?.value == null ? times : 0 };
}

/** "" when the orientation is BrainGlobe's three letters (one of a/p, s/i, l/r), else what is wrong. */
export function orientationProblem(letters: string): string {
  const l = letters.trim().toLowerCase();
  if (!/^[apsilr]{3}$/.test(l)) return 'three letters, each one of a, p, s, i, l, r';
  const pairs = [...l].map((c) => ['ap', 'si', 'lr'].find((p) => p.includes(c))).sort().join();
  return pairs === 'ap,lr,si' ? '' : 'each direction once: one of a/p, one of s/i, one of l/r';
}

/** What a job's row offers: Cancel while it runs, Run again when it ended without a result, Open once done. */
export function jobActions(entry: VolumeEntry): ('cancel' | 'again' | 'open')[] {
  if (entry.state === 'running') return ['cancel'];
  if (entry.state === 'done') return ['open'];
  return ['again'];
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string | boolean> = {},
  ...children: (Node | string | null | undefined | false)[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false) continue;
    if (k === 'class') node.className = String(v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

function button(label: string, act: () => Promise<unknown> | void, said: HTMLElement): HTMLButtonElement {
  const b = el('button', { type: 'button', class: 'model-fact-act' }, label);
  b.addEventListener('click', () => {
    b.disabled = true;
    void Promise.resolve(act()).catch((error: unknown) => {
      said.textContent = error instanceof Error ? error.message : String(error);
      said.hidden = false;
    }).finally(() => { b.disabled = false; });
  });
  return b;
}

const ADDED_FOR_TOOLS = 'Volume added. Complete its missing facts below, then select it in a tool’s details.';

export function volumesSection(entries: VolumeEntry[], commands: VolumeCommands, words: string,
  setWords: (words: string) => void, registration = true): HTMLElement {
  const said = el('p', { class: 'models-own-said', role: 'status', hidden: !words }, words);
  // Claimed when an action starts, not when this is drawn: the page keeps these forms across its renders, a session
  // switch included, so a form drawn in one session may be submitted in the next (modelspage.ts render's editors).
  const claim = () => commands.owner?.() ?? (() => true);
  const file = el('input', { type: 'file', accept: '.tif,.tiff', 'aria-label': 'A volume (TIFF or OME-TIFF)' }) as HTMLInputElement;
  // The studio's own control, never the browser's raw "Choose File"; kept whole across the page's redraws (its editors).
  const picker = filePicker(file, 'Choose a volume', 'No volume chosen');
  picker.setAttribute('data-model-editor', 'volume-file');
  file.addEventListener('change', () => {
    const chosen = file.files?.[0];
    if (!chosen) return;
    const still = claim();
    setWords(`Adding ${chosen.name}… reading the stack's stated facts`);
    void commands.upload(chosen).then((r) => { if (still()) setWords(!r.ok ? r.says ?? 'The volume could not be added.'
      : registration ? r.says ?? 'Added.' : ADDED_FOR_TOOLS); })
      .catch((e: unknown) => { if (still()) setWords(e instanceof Error ? e.message : String(e)); });
  });
  const path = el('input', { type: 'text', class: 'mm-control', placeholder: 'or its path on this Mac', 'aria-label': 'A volume’s path on this Mac', spellcheck: 'false' }) as HTMLInputElement;
  const addForm = el('form', { class: 'models-own-form', 'data-model-editor': 'volume-add' }, path,
    el('button', { type: 'submit', class: 'mm-control' }, 'Add volume'));
  addForm.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!path.value.trim()) return;
    const still = claim();
    void commands.add(path.value.trim()).then((reply) => {
      // An added volume replaces what an earlier try said, as a chosen file's does: a refusal ("brainreg isn't installed
      // here yet") left under the volume it no longer applies to read as if the add had failed (Window C, 2026-10-14).
      // Only in the session it was added to: its volume is that session's (the Spec review of 87ff1e59), and the form,
      // kept across a switch, may by then hold a path typed in the next session, which is left as it is.
      if (!still()) return;
      path.value = '';
      const says = reply?.says;
      setWords(registration ? (typeof says === 'string' && says ? says : 'Added.') : ADDED_FOR_TOOLS);
    }).catch((e: unknown) => { if (still()) setWords(e instanceof Error ? e.message : String(e)); });
  });
  const volumes = entries.filter((e) => e.kind === 'volume');
  const jobs = entries.filter((e) => e.kind === 'job');
  const volumeRows = volumes.map((v) => {
    const need = needs(v, registration);
    const series = v.values?.series?.[Number(v.values.facts?.series?.value ?? 0)];
    const summary = registration ? v.summary : series ? `${series.shape.join(' × ')} (${series.axes})` : 'Volume facts are being read.';
    const row = el('li', { class: 'volume-row', 'data-volume': v.id }, el('strong', {}, v.title), el('p', { class: 'model-what' }, summary));
    if (need.series || need.voxel || need.orientation || need.channels || need.times) {
      const listed = v.values?.series ?? [];
      const seriesPick = el('select', { class: 'mm-control', 'aria-label': 'Series' }, ...listed.map((x, i) =>
        el('option', { value: String(i) }, `series ${i}: ${x.shape.join(' × ')} (${x.axes})`))) as HTMLSelectElement;
      const timePick = el('select', { class: 'mm-control', 'aria-label': 'Time point' }, ...Array.from({ length: need.times }, (_, i) => el('option', { value: String(i) }, `time ${i}`))) as HTMLSelectElement;
      const z = el('input', { type: 'number', step: 'any', min: '0', class: 'mm-control volume-voxel', placeholder: 'z µm', 'aria-label': 'Voxel size z (µm)' }) as HTMLInputElement;
      const y = el('input', { type: 'number', step: 'any', min: '0', class: 'mm-control volume-voxel', placeholder: 'y µm', 'aria-label': 'Voxel size y (µm)' }) as HTMLInputElement;
      const x = el('input', { type: 'number', step: 'any', min: '0', class: 'mm-control volume-voxel', placeholder: 'x µm', 'aria-label': 'Voxel size x (µm)' }) as HTMLInputElement;
      const orient = el('input', { type: 'text', maxlength: '3', class: 'mm-control volume-orientation', placeholder: 'psl', 'aria-label': 'Orientation: BrainGlobe’s three letters', spellcheck: 'false' }) as HTMLInputElement;
      const channel = el('select', { class: 'mm-control', 'aria-label': 'Channel' }, ...Array.from({ length: need.channels }, (_, i) => el('option', { value: String(i) }, `channel ${i}`))) as HTMLSelectElement;
      const form = el('form', { class: 'volume-facts',
        'data-model-editor': JSON.stringify(['volume', v.day, v.id, registration, v.values?.facts, v.values?.series]) },
        need.series ? el('span', {}, 'Series: ', seriesPick, el('small', {}, ' its scale, channels and times are its own')) : null,
        need.voxel ? el('span', {}, 'Voxel sizes (µm, z y x): ', z, y, x) : null,
        need.orientation ? el('span', {}, 'Orientation: ', orient, el('small', {}, ' BrainGlobe’s letters, axis 0 first (psl: towards posterior, superior, left)')) : null,
        need.channels ? el('span', {}, 'Channel: ', channel) : null,
        need.times ? el('span', {}, 'Time point: ', timePick) : null,
        el('button', { type: 'submit', class: 'mm-control' }, 'Save'));
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        const facts: VolumeFacts = { ...(v.day ? { session: v.day } : {}), ...(!registration ? { registration: false } : {}) };
        if (need.series) facts.series = Number(seriesPick.value);
        if (need.voxel) facts.voxelUm = [z, y, x].map((i) => Number(i.value));
        if (need.orientation) {
          const why = orientationProblem(orient.value);
          if (why) { setWords(`Orientation: ${why}`); return; }
          facts.orientation = orient.value.trim().toLowerCase();
        }
        if (need.channels) facts.channel = Number(channel.value);
        if (need.times) facts.time = Number(timePick.value);
        const still = claim();
        void commands.describe(v.id, facts).catch((e: unknown) => { if (still()) setWords(e instanceof Error ? e.message : String(e)); });
      });
      row.append(form);
    } else if (registration) {
      row.append(button('Register to the Allen atlas', () => commands.start(v.id), said));
    } else {
      row.append(el('p', {}, 'Ready for volume tools. Select this volume in a tool’s details.'));
    }
    return row;
  });
  const jobRows = jobs.map((j) => el('li', { class: 'volume-job', 'data-job': j.id, 'data-state': j.state },
    el('strong', {}, j.title), el('span', { class: 'volume-job-state' }, j.state), el('p', { class: 'model-what' }, j.summary),
    ...jobActions(j).map((a) => a === 'cancel' ? button('Cancel', () => commands.cancel(j.id), said)
      : a === 'again' ? button('Run again', () => commands.again(j.id), said)
        : button('Open', () => commands.open(j.id), said))));
  return el('section', { class: 'models-own volumes-section', 'aria-labelledby': 'models-volumes-title' },
    el('h3', { id: 'models-volumes-title' }, registration ? 'Brain volumes (brainreg)' : 'Volumes'),
    el('p', {}, registration ? 'Register a whole brain imaged as a volume (a TIFF or OME-TIFF) to the Allen mouse brain atlas at 25 µm. '
      + 'It runs as a job of several minutes in brainreg’s own Python; its result is shown in brainreg’s own downsampled space.'
      : 'Add a TIFF or OME-TIFF stack, then provide the scale, series, channel and time point its file does not state. Select it in a volume tool’s details to run that tool.'),
    el('div', { class: 'models-own-form' }, picker), addForm,
    volumeRows.length ? el('ul', { class: 'volume-list' }, ...volumeRows) : null,
    jobRows.length ? el('ul', { class: 'volume-jobs' }, ...jobRows) : null, said);
}
