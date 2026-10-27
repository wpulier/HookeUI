// Synthetic UI transitions; actual plugin execution is qualified separately.
import {beforeEach,afterEach,expect,test,vi} from 'vitest';
import {TestElement,fire,settleMicrotasks,syntheticDOM} from './workspace-windows.test-support';
import {recipeEntry,showScientificRecipe} from './scientific-recipe';
const source={id:'p'+'a'.repeat(32),day:'2026-10-19',version:2};
const exact={id:source.id,session:source.day,revision:2,digest:'b'.repeat(64)};
const parameters={format:'scientific-tool-document-v1',schema:'test',version:1,state:{diameter:30}};
const recipe={format:'scientific-recipe-v1',input:exact,steps:[{template:exact,name:'Segment',capability:'mine/tool#segment',parameters}]};
const fetcher=vi.fn<typeof fetch>(),open=vi.fn(async(_target:{id:string;version:number;title:string})=>{});
let root:TestElement;
const button=(text:string)=>root.querySelectorAll('button').find(b=>b.textContent===text)!;
const response=(data:object,status=200)=>new Response(JSON.stringify(data),{status});
async function settle(){for(let i=0;i<12;i++)await settleMicrotasks();}
beforeEach(()=>{vi.stubGlobal('document',syntheticDOM().document);vi.stubGlobal('fetch',fetcher);fetcher.mockReset();open.mockClear();root=document.createElement('section') as unknown as TestElement;});
afterEach(()=>vi.unstubAllGlobals());
test('reopening a definition never starts or prepares a run',async()=>{
 await showScientificRecipe(root as unknown as HTMLElement,{recipe},source,open);
 expect(fetcher).not.toHaveBeenCalled();expect(open).not.toHaveBeenCalled();
 expect(root.textContent).toContain('Nothing runs when you reopen');expect(button('Start a new run')).toBeTruthy();
});
test('creation captures Project and revision with duplicate protection',async()=>{
 recipeEntry(root as unknown as HTMLElement,{scientificView:{tool:{recipe:parameters}}},source,open);
 let resolve!:(r:Response)=>void;fetcher.mockImplementation(()=>new Promise(r=>{resolve=r;}));
 fire(button('Create recipe'),'click');fire(button('Create recipe'),'click');expect(fetcher).toHaveBeenCalledTimes(1);
 expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({session:source.day,revision:2,action:'create'});
 resolve(response({ok:true,project:{id:'p'+'c'.repeat(32),session:source.day,revision:1,document:{title:'Recipe'}}}));await settle();
 expect(root.textContent).toContain('Saved.');expect(open).not.toHaveBeenCalled();fire(button('Open saved update'),'click');await settle();expect(open).toHaveBeenCalledOnce();
});
test('failed preparation preserves previous result links and scoped retry',async()=>{
 fetcher.mockResolvedValueOnce(response({ok:true,project:{...exact,document:{kind:'scientific-recipe',state:{recipe:{...recipe,steps:[...recipe.steps,...recipe.steps]}}}}}));
 await showScientificRecipe(root as unknown as HTMLElement,{recipeRun:{format:'scientific-recipe-run-v1',recipe:exact,completed:[{working:exact,result:exact}],working:null}},source,open);
 expect(fetcher).toHaveBeenCalledTimes(1);expect(button('Inspect accepted step 1')).toBeTruthy();
 fetcher.mockResolvedValue(response({ok:false,says:'Prepare this integration.',next:'Saved results remain.'},409));
 fire(button('Prepare this step'),'click');await settle();expect(root.textContent).toContain('Prepare this integration');expect(button('Prepare this step').disabled).toBe(false);
 fire(button('Prepare this step'),'click');await settle();expect(fetcher.mock.calls[1][1]?.body).toEqual(fetcher.mock.calls[2][1]?.body);
 expect(open).not.toHaveBeenCalled();expect(button('Inspect accepted step 1')).toBeTruthy();
});
test('mismatched definition refuses executable actions',async()=>{
 fetcher.mockResolvedValue(response({ok:true,project:{...exact,digest:'c'.repeat(64),document:{kind:'scientific-recipe',state:{recipe}}}}));
 await showScientificRecipe(root as unknown as HTMLElement,{recipeRun:{format:'scientific-recipe-run-v1',recipe:exact,completed:[],working:null}},source,open);
 expect(root.textContent).toContain('exact recipe definition is unavailable');expect(root.querySelectorAll('button')).toHaveLength(0);
});
test('tools without declared configuration do not advertise replay',()=>{
 recipeEntry(root as unknown as HTMLElement,{scientificView:{tool:{document:parameters}}},source,open);
 expect(root.querySelectorAll('button')).toHaveLength(0);
});

test.each([{...recipe,format:'future'},{...recipe,steps:[]}])('run cannot claim completion from unsupported definition %j',async invalid=>{
 fetcher.mockResolvedValue(response({ok:true,project:{...exact,document:{kind:'scientific-recipe',state:{recipe:invalid}}}}));
 await showScientificRecipe(root as unknown as HTMLElement,{recipeRun:{format:'scientific-recipe-run-v1',recipe:exact,completed:[],working:null}},source,open);
 expect(root.textContent).not.toContain('Workflow results accepted');expect(root.querySelectorAll('button')).toHaveLength(0);
 expect(root.textContent).toContain('exact recipe definition is unavailable');
});

const other={id:'p'+'d'.repeat(32),session:source.day,revision:3,digest:'e'.repeat(64)};
const definitionReply=(steps=[...recipe.steps,...recipe.steps])=>response({ok:true,project:{...exact,document:{kind:'scientific-recipe',state:{recipe:{...recipe,steps}}}}});
const run=(fields:object)=>({recipeRun:{format:'scientific-recipe-run-v2',recipe:exact,input:other,completed:[],working:null,attempts:[],...fields}});
test('a new run can start on another exact saved input',async()=>{
 await showScientificRecipe(root as unknown as HTMLElement,{recipe},source,open);
 fetcher.mockResolvedValueOnce(response({ok:true,inputs:[{input:exact,title:'Original'},{input:other,title:'Second crop',layers:1,tool:'Read OME-Zarr'}]}));
 fire(button('Find saved inputs'),'click');await settle();
 expect(root.textContent).toContain('Second crop · revision 3 · 1 layer · last tool: Read OME-Zarr');expect(root.textContent).not.toContain('Original · revision');
 fetcher.mockResolvedValueOnce(response({ok:true,project:{id:'p'+'f'.repeat(32),session:source.day,revision:1,document:{title:'Run'}}}));
 fire(button('Start a run on this input'),'click');await settle();
 expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toMatchObject({action:'begin',revision:2,input:other});
 expect(open).not.toHaveBeenCalled();
});
test('a failed step makes the run partial and keeps every attempt',async()=>{
 fetcher.mockResolvedValueOnce(definitionReply());
 await showScientificRecipe(root as unknown as HTMLElement,run({completed:[{working:exact,result:exact}],attempts:[{step:1,working:other,outcome:'failed',note:'Dimensions did not match'}]}),source,open);
 expect(root.textContent).toContain('Partial: 1 of 2 steps accepted · step 2 failed');expect(root.textContent).not.toContain('Workflow results accepted');
 expect(root.textContent).toContain('Step 2, attempt 1: failed — Dimensions did not match');expect(root.textContent).toContain('another saved input');
 fire(button('Inspect failed attempt 1'),'click');await settle();expect(open).toHaveBeenCalledWith({id:other.id,version:3,title:'Failed attempt 1'});
 fetcher.mockResolvedValueOnce(response({ok:false,says:'Prepare this integration.'},409));
 fire(button('Prepare this step again'),'click');await settle();
 expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({session:source.day,revision:2,action:'prepare'});
});
test('recording a failure sends the researcher’s note and claims nothing',async()=>{
 fetcher.mockResolvedValueOnce(definitionReply());
 await showScientificRecipe(root as unknown as HTMLElement,run({working:other}),source,open);
 expect(root.textContent).toContain('Step 1 of 2');
 const note=root.querySelectorAll('textarea')[0] as unknown as {value:string};note.value='Tool did not open';
 fetcher.mockResolvedValueOnce(response({ok:true,project:{id:source.id,session:source.day,revision:3,document:{title:'Run'}}}));
 fire(button('Record this attempt as failed'),'click');await settle();
 expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({session:source.day,revision:2,action:'fail',note:'Tool did not open'});
 expect(root.textContent).toContain('Saved. Open the updated document');expect(root.textContent).not.toContain('Workflow results accepted');
});
test('a complete run still lists failed attempts',async()=>{
 fetcher.mockResolvedValueOnce(definitionReply(recipe.steps));
 await showScientificRecipe(root as unknown as HTMLElement,run({completed:[{working:exact,result:exact}],attempts:[{step:0,working:other,outcome:'failed',note:''}]}),source,open);
 expect(root.textContent).toContain('Workflow results accepted');expect(root.textContent).toContain('Failed attempts (1)');expect(root.textContent).toContain('Step 1, attempt 1: failed');
});
test.each([{step:5,working:other,outcome:'failed',note:''},{step:0,working:other,outcome:'passed',note:''},{step:0,working:null,outcome:'failed',note:''}])('unsupported attempt %j refuses actions',async attempt=>{
 fetcher.mockResolvedValueOnce(definitionReply());
 await showScientificRecipe(root as unknown as HTMLElement,run({attempts:[attempt]}),source,open);
 expect(root.textContent).toContain('unsupported record');expect(root.querySelectorAll('button')).toHaveLength(0);
});
test('steps and saved tools are named by their tool, not only their document title',async()=>{
 await showScientificRecipe(root as unknown as HTMLElement,{recipe},source,open);
 expect(root.textContent).toContain('Tool segment · settings from saved revision 2');
 fetcher.mockResolvedValueOnce(response({ok:true,templates:[{template:other,name:'Same title',tool:'Private scientific view'}]}));
 fire(button('Find saved tool configurations'),'click');await settle();
 expect(button('Add Private scientific view (Same title · revision 3)')).toBeTruthy();
});
