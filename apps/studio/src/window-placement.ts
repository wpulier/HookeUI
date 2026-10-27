import type { FrameHeader } from './protocol';
import type { LiveSample, WindowSource } from './workspace-windows';

/** A live lease alone says nothing about where its subject is on the current frame. */
export function livePlacementObject(source: Pick<WindowSource, 'identity' | 'kind'>, sample: LiveSample | null,
  frame: Pick<FrameHeader, 'run' | 'boot' | 'epoch'> | null, fresh: boolean): string | null {
  const object = source.identity.object;
  if (!fresh || source.kind !== 'live' || typeof object !== 'string' || !sample || !frame
    || !frame.run || !frame.boot || sample.run !== frame.run || source.identity.run !== frame.run
    || sample.boot !== frame.boot || sample.epoch == null || sample.epoch !== frame.epoch) return null;
  return object; // caller must additionally verify this object's pixels on the current view
}
