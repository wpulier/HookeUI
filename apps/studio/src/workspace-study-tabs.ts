export interface TabState {
  version: 2;
  order: string[];
  active: string | null;
  recent: string[];
  closed: string[];
}
export const emptyTabs = (): TabState => ({ version: 2, order: [], active: null, recent: [], closed: [] });
export function readTabs(raw: string | null): TabState | null {
  try {
    const value = JSON.parse(raw ?? 'null');
    if (!value || value.version !== 2 || !Array.isArray(value.order)) return null;
    const strings = (items: unknown): string[] => Array.isArray(items) ? [...new Set(items.filter((id): id is string => typeof id === 'string' && /^[\w.-]{1,64}$/.test(id)))] : [];
    const order = strings(value.order);
    return { version: 2, order, active: order.includes(value.active) ? value.active : null, recent: strings(value.recent).filter(id => order.includes(id)), closed: strings(value.closed).filter(id => !order.includes(id)) };
  } catch { return null; }
}
export function activateTab(state: TabState, id: string): TabState {
  return { ...state, order: state.order.includes(id) ? state.order : [...state.order, id], active: id,
    recent: [id, ...state.recent.filter(item => item !== id)], closed: state.closed.filter(item => item !== id) };
}
export function closeTab(state: TabState, id: string): TabState {
  const order = state.order.filter(item => item !== id), recent = state.recent.filter(item => item !== id);
  return { ...state, order, recent, active: state.active === id ? recent.find(item => order.includes(item)) ?? order.at(-1) ?? null : state.active,
    closed: [id, ...state.closed.filter(item => item !== id)] };
}
export function moveTab(state: TabState, id: string, index: number): TabState {
  if (!state.order.includes(id)) return state;
  const order = state.order.filter(item => item !== id);
  order.splice(Math.max(0, Math.min(index, order.length)), 0, id);
  return { ...state, order };
}
