// Actual main-shared note renderer and SessionHome events over synthetic DOM; full bootstrap is browser acceptance.
import { afterEach, expect, test, vi } from 'vitest';
import { SetupNote } from './setup-note';
import { SessionHome } from './session-home';
import { syntheticDOM } from './workspace-windows.test-support';
import type { CapabilitiesWire, ModelRow } from './protocol';

afterEach(() => vi.unstubAllGlobals());
test('ordinary session and home transitions suppress optional Seeing solicitation for the local example and empty workspace', () => {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const root = dom.document.createElement('div'), homeRoot = dom.document.createElement('section');
  const note = new SetupNote(root as unknown as HTMLElement, vi.fn(), vi.fn());
  const home = new SessionHome(homeRoot as unknown as HTMLElement, { live: vi.fn(), open: vi.fn(), addFiles: vi.fn(), example: vi.fn(),
    library: vi.fn(), reconnect: vi.fn(), troubleshoot: vi.fn(), noCamera: vi.fn(), cameraPage: () => '/camera/' });
  let localOnly = false;
  home.onVisibilityChange = () => note.context(localOnly, home.isOpen()); // same main assignment, actual home events
  const seeing: ModelRow = { id:'gemini',name:'Gemini',runs:'cloud',where:'cloud',what:'Seeing',kind:'model',version:'v',credit:{},link:'',
    licence:'',tier:'open',mine:false,status:'needs-setup',word:'Needs your Google Gemini API key',group:'needs-you',chosen:true,inUse:true,
    next:{kind:'key',says:'Add key',service:'gemini'},steps:[],facts:[],size:0,files:[],folder:'',revision:'',
    usedFor:[{role:'vision',title:'Seeing',option:'gemini',options:['Gemini'],current:true,fixed:false}] };
  const wire: CapabilitiesWire = {type:'capabilities', families:[], actors:{you:'',agent:'',auto:'',researcher:''},capabilities:[],roles:[],
    preferences:[],pending:0,build:'',agentText:'',models:[seeing]};
  const unchanged = JSON.stringify(wire);
  note.update(wire); home.open('choose'); expect(root.hidden).toBe(true);
  home.close(); expect(root.hidden).toBe(true); // nothing has asked for the key yet: quiet (never at first launch)
  note.requested(); expect(root.hidden).toBe(false); expect(root.textContent).toContain('The Assistant needs your Google Gemini API key to see your specimen.');
  localOnly = true; note.context(localOnly, home.isOpen()); expect(root.hidden).toBe(true);
  note.update(wire); expect(root.hidden).toBe(true); // capability polling, including skipped/completed guide
  home.open('choose'); home.close(); expect(root.hidden).toBe(true);
  localOnly = false; note.context(localOnly, home.isOpen()); expect(root.hidden).toBe(false);
  home.open('camera'); expect(root.hidden).toBe(true);
  expect(JSON.stringify(wire)).toBe(unchanged); // no preference or availability rewrite
});

test('in the workspace the note is said beside the Assistant while nothing is on the Stage too, never in the example', () => {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const root = dom.document.createElement('div');
  const note = new SetupNote(root as unknown as HTMLElement, vi.fn(), vi.fn(), { whenEmpty: true });
  const seeing = { id:'gemini',name:'Gemini',status:'needs-setup',word:'Needs your Google Gemini API key',group:'needs-you',chosen:true,inUse:true,
    next:{kind:'key',says:'Add key',service:'gemini'},steps:[],usedFor:[{role:'vision',title:'Seeing',option:'gemini',options:['Gemini'],current:true,fixed:false}] } as unknown as ModelRow;
  note.update({ type:'capabilities', families:[], capabilities:[], roles:[], preferences:[], models:[seeing] } as unknown as CapabilitiesWire);
  expect(root.hidden).toBe(true); // before the Project is known (its day message), quiet: it may be the example
  note.context(false, true); expect(root.hidden).toBe(true); // and quiet until something asks for the key
  note.requested(); expect(root.hidden).toBe(false); expect(root.textContent).toContain('Google Gemini');
  note.context(true, true); expect(root.hidden).toBe(true);
});
