import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig, searchForWorkspaceRoot } from 'vite';

const orchestrator = process.env.ORCHESTRATOR_URL ?? 'http://127.0.0.1:8770';
// The numbers the studio shares with the server and the sim harness (src/overlay.ts imports it).
const contract = fileURLToPath(new URL('../../contract', import.meta.url));
const services = fileURLToPath(new URL('../../services', import.meta.url));
const build = JSON.parse(execFileSync(`${services}/.venv/bin/python`, ['-m', 'orchestrator.build_info'], { cwd: services, encoding: 'utf8' }));
// An official builder may supply its own mark for the menu button (HOOKEUI_MARK_DARK, HOOKEUI_MARK_LIGHT: SVG files),
// as it supplies its own app icon; otherwise the core's mark (src/assets/core-mark-*.svg). The source stays unchanged.
const suppliedMark: Record<string, string | undefined> = { dark: process.env.HOOKEUI_MARK_DARK, light: process.env.HOOKEUI_MARK_LIGHT };
for (const file of Object.values(suppliedMark)) {
  if (file && !existsSync(file)) throw new Error(`The supplied mark is missing: ${file}`);
}

export default defineConfig({
  define: { __MICROMIND_BUILD__: JSON.stringify(build) },
  plugins: [{ name: 'micromind-build-identity', generateBundle() {
    this.emitFile({ type: 'asset', fileName: 'build.json', source: JSON.stringify(build) });
  } }, { name: 'hookeui-supplied-mark', enforce: 'pre', resolveId(source) {
    const tone = /(?:^|\/)assets\/core-mark-(dark|light)\.svg$/.exec(source)?.[1];
    const file = tone ? suppliedMark[tone] : undefined;
    return file ? resolve(file) : null;
  } }],
  build: {
    rolldownOptions: { input: { studio: 'index.html', workspace: 'workspace.html', study: 'workspace-study.html' } },
  },
  server: {
    port: 5180,
    strictPort: true,
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), contract] },
    proxy: {
      '/api': orchestrator,
      '/research': orchestrator, // researchers' pictures, videos and lessons
      '/ws': { target: orchestrator.replace(/^http/, 'ws'), ws: true },
    },
  },
});
