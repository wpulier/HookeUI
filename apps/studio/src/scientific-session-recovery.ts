/** Browser-private capability. Never put it in a Project, observation or inventory. */
export type ScientificRecoveryStorage = { storage: Storage; prefix: string };
export class ScientificSessionRecovery {
  private readonly name: string;
  private readonly persistence: ScientificRecoveryStorage;
  constructor(persistence: ScientificRecoveryStorage, identity: readonly unknown[]) {
    this.persistence = persistence;
    this.name = `${persistence.prefix}:${JSON.stringify(identity)}`;
  }
  key(): string {
    try {
      const { storage } = this.persistence;
      const existing = storage.getItem(this.name);
      if (existing !== null) {
        if (!/^[A-Za-z0-9_-]{32,128}$/.test(existing)) throw new Error('Invalid recovery key');
        return existing;
      }
      const key = crypto.randomUUID();
      storage.setItem(this.name, key);
      if (storage.getItem(this.name) !== key) throw new Error('Recovery key was not retained');
      return key;
    } catch {
      throw new Error('This browser cannot retain private-session recovery. Restore browser storage access, then open the session. No analysis was started.');
    }
  }
  stopped(): void {
    // A stale key cannot resurrect a stopped worker. Cleanup cannot undo Stop.
    try { this.persistence.storage.removeItem(this.name); } catch { /* retry on next open */ }
  }
}

/** Locally retained window identities only; recovery secrets never leave this store. */
export function scientificRecoveryWindows(persistence: ScientificRecoveryStorage, project: string, offset = 0): {id:string; revision:number; placement:string}[] {
  const rows:{id:string;revision:number;placement:string}[]=[];
  try {
    const prefix=`${persistence.prefix}:`;
    let matched = 0;
    for(let i=0;i<persistence.storage.length&&rows.length<32;i++) {
      const key=persistence.storage.key(i);
      if(!key?.startsWith(prefix))continue;
      try {
        const identity:unknown=JSON.parse(key.slice(prefix.length));
        if(!Array.isArray(identity)||identity.length!==4||identity[0]!==project||
          typeof identity[1]!=='string'||!/^p[0-9a-f]{32}$/.test(identity[1])||
          !Number.isSafeInteger(identity[2])||identity[2]<1||
          typeof identity[3]!=='string'||!/^[A-Za-z0-9_-]{1,128}$/.test(identity[3]))continue;
        if(!/^[A-Za-z0-9_-]{32,128}$/.test(persistence.storage.getItem(key)??''))continue;
        if (matched++ < Math.max(0, offset)) continue;
        rows.push({id:identity[1],revision:identity[2],placement:identity[3]});
      }catch{/* Keep malformed records private; valid windows remain recoverable. */}
    }
  }catch{/* Storage refusal cannot change saved work. */}
  return rows;
}
