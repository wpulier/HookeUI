// Synthetic interaction evidence; actual preparation and review are separate gates.
import { expect, test, vi } from 'vitest';
import { mounted, tool } from './toolbox-page.test';
import { fire, settleMicrotasks } from './workspace-windows.test-support';

test('candidate preparation keeps unavailable status, exposes Stop and allows retry', async () => {
  const { root, page, commands, wire } = mounted([tool('candidate', {
    status: 'unavailable', word: 'Needs integration', group: 'unavailable',
    facts: [{ kind: 'code', ok: null, says: 'Dependencies not prepared', prepare: 'candidate' }],
  })]);
  commands.prepare = vi.fn(async () => undefined);
  commands.install = vi.fn(async () => undefined);
  commands.installCancel = vi.fn(async () => undefined);
  page.update(wire); page.show('candidate');
  fire(root.querySelectorAll('button').find(button => button.textContent === 'Prepare for review')!, 'click');
  await settleMicrotasks();
  expect(commands.prepare).toHaveBeenCalledWith('candidate');
  expect(commands.install).not.toHaveBeenCalled();
  wire.setup = { edition: 'research', options: {}, installs: [{ model: 'candidate', name: 'candidate',
    state: 'installing', says: 'Preparing dependencies', prepareOnly: true }] };
  page.update(wire);
  expect(root.textContent).toContain('Preparing dependencies');
  fire(root.querySelectorAll('button').find(button => button.textContent === 'Stop')!, 'click');
  expect(commands.installCancel).toHaveBeenCalledWith('candidate');
  wire.setup.installs![0] = { ...wire.setup.installs![0], state: 'cancelled', says: 'Stopped' };
  page.update(wire);
  expect(root.querySelectorAll('button').some(button => button.textContent === 'Prepare for review')).toBe(true);
});


test('guided candidate setup retains permission, preparation and recovery without an executable plan', async () => {
  const values = new Map([['capability-flow', JSON.stringify({route: 'add', model: 'candidate', candidate: 'candidate', role: '', option: '', session: ''})]]);
  vi.stubGlobal('sessionStorage', {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)});
  const candidate = tool('candidate', {
    status: 'unavailable', word: 'Needs integration', group: 'unavailable',
    setups: [{ role: 'model:candidate', title: 'Candidate', option: 'candidate', plan: null, canTry: false, recheck: false }],
    facts: [{ kind: 'code', ok: false, says: 'Review this package before running its code', trust: 'candidate-source' }],
  });
  const { root, page, commands, wire } = mounted([candidate]);
  commands.channelTrust = vi.fn(async () => undefined);
  commands.prepare = vi.fn(async () => undefined);
  commands.installCancel = vi.fn(async () => undefined);
  page.update(wire);
  page.show('add-add');
  await settleMicrotasks();
  let sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).toContain('Needs integration');
  fire(sheet.querySelectorAll('button').find(b => b.textContent === 'Allow code from candidate-source')!, 'click');
  expect(commands.channelTrust).toHaveBeenCalledWith('candidate-source', true);
  candidate.facts = [{kind: 'code', ok: null, says: 'Dependencies not prepared; workflow unqualified', prepare: 'candidate'}];
  page.update(wire);
  sheet = root.querySelector('.tool-setup-sheet')!;
  fire(sheet.querySelectorAll('button').find(b => b.textContent === 'Prepare for review')!, 'click');
  await settleMicrotasks();
  expect(commands.prepare).toHaveBeenCalledWith('candidate');
  wire.setup = {edition: 'research', options: {}, installs: [{model: 'candidate', name: 'candidate', state: 'installing', says: 'Preparing dependencies', prepareOnly: true}]};
  page.update(wire);
  sheet = root.querySelector('.tool-setup-sheet')!;
  fire(sheet.querySelectorAll('button').find(b => b.textContent === 'Stop')!, 'click');
  expect(commands.installCancel).toHaveBeenCalledWith('candidate');
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
  expect(sheet.textContent).toContain('workflow unqualified');
});


test('package details put preparation before capabilities and retain failed diagnostics behind a disclosure', () => {
  const diagnostic = 'uv failed: DNS lookup failed for the declared dependency';
  const { root, page, commands, wire } = mounted([tool('candidate', {
    status: 'unavailable', word: 'Needs integration', group: 'unavailable',
    facts: [{ kind: 'code', ok: false, says: diagnostic, prepare: 'candidate' }],
  })]);
  commands.prepare = vi.fn(async () => undefined);
  wire.setup = { edition: 'research', options: {}, installs: [{model: 'candidate', name: 'candidate',
    state: 'failed', says: diagnostic, prepareOnly: true}] };
  page.update(wire); page.show('candidate');
  const details = root.querySelector('.tool-details')!;
  const preparation = details.querySelector('.tool-preparation')!;
  expect(preparation).not.toBeNull();
  // Its next step stays in view, ahead of everything folded under "More about this model".
  expect(details.textContent.indexOf('Prepare for review')).toBeLessThan(details.textContent.indexOf('More about this model'));
  expect(preparation.closest('.tool-details-more')).toBeNull();
  expect(preparation.textContent).toContain('Preparation didn’t finish');
  expect(preparation.textContent).toContain('does not enable');
  const diagnosticDetails = preparation.querySelector('details')!;
  expect(diagnosticDetails.textContent).toContain(diagnostic);
  expect(diagnosticDetails.getAttribute('open')).toBeNull();
  expect(commands.prepare).not.toHaveBeenCalled();
  diagnosticDetails.setAttribute('open', '');
  const diagnosticText = Object.assign(diagnosticDetails.querySelector('pre')!, {scrollTop: 24});
  page.update(wire);
  const refreshed = root.querySelector('.tool-preparation')!.querySelector('details')!;
  expect(refreshed).toBe(diagnosticDetails);
  expect(refreshed.getAttribute('open')).toBe('');
  expect(refreshed.querySelector('pre')).toBe(diagnosticText);
  expect(diagnosticText.scrollTop).toBe(24);
  wire.setup!.installs![0].says = 'Different failure'; page.update(wire);
  expect(root.querySelector('.tool-preparation')!.querySelector('details')).not.toBe(diagnosticDetails);
});

test('guided candidate setup downloads its declared file owner and recovers without activating', async () => {
  const values = new Map([['capability-flow', JSON.stringify({route: 'add', model: 'candidate', candidate: 'candidate', role: '', option: '', session: ''})]]);
  vi.stubGlobal('sessionStorage', {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)});
  const candidate = tool('candidate', {
    status: 'unavailable', word: "Can't be turned on here", group: 'unavailable',
    setups: [{role: 'model:candidate', title: 'Candidate', option: 'candidate', plan: null, canTry: false, recheck: false}],
    facts: [{kind: 'files', ok: false, model: 'shared-weights', ref: '/private/weights', says: 'Not downloaded yet: 1.2 GB'},
      {kind: 'code', ok: null, says: 'Dependencies prepared; workflow unqualified', prepare: 'candidate'}],
  });
  const {root, page, commands, wire} = mounted([candidate]);
  commands.download = vi.fn(async () => undefined);
  page.update(wire); page.show('add-add'); await settleMicrotasks();
  let sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).toContain('Prepare this tool for review');
  expect(sheet.textContent).not.toContain("Can't be turned on here");
  candidate.facts.push({kind: 'licence', ok: null, says: 'Confirm access on the publisher’s page', confirm: 'access'});
  page.update(wire); sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('.cap-download-start')).toBeNull();
  candidate.facts[2].ok = true;
  page.update(wire); sheet = root.querySelector('.tool-setup-sheet')!;
  fire(sheet.querySelectorAll('button').find(b => b.textContent === 'Download files')!, 'click');
  expect(commands.download).toHaveBeenCalledWith('shared-weights');
  wire.setup = {edition: 'research', options: {}, downloads: [{model: 'shared-weights', name: 'Weights', state: 'downloading', got: 5, total: 10, says: 'Downloading'}]};
  page.update(wire); sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).toContain('Downloading 50%');
  fire(sheet.querySelectorAll('button').find(b => b.textContent === 'Cancel')!, 'click');
  expect(commands.download).toHaveBeenCalledWith('shared-weights', true);
  wire.setup.downloads![0] = {...wire.setup.downloads![0], state: 'failed', says: 'Connection lost; partial download retained'};
  page.update(wire); sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).toContain('Connection lost; partial download retained');
  fire(sheet.querySelectorAll('button').find(b => b.textContent === 'Try again')!, 'click');
  expect(commands.download).toHaveBeenLastCalledWith('shared-weights');
  candidate.facts[0] = {kind: 'files', ok: true, ref: '/private/weights', says: 'Downloaded: 1.2 GB'};
  wire.setup.downloads![0].state = 'done';
  page.update(wire); sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('.cap-download-start')).toBeNull();
  expect(sheet.textContent).toContain('workflow unqualified');
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test('retry presents active preparation and keeps the previous failure as history', () => {
  const diagnostic = 'Its install failed: dependency connection lost';
  const {root, page, commands, wire} = mounted([tool('candidate', {
    status: 'unavailable', word: 'Needs setup', group: 'unavailable',
    facts: [{kind: 'code', ok: false, says: diagnostic, prepare: 'candidate'}],
  })]);
  commands.prepare = vi.fn(async () => undefined);
  commands.installCancel = vi.fn(async () => undefined);
  wire.setup = {edition: 'research', options: {}, installs: [{model: 'candidate', name: 'Candidate',
    state: 'installing', says: 'Checking prepared dependencies', prepareOnly: true}]};
  page.update(wire); page.show('candidate');
  const preparation = root.querySelector('.tool-preparation')!;
  expect(preparation.querySelector('.model-fact-says')!.textContent).toContain('Preparation is in progress');
  expect(preparation.querySelector('.model-fact-says')!.textContent).not.toContain(diagnostic);
  const history = preparation.querySelector('.model-preparation-diagnostics')!;
  expect(history.textContent).toContain('Before this attempt');
  expect(history.textContent).toContain(diagnostic);
  expect(history.getAttribute('open')).toBeNull();
  expect(preparation.textContent).toContain('Checking prepared dependencies');
  fire(preparation.querySelectorAll('button').find(b => b.textContent === 'Stop')!, 'click');
  expect(commands.installCancel).toHaveBeenCalledWith('candidate');
});


test('current failed attempt is visible before an older environment failure',()=>{
  const old='Earlier DNS lookup failed';
  const current='Saved Project association needs repair; no software was removed';
  const {root,page,commands,wire}=mounted([tool('candidate',{status:'unavailable',word:'Needs setup',group:'unavailable',facts:[{kind:'code',ok:false,says:old,prepare:'candidate'}]})]);
  commands.prepare=vi.fn(async()=>undefined);
  wire.setup={edition:'research',options:{},installs:[{model:'candidate',name:'Candidate',state:'failed',says:current,prepareOnly:true}]};
  page.update(wire);page.show('candidate');
  const preparation=root.querySelector('.tool-preparation')!;
  expect(preparation.querySelector('.model-fact-says')!.textContent).toContain(current);
  const diagnostic=preparation.querySelector('pre')!.textContent;
  expect(diagnostic.indexOf(current)).toBeLessThan(diagnostic.indexOf(old));
});

test('cached scientific files offer explicit publication without claiming prepared or enabling code', async () => {
  const values = new Map([['capability-flow', JSON.stringify({route: 'add', model: 'candidate', candidate: 'candidate', role: '', option: '', session: ''})]]);
  vi.stubGlobal('sessionStorage', {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)});
  const candidate = tool('candidate', {
    status: 'unavailable', word: 'Needs integration', group: 'unavailable',
    setups: [{role: 'model:candidate', title: 'Candidate', option: 'candidate', plan: null, canTry: false, recheck: false}],
    facts: [{kind: 'files', ok: false, cached: true, model: 'candidate',
      says: 'Found in an older cache. Prepare cached files to verify and publish its pinned revision.'},
    {kind: 'code', ok: null, says: 'Dependencies prepared; workflow qualification remains open'}],
  });
  const {root, page, commands, wire} = mounted([candidate]);
  commands.download = vi.fn(async () => undefined);
  page.update(wire); page.show('add-add'); await settleMicrotasks();
  expect(root.querySelector('.tool-setup-success')).toBeNull();
  const action = root.querySelectorAll('button').find(b => b.textContent === 'Prepare cached files');
  expect(action).toBeDefined();
  expect(root.querySelector('[data-kind="files"]')!.getAttribute('data-ok')).toBe('false');
  fire(action!, 'click'); await settleMicrotasks();
  expect(commands.download).toHaveBeenCalledWith('candidate');
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
  expect(root.textContent).toContain('workflow qualification remains open');
  candidate.facts.push({kind: 'licence', ok: null, says: 'Confirm access', confirm: 'access'});
  page.update(wire);
  expect(root.querySelectorAll('button').some(b => b.textContent === 'Prepare cached files')).toBe(false);
});
