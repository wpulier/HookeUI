import type { CapabilityEntryIntent } from './capability-entry';

export interface CapabilityFlowContext {
  route: 'find' | 'add' | 'create';
  model: string;
  role: string;
  option: string;
  session: string;
  recipe?: string;
  source?: string;
  candidate?: string;
  input?: string;
  draft?: string;
  comparison?: string;
  workspace?: string;
  values?: Partial<CapabilityEntryIntent>;
}

const KEY = 'capability-flow';
const REFERENCES = ['model', 'role', 'option', 'session', 'recipe', 'source', 'candidate', 'input', 'draft', 'comparison', 'workspace'] as const;

export function readFlow(storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>): CapabilityFlowContext | null {
  try {
    const value = JSON.parse((storage ?? globalThis.sessionStorage)?.getItem(KEY) ?? 'null');
    if (!value || !['find', 'add', 'create'].includes(value.route)) return null;
    if (!['model', 'role', 'option', 'session'].every(k => typeof value[k] === 'string' && value[k].length <= 4096)) return null;
    const context = { route: value.route } as CapabilityFlowContext;
    for (const key of REFERENCES) if (typeof value[key] === 'string' && value[key].length <= 4096) context[key] = value[key];
    context.values = entryValues(value.values);
    if (!context.values) delete context.values;
    return context;
  } catch { return null; }
}

export function saveFlow(context: CapabilityFlowContext | null, storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>): void {
  try {
    const target = storage ?? globalThis.sessionStorage;
    if (!context) { target?.removeItem(KEY); return; }
    const clean: Record<string, unknown> = { route: context.route };
    for (const key of REFERENCES) if (context[key] !== undefined) clean[key] = context[key]!;
    const values = entryValues(context.values);
    if (values) clean.values = values;
    target?.setItem(KEY, JSON.stringify(clean));
  } catch { /* Browser storage can be unavailable; the open task still works. */ }
}

function entryValues(value: unknown): Partial<CapabilityEntryIntent> | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const input = value as Record<string, unknown>;
  const clean: Record<string, string> = {};
  for (const key of ['query', 'source', 'description'])
    if (typeof input[key] === 'string' && input[key].length <= (key === 'description' ? 4000 : 2048)) clean[key] = input[key];
  const choices = { route: ['find', 'add', 'create'], job: ['find', 'name', 'measure', 'clear'],
    method: ['builder', 'coder'], runner: ['python-env', 'fiji'],
    output: ['table.objects', 'image.2d.rgb8', 'points.2d', 'text', 'labels.instance.2d'] };
  for (const [key, options] of Object.entries(choices))
    if (typeof input[key] === 'string' && options.includes(input[key])) clean[key] = input[key];
  return clean;
}
