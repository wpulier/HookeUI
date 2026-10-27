// The redesign's tokens (slice 0, design/studio/handoff/tests/tokens.test.ts, ported): every text colour reads on
// every surface it sits on (WCAG AA, 4.5:1), read from style.css's :root itself, tints composited before measuring.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const STYLE = fileURLToPath(new URL('./style.css', import.meta.url));
const css = readFileSync(STYLE, 'utf8');
// Only :root's tokens count: the first block of the file.
const root = /:root\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';

/** A token's colour as [r, g, b, alpha], from #rrggbb or rgb(r g b / a%). */
function colour(name: string): [number, number, number, number] {
  const match = new RegExp(`--${name}:\\s*([^;]+);`).exec(root);
  if (!match) throw new Error(`--${name} is not in style.css :root`);
  const value = match[1].trim();
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16)).concat(1) as [number, number, number, number];
  const rgb = /^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+)%)?\s*\)$/.exec(value);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), rgb[4] ? Number(rgb[4]) / 100 : 1];
  throw new Error(`--${name} is not a plain colour: ${value}`);
}
const over = (top: number[], bottom: number[]) => top.slice(0, 3).map((c, i) => c * top[3] + bottom[i] * (1 - top[3]));
const luminance = (rgb: number[]) => {
  const [r, g, b] = rgb.map((c) => { const v = c / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a: number[], b: number[]) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
const white = [255, 255, 255, 1];

describe('slice 0 · text contrast on every surface', () => {
  const surfaces = ['mm-page', 'mm-bar', 'mm-card', 'mm-raise', 'mm-panel', 'mm-panel-solid'];
  for (const text of ['mm-text', 'mm-text-2', 'mm-text-3']) {
    for (const surface of surfaces) {
      it(`${text} on ${surface} reaches 4.5:1`, () => {
        expect(contrast(colour(text), colour(surface))).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
  it('a button\'s words on its soft fill over the bar', () => {
    const fill = over(colour('mm-ctl-fill'), colour('mm-bar'));
    expect(contrast(colour('mm-ctl-text'), fill)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(colour('mm-ctl-text-2'), over(colour('mm-seg-track'), colour('mm-bar')))).toBeGreaterThanOrEqual(4.5);
  });
  it('a chosen option on its raised segment', () => {
    expect(contrast(white, colour('mm-seg-on'))).toBeGreaterThanOrEqual(4.5);
  });
  it('a switched-on tool: the accent\'s words on the accent tint', () => {
    const tint = over(colour('mm-accent-soft'), over(colour('mm-ctl-fill'), colour('mm-bar')));
    expect(contrast(colour('mm-accent-on-soft'), tint)).toBeGreaterThanOrEqual(4.5);
  });
  it('white on the filled primary and on the recording capsule', () => {
    expect(contrast(white, colour('mm-accent-fill'))).toBeGreaterThanOrEqual(4.5);
    expect(contrast(white, colour('mm-safranin-fill'))).toBeGreaterThanOrEqual(4.5);
  });
});

describe('slice 4 · overlays are solid', () => {
  it('no overlay or menu surface is see-through', () => {
    for (const name of ['mm-panel', 'mm-panel-solid', 'mm-hud']) expect(colour(name)[3]).toBe(1);
  });
  it('no frosted glass', () => {
    expect(/--mm-blur:\s*none\s*;/.test(root)).toBe(true);
  });
});

describe('slice 0 · the production names still resolve', () => {
  // Rules not yet ported use these; each must alias a token that :root defines (handoff traps: keep the aliases).
  const aliases = ['ink', 'glass', 'glass-edge', 'paper', 'muted', 'eosin', 'iodine', 'methylene', 'fast-green', 'safranin',
    'crystal-violet', 'toluidine', 'sans', 'annotation', 'line'];
  for (const name of aliases) {
    it(`--${name} aliases a defined --mm-* token`, () => {
      const value = new RegExp(`--${name}:\\s*var\\(--(mm-[\\w-]+)\\)\\s*;`).exec(root);
      expect(value, `--${name} is not an alias of an --mm-* token`).not.toBeNull();
      expect(root).toMatch(new RegExp(`--${value![1]}:`));
    });
  }
  it('no rule outside :root sets text in a raw hex colour (white words on a fill excepted)', () => {
    for (const file of ['./style.css', './workspace-windows.css']) {
      const text = readFileSync(fileURLToPath(new URL(file, import.meta.url)), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      const rules = file === './style.css' ? text.slice(text.indexOf('}', text.indexOf(':root')) + 1) : text;
      const raw = [...rules.matchAll(/(?:^|[;{\s])color:\s*(#[0-9a-f]{3,8})\b/gi)].map((m) => m[1].toLowerCase());
      expect(raw.filter((hex) => hex !== '#fff' && hex !== '#ffffff'), file).toEqual([]);
    }
  });
  it('no rule in the app\'s CSS uses backdrop-filter (surfaces are solid)', () => {
    for (const file of ['./style.css', './workspace-windows.css']) {
      const rules = readFileSync(fileURLToPath(new URL(file, import.meta.url)), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      expect(rules, file).not.toMatch(/backdrop-filter\s*:/);
    }
  });
});

// The workspace's palette (workspace-theme.css), the workspace-feel mock's (design/workspace-feel/): each theme is
// style.css's :root, then the workspace block, then the theme's own block, var() references resolved as the browser does.
const THEME_CSS = readFileSync(fileURLToPath(new URL('./workspace-theme.css', import.meta.url)), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const declarations = (body: string) => new Map([...body.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
const block = (selector: string) => {
  const match = new RegExp(`${selector.replace(/[[\]'()]/g, '\\$&')}\\s*\\{([^}]*)\\}`).exec(THEME_CSS);
  if (!match) throw new Error(`${selector} is not in workspace-theme.css`);
  return declarations(match[1]);
};
const THEMES: Record<string, Map<string, string>> = {
  dark: new Map([...declarations(root), ...block(':root[data-workspace-theme]')]),
  black: new Map([...declarations(root), ...block(':root[data-workspace-theme]'), ...block(":root[data-workspace-theme='black']")]),
  light: new Map([...declarations(root), ...block(':root[data-workspace-theme]'), ...block(":root[data-workspace-theme='light']")]),
};
function themed(theme: string, name: string, depth = 0): string {
  const value = THEMES[theme].get(name);
  if (value === undefined) throw new Error(`--${name} is not defined for the ${theme} workspace theme`);
  const alias = /^var\(--([\w-]+)\)$/.exec(value);
  return alias && depth < 8 ? themed(theme, alias[1], depth + 1) : value;
}
function themedColour(theme: string, name: string): [number, number, number, number] {
  const value = themed(theme, name);
  const hex = /^#([0-9a-f]{6})$/i.exec(value);
  if (hex) return [0, 2, 4].map((i) => parseInt(hex[1].slice(i, i + 2), 16)).concat(1) as [number, number, number, number];
  const rgb = /^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+)%)?\s*\)$/.exec(value);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), rgb[4] ? Number(rgb[4]) / 100 : 1];
  throw new Error(`--${name} (${theme}) is not a plain colour: ${value}`);
}

describe('workspace feel · the workspace takes the mock\'s palette', () => {
  it('surfaces, text and the lit cell\'s colours are the mock\'s, in dark and light', () => {
    const expected = {
      dark: { 'mm-page': '#141416', 'mm-bar': '#19191c', 'mm-card': '#222226', 'mm-raise': '#2a2a2f', 'mm-hud': '#19191c',
        'mm-text': '#ededf0', 'mm-text-2': '#a7a7b0', 'mm-accent-fill': '#2f6fd6', 'mm-alive': '#ffd28a', 'mm-ready': '#4cc987', 'mm-attention': '#ff6f66' },
      light: { 'mm-page': '#f3f3f5', 'mm-bar': '#ffffff', 'mm-card': '#f0f0f3', 'mm-raise': '#e7e7eb', 'mm-hud': '#ffffff',
        'mm-text': '#17171a', 'mm-text-2': '#50505a', 'mm-text-3': '#64646d', 'mm-accent-fill': '#2563d9', 'mm-alive': '#d18c0c', 'mm-ready': '#18914f', 'mm-attention': '#cf3a31' },
    } as const;
    for (const [theme, tokens] of Object.entries(expected)) {
      for (const [name, value] of Object.entries(tokens)) expect(themed(theme, name), `${theme} --${name}`).toBe(value);
    }
    expect(themed('dark', 'mm-line')).toBe('rgb(255 255 255 / 7%)');
    expect(themed('dark', 'mm-line-strong')).toBe('rgb(255 255 255 / 11%)');
  });
  it('radii by tier and the motion', () => {
    for (const theme of Object.keys(THEMES)) {
      expect(['mm-r-control', 'mm-r-tab', 'mm-r-card', 'mm-r-sheet', 'mm-r-pill'].map((name) => themed(theme, name)))
        .toEqual(['6px', '8px 8px 0 0', '10px', '14px', '999px']);
      expect(['mm-fast', 'mm-base', 'mm-reveal'].map((name) => themed(theme, name))).toEqual(['120ms', '200ms', '320ms']);
      expect(themed(theme, 'mm-ease')).toBe('cubic-bezier(0.2, 0.8, 0.2, 1)');
    }
  });
  it('the Stage is a true-black eyepiece in every theme', () => {
    expect(THEME_CSS).toMatch(/:root\[data-workspace-theme\] #stage\{--mm-well:#000000\}/);
  });
});

describe('workspace feel · text contrast on every workspace surface', () => {
  const surfaces = ['mm-page', 'mm-bar', 'mm-card', 'mm-raise', 'mm-hud', 'mm-panel', 'mm-panel-solid'];
  for (const theme of Object.keys(THEMES)) {
    for (const text of ['mm-text', 'mm-text-2', 'mm-text-3', 'mm-accent']) {
      it(`${theme}: ${text} reaches 4.5:1 on every surface (text-3 on the panel included)`, () => {
        for (const surface of surfaces) {
          expect(contrast(themedColour(theme, text), themedColour(theme, surface)), `${text} on ${surface}`).toBeGreaterThanOrEqual(4.5);
        }
      });
    }
    it(`${theme}: words in a role colour, white on the filled primary, the accent on its tint`, () => {
      for (const role of ['mm-green', 'mm-safranin']) {
        for (const surface of ['mm-page', 'mm-bar', 'mm-card']) {
          expect(contrast(themedColour(theme, role), themedColour(theme, surface)), `${role} on ${surface}`).toBeGreaterThanOrEqual(4.5);
        }
      }
      expect(contrast(white, themedColour(theme, 'mm-accent-fill'))).toBeGreaterThanOrEqual(4.5);
      const tint = over(themedColour(theme, 'mm-accent-soft'), themedColour(theme, 'mm-bar'));
      expect(contrast(themedColour(theme, 'mm-accent-on-soft'), tint)).toBeGreaterThanOrEqual(4.5);
    });
    it(`${theme}: floating surfaces are solid`, () => {
      for (const name of ['mm-hud', 'mm-panel', 'mm-panel-solid', 'mm-raise']) expect(themedColour(theme, name)[3]).toBe(1);
    });
  }
  // The lit cell is a graphic beside its words (1.4.11: 3:1). Green, red and the accent's needs-you reach it everywhere;
  // gold does in the dark themes. Light gold (#d18c0c, the plan's choice) is 2.8:1 on white, so a working cell never
  // stands alone: its words (Working 12 s, Setting up) say the state.
  it('the lit cell\'s colours read as graphics on the panels they sit on', () => {
    for (const theme of Object.keys(THEMES)) {
      const states = theme === 'light' ? ['mm-ready', 'mm-attention', 'mm-accent'] : ['mm-alive', 'mm-ready', 'mm-attention', 'mm-accent'];
      for (const state of states) {
        for (const surface of ['mm-page', 'mm-bar', 'mm-card']) {
          expect(contrast(themedColour(theme, state), themedColour(theme, surface)), `${theme} ${state} on ${surface}`).toBeGreaterThanOrEqual(3);
        }
      }
    }
  });
});
