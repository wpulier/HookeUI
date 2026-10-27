// Drag a box to outline (AGENTS.md "Seeing and acting at once"): a press that moves under 8 px is
// a plain click, as before; past it the drag is a box, sent once on release in frame pixels
// whichever way it was dragged; Escape cancels it, and the browser's click after a drag or a
// cancelled one never becomes a point click.
import { describe, expect, test } from 'vitest';
import { BoxDrag, DRAG_MIN_PX, frameBox, ordered } from './boxdrag';

// A view whose picture sits 10 px right and 20 px down, shown at twice its size.
const toFrame = (x: number, y: number): [number, number] => [(x - 10) / 2, (y - 20) / 2];
const picture = { w: 1280, h: 720 };

describe('BoxDrag', () => {
  test('a press that moves less than the threshold is a plain click', () => {
    const drag = new BoxDrag();
    drag.press(100, 100);
    expect(drag.move(100 + DRAG_MIN_PX, 100)).toBe(false);
    expect(drag.rect()).toBeNull();
    expect(drag.release(105, 104)).toEqual({ kind: 'click' });
    expect(drag.clickAllowed()).toBe(true);
  });

  test('past the threshold it is a box drag, drawn and sent once on release', () => {
    const drag = new BoxDrag();
    drag.press(100, 100);
    expect(drag.move(109, 100)).toBe(true);
    expect(drag.dragging).toBe(true);
    // Back near the start it stays a box: the user is still dragging.
    expect(drag.move(103, 102)).toBe(true);
    drag.move(60, 140);
    expect(drag.rect()).toEqual([60, 100, 100, 140]);
    expect(drag.release(60, 150)).toEqual({ kind: 'box', from: [100, 100], to: [60, 150] });
    expect(drag.dragging).toBe(false);
    expect(drag.clickAllowed()).toBe(false); // the browser's click after the drag
    expect(drag.clickAllowed()).toBe(true); // the next real click goes ahead
  });

  test('Escape cancels the drag: its release does nothing and its click is swallowed', () => {
    const drag = new BoxDrag();
    drag.press(100, 100);
    drag.move(200, 200);
    drag.cancel();
    expect(drag.dragging).toBe(false);
    expect(drag.rect()).toBeNull();
    expect(drag.release(200, 200)).toEqual({ kind: 'none' });
    expect(drag.clickAllowed()).toBe(false);
  });

  test('a release that never sent its click does not eat the next click', () => {
    const drag = new BoxDrag();
    drag.press(0, 0);
    drag.move(50, 50);
    drag.release(50, 50); // released off the page: no click event came
    drag.press(10, 10);
    expect(drag.release(10, 10)).toEqual({ kind: 'click' });
    expect(drag.clickAllowed()).toBe(true);
  });

  test('a move without a press is not a drag', () => {
    const drag = new BoxDrag();
    expect(drag.move(300, 300)).toBe(false);
    expect(drag.release(300, 300)).toEqual({ kind: 'none' });
    expect(drag.clickAllowed()).toBe(true);
  });
});

describe('frameBox', () => {
  test('is the same box in frame pixels whichever way it was dragged', () => {
    const expected = [45, 40, 95, 90];
    const corners: [[number, number], [number, number]][] = [
      [[100, 100], [200, 200]], // down and right
      [[200, 200], [100, 100]], // up and left
      [[200, 100], [100, 200]], // down and left
      [[100, 200], [200, 100]], // up and right
    ];
    for (const [from, to] of corners) expect(frameBox(from, to, toFrame, picture)).toEqual(expected);
  });

  test('is kept to the picture when the drag runs past its edge', () => {
    expect(frameBox([0, 0], [3000, 100], toFrame, picture)).toEqual([0, 0, 1280, 40]);
  });

  test('is null when too thin to hold anything or a corner cannot be converted', () => {
    expect(frameBox([100, 100], [102, 300], toFrame, picture)).toBeNull();
    expect(frameBox([100, 100], [200, 200], () => null, picture)).toBeNull();
  });

  test('ordered puts the smallest corner first', () => {
    expect(ordered([5, 1], [2, 9])).toEqual([2, 1, 5, 9]);
  });
});
