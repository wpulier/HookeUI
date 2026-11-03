/** Which microphone Talk (voice.ts) and Record (recorder.ts) listen with. Will, 3 Nov: the Mac's own microphone (the
 * built-in one, or a headset he plugged in), never the iPhone unless he picks it, with a picker that is remembered.
 * macOS often makes a nearby iPhone the default input (Continuity), above all while the phone is the microscope camera:
 * the camera bridge opens only its video, so a microphone opened on the phone is ours alone (and the bridge's rule is
 * that only it opens the iPhone, experiments/iphone-camera-feed/AGENTS.md).
 *
 * The choice is made before anything is opened, from the device list (it has labels once the microphone has been
 * allowed), and opened by its id. With no labels yet (a first run), the default is opened, its label read, and it is
 * stopped at once if it is a phone. The pick is kept in this browser's storage as `talk-mic` (its id and label; ids
 * can change, so the label is the fallback). */

export type Input = { kind: string; deviceId: string; label: string };
/** A microphone the user picked: its id, and its label to find it again when the id changes. */
export interface MicPick { id: string; label: string }

export const MIC_KEY = 'talk-mic';
/** What Talk says when the only microphone left is a phone the user hasn't picked. */
export const ONLY_PHONE = 'Talk didn’t use the iPhone’s microphone: the iPhone is the camera. Plug in a microphone, or choose the iPhone under Microphone in the camera menu.';

// No word break: Will's phone is "iPhoneWild" ("iPhoneWild Microphone").
const PHONE = /iphone|ipad/i;
// The Mac's own microphone, also in other languages ("Micrófono integrado", "MacBook Pro-Mikrofon", "内蔵マイク").
const MAC_OWN = /built-in|internal|macbook|imac|mac mini|mac studio|mac pro|studio display|integr|intégré|内蔵|内建|内置|내장/i;
// Inputs that are not a person's microphone: capture cards, virtual and loopback devices.
const NOT_A_MIC = /blackmagic|ultrastudio|capture|virtual|loopback|blackhole|soundflower|zoomaudio|teams audio/i;
// Chrome lists the default and the communications device again under these ids.
const ALIASES = new Set(['default', 'communications']);
const DEFAULT_PREFIX = /^default\s*-\s*/i;

/** The names the camera bridge knows its iPhone by, without " Camera" ("iPhoneWild"): a renamed phone's microphone is
 * "<name> Microphone", whatever the name. Told by the camera menu (phonepanel.ts) from the bridge's /api/health. */
const phoneNames = new Set<string>();

/** The bridge's iPhone camera ("iPhoneWild Camera"): its microphone ("iPhoneWild Microphone") counts as a phone. */
export function notePhoneCamera(name: string | null | undefined): void {
  const base = (name ?? '').trim().replace(/\s+camera$/i, '');
  if (base && base.length < (name ?? '').trim().length) phoneNames.add(base.toLowerCase());
}

export function knownPhones(): string[] {
  return [...phoneNames];
}

/** Whether a microphone label is a phone's: any iPhone or iPad, or the bridge's own phone under any name. */
export function isPhone(label: string, phones: readonly string[] = knownPhones()): boolean {
  if (PHONE.test(label)) return true;
  const lower = label.toLowerCase();
  return phones.some((name) => lower.includes(`${name.toLowerCase()} microphone`));
}

function labelled(devices: readonly Input[]): Input[] {
  return devices.filter((d) => d.kind === 'audioinput' && d.label && !ALIASES.has(d.deviceId));
}

/** The microphone to open: the saved pick if it is here (by id, else by label), else the system default when it is a
 * real microphone (a headset or USB microphone the user plugged in), else the Mac's own, else any input that is not a
 * phone, a capture card or a loopback. Never a phone unless it is the saved pick. Null with no labels (no permission
 * yet) or nothing usable. */
export function chooseMicrophone(devices: readonly Input[], saved: MicPick | null,
  phones: readonly string[] = knownPhones()): Input | null {
  const inputs = labelled(devices);
  if (saved) {
    const pick = inputs.find((d) => d.deviceId === saved.id) ?? inputs.find((d) => d.label === saved.label);
    if (pick) return pick;
  }
  const usable = inputs.filter((d) => !isPhone(d.label, phones) && !NOT_A_MIC.test(d.label));
  // The default: Chrome names it in its alias ("Default - AirPods Pro"); WebKit lists it first.
  const alias = devices.find((d) => d.kind === 'audioinput' && d.deviceId === 'default' && d.label);
  const fallback = inputs[0];
  const named = alias ? alias.label.replace(DEFAULT_PREFIX, '') : fallback?.label;
  const preferred = usable.find((d) => d.label === named);
  return preferred ?? usable.find((d) => MAC_OWN.test(d.label)) ?? usable[0] ?? null;
}

/** How the picker names an input: the bridge's phone (or, while the bridge's isn't known, any iPhone) is "iPhone
 * (camera)". */
export function microphoneName(input: Input, phones: readonly string[] = knownPhones()): string {
  if (!isPhone(input.label, phones)) return input.label;
  const lower = input.label.toLowerCase();
  const theCamera = phones.some((name) => lower.includes(`${name.toLowerCase()} microphone`))
    || (phones.length === 0 && /iphone/i.test(input.label));
  return theCamera ? 'iPhone (camera)' : input.label;
}

// --- The saved pick (localStorage `talk-mic`); every read and write is tried, since site storage can be blocked. ---

const listeners = new Set<() => void>();

export function savedMicrophone(): MicPick | null {
  try {
    const raw = JSON.parse(localStorage.getItem(MIC_KEY) ?? 'null') as Partial<MicPick> | null;
    if (!raw || typeof raw.label !== 'string' || !raw.label) return null;
    return { id: typeof raw.id === 'string' ? raw.id : '', label: raw.label };
  } catch {
    return null;
  }
}

/** Keep the pick (null: back to automatic) and tell anyone listening (a Talk in progress switches to it). */
export function saveMicrophone(pick: MicPick | null): void {
  try {
    if (pick) localStorage.setItem(MIC_KEY, JSON.stringify({ id: pick.id, label: pick.label }));
    else localStorage.removeItem(MIC_KEY);
  } catch { /* not remembered: this Talk still uses it */ }
  for (const listener of [...listeners]) listener();
}

/** Called after each pick; returns the way to stop listening. */
export function onMicrophonePick(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// --- Opening it ---

type Media = Pick<MediaDevices, 'getUserMedia'> & Partial<Pick<MediaDevices, 'enumerateDevices'>>;

/** A refusal that no other microphone fixes: the permission itself. */
function refused(error: unknown): boolean {
  const name = (error as { name?: unknown } | null)?.name;
  return name === 'NotAllowedError' || name === 'SecurityError';
}

/** No microphone Talk may use without being asked (only the phone is here). */
export class MicChoiceError extends Error {}

export async function listInputs(media: Partial<Pick<MediaDevices, 'enumerateDevices'>> | undefined = globalThis.navigator?.mediaDevices): Promise<Input[]> {
  if (!media?.enumerateDevices) return [];
  try { return await media.enumerateDevices(); } catch { return []; }
}

const stop = (stream: MediaStream) => stream.getTracks().forEach((track) => track.stop());

/** Open the chosen microphone with `audio`'s processing. Labels known: the choice is opened by its id, first. No labels
 * (a first run): the saved pick's id is tried, else the default is opened and, if it is a phone, stopped at once and
 * the choice opened instead. `wanted` false (Talk ended meanwhile): nothing more is opened, what was is stopped, and
 * the answer is null. Throws the browser's refusal, or MicChoiceError when only a phone is left. */
export async function openMicrophone(audio: MediaTrackConstraints, wanted: () => boolean = () => true,
  media: Media = navigator.mediaDevices): Promise<MediaStream | null> {
  const saved = savedMicrophone();
  const exact = (id: string) => media.getUserMedia({ audio: { ...audio, deviceId: { exact: id } } });
  if (media.enumerateDevices) {
    const devices = await listInputs(media);
    if (!wanted()) return null;
    if (labelled(devices).length) {
      const chosen = chooseMicrophone(devices, saved);
      if (!chosen) throw new MicChoiceError(ONLY_PHONE);
      try { return keep(await exact(chosen.deviceId), wanted); }
      catch (error) { if (refused(error)) throw error; } // gone, or its id changed: read the list again below
    } else if (saved?.id) {
      try { return keep(await exact(saved.id), wanted); }
      catch (error) { if (refused(error)) throw error; }
    }
    if (!wanted()) return null;
  }
  const mic = await media.getUserMedia({ audio });
  if (!wanted()) { stop(mic); return null; }
  const label = mic.getAudioTracks()[0]?.label ?? '';
  if (!label) return mic; // nothing to tell it by
  const phone = isPhone(label);
  if (phone) stop(mic); // the label read is all a phone is opened for
  const chosen = chooseMicrophone(await listInputs(media), saved);
  if (!chosen) {
    if (phone) throw new MicChoiceError(ONLY_PHONE);
    return mic;
  }
  if (chosen.label === label) {
    if (!phone) return mic;
    return keep(await exact(chosen.deviceId), wanted); // the saved pick is the phone: opened again, by its id
  }
  if (!wanted()) { if (!phone) stop(mic); return null; }
  if (phone) return keep(await exact(chosen.deviceId), wanted);
  try {
    const better = await exact(chosen.deviceId);
    stop(mic);
    return keep(better, wanted);
  } catch {
    return mic; // the default is a real microphone: kept when the choice can't be opened
  }
}

function keep(stream: MediaStream, wanted: () => boolean): MediaStream | null {
  if (wanted()) return stream;
  stop(stream);
  return null;
}
