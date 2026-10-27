/** One sandbox message: Escape closes only the focused iframe's own containing surface. */
const bindings = new WeakMap<object, {frame:HTMLIFrameElement; nonce:string; close:()=>void}>();
let listening = false;
export function bindArtifactEscape(frame:HTMLIFrameElement, close:()=>void): void {
  if (!listening) {
    listening = true;
    window.addEventListener('message', (event:MessageEvent) => {
      if (event.origin !== 'null' || !event.source || !event.data || event.data.type !== 'micromind-artifact-escape') return;
      const binding = bindings.get(event.source);
      if (!binding || event.data.nonce !== binding.nonce || !binding.frame.isConnected || document.activeElement !== binding.frame) return;
      const modal = [...document.querySelectorAll('dialog:modal')].at(-1);
      if (modal && !modal.contains(binding.frame)) return;
      binding.close();
    });
  }
  // A new URL avoids an old cached document without the response-only keyboard bridge.
  const [path, hash] = frame.src.split('#',2);
  frame.src = `${path}${path.includes('?') ? '&' : '?'}artifactKeyboard=1${hash ? `#${hash}` : ''}`;
  frame.addEventListener('load', () => {
    if (!frame.contentWindow) return;
    const nonce = crypto.randomUUID();
    bindings.set(frame.contentWindow,{frame,nonce,close});
    // Opaque sandbox origins require '*'; the recipient is this exact WindowProxy.
    frame.contentWindow.postMessage({type:'micromind-artifact-keyboard-init',nonce},'*');
  });
}
