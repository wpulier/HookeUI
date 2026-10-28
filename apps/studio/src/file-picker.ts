/** A file input drawn as the studio's own control (ticket 67: comparing on your picture showed a raw "Choose File / No
 * file chosen"): the input itself, visually hidden and still focusable, inside a label that reads as a button and names
 * the file chosen (modelspage.css `.file-picker`). The comparison picture's and a brain volume's. */
export function filePicker(input: HTMLInputElement, prompt = 'Choose a picture', none = 'No picture chosen'): HTMLElement {
  input.classList.add('visually-hidden');
  const part = (cls: string, text: string) => { const span = document.createElement('span'); span.className = cls; span.textContent = text; return span; };
  const name = part('file-picker-name', input.files?.[0]?.name ?? none);
  input.addEventListener('change', () => { name.textContent = input.files?.[0]?.name ?? none; });
  const label = document.createElement('label');
  label.className = 'file-picker';
  label.append(input, part('mm-control file-picker-button', prompt), name);
  return label;
}
