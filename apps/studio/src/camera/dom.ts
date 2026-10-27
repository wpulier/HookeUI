// A small element builder for the camera's views (no framework: the camera page loads on its own, from the bridge).

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & Record<string, unknown> = {},
  ...children: (Node | string)[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key.startsWith('data-') || key.startsWith('aria-') || key === 'role' || key === 'for') node.setAttribute(key, String(value));
    else (node as unknown as Record<string, unknown>)[key] = value;
  }
  node.append(...children);
  return node;
}

/** A pill that is on or off (aria-pressed), or a one-off action when `pressed` is undefined. */
export function chip(label: string, title = '', pressed?: boolean): HTMLButtonElement {
  const button = el('button', { type: 'button', className: 'cam-chip', textContent: label, title });
  if (pressed !== undefined) button.setAttribute('aria-pressed', String(pressed));
  return button;
}

export function press(button: HTMLElement, on: boolean): void {
  button.setAttribute('aria-pressed', String(on));
}
