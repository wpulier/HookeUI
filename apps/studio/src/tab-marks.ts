// The Tab row's marks, on the mock's 20 px grid with a 1.5 px stroke (design/workspace-feel, `P`): each Tab's kind, the
// row's + and ⌄, ×, and the Stage bar's back chevron; Tools' task marks and the run page's marks share the one set. Shared by work Tabs (workspace-views.ts) and researcher and Study
// Tabs (workspace-research-tabs.ts), so every Tab has one anatomy.

const PATHS = {
  folder: '<path d="M3 6h5l2-2h6a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6z"/>',
  home: '<circle cx="10" cy="10" r="6"/><circle cx="10" cy="10" r="2"/>',
  picture: '<rect x="3.5" y="4.5" width="13" height="11" rx="1.5"/><path d="m4 14 4-4 3 3 2-2 3.5 3"/>',
  recording: '<rect x="3.5" y="5" width="9.5" height="10" rx="1.5"/><path d="m13 8.5 3.5-2v7l-3.5-2"/>',
  output: '<path d="M6 3.5h6l3 3V16a.5.5 0 0 1-.5.5h-8.5a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z"/><path d="M8 9.5h5M8 12h5M8 14.5h3"/>',
  compare: '<path d="M7.6 4.2 11 6.2v3.9l-3.4 2-3.4-2V6.2z"/><path d="M12.4 8.4l3.4 2v3.9l-3.4 2-3.4-2"/>',
  study: '<path d="M4 5.5h12M4 10h12M4 14.5h7"/><circle cx="14.5" cy="14.5" r="1.6"/>',
  plus: '<path d="M10 4.5v11M4.5 10h11"/>',
  down: '<path d="m6 8 4 4 4-4"/>',
  left: '<path d="m12 5-5 5 5 5"/>',
  x: '<path d="M5.5 5.5l9 9M14.5 5.5l-9 9"/>',
  stop: '<rect x="1" y="1" width="18" height="18" rx="3" fill="currentColor" stroke="none"/>',
  // Tools and the run page (the mock's icon set, same grid and stroke): a task's kind, the row's chevron, a click.
  right: '<path d="m8 5 5 5-5 5"/>',
  outline: '<path d="M10 3.6 15.6 6.8v6.4L10 16.4l-5.6-3.2V6.8z"/>',
  count: '<path d="M5 5v10M8 5v10M11 5v10M14 5v10M3.5 12.5 15.5 7"/>',
  find: '<circle cx="9" cy="9" r="5"/><path d="m13 13 3.5 3.5"/><path d="M7 9h4"/>',
  measure: '<rect x="3" y="7" width="14" height="6" rx="1"/><path d="M6 7v2.5M9 7v3.5M12 7v2.5M15 7v3.5"/>',
  bright: '<circle cx="10" cy="10" r="2.6"/><path d="M10 3.5v1.6M10 14.9v1.6M3.5 10h1.6M14.9 10h1.6M5.4 5.4l1.1 1.1M13.5 13.5l1.1 1.1M5.4 14.6l1.1-1.1M13.5 6.5l1.1-1.1"/>',
  clearer: '<circle cx="10" cy="10" r="6.2"/><path d="M10 3.8v12.4"/><path d="M10 6.5a3.5 3.5 0 0 1 0 7"/>',
  track: '<path d="M4 15c2-1 2.5-4 5-5s4 .5 7-5"/><circle cx="4" cy="15" r="1.2"/><circle cx="16" cy="5" r="1.2"/>',
  table: '<rect x="3.5" y="4" width="13" height="12" rx="1.5"/><path d="M3.5 8h13M3.5 12h13M8 8v8M12.5 8v8"/>',
  eyepiece: '<circle cx="10" cy="10" r="6"/><circle cx="10" cy="10" r="2"/>',
  camera: '<path d="M4 7.5h2.5L8 5.5h4l1.5 2H16a.5.5 0 0 1 .5.5v7a.5.5 0 0 1-.5.5H4a.5.5 0 0 1-.5-.5V8a.5.5 0 0 1 .5-.5z"/><circle cx="10" cy="11" r="2.5"/>',
  look: '<path d="M2.8 10s2.6-4.8 7.2-4.8 7.2 4.8 7.2 4.8-2.6 4.8-7.2 4.8S2.8 10 2.8 10z"/><circle cx="10" cy="10" r="2.2"/>',
  slide: '<rect x="3" y="6" width="14" height="8" rx="1"/><path d="M7 6v8"/>',
  name: '<path d="M4 6.5h12M4 10h12M4 13.5h7"/>',
  clock: '<circle cx="10" cy="10" r="6.5"/><path d="M10 6.5V10l2.5 1.8"/>',
  // Models and Add model or tool (step 8): the catalog, a link, AI building, a download, a tick, a cost.
  models: '<path d="M10 3.5 16.5 7 10 10.5 3.5 7z"/><path d="M3.5 10.2 10 13.7l6.5-3.5M3.5 13.3 10 16.8l6.5-3.5"/>',
  link: '<path d="M8.6 11.4a3 3 0 0 0 4.2 0l2.3-2.3a3 3 0 0 0-4.2-4.2l-.9.9"/><path d="M11.4 8.6a3 3 0 0 0-4.2 0l-2.3 2.3a3 3 0 0 0 4.2 4.2l.9-.9"/>',
  spark: '<path d="M10 3.5v3M10 13.5v3M3.5 10h3M13.5 10h3M5.6 5.6l2 2M12.4 12.4l2 2M5.6 14.4l2-2M12.4 7.6l2-2"/>',
  download: '<path d="M10 3.8v8.7M6.6 9.2 10 12.6l3.4-3.4M4.5 15.8h11"/>',
  tick: '<path d="m4.5 10.5 3.5 3.5 7.5-8"/>',
  search: '<circle cx="9" cy="9" r="5"/><path d="m13 13 3.5 3.5"/>',
  cost: '<circle cx="10" cy="10" r="6.5"/><path d="M12 7.9c-.4-.6-1.1-.9-2-.9-1.1 0-2 .6-2 1.4 0 1.9 4 1.1 4 3 0 .8-.9 1.5-2 1.5-.9 0-1.6-.4-2-1M10 5.8v1.2M10 13v1.2"/>',
} as const;
export type TabMark = keyof typeof PATHS;

/** A mark as an inline SVG in the text colour (hidden from assistive technology: the words beside it say it). */
export function tabMark(name: TabMark, className = 'workspace-tab-icon'): SVGSVGElement {
  const svg = (document.createElementNS?.('http://www.w3.org/2000/svg', 'svg') ?? document.createElement('svg')) as SVGSVGElement;
  svg.setAttribute('viewBox', '0 0 20 20');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', className);
  svg.innerHTML = PATHS[name];
  return svg;
}
