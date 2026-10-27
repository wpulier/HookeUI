import { describe, expect, it } from 'vitest';
import { cameraControl, specimenName, type CameraFacts } from './camera-label';

const base: CameraFacts = { asset: false, footage: false, title: 'Recording 3', live: false, off: false, sourceLabel: 'iPhone', device: 'iPhone 17 Pro', doctor: '' };

describe('the Stage bar\'s specimen control', () => {
  it('says live and which camera when the Stage is live', () => {
    expect(cameraControl({ ...base, live: true })).toMatchObject({ state: 'live', text: 'Live · iPhone 17 Pro' });
  });
  it('names the picture or recording on the Stage by its short title, and how to go back to the camera', () => {
    const words = cameraControl({ ...base, asset: true, live: true, title: 'Human cells · David Root (CC0)', still: true });
    expect(words).toMatchObject({ state: 'asset', text: 'Human cells' });
    expect(words.title).toContain('Picture on the Stage');expect(words.title).toContain('Switch to live camera');
    expect(cameraControl({ ...base, asset: true, title: 'Day 2' }).title).toContain('Recording on the Stage');
    expect(cameraControl({ ...base, asset: true, off: true, title: 'Human cells' })).toMatchObject({ text: 'Human cells' });
    expect(cameraControl({ ...base, asset: true, off: true, title: 'Human cells' }).title).not.toContain('Switch to live camera');
  });
  it('where the Stage bar names the specimen, names only the camera: No camera, its device, else Camera, with the same title', () => {
    const picture = { ...base, asset: true, still: true, title: 'Human-cells, 2026-10-25 (picture)', named: true };
    expect(cameraControl({ ...picture, off: true })).toMatchObject({ state: 'camera-none', text: 'No camera' });
    expect(cameraControl(picture)).toMatchObject({ state: 'camera', text: 'iPhone 17 Pro' });
    expect(cameraControl({ ...picture, device: '' })).toMatchObject({ state: 'camera', text: 'Camera' });
    expect(cameraControl({ ...picture, off: true }).title).toBe(cameraControl({ ...picture, off: true, named: false }).title);
    expect(cameraControl(picture).title).toContain('Picture on the Stage: “Human-cells, 2026-10-25 (picture)”');
    // Live, footage and nothing on the Stage say what they always said.
    expect(cameraControl({ ...base, live: true, named: true })).toMatchObject({ state: 'live', text: 'Live · iPhone 17 Pro' });
  });
  it('invites a choice when nothing is on the Stage, and says when a chosen camera needs attention', () => {
    expect(cameraControl({ ...base, off: true })).toMatchObject({ state: 'none', text: 'Choose a specimen' });
    expect(cameraControl({ ...base, doctor: 'The iPhone offers no camera', dot: 'fault' })).toMatchObject({ state: 'offline', text: 'Camera · needs attention' });
    expect(cameraControl({ ...base, doctor: 'Connect your iPhone', dot: 'work' })).toMatchObject({ text: 'Camera · connecting' });
    expect(cameraControl({ ...base, doctor: 'Connect your iPhone', dot: 'work' }).text).not.toContain('attention');
    expect(cameraControl(base).text).toBe('Camera · no picture');
  });
  it('names test footage as footage', () => {
    expect(cameraControl({ ...base, footage: true, title: 'cells.mp4' }).text).toBe('Test footage · cells.mp4');
  });
  it('keeps a long title short', () => {
    expect(specimenName('A very long specimen title that goes on and on')).toBe('A very long specimen title that…');
  });
});
