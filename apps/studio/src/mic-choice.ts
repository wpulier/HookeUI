/** Which microphone Talk listens with (voice.ts). Talk opens the system's default input, but macOS often makes a
 * nearby iPhone or iPad the default (Continuity), especially while the phone serves as a camera: in the 30 Oct demo
 * Talk heard the phone across the room. When the default is a phone and this Mac has its own microphone, Talk uses
 * the Mac's; failing that, another real microphone. Any other default (a headset, a USB microphone) is the user's
 * choice and is kept. */

type Input = { kind: string; deviceId: string; label: string };

const PHONE = /\b(iphone|ipad)\b/i;
const MAC_OWN = /built-in|macbook|imac|mac mini|mac studio|studio display/i;
// Inputs that are not a person's microphone: capture cards, virtual and loopback devices.
const NOT_A_MIC = /blackmagic|ultrastudio|capture|virtual|loopback|blackhole|soundflower|zoomaudio|teams audio/i;
// Chrome lists the default and the communications device again under these ids.
const ALIASES = new Set(['default', 'communications']);

/** The deviceId to use instead of the microphone labelled `current`, or null to keep it. */
export function betterMicrophone(current: string, devices: readonly Input[]): string | null {
  if (!current || !PHONE.test(current)) return null;
  const others = devices.filter((d) => d.kind === 'audioinput' && d.label && !ALIASES.has(d.deviceId)
    && !PHONE.test(d.label) && !NOT_A_MIC.test(d.label));
  return (others.find((d) => MAC_OWN.test(d.label)) ?? others[0])?.deviceId ?? null;
}
