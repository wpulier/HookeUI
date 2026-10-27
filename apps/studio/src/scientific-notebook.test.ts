// Synthetic UI/transport regressions for the notebook connection. A real Jupyter client is a separate opt-in check.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ScientificConsole } from './scientific-console';
import { notebookGrant } from './scientific-notebook';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';

let root: TestElement;
beforeEach(() => { const dom = syntheticDOM(); root = dom.host; vi.stubGlobal('document', dom.document); });
afterEach(() => vi.unstubAllGlobals());
const button = (prefix: string) => root.querySelectorAll('button').find(row => row.textContent.startsWith(prefix))!;
const grant = { name: 'hooke-0123456789ab', display: 'Hooke · p0123456789abcdef', kernel: '/home/jupyter/hooke-0123456789ab', expires: 2_000_000_000,
  install: 'jupyter kernelspec install --user "/home/jupyter/hooke-0123456789ab"',
  meaning: 'Cells from this notebook run in this Hooke session.' };
function mount(connect = vi.fn(async (): Promise<unknown> => grant), revoke = vi.fn(async () => {})) {
  const console = new ScientificConsole(root as unknown as HTMLElement, { execute: vi.fn(), stop: vi.fn(async () => {}),
    notebook: { connect, revoke } });
  console.bindSession('session-one');
  return { console, connect, revoke };
}
const text = () => root.textContent;

test('connecting shows the kernelspec command and never asks for or shows a token', async () => {
  const { connect } = mount();
  fire(button('Connect a notebook'), 'click'); await settleMicrotasks();
  expect(connect).toHaveBeenCalledTimes(1);
  expect(root.querySelector('pre')!.textContent).toBe(grant.install);
  expect(text()).toContain('choose the kernel “Hooke · p0123456789abcdef” in Jupyter');
  expect(text()).toContain(grant.meaning);
  expect(button('Revoke notebook connection').disabled).toBe(false);
  expect(button('Connect again').textContent).toBe('Connect again (ends the earlier connection)');
});

test('revoking says a notebook still open can run nothing here', async () => {
  const { revoke } = mount();
  fire(button('Connect a notebook'), 'click'); await settleMicrotasks();
  fire(button('Revoke notebook connection'), 'click'); await settleMicrotasks();
  expect(revoke).toHaveBeenCalledTimes(1);
  expect(root.querySelector('pre')!.textContent).toBe('');
  expect(text()).toContain('A notebook still open can no longer run anything here.');
  expect(button('Revoke notebook connection').disabled).toBe(true);
});

test('stopping or replacing the session ends its notebook connection, and the panel says so', async () => {
  const { console } = mount();
  fire(button('Connect a notebook'), 'click'); await settleMicrotasks();
  console.sessionStopped('session-one');
  expect(text()).toContain('This session ended, so its notebook connection ended too.');
  expect(root.querySelector('pre')!.textContent).toBe('');
  expect(button('Connect a notebook').disabled).toBe(true);
});

test('a refused or malformed connection is said and nothing is shown as connected', async () => {
  mount(vi.fn(async () => { throw new Error('This session has no saved resource.'); }));
  fire(button('Connect a notebook'), 'click'); await settleMicrotasks();
  expect(text()).toContain('The notebook was not connected: This session has no saved resource.');
  expect(button('Revoke notebook connection').disabled).toBe(true);
  for (const bad of [{ ...grant, token: 'secret' }, { ...grant, name: 'other' }, { ...grant, install: 'rm -rf /' }, null])
    expect(() => notebookGrant(bad)).toThrow('invalid notebook connection');
});
