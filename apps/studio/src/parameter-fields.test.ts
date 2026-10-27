import { expect, test } from 'vitest';
import { parameterError, parameterValues, type Field } from './parameter-fields';
const field: Field = { key: 'size', label: 'Size', kind: 'number', numeric: true, required: false, initial: 5,
  min: 1, max: 10, step: 1, choices: [], said: {}, hint: '' };
test('invalid numeric settings cannot silently become defaults', () => {
  for (const size of ['oops', 'Infinity', 0, 11, 1.5]) expect(parameterError([field], { size })).toContain('Size');
  expect(parameterError([field], { size: '7' })).toBeNull();
  expect(parameterValues([field], { size: '7' })).toEqual({ size: 7 });
  expect(parameterValues([field], {})).toEqual({});
});
test('false and zero survive conversion while an untouched default is omitted', () => {
  const boolean = { ...field, key: 'smooth', kind: 'boolean' as const, initial: true };
  expect(parameterValues([boolean, field], { smooth: false, size: 0 })).toEqual({ smooth: false, size: 0 });
  expect(parameterValues([boolean], {})).toEqual({});
});

test('native badInput does not become an omitted default', async () => {
  const { vi } = await import('vitest');
  const { TestElement, fire } = await import('./workspace-windows.test-support');
  const { parameterControl } = await import('./parameter-fields');
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  try {
    const values: Record<string, unknown> = {};
    const box = parameterControl('test', field, values) as unknown as InstanceType<typeof TestElement>;
    const input = box.querySelector('input')!;
    Object.assign(input, { value: '', validity: { badInput: true } });
    fire(input, 'input');
    expect(parameterError([field], values)).toContain('Size');
  } finally { vi.unstubAllGlobals(); }
});

test('the run page look: the label alone, the default shown in a box with its unit, a slider for a bounded stepped number', async () => {
  const { vi } = await import('vitest');
  const { TestElement, fire } = await import('./workspace-windows.test-support');
  const { parameterControl } = await import('./parameter-fields');
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  try {
    const sigma: Field = { key: 'sigma', label: 'Smoothing', kind: 'number', numeric: true, required: false, initial: 1, min: 0.1, max: 5, step: 0.1, unit: 'px', choices: [], said: {}, hint: 'The blur' };
    const values: Record<string, unknown> = {};
    const box = parameterControl('run', sigma, values, { run: true }) as unknown as InstanceType<typeof TestElement>;
    expect(box.querySelector('label')!.textContent).toBe('Smoothing'); // its unit is beside the value, not in the label
    const [slider, number] = box.querySelectorAll('input');
    expect([slider.getAttribute('type'), number.getAttribute('type')]).toEqual(['range', 'number']);
    expect(number.value).toBe('1.0'); // the declared default, shown to the step's precision…
    expect(values).toEqual({}); // …and not sent until changed: empty sends the default
    expect(box.querySelector('.numbox-unit')!.textContent).toBe('px');
    slider.value = '2.5'; fire(slider, 'input');
    expect(number.value).toBe('2.5');
    expect(parameterValues([sigma], values)).toEqual({ sigma: 2.5 });
  } finally { vi.unstubAllGlobals(); }
});
