/** One in-flight read per Project. Refresh events never invalidate useful pending answers. */
export class ProjectRead {
  private pending?: {key:string; controller:AbortController; promise:Promise<void>; slow:ReturnType<typeof setTimeout>; deadline:ReturnType<typeof setTimeout>};
  slow = false;
  private readonly changed:()=>void;
  constructor(changed:()=>void = ()=>{}) {this.changed=changed;}
  cancel():void {
    const read=this.pending;this.pending=undefined;this.slow=false;
    if(read){clearTimeout(read.slow);clearTimeout(read.deadline);read.controller.abort();}
  }
  run(key:string, load:(signal:AbortSignal,current:()=>boolean)=>Promise<void>):Promise<void>{
    if(this.pending?.key===key)return this.pending.promise;
    this.cancel();
    const controller=new AbortController();
    const current=()=>this.pending?.controller===controller;
    const read={key,controller,promise:Promise.resolve(),slow:setTimeout(()=>{
      if(current()){this.slow=true;this.changed();}
    },10_000),deadline:setTimeout(()=>controller.abort(new Error('Project read timed out. Retry to read it again.')),30_000)};
    this.pending=read;
    read.promise=Promise.resolve().then(()=>load(controller.signal,current)).finally(()=>{
      clearTimeout(read.slow);clearTimeout(read.deadline);
      if(current()){const wasSlow=this.slow;this.pending=undefined;this.slow=false;if(wasSlow)this.changed();}
    });
    return read.promise;
  }
}
