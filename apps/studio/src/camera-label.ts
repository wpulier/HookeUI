/** What the Tab row's specimen control says: the specimen on the Stage, live or not (Will, 2026-10-23: "the sun of the
 * app is the specimen, live or recorded"). A picture or recording is named by its own short title; the live camera says
 * Live and which camera; with nothing on the Stage it invites a choice. The camera's own state is said only when a
 * chosen camera is what should be on the Stage. Moved out of main.ts so it can be tested. */
export interface CameraFacts {
  asset: boolean; footage: boolean; title: string; live: boolean; off: boolean; sourceLabel: string; device: string; doctor: string;
  /** The camera checklist's status dot (camera/words.ts dotState): a fault, or still starting/connecting (never a fault). */
  dot?: 'live' | 'fault' | 'work' | 'off';
  /** The asset on the Stage is a picture (a held still), not a recording. */
  still?: boolean;
  /** The Stage bar already names the specimen on its left (the workspace): this control then names the camera only. */
  named?: boolean;
}
export interface CameraWords { state: string; text: string; title: string }

/** A specimen's short name: its title's first part ("Human cells" from "Human cells · David Root (CC0)"). */
export function specimenName(title: string): string {
  const first = title.split(' · ')[0].trim();
  return first.length > 32 ? `${first.slice(0, 31).trimEnd()}…` : first;
}

export function cameraControl(facts: CameraFacts): CameraWords {
  const change = 'Click to change the specimen: a picture, a recording, or a camera.';
  if (facts.asset) {
    const kind = facts.still ? 'Picture' : 'Recording';
    const title = `${kind} on the Stage: “${facts.title}”. ${change}${facts.off ? '' : ' Switch to live camera is there too.'}`;
    // Named once: where the Stage bar names the specimen, this control is the camera's (the mock's "No camera"), with
    // the same menu and title, and no picture-frame mark.
    if (facts.named) return { state: facts.off ? 'camera-none' : 'camera', text: facts.off ? 'No camera' : facts.device || 'Camera', title };
    return { state: 'asset', text: specimenName(facts.title) || kind, title };
  }
  if (facts.off) return { state: 'none', text: 'Choose a specimen', title: 'Nothing is on the Stage and no camera is chosen. Click to open a picture or recording, or choose a camera.' };
  if (facts.footage) return { state: 'footage', text: `Test footage · ${facts.title}`, title: `Test footage stands for the live camera: ${facts.title}. ${change}` };
  if (facts.live) return { state: 'live', text: facts.device ? `Live · ${facts.device}` : 'Live', title: `The live camera${facts.device ? `, ${facts.device}` : ''}, on the Stage. ${change}` };
  // A chosen camera with no picture yet: a fault says so; starting or not connected yet is never a fault.
  if (facts.dot === 'fault') return { state: 'offline', text: 'Camera · needs attention', title: `${facts.doctor || 'The camera has a problem'}. ${change}` };
  if (facts.dot === 'work') return { state: 'offline', text: 'Camera · connecting', title: `${facts.doctor || 'Connecting the camera'}. ${change}` };
  return { state: 'offline', text: 'Camera · no picture', title: `${facts.doctor || 'No picture from the camera'}. ${change}` };
}
