// A real Chromium in its own fresh profile, driven one JSON line at a time by tests/scientific_browser.py.
// argv: the playwright module (index.mjs), the Chromium executable, the profile folder, the Studio URL.
// Each stdin line is {"op": ..., ...}; each stdout line answers it with {"ok": true, "out": ...} or {"ok": false, "error": ...}.
import { createInterface } from 'node:readline';

const [,, module, executable, profile, url] = process.argv;
const { chromium } = await import(module);
const context = await chromium.launchPersistentContext(profile, {
  executablePath: executable, headless: true, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1,
  colorScheme: 'dark', acceptDownloads: false,
});
const page = context.pages()[0] ?? await context.newPage();
const problems = [];
page.on('pageerror', error => problems.push(`pageerror ${error.message}`.slice(0, 600)));
page.on('console', message => { if (message.type() === 'error') problems.push(`console ${message.text()}`.slice(0, 600)); });

const locate = c => c.role ? page.getByRole(c.role, { name: c.name, exact: c.exact ?? true }).nth(c.nth ?? 0)
  : c.label ? page.getByLabel(c.label, { exact: c.exact ?? true }).nth(c.nth ?? 0)
  : c.text ? page.getByText(c.text, { exact: c.exact ?? false }).nth(c.nth ?? 0)
  : page.locator(c.css).nth(c.nth ?? 0);
const ops = {
  goto: async c => { await page.goto(c.url ?? url); return page.url(); },
  click: async c => { await locate(c).click({ timeout: c.timeout ?? 15000 }); return 'clicked'; },
  fill: async c => { await locate(c).fill(String(c.value), { timeout: c.timeout ?? 15000 }); return 'filled'; },
  type: async c => { await locate(c).pressSequentially(String(c.value), { timeout: c.timeout ?? 15000 }); return 'typed'; },
  select: async c => { await locate(c).selectOption(c.value, { timeout: c.timeout ?? 15000 }); return 'selected'; },
  check: async c => { await locate(c).setChecked(c.value !== false, { timeout: c.timeout ?? 15000 }); return 'set'; },
  press: async c => { if (c.css || c.role || c.label || c.text) await locate(c).press(c.key); else await page.keyboard.press(c.key); return 'pressed'; },
  value: async c => await locate(c).inputValue({ timeout: c.timeout ?? 15000 }),
  checked: async c => await locate(c).isChecked({ timeout: c.timeout ?? 15000 }),
  disabled: async c => await locate(c).isDisabled({ timeout: c.timeout ?? 15000 }),
  waitfor: async c => { await locate(c).waitFor({ state: c.state ?? 'visible', timeout: c.timeout ?? 60000 }); return 'present'; },
  waittext: async c => {
    await page.waitForFunction(([css, text]) => [...document.querySelectorAll(css)].some(e => e.textContent.includes(text)),
      [c.css ?? 'body', c.text], { timeout: c.timeout ?? 60000 });
    return 'present';
  },
  waitcount: async c => {
    await page.waitForFunction(([css, count]) => document.querySelectorAll(css).length > count, [c.css, c.count], { timeout: c.timeout ?? 60000 });
    return 'more';
  },
  text: async c => (await (c.css || c.role || c.label || c.text ? locate(c) : page.locator('body')).innerText({ timeout: c.timeout ?? 15000 })).slice(0, c.max ?? 20000),
  eval: async c => await page.evaluate(c.js),
  shot: async c => { await page.screenshot({ path: c.path }); return c.path; },
  wait: async c => { await page.waitForTimeout(c.ms); return 'waited'; },
  problems: async () => problems.splice(0),
  close: async () => { await context.close(); setTimeout(() => process.exit(0), 20); return 'closed'; },
};
const lines = createInterface({ input: process.stdin });
process.stdout.write(JSON.stringify({ ok: true, out: 'ready' }) + '\n');
for await (const line of lines) {
  let answer;
  try { const c = JSON.parse(line); answer = { ok: true, out: await ops[c.op](c) }; }
  catch (error) { answer = { ok: false, error: String(error).slice(0, 2000) }; }
  process.stdout.write(JSON.stringify(answer) + '\n');
}
await context.close();
