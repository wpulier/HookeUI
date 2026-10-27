/** Dock meters use viewport coordinates even when the dock is in phone page flow. */
export function meterPopover(anchor: { left: number; right: number; top: number; bottom: number },
  viewport: { width: number; height: number; top?: number; left?: number }) {
  const edge = 12, gap = 8;
  const left = viewport.left ?? 0, top = viewport.top ?? 0;
  const width = Math.min(368, Math.max(0, viewport.width - edge * 2));
  const above = Math.max(0, Math.min(viewport.height - edge * 2, anchor.top - top - edge - gap));
  const below = Math.max(0, Math.min(viewport.height - edge * 2, top + viewport.height - anchor.bottom - edge - gap));
  const placeAbove = above >= below;
  return {
    width,
    left: Math.max(left + edge, Math.min(anchor.right - width, left + viewport.width - width - edge)),
    top: placeAbove ? undefined : Math.max(top + edge, anchor.bottom + gap),
    bottom: placeAbove ? Math.max(edge, top + viewport.height - anchor.top + gap) : undefined,
    maxHeight: Math.max(0, placeAbove ? above : below),
  };
}

export function positionMeter(panel: HTMLElement, button: HTMLElement): void {
  const viewport = window.visualViewport;
  const frame = { width: viewport?.width ?? window.innerWidth, height: viewport?.height ?? window.innerHeight,
    left: viewport?.offsetLeft ?? 0, top: viewport?.offsetTop ?? 0 };
  const box = meterPopover(button.getBoundingClientRect(), frame);
  panel.style.position = 'fixed';
  panel.style.width = `${box.width}px`;
  panel.style.left = `${box.left}px`;
  panel.style.right = 'auto';
  panel.style.top = box.top === undefined ? 'auto' : `${box.top}px`;
  panel.style.bottom = box.bottom === undefined ? 'auto' : `${window.innerHeight - frame.top - frame.height + box.bottom}px`;
  panel.style.maxHeight = `${box.maxHeight}px`;
}
