/** Match the displayed view and device scale within the existing renderer allowance. */
export type ScientificViewport = { width: number; height: number; cssWidth: number; cssHeight: number; deviceScale: number };

export function scientificViewport(element: HTMLElement, deviceScale = window.devicePixelRatio || 1): ScientificViewport {
  const { width, height } = element.getBoundingClientRect();
  if (![width, height, deviceScale].every(Number.isFinite) || width <= 0 || height <= 0 || deviceScale <= 0)
    throw new Error('Open this scientific window at a visible size before refreshing its preview.');
  // The worker's existing maximum side stays authoritative. Preserve the aspect
  // ratio when its display is larger; never allocate a full device-size bitmap first.
  const scale = Math.min(deviceScale, 1024 / Math.max(width, height));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)),
    cssWidth: width, cssHeight: height, deviceScale };
}

export function sameScientificViewport(first: ScientificViewport, next: ScientificViewport): boolean {
  return first.cssWidth === next.cssWidth && first.cssHeight === next.cssHeight && first.deviceScale === next.deviceScale;
}
