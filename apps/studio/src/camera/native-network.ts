import type { Doctor } from './bridge-api';

type NativeNetwork = { postMessage(message: { op: 'observe'; needed: boolean }): Promise<unknown> };
type NativeHost = Window & { webkit?: { messageHandlers?: { hookeCameraNetwork?: NativeNetwork } } };

/** Native permission observation is not evidence of a camera picture or control readiness. */
export async function cameraNetworkDoctor(doctor: Doctor, native?: NativeNetwork): Promise<Doctor> {
  if (!native) return doctor;
  const needed = doctor.source === 'blackmagic';
  let value: unknown;
  try { value = await native.postMessage({ op: 'observe', needed }); } catch { return doctor; }
  if (!needed || doctor.live || !value || typeof value !== 'object' || !('state' in value) || value.state !== 'denied') return doctor;
  const fix = 'In System Settings › Privacy & Security › Local Network, allow this Hooke app, then Reconnect.';
  const item = { id: 'native-network', title: 'Allow camera access on this Mac', ok: false,
    evidence: 'macOS refused Local Network access', fix, action: null, problem: 'Local Network access is denied' };
  return { ...doctor, mode: 'problem', headline: item.title, first: item.id,
    items: [item, ...doctor.items], waiting: 'Waiting for Local Network access',
    report: `${doctor.report ?? ''}\nLocal Network: refused by macOS. ${fix}` };
}

export function nativeCameraNetwork(): NativeNetwork | undefined {
  return typeof window === 'undefined' ? undefined : (window as NativeHost).webkit?.messageHandlers?.hookeCameraNetwork;
}
