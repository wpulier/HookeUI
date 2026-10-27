// H05 (P2) production-path browser recipes: the installed Studio, a fresh browser profile, a real confined napari
// worker. Usage: node scientific_browser_p2.mjs <playwright index.mjs> <chromium> <profile dir> <app url>
// <document title> <scientific_comparison_recipes.py> [only]
// Prints one JSON object: {opened, recipes, results: {childId: {ok, detail | error}}, initial, ops, checkpoints}.
//
// Ledger procedure steps whose oracle asks for matched native screenshots or analytic geometry, Qt controls whose
// requirement includes every upstream state, and direct 3D drags (plain napari has no offscreen canvas to replay
// them on) have no recipe here. Every recipe begins from the document opened through Project Data (Data → its row,
// Open <title> → Open session → Refresh preview), operates the ordinary View controls with the mouse and keyboard and presses
// Apply view changes. Appearance replay records the complete submitted transaction; other controls record their
// upstream setter and current values before Apply. After each recipe it reads the session's napari state
// through Studio's own Python console. The test replays the equivalents in a plain pinned-napari process and
// compares the states. Each recipe here also checks that the control shows the value the worker reports back and
// that the rendered preview's pixels changed.
// only: comma-separated ids (to debug, or for a negative); inject: a Python file run in the session's console first.
const [, , playwrightPath, chromium, profile, url, title, recipesPath, only, inject, sourcePaths] = process.argv;
import { readFileSync } from 'node:fs';
import {createHash} from 'node:crypto';
import {readConsoleJSON, stopScientificSession, appearanceTransaction, captureAppearanceSettings, isOwnedViewChange} from './scientific_p2_observation.mjs';
import { scientificDocument } from './scientific_document_scope.mjs';
const { chromium: browserType } = await import(playwrightPath);
const context = await browserType.launchPersistentContext(profile, {
  executablePath: chromium, headless: true, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2,
});
const page = context.pages()[0] ?? await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(String(error).slice(0, 300)));

const document = scientificDocument(page, title);
const label = document.label, button = document.button;
const navigationButton = name => page.getByRole('button', {name, exact:true});
const LAYER = 'Native input';
const MESH = 'Measured mesh';
const touched = new Set();  // controls edited since the last Apply
const transactions = [];  // submitted appearance payloads retained with their returned layer identity
const ops = [];  // every applied control's upstream equivalent, in order
const checkpoints = [];  // after each recipe: how many ops preceded it, and the session's state then

async function until(test, seconds, what) {
  const deadline = Date.now() + seconds * 1000;
  while (Date.now() < deadline) {
    if (await test()) return;
    await page.waitForTimeout(500);
  }
  const said = (await document.statuses().allInnerTexts()).filter(Boolean).join(' | ');
  throw new Error(`timed out waiting for ${what}; the page said: ${said}`);
}

async function pixels() {
  // The private scientific preview canvas: a cheap digest of its drawn pixels.
  if (!(await document.preview().count())) return null;
  return await document.preview().evaluate(canvas => {
    const context = canvas?.getContext('2d');
    if (!context) return null;
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let hash = 0;
    for (let i = 0; i < data.length; i += 7) hash = (hash * 31 + data[i]) >>> 0;
    return `${canvas.width}x${canvas.height}:${hash}`;
  });
}

async function lit() {
  return await document.preview().evaluate(canvas => {
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let count = 0;
    for (let i = 0; i < data.length; i += 4) if (data[i] > 0) count++;
    return count;
  });
}

const AXES = ['depth', 'vertical', 'horizontal'];
const numbers = async names => Promise.all(names.map(async name => Number(await label(name).inputValue())));

async function equivalents() {
  // The upstream equivalent of every control edited since the last Apply, from the values the controls now hold.
  const out = [], seen = new Set();
  const add = async (key, make) => { if (!seen.has(key)) { seen.add(key); out.push(await make()); } };
  const set = (layer, path, value) => ['set', layer, path, value];
  for (const name of touched) {
    const value = async () => await label(name).inputValue();
    const field = { [`Volume rendering ${LAYER}`]: ['rendering', String], [`Attenuation ${LAYER}`]: ['attenuation', Number],
                    [`Isosurface threshold ${LAYER}`]: ['iso_threshold', Number],
                    [`3D interpolation ${LAYER}`]: ['interpolation3d', String],
                    [`Image depiction ${LAYER}`]: ['depiction', String] }[name];
    if (name === 'Displayed dimensions') await add(name, async () => ['ndisplay', Number(await value())]);
    else if (field) await add(name, async () => set(LAYER, field[0], field[1](await value())));
    else if (name.startsWith('Plane ')) {
      await add('plane', async () => ['update', LAYER, 'plane', {
        position: await numbers(AXES.map(a => `Plane position ${a} ${LAYER}`)),
        normal: await numbers(AXES.map(a => `Plane normal ${a} ${LAYER}`)),
        thickness: Number(await label(`Plane thickness ${LAYER}`).inputValue()) }]);
    } else if (name === 'clipping') {
      await add('clipping', async () => {
        const planes = [];
        for (let n = 1; await label(`Enable clipping plane ${n} ${LAYER}`).count(); n++) {
          planes.push({ position: await numbers(AXES.map(a => `Clipping plane ${n} position ${a} ${LAYER}`)),
                        normal: await numbers(AXES.map(a => `Clipping plane ${n} normal ${a} ${LAYER}`)),
                        enabled: await label(`Enable clipping plane ${n} ${LAYER}`).isChecked() });
        }
        return set(LAYER, 'experimental_clipping_planes', planes);
      });
    } else if (name.startsWith('Camera ') || name.endsWith(' direction')) {
      await add('camera', async () => ['camera', {
        center: await numbers(AXES.map(a => `Camera center ${a}`)), angles: await numbers(AXES.map(a => `Camera angle ${a}`)),
        zoom: Number(await label('Camera zoom').inputValue()), perspective: Number(await label('Camera perspective').inputValue()),
        orientation: await Promise.all(['Depth direction', 'Vertical direction', 'Horizontal direction'].map(d => label(d).inputValue())) }]);
    } else if (name === `Shading ${MESH}`) await add(name, async () => set(MESH, 'shading', await value()));
    else {
      const part = /^(Show wireframe|Wireframe )/.test(name) ? ['wireframe', 'Wireframe']
        : /^(Show face normals|Face normals )/.test(name) ? ['normals.face', 'Face normals']
        : /^(Show vertex normals|Vertex normals )/.test(name) ? ['normals.vertex', 'Vertex normals'] : null;
      if (!part) throw new Error(`no upstream equivalent is declared for the control ${name}`);
      await add(part[0], async () => {
        const [path, prefix] = part, settings = { visible: await label(`Show ${prefix.toLowerCase()} ${MESH}`).isChecked(),
          width: Number(await label(`${prefix} width ${MESH}`).inputValue()),
          color: await numbers(['red', 'green', 'blue', 'alpha'].map(c => `${prefix} ${c} ${MESH}`)) };
        if (prefix !== 'Wireframe') settings.length = Number(await label(`${prefix} length ${MESH}`).inputValue());
        return ['update', MESH, path, settings];
      });
    }
  }
  return out;
}

async function python(source) {
  // Studio's own Python console for this private session: one cell, its last printed line.
  await document.tab('Python').click();
  const history = label('Python cell history');
  const before = await history.locator('section').count();
  await label('Python code').fill(source);
  await until(async () => await button('Run code').isEnabled({timeout:500}), 60, 'Run code');
  await button('Run code').click();
  await until(async () => (await history.locator('section').count()) > before, 120, 'the Python cell');
  const cell = history.locator('section').last();
  const failed = cell.locator('pre[aria-label="error"]');
  if (await failed.count()) throw new Error(`Python said: ${(await failed.innerText()).slice(-1500)}`);
  const printed = (await cell.locator('pre[aria-label="stdout"]').innerText().catch(() => '')).trim().split('\n');
  await document.tab('View').click();
  return printed[printed.length - 1];
}

async function apply() {
  const before = await pixels();
  const equivalentsBefore = await equivalents();
  const appearanceKind = title === 'H05 surface' ? 'surface' : 'image';
  const appearanceName = appearanceKind === 'surface' ? MESH : LAYER;
  const captured = await captureAppearanceSettings(label, appearanceKind, appearanceName);
  touched.clear();
  const responsePromise = page.waitForResponse(response => isOwnedViewChange(response.request(), ownedReview), {timeout:60000});
  const [response] = await Promise.all([responsePromise, button('Apply view changes').click()]);
  if (!response.ok()) throw new Error(`View change returned ${response.status()}`);
  const submitted = response.request().postDataJSON(), change = submitted.request.request;
  if (['image-presentation','surface-presentation'].includes(change.operation)) {
    const result = await response.json();
    if (change.operation !== `${appearanceKind}-presentation`) throw new Error('Appearance transaction has the wrong owned family');
    const op = appearanceTransaction(submitted, result.frame, appearanceName, captured);
    transactions.push({url:response.url(), captured, submitted, target:result.frame.layers.find(layer => layer.id === change.value.layer)});
    ops.push(op);
  } else ops.push(...equivalentsBefore);
  // A new frame replaces the preview once the worker has applied the change and rendered it.
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(500);
    const status = await label('Scientific session status').innerText().catch(() => '');
    if ((await pixels()) !== before && !/Applying|Rendering|Opening/i.test(status)) break;
  }
  await page.waitForTimeout(500);
  return { before, after: await pixels() };
}

async function choose(name, value) {
  await label(name).selectOption(value);
  touched.add(name);
  const frames = await apply();
  const shown = await label(name).inputValue();
  if (shown !== value) throw new Error(`${name} shows ${shown} after applying ${value}`);
  return frames;
}

async function type(name, value) {
  await label(name).fill(String(value));
  touched.add(name);
  const frames = await apply();
  const shown = Number(await label(name).inputValue());
  if (Math.abs(shown - Number(value)) > 1e-6 * Math.max(1, Math.abs(Number(value)))) {
    throw new Error(`${name} shows ${shown} after applying ${value}`);
  }
  return frames;
}

function changed(frames, what) {
  if (!frames.before || frames.before === frames.after) throw new Error(`${what} did not change the rendered preview`);
  return { changed: true };
}

async function options(name) {
  return await label(name).evaluate(select => [...select.options].map(option => option.value));
}

const MODES = ['translucent', 'additive', 'iso', 'mip', 'minip', 'attenuated_mip', 'average'];  // ImageRendering, pinned
const DIRECTIONS = { 'Depth direction': ['towards', 'away'], 'Vertical direction': ['up', 'down'],
                     'Horizontal direction': ['left', 'right'] };

async function mode(value) {
  if ((await label(`Volume rendering ${LAYER}`).inputValue()) === value) {
    await choose(`Volume rendering ${LAYER}`, value === 'mip' ? 'translucent' : 'mip');
  }
  return changed(await choose(`Volume rendering ${LAYER}`, value), `Volume rendering ${value}`);
}

async function direction(name, value) {
  const [first, second] = DIRECTIONS[name];
  if ((await label(name).inputValue()) === value) await choose(name, value === first ? second : first);
  return changed(await choose(name, value), `${name} ${value}`);
}

async function camera(name, value) {
  return changed(await type(name, value), name);
}

async function depiction(value) {
  if ((await label(`Image depiction ${LAYER}`).inputValue()) === value) {
    await choose(`Image depiction ${LAYER}`, value === 'plane' ? 'volume' : 'plane');
  }
  return changed(await choose(`Image depiction ${LAYER}`, value), `Image depiction ${value}`);
}

async function plane(names, values) {
  if ((await label(`Image depiction ${LAYER}`).inputValue()) !== 'plane') await choose(`Image depiction ${LAYER}`, 'plane');
  for (const [index, name] of names.entries()) {
    await label(`${name} ${LAYER}`).fill(String(values[index]));
    touched.add(`${name} ${LAYER}`);
  }
  const frames = await apply();
  for (const [index, name] of names.entries()) {
    const shown = Number(await label(`${name} ${LAYER}`).inputValue());
    if (Math.abs(shown - values[index]) > 1e-6) throw new Error(`${name} shows ${shown} after applying ${values[index]}`);
  }
  return changed(frames, names.join(', '));
}

async function clipping(count) {
  if ((await label(`Image depiction ${LAYER}`).inputValue()) !== 'volume') await choose(`Image depiction ${LAYER}`, 'volume');
  while (await label(new RegExp(`^Remove clipping plane \\d+ ${LAYER}$`)).count()) {
    await label(new RegExp(`^Remove clipping plane \\d+ ${LAYER}$`)).first().click();
    touched.add('clipping');
    await apply();
  }
  const positions = [[6, 20, 30], [6, 20, 30]], normals = [[0, 0, 1], [0, 1, 0]];
  for (let n = 1; n <= count; n++) {
    await button(`Add clipping plane ${LAYER}`).click();
    touched.add('clipping');
    for (const [index, axis] of ['depth', 'vertical', 'horizontal'].entries()) {
      await label(`Clipping plane ${n} position ${axis} ${LAYER}`).fill(String(positions[n - 1][index]));
      await label(`Clipping plane ${n} normal ${axis} ${LAYER}`).fill(String(normals[n - 1][index]));
    }
    await label(`Enable clipping plane ${n} ${LAYER}`).check();
  }
  const frames = await apply();
  for (let n = 1; n <= count; n++) {
    if (!(await label(`Enable clipping plane ${n} ${LAYER}`).isChecked())) throw new Error(`clipping plane ${n} is not enabled`);
  }
  changed(frames, `${count} clipping plane(s)`);
  // Disabling them through the same control restores the unclipped render.
  for (let n = 1; n <= count; n++) await label(`Enable clipping plane ${n} ${LAYER}`).uncheck();
  touched.add('clipping');
  const off = await apply();
  if (off.after === frames.after) throw new Error('disabling the clipping planes did not change the preview');
  return { planes: count, enabled: true, disabled: true };
}

const RECIPES = {
  ...Object.fromEntries(MODES.map(value => [`P2.volume-modes.imagerendering-${value.replace('_', '-')}`, () => mode(value)])),
  'P2.volume-modes.image-rendering': () => mode('average'),
  'P2.volume-parameters.image-attenuation': async () => {
    await mode('attenuated_mip');
    return changed(await type(`Attenuation ${LAYER}`, 0.4), 'Attenuation');
  },
  'P2.volume-parameters.image-iso-threshold': async () => {
    // Upstream's meaning: a higher threshold draws a smaller isosurface, so fewer pixels are lit.
    await mode('iso');
    const surfaces = [];
    for (const value of [10, 40]) { await type(`Isosurface threshold ${LAYER}`, value); surfaces.push(await lit()); }
    if (!(surfaces[0] > surfaces[1] && surfaces[1] > 0)) throw new Error(`lit pixels at thresholds 10 and 40: ${surfaces}`);
    return { lit: surfaces };
  },
  'P2.volume-parameters.image-interpolation3d': async () => {
    await mode('mip');
    const current = await label(`3D interpolation ${LAYER}`).inputValue();
    return changed(await choose(`3D interpolation ${LAYER}`, current === 'nearest' ? 'linear' : 'nearest'), '3D interpolation');
  },
  'P2.camera.camera-angles': () => camera('Camera angle horizontal', 25),
  'P2.camera.camera-center': () => camera('Camera center horizontal', 30),
  'P2.camera.camera-zoom': () => camera('Camera zoom', 18),
  'P2.camera.camera-perspective': () => camera('Camera perspective', 40),
  'P2.camera.camera-orientation': () => direction('Vertical direction', 'down'),
  'P2.camera.api-depthaxisorientation-away': () => direction('Depth direction', 'away'),
  'P2.camera.api-depthaxisorientation-towards': () => direction('Depth direction', 'towards'),
  'P2.camera.api-verticalaxisorientation-up': () => direction('Vertical direction', 'up'),
  'P2.camera.api-verticalaxisorientation-down': () => direction('Vertical direction', 'down'),
  'P2.camera.api-horizontalaxisorientation-left': () => direction('Horizontal direction', 'left'),
  'P2.camera.api-horizontalaxisorientation-right': () => direction('Horizontal direction', 'right'),
  'P2.clipping-planes.volumedepiction-plane': () => depiction('plane'),
  'P2.clipping-planes.volumedepiction-volume': () => depiction('volume'),
  'P2.clipping-planes.scalarfieldbase-depiction': () => depiction('plane'),
  'P2.clipping-planes.plane-position': () => plane(['Plane position depth'], [7]),
  'P2.clipping-planes.plane-normal': () => plane(['Plane normal depth', 'Plane normal horizontal'], [0, 1]),
  'P2.clipping-planes.scalarfieldbase-plane': () => plane(['Plane position depth', 'Plane normal depth', 'Plane normal horizontal'], [4, 1, 0]),
  'P2.clipping-planes.slicing-slicingplane-thickness': () => plane(['Plane thickness'], [5]),
  'P2.clipping-planes.clipping-clippingplane-enabled': () => clipping(1),
  'P2.clipping-planes.layer-experimental-clipping-planes': () => clipping(1),
};

// The retained surface (H05 surface): the Surface display controls, one recipe per child.
const SHADINGS = ['none', 'flat', 'smooth'];  // Shading, pinned

async function shading(value) {
  const name = `Shading ${MESH}`;
  if ((await label(name).inputValue()) === value) await choose(name, value === 'flat' ? 'smooth' : 'flat');
  return changed(await choose(name, value), `Shading ${value}`);
}

async function section(name) {
  // Wireframe and normals settings sit in a collapsed section: open it the way a person does, by its summary.
  const details = label(name).locator('xpath=ancestor::details[1]');
  if ((await details.getAttribute('open')) === null) await details.locator('summary').click();
}

async function line(prefix, settings) {
  // Fill a wireframe or normals line (Show, width, length, RGBA) and apply; every value must come back from the worker.
  const names = Object.keys(settings).map(key => key === 'visible' ? `Show ${prefix.toLowerCase()} ${MESH}` : `${prefix} ${key} ${MESH}`);
  await section(names[0]);
  let edited = false;
  for (const [index, [key, value]] of Object.entries(settings).entries()) {
    const field = label(names[index]);
    if (key === 'visible' ? (await field.isChecked()) === value : Number(await field.inputValue()) === value) continue;
    if (key === 'visible') await field[value ? 'check' : 'uncheck']();
    else await field.fill(String(value));
    touched.add(names[index]);
    edited = true;
  }
  const now = await pixels();
  const frames = edited ? await apply() : { before: now, after: now };
  for (const [index, [key, value]] of Object.entries(settings).entries()) {
    const shown = key === 'visible' ? await label(names[index]).isChecked() : Number(await label(names[index]).inputValue());
    if (key === 'visible' ? shown !== value : Math.abs(shown - value) > 1e-6) throw new Error(`${names[index]} shows ${shown} after applying ${value}`);
  }
  return frames;
}

async function shown(prefix, extra = {}) {
  // From hidden to shown: the line appears in the render; hiding it again removes it.
  await line(prefix, { visible: false });
  const frames = await line(prefix, { visible: true, ...extra });
  changed(frames, `Show ${prefix}`);
  const hidden = await line(prefix, { visible: false });
  if (hidden.after === frames.after) throw new Error(`hiding ${prefix} did not change the preview`);
  return { shown: true, hidden: true };
}

async function varied(prefix, settings) {
  await line(prefix, { visible: true, width: 2, ...(prefix === 'Wireframe' ? {} : { length: 4 }), red: 1, green: 1, blue: 1, alpha: 1 });
  return changed(await line(prefix, settings), `${prefix} ${Object.keys(settings).join(', ')}`);
}

async function frameSurfaceNormals() {
  // The saved output opens with its own default camera, not the source session's
  // camera. Frame the mesh plus its longest normals through ordinary controls.
  // These setup transactions remain in ops after the initial checkpoint, so the
  // independent reference must replay them rather than assume matching state.
  const camera = {'Camera angle depth':15, 'Camera angle vertical':30, 'Camera angle horizontal':60, 'Camera zoom':6};
  for (const [name, value] of Object.entries(camera)) {
    await label(name).fill(String(value));
    touched.add(name);
  }
  await apply();
  for (const [name, value] of Object.entries(camera)) {
    if (Number(await label(name).inputValue()) !== value) throw new Error(`${name} did not retain its normal-observation framing`);
  }
  // Black lines against the black background are not a visibility oracle.
  // Establish each contrasting baseline visibly, then leave it hidden. This
  // also avoids waiting for a pixel change while only editing hidden settings.
  // The recipes below still prove appearance/disappearance, length, width and color.
  for (const prefix of ['Face normals', 'Vertex normals']) {
    changed(await line(prefix, {visible:true, width:2, length:6, red:1, green:1, blue:1, alpha:1}), `Baseline ${prefix}`);
    changed(await line(prefix, {visible:false}), `Hide baseline ${prefix}`);
  }
}

const SURFACE_RECIPES = {
  ...Object.fromEntries(SHADINGS.map(value => [`P2.surface-render.shading-${value}`, () => shading(value)])),
  'P2.surface-render.surface-shading': () => shading('smooth'),
  'P2.surface-render.qtshadingcomboboxcontrol': async () => {
    // The control offers every pinned shading, and each one renders the mesh differently.
    const offered = await options(`Shading ${MESH}`);
    if (JSON.stringify(offered) !== JSON.stringify(SHADINGS)) throw new Error(`Shading offers ${offered}`);
    const renders = {};
    for (const value of SHADINGS) { await shading(value); renders[value] = await pixels(); }
    if (new Set(Object.values(renders)).size !== SHADINGS.length) throw new Error(`renders per shading: ${JSON.stringify(renders)}`);
    return { offered };
  },
  'P2.surface-render.surface-wireframe': () => shown('Wireframe', { width: 2 }),
  'P2.surface-render.wireframe-surfacewireframe-visible': () => shown('Wireframe'),
  'P2.surface-render.wireframe-surfacewireframe-width': () => varied('Wireframe', { width: 6 }),
  'P2.surface-render.wireframe-surfacewireframe-color': () => varied('Wireframe', { green: 0, blue: 0 }),
  'P2.surface-render.surface-normals': () => shown('Face normals', { length: 6 }),
  'P2.surface-render.surface-normals-surfacenormals-face': () => shown('Face normals', { length: 6 }),
  'P2.surface-render.surface-normals-surfacenormals-vertex': () => shown('Vertex normals', { length: 6 }),
  'P2.surface-render.normals-normals-visible': () => shown('Vertex normals'),
  'P2.surface-render.normals-normals-length': () => varied('Face normals', { length: 12 }),
  'P2.surface-render.normals-normals-width': () => varied('Face normals', { width: 6 }),
  'P2.surface-render.normals-normals-color': () => varied('Face normals', { red: 0, blue: 0 }),
};
const SETS = { 'H05 volume': RECIPES, 'H05 volume (negative)': RECIPES, 'H05 surface': SURFACE_RECIPES };
const chosen = SETS[title];
if (!chosen) throw new Error(`no recipes for the document ${title}`);

const NAMES = title === 'H05 surface' ? [MESH] : [LAYER];
const opened = {};
let initial = null, runtime = null, guiProfile = null, sessionRequested = false, cleanup = null, canvasSize = null, ownedReview = null;
async function checkpoint(id) {
  checkpoints.push({ id, ops: ops.length,
                     state: await readConsoleJSON(python, `snapshot(${JSON.stringify(NAMES)}, viewer)`) });
}
try {
  await page.goto(url);
  await page.waitForTimeout(3000);
  // Opening from Data also raises a restored window; merely finding its old Close button does not foreground it.
  // The rail's Data is always in view; a result is one row of Data whose button is "Open <title>" (workspace-feel step 4).
  await navigationButton('Data').click();
  const opener = navigationButton(`Open ${title}`);
  const card = page.locator('.study-result-card').filter({has:opener});
  await card.getByRole('button', {name:`Open ${title}`, exact:true}).click({timeout:60000});
  // The row opens in the Data reader on the right (ticket 66); the scientific viewer is its full tool, in the reader's ⋯.
  await page.locator('.data-reader-side .rd-more').click({timeout:60000});
  await page.locator('.mm-drawer-menu button', {hasText:'Open the scientific viewer'}).click({timeout:10000});
  await document.root.waitFor({state:'visible', timeout:60000});
  await document.bind();
  opened.from = 'Project Data';
  await until(async () => await button('Open session').isEnabled({timeout:500}).catch(() => false), 60, 'Open session');
  const resource = JSON.parse(await document.root.getAttribute('data-study-retained')).resource;
  const opening = page.waitForResponse(response => {
    const request = response.request(), path = new URL(request.url()).pathname;
    if (request.method() !== 'POST' || !/^\/api\/scientific-reviews(?:\/[A-Za-z0-9_-]+\/attach)?$/.test(path)) return false;
    const body = request.postDataJSON();
    return body?.document === resource.id && body.session === resource.day && body.revision === resource.version;
  }, {timeout:240000});
  sessionRequested = true;
  const [response] = await Promise.all([opening, button('Open session').click()]);
  const openedReply = await response.json();
  if (!response.ok() || openedReply.ok !== true || typeof openedReply.review !== 'string'
      || !/^[A-Za-z0-9_-]{16,128}$/.test(openedReply.review)) throw new Error('Owned scientific session identity missing');
  ownedReview = openedReply.review;
  opened.review = ownedReview; opened.resource = resource;
  await until(async () => await button('Refresh preview').isEnabled({timeout:500}).catch(() => false), 240, 'the confined session');
  await button('Refresh preview').click();
  await until(async () => (await pixels()) !== null, 60, 'the first preview');
  canvasSize = await document.preview().evaluate(canvas => [canvas.height, canvas.width]);
  await document.tab('View').click();
  if ((await label('Displayed dimensions').inputValue()) !== '3') await choose('Displayed dimensions', '3');
  opened.three = true;
  // The compared state comes from the session itself, through its own Python console.
  await python(readFileSync(recipesPath, 'utf8') + '\nimport json as _json\nprint("ready")');
  runtime = await readConsoleJSON(python, `runtime(${JSON.stringify(JSON.parse(readFileSync(sourcePaths, 'utf8')))})`);
  guiProfile = await readConsoleJSON(python, `comparison_profile(viewer, '${createHash('sha256').update(readFileSync(recipesPath)).digest('hex')}')`);
  initial = await readConsoleJSON(python, `initial(${JSON.stringify(NAMES)}, viewer, ${JSON.stringify(title === 'H05 surface' ? NAMES : [])})`, {raw:true});
  ops.length = 0;
  await checkpoint('initial');
  if (title === 'H05 surface') await frameSurfaceNormals();
  if (inject) await python(readFileSync(inject, 'utf8') + '\nprint("injected")');  // a negative: Hooke's path broken
} catch (error) {
  opened.error = String(error).slice(0, 1000);
}
const results = {};
for (const [id, recipe] of Object.entries(chosen).filter(([id]) => !only || only === '-' || only.split(',').includes(id))) {
  if (opened.error) { results[id] = { ok: false, error: `the view did not open: ${opened.error}` }; continue; }
  try {
    results[id] = { ok: true, detail: await recipe() };
    await checkpoint(id);
  } catch (error) {
    results[id] = { ok: false, error: String(error).slice(0, 1000) };
    try { await checkpoint(id); } catch { /* the state could not be read: the failure above is the evidence */ }
  }
}
opened.pageErrors = errors;
try {
  cleanup = sessionRequested ? await stopScientificSession(document, until) : {ok:true, stopped:false};
} catch (error) {
  cleanup = {ok:false, error:String(error)};
} finally {
  await context.close();
}
process.stdout.write(JSON.stringify({ opened, recipes: Object.keys(chosen).sort(), results, initial, ops, transactions, checkpoints, runtime, profile:guiProfile, cleanup, canvasSize }));
