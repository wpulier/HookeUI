import './style.css';
import { createCellScene } from './scene/cellScene';
import type {
  ProbeStackDetail,
  RenderStats,
  SelectableGroup,
  SelectableItem,
  SelectionDetail,
} from './scene/types';

const app = document.querySelector<HTMLDivElement>('#app');

if (!app) {
  throw new Error('App root was not found.');
}
const appRoot = app;

function requireElement<T extends Element>(element: T | null, name: string): T {
  if (!element) {
    throw new Error(`${name} could not be initialized.`);
  }

  return element;
}

appRoot.innerHTML = `
  <main class="cell-app" aria-label="Interactive mammalian cell visualization">
    <section class="viewport-shell">
      <div class="scene-toolbar" aria-label="Scene controls">
        <button class="pause-toggle control-button" type="button" aria-pressed="false">
          <span class="pause-icon" aria-hidden="true"></span>
          <span class="pause-copy">Pause</span>
        </button>
        <button class="probe-toggle control-button" type="button" aria-pressed="false">
          <span class="probe-icon" aria-hidden="true"></span>
          <span>Probe</span>
        </button>
        <div class="probe-readout" aria-live="polite"></div>
      </div>
      <div class="quality-readout" aria-live="polite">Draws 0 | Tris 0</div>
      <nav class="selector-rail" aria-label="Organelle selector"></nav>
      <section class="selector-drawer" aria-label="Selection details" hidden></section>
      <div id="cell-scene" class="cell-scene" aria-label="3D mammalian cell scene"></div>
      <aside class="inspector" aria-live="polite" data-state="idle">
        <div class="inspector-head">
          <div>
            <p class="inspector-kicker">No selection</p>
            <h1 class="inspector-title">Cell overview</h1>
          </div>
          <button class="clear-selection" type="button" aria-label="Clear selection" hidden>×</button>
        </div>
        <p class="inspector-type">Whole-cell view</p>
        <p class="inspector-description">
          Select a structure to inspect it without interrupting the scene.
        </p>
        <dl class="scale-notes">
          <div>
            <dt>Biological scale</dt>
            <dd>Approx. 30 micrometer cell diameter</dd>
          </div>
          <div>
            <dt>Visual scale</dt>
            <dd>Tiny structures are enlarged for inspection</dd>
          </div>
        </dl>
      </aside>
    </section>
  </main>
`;

const sceneHost = requireElement(appRoot.querySelector<HTMLDivElement>('#cell-scene'), 'Scene host');
const pauseButton = requireElement(appRoot.querySelector<HTMLButtonElement>('.pause-toggle'), 'Pause button');
const pauseCopy = requireElement(appRoot.querySelector<HTMLSpanElement>('.pause-copy'), 'Pause label');
const probeButton = requireElement(appRoot.querySelector<HTMLButtonElement>('.probe-toggle'), 'Probe button');
const probeReadout = requireElement(appRoot.querySelector<HTMLDivElement>('.probe-readout'), 'Probe readout');
const selectorRail = requireElement(appRoot.querySelector<HTMLElement>('.selector-rail'), 'Selector rail');
const selectorDrawer = requireElement(appRoot.querySelector<HTMLElement>('.selector-drawer'), 'Selector drawer');
const inspector = requireElement(appRoot.querySelector<HTMLElement>('.inspector'), 'Inspector');
const inspectorKicker = requireElement(appRoot.querySelector<HTMLParagraphElement>('.inspector-kicker'), 'Inspector kicker');
const inspectorTitle = requireElement(appRoot.querySelector<HTMLHeadingElement>('.inspector-title'), 'Inspector title');
const inspectorType = requireElement(appRoot.querySelector<HTMLParagraphElement>('.inspector-type'), 'Inspector type');
const inspectorDescription = requireElement(
  appRoot.querySelector<HTMLParagraphElement>('.inspector-description'),
  'Inspector description',
);
const clearButton = requireElement(appRoot.querySelector<HTMLButtonElement>('.clear-selection'), 'Clear button');
const scaleNotes = requireElement(appRoot.querySelector<HTMLDListElement>('.scale-notes'), 'Scale notes');
const statsReadout = requireElement(appRoot.querySelector<HTMLDivElement>('.quality-readout'), 'Stats readout');

const scene = createCellScene(sceneHost);
const selectableGroups = scene.getSelectableGroups();
let isPaused = false;
let isProbeMode = false;
let activeDrawerGroupId: string | null = null;
let selectedDetail: SelectionDetail | null = null;

function scaleNotesHtml(biologicalScale: string, visualScaleNote: string) {
  return `
    <div>
      <dt>Biological scale</dt>
      <dd>${biologicalScale}</dd>
    </div>
    <div>
      <dt>Visual scale</dt>
      <dd>${visualScaleNote}</dd>
    </div>
  `;
}

function renderInspector(detail: SelectionDetail | null, state: 'idle' | 'preview' | 'selected') {
  const meta = detail?.meta ?? null;
  inspector.dataset.state = state;
  clearButton.hidden = state !== 'selected';

  if (!meta) {
    inspectorKicker.textContent = 'No selection';
    inspectorTitle.textContent = 'Cell overview';
    inspectorType.textContent = 'Whole-cell view';
    inspectorDescription.textContent = 'Select a structure to inspect it without interrupting the scene.';
    scaleNotes.innerHTML = scaleNotesHtml('Approx. 30 micrometer cell diameter', 'Tiny structures are enlarged for inspection');
    return;
  }

  inspectorKicker.textContent = state === 'selected' ? 'Selected structure' : 'Preview';
  inspectorTitle.textContent = meta.name;
  inspectorType.textContent = meta.type;
  inspectorDescription.textContent = meta.description;
  scaleNotes.innerHTML = scaleNotesHtml(meta.biologicalScale, meta.visualScaleNote);
}

function childButtonHtml(item: SelectableItem, depth = 0): string {
  const instanceAttr = item.instanceId === undefined ? '' : ` data-instance-id="${item.instanceId}"`;
  const denseClass = item.name.length <= 4 ? ' is-compact' : '';
  const children = item.children?.length
    ? `<div class="selector-children" data-depth="${depth + 1}">${item.children.map((child) => childButtonHtml(child, depth + 1)).join('')}</div>`
    : '';

  return `
    <div class="selector-child-wrap" data-depth="${depth}">
      <button class="selector-child${denseClass}" type="button" data-target-id="${item.targetId}"${instanceAttr}>
        ${item.name}
      </button>
      ${children}
    </div>
  `;
}

function renderSelectorRail(groups: SelectableGroup[]) {
  selectorRail.innerHTML = groups
    .map(
      (group) => `
        <button
          class="selector-item"
          type="button"
          data-group-id="${group.id}"
          data-target-id="${group.targetId}"
          style="--item-color: ${group.color}"
          aria-label="${group.name}"
        >
          <span class="selector-swatch" aria-hidden="true"></span>
          <span class="selector-label">${group.name}</span>
          <span class="selector-count">${group.count}</span>
        </button>
      `,
    )
    .join('');
}

function renderDrawer(group: SelectableGroup | null) {
  if (!group?.children?.length) {
    selectorDrawer.hidden = true;
    selectorDrawer.innerHTML = '';
    return;
  }

  selectorDrawer.hidden = false;
  selectorDrawer.innerHTML = `
    <div class="drawer-head">
      <p>${group.name}</p>
      <button class="drawer-close" type="button" aria-label="Close selector">×</button>
    </div>
    <div class="drawer-list">
      ${group.children.map((child) => childButtonHtml(child)).join('')}
    </div>
  `;
}

function setActiveSelection(detail: SelectionDetail | null) {
  const targetId = detail?.targetId ?? '';
  const instanceId = detail?.instanceId;
  const groupId = detail?.groupId ?? '';

  appRoot.querySelectorAll<HTMLElement>('.selector-item, .selector-child').forEach((button) => {
    const buttonTarget = button.dataset.targetId ?? '';
    const buttonGroup = button.dataset.groupId ?? '';
    const buttonInstance = button.dataset.instanceId === undefined ? undefined : Number(button.dataset.instanceId);
    const isActive =
      (buttonTarget === targetId && buttonInstance === instanceId) ||
      (buttonGroup !== '' && buttonGroup === groupId && instanceId === undefined);
    button.classList.toggle('is-active', isActive);
  });
}

renderSelectorRail(selectableGroups);
renderInspector(null, 'idle');

pauseButton.addEventListener('click', () => {
  isPaused = !isPaused;
  scene.setPaused(isPaused);
  pauseButton.setAttribute('aria-pressed', String(isPaused));
  pauseCopy.textContent = isPaused ? 'Resume' : 'Pause';
});

probeButton.addEventListener('click', () => {
  isProbeMode = !isProbeMode;
  scene.setProbeMode(isProbeMode);
  probeButton.setAttribute('aria-pressed', String(isProbeMode));
  appRoot.classList.toggle('is-probing', isProbeMode);
  probeReadout.textContent = isProbeMode ? 'Stack ready' : '';
});

selectorRail.addEventListener('click', (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>('.selector-item');
  if (!button) {
    return;
  }

  const groupId = button.dataset.groupId ?? '';
  const targetId = button.dataset.targetId ?? '';
  const group = selectableGroups.find((item) => item.id === groupId) ?? null;
  activeDrawerGroupId = group?.children?.length ? group.id : null;
  renderDrawer(group?.id === activeDrawerGroupId ? group : null);
  scene.selectTarget(targetId);
});

selectorDrawer.addEventListener('click', (event) => {
  const close = (event.target as HTMLElement).closest<HTMLButtonElement>('.drawer-close');
  if (close) {
    activeDrawerGroupId = null;
    renderDrawer(null);
    return;
  }

  const button = (event.target as HTMLElement).closest<HTMLButtonElement>('.selector-child');
  if (!button) {
    return;
  }

  const targetId = button.dataset.targetId ?? '';
  const instanceId = button.dataset.instanceId === undefined ? undefined : Number(button.dataset.instanceId);
  scene.selectTarget(targetId, instanceId);
});

clearButton.addEventListener('click', () => {
  scene.clearSelection();
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    scene.clearSelection();
    activeDrawerGroupId = null;
    renderDrawer(null);
  }
});

sceneHost.addEventListener('cell-selection-change', (event) => {
  const detail = (event as CustomEvent<SelectionDetail>).detail;
  selectedDetail = detail.meta ? detail : null;
  if (!selectedDetail) {
    activeDrawerGroupId = null;
    renderDrawer(null);
  }
  renderInspector(selectedDetail, selectedDetail ? 'selected' : 'idle');
  setActiveSelection(selectedDetail);
});

sceneHost.addEventListener('cell-probe-stack-change', (event) => {
  const detail = (event as CustomEvent<ProbeStackDetail>).detail;

  if (!isProbeMode) {
    probeReadout.textContent = '';
    return;
  }

  probeReadout.textContent = detail.stackSize > 0 ? `${detail.stackIndex + 1} / ${detail.stackSize}` : 'Stack ready';
});

sceneHost.addEventListener('cell-render-stats', (event) => {
  const stats = (event as CustomEvent<RenderStats>).detail;
  statsReadout.textContent = `Draws ${stats.drawCalls} | Tris ${stats.triangles}`;
});

window.addEventListener('beforeunload', () => {
  scene.dispose();
});
