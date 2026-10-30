import type { AssetMeta } from './protocol';

type Reference = AssetMeta['retained'];
type Receipt = { ok?: boolean; error?: string | null; retained?: Reference };
type PendingStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
/** Explicit Add material completes before navigation. Restoration never calls this.
 * Pending request keys survive reconnect/reload; an acknowledged new Add gets a new key. */
export class SourceIntake {
  private selection = 0;
  private currentProject: () => string;
  private retain: (asset: string, session: string, request: string) => Promise<Receipt>;
  private currentOpening: () => number;
  private pending: PendingStore;
  constructor(currentProject: () => string,
    retain: (asset: string, session: string, request: string) => Promise<Receipt>,
    currentOpening: () => number = () => 0, pending?: PendingStore) {
    this.currentProject = currentProject;
    this.retain = retain;
    this.currentOpening = currentOpening;
    const memory = new Map<string, string>();
    this.pending = pending ?? {getItem: key => memory.get(key) ?? null,
      setItem: (key, value) => { memory.set(key, value); }, removeItem: key => { memory.delete(key); }};
  }

  async prepare(asset: string, session: string, retained?: Reference): Promise<{ready: boolean; retained?: Reference}> {
    const selection = ++this.selection, opening = this.currentOpening();
    if (this.currentProject() !== session) throw new Error('Return to this Project before adding material.');
    if (asset !== 'live' && !retained) {
      const key = `hooke.material-intake:${JSON.stringify([session, asset])}`;
      const request = this.pending.getItem(key) ?? `material-${crypto.randomUUID()}`;
      this.pending.setItem(key, request); // failure refuses admission; never silently lose retry identity
      let result: Receipt;
      try { result = await this.retain(asset, session, request); }
      catch (error) { if (selection !== this.selection) return {ready:false}; throw error; }
      if (this.pending.getItem(key) === request) this.pending.removeItem(key);
      if (selection !== this.selection) return {ready:false};
      if (result.ok === false) throw new Error(result.error || 'The material could not be added.');
      retained = result.retained;
    }
    if (selection !== this.selection) return {ready:false};
    if (this.currentProject() !== session || this.currentOpening() !== opening)
      throw new Error('The material was kept in its Project. Choose it again to open it.');
    return {ready:true, ...(retained ? {retained} : {})};
  }
}
