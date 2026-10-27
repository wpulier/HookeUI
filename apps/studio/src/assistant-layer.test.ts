// The Assistant over a researcher's or a Study's Tab (assistant-layer.ts, used by workspace-study.ts): the Stage bar's
// Assistant opens and closes it, Escape closes it, and focus goes to its composer, then back to the button.
import { afterEach, expect, test, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { AssistantLayer } from './assistant-layer';
import { fire, TestElement } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());

test('the Stage bar’s Assistant opens a layer of its own; Escape or a second press closes it and focus returns to the button', () => {
  const panel = new TestElement('aside'), button = new TestElement('button'), field = new TestElement('textarea');
  let focused: TestElement | null = null, active = true, drawn = 0;
  for (const node of [button, field]) Object.assign(node, { focus: () => { focused = node; } });
  const layer = new AssistantLayer({ panel: panel as unknown as HTMLElement, button: button as unknown as HTMLElement, field: () => field as unknown as HTMLElement,
    active: () => active, changed: () => { drawn += 1; } });
  expect(layer.open).toBe(false);
  layer.toggle();
  expect([layer.open, drawn, focused]).toEqual([true, 1, field]);
  fire(panel, 'keydown', { key: 'Escape' });
  expect([layer.open, drawn, focused]).toEqual([false, 2, button]);
  layer.toggle(); layer.toggle();
  expect([layer.open, focused]).toEqual([false, button]);
  // Escape does nothing when the layer is closed, when another control already took it, or away from these Tabs.
  fire(panel, 'keydown', { key: 'Escape' }); expect(drawn).toBe(4);
  layer.set(true);
  fire(panel, 'keydown', { key: 'Escape', defaultPrevented: true }); expect(layer.open).toBe(true);
  active = false; fire(panel, 'keydown', { key: 'Escape' }); expect(layer.open).toBe(true);
  // Leaving the Tab drops the layer without moving focus.
  focused = null; layer.reset();
  expect([layer.open, focused]).toEqual([false, null]);
});

test('the workspace uses the layer on researcher, Study and reader Tabs: the Assistant button toggles it, a Study’s box opens it, leaving resets it', () => {
  const study = readFileSync(new URL('./workspace-study.ts', import.meta.url), 'utf8');
  // Where a page holds the Stage (a researcher's or a Study's, or a reader Tab's one Data item), the Assistant is a layer.
  expect(study).toContain("const assistantAside = () => document.body.dataset.researchOpen === 'true' || document.body.dataset.readerTab === 'true';");
  expect(study).toMatch(/if \(assistantAside\(\)\) \{ assistantLayer\.toggle\(\); return; \}/);
  expect(study).toContain("new AssistantLayer({ panel: rightPanel, button: agentButton, field: () => find('question'),");
  // A Study's composer opens the layer and sends its words through the Assistant's composer.
  expect(study).toContain('ask:(text:string)=>{assistantLayer.set(true);');
  expect(study).toContain('assistantLayer.reset();applyLayout();');
  expect(study).toContain('document.body.dataset.assistantOver = String(aside && assistantLayer.open);');
  // The Stage bar's Stop shows from researchStageBar (research-tab.ts), so it goes once Stop is asked.
  expect(study).toContain("researchStopRid=row&&bar!.stop?row.rid:'';researchStop.hidden=!researchStopRid;");
});
