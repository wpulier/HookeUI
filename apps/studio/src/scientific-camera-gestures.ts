import type {ScientificCanvas,ScientificPaint} from './scientific-canvas';
import type {ScientificFrame} from './scientific-view';
import type {ViewerChange} from './scientific-navigation';

type Mode='orbit'|'pan'|'zoom';
type Draft={pointer:number;paint:ScientificPaint;rectangle:number[];points:[number,number][];mode:Mode};
type Options={send(frame:string,change:ViewerChange):Promise<void>;changed():void};
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
const words=(error:unknown)=>error instanceof Error?error.message:String(error);

/** One completed camera gesture, admitted against the exact pixels it started on. */
export class ScientificCameraGestures {
  private available=false;
  private frame:ScientificFrame|null=null;
  private session:string|null=null;
  private epoch=0;
  private draft:Draft|null=null;
  private busy=false;
  private uncertain:string|null=null;
  private closed=false;
  private readonly canvas:HTMLCanvasElement;
  private readonly mode=node('select');
  private readonly status=node('p');
  private readonly down=(e:PointerEvent)=>this.begin(e);
  private readonly move=(e:PointerEvent)=>this.extend(e);
  private readonly up=(e:PointerEvent)=>this.finish(e);
  private readonly lost=(e:PointerEvent)=>{if(this.draft?.pointer===e.pointerId)this.cancel('Unsent camera gesture cancelled.');};
  private readonly blur=()=>this.cancel('Unsent camera gesture cancelled.');
  private readonly visibility=()=>{if(document.visibilityState==='hidden')this.blur();};
  private readonly key=(e:KeyboardEvent)=>{
    if(e.key==='Escape'){this.blur();return;}
    if(e.target!==this.canvas || !this.ready() || this.draft || !['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key))return;
    const paint=this.drawing.current();if(!paint)return;
    const y=paint.frame.height/2,x=paint.frame.width/2,step=e.shiftKey?24:8;
    const vertical=e.key==='ArrowUp'?-step:e.key==='ArrowDown'?step:this.mode.value==='zoom'?(e.key==='ArrowRight'?-step:step):0;
    const end:[number,number]=[Math.min(paint.frame.height-1,Math.max(0,y+vertical)),
      Math.min(paint.frame.width-1,Math.max(0,x+(e.key==='ArrowLeft'?-step:e.key==='ArrowRight'?step:0)))];
    e.preventDefault();void this.submit(paint,this.mode.value as Mode,[[y,x],end]);
  };
  private readonly root:HTMLElement;
  private readonly drawing:ScientificCanvas;
  private readonly options:Options;
  constructor(root:HTMLElement,drawing:ScientificCanvas,options:Options){
    this.root=root;this.drawing=drawing;this.options=options;this.canvas=drawing.element();
    const label=node('label','3D camera interaction');this.mode.setAttribute('aria-label','3D camera interaction');
    for(const [value,text] of [['orbit','Orbit'],['pan','Pan'],['zoom','Zoom']]){const option=node('option',text);option.value=value;this.mode.append(option);}
    this.mode.value='orbit';this.mode.addEventListener('change',this.blur);label.append(this.mode);
    this.status.setAttribute('role','status');root.append(label,node('p','Drag the 3D preview and release to move the camera. Arrow keys move it; Shift makes a larger step. Escape cancels an unsent drag.'),this.status);
    this.canvas.addEventListener('pointerdown',this.down);this.canvas.addEventListener('pointermove',this.move);
    this.canvas.addEventListener('pointerup',this.up);this.canvas.addEventListener('pointercancel',this.lost);
    this.canvas.addEventListener('lostpointercapture',this.lost);window.addEventListener('blur',this.blur);
    window.addEventListener('keydown',this.key);document.addEventListener('visibilitychange',this.visibility);this.paint();
  }
  blocked():boolean{return !!this.draft||this.busy;}
  bindSession(session:string|null):void{if(session===this.session)return;this.cancel();this.session=session;this.epoch++;this.busy=false;this.uncertain=null;this.frame=null;this.status.textContent='';this.paint();}
  update(frame:ScientificFrame):void{if(this.draft && this.draft.paint.frame.frame!==frame.frame)this.revoke();this.frame=frame;if(this.uncertain && this.uncertain!==frame.frame)this.uncertain=null;this.paint();}
  setAvailable(value:boolean):void{this.available=value;if(!value)this.cancel();this.paint();}
  revoke():void{this.cancel('The preview changed. The unsent camera gesture was cancelled.');}
  private ready():boolean{return !this.closed&&this.available&&!!this.session&&!this.busy&&!this.uncertain&&this.frame?.dimensions.ndisplay===3&&!this.frame.grid?.enabled;}
  private rectangle():number[]{const r=this.canvas.getBoundingClientRect();return[r.left,r.top,r.width,r.height];}
  private begin(e:PointerEvent):void{
    if(e.button!==0||!this.ready()||this.draft)return;
    try{
      const paint=this.drawing.current();if(!paint)throw new Error('Refresh the scientific preview first.');
      const point=this.drawing.position(paint.draw,e.clientX,e.clientY);
      this.canvas.setPointerCapture(e.pointerId);this.canvas.focus({preventScroll:true});
      this.draft={pointer:e.pointerId,paint,rectangle:this.rectangle(),points:[point],mode:this.mode.value as Mode};
      // Keep in-flow feedback unchanged while the pointer owns this rectangle.
      e.preventDefault();this.options.changed();
    }catch(error){this.status.textContent=words(error);this.cancel();}
  }
  private extend(e:PointerEvent):void{
    const draft=this.draft;if(!draft||draft.pointer!==e.pointerId)return;
    try{
      if(this.rectangle().some((v,i)=>v!==draft.rectangle[i]))throw new Error('The preview moved or resized. Nothing was applied.');
      const point=this.drawing.position(draft.paint.draw,e.clientX,e.clientY),last=draft.points.at(-1)!;
      if(Math.hypot(point[0]-last[0],point[1]-last[1])<1)return;
      if(draft.points.length>=256)throw new Error('Use a shorter camera gesture. Nothing was applied.');
      draft.points.push(point);
    }catch(error){this.cancel(words(error));}
  }
  private finish(e:PointerEvent):void{
    if(this.draft?.pointer!==e.pointerId)return;this.extend(e);const draft=this.draft;if(!draft)return;
    this.draft=null;this.release(draft.pointer);e.preventDefault();
    if(draft.points.length<2){this.status.textContent='Camera unchanged.';this.options.changed();return;}
    void this.submit(draft.paint,draft.mode,draft.points);
  }
  private async submit(paint:ScientificPaint,mode:Mode,points:[number,number][]):Promise<void>{
    if(!this.ready()||this.drawing.current()?.draw!==paint.draw)return;
    const epoch=this.epoch;this.busy=true;this.options.changed();this.paint();
    try{
      await this.options.send(paint.frame.frame,{operation:'camera-gesture',value:{mode,width:paint.frame.width,height:paint.frame.height,points}});
      if(!this.closed&&this.epoch===epoch)this.status.textContent='Camera moved. Saved scientific data is unchanged.';
    }catch(error){if(!this.closed&&this.epoch===epoch){this.uncertain=paint.frame.frame;this.status.textContent=words(error)+' Refresh the preview before trying again.';}}
    finally{if(!this.closed&&this.epoch===epoch){this.busy=false;this.paint();this.options.changed();}}
  }
  private release(pointer:number):void{if(this.canvas.hasPointerCapture(pointer))this.canvas.releasePointerCapture(pointer);}
  private cancel(message?:string):void{const old=this.draft;this.draft=null;if(old){this.release(old.pointer);if(message)this.status.textContent=message;this.options.changed();}}
  private paint():void{this.root.hidden=this.frame?.dimensions.ndisplay!==3||!!this.frame.grid?.enabled;this.mode.disabled=!this.ready()||!!this.draft;}
  close():void{if(this.closed)return;this.closed=true;this.cancel();this.epoch++;this.canvas.removeEventListener('pointerdown',this.down);this.canvas.removeEventListener('pointermove',this.move);this.canvas.removeEventListener('pointerup',this.up);this.canvas.removeEventListener('pointercancel',this.lost);this.canvas.removeEventListener('lostpointercapture',this.lost);window.removeEventListener('blur',this.blur);window.removeEventListener('keydown',this.key);document.removeEventListener('visibilitychange',this.visibility);this.root.replaceChildren();}
}
