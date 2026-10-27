// The header's camera menu (phonepanel.ts): labelled, the full camera setup at its top, its four sources with No camera
// among them, and what is on stage with an explicit Switch to live camera (Will, 2026-10-15).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fire, settleMicrotasks, TestElement } from './workspace-windows.test-support';
import type { Doctor } from './camera/bridge-api';
import { PhonePanel } from './phonepanel';

const OFF: Doctor = { source: 'none', chosen: 'none', live: false, mode: 'off', headline: 'No camera',
  line: 'This uses pictures and videos only. Choose a camera when you want one.', first: null, items: [] };
const LIVE: Doctor = { source: 'continuity', chosen: 'continuity', live: true, mode: 'live', headline: 'Live', first: null,
  items: [{ id: 'arriving', title: 'The picture arrives', ok: true, evidence: '30 fps', fix: '', action: null, problem: '' }] };

describe('the camera menu', () => {
  let doctor: Doctor;
  let asked: string[];
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag), addEventListener: () => {}, hidden: true });
    vi.stubGlobal('window', { open: vi.fn() });
    doctor = LIVE;
    asked = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: { method?: string }) => {
      asked.push(`${init?.method ?? 'GET'} ${url.replace('http://bridge', '')}`);
      if (url.includes('/api/source')) doctor = url.includes('kind=none') ? OFF : LIVE;
      const body = url.includes('/api/doctor') ? doctor : url.includes('/api/phone') ? { reachable: false } : { ok: true };
      return { ok: true, status: 200, json: async () => body };
    }));
  });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  async function menu(onStage: string | null = null, feedSelector = false) {
    const root = new TestElement('section');
    root.hidden = true;
    const toggle = new TestElement('button');
    const panel = new PhonePanel(root as unknown as HTMLElement, toggle as unknown as HTMLButtonElement, () => 'http://bridge', () => {}, { feedSelector });
    const live = vi.fn();
    panel.onStage = () => onStage;
    panel.goLive = live;
    panel.setOpen(true);
    await vi.waitFor(() => expect(panel.doctor).not.toBeNull());
    return { root, toggle, panel, live };
  }
  const buttons = (root: TestElement) => root.querySelectorAll('button');
  const named = (root: TestElement, text: string) => buttons(root).find((b) => b.textContent === text) as TestElement;

  it('says what it is and puts the full camera setup first', async () => {
    const { root } = await menu();
    const [top] = root.children as TestElement[];
    expect(top.querySelector('h2')?.textContent).toBe('Camera');
    const setup = top.querySelector('.phone-setup-link') as TestElement;
    expect(setup.textContent).toBe('Open camera setup ↗');
    expect(root.querySelectorAll('h3').map((h) => h.textContent)).toContain('Source');
    expect(buttons(root.querySelector('.phone-sources') as TestElement).map((b) => b.textContent))
      .toEqual(['iPhone', 'Blackmagic', 'Other camera', 'No camera']);
    expect(root.querySelector('.phone-source-note')?.textContent).toContain('Continuity Camera');
    fire(setup, 'click');
    expect(window.open).toHaveBeenCalledWith('http://bridge/camera/', '_blank', 'noopener');
  });

  it('chooses No camera, and then says so calmly with nothing to fix', async () => {
    const { root, toggle } = await menu();
    fire(named(root, 'No camera'), 'click');
    for (let i = 0; i < 5; i += 1) await settleMicrotasks();
    expect(asked).toContain('POST /api/source?kind=none');
    expect(named(root, 'No camera').getAttribute('aria-pressed')).toBe('true');
    expect(root.querySelector('.phone-state')?.textContent).toBe('No camera');
    expect(root.querySelector('.cam-off')?.textContent).toContain('pictures and videos only');
    const row = root.querySelectorAll('.cam-row').find((r) => r.querySelectorAll('button').some((b) => b.textContent === 'Reconnect'));
    expect(row?.hidden).toBe(true); // Reconnect, Reset camera: nothing to fix
    expect((root.querySelector('.cam-fold') as TestElement).hidden).toBe(true);
    expect(toggle.dataset.doctor).toBe('off');
    expect(root.querySelector('.phone-source-note')?.textContent).toContain('Nothing connects');
  });

  it('with a recording on stage, says so and goes live only on Switch to live camera', async () => {
    const { root, live } = await menu('Sperm motility');
    const stage = root.querySelector('.phone-stage') as TestElement;
    expect(stage.hidden).toBe(false);
    expect(stage.textContent).toContain('On the Stage: Sperm motility');
    expect(live).not.toHaveBeenCalled(); // opening the menu changed nothing on stage
    fire(named(root, 'Switch to live camera'), 'click');
    expect(live).toHaveBeenCalledTimes(1);
    expect(root.hidden).toBe(true);
  });

  it('the Specimen menu always leads with Live camera: on the Stage, one click back, or how to choose one (Will, 2026-10-23)', async () => {
    const recording = await menu('Prerecorded video · Leaf', true);
    const row = recording.root.querySelector('.phone-choice-live') as TestElement;
    expect(row.textContent).toContain('Live camera'); expect(row.disabled).toBe(false);
    expect((recording.root.querySelector('.phone-stage') as TestElement).hidden).toBe(true); // not said twice
    fire(row, 'click'); expect(recording.live).toHaveBeenCalledTimes(1);
    const live = await menu(null, true);
    const now = live.root.querySelector('.phone-choice-live') as TestElement;
    expect(now.textContent).toContain('on the Stage'); expect(now.disabled).toBe(true);
    doctor = OFF;
    const none = await menu('Prerecorded video · Leaf', true);
    const choose = none.root.querySelector('.phone-choice-live') as TestElement;
    expect(choose.textContent).toContain('No camera chosen yet');
    fire(choose, 'click'); expect(none.live).not.toHaveBeenCalled(); // nothing switches without a camera
    expect((none.root.querySelector('.phone-camera') as unknown as { open: boolean }).open).toBe(true);
  });

  it('periodic refresh preserves the focused specimen node but never rebinds it to another Project opening', async () => {
    const { root, panel } = await menu(null, true);
    const owner = { session: 'project-a', generation: 1 };
    const first = vi.fn(), updated = vi.fn(), other = vi.fn();
    let choice = { id: 'source:asset-1', owner, label: 'Recording', detail: 'Video', act: first };
    panel.choices = () => [choice];
    await panel.refresh();
    const choiceRow = () => root.querySelector('.phone-choice-list')!.querySelector('.phone-choice') as TestElement; // after the fixed Live camera row
    const button = choiceRow();
    // Synthetic focus observation: removing the focused button loses focus, as in the browser.
    const focus = Object.assign(document, { activeElement: button });
    const remove = button.remove.bind(button);
    vi.spyOn(button, 'remove').mockImplementation(() => { if (focus.activeElement === button) Object.assign(focus, { activeElement: null }); remove(); });
    choice = { ...choice, label: 'Renamed recording', detail: 'Updated description', act: updated };
    Object.assign(document, { hidden: false });
    await vi.advanceTimersByTimeAsync(3000);
    expect(choiceRow()).toBe(button);
    expect(focus.activeElement).toBe(button);
    expect(button.textContent).toBe('Renamed recordingUpdated description');
    expect(button.remove).not.toHaveBeenCalled();
    fire(button, 'click');
    expect(updated).toHaveBeenCalledOnce();expect(first).not.toHaveBeenCalled();
    panel.setOpen(true);await settleMicrotasks();
    // Returning to the same Project with a new opening is a different owner, even with identical labels/IDs.
    choice = { ...choice, owner: { ...owner, generation: 3 }, act: other };
    await panel.refresh();
    const replacement = choiceRow();
    expect(replacement).not.toBe(button);expect(button.parentElement).toBeNull();
    expect(focus.activeElement).toBeNull();expect(other).not.toHaveBeenCalled();
    fire(replacement, 'click');expect(other).toHaveBeenCalledOnce();
  });

  it("the header's camera control only opens this menu: nothing in the studio puts the camera on stage from its click", async () => {
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const main = readFileSync(fileURLToPath(new URL('./main.ts', import.meta.url)), 'utf8');
    expect(main).not.toMatch(/source\.addEventListener\('click'/); // the trap of 2026-10-14: "Live camera" swapped a recording out
    expect(main).not.toMatch(/'go-live'/);
    expect(main.match(/phonePanel\.goLive = \(\) => void ([A-Za-z]+)\('live'\);/)?.[1]).toBe('openUserInput'); // shared input-Tab admission, never a direct feed replacement // only Switch to live camera does
  });
});
