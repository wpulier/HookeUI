import {scientificViewPreferences,type ScientificViewPreferences} from './scientific-view-preferences';
export interface StudyResourceReference {
  day: string;
  id: string;
  version: number | null;
  picture: number | null;
  /** A retained research output has its own identity, separate from findings and image order. */
  outputId?: string;
}
export interface StudyRetainedView {
  placementId?: string;
  geometryUnavailable?: boolean;
  coordinates?: 'stage';
  display?: {background?:boolean;sort?:string;direction?:string;reading?:number;scientific?:ScientificViewPreferences};
  presentation?: 'masks' | 'table' | 'capture' | 'datatable' | 'analysis' | 'graph';
  resource: StudyResourceReference;
  title: string;
  zoom: number;
  pan: number[];
  position?: number[];
  size?: number[] | null;
}
export interface StudyPresentation {
  schema: 1;
  session: string;
  source: string | null;
  position: number;
  paused: boolean;
  wholeFrame: boolean;
  conversation: string | null;
  draft: string;
  reading: number;
  views: StudyRetainedView[];
  missing: string[];
}
export interface StudyRecovery { recovered: boolean; missing: string[] }

/** Ephemeral presentation intent; never saved as scientific or layout state. */
export interface StudyOpenOptions { background?: boolean; focus?: boolean }

/** HTTP-backed documents do not depend on the native presentation handshake. */
export async function restoreStudyItem(item:StudyRetainedView,renderers:{
 scientific:(item:StudyRetainedView, options?:StudyOpenOptions)=>void|Promise<void>;text:(item:StudyRetainedView, options?:StudyOpenOptions)=>void|Promise<void>;capture:(item:StudyRetainedView, options?:StudyOpenOptions)=>void|Promise<void>;
 table:(item:StudyRetainedView, options?:StudyOpenOptions)=>void|Promise<void>;masks:(item:StudyRetainedView, options?:StudyOpenOptions)=>void|Promise<void>;
 nativeReady:()=>Promise<void>;native:(item:StudyRetainedView)=>Promise<void>;
},options:StudyOpenOptions={background:true}):Promise<void>{
 if(!item.presentation&&item.resource.picture===null&&/^p[0-9a-f]{32}$/.test(item.resource.id))return renderers.scientific(item,options);
 if(!item.presentation&&item.resource.picture===null&&/^d\d+$/.test(item.resource.id))return renderers.text(item,options);
 if(item.presentation==='analysis'||item.presentation==='graph'){const {openTableDerived}=await import('./datatable-derived-presentation');return openTableDerived(item,options);}
 if(item.presentation==='datatable'){const {openStudyDataTable}=await import('./datatable-presentation');return openStudyDataTable(item,options);}
 if(item.presentation==='capture')return renderers.capture(item,options);
 if(item.presentation==='table')return renderers.table(item,options);
 if(item.presentation==='masks'||/^e\d+$/.test(item.resource.id))return renderers.masks(item,options);
 await renderers.nativeReady();await renderers.native(item);
}

/** A null picture addresses a written resource; retained image delivery needs its exact index. */
export function studyEntryReference(entry: { day?: string; id: string; kind?: string; version?: number | null; pictures?: unknown[] }, session: string): StudyResourceReference {
  if (!entry.day || entry.day !== session) throw new Error('Open the originating project before opening this retained result.');
  // Match Made's resourceTarget: opening research means its report, even when it has images.
  return { day: entry.day, id: entry.id, version: entry.version ?? null, picture: entry.kind === 'research' ? null : entry.pictures?.length ? 0 : null };
}

export function studyResourceReference(value: unknown): StudyResourceReference | null {
  if (!value || typeof value !== 'object') return null;
  const item = value as Record<string, unknown>;
  if (typeof item.day !== 'string' || !item.day || typeof item.id !== 'string' || !item.id) return null;
  const counter = (value: unknown) => value === null || Number.isSafeInteger(value) && Number(value) >= 0;
  if (!counter(item.version) || !counter(item.picture)) return null;
  if ('outputId' in item && (typeof item.outputId !== 'string' || !item.outputId || item.outputId.length > 256)) return null;
  return { day: item.day, id: item.id, version: item.version as number | null, picture: item.picture as number | null,
    ...(typeof item.outputId === 'string' ? { outputId: item.outputId } : {}) };
}
export function sameStudyResource(left: StudyResourceReference, right: StudyResourceReference): boolean {
  return left.day === right.day && left.id === right.id && left.version === right.version && left.picture === right.picture && left.outputId === right.outputId;
}

export function readStudyPresentation(value: unknown, session: string): StudyPresentation | null {
  if (!value || typeof value !== 'object') return null;
  const row = value as Record<string, unknown>;
  if (row.schema !== 1 || row.session !== session || typeof row.draft !== 'string' || !Array.isArray(row.views)) return null;
  const finite = (value: unknown, fallback: number) => typeof value === 'number' && Number.isFinite(value) ? value : fallback;
  const pair = (value: unknown): number[] | undefined => Array.isArray(value) && value.length === 2 && value.every(v => typeof v === 'number' && Number.isFinite(v)) ? value.slice() : undefined;
  const views: StudyRetainedView[] = [];
  for (const value of row.views.slice(0, 64)) {
    if (!value || typeof value !== 'object') continue;
    const view = value as Record<string, unknown>, resource = studyResourceReference(view.resource), pan = pair(view.pan);
    if (!resource || !pan || pan.some(v => v < 0 || v > 1)) continue;
    const zoom = finite(view.zoom, 1);
    if (zoom <= 0 || zoom > 1000) continue;
    const display=view.display&&typeof view.display==='object'?view.display as Record<string,unknown>:{};
    let scientific: ScientificViewPreferences | undefined;
    try { if ('scientific' in display) scientific=scientificViewPreferences(display.scientific); } catch { return null; }
    views.push({ resource,display:{...(scientific?{scientific}:{}),...(typeof display.background==='boolean'?{background:display.background}:{}),...(typeof display.sort==='string'?{sort:display.sort}:{}),...(display.direction==='asc'||display.direction==='desc'?{direction:display.direction}:{}),...(typeof display.reading==='number'&&Number.isFinite(display.reading)?{reading:Math.max(0,display.reading)}:{})}, ...(typeof view.placementId==='string'?{placementId:view.placementId}:{}), ...(view.coordinates==='stage'?{coordinates:'stage' as const}:{}), ...(view.geometryUnavailable===true?{geometryUnavailable:true}:{}), ...(view.presentation==='masks'||view.presentation==='table'||view.presentation==='capture'||view.presentation==='datatable'||view.presentation==='analysis'||view.presentation==='graph'?{presentation:view.presentation}:{}), title: typeof view.title === 'string' ? view.title : 'Retained result', zoom, pan,
      position: pair(view.position), size: pair(view.size) });
  }
  return { schema: 1, session, source: typeof row.source === 'string' ? row.source : null,
    position: Math.max(0, finite(row.position, 0)), paused: row.paused !== false, wholeFrame: row.wholeFrame === true,
    conversation: typeof row.conversation === 'string' ? row.conversation : null, draft: row.draft.slice(0, 100_000),
    reading: Math.max(0, finite(row.reading, 0)), views,
    missing: Array.isArray(row.missing) ? row.missing.filter((word): word is string => typeof word === 'string').slice(0, 16) : [] };
}

export class StudySessionSwitch {
  private pending = false;
  get busy(): boolean { return this.pending; }
  async run<T>(blocked: () => string | null, work: () => Promise<T>): Promise<T> {
    if (this.pending) throw new Error('Wait for this project to finish opening.');
    const reason = blocked();
    if (reason) throw new Error(reason);
    this.pending = true;
    try { return await work(); }
    finally { this.pending = false; }
  }
}
