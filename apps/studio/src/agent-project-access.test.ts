import { afterEach, describe, expect, it, vi } from 'vitest';
import { syntheticDOM } from './workspace-windows.test-support';
afterEach(() => vi.unstubAllGlobals());
import { mountProjectAccess } from './agent-project-access';

describe('Project access', () => {
  it('captures the chosen Project, exact connection and role, then reports the saved result', async () => {
    const request = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ editable: true,
      projects: [{ session: '2026-10-28.2', name: 'Microscopy', by: 'agent:ac_owner', grants: {} }] })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true })));
    vi.stubGlobal('document', syntheticDOM().document);
    const host = document.createElement('div');
    await mountProjectAccess(host, { id: 'ac_reviewer', label: 'Reviewer' }, request);
    const role = host.querySelectorAll('select')[1];
    role.value = 'contribute';
    await host.querySelector<HTMLButtonElement>('button')!.onclick!(undefined as unknown as PointerEvent);
    await vi.waitFor(() => expect(host.textContent).toContain('Reviewer now has contribute access to Microscopy'));
    expect(JSON.parse(request.mock.calls[1][1]!.body as string)).toEqual({session:'2026-10-28.2',connection:'ac_reviewer',role:'contribute'});
  });
  it('does not offer unsupported native grants', async () => {
    const request = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({ editable:false,projects:[] })));
    vi.stubGlobal('document', syntheticDOM().document);
    const host = document.createElement('div');
    await mountProjectAccess(host,{id:'ac_x',label:'X'},request);
    expect(host.querySelector('button')).toBeNull();
    expect(host.textContent).toContain('cannot grant access yet');
  });
});
