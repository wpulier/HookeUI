import { describe, expect, it } from 'vitest';
import { FrameTiming } from './frametiming';
import type { FrameHeader } from './protocol';

const header = (id: number, t: number, an: number | null, sent: number): FrameHeader =>
  ({ type: 'frame', id, w: 1280, h: 720, dx: 0, dy: 0, epoch: 0, tracked: true, steady: true, speed: 0, t, an, sent, fov: null });

describe('FrameTiming', () => {
  it('reports each stage once per frame, every few seconds', () => {
    const timing = new FrameTiming(10);
    expect(timing.flush(100)).toBeNull(); // starts the clock
    timing.painted(header(1, 100.0, 100.02, 100.03), 100.05);
    timing.painted(header(1, 100.0, 100.02, 100.03), 100.2); // painted again: not counted twice
    timing.painted(header(2, 100.1, 100.13, 100.135), 100.16);
    timing.painted(header(3, 100.2, 100.21, 100.215), 100.25);
    expect(timing.flush(105)).toBeNull();
    const report = timing.flush(110.5);
    expect(report?.frames).toBe(3);
    expect(report?.stages.analyse.p50).toBe(20); // 10, 20, 30 ms
    expect(report?.stages.total.p90).toBe(60); // 50, 60, 50 ms
    expect(timing.flush(121)).toBeNull(); // nothing new painted
  });

  it('measures camera-to-screen from the bridge when the frame says when it took it', () => {
    // Demo night (2026-10-12): frames waited between the bridge and the app, and the meter (measuring from the app)
    // never saw it.
    const timing = new FrameTiming(0);
    timing.flush(1);
    timing.painted({ ...header(1, 10.5, 10.52, 10.53), ba: 10.0 }, 10.55);
    const report = timing.flush(11);
    expect(report?.stages.intake.p50).toBe(500);
    expect(report?.stages.total.p50).toBe(550);
  });

  it('skips stages a frame has no time for', () => {
    const timing = new FrameTiming(0);
    timing.flush(1);
    timing.painted(header(1, 10, null, 10.01), 10.03);
    const report = timing.flush(11);
    expect(report?.stages.analyse).toBeUndefined();
    expect(report?.stages.deliver.p50).toBe(20);
  });
});
