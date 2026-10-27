import type { StudyRetainedView } from './workspace-study-restoration';
export type Rect = [number,number,number,number];
export interface NotePlacement {id:string;rect:Rect;placementId?:string}
export interface WorkspaceTabInput {source:string;position:number}
/** Schema 5 adds output and compare Tabs and table, analysis and graph windows; schema 6 the reader Tab (one Data item
 * shown full width, ticket 66); 2-5 keep their own format. */
export const LAYOUT_SCHEMA=6;
export type LayoutSchema=2|3|4|5|6;
export const LAYOUT_SCHEMAS:readonly LayoutSchema[]=[2,3,4,5,6];
export type TabKind='output'|'compare'|'reader';
export interface TabProvenance {researcher?:string;version?:number;run?:string;from?:string}
export type LaneLabel='A'|'B'|'C';
export interface CompareLane {placementId:string;label:LaneLabel;impl:string}
export interface TabCompare {sync:boolean;lanes:CompareLane[]}
export type ItemPresentation=NonNullable<StudyRetainedView['presentation']>|'datatable'|'analysis'|'graph';
export const SCHEMA5_PRESENTATIONS:readonly ItemPresentation[]=['datatable','analysis','graph'];
export interface WorkspaceView {input?:WorkspaceTabInput;id:string;title:string;specimen:{visible:boolean;rect:Rect|null};items:StudyRetainedView[];notes:NotePlacement[];kind?:TabKind;provenance?:TabProvenance;compare?:TabCompare}
export interface WorkspaceLayout {schema:LayoutSchema;coordinates:'stage';active:string;views:WorkspaceView[]}
export const mainLayout = ():WorkspaceLayout => ({schema:2,coordinates:'stage',active:'main',views:[{id:'main',title:'Main',specimen:{visible:true,rect:null},items:[],notes:[]}]});
export function transferItem(layout:WorkspaceLayout,from:string,to:string,item:StudyRetainedView):WorkspaceLayout {
  const result=structuredClone(layout),source=result.views.find(v=>v.id===from),target=result.views.find(v=>v.id===to);
  if(!source||!target||from===to)return result;
  const key=(v:StudyRetainedView)=>v.placementId ?? JSON.stringify([v.resource,v.presentation]);
  source.items=source.items.filter(v=>key(v)!==key(item));
  pruneLanes(source);
  if(!target.items.some(v=>key(v)===key(item)))target.items.push(structuredClone(item));
  return result;
}
/** A reader Tab's one item (wire contract, schema 6): which Data entry it shows, by its exact identity, and its title. */
export interface ReaderTabItem {resource:{day:string;id:string;version:number|null};title:string}
/** A reader Tab: one Data item in a Tab of its own, the reader full width, the specimen hidden (Open in new Tab). */
export function readerView(id:string,item:ReaderTabItem):WorkspaceView {
 const resource={day:item.resource.day,id:item.resource.id,version:item.resource.version??null};
 return {id,title:item.title.slice(0,80)||'Data',specimen:{visible:false,rect:null},items:[{resource,title:item.title.slice(0,200)} as unknown as StudyRetainedView],notes:[],kind:'reader'};
}
/** The item a reader Tab shows, or null when the view is not one (or its item is malformed). */
export function readerItem(view:Pick<WorkspaceView,'kind'|'items'>):ReaderTabItem|null {
 if(view.kind!=='reader')return null;
 const item=view.items[0] as unknown as Partial<ReaderTabItem>|undefined,resource=item?.resource;
 if(!resource||typeof resource.day!=='string'||typeof resource.id!=='string')return null;
 return {resource:{day:resource.day,id:resource.id,version:typeof resource.version==='number'?resource.version:null},title:typeof item?.title==='string'?item.title:resource.id};
}
/** True when the layout holds a reader Tab, which only schema 6 can save. */
export function needsSchema6(layout:Pick<WorkspaceLayout,'views'>):boolean {return layout.views.some(v=>v.kind==='reader');}
/** The layout as a server that saves only up to `supported` takes it: a reader Tab is left out (it stays in this
 * studio for the session) and the schema is at most what it reads. The newest server takes it whole. */
export function layoutForServer(layout:WorkspaceLayout,supported:LayoutSchema):{schema:LayoutSchema;coordinates:'stage';views:WorkspaceView[]} {
 if(supported>=6)return {schema:layout.schema,coordinates:'stage',views:layout.views};
 return {schema:Math.min(layout.schema,supported) as LayoutSchema,coordinates:'stage',views:layout.views.filter(v=>v.kind!=='reader')};
}
/** True when the layout holds a field only schema 5 can save. */
export function needsSchema5(layout:Pick<WorkspaceLayout,'views'>):boolean {
 return layout.views.some(v=>v.kind!==undefined||v.provenance!==undefined||v.compare!==undefined||
  v.items.some(item=>SCHEMA5_PRESENTATIONS.includes(item.presentation as ItemPresentation)));
}
/** Like rememberView's move to schema 3: only writing a schema-5 field moves a layout to schema 5. */
export function bumpForSchema5(layout:WorkspaceLayout):void {
 if(needsSchema5(layout))layout.schema=Math.max(5,layout.schema) as LayoutSchema;
 if(needsSchema6(layout))layout.schema=6;
}
/** Mark a Tab as an output or compare Tab. Lanes name 2-3 of the Tab's own windows, A-C, once each. */
export function setTabKind(layout:WorkspaceLayout,id:string,fields:{kind:TabKind;provenance?:TabProvenance;compare?:TabCompare}):void {
 const view=layout.views.find(v=>v.id===id);
 if(!view)throw new Error('That Tab is no longer in this Project.');
 if(fields.compare){
  if(fields.kind!=='compare')throw new Error('Only a compare Tab has lanes.');
  const lanes=fields.compare.lanes,placements=new Set(view.items.map(item=>item.placementId));
  if(lanes.length<2||lanes.length>3)throw new Error('A compare Tab needs 2 or 3 lanes.');
  if(new Set(lanes.map(l=>l.label)).size!==lanes.length||lanes.some(l=>!['A','B','C'].includes(l.label)))throw new Error('Each lane is A, B or C, used once.');
  if(new Set(lanes.map(l=>l.placementId)).size!==lanes.length)throw new Error('Each lane shows a different window.');
  if(lanes.some(l=>!placements.has(l.placementId)))throw new Error('A lane must show one of this Tab\'s windows.');
 }
 view.kind=fields.kind;
 if(fields.provenance)view.provenance=structuredClone(fields.provenance);else delete view.provenance;
 if(fields.compare)view.compare=structuredClone(fields.compare);else delete view.compare;
 bumpForSchema5(layout);
}
/** New placement identities for a copied Tab (recovery, Duplicate); its lanes follow their windows. */
export function renewPlacements(view:WorkspaceView,uuid:()=>string):void {
 if(view.kind==='reader')return; // its one item is an identity, not a window placement
 const renamed=new Map<string,string>();
 for(const item of view.items){const id=uuid();if(item.placementId)renamed.set(item.placementId,id);item.placementId=id;}
 for(const note of view.notes)note.placementId=uuid();
 if(view.compare)for(const lane of view.compare.lanes)lane.placementId=renamed.get(lane.placementId)??lane.placementId;
 pruneLanes(view);
}
/** A lane follows its window: when the window leaves the Tab, so does the lane, and under 2 lanes the comparison ends. */
export function pruneLanes(view:WorkspaceView):void {
 if(!view.compare)return;
 const placements=new Set(view.items.map(item=>item.placementId));
 view.compare.lanes=view.compare.lanes.filter(l=>placements.has(l.placementId));
 if(view.compare.lanes.length<2)delete view.compare;
}
export function tileRects(count:number,width:number,height:number):Rect[] {
  const columns=Math.max(1,Math.ceil(Math.sqrt(count))),rows=Math.max(1,Math.ceil(count/columns));
  const gap=12,w=Math.max(1,(width-gap*(columns+1))/columns),h=Math.max(1,(height-gap*(rows+1))/rows);
  return Array.from({length:count},(_,i)=>[gap+(i%columns)*(w+gap),gap+Math.floor(i/columns)*(h+gap),w,h]);
}

/** Undo only geometry still owned by Arrange; later edits, moves and removals win. */
export function undoArrange(layout:WorkspaceLayout,before:WorkspaceView,arranged:WorkspaceView):WorkspaceLayout {
 const result=structuredClone(layout),view=result.views.find(v=>v.id===before.id);
 if(!view)return result;
 const same=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);
 if(same(view.specimen.rect,arranged.specimen.rect))view.specimen.rect=structuredClone(before.specimen.rect);
 for(const note of view.notes){
  const old=before.notes.find(n=>n.placementId===note.placementId),after=arranged.notes.find(n=>n.placementId===note.placementId);
  if(old&&after&&same(note.rect,after.rect))note.rect=structuredClone(old.rect);
 }
 for(const item of view.items){
  const old=before.items.find(n=>n.placementId===item.placementId),after=arranged.items.find(n=>n.placementId===item.placementId);
  if(!old||!after||!same([item.position,item.size],[after.position,after.size]))continue;
  if(old.position===undefined)delete item.position;else item.position=structuredClone(old.position);
  if(old.size===undefined)delete item.size;else item.size=structuredClone(old.size);
 }
 return result;
}

export type WorkspaceInput = {source:string|null}|{resource:import('./modelspage').RetainedInput['reference']}|{volume:string};
/** `destination` 'new': a New Tab run's (the Tab it opened is for its result, so its masks are placed there even when its
 * outlines are drawn on the specimen: S5a). */
export interface WorkspaceOrigin {session:string;tab:string;placementId:string;run:string;input:WorkspaceInput;destination?:'new'}
export function workspaceInput(fields:{type?:'action'|'capability-run';resource?:import('./modelspage').RetainedInput['reference'];volume?:string},source:string|null):WorkspaceInput {
 if(fields.resource)return {resource:structuredClone(fields.resource)};
 if(fields.volume)return {volume:fields.volume};
 if(fields.type==='capability-run'&&!source)throw new Error('Choose a picture or video before running this tool.');
 return {source};
}
export interface WorkspaceCompletion {id:string;execution:string;origin:WorkspaceOrigin;item:StudyRetainedView;state:"placed"|"available"}
