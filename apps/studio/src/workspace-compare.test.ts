// Synthetic retained metadata and viewport arithmetic; no native/browser/scientific qualification.
import {afterEach,expect,test,vi} from 'vitest';
import {comparisonCandidates,zoomComparison,publishComparisonTable,CompareDrawQueue} from './workspace-compare';
const saved={resource:{day:'p',id:'e1',version:4,picture:null},title:'Original',zoom:1,pan:[.5,.5]};
const entry={kind:'population',day:'p',id:'e1',version:4,title:'Original',source:{frame:10,seq:3,boot:'one'},madeBy:{implementation:'segment-a'}};
test('comparison choices retain exact source revision/producer and exclude other captures or Projects',()=>{
 const rows=[entry,{...entry,id:'e2',version:2,madeBy:{implementation:'segment-b'}},{...entry,id:'e3',source:{...entry.source,boot:'two'}},{...entry,id:'e4',day:'q'},{...entry,id:'e5',source:{...entry.source,seq:4}},{...entry,id:'e6',madeBy:{}},{...entry,id:'e7',state:'unavailable'}];
 expect(comparisonCandidates(rows,saved)).toEqual([
 {resource:saved.resource,title:'Original',implementation:'segment-a',capture:10},
 {resource:{day:'p',id:'e2',version:2,picture:null},title:'Original',implementation:'segment-b',capture:10}]);
 expect(()=>comparisonCandidates(rows,{...saved,resource:{...saved.resource,version:3}})).toThrow('revision');
});
test('synchronized zoom retains the selected image coordinate and bounded aspect ratio',()=>{
 const frame={width:600,height:400},original={x:0,y:0,...frame};
 const next=zoomComparison(original,frame,.5,[.25,.75]);
 expect(next).toEqual({x:75,y:150,width:300,height:200});
 expect(next.x+next.width*.25).toBe(150);expect(next.y+next.height*.75).toBe(300);
 expect(zoomComparison(next,frame,1e-9,[.5,.5]).width).toBe(600/16);
 expect(zoomComparison(next,frame,100,[.5,.5]).width).toBe(600);
});

afterEach(()=>vi.unstubAllGlobals());
test('accepted comparison publication after a changed opening never mounts its table in the next Tab',async()=>{
 let current=true,finish!:(value:unknown)=>void;
 const request=vi.fn((_url:string,_init:RequestInit)=>new Promise(resolve=>{finish=resolve;}));vi.stubGlobal('fetch',request);
 const open=vi.fn(),view={id:'compare',title:'Methods',specimen:{visible:false,rect:null},items:[],notes:[]} as const;
 const saving=publishComparisonTable({session:'p',tab:'compare',revision:9,view:structuredClone(view) as never},()=>current,open);
 expect(JSON.parse(String(request.mock.calls[0][1].body))).toEqual({session:'p',tab:'compare',layoutRevision:9,title:'Methods'});
 current=false;finish({ok:true,json:async()=>({id:'tb4',version:1,title:'Methods'})});
 expect(await saving).toEqual({saved:true,shown:false});expect(open).not.toHaveBeenCalled();
});

test('comparison redraw keeps one latest viewport per frame and cancels old ownership',()=>{
 const callbacks=new Map<number,FrameRequestCallback>();let next=0;
 const draw=vi.fn(),schedule=vi.fn((callback:FrameRequestCallback)=>{callbacks.set(++next,callback);return next;}),cancel=vi.fn((id:number)=>callbacks.delete(id));
 const queue=new CompareDrawQueue(draw,schedule,cancel),source={};
 queue.push(source,{x:1,y:2,width:100,height:80});queue.push(source,{x:3,y:4,width:50,height:40});
 expect(schedule).toHaveBeenCalledOnce();expect(draw).not.toHaveBeenCalled();expect(queue.pending(source)?.x).toBe(3);
 callbacks.get(1)!(0);expect(draw).toHaveBeenCalledExactlyOnceWith(source,{x:3,y:4,width:50,height:40});
 queue.push(source,{x:9,y:8,width:20,height:16});const late=callbacks.get(2)!;queue.cancel();late(0);
 expect(cancel).toHaveBeenCalledWith(2);expect(draw).toHaveBeenCalledOnce();expect(queue.pending(source)).toBeNull();
});
