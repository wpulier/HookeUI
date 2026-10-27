import { expect, test } from 'vitest';
import { meterPopover } from './meter-popover';

test('phone meters at either dock edge stay inside the viewport after page scrolling', () => {
  for (const right of [42, 378]) {
    const box = meterPopover({ left: right - 28, right, top: 630, bottom: 658 }, { width: 390, height: 844 });
    expect(box.left).toBeGreaterThanOrEqual(12);
    expect(box.left + box.width).toBeLessThanOrEqual(378);
    expect(box.bottom! + box.maxHeight).toBeLessThanOrEqual(832);
  }
});

test('software keyboard viewport and an offscreen dock do not strand the panel', () => {
  const box = meterPopover({ left: 0, right: 32, top: 780, bottom: 808 }, { width: 390, height: 340, top: 80 });
  expect(box.bottom).toBe(12);
  expect(box.maxHeight).toBe(316);
  expect(box.left).toBe(12);
});
