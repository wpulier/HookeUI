/** Observed bridge absence, distinct from permission, ownership or selection failure. */
export class CameraInputUnavailable extends Error {
 constructor(){super('The camera bridge is unavailable. Saved Data and notes can still open.');this.name='CameraInputUnavailable';}
}

interface InputHealth { state: string; bridge: { ok: boolean; error: string } }
interface InputSelection {
 check(): void;
 health(): InputHealth | null;
 waitFor(check: () => boolean, message: string, timeout: number): Promise<void>;
 select(): Promise<void>;
}
/** Startup has no bridge observation yet. Wait for one bounded observation before
 * selecting Live, then preserve absence separately from a refused selection. */
export async function selectObservedWorkspaceInput(source: string, owner: InputSelection): Promise<void> {
 owner.check();
 if(source==='live'){
  await owner.waitFor(()=>{
   const health=owner.health();
   return !!health&&(health.state!=='waiting'||health.bridge.ok||!!health.bridge.error);
  },'The camera connection has not been checked yet. Retry opening; your saved Data is kept.',3000);
  owner.check(); // a Project change, recording or voice start during observation still refuses selection
  const health=owner.health();
  if(health&&!health.bridge.ok&&(health.state==='bridge_offline'||health.state==='waiting'&&!!health.bridge.error))throw new CameraInputUnavailable();
 }
 await owner.select();
}
