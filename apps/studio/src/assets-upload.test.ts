// Production form handlers over synthetic DOM/transport; real browser File retention is qualified separately.
import {afterEach, beforeEach, expect, test, vi} from 'vitest';
import {AssetsView} from './assets';
import type {CameraFeed, FootageItem} from './camera';
import {TestElement, fire} from './workspace-windows.test-support';

class Observer { observe() {} unobserve() {} disconnect() {} }
let active: ElementNode | null = null;
class ElementNode extends TestElement {
  selectionStart: number | null = null;
  selectionEnd: number | null = null;
  selectionDirection: 'forward' | 'backward' | 'none' = 'none';
  focus() { active=this; }
  setSelectionRange(start:number,end:number,direction:'forward'|'backward'|'none'='none') { this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction; }
  remove() { if(active&&this.contains(active))active=null;super.remove(); }
}
const settle = async () => { for (let n=0;n<8;n++) await Promise.resolve(); };
beforeEach(() => {
  vi.stubGlobal('IntersectionObserver', Observer);
  active=null;
  vi.stubGlobal('document', {createElement:(tag:string) => new ElementNode(tag),get activeElement(){return active;}});
});
afterEach(() => vi.unstubAllGlobals());

function fixture() {
  const root = new TestElement('main');
  const attempts: {file:File; metadata:Parameters<CameraFeed['upload']>[1]; progress:(share:number)=>void; resolve:(item:FootageItem)=>void; reject:(error:Error)=>void}[] = [];
  const feed = {state:null,library:[],libraryVersion:'initial',upload:(file:File,metadata:Parameters<CameraFeed['upload']>[1],progress:(share:number)=>void) =>
    new Promise<FootageItem>((resolve,reject) => attempts.push({file,metadata,progress,resolve,reject}))};
  const view = new AssetsView(root as unknown as HTMLElement, feed as unknown as CameraFeed, async()=>{});
  view.render();fire(root.querySelector('.assets-add-open')!,'click');
  const form=root.querySelector('.assets-form')!, inputs=form.querySelectorAll('input');
  const chosen = new File(['synthetic recording bytes'], 'leaf.mp4', {type:'video/mp4'});
  Object.assign(inputs[0],{files:[chosen]});
  inputs[1].value='Leaf epidermis';inputs[2].value='My retained upload';inputs[3].value='40';inputs[4].value='10';
  return {root,feed,view,form,inputs,chosen,attempts};
}

test('upload progress and failure preserve the File and all fields, and retry sends the same draft once', async()=>{
  const {root,feed,view,form,inputs,chosen,attempts}=fixture();
  fire(form,'submit');expect(attempts).toHaveLength(1);
  attempts[0].progress(.45);
  feed.libraryVersion='refreshed';view.render();
  expect(root.querySelector('.assets-form')).toBe(form);
  expect(form.querySelectorAll('input')).toEqual(inputs);
  expect(inputs.slice(1,5).map(input=>input.value)).toEqual(['Leaf epidermis','My retained upload','40','10']);
  attempts[0].progress(1);fire(form,'submit');expect(attempts).toHaveLength(1); // bytes sent is not completion
  expect(form.querySelector('.assets-upload')!.disabled).toBe(true);
  attempts[0].reject(new Error('connection interrupted'));await settle();
  expect(root.textContent).toContain('connection interrupted');
  expect(form.querySelector('.assets-upload')!.textContent).toBe('Try again');
  expect(form.querySelector('.assets-upload')!.disabled).toBe(false);
  expect(inputs.every(input=>!input.disabled)).toBe(true);
  fire(form,'submit');expect(attempts).toHaveLength(2);
  expect(attempts[1].file).toBe(chosen);
  expect(attempts[1].metadata).toEqual({title:'My retained upload',specimen:'Leaf epidermis',objective:40,eyepiece:10,microscope:true});
  attempts[1].resolve({name:'leaf.mp4',title:'My retained upload',group:'Added here',seconds:1,truth:false});await settle();
  expect(root.textContent).toContain('Added "My retained upload"');
  expect(root.querySelector('.assets-form')).not.toBe(form);
  expect(root.querySelector('.assets-form')!.querySelectorAll('input').slice(1,5).every(input=>input.value==='')).toBe(true);
  const again=root.querySelector('.assets-form')!;
  Object.assign(again.querySelectorAll('input')[0],{files:[chosen]});again.querySelectorAll('input')[1].value='Leaf';
  fire(again,'submit');attempts[2].resolve({name:'leaf.mp4',title:'My retained upload',group:'Added here',seconds:1,truth:false});await settle();
  expect(root.querySelector('.assets-form')).not.toBe(again); // identical completion message still clears the next File
});

test('explicitly closing the upload form discards the unsent draft',()=>{
  const {root,form,attempts}=fixture();
  fire(root.querySelector('.assets-add-open')!,'click');
  expect(root.querySelector('.assets-form')).toBeNull();
  fire(root.querySelector('.assets-add-open')!,'click');
  expect(root.querySelector('.assets-form')).not.toBe(form);
  expect(root.querySelector('.assets-form')!.querySelectorAll('input')[1].value).toBe('');
  expect(attempts).toHaveLength(0);
});

test('a library refresh restores focused draft field and selected text without replacing it',()=>{
  const {root,feed,view,inputs}=fixture();
  const title=inputs[2] as ElementNode;title.focus();title.setSelectionRange(3,8,'backward');
  feed.libraryVersion='new-arrival';view.render();
  expect(active).toBe(title);
  expect(root.contains(title)).toBe(true);
  expect([title.selectionStart,title.selectionEnd,title.selectionDirection]).toEqual([3,8,'backward']);
});

test('editing a footage search in the middle retains its caret after results redraw',()=>{
  const {root}=fixture();const search=root.querySelector('.assets-search') as ElementNode;
  search.value='leaf cell';search.focus();search.setSelectionRange(4,4);fire(search,'input');
  const next=root.querySelector('.assets-search') as ElementNode;
  expect(active).toBe(next);expect([next.selectionStart,next.selectionEnd]).toEqual([4,4]);
});

test('a background library refresh preserves focused search and selected text',()=>{
  const {root,feed,view}=fixture();const search=root.querySelector('.assets-search') as ElementNode;
  search.value='leaf cell';fire(search,'input');
  const current=root.querySelector('.assets-search') as ElementNode;current.focus();current.setSelectionRange(1,5,'backward');
  feed.libraryVersion='background-refresh';view.render();
  const next=root.querySelector('.assets-search') as ElementNode;
  expect(active).toBe(next);expect(next.value).toBe('leaf cell');
  expect([next.selectionStart,next.selectionEnd,next.selectionDirection]).toEqual([1,5,'backward']);
});

test('Show more focuses the first newly revealed recording in its own group without playing it',()=>{
  const root=new TestElement('main');
  const library:FootageItem[]=['Real microscope','Added here'].flatMap(group=>Array.from({length:49},(_,i)=>({
    name:`${group}/${i}.mp4`,title:`${group} clip ${i}`,group,seconds:1,truth:false,
  })));
  const feed={state:{folder:'/synthetic',source:'camera',footage:null},library,libraryVersion:'initial',thumbUrl:(item:FootageItem)=>`/synthetic/${item.name}`};
  const play=vi.fn(async()=>{});
  const view=new AssetsView(root as unknown as HTMLElement,feed as unknown as CameraFeed,play);
  view.render();
  const more=root.querySelectorAll('.assets-more')[1] as ElementNode;
  more.focus();fire(more,'click');
  expect(root.querySelectorAll('.assets-play')).toHaveLength(97);
  expect(active?.getAttribute('aria-label')).toBe('Open Added here clip 48 in this project'); // the user's own: an asset, not test footage
  expect(root.contains(active!)).toBe(true);
  expect(play).not.toHaveBeenCalled();
});
