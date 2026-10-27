import {expect,test} from 'vitest';
import {ScientificSessionRecovery, scientificRecoveryWindows} from './scientific-session-recovery';
class Store implements Storage {
 data=new Map<string,string>();get length(){return this.data.size;}
 clear(){this.data.clear();} key(i:number){return [...this.data.keys()][i]??null;}
 getItem(k:string){return this.data.get(k)??null;}setItem(k:string,v:string){this.data.set(k,v);}removeItem(k:string){this.data.delete(k);}
}
test('window recovery exposes exact same-Project identities but never its capability secret',()=>{
 const storage=new Store(),p={storage,prefix:'private'};
 const identity=['project','p'+'a'.repeat(32),4,'original-window'];
 const session=new ScientificSessionRecovery(p,identity),secret=session.key();
 new ScientificSessionRecovery(p,['other','p'+'b'.repeat(32),2,'other-window']).key();
 storage.setItem('private:broken','not-a-secret');
 storage.setItem('private:'+JSON.stringify(['project','p'+'c'.repeat(32),0,'window']),'x'.repeat(36));
 const windows=scientificRecoveryWindows(p,'project');
 expect(windows).toEqual([{id:identity[1],revision:4,placement:'original-window'}]);
 expect(JSON.stringify(windows)).not.toContain(secret);
 session.stopped();expect(scientificRecoveryWindows(p,'project')).toEqual([]);
});

test('later recovery identities remain reachable behind 32 stale windows',()=>{
 const storage=new Store(),p={storage,prefix:'private'};
 for(let i=0;i<33;i++)new ScientificSessionRecovery(p,['project','p'+i.toString(16).padStart(32,'0'),1,`window-${i}`]).key();
 expect(scientificRecoveryWindows(p,'project')).toHaveLength(32);
 expect(scientificRecoveryWindows(p,'project',32)).toEqual([{id:'p'+'20'.padStart(32,'0'),revision:1,placement:'window-32'}]);
 expect(scientificRecoveryWindows(p,'project',33)).toEqual([]);
});
