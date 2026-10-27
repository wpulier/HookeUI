// Closing a Tab shows where it went (workspace-feel plan, "Closing shows where things went"; the mock's closeTab()): a
// copy of the Tab moves to where its content lives (Researchers for a researcher, Data for an input or result Tab, ⌄
// otherwise) and the target pulses once. The state has already changed when this runs; the motion only says so. CSS
// transitions do the moving (workspace-feel.css `.workspace-tab-fly`), so no script runs per frame. Under reduced motion
// only the pulse plays.

const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/** Pulse the target once (the class comes off when its animation ends, or after 600 ms). */
export function pulse(target: HTMLElement): void {
  target.classList.remove('workspace-pulse');
  void target.offsetWidth;
  target.classList.add('workspace-pulse');
  window.setTimeout(() => target.classList.remove('workspace-pulse'), 600);
}

/** Send a copy of a closed Tab (its rectangle before it closed, its mark and label) to `target`, then pulse it. */
export function flyTo(from: DOMRect | null, label: string, mark: Element | null, target: HTMLElement | null): void {
  if (!target || typeof document === 'undefined' || typeof document.createElement !== 'function') return;
  const to = target.getBoundingClientRect?.();
  if (!from || !to || !to.width || reduced()) { pulse(target); return; }
  const fly = document.createElement('div');
  fly.className = 'workspace-tab-fly';
  fly.setAttribute('aria-hidden', 'true');
  if (mark) fly.append(mark.cloneNode(true));
  fly.append(label);
  Object.assign(fly.style, { left: `${from.left}px`, top: `${from.top}px`, width: `${from.width}px` });
  fly.style.setProperty('--dx', `${to.left + to.width / 2 - (from.left + from.width / 2)}px`);
  fly.style.setProperty('--dy', `${to.top + to.height / 2 - (from.top + from.height / 2)}px`);
  document.body.append(fly);
  let done = false;
  const end = () => { if (done) return; done = true; fly.remove(); pulse(target); };
  requestAnimationFrame(() => requestAnimationFrame(() => fly.classList.add('go')));
  fly.addEventListener('transitionend', end, { once: true });
  window.setTimeout(end, 420);
}
