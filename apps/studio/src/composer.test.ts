// The composer (composer.ts; researcher-spec.md section 6): one component for the Assistant and every researcher. The
// chip line only with something in it; a field from two lines to eight where Enter sends and Shift+Enter starts a line;
// then +, the model chip, the extra controls (Talk) and Send, grey with nothing to send and blue with words.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { Composer, FIELD_MAX, FIELD_MIN, fieldHeight, sendsOnEnter } from './composer';
import { syntheticDOM, TestElement, fire } from './workspace-windows.test-support';

let dom: ReturnType<typeof syntheticDOM>;
beforeEach(() => { dom = syntheticDOM(); vi.stubGlobal('document', dom.document); });
afterEach(() => vi.unstubAllGlobals());

class Form extends TestElement {
  submitted: unknown[] = [];
  requestSubmit(submitter?: unknown) { this.submitted.push(submitter); }
}
const key = (fields: Partial<KeyboardEvent> = {}) => ({ key: 'Enter', shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, isComposing: false, ...fields });

function assistant(open = vi.fn()) {
  const form = new Form('form'), talk = new TestElement('button'); talk.className = 'talk';
  const composer = new Composer(form as unknown as HTMLFormElement, { fieldId: 'question', label: 'Message to the Assistant', placeholder: 'Ask anything',
    attach: { label: 'Add a specimen', items: () => [{ label: 'Choose from Data…', act: () => {} }, { label: 'Open a picture or video…', act: () => {} }] },
    model: { words: 'Gemini 3.8 Flash', open }, extra: [talk as unknown as HTMLElement] });
  return { form, talk, composer, field: composer.field as unknown as TestElement & { value: string; scrollHeight?: number } };
}

describe('the composer', () => {
  test('its parts in order, the field named and the chip line hidden while empty (no form title, no hint in the box)', () => {
    const { form, talk, composer } = assistant();
    expect(form.children.map((n) => n.className)).toEqual(['mm-composer-chips', 'mm-composer-field', 'mm-composer-actions']);
    expect(form.className).toContain('mm-composer');
    expect(composer.chips.hidden).toBe(true);
    expect(composer.field.id).toBe('question');
    expect(composer.field.placeholder).toBe('Ask anything');
    expect(composer.field.getAttribute('aria-label')).toBe('Message to the Assistant');
    const actions = (composer.actions as unknown as TestElement).children;
    expect(actions.map((n) => n.className)).toEqual(['mm-composer-icon mm-composer-attach', 'mm-composer-model', 'mm-composer-spacer', 'talk', 'mm-composer-send']);
    expect(actions[3]).toBe(talk);
    expect(composer.send.type).toBe('submit');
    expect(composer.send.getAttribute('aria-label')).toBe('Send');
  });

  test('Send is grey with nothing to send and blue with words; set() and typing both say so', () => {
    const { composer, field } = assistant();
    expect(composer.send.getAttribute('aria-disabled')).toBe('true');
    field.value = 'What is this?'; fire(field, 'input');
    expect(composer.send.getAttribute('aria-disabled')).toBe('false');
    composer.set('   ');
    expect(composer.send.getAttribute('aria-disabled')).toBe('true');
    composer.set('Start a researcher to ');
    expect(composer.field.value).toBe('Start a researcher to ');
    expect(composer.root.dataset.empty).toBe('false');
  });

  test('a draft written from elsewhere (Help\'s Ask the Assistant, a researcher draft) turns Send blue and sizes the field', async () => {
    const { composer, field } = assistant();
    Object.defineProperty(field, 'scrollHeight', { value: 100, configurable: true });
    composer.set('Count the cells in this view');
    expect(composer.send.getAttribute('aria-disabled')).toBe('false');
    expect(composer.root.dataset.empty).toBe('false');
    expect(composer.field.style.height).toBe('100px');
    // Every programmatic draft goes through set(): a bare write to the field would leave Send grey while it holds words.
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    for (const file of ['./workspace-study.ts', './main.ts']) {
      const source = readFileSync(fileURLToPath(new URL(file, import.meta.url)), 'utf8');
      expect(source, file).not.toMatch(/\bquestion\.value\s*=(?!=)/);
    }
  });

  test('Enter sends through the form, Shift+Enter and a composing input method do not, and a request being answered keeps the words', () => {
    const { form, composer, field } = assistant();
    composer.set('What is this?');
    fire(field, 'keydown', key());
    expect(form.submitted).toEqual([composer.send]);
    fire(field, 'keydown', key({ shiftKey: true }));
    fire(field, 'keydown', key({ isComposing: true }));
    expect(form.submitted).toHaveLength(1);
    composer.send.disabled = true; // still answering the last one
    fire(field, 'keydown', key());
    expect(form.submitted).toHaveLength(1);
    composer.send.disabled = false; composer.set('');
    fire(field, 'keydown', key());
    expect(form.submitted).toHaveLength(1);
    expect(sendsOnEnter(key({ metaKey: true }))).toBe(false);
    expect(sendsOnEnter({ ...key(), keyCode: 229 })).toBe(false);
  });

  test('the field grows from two lines to eight, then scrolls inside', () => {
    const { composer, field } = assistant();
    for (const [content, height, overflow] of [[10, FIELD_MIN, 'hidden'], [100, 100, 'hidden'], [400, FIELD_MAX, 'auto']] as const) {
      Object.defineProperty(field, 'scrollHeight', { value: content, configurable: true });
      composer.refresh();
      expect(composer.field.style.height).toBe(`${height}px`);
      expect(composer.field.style.overflowY).toBe(overflow);
    }
    expect(fieldHeight(Number.NaN)).toBe(FIELD_MIN);
  });

  test('the chip line shows only with something in it; the model chip says its words and opens its control', () => {
    const open = vi.fn();
    const { composer } = assistant(open);
    const chip = new TestElement('button'); chip.textContent = 'Human cells';
    composer.setChips(chip as unknown as Node, 'answers go to Main');
    expect(composer.chips.hidden).toBe(false);
    expect(composer.chips.textContent).toBe('Human cellsanswers go to Main');
    composer.setChips();
    expect(composer.chips.hidden).toBe(true);
    expect(composer.modelChip!.textContent).toBe('Gemini 3.8 Flash');
    composer.setModel('Gemini 3.8 Pro', 'The Assistant answers with Gemini 3.8 Pro (cloud)');
    expect(composer.modelChip!.textContent).toBe('Gemini 3.8 Pro');
    expect(composer.modelChip!.title).toBe('The Assistant answers with Gemini 3.8 Pro (cloud)');
    // A model with no key says so on the chip, before any question is asked.
    composer.setModel('Gemini 3.8 Flash', 'Needs your Google Gemini API key. Add it in Models › Keys', 'needs a key');
    expect(composer.modelChip!.textContent).toBe('Gemini 3.8 Flashneeds a key');
    expect(composer.modelChip!.querySelector<HTMLElement>('.mm-composer-model-needs')!.hidden).toBe(false);
    composer.setModel('Gemini 3.8 Pro', 'The Assistant answers with Gemini 3.8 Pro (cloud)');
    expect(composer.modelChip!.querySelector<HTMLElement>('.mm-composer-model-needs')!.hidden).toBe(true);
    fire(composer.modelChip as unknown as TestElement, 'click');
    expect(open).toHaveBeenCalledWith(composer.modelChip);
  });

  test('+ opens the ways to attach above itself; a researcher’s composer has no + and a static model chip', () => {
    const { composer } = assistant();
    fire(composer.attachButton as unknown as TestElement, 'click');
    const menu = dom.host.children.find((n) => n.className.includes('mm-drawer-menu'))!;
    expect(menu.children.map((n) => n.textContent)).toEqual(['Choose from Data…', 'Open a picture or video…']);
    expect(menu.style.bottom).toBeTruthy();
    expect(menu.style.top).toBeUndefined();
    const researcher = new Composer(null, { label: 'Ask Researcher 3 anything', placeholder: 'Ask Researcher 3 anything', model: { words: 'GPT-6.1 Sol' } });
    expect(researcher.attachButton).toBeNull();
    expect(researcher.modelChip!.dataset.static).toBe('true');
    expect(researcher.root.className).toContain('mm-composer');
  });
});
