// The side column: researchers' work sits beside the live view, never on top of it. While it
// is open, the view keeps the rest of the stage (the eyepiece glides aside); on phones the
// column becomes a sheet along the bottom and the view keeps the space above it. The
// magnifier and the details card are not in it: they float over the view (magnifier.ts,
// notes.ts), so the view never shrinks or moves for them. The panel stack (panels.ts) asks it
// for its room each frame and hands that to the view.

import type { Panel, Rect, Reserved } from './panels';

export class SideColumn implements Panel {
  readonly id = 'side';
  private readonly side = document.getElementById('side')!;
  private readonly onClose: () => void;

  /** `onClose`: what Escape does to it (the result viewer is what it holds). */
  constructor(onClose: () => void) {
    this.onClose = onClose;
  }

  isOpen(): boolean {
    return [...this.side.children].some((c) => !(c as HTMLElement).hidden && (c as HTMLElement).dataset.resultTucked !== 'true');
  }

  close(): void {
    this.onClose();
  }

  footprint(): DOMRect | null {
    return this.isOpen() ? this.side.getBoundingClientRect() : null;
  }

  frame(): void {
    this.side.dataset.open = String(this.isOpen());
  }

  reserves(stage: Rect): Partial<Reserved> {
    if (!this.isOpen() || stage.right <= stage.left) return {};
    const rect = this.side.getBoundingClientRect();
    const sheet = getComputedStyle(this.side).getPropertyValue('--sheet').trim() === '1';
    return sheet ? { bottom: Math.max(0, stage.bottom - rect.top + 8) } : { right: Math.max(0, stage.right - rect.left + 8) };
  }
}

/** The shelf along the bottom keeps its own strip: the eyepiece sits above it. */
export function shelfRoom(shelf: HTMLElement): (stage: Rect) => Partial<Reserved> {
  return (stage) => {
    if (shelf.hidden) return {};
    const rect = shelf.getBoundingClientRect();
    return rect.height > 0 ? { shelf: Math.max(0, stage.bottom - rect.top + 6) } : {};
  };
}
