// Synthetic DOM/event/decoder fixtures, not a browser layout or pixel oracle.
// No presentation/action/layout implementation lives here. Real modules own all state and handlers.
export class TestElement extends EventTarget {
  readonly tagName: string;
  readonly children: TestElement[] = [];
  readonly dataset: Record<string, string> = {};
  readonly style = Object.assign({} as Record<string, string>, {
    setProperty: (name: string, value: string) => { this.style[name] = value; },
    removeProperty: (name: string) => { const value = this.style[name] ?? ''; delete this.style[name]; return value; },
  });
  readonly attributes: Record<string, string> = {};
  parentElement: TestElement | null = null;
  hidden = false;
  private text = '';
  get textContent(): string { return this.text + this.children.map((child) => child.textContent).join(''); }
  set textContent(value: string) { this.text = value; this.replaceChildren(); }
  className = '';
  title = '';
  id = '';
  value = '';
  disabled = false;
  captured: number | null = null;
  readonly classList: { add(...names: string[]): void; remove(...names: string[]): void; contains(name: string): boolean; toggle(name: string, force?: boolean): boolean } = {
    add: (...names: string[]) => { this.className = [...new Set([...this.className.split(' '), ...names])].filter(Boolean).join(' '); },
    remove: (...names: string[]) => { this.className = this.className.split(' ').filter((v) => !names.includes(v)).join(' '); },
    contains: (name: string) => this.className.split(' ').includes(name),
    toggle: (name: string, force?: boolean) => {
      const add = force ?? !this.classList.contains(name);
      if (add) this.classList.add(name); else this.classList.remove(name);
      return add;
    },
  };
  constructor(tag: string) { super(); this.tagName = tag.toUpperCase(); }
  append(...nodes: (TestElement | string)[]) { for (const value of nodes) { const node = typeof value === 'string' ? Object.assign(new TestElement('#text'), { textContent: value }) : value; node.remove(); node.parentElement = this; this.children.push(node); } }
  appendChild(node: TestElement) { this.append(node); return node; }
  get firstChild(): TestElement | { textContent: string } | null {
    const owner = this;
    return this.text ? { get textContent() { return owner.text; }, set textContent(value: string) { owner.text = value; } } : this.children[0] ?? null;
  }
  insertBefore(node: TestElement, before: TestElement | null) {
    if (!before) return this.appendChild(node);
    if (!this.children.includes(before)) throw new Error("Reference node is not a child");
    node.remove(); node.parentElement = this; this.children.splice(this.children.indexOf(before), 0, node); return node;
  }
  prepend(node: TestElement) { node.remove(); node.parentElement = this; this.children.unshift(node); }
  get lastElementChild(): TestElement | null { return this.children[this.children.length - 1] ?? null; }
  get previousElementSibling(): TestElement | null { const siblings = this.parentElement?.children ?? []; return siblings[siblings.indexOf(this) - 1] ?? null; }
  before(...nodes: TestElement[]) {
    const parent = this.parentElement; if (!parent) return;
    for (const node of nodes) { node.remove(); node.parentElement = parent; parent.children.splice(parent.children.indexOf(this), 0, node); }
  }
  after(...nodes: TestElement[]) {
    const parent = this.parentElement; if (!parent) return;
    let previous: TestElement = this;
    for (const node of nodes) { node.remove(); node.parentElement = parent; parent.children.splice(parent.children.indexOf(previous) + 1, 0, node); previous = node; }
  }
  replaceChildren(...nodes: TestElement[]) { for (const node of [...this.children]) node.remove(); this.append(...nodes); }
  remove() { const parent = this.parentElement; if (parent) parent.children.splice(parent.children.indexOf(this), 1); this.parentElement = null; }
  setAttribute(name: string, value: string) {
    this.attributes[name] = value;
    if (name === 'class') this.className = value;
    if (name === 'id') this.id = value;
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c: string) => c.toUpperCase())] = value;
  }
  getAttribute(name: string) { return this.attributes[name] ?? null; }
  removeAttribute(name: string) { delete this.attributes[name]; }
  matches(selector: string): boolean {
    const qualified = /^([a-z]+)(\[data-[^\]]+\])$/.exec(selector);
    if (qualified) return this.matches(qualified[1]) && this.matches(qualified[2]);
    if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
    const data = /^\[data-([a-z-]+)(?:="([^"]+)")?\]$/.exec(selector);
    if (data) { const key = data[1].replace(/-([a-z])/g, (_, c: string) => c.toUpperCase()); return data[2] === undefined ? key in this.dataset : this.dataset[key] === data[2]; }
    return this.tagName.toLowerCase() === selector;
  }
  closest(selector: string): TestElement | null {
    if (selector.split(',').some((s) => this.matches(s.trim()))) return this;
    return this.parentElement?.closest(selector) ?? null;
  }
  querySelector(selector: string): TestElement | null {
    for (const child of this.children) { if (child.matches(selector)) return child; const inner = child.querySelector(selector); if (inner) return inner; }
    return null;
  }
  querySelectorAll(selector: string): TestElement[] {
    return this.children.flatMap((child) => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  contains(node: TestElement): boolean { return node === this || this.children.some((child) => child.contains(node)); }
  setPointerCapture(id: number) { this.captured = id; }
  releasePointerCapture() { this.captured = null; }
  get offsetParent() { return this.parentElement; }
  private px(name: string, fallback: number): number {
    const value = this.style[name];
    if (value?.endsWith('%')) return (parseFloat(value) / 100) * (name === 'width' ? this.parentElement?.offsetWidth ?? 0 : this.parentElement?.offsetHeight ?? 0);
    const number = parseFloat(value ?? ''); return Number.isFinite(number) ? number : fallback;
  }
  get offsetWidth(): number { return this.px('width', 320); }
  get offsetHeight(): number {
    const aspect = this.style.aspectRatio?.split('/').map(Number);
    return this.px('height', aspect?.length === 2 && aspect[1] > 0 ? this.offsetWidth * aspect[1] / aspect[0] : 240);
  }
  get offsetLeft() { return this.px('left', 0); }
  get offsetTop() { return this.px('top', 0); }
  getBoundingClientRect(): DOMRect {
    const parent = this.parentElement?.getBoundingClientRect();
    const left = (parent?.left ?? 0) + this.offsetLeft, top = (parent?.top ?? 0) + this.offsetTop;
    return { left, top, x: left, y: top, width: this.offsetWidth, height: this.offsetHeight,
      right: left + this.offsetWidth, bottom: top + this.offsetHeight, toJSON: () => ({}) };
  }
}

export class TestImage extends TestElement {
  src = '';
  currentSrc = '';
  naturalWidth = 0;
  naturalHeight = 0;
  complete = false;
  decodeCalls = 0;
  private resolveDecode: (() => void) | null = null;
  private rejectDecode: ((error: Error) => void) | null = null;
  constructor() { super('img'); }
  decode() {
    this.decodeCalls++;
    return new Promise<void>((resolve, reject) => { this.resolveDecode = resolve; this.rejectDecode = reject; });
  }
  captureDecode() {
    const resolve = this.resolveDecode, src = this.src;
    return (width = 148, height = 176) => {
      this.naturalWidth = width; this.naturalHeight = height; this.complete = true; this.currentSrc = src;
      resolve?.();
    };
  }
  finishDecode(width = 148, height = 176) {
    this.captureDecode()(width, height);
  }
  failDecode() { this.rejectDecode?.(new Error('synthetic decode failure')); }
}

export function syntheticDOM() {
  const host = new TestElement('main'); host.style.width = '1200px'; host.style.height = '800px';
  const frames: FrameRequestCallback[] = [];
  const images: TestImage[] = [];
  const document = Object.assign(new EventTarget(), {
    createElement: (tag: string) => { const node = tag === 'img' ? new TestImage() : new TestElement(tag); if (node instanceof TestImage) images.push(node); return node; },
    body: host,
  });
  return { host, document, images, frames,
    raf: (callback: FrameRequestCallback) => { frames.push(callback); return frames.length; },
    frame: () => { for (const callback of frames.splice(0)) callback(16); },
  };
}

export function fire(node: TestElement, type: string, fields: Record<string, unknown> = {}) {
  const event = new Event(type, { cancelable: true });
  for (const [key, value] of Object.entries(fields)) Object.defineProperty(event, key, { value });
  node.dispatchEvent(event);
}

export async function settleMicrotasks() { await Promise.resolve(); await Promise.resolve(); }
