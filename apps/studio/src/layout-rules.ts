/** Browser-local layout decisions. Scientific geometry remains in view.placeFrame. */
export function typingTarget(target: EventTarget | null): boolean {
  return target instanceof Element && Boolean(target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
}
export function namingConfidence(object: { status: string; source: string; confidence: number | null }): { text: string; tone: string } {
  if (object.status === 'confirmed') return { text: 'Confirmed', tone: 'confirmed' };
  if (object.source === 'ai:sam3.1-concept') return { text: 'Found by your words', tone: '' };
  if (object.source.startsWith('ai:sam')) return { text: 'Matched by appearance', tone: '' };
  if (object.status === 'unsure') return { text: 'Not sure', tone: 'unsure' };
  return typeof object.confidence === 'number' && Number.isFinite(object.confidence) && object.confidence >= 0 && object.confidence <= 1
    ? { text: `${Math.round(object.confidence * 100)}% likely`, tone: '' } : { text: 'Best guess', tone: 'unsure' };
}

/** Decide against the full stage, never the already-inset canvas. */
export function cornerMode(viewportWidth: number, room: number, hasControls: boolean): 'flow' | 'bands' | 'corners' {
  return viewportWidth <= 960 ? 'flow' : hasControls && room < 130 ? 'bands' : 'corners';
}
export const cornerWidth = (room: number): number => Math.round(Math.max(150, Math.min(room, 352)));
