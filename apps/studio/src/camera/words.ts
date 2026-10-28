// What the camera page and the studio's Phone panel say, and the few rules they share, in one place (2026-10-12:
// written twice, the two copies had drifted in nine places). Pure: no DOM, no fetch.

import type { Doctor, PhoneState } from './bridge-api';

/** The live sources, as the page's switch names them. "Blackmagic Camera" is the free Blackmagic Camera app on the
 * iPhone (by Blackmagic Design), never a Blackmagic camera; it picks its link (the cable or Wi-Fi) by itself. No camera
 * (Will, 2026-10-15) is pictures and videos only: nothing connects, nothing is searched for. */
export const SOURCES: [string, string][] = [['continuity', 'iPhone'], ['blackmagic', 'Blackmagic'], ['other', 'Other camera'], ['none', 'No camera']];

/** One line under the switch: what the chosen source is, for someone who has never heard of either. */
export const SOURCE_NOTES: Record<string, string> = {
  continuity: "The iPhone through Continuity Camera, built into the Mac: nothing to install. Its focus and exposure are the phone's own.",
  blackmagic: 'The free Blackmagic Camera app on the iPhone. It records in full quality, and its focus, exposure, colour and lenses are set from here.',
  other: 'Any other camera: a USB microscope camera, a webcam or a capture card plugged into this Mac, or a camera that streams over the network.',
  none: 'Pictures and videos only: open or drop them on the stage. Nothing connects, and nothing searches for a camera.',
};

/** A source's switch tab: the old pinned names ("wifi", "usb") were Blackmagic. */
export const tab = (kind: string | undefined | null): string => (kind === 'wifi' || kind === 'usb' ? 'blackmagic' : kind || 'continuity');

export const sourceTitle = (kind: string | undefined | null): string =>
  ({ continuity: 'The iPhone', blackmagic: 'Blackmagic Camera', other: 'The other camera', none: 'No camera' } as Record<string, string>)[tab(kind)] ?? 'The camera';

/** What a checklist button is called (camera_doctor.ACTIONS). */
export const ACTION_LABELS: Record<string, string> = {
  reconnect: 'Reconnect',
  reset: 'Reset camera',
  'try-again': 'Try connecting again', // Reset camera, as the not-connected guide offers it
  'start-stream': "Start the phone's stream",
  'use-iphone': 'Use the iPhone',
  'settings-file': 'Get the settings file',
  'phone-check': 'Run the Phone check',
};

/** What a checklist button says while it runs, and once it is done. */
export const ACTION_RUNNING: Record<string, string> = {
  reconnect: 'Reconnecting…',
  reset: 'Resetting…',
  'try-again': 'Connecting…',
  'start-stream': 'Starting…',
  'use-iphone': 'Switching…',
};
export const ACTION_DONE: Record<string, string> = {
  reconnect: 'Asked the camera for its picture again.',
  reset: 'The camera link was restarted.',
  'try-again': 'Tried again.',
  'start-stream': "The phone's stream was started.",
};

export const ACTION_HINTS: Record<string, string> = {
  reconnect: 'Ask the camera for its picture again. Try this first.',
  reset: "Restart the camera link: Blackmagic's stream from the phone, or Continuity Camera for the iPhone. For when Reconnect doesn't bring the picture back.",
};

/** The headline: one sentence for the state of the picture. */
export function headline(doctor: Doctor | null, switchingTo: string | null = null): string {
  if (switchingTo) return `Switching to ${sourceTitle(switchingTo).replace(/^The /, 'the ')}`;
  if (!doctor) return "The camera bridge isn't running";
  if (doctor.mode === 'off') return 'No camera';
  if (doctor.source === 'footage') return 'A picture or video is playing; camera checks are paused';
  if (doctor.live && !doctor.first) return `${sourceTitle(doctor.chosen ?? doctor.source)} is live${doctor.via ? `, over ${doctor.via}` : ''}`;
  return doctor.headline;
}

/** The status dot: live, something broke, or waiting (starting, or not connected yet: neither is a fault). The doctor
 * decides what live means. */
export function dotState(doctor: Doctor | null): 'live' | 'fault' | 'work' | 'off' {
  if (!doctor) return 'off';
  if (doctor.mode === 'off') return 'off'; // no camera chosen: calm, never a fault
  if (doctor.live) return 'live';
  if (doctor.mode === 'connect') return 'work';
  const first = doctor.items.find((item) => item.id === doctor.first);
  return first ? 'fault' : 'work';
}

/** "All 11 checks pass" or "1 of 11 checks need attention". */
export function checksSummary(doctor: Doctor): { ok: boolean; text: string } {
  if (doctor.mode === 'connect') return { ok: true, text: "Where it's up to" }; // not connected yet: nothing failed
  if (!doctor.items.length) return { ok: true, text: 'No camera checks to report' };
  const failing = doctor.items.filter((i) => i.ok === false).length;
  const passing = doctor.items.filter((i) => i.ok === true).length;
  if (failing) return { ok: false, text: `${failing} of ${doctor.items.length} checks need attention` };
  const unchecked = doctor.items.length - passing;
  return { ok: true, text: `All ${passing} checks pass${unchecked ? `, ${unchecked} not checked yet` : ''}` };
}

/** The first-time Blackmagic Camera setup, four steps: get the app (the user says so, or the app answers), let the
 * Mac reach it (its API answers), import HookeUI's settings, choose them. Done from the checklist's own facts;
 * never shown over a live picture (the phone's API can be found a few seconds after its picture arrives), and never
 * again once it has been set up on this Mac (the app closed is then only "open it"). */
export function setupSteps(doctor: Doctor | null, haveApp = false): { show: boolean; done: boolean[]; now: number } {
  const fact = (id: string) => doctor?.items.find((i) => i.id === id)?.ok === true;
  const app = fact('app');
  const settings = app && fact('settings');
  const quality = settings && fact('quality');
  const done = [haveApp || app, app, settings, quality];
  const show = tab(doctor?.source) === 'blackmagic' && !doctor?.live && !doctor?.setUp && !(app && settings && quality);
  return { show, done, now: done.indexOf(false) };
}

/** How evenly frames arrive: at 30 fps one comes every 33 ms; "late" is how much longer than that the slowest 1 in 20
 * waited (the p95 gap). `even` is the bridge's own verdict (27 fps or more, p95 50 ms or less). */
export function smoothness(fps: number | null | undefined, p95Ms: number | null | undefined, even: boolean | undefined): { word: string; line: string } {
  const every = fps ? 1000 / fps : 33;
  const late = p95Ms != null ? Math.max(0, Math.round(p95Ms - every)) : null;
  const word = even ? 'smooth' : late != null && late <= 70 ? 'slight stutter' : 'stutters';
  return { word, line: late == null ? '' : even ? `a frame every ${Math.round(every)} ms` : `1 in 20 frames ${late} ms late` };
}

/** "2 h 31 min" from seconds. */
export function timeLeft(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

/** Zoom within the lens: the API gives 0 (the lens as it is) to 1 (its closest), not a magnification. */
export const zoomWords = (z: number): string => (z <= 0.005 ? 'none' : z >= 0.995 ? 'closest' : `${Math.round(z * 100)}%`);

export const tintWords = (t: number): string => (t > 0 ? `+${t}` : String(t));

/** Focus and exposure held at 30 fps: nothing on the phone changes the view by itself (phone_control.Phone.locked). */
export const held = (p: PhoneState): boolean => p.autofocus === false && p.autoExposure === 'Off' && String(p.fps ?? '').startsWith('30');

/** The lens buttons: only lenses the Phone check switched to, the one in use pressed. */
export function lensChoices(p: PhoneState): { mm: number; current: boolean }[] {
  return [...(p.lenses ?? [])].sort((a, b) => a - b).map((mm) => ({ mm, current: mm === p.focalLength }));
}

export const ISOS = [54, 64, 80, 100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1000, 1250, 1600, 2000, 2500, 3200];
export const SHUTTERS = [30, 48, 50, 60, 100, 120, 125, 200, 250, 500, 1000, 2000];
export const FRAME_RATES = ['24', '30', '60'];

/** White balance presets, by the light a microscope's lamp gives. */
export const WHITE_BALANCES: [number, string][] = [
  [3200, 'tungsten or halogen lamp'], [4000, 'fluorescent'], [5600, 'daylight or white LED'], [6500, 'cool LED or overcast daylight'],
];

/** The lowest white balance the Mac writes; Auto can land below it (2470 K on 2026-10-12), so a slider reaches that. */
export const WB_MIN = 2500;
export const WB_MAX = 10000;

export const nearest = (list: number[], v: number): number => list.reduce((best, x) => (Math.abs(x - v) < Math.abs(best - v) ? x : best), list[0]);
