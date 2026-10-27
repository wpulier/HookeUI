// The Capabilities page's layout and filtering (capabilities.ts; ADR 0007), without a page.
import { describe, expect, it } from 'vitest';
import { capabilityChecks, capabilityEvidence, checkingWords, checkTime, checkWords, chosenIn, count, emptyFilter, fallbackPowers, familyPowers, howText, layout, matches, modelOptions, powers, prefValue, roleMark, roleAvailability, visibleModelRoles, stale } from './capabilities';
import type { CapabilitiesWire, Capability } from './protocol';

function cap(id: string, actor: Capability['actor'], extra: Partial<Capability> = {}): Capability {
  return { id, actor, family: 'outlining', title: id, what: `does ${id}`, how: {}, available: true, why: '', verdict: null, ...extra };
}

const WIRE: CapabilitiesWire = {
  type: 'capabilities',
  families: [{ id: 'outlining', title: 'Outlining' }, { id: 'motion', title: 'Motion' }],
  actors: { you: 'You', agent: 'Agent', auto: 'On its own', researcher: 'Researcher' },
  capabilities: [
    cap('click-outline', 'you', { pair: 'outline-a-thing', how: { gesture: 'click' } }),
    cap('highlight', 'agent', { pair: 'outline-a-thing', how: { say: 'highlight the stomata' }, roles: ['outline'],
      verdict: { id: 'highlight', state: 'works', note: '', at: '2026-10-06T14:00:00', build: 'aaa1111', by: 'you' } }),
    cap('clear', 'agent'),
    cap('cells-button', 'you', { available: false, why: 'off in the settings (Cells)' }),
    cap('outline-check', 'auto'),
    cap('track-motion', 'agent', { family: 'motion', title: 'What moves, how fast',
      verdict: { id: 'track-motion', state: 'broken', note: 'lost fast ones', at: '2026-10-06T14:08:00', build: 'bbb2222', by: 'you' } }),
  ],
  roles: [{ id: 'outline', kind: 'model', title: 'Outlining', purpose: 'Draws an outline', current: 'edgetam', options: [], powers: ['highlight'], agentCanChange: [] },
    { id: 'style', kind: 'policy', title: 'Outlines', purpose: 'How they are drawn', current: 'classic', options: [], powers: [], agentCanChange: ['set_style'] }],
  preferences: [],
  pending: 0,
  build: 'bbb2222',
  agentText: '',
};

describe('the atlas', () => {
  it('puts one ability done by different actors on one line, and stacks the rest in their columns', () => {
    const [outlining] = layout(WIRE, emptyFilter());
    expect(outlining.lines[0].map((c) => c?.id ?? null)).toEqual(['click-outline', 'highlight', null, null]);
    expect(outlining.lines.slice(1).map((l) => l.map((c) => c?.id ?? null))).toEqual([
      [null, 'clear', null, null], ['cells-button', null, null, null], [null, null, 'outline-check', null]]); // one ability a line
    expect(outlining.counts).toMatchObject({ total: 5, works: 1, broken: 0, notYet: 4, unavailable: 1 });
  });

  it('filters by who, verdict, availability and words, and leaves out empty families', () => {
    const f = emptyFilter();
    f.actors = new Set(['agent']);
    expect(layout(WIRE, f).flatMap((r) => r.lines.flat()).filter(Boolean).map((c) => c!.id).sort()).toEqual(['clear', 'highlight', 'track-motion']);
    const broken = { ...emptyFilter(), status: 'broken' as const };
    expect(layout(WIRE, broken).map((r) => r.id)).toEqual(['motion']);
    const missing = { ...emptyFilter(), status: 'unavailable' as const };
    expect(layout(WIRE, missing).flatMap((r) => r.lines.flat()).filter(Boolean).map((c) => c!.id)).toEqual(['cells-button']);
    const roles = new Map(WIRE.roles.map((r) => [r.id, r]));
    expect(matches(WIRE.capabilities[1], { ...emptyFilter(), text: 'stomata' }, roles)).toBe(true); // what to say
    expect(matches(WIRE.capabilities[1], { ...emptyFilter(), text: 'outlining' }, roles)).toBe(true); // its role
    expect(layout(WIRE, { ...emptyFilter(), text: 'two magnifier windows' })).toEqual([]);
  });

  it('says how to reach a thing, and when a verdict was given on an older build', () => {
    expect(howText(WIRE.capabilities[1])).toBe('Say “highlight the stomata”');
    expect(howText(WIRE.capabilities[0])).toBe('Click');
    expect(howText(WIRE.capabilities[4])).toBe('Runs by itself');
    expect(stale(WIRE.capabilities[1], WIRE.build)).toBe(true);
    expect(stale(WIRE.capabilities[5], WIRE.build)).toBe(false);
    expect(count([]).total).toBe(0);
  });
});

describe('preferences', () => {
  it('says on, off, the value, or not set', () => {
    expect([true, false, 'pink', 0, null, undefined, ''].map(prefValue)).toEqual(['On', 'Off', 'pink', '0', 'Not set', 'Not set', 'Not set']);
  });
});

describe('what powers a capability', () => {
  it('lists who decides (for the agent), its model roles with their choice, and the code it runs on', () => {
    const wire: CapabilitiesWire = {
      ...WIRE,
      roles: [
        { id: 'fast', kind: 'model', title: 'Quick decisions', purpose: '', current: 'g35', options: [{ id: 'g35', label: 'Gemini 3.5 Flash', note: '', available: true }], powers: [], agentCanChange: [] },
        { id: 'vision', kind: 'model', title: 'Seeing', purpose: '', current: 'g35', options: [{ id: 'g35', label: 'Gemini 3.5 Flash', note: '', available: true }], powers: [], agentCanChange: [] },
        { id: 'outline', kind: 'model', title: 'Outlining', purpose: '', current: 'edgetam', options: [{ id: 'edgetam', label: 'EdgeTAM', note: '', available: true }], powers: [], agentCanChange: [] },
      ],
      parts: [{ id: 'outlines', title: 'Outline store', purpose: 'Keeps every outline pinned', source: 'services/orchestrator/scene.py', powers: ['highlight'] }],
    };
    const highlight = { ...WIRE.capabilities[1], parts: ['outlines'] };
    expect(powers(wire, highlight).map((p) => [p.kind, p.title, p.detail])).toEqual([
      ['asked', 'Quick decisions', 'Gemini 3.5 Flash'],
      ['asked', 'Seeing', 'Gemini 3.5 Flash'],
      ['model', 'Outlining', 'EdgeTAM'],
      ['code', 'Outline store', 'Keeps every outline pinned'],
    ]);
    const camera = { ...WIRE.capabilities[0], actor: 'you' as const, roles: [], parts: ['outlines'] };
    expect(powers(wire, camera).map((p) => p.kind)).toEqual(['code']); // you do it: nobody decides for you
  });
});

describe('the key', () => {
  it('marks a model by where its choice runs, a method as an approach, and code by what it is', () => {
    const model = (current: string) => ({ id: 'outline', kind: 'model' as const, title: 'Outlining', purpose: '', current, powers: [], agentCanChange: [],
      options: [{ id: 'edgetam', label: 'EdgeTAM', note: '', available: true, runs: 'mac' as const },
        { id: 'sam-fal', label: 'SAM 3.1 (fal)', note: '', available: true, runs: 'both' as const },
        { id: 'off', label: 'Off', note: '', available: true, runs: '' as const }] });
    expect(roleMark(model('edgetam'))).toBe('mac');
    expect(roleMark(model('sam-fal'))).toBe('cloud');
    expect(roleMark(model('off'))).toBe('mac'); // switched off: still a model, where its options run
    expect(roleMark({ ...model('edgetam'), kind: 'method' })).toBe('way');
    const wire: CapabilitiesWire = { ...WIRE, roles: [model('edgetam')],
      parts: [{ id: 'tracker', title: 'Slide tracker', kind: 'algorithm', purpose: '', source: '', powers: [] },
        { id: 'outlines', title: 'Outline store', kind: 'app', purpose: '', source: '', powers: [] }] };
    const c = cap('x', 'you', { roles: ['outline'], parts: ['tracker', 'outlines'] });
    expect(powers(wire, c).map((p) => p.mark)).toEqual(['mac', 'algorithm', 'app']);
  });
});

describe('a family\'s powers', () => {
  it('says once what every line uses, and gives each line only what it adds', () => {
    const wire: CapabilitiesWire = { ...WIRE, roles: [],
      parts: [{ id: 'magnifier', title: 'Magnifier', kind: 'app', purpose: '', source: '', powers: [] },
        { id: 'quality', title: 'Frame measures', kind: 'algorithm', purpose: '', source: '', powers: [] }] };
    const a = cap('zoom', 'you', { parts: ['magnifier'] });
    const b = cap('focus', 'agent', { parts: ['magnifier', 'quality'] });
    const fp = familyPowers(wire, [[a, null, null, null], [null, b, null, null]]);
    expect(fp.shared.map((p) => p.title)).toEqual(['Magnifier']);
    expect(fp.lines.map((ps) => ps.map((p) => p.title))).toEqual([[], ['Frame measures']]);
    // one line: nothing is shared, the line keeps all of it
    expect(familyPowers(wire, [[a, null, null, null]]).shared).toEqual([]);
  });
});

describe('every model a role can use', () => {
  it('lists each, where it runs and which is in use; "off" is not a model, and methods list none', () => {
    const outline = { id: 'outline', kind: 'model' as const, title: 'Outlining', purpose: '', current: 'edgetam', powers: [], agentCanChange: [],
      options: [{ id: 'edgetam', label: 'EdgeTAM', note: '', available: true, runs: 'mac' as const },
        { id: 'sam-fal', label: 'SAM 3.1 (fal)', note: '', available: false, runs: 'both' as const },
        { id: 'off', label: 'Off', note: '', available: true, runs: '' as const }] };
    expect(modelOptions(outline)).toEqual([
      { id: 'edgetam', label: 'EdgeTAM', mark: 'mac', active: true, available: true },
      { id: 'sam-fal', label: 'SAM 3.1 (fal)', mark: 'cloud', active: false, available: false },
    ]);
    expect(modelOptions({ ...outline, current: 'off' }).some((o) => o.active)).toBe(false); // switched off: none in use
    expect(modelOptions({ ...outline, kind: 'method' })).toEqual([]);
  });
});

describe('models fixed in the code, and fallbacks', () => {
  it('lists a fixed model and its fallback, the one in use marked, and where each power is chosen', () => {
    const writer = { id: 'fixed:lesson-writer', kind: 'model' as const, title: 'Lesson writer', purpose: '', fixed: true, current: 'gpt-6.1-sol',
      label: 'GPT-6.1 Sol', runs: 'cloud' as const, options: [], powers: [], agentCanChange: [],
      primary: { label: 'Claude Sonnet 5.5', value: 'claude-sonnet-5-5', runs: 'cloud' as const, available: false },
      fallback: { label: 'GPT-6.1 Sol', value: 'gpt-6.1-sol', runs: 'cloud' as const, available: true, when: 'no ANTHROPIC_API_KEY', active: true } };
    expect(modelOptions(writer).map((o) => [o.label, o.active])).toEqual([['Claude Sonnet 5.5', false], ['GPT-6.1 Sol', true]]);
    const vision = { id: 'vision', kind: 'model' as const, title: 'Seeing', purpose: '', current: 'g', powers: [], agentCanChange: [],
      options: [{ id: 'g', label: 'Gemini 3.5 Flash', note: '', available: true, runs: 'cloud' as const }] };
    const wire: CapabilitiesWire = { ...WIRE, roles: [writer, vision] };
    const like = cap('like', 'agent', { roles: ['fixed:lesson-writer'], fallbacks: [{ when: 'SAM 3.1 can’t run', does: 'the vision model boxes them', roles: ['vision'] }] });
    const [fb] = fallbackPowers(wire, like);
    expect([fb.when, fb.powers.map((p) => [p.title, p.detail])]).toEqual(['SAM 3.1 can’t run', [['Seeing', 'Gemini 3.5 Flash']]]);
    const [own] = powers(wire, cap('l', 'researcher', { roles: ['fixed:lesson-writer'] }));
    expect([own.detail, chosenIn(own), chosenIn(fb.powers[0])]).toEqual(['GPT-6.1 Sol', 'fixed in the code', 'set in Models']);
  });
});


describe('declared capability citation and evaluation sources', () => {
  it('uses only this capability’s currently selected option, never a default or unused model', () => {
    const current = {id:'current',label:'Current model',available:true,note:'Measured note',sources:[
      {title:'Paper',url:'https://example.org/current',licence:'MIT'},
      {title:'Unsafe link',url:'javascript:alert(1)',licence:''}]};
    const wire = {...WIRE,roles:[{...WIRE.roles[0],current:'current',default:'default',options:[current,
      {id:'default',label:'Unused default',available:true,note:'Different benchmark',sources:[{title:'Other paper',url:'https://example.org/unused',licence:'Other'}]}]},
      {...WIRE.roles[1],current:'elsewhere',options:[{id:'elsewhere',label:'Unrelated',available:true,note:'',sources:[{title:'Unrelated paper',url:'https://example.org/unrelated',licence:'Other'}]}]}]};
    expect(capabilityEvidence(wire,{...WIRE.capabilities[1],roles:['outline','outline'],evidence:['sim:clicks']})).toEqual({
      roles:[{id:'outline',title:'Outlining',choice:'Current model',note:'Measured note',sources:[{title:'Paper',url:'https://example.org/current',licence:'MIT'}]}],checks:['sim:clicks']});
  });
  it('preserves unknown selection and missing sources instead of substituting another role option', () => {
    const wire = {...WIRE,roles:[{...WIRE.roles[0],current:'missing',options:[{id:'available',label:'Another',available:true,note:'Not applicable'}]}]};
    expect(capabilityEvidence(wire,{...WIRE.capabilities[1],roles:['outline','unknown']})).toEqual({roles:[
      {id:'outline',title:'Outlining',choice:'missing',note:null,sources:[]},
      {id:'unknown',title:'unknown',choice:null,note:null,sources:[]}],checks:[]});
    expect(capabilityEvidence(wire,{...WIRE.capabilities[1],roles:[]})).toEqual({roles:[],checks:[]});
  });
});


describe('unserved role visibility and truthful availability', () => {
  const role = WIRE.roles[0];
  const spare = { ...role, id: 'spare', title: 'Future microscope model', powers: [], fallbackFor: [] };
  const wire = { ...WIRE, roles: [...WIRE.roles, spare] };
  it('keeps unserved roles discoverable without leaking them into scoped actor/verdict results', () => {
    expect(visibleModelRoles(wire, emptyFilter())).toContain(spare);
    expect(visibleModelRoles(wire, { ...emptyFilter(), text: 'future microscope' })).toEqual([spare]);
    expect(visibleModelRoles(wire, { ...emptyFilter(), text: 'does-not-match' })).toEqual([]);
    expect(visibleModelRoles(wire, { ...emptyFilter(), actors: new Set(['agent']) })).not.toContain(spare);
    expect(visibleModelRoles(wire, { ...emptyFilter(), status: 'broken' })).not.toContain(spare);
  });
  it('retains roles serving a capability only through fallback and treats missing references as unserved', () => {
    const fallback = { ...spare, id: 'fallback', fallbackFor: ['highlight'] };
    const missing = { ...spare, id: 'removed', powers: ['removed-capability'] };
    expect(visibleModelRoles({ ...wire, roles: [fallback, missing] }, { ...emptyFilter(), text: 'highlight' })).toEqual([fallback]);
    expect(visibleModelRoles({ ...wire, roles: [missing] }, emptyFilter())).toEqual([missing]);
  });
  it('excludes Off and unavailable choices from availability, and never labels behaviour/fixed roles as model choices', () => {
    const option = { id: 'one', label: 'One', available: true, note: '' };
    const base = { ...spare, kind: 'model' as const, options: [option, {...option,id:'off'}, {...option,id:'unavailable',available:false}] };
    expect(roleAvailability(base)).toBe('Only one model can run here.');
    expect(roleAvailability({...base, options:base.options.slice(1)})).toBe('No model can run here.');
    expect(roleAvailability({...base, options:[option,{...option,id:'two'}]})).toBeNull();
    expect(roleAvailability({...base, kind:'policy'})).toBeNull();
    expect(roleAvailability({...base, fixed:true})).toBeNull();
  });
});

describe('the app’s own check of each model (model_checks.py), beside the verdicts', () => {
  const now = new Date('2026-10-11T03:00:00');
  const at = '2026-10-11T02:41:05+00:00';
  it('says works, broken with why, and what could not be checked, with its time', () => {
    const local = checkTime(at, now);
    expect(checkWords({ state: 'works', why: '', at, loadMs: 1200 }, now)).toBe(`Last check: works (loaded in 1.2 s) · ${local}`);
    expect(checkWords({ state: 'broken', why: "micro-sam's own Python exited", at }, now)).toBe(`Broken: micro-sam's own Python exited · ${local}`);
    expect(checkWords({ state: 'needs-key', why: 'needs FAL_KEY', at }, now)).toBe(`Last check: needs a key (needs FAL_KEY) · ${local}`);
    expect(checkWords({ state: 'broken', why: 'out of memory', at, earlierBuild: 'abc1234' }, now)).toContain(', an earlier build');
    expect(checkWords(undefined, now)).toBe('');
  });
  it('puts the checks of the models a capability runs on now on its card, the verdict untouched', () => {
    const wire: CapabilitiesWire = { ...WIRE, roles: [{ ...WIRE.roles[0], options: [
      { id: 'edgetam', label: 'EdgeTAM', said: 'EdgeTAM (on this Mac)', note: '', available: true, check: { state: 'broken', why: 'out of memory', at } },
      { id: 'sam2.1-tiny', label: 'SAM 2.1 tiny', note: '', available: true, check: { state: 'works', why: '', at } }] }, WIRE.roles[1]] };
    const highlight = wire.capabilities.find(c => c.id === 'highlight')!;
    expect(capabilityChecks(wire, highlight)).toEqual([{ role: 'Outlining', model: 'EdgeTAM (on this Mac)', check: { state: 'broken', why: 'out of memory', at } }]);
    expect(highlight.verdict?.state).toBe('works');
    expect(checkingWords({ ...wire, checking: { by: 'Check now', done: 2, of: 21, now: 'micro-sam (light microscopy) on this Mac' } }))
      .toBe('Checking 3 of 21: micro-sam (light microscopy) on this Mac…');
    expect(checkingWords(wire)).toBe('');
  });
});

describe('discovery next action', () => {
  it('uses the exact catalog tool for setup and configured Try', async () => {
    const { discoveryAction } = await import('./capabilities');
    expect(discoveryAction(cap('a', 'agent', { catalog: 'mine/tool', available: false }), WIRE))
      .toEqual({ label: 'View details', model: 'mine/tool' });
    expect(discoveryAction(cap('a', 'agent', { catalog: 'mine/tool' }), WIRE))
      .toEqual({ label: 'Try', model: 'mine/tool' });
    expect(discoveryAction(cap('a', 'agent', { how: { tool: 'outline' } }), WIRE))
      .toEqual({ label: 'Configure and run', capability: 'a' });
  });
});

it('does not promise Actions for tools outside the Actions families', async () => {
  const { discoveryAction } = await import('./capabilities');
  expect(discoveryAction(cap('start-researcher', 'agent', { family: 'research', how: { tool: 'start_researcher' } }), WIRE)).toBeNull();
});


it('discovery points at the blocked current model when another dependency is ready', async () => {
  const { discoveryAction } = await import('./capabilities');
  const models = [
    { id: 'outline', status: 'ready', usedFor: [{ role: 'outline', current: true }], next: null },
    { id: 'vision', status: 'needs-setup', usedFor: [{ role: 'vision', current: true }], next: { kind: 'key' } },
  ] as CapabilitiesWire['models'];
  expect(discoveryAction(cap('find', 'agent', { available: false, roles: ['outline', 'vision'] }), { ...WIRE, models }))
    .toEqual({ label: 'Turn on', model: 'vision' });
});
