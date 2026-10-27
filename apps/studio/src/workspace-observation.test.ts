// Synthetic pure collector contracts. No DOM, capture API, provider or renderer acceptance.
// Initial test-only commit deliberately precedes workspace-observation.ts; parent gate observes RED.
import { describe, expect, test } from 'vitest';
import { createInteractionState, reduceInteraction, snapshotInteraction, workspaceToSource } from './workspace-observation';

const contentHit = {
  presentationId: 'window-a', sourceKey: 'source-a', sourceGeneration: 1,
  hitPath: ['window-a'], targetCandidates: ['target-a'],
};

describe('request-time pointer and keyboard facts', () => {
  test('Send editor focus stays separate from the last content pointer and focus', () => {
    let state = createInteractionState();
    state = reduceInteraction(state, {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [200.25, 120.5], buttons: 0, atMs: 100, hit: contentHit,
    });
    state = reduceInteraction(state, {
      type: 'focus', atMs: 120,
      focus: { presentationId: 'window-a', controlId: 'window-a', role: 'group', editable: false },
    });
    const privateEditor = {
      presentationId: null, controlId: 'ask-input', role: 'textbox', editable: true,
      value: 'private draft must not be observed', text: 'private draft must not be observed',
    };
    state = reduceInteraction(state, { type: 'focus', atMs: 150, focus: privateEditor });
    state = reduceInteraction(state, {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [1350.5, 840.25], buttons: 0, atMs: 180,
      hit: { presentationId: null, sourceKey: null, hitPath: ['ask-send'], targetCandidates: [] },
    });
    const snapshot = snapshotInteraction(state, { nowMs: 200, selected: ['target-a'] });
    expect(snapshot.focus.controlId).toBe('ask-input');
    expect(snapshot.pointer.css).toEqual([1350.5, 840.25]);
    expect(snapshot.lastContentPointer?.presentationId).toBe('window-a');
    expect(snapshot.lastContentFocus?.presentationId).toBe('window-a');
    expect(snapshot.selected).toEqual(['target-a']);
    expect(JSON.stringify(snapshot)).not.toContain('private draft');
  });

  test('keyboard content focus and explicit selection do not invent a mouse position', () => {
    const state = reduceInteraction(createInteractionState(), {
      type: 'focus', atMs: 100,
      focus: { presentationId: 'window-b', controlId: 'window-b', role: 'group', editable: false },
    });
    const snapshot = snapshotInteraction(state, { nowMs: 120, selected: ['target-b'] });
    expect(snapshot.pointer.css).toBeNull();
    expect(snapshot.pointer.modality).toBe('none');
    expect(snapshot.focus.presentationId).toBe('window-b');
    expect(snapshot.selected).toEqual(['target-b']);
  });

  test('snapshot uses only injected registered hits and never infers what words mean', () => {
    let state = reduceInteraction(createInteractionState(), {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [200, 120], buttons: 0, atMs: 100, hit: contentHit,
    });
    // A close button is the top registered control, not the underlying specimen hit.
    state = reduceInteraction(state, {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [205, 125], buttons: 0, atMs: 110,
      hit: { presentationId: 'window-a', sourceKey: null,
        hitPath: ['close:window-a', 'window-a'], targetCandidates: ['close-target-a'] },
    });
    const snapshot = snapshotInteraction(state, { nowMs: 120, selected: [] });
    expect(snapshot.pointer.hitPath).toEqual(['close:window-a', 'window-a']);
    expect(snapshot.pointer.targetCandidates).toEqual(['close-target-a']);
    expect(snapshot.pointer.sourcePoint).toBeNull();
  });

  test('capture recipient and the physically hit window are distinct during a drag', () => {
    const state = reduceInteraction(createInteractionState(), {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [700, 150], buttons: 1, atMs: 100, capturedBy: 'window-a',
      hit: { presentationId: 'window-b', sourceKey: 'source-b', hitPath: ['window-b'], targetCandidates: [] },
    });
    const snapshot = snapshotInteraction(state, { nowMs: 120, selected: [] });
    expect(snapshot.pointer.capturedBy).toBe('window-a');
    expect(snapshot.pointer.hitPath).toEqual(['window-b']);
  });

  test('request snapshot survives reducer and caller-array changes afterward', () => {
    const hit = { ...contentHit, hitPath: ['window-a'], targetCandidates: ['target-a'] };
    const state = reduceInteraction(createInteractionState(), {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [200.25, 120.5], buttons: 0, atMs: 100, hit,
    });
    const selected = ['target-a'];
    const snapshot = snapshotInteraction(state, { nowMs: 120, selected });
    hit.hitPath[0] = 'changed';
    selected[0] = 'changed';
    const newer = reduceInteraction(state, {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [900, 400], buttons: 0, atMs: 130, hit: { ...contentHit, presentationId: 'window-b' },
    });
    expect(snapshot.pointer.css).toEqual([200.25, 120.5]);
    expect(snapshot.pointer.hitPath).toEqual(['window-a']);
    expect(snapshot.selected).toEqual(['target-a']);
    expect(snapshotInteraction(newer, { nowMs: 150, selected: [] }).pointer.css).toEqual([900, 400]);
    expect(Object.isFrozen(snapshot.pointer.hitPath)).toBe(true);
  });

  test('leave invalidates current hover but retains a labeled historical content point', () => {
    let state = reduceInteraction(createInteractionState(), {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [200, 120], buttons: 0, atMs: 100, hit: contentHit,
    });
    state = reduceInteraction(state, { type: 'pointer-leave', pointerId: 1, atMs: 110 });
    const snapshot = snapshotInteraction(state, { nowMs: 120, selected: [] });
    expect(snapshot.pointer.state).toBe('outside');
    expect(snapshot.pointer.css).toBeNull();
    expect(snapshot.pointer.hitPath).toEqual([]);
    expect(snapshot.lastContentPointer?.historical).toBe(true);
    expect(snapshot.lastContentPointer?.presentationId).toBe('window-a');
  });

  test('hidden-page transition clears pointer and focus validity without reading editor text', () => {
    let state = reduceInteraction(createInteractionState(), {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [200, 120], buttons: 0, atMs: 100, hit: contentHit,
    });
    state = reduceInteraction(state, { type: 'visibility', visible: false, atMs: 110 });
    const snapshot = snapshotInteraction(state, { nowMs: 120, selected: [] });
    expect(snapshot.availability).toBe('unavailable');
    expect(snapshot.pointer.hitPath).toEqual([]);
    expect(snapshot.pointer.css).toBeNull();
  });
});

describe('touch contacts', () => {
  test('release does not create perpetual touch hover', () => {
    let state = reduceInteraction(createInteractionState(), {
      type: 'pointer-down', pointerId: 11, modality: 'touch', primary: true,
      css: [200, 120], buttons: 1, atMs: 100, hit: contentHit,
    });
    state = reduceInteraction(state, { type: 'pointer-up', pointerId: 11, atMs: 120 });
    const snapshot = snapshotInteraction(state, { nowMs: 140, selected: [] });
    expect(snapshot.contacts).toEqual([]);
    expect(snapshot.pointer.state).toBe('released');
    expect(snapshot.pointer.hitPath).toEqual([]);
    expect(snapshot.lastContentPointer?.historical).toBe(true);
  });

  test('primary pointer and two contacts stay bounded when a third contact arrives', () => {
    let state = createInteractionState();
    for (const [pointerId, primary, x] of [[11, true, 200], [12, false, 230], [13, false, 260]] as const) {
      state = reduceInteraction(state, {
        type: 'pointer-down', pointerId, modality: 'touch', primary,
        css: [x, 120], buttons: 1, atMs: 100, hit: contentHit,
      });
    }
    const snapshot = snapshotInteraction(state, { nowMs: 120, selected: [] });
    expect(snapshot.pointer.pointerId).toBe(11);
    expect(snapshot.contacts.map((c) => c.pointerId)).toEqual([11, 12]);
    expect(snapshot.omittedContacts).toBe(1);
    expect(snapshot.availability).toBe('partial');
  });

  test('cancel clears matching contact and capture owner; another active contact survives', () => {
    let state = createInteractionState();
    state = reduceInteraction(state, {
      type: 'pointer-down', pointerId: 11, modality: 'touch', primary: true,
      css: [200, 120], buttons: 1, atMs: 100, capturedBy: 'window-a', hit: contentHit,
    });
    state = reduceInteraction(state, {
      type: 'pointer-down', pointerId: 12, modality: 'touch', primary: false,
      css: [230, 120], buttons: 1, atMs: 110, hit: contentHit,
    });
    state = reduceInteraction(state, { type: 'pointer-cancel', pointerId: 11, atMs: 120 });
    const snapshot = snapshotInteraction(state, { nowMs: 130, selected: [] });
    expect(snapshot.contacts.map((c) => c.pointerId)).toEqual([12]);
    expect(snapshot.pointer.capturedBy).toBeNull();
    expect(snapshot.pointer.hitPath).toEqual([]);
  });
});

describe('finite CSS source transforms', () => {
  const view = {
    contentRect: [120.25, 60.5, 320, 240], clipRect: [120.25, 60.5, 320, 240],
    viewTransform: { sourceToWorkspace: [0.5, 0, 0, 0.5, 120.25, 60.5], originalSize: [640, 480] },
  };

  test('known source point uses CSS floats without DPR multiplication', () => {
    expect(workspaceToSource([200.25, 120.5], view)).toEqual([160, 120]);
  });

  test('window chrome and letterbox have no source hit', () => {
    expect(workspaceToSource([110.25, 50.5], view)).toBeNull();
    expect(workspaceToSource([450.25, 120.5], view)).toBeNull();
  });

  test('singular and nonfinite transforms do not invent a source hit', () => {
    expect(workspaceToSource([200.25, 120.5], {
      ...view, viewTransform: { ...view.viewTransform, sourceToWorkspace: [0, 0, 0, 0, 120.25, 60.5] },
    })).toBeNull();
    expect(() => workspaceToSource([200.25, 120.5], {
      ...view, viewTransform: { ...view.viewTransform, sourceToWorkspace: [NaN, 0, 0, 0.5, 120.25, 60.5] },
    })).toThrow();
  });

  test('malformed pointer event is refused before it can become a snapshot', () => {
    expect(() => reduceInteraction(createInteractionState(), {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [NaN, 120], buttons: 0, atMs: 100, hit: contentHit,
    })).toThrow();
  });
});

describe('registered interaction dependencies for bounded observation adapters', () => {
  test('current editor focus inside a presentation keeps its registered identity apart from content history', () => {
    let state = reduceInteraction(createInteractionState(), {
      type: 'focus', atMs: 100,
      focus: { presentationId: 'window-a', controlId: 'window-a', role: 'group', editable: false },
    });
    const editor = { presentationId: 'window-b', controlId: 'notes:window-b', role: 'textbox', editable: true,
      value: 'PRIVATE DRAFT', text: 'PRIVATE DRAFT', observedValue: 'PRIVATE DRAFT' };
    state = reduceInteraction(state, { type: 'focus', atMs: 120, focus: editor });
    const snapshot = snapshotInteraction(state, { nowMs: 150, selected: [] });
    expect(snapshot.focus.presentationId).toBe('window-b');
    expect(snapshot.focus.controlId).toBe('notes:window-b');
    expect(snapshot.lastContentFocus?.presentationId).toBe('window-a');
    expect(JSON.stringify(snapshot)).not.toContain('PRIVATE DRAFT');
  });

  test('chrome hit retains the physical presentation and control with no invented source point', () => {
    const state = reduceInteraction(createInteractionState(), {
      type: 'pointer-move', pointerId: 1, modality: 'mouse', primary: true,
      css: [610.125, 50.5], buttons: 1, atMs: 100, capturedBy: 'window-a',
      hit: { presentationId: 'window-b', sourceKey: null,
        hitPath: ['close:window-b', 'window-b'], targetCandidates: [] },
    });
    const snapshot = snapshotInteraction(state, { nowMs: 120, selected: [] });
    expect(snapshot.pointer.presentationId).toBe('window-b');
    expect(snapshot.pointer.hitPath).toEqual(['close:window-b', 'window-b']);
    expect(snapshot.pointer.sourceKey).toBeNull();
    expect(snapshot.pointer.sourcePoint).toBeNull();
    expect(snapshot.pointer.capturedBy).toBe('window-a');
  });

  test('secondary chrome contact keeps its own dependency while primary points elsewhere', () => {
    let state = reduceInteraction(createInteractionState(), {
      type: 'pointer-down', pointerId: 11, modality: 'touch', primary: true,
      css: [200, 120], buttons: 1, atMs: 100, hit: contentHit,
    });
    state = reduceInteraction(state, {
      type: 'pointer-down', pointerId: 12, modality: 'touch', primary: false,
      css: [610.125, 50.5], buttons: 1, atMs: 110,
      hit: { presentationId: 'window-b', sourceKey: null,
        hitPath: ['close:window-b', 'window-b'], targetCandidates: [] },
    });
    const snapshot = snapshotInteraction(state, { nowMs: 130, selected: [] });
    expect(snapshot.pointer.presentationId).toBe('window-a');
    expect(snapshot.contacts[1].presentationId).toBe('window-b');
    expect(snapshot.contacts[1].hitPath).toEqual(['close:window-b', 'window-b']);
    expect(snapshot.contacts[1].sourcePoint).toBeNull();
  });
});
