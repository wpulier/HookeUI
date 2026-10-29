// The voice's state on screen (voice-presence.ts; W3 "alive and available"): each state's words beside Talk, in the
// Assistant header's chip and on the presence pill; the server's state over a voice's own events; Hooke's work in
// flight beside an active state; the pill only while Talk is on and the Assistant is closed, opening it again; and what
// the page says it shows (body data-voice-state, data-voice-working) for the journeys' bench.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { VoiceStateMessage } from './protocol';
import { PRESENCE_PILL, presencePill, presenceView, VoicePresence, type PresenceInput, type TalkState } from './voice-presence';
import type { StripPill } from './working-strip';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';

const state = (s: VoiceStateMessage['state'], n = 0, seq = 1): VoiceStateMessage => ({ type: 'voice-state', state: s, n, seq, at: 1000 + seq,
  working: Array.from({ length: n }, (_, i) => ({ key: `handoff:h${i}`, label: `Work ${i}`, since: 990 })) });
const live = (fields: Partial<PresenceInput> = {}): PresenceInput => ({ talk: 'live', held: false, server: null, working: 0, ...fields });

describe('what each state says', () => {
  it('says the server state beside Talk, in the chip and on the pill', () => {
    const rows = (['heard', 'thinking', 'speaking'] as const).map((s) => presenceView(live({ server: state(s) })));
    expect(rows.map((v) => [v.state, v.status, v.chip, v.pill])).toEqual([
      ['heard', 'Heard you', 'Heard you', 'heard you'],
      ['thinking', 'Thinking', 'Thinking', 'thinking'],
      ['speaking', 'Speaking', 'Speaking', 'speaking'],
    ]);
  });

  it('is Hooke working, with the count of what is in flight, when the voice is otherwise idle', () => {
    const v = presenceView(live({ server: state('working', 1), working: 2 }));
    expect([v.state, v.n, v.status, v.chip, v.pill]).toEqual(['working', 2, 'Hooke is working · 2', 'Working · 2', 'working · 2']);
    expect(v.label).toBe('Hooke: Hooke is working. 2 pieces of work in flight');
    // The server idle while a researcher works here: the one list says so.
    expect(presenceView(live({ server: state('idle'), working: 1 })).chip).toBe('Working · 1');
  });

  it('is live and ready, in the caller\'s own words beside Talk, with nothing in flight', () => {
    const v = presenceView(live({ server: state('idle') }));
    expect([v.state, v.status, v.chip, v.pill, v.n]).toEqual(['idle', '', 'Live', 'live', 0]);
  });

  it('keeps Hooke\'s work beside an active state, so thinking or speaking over work in flight says both', () => {
    const v = presenceView(live({ server: state('speaking', 2), working: 2 }));
    expect([v.state, v.also, v.chip]).toEqual(['speaking', '2 working', 'Speaking']);
  });

  it('shows the user\'s own key held as listening before the server says it, and Talk\'s own off and connecting', () => {
    expect(presenceView(live({ held: true, server: state('thinking') })).state).toBe('listening');
    for (const talk of ['off', 'error'] as TalkState[]) expect(presenceView(live({ talk, server: state('thinking'), working: 3 })).state).toBe('off');
    const connecting = presenceView(live({ talk: 'connecting' }));
    expect([connecting.state, connecting.chip, connecting.status]).toEqual(['connecting', 'Connecting', '']);
  });

  it('reads a voice that sends no state from its own events (processing, a tool, speaking), never a timer', () => {
    expect(presenceView(live({ fallback: 'processing' })).state).toBe('thinking');
    expect(presenceView(live({ fallback: 'tool' })).state).toBe('thinking');
    expect(presenceView(live({ fallback: 'speaking' })).state).toBe('speaking');
    expect(presenceView(live({ fallback: 'listening' })).state).toBe('idle');
    // The server's state wins over those.
    expect(presenceView(live({ fallback: 'processing', server: state('idle') })).state).toBe('idle');
  });

  it('never says "Working · 0": the server\'s count holds until the list catches up', () => {
    expect(presenceView(live({ server: state('working', 1), working: 0 })).chip).toBe('Working · 1');
  });
});

describe('the presence pill', () => {
  it('is "Hooke" and its state, first in the strip, with no × and a click that opens the Assistant', () => {
    const open = vi.fn();
    const pill = presencePill(presenceView(live({ server: state('thinking') })), open, 5)!;
    expect([pill.id, pill.name, pill.words, pill.voice, pill.lead, pill.fixed]).toEqual([PRESENCE_PILL, 'Hooke', 'thinking', 'thinking', true, true]);
    pill.open();
    expect(open).toHaveBeenCalledOnce();
    expect(presencePill(presenceView(live({ talk: 'off' })), open, 5)).toBeNull();
  });
});

describe('VoicePresence', () => {
  let shown = true, talk: TalkState = 'live', held = false, working = 0;
  let sunk: StripPill[][] = [];
  let header: TestElement, button: TestElement;
  const open = vi.fn();
  beforeEach(() => {
    shown = true; talk = 'live'; held = false; working = 0; sunk = []; open.mockReset();
    const dom = syntheticDOM();
    vi.stubGlobal('document', Object.assign(dom.document, {
      createElementNS: (_ns: string, tag: string) => new TestElement(tag),
      body: Object.assign(dom.host, { dataset: {} as Record<string, string> }),
    }));
    header = new TestElement('header');
    header.append(Object.assign(new TestElement('h2'), { textContent: 'Assistant' }));
    button = new TestElement('button');
  });
  afterEach(() => vi.unstubAllGlobals());
  const make = () => new VoicePresence({
    talk: () => talk, held: () => held, working: () => working, assistantShown: () => shown, openAssistant: open,
    sink: (pills) => sunk.push(pills), header: () => header as unknown as HTMLElement, talkButton: button as unknown as HTMLElement,
  });

  it('puts the chip after the header\'s title, says the state there and on the page, and marks Talk while Hooke works', () => {
    const p = make();
    p.serverState(state('thinking'));
    const chip = header.children[1];
    expect(chip.className).toBe('voice-chip');
    expect(chip.hidden).toBe(false);
    expect(chip.textContent).toBe('Thinking');
    expect(document.body.dataset.voiceState).toBe('thinking');
    expect(document.body.dataset.voiceWorking).toBe('0');
    expect('working' in button.dataset).toBe(false); // thinking is the mark's; Talk's gold dot is Hooke working
    working = 2;
    p.serverState(state('working', 2, 2));
    expect(chip.textContent).toBe('Working · 2');
    expect('working' in button.dataset).toBe(true);
    expect(document.body.dataset.voiceWorking).toBe('2');
    p.serverState(state('speaking', 2, 3));
    expect(chip.textContent).toBe('Speaking · 2 working');
    expect('working' in button.dataset).toBe(false);
    working = 0;
    p.serverState(state('idle', 0, 4));
    expect(chip.textContent).toBe('Live');
    talk = 'off';
    p.reset();
    expect(chip.hidden).toBe(true);
    expect(document.body.dataset.voiceState).toBe('off');
  });

  it('shows the presence pill only while Talk is on and the Assistant is closed, and drops an older state', () => {
    const p = make();
    p.serverState(state('thinking', 0, 5));
    expect(sunk.at(-1) ?? []).toEqual([]);
    shown = false;
    p.render();
    expect(sunk.at(-1)!.map((pill) => [pill.id, pill.words])).toEqual([[PRESENCE_PILL, 'thinking']]);
    p.serverState(state('idle', 0, 4)); // a late send of an older state changes nothing
    expect(sunk.at(-1)![0].words).toBe('thinking');
    sunk.at(-1)![0].open();
    expect(open).toHaveBeenCalledOnce();
    shown = true;
    p.render();
    expect(sunk.at(-1)).toEqual([]);
    shown = false;
    talk = 'off';
    p.reset();
    expect(sunk.at(-1)).toEqual([]);
  });

  it('a voice\'s own events stand in only until the server says a state, and a new Talk session starts afresh', () => {
    const p = make();
    p.setFallback('processing');
    expect(p.view.state).toBe('thinking');
    p.serverState(state('idle', 0, 1));
    expect(p.view.state).toBe('idle');
    expect(p.told).toBe(true);
    p.reset();
    expect(p.told).toBe(false);
    p.serverState(state('heard', 0, 1)); // a new session's first state (its seq starts again)
    expect(p.view.state).toBe('heard');
  });

  it('shows the key held at once, whatever the server last said', () => {
    const p = make();
    p.serverState(state('speaking'));
    held = true;
    expect(p.render().state).toBe('listening');
  });
});
