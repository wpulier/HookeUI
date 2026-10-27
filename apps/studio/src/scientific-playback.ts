import type { ScientificFrame } from './scientific-view';

type Transport = {
  step(frame: string, axis: number, index: number): Promise<ScientificFrame>;
  drawn(frame: string): Promise<unknown>;
  changed(): void;
};
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const element = document.createElement(tag); element.textContent = text; return element;
};

/** Serialized view navigation. Never queues ahead of accepted, visible pixels. */
export class ScientificPlayback {
  private frame: ScientificFrame | null = null;
  private available = false;
  private awaitingFrame: string | null = null;
  private session: string | null = null;
  private epoch = 0;
  private running = false;
  private pending = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  private uncertain = false;
  private axis = node('select');
  private direction = node('select');
  private rate = node('input');
  private mode = node('select');
  private first = node('input');
  private last = node('input');
  private boundsKey = '';
  private travel = 1;
  private firstStep = false;
  private shown = 0;
  private started = 0;
  private stopping: string | null = null;
  private play = node('button', 'Play slices');
  private stop = node('button', 'Stop playback');
  private status = node('p');
  private readonly visibility = () => { if (document.visibilityState === 'hidden') this.end('Playback stopped while the view is hidden.'); };
  private readonly blur = () => this.end('Playback stopped when the window lost focus.');

  private readonly root: HTMLElement;
  private readonly transport: Transport;
  constructor(root: HTMLElement, transport: Transport) {
    this.root=root; this.transport=transport;
    this.axis.setAttribute('aria-label', 'Playback axis');
    this.direction.setAttribute('aria-label', 'Playback direction');
    for (const [value,label] of [['1','Forward'],['-1','Backward']]) {
      const option = node('option',label); option.value = value; this.direction.append(option);
    }
    this.direction.value = '1';
    this.mode.setAttribute('aria-label','Playback mode');
    for (const [value,label] of [['once','Once'],['loop','Loop'],['bounce','Back and forth']]) {
      const option=node('option',label);option.value=value;this.mode.append(option);
    }
    this.mode.value='loop'; // napari's ApplicationSettings.playback_mode default
    for (const [input,label] of [[this.first,'First slice'],[this.last,'Last slice']] as const) {
      input.type='number';input.min='0';input.step='1';input.setAttribute('aria-label',label);
    }
    this.axis.addEventListener('change',()=>this.range());
    // napari's ApplicationSettings.playback_fps default. Every step relays a full drawn frame, so the rate stays at
    // most 10 per second until live-view delay and click latency are measured at a higher rate.
    this.rate.type = 'number'; this.rate.min = '0.1'; this.rate.max = '10'; this.rate.step = 'any'; this.rate.value = '10';
    this.rate.setAttribute('aria-label','Maximum slices per second');
    this.status.setAttribute('role','status');
    this.play.addEventListener('click',()=>this.start()); this.stop.addEventListener('click',()=>this.end('Playback stopped. The current view is kept.'));
    document.addEventListener('visibilitychange',this.visibility); window.addEventListener('blur',this.blur);
    root.append(node('h4','Play a stack or time series'),node('p','Only sliced axes can play. Each slice waits for its rendered frame; slower tools may run below the requested rate. Saved data is unchanged.'),
      node('label','Axis'),this.axis,node('label','Direction'),this.direction,node('label','Maximum slices per second'),this.rate,node('label','Mode'),this.mode,node('label','First slice'),this.first,node('label','Last slice'),this.last,this.play,this.stop,this.status);
    this.paint();
  }
  active(): boolean { return this.running || this.pending; }
  bindSession(session: string | null): void {
    if (session === this.session) return;
    this.epoch++; this.session = session; this.frame = null; this.pending = false; this.uncertain=false;
    this.end(''); this.boundsKey=''; this.axis.replaceChildren(); this.available = false; this.paint();
  }
  update(frame: ScientificFrame): void {
    if (this.running && !this.pending && this.frame && this.frame.frame !== frame.frame)
      this.end('Playback stopped because another control changed the view.');
    if (!this.pending) this.uncertain=false;
    this.frame = structuredClone(frame);
    if (!this.running && !this.pending) {
      const selected = this.axis.value;
      this.axis.replaceChildren();
      frame.dimensions.order.filter(axis=>!frame.dimensions.displayed.includes(axis) && frame.dimensions.counts[axis]>1).forEach(axis=>{
        const option = node('option',`${frame.dimensions.labels[axis] || 'Axis'} (${axis})`); option.value=String(axis); this.axis.append(option);
      });
      if (Array.from(this.axis.querySelectorAll('option')).some(option=>option.value===selected)) this.axis.value=selected;
      else this.axis.value=this.axis.querySelector('option')?.value ?? '';
      this.range();
    }
    this.paint();
  }
  setAvailable(value: boolean, awaitingFrame: string | null = null): void {
    this.available=value; this.awaitingFrame=awaitingFrame; this.paint();
  }
  close(): void {
    this.closed=true; this.epoch++; this.pending=false; this.end('');
    document.removeEventListener('visibilitychange',this.visibility); window.removeEventListener('blur',this.blur);
    this.root.replaceChildren();
  }
  private range(): void {
    const count=this.frame?.dimensions.counts[Number(this.axis.value)] ?? 0;
    const key=`${this.axis.value}:${count}`;
    if(key===this.boundsKey)return;
    this.boundsKey=key;this.first.value='0';this.last.value=String(Math.max(0,count-1));
    this.first.max=this.last.max=String(Math.max(0,count-1));
  }
  private paint(): void {
    if (this.closed) return;
    const locked=this.active();
    this.axis.disabled=this.direction.disabled=this.rate.disabled=this.mode.disabled=this.first.disabled=this.last.disabled=locked || !this.available;
    this.play.disabled=locked || !this.available || !this.session || this.uncertain || !this.axis.querySelectorAll('option').length;
    this.stop.disabled=!this.running;
  }
  private start(): void {
    if (this.play.disabled || !this.frame) return;
    const rate=Number(this.rate.value), axis=Number(this.axis.value), direction=Number(this.direction.value);
    if (!this.rate.value.trim() || !Number.isFinite(rate) || rate<0.1 || rate>10 || ![1,-1].includes(direction) || !Number.isInteger(axis)
      || this.frame.dimensions.displayed.includes(axis) || !(this.frame.dimensions.counts[axis]>1)) {
      this.status.textContent='Choose a sliced axis, a direction and a rate from 0.1 to 10 slices per second.'; return;
    }
    const first=Number(this.first.value),last=Number(this.last.value);
    if (!this.first.value.trim() || !this.last.value.trim() || !Number.isSafeInteger(first) || !Number.isSafeInteger(last) || first<0 || first>=last || last>=this.frame.dimensions.counts[axis]
      || !['once','loop','bounce'].includes(this.mode.value)) {
      this.status.textContent='Choose at least two slices within this axis, from First slice through Last slice.'; return;
    }
    this.travel=direction;this.firstStep=true;this.shown=0;this.started=performance.now();this.stopping=null;
    this.running=true; this.status.textContent='Playing slices. Saved data is unchanged.'; this.paint(); this.transport.changed();
    // napari's AnimationThread.work: loop and back and forth advance at once; once waits one interval, except that
    // started on (or beyond) its last frame it shows the first frame at once.
    const current=Math.min(Math.max(this.frame.dimensions.steps[axis],first),last);
    if (this.mode.value!=='once' || (direction>0 ? current>=last : current<=first)) void this.advance(); else this.schedule();
  }
  private end(message: string): void {
    const was=this.running; this.running=false;
    if (this.timer!==null) clearTimeout(this.timer); this.timer=null;
    // A slice already requested still arrives and is drawn: the summary waits for it, so it never undercounts.
    if (was && this.pending) { this.stopping=message; this.status.textContent=`${message} Finishing the slice already requested…`.trim(); }
    else if (message || was) this.status.textContent=was ? this.summary(message) : message;
    this.paint(); if (was) this.transport.changed();
  }
  /** Unlike napari, which may skip frames to keep its rate, each slice here waits for its drawn frame: say how many
   * were shown and the rate actually reached, never the requested one. */
  private summary(message: string): string {
    if (!this.shown) return message;
    const seconds=(performance.now()-this.started)/1000, achieved=seconds>0?this.shown/seconds:0;
    return `${message} Shown ${this.shown} slice${this.shown===1?'':'s'}, every one drawn, at ${achieved.toFixed(1)} per second (requested ${Number(this.rate.value)}).`.trim();
  }
  private schedule(): void {
    if (!this.running || this.timer!==null || this.pending) return;
    this.timer=setTimeout(()=>{this.timer=null; void this.advance();},1000/Number(this.rate.value));
  }
  private visible(): boolean {
    if (document.visibilityState==='hidden' || !this.root.isConnected) return false;
    for (let element: HTMLElement | null=this.root; element; element=element.parentElement)
      if (element.hidden || element.dataset.workspaceHidden==='true') return false;
    return true;
  }
  private async advance(): Promise<void> {
    if (!this.running || this.closed) return;
    if (!this.frame || !this.visible()) { this.end('Playback stopped. Wait for the visible current frame before playing again.'); return; }
    if (!this.available) {
      if (this.awaitingFrame === this.frame.frame) {
        this.status.textContent='Waiting for this slice to finish drawing…'; this.schedule();
      } else this.end('Playback stopped. Wait for the visible current frame before playing again.');
      return;
    }
    this.status.textContent='Playing slices. Saved data is unchanged.';
    const axis=Number(this.axis.value),current=this.frame.dimensions.steps[axis],first=Number(this.first.value),last=Number(this.last.value);
    // As napari, a current slice outside the frame range counts from the nearest end of the range.
    const from=Math.min(Math.max(current,first),last);
    let index=from+this.travel;
    if(this.firstStep && this.mode.value==='once' && (this.travel>0 && from>=last || this.travel<0 && from<=first)) index=this.travel>0?first:last;
    else if(index<first || index>last){
      if(this.mode.value==='once'){this.end('Reached the end of this axis. The current view is kept.');return;}
      if(this.mode.value==='loop')index=this.travel>0?first:last;
      else {this.travel=-this.travel;index=from+this.travel;}
    }
    this.firstStep=false;
    const epoch=this.epoch, previous=this.frame; this.pending=true; this.paint();
    try {
      const next=await this.transport.step(previous.frame,axis,index);
      if (this.closed || epoch!==this.epoch) return;
      const steps=[...previous.dimensions.steps]; steps[axis]=index;
      if (JSON.stringify(next.dimensions.steps)!==JSON.stringify(steps) || this.frame?.frame!==next.frame || next.dimensions.steps[axis]!==index ||
        JSON.stringify(next.dimensions.order)!==JSON.stringify(previous.dimensions.order) || next.dimensions.ndisplay!==previous.dimensions.ndisplay)
        throw new Error('The returned slice does not match this playback step. Check the current view.');
      await this.transport.drawn(next.frame);
      if (this.closed || epoch!==this.epoch) return;
      if (this.frame?.frame!==next.frame) throw new Error('Another control replaced the playback frame before it was drawn.');
      this.shown++;
    } catch(error) {
      if (!this.closed && epoch===this.epoch) { this.uncertain=true; this.stopping=null; this.end(`${error instanceof Error?error.message:String(error)} Playback stopped; check the current view before retrying.`); }
    } finally {
      if (!this.closed && epoch===this.epoch) {
        this.pending=false;
        if (this.stopping!==null && !this.running) { this.status.textContent=this.summary(this.stopping); this.stopping=null; }
        this.paint(); this.transport.changed(); this.schedule();
      }
    }
  }
}
