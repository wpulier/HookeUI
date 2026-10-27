/** Captures only the existing input canvas; never opens a camera, microphone or screen grant. */
export interface AnnotatedCapture { stream: MediaStream; dispose: () => void }
export class AnnotatedRecording {
  private checkOwner: () => void = () => {};
  /** Called before the next input frame is accepted, independently of the visibility timer. */
  beforeFrame(): void { this.checkOwner(); }
  private readonly canvas: HTMLCanvasElement;
  private readonly inputUnavailable: () => string | null;
  private readonly owner: () => string;
  constructor(canvas: HTMLCanvasElement, inputUnavailable: () => string | null, owner: () => string) {
    this.canvas=canvas;this.inputUnavailable=inputUnavailable;this.owner=owner;
  }

  unavailable(): string | null {
    if (typeof this.canvas.captureStream !== 'function') return 'This browser cannot record the annotated view. Choose Camera source or Workspace.';
    const reason=this.inputUnavailable();if(reason)return reason;
    const rect=this.canvas.getBoundingClientRect(),style=getComputedStyle(this.canvas);
    if(document.hidden||!this.canvas.isConnected||!this.canvas.width||!this.canvas.height||rect.width<=0||rect.height<=0
      ||style.display==='none'||style.visibility==='hidden'||this.canvas.closest('[hidden], [data-workspace-hidden="true"]'))
      return 'Show the input view in this Tab before recording its annotations.';
    return null;
  }

  start(ended: (reason: string) => void): AnnotatedCapture {
    const reason=this.unavailable();if(reason)throw new Error(reason);
    const owner=this.owner(),stream=this.canvas.captureStream(30);
    if(!stream.getVideoTracks().some(track=>track.readyState==='live')){
      for(const track of stream.getTracks())track.stop();
      throw new Error('The annotated view did not provide a video track. Choose Workspace or try again.');
    }
    let stopped=false;
    const check=()=>{
      const reason=this.owner()!==owner?'The Project, Tab or input changed.':this.unavailable();
      if(!reason||stopped)return;
      stopped=true;clearInterval(timer);document.removeEventListener('visibilitychange',check);window.removeEventListener('workspace-tab-context',check);
      ended(reason);
    };
    this.checkOwner=()=>{if(this.owner()!==owner)check();};
    const timer=setInterval(check,250);document.addEventListener('visibilitychange',check);window.addEventListener('workspace-tab-context',check);
    return {stream,dispose:()=>{stopped=true;this.checkOwner=()=>{};clearInterval(timer);document.removeEventListener('visibilitychange',check);window.removeEventListener('workspace-tab-context',check);}};
  }
}
