/** Layer legends use the same measured facts and palette as the canvas. No inferred names or units. */
import type { View } from './view';
import type { MeasurementPin } from './measurements';
import { bindParticleExport } from './particle-export';
import { bindPopulationNative } from './population-native';
import type { ClientCommand } from './protocol';
import { tracksLabel } from './motiontracks';
import { colourChoice, categoryColour, categoryWords, heat, type LayerStyle, type LayerWire } from './population';
import type { TracksStyle } from './motiontracks';

export function legendFacts(style: LayerStyle | TracksStyle): { label: string; colour?: string; ramp?: boolean }[] {
  const legend = style.legend;
  if (style.colour === 'category' && legend && 'categories' in legend && legend.categories) {
    return [{ label: legend.label }, ...legend.categories.map((category, index) => ({ label: categoryWords(category), colour: categoryColour(index) }))];
  }
  if (style.colour !== 'column' || !legend || !Number.isFinite(legend.min) || !Number.isFinite(legend.max)) return [];
  const n = (v: number) => Math.abs(v) >= 100 ? Math.round(v).toString() : Number(v.toPrecision(3)).toString();
  return [{ label: `${legend.label}: ${n(legend.min!)}–${n(legend.max!)} ${legend.unit ?? ''}`.trim(), ramp: true }];
}
export function renderLegend(host: HTMLElement, style: LayerStyle | TracksStyle): void {
  const facts = legendFacts(style), stamp = JSON.stringify(facts);
  if (host.dataset.legend === stamp) return;
  host.dataset.legend = stamp; host.hidden = !facts.length; host.replaceChildren();
  for (const fact of facts) {
    const item = document.createElement('span'); item.className = 'legend-item';
    if (fact.colour || fact.ramp) {
      const swatch = document.createElement('span'); swatch.setAttribute('aria-hidden', 'true');
      swatch.className = fact.ramp ? 'legend-ramp' : 'legend-swatch';
      swatch.style.background = fact.colour ?? `linear-gradient(90deg, ${Array.from({length: 9}, (_, i) => heat(i / 8)).join(', ')})`;
      item.append(swatch);
    }
    item.append(document.createTextNode(fact.label)); host.append(item);
  }
}

/** A display hint only. Unaligned or historical timestamps never fabricate a fresh change. */
export function assistantChangeRemaining(change: { by: string; at: number } | undefined, now: number): number {
  const age = change ? now - change.at : Infinity;
  return change?.by === 'assistant' && Number.isFinite(age) && age >= 0 && age < 6 ? 6 - age : 0;
}

/** A layer this card showed, as its earlier-layers list names it. */
export interface EarlierLayer { id: string; session: string; title: string; count: number; model: string }
/** How many earlier layers the card lists. */
export const EARLIER_MOST = 3;
/** Remember the population the card shows now, newest first (each once; the newest few). */
export function rememberLayer(list: EarlierLayer[], pop: { id: string; session?: string; title?: string; count: number }, model: string): EarlierLayer[] {
  const row = { id: pop.id, session: pop.session ?? '', title: pop.title ?? pop.id, count: pop.count, model };
  const first = list[0];
  if (first && first.id === row.id && first.count === row.count && first.model === row.model && first.title === row.title) return list;
  return [row, ...list.filter((r) => r.id !== row.id)].slice(0, EARLIER_MOST + 1);
}
/** "Outlines · 15 by Cellpose-SAM", as the card's own line names the layer shown. */
export const earlierWords = (row: EarlierLayer): string => `Outlines · ${row.count}${row.model ? ` by ${row.model}` : ''}`;
/** Where a Project's earlier layers are kept in this browser, so the list survives a reload (per Project; optional). */
export const EARLIER_KEY = 'micromind-earlier-layers:';
/** A Project's earlier layers as kept, newest first; none when browser storage is unavailable or holds anything else. */
export function loadEarlier(session: string): EarlierLayer[] {
  try {
    const rows: unknown = JSON.parse(localStorage.getItem(EARLIER_KEY + session) ?? '[]');
    if (!Array.isArray(rows)) return [];
    return rows.filter((r): r is EarlierLayer => !!r && typeof r === 'object' && typeof r.id === 'string' && r.session === session
      && typeof r.title === 'string' && Number.isFinite(r.count) && typeof r.model === 'string').slice(0, EARLIER_MOST + 1);
  } catch { return []; }
}
export function saveEarlier(session: string, rows: EarlierLayer[]): void {
  try { localStorage.setItem(EARLIER_KEY + session, JSON.stringify(rows)); } catch { /* browser storage is optional */ }
}

/** Bind the production layer controls to typed scene facts and an existing command sender. */
export function bindLayerControls(view: Pick<View, 'layer' | 'population' | 'tracksLayer' | 'shownTracks' | 'externalLegends'>,
  send: (command: ClientCommand) => void,
  correct?: (command: Extract<ClientCommand, {type: 'population-correct'}>) => Promise<{ok: boolean; error?: string | null; summary?: string}>,
  openTable: (id: string, pin?: MeasurementPin) => void = () => {},
  /** The model that made a population, by its name (tools-catalog.ts madeByName): the Stage chip names it. */
  modelName: (madeBy: Record<string, unknown> | undefined) => string = () => '',
  openPopulation?: (id: string, pin: MeasurementPin) => void
): {syncCells: () => void; syncTracks: () => void} {
  const element = <T extends HTMLElement>(id: string) => document.getElementById(id)! as T;
// The cells' layer, with the verbs the brain uses, as the user's own change (screen.py "present").
const layerControls = element<HTMLElement>('layer-controls');
const layerVisible = element<HTMLButtonElement>('layer-visible');
const layerOpacity = element<HTMLInputElement>('layer-opacity');
const layerColour = element<HTMLSelectElement>('layer-colour');
const layerFill = element<HTMLButtonElement>('layer-fill');
const layerName = document.createElement('span'); layerName.className = 'layer-name'; layerControls.prepend(layerName);
const layerLegend = document.createElement('div'); layerLegend.className = 'layer-legend'; layerLegend.hidden = true; layerControls.append(layerLegend);
view.externalLegends = true;
const layerSetBy = document.createElement('span'); layerSetBy.className = 'layer-set-by'; layerSetBy.hidden = true; layerControls.append(layerSetBy);
let layerSetByTimer: ReturnType<typeof setTimeout> | null = null, layerSetByStamp = '';
let lastPopulation: string | null = null; // the population shown last (shown again by "Cells shown")
// Run again adds a layer (Will's call, 2026-10-25): the layers this card showed before stay listed under it, newest
// first, each shown again with one click (the layer draws one population at a time; the server's `present` show). Kept
// per Project in this browser (loadEarlier), so a reload, another Tab or page, and coming back keep the list.
const earlierLayers = document.createElement('div'); earlierLayers.className = 'layer-earlier'; earlierLayers.hidden = true;
earlierLayers.setAttribute('role', 'list'); earlierLayers.setAttribute('aria-label', 'Earlier outlines');
const earlierBySession = new Map<string, EarlierLayer[]>();
let earlierStamp = '', currentSession = '';
const earlierOf = (session: string): EarlierLayer[] => {
  let rows = earlierBySession.get(session);
  if (!rows) { rows = loadEarlier(session); earlierBySession.set(session, rows); }
  return rows;
};
const corrections = document.createElement('div'); corrections.className = 'population-corrections';
const memberSelect = document.createElement('select'); memberSelect.className = 'member-select';
memberSelect.setAttribute('aria-label', 'Member to remove');
const removeMember = document.createElement('button'); removeMember.type = 'button'; removeMember.className = 'member-remove'; removeMember.textContent = 'Remove member';
const undoMember = document.createElement('button'); undoMember.type = 'button'; undoMember.className = 'member-undo'; undoMember.textContent = 'Undo correction';
const originalView = document.createElement('button'); originalView.type = 'button'; originalView.className = 'population-original'; originalView.textContent = 'Original';
const currentView = document.createElement('button'); currentView.type = 'button'; currentView.className = 'population-current'; currentView.textContent = 'Current';
const correctionState = document.createElement('span'); correctionState.className = 'correction-state'; correctionState.setAttribute('role', 'status');
corrections.append(memberSelect, removeMember, undoMember, originalView, currentView, correctionState);
// Looking first: the card shows the outlines and how they are drawn; correcting members, the original and current
// revisions, export and retaining labels are one click away, under Correct and export (every control kept).
const correctAndExport = document.createElement('details'); correctAndExport.className = 'layer-more';
const correctSummary = document.createElement('summary'); correctSummary.textContent = 'Correct and export';
correctAndExport.append(correctSummary, corrections); layerControls.append(correctAndExport);
const exchange = bindParticleExport(corrections, () => view.layer?.population === view.population?.id ? view.population : null);
const nativeLabels = bindPopulationNative(corrections, () => view.layer?.population === view.population?.id ? view.population : null,
  async target => { const {openStudyScientific} = await import('./scientific-document');
    await openStudyScientific({resource:{id:target.id,day:target.session,version:target.revision,picture:null},title:target.title,zoom:1,pan:[.5,.5]}); });
let memberStamp = '', correcting = false, correctionError = '';
async function correctMember(change: 'remove' | 'undo'): Promise<void> {
  const pop = view.population;
  if (!pop?.session || !correct || correcting || pop.status !== 'saved' || view.layer?.population !== pop.id) return;
  const member = memberSelect.value;
  if (change === 'remove' && !pop.members.some(row => row.id === member)) return;
  correcting = true; correctionError = ''; syncCells();
  try {
    const reply = await correct({type: 'population-correct', session: pop.session, population: pop.id, revision: pop.revision,
      change, ...(change === 'remove' ? {member} : {})});
    if (!reply.ok) throw new Error(reply.error ?? reply.summary ?? 'The correction could not be made');
    // Only refresh the result the user is still inspecting. A later selection stays theirs.
    if (view.layer?.population === pop.id && view.population?.session === pop.session)
      send({type: 'present', what: pop.id, do: 'open', revision: pop.revision + 1});
  } catch (error) { correctionError = (error as Error).message; }
  finally { correcting = false; syncCells(); }
}
memberSelect.addEventListener('change', syncCells);
removeMember.addEventListener('click', () => { void correctMember('remove'); });
undoMember.addEventListener('click', () => { void correctMember('undo'); });
function showPopulation(original: boolean): void {
  const pop=view.population,target=tableTarget;
  if(correcting||!target||pop?.status!=='saved'||pop.id!==target.id||pop.session!==target.session||
    pop.revision!==target.revision||view.layer?.population!==target.id||original&&pop.originalCount==null)return;
  const revision=original?0:target.revision;
  if(openPopulation)openPopulation(target.id,{session:target.session,revision});
  else send({type:'present',what:target.id,do:'open',revision});
}
originalView.addEventListener('click',()=>showPopulation(true));
currentView.addEventListener('click',()=>showPopulation(false));
// Its ×: hides the cells and closes the control until cells are shown again ("show the cells"); the control also closes
// itself once its population is gone (the user's notes of 2026-10-11: it stayed after the cells it was for had gone).
const layerClose = document.createElement('button');
layerClose.type = 'button'; layerClose.className = 'layer-close'; layerClose.setAttribute('aria-label', 'Hide the outlines and close this');
layerClose.title = 'Hide the outlines (open the saved result to bring them back)';
// Its Table: every cell's numbers to sort and download (the Measurements page).
const layerTable = document.createElement('button');
layerTable.type = 'button'; layerTable.className = 'layer-table'; layerTable.textContent = 'Table';
layerTable.title = "Measurements of each member, to sort and download as CSV";
let tableTarget: (MeasurementPin & {id: string}) | null = null;
layerTable.addEventListener('click', () => {
  const pop = view.population, target = tableTarget;
  if (!target || pop?.status !== 'saved' || pop.id !== target.id || pop.session !== target.session ||
      pop.revision !== target.revision || (view.layer?.population && view.layer.population !== target.id)) return;
  openTable(target.id, {session: target.session, revision: target.revision});
});
layerControls.append(layerTable, layerClose, earlierLayers);
layerClose.addEventListener('click', () => {
  if (view.layer?.population) send({ type: 'present', what: `layer:${view.layer.population}`, do: 'hide' });
  lastPopulation = null;
  layerControls.hidden = true;
});
function syncCells(): void {
  exchange.sync();
  nativeLabels.sync();
  const layer = view.layer;
  const change = layer?.lastChange, stamp = JSON.stringify([layer?.population, change]);
  if (stamp !== layerSetByStamp) {
    layerSetByStamp = stamp;
    if (layerSetByTimer !== null) clearTimeout(layerSetByTimer);
    const remaining = assistantChangeRemaining(change, Date.now() / 1000);
    layerSetBy.hidden = remaining === 0;
    layerSetBy.textContent = 'set by HookeUI';
    if (!layerSetBy.hidden) layerSetByTimer = setTimeout(() => { layerSetBy.hidden = true; }, remaining * 1000);
  }
  if (layer?.population && view.population?.id === layer.population) {
    const pop = view.population, session = pop.session ?? '';
    const before = earlierOf(session), after = rememberLayer(before, pop, modelName(pop.madeBy));
    if (after !== before) { earlierBySession.set(session, after); if (session) saveEarlier(session, after); }
    currentSession = session;
  }
  if (layer?.population) lastPopulation = layer.population;
  else if (!view.population) lastPopulation = null; // its population is gone: nothing left for it to control
  layerControls.hidden = !lastPopulation;
  renderEarlier();
  const shown = !!layer?.population;
  const count = view.population?.count;
  const pop = view.population;
  tableTarget = pop?.session && pop.status === 'saved' && pop.id === lastPopulation &&
    Number.isSafeInteger(pop.revision) && pop.revision >= 0
    ? {id: pop.id, session: pop.session, revision: pop.revision} : null;
  layerTable.disabled = !tableTarget;
  corrections.hidden = !shown || !pop?.session; correctAndExport.hidden = corrections.hidden;
  const stampMembers = JSON.stringify([pop?.id, pop?.revision, pop?.members]);
  if (stampMembers !== memberStamp) {
    const previous = memberSelect.value;
    memberStamp = stampMembers;
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Choose member…';
    memberSelect.replaceChildren(empty, ...(pop?.members ?? []).map(member => {
      const option = document.createElement('option'); option.value = member.id;
      option.textContent = `${member.id}${typeof member.measure?.areaPx === 'number' ? ` · ${member.measure.areaPx} px²` : ''}`;
      return option;
    }));
    memberSelect.value = pop?.members?.some(member => member.id === previous) ? previous : '';
  }
  memberSelect.disabled = correcting || pop?.status !== 'saved';
  removeMember.disabled = memberSelect.disabled || !memberSelect.value || !correct;
  undoMember.disabled = memberSelect.disabled || !pop?.canUndo || !correct;
  originalView.disabled = correcting || !tableTarget || pop?.originalCount == null;
  currentView.disabled = correcting || !tableTarget;
  originalView.textContent = `Original${pop?.originalCount != null ? ` (${pop.originalCount})` : ''}`;
  currentView.textContent = `Current${count !== undefined ? ` (${count})` : ''}`;
  // The saved state in words, never the server's raw status (Saved only once the journal filed it).
  const revision = pop?.revision ?? 0, status = pop?.status ?? '';
  const savedWords = status === 'saved' ? `Saved · version ${revision}` : status === 'saving' ? `Saving version ${revision}…` : status === 'failed' ? `Version ${revision} not saved` : status ? `Version ${revision} · ${status}` : '';
  correctionState.textContent = correctionError || (correcting ? 'Applying correction…' : savedWords);
  memberSelect.title = memberSelect.disabled && !correcting && status !== 'saved' ? 'Corrections wait until this version is saved.' : '';
  const model = pop ? modelName(pop.madeBy) : ''; // the model is named on the Stage too
  // Its title, then one quiet line: how many and by which model ("341 by Cellpose-SAM"), or that they are hidden.
  const sub = shown ? [count !== undefined ? String(count) : '', model ? `by ${model}` : ''].filter(Boolean).join(' ') : `Hidden${model ? `, by ${model}` : ''}`;
  // Shown but none of them drawn on the Stage: the card says so and why (the server's reason), never only the count.
  const notDrawn = shown ? notDrawnWords(layer) : '';
  layerName.replaceChildren(Object.assign(document.createElement('b'), { className: 'layer-title', textContent: 'Outlines' }), ' ',
    Object.assign(document.createElement('span'), { className: 'layer-sub', textContent: sub }),
    ...(notDrawn ? [Object.assign(document.createElement('span'), { className: 'layer-sub layer-why', textContent: notDrawn })] : []));
  layerName.title = notDrawn || (shown ? `${layer?.verified.length ?? 0} of ${count ?? 'unknown'} verified in place` : 'Hidden; retained on the captured view');
  layerLegend.hidden = !shown;
  if (shown && layer) renderLegend(layerLegend, layer.style);
  layerVisible.setAttribute('aria-pressed', String(shown));
  // A toggle keeps one name; its pressed state says whether outlines are shown (the label once flipped between a
  // state, "Outlines enabled", and an action, "Show outlines").
  layerVisible.textContent = 'Show outlines';
  layerVisible.title = shown ? 'Outlines are shown. Click to hide them; they stay retained.' : 'Outlines are hidden. Click to show them.';
  for (const control of [layerOpacity, layerColour, layerFill]) control.disabled = !shown;
  if (shown && layer) {
    if (document.activeElement !== layerOpacity) layerOpacity.value = String(layer.style.opacity ?? 1);
    const s = layer.style;
    const column = colourChoice(s);
    // A colouring the menu has no entry for (by meanRed, by a column the brain chose) shows none, never the last pick.
    if ([...layerColour.options].some((o) => o.value === column)) layerColour.value = column;
    else layerColour.selectedIndex = -1;
    layerFill.setAttribute('aria-pressed', String(!!s.fill));
  }
}
/** The earlier layers under the card: those of the current one's Project, not the current one. */
function renderEarlier(): void {
  const earlier = lastPopulation ? earlierOf(currentSession) : [];
  const current = earlier.find((row) => row.id === lastPopulation);
  const rows = current ? earlier.filter((row) => row.id !== lastPopulation).slice(0, EARLIER_MOST) : [];
  const stamp = JSON.stringify(rows);
  if (stamp === earlierStamp) return;
  earlierStamp = stamp;
  earlierLayers.hidden = !rows.length;
  earlierLayers.replaceChildren(...rows.map((row) => {
    const item = document.createElement('div'); item.className = 'layer-earlier-row'; item.setAttribute('role', 'listitem');
    const words = document.createElement('span'); words.className = 'layer-sub';
    words.textContent = earlierWords(row); words.title = row.title;
    const show = document.createElement('button'); show.type = 'button'; show.className = 'layer-earlier-show'; show.textContent = 'Show';
    show.setAttribute('aria-label', `Show ${earlierWords(row)}`);
    show.title = 'Show these outlines instead (the ones shown now stay listed here)';
    show.addEventListener('click', () => send({ type: 'present', what: row.id, do: 'show' }));
    item.append(words, show);
    return item;
  }));
}
const layerTarget = () => (lastPopulation ? `layer:${lastPopulation}` : 'the cells');
layerVisible.addEventListener('click', () => {
  const shown = !!view.layer?.population;
  send({ type: 'present', what: shown ? layerTarget() : lastPopulation ?? 'the cells', do: shown ? 'hide' : 'show' });
});
layerOpacity.addEventListener('change', () => send({ type: 'present', what: layerTarget(), do: 'opacity', value: Number(layerOpacity.value) }));
layerColour.addEventListener('change', () => send({ type: 'present', what: layerTarget(), do: 'colour', value: layerColour.value }));
layerFill.addEventListener('click', () => send({ type: 'present', what: layerTarget(),
  do: layerFill.getAttribute('aria-pressed') === 'true' ? 'edge' : 'fill' }));
// The track layer uses the same explicit present verbs as cells; hiding never discards its evidence.
const tracksControls = document.createElement('div'); tracksControls.className = 'layer-pill'; tracksControls.hidden = true;
tracksControls.setAttribute('role', 'group'); tracksControls.setAttribute('aria-label', 'Motion tracks');
const tracksVisible = document.createElement('button'); tracksVisible.type = 'button'; tracksVisible.className = 'layer-eye';
const tracksName = document.createElement('span'); tracksName.className = 'layer-name';
const tracksFade = document.createElement('label'); tracksFade.className = 'layer-fade'; tracksFade.textContent = 'Fade ';
const tracksOpacity = document.createElement('input'); tracksOpacity.type = 'range'; tracksOpacity.min = '0.1'; tracksOpacity.max = '1'; tracksOpacity.step = '0.05'; tracksOpacity.setAttribute('aria-label', 'How strongly the motion tracks are drawn'); tracksFade.append(tracksOpacity);
const tracksLegend = document.createElement('div'); tracksLegend.className = 'layer-legend';
const tracksContext = document.createElement('span'); tracksContext.className = 'layer-context';
const tracksSetBy = document.createElement('span'); tracksSetBy.className = 'layer-set-by'; tracksSetBy.hidden = true;
const tracksTable = document.createElement('button'); tracksTable.type = 'button'; tracksTable.className = 'layer-table';
tracksTable.textContent = 'Table'; tracksTable.title = "Every track's speed and straightness, to sort and download as CSV";
tracksTable.addEventListener('click', () => { const id = view.shownTracks?.id ?? lastTracks; if (id) openTable(id); });
tracksControls.append(tracksVisible, tracksName, tracksFade, tracksContext, tracksLegend, tracksTable, tracksSetBy);
layerControls.after(tracksControls);
let lastTracks: string | null = null;
let tracksChangeStamp = '', tracksChangeTimer: ReturnType<typeof setTimeout> | null = null;
function syncTracks(): void {
  const layer = view.tracksLayer, tracks = view.shownTracks;
  const stamp = JSON.stringify([layer?.tracks, layer?.lastChange]);
  if (stamp !== tracksChangeStamp) {
    tracksChangeStamp = stamp;
    if (tracksChangeTimer !== null) clearTimeout(tracksChangeTimer);
    const remaining = assistantChangeRemaining(layer?.lastChange, Date.now() / 1000);
    tracksSetBy.hidden = remaining === 0; tracksSetBy.textContent = 'set by HookeUI';
    if (remaining) tracksChangeTimer = setTimeout(() => { tracksSetBy.hidden = true; }, remaining * 1000);
  }
  if (layer?.tracks) lastTracks = layer.tracks;
  tracksControls.hidden = !lastTracks;
  const shown = !!layer?.tracks;
  tracksVisible.setAttribute('aria-pressed', String(shown));
  tracksVisible.setAttribute('aria-label', shown ? 'Hide motion tracks' : 'Show motion tracks');
  tracksVisible.title = shown ? 'Hide motion tracks' : 'Show motion tracks';
  tracksName.textContent = `Tracks${tracks ? ` · ${tracks.count}` : ''}`;
  tracksName.title = tracks?.title ?? 'Retained motion tracks';
  tracksOpacity.disabled = !shown;
  tracksContext.textContent = shown && tracks ? tracksLabel(tracks) : 'Hidden';
  tracksContext.title = layer?.why ?? '';
  tracksLegend.hidden = !shown;
  if (shown && layer) {
    if (document.activeElement !== tracksOpacity) tracksOpacity.value = String(layer.style.opacity);
    renderLegend(tracksLegend, layer.style);
  }
}
tracksVisible.addEventListener('click', () => { if (lastTracks) send({ type: 'present', what: view.tracksLayer?.tracks ? `tracks:${lastTracks}` : lastTracks, do: view.tracksLayer?.tracks ? 'hide' : 'show' }); });
tracksOpacity.addEventListener('change', () => { if (lastTracks) send({ type: 'present', what: `tracks:${lastTracks}`, do: 'opacity', value: Number(tracksOpacity.value) }); });
  return {syncCells, syncTracks};
}

/** Why shown outlines are not drawn on the Stage, when none of them is checked in place there and the server said why:
 *  where they are kept as itself ("Kept on the original image; open the saved result…"), any other reason as a cause
 *  ("Not drawn on the Stage: the picture on the Stage is changing."). '' while any is drawn or no reason is given. */
export function notDrawnWords(layer: Pick<LayerWire, 'population' | 'verified' | 'why'> | null | undefined): string {
  const why = layer?.population && !layer.verified?.length ? (layer.why ?? '').trim() : '';
  if (!why) return '';
  return /^kept\b/i.test(why) ? `${why[0].toUpperCase()}${why.slice(1)}.` : `Not drawn on the Stage: ${why}.`;
}
