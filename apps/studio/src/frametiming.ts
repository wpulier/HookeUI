import type { FrameHeader } from './protocol';

/** Where camera-to-screen time goes, per stage (roadmap W0-6): from the bridge taking the frame to the
 * orchestrator reading it (`intake`: where demo night's backlog built up unseen, 2026-10-12), to analysed
 * (disc, motion, focus), to handed to the studios, to painted here. `total` runs from the bridge when the
 * frame says when it took it (`ba`), else from the orchestrator. The server and
 * the studio run on the same machine, so their clocks agree. Collected per painted frame and
 * sent as p50/p90 every `everyS` seconds (the `timing` command); make day-report prints them. */
export interface TimingReport {
  frames: number;
  stages: Record<string, { p50: number; p90: number }>;
}

const STAGES = ['intake', 'analyse', 'publish', 'deliver', 'total'] as const;
export type Stage = (typeof STAGES)[number];
const RECENT = 150; // the performance meter's window: about 5 s of frames at 30 fps

export class FrameTiming {
  private samples: Record<Stage, number[]> = { intake: [], analyse: [], publish: [], deliver: [], total: [] };
  private latest: Record<Stage, number[]> = { intake: [], analyse: [], publish: [], deliver: [], total: [] }; // the last RECENT frames
  private last = 0;
  private lastId = -1;

  private readonly everyS: number;

  constructor(everyS = 10) {
    this.everyS = everyS;
  }

  /** A frame painted at `nowS` (wall seconds). Each frame counts once. */
  painted(header: FrameHeader, nowS: number): void {
    if (header.id === this.lastId) return;
    this.lastId = header.id;
    const ms = (a: number | null | undefined, b: number | null | undefined) =>
      typeof a === 'number' && typeof b === 'number' && b >= a ? (b - a) * 1000 : null;
    const values = {
      intake: ms(header.ba, header.t),
      analyse: ms(header.t, header.an),
      publish: ms(header.an, header.sent),
      deliver: ms(header.sent, nowS),
      total: ms(header.ba ?? header.t, nowS),
    };
    for (const stage of STAGES) {
      const v = values[stage];
      if (v !== null && v < 60_000) {
        this.samples[stage].push(v);
        const latest = this.latest[stage];
        latest.push(v);
        if (latest.length > RECENT) latest.shift();
      }
    }
  }

  /** A stage's p90 over the last few seconds of frames (the performance meter), or null. */
  recent(stage: Stage, q = 0.9): number | null {
    const sorted = [...this.latest[stage]].sort((a, b) => a - b);
    return sorted.length ? Math.round(pick(sorted, q)) : null;
  }

  /** The report for the frames since the last one, once `everyS` has passed; else null. */
  flush(nowS: number): TimingReport | null {
    if (this.last === 0) this.last = nowS;
    if (nowS - this.last < this.everyS || this.samples.total.length === 0) return null;
    this.last = nowS;
    const stages: TimingReport['stages'] = {};
    for (const stage of STAGES) {
      const sorted = [...this.samples[stage]].sort((a, b) => a - b);
      if (sorted.length) stages[stage] = { p50: Math.round(pick(sorted, 0.5)), p90: Math.round(pick(sorted, 0.9)) };
    }
    const frames = this.samples.total.length;
    for (const stage of STAGES) this.samples[stage] = [];
    return { frames, stages };
  }
}

function pick(sorted: number[], q: number): number {
  return sorted[Math.min(sorted.length - 1, Math.round(q * (sorted.length - 1)))];
}
