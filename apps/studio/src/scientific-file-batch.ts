/** Sequential retained-file intake through the existing bounded transaction. */
import { ChunkRecoveryRequired, MAX_SOURCE_BYTES, recoverChunkTransfer, uploadScientificFile } from './scientific-chunk-upload';

export type SavedSource = {id:string;session:string;revision:number;title:string};
type Item = {file:File;request:string;saved:SavedSource|null;uncertain:boolean};
const object=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value);

export class ScientificFileBatch {
  readonly items:Item[];
  readonly session:string;
  constructor(session:string, files:readonly File[]) {
    this.session=session;
    if(!files.length||files.length>256)throw new Error('Choose between 1 and 256 files per import.');
    if(files.some(file=>!Number.isSafeInteger(file.size)||file.size<0||file.size>MAX_SOURCE_BYTES))
      throw new Error('Each file must be at most 64 GiB, subject to free disk space.');
    this.items=files.map(file=>({file,request:crypto.randomUUID(),saved:null,uncertain:false}));
  }
  get completed():boolean{return this.items.every(item=>item.saved!==null);}
  get needsRecovery():boolean{return this.items.some(item=>item.uncertain);}
  get count():number{return this.items.filter(item=>item.saved!==null).length;}
  async run(signal:AbortSignal, progress:(index:number,done:number,total:number)=>void, saved:(source:SavedSource)=>void):Promise<void>{
    for(const [index,item] of this.items.entries()){
      if(item.saved)continue;
      signal.throwIfAborted();
      let result:unknown;
      try{
        if(item.uncertain){
          result=await recoverChunkTransfer('file',this.session,item.request);
          item.uncertain=false;
          if(object(result)&&result.project===null)result=undefined;
        }
        if(result===undefined)result=await uploadScientificFile(item.file,{session:this.session,request:item.request,signal,
          progress:(done,total)=>progress(index,done,total)});
      }catch(error){
        if(error instanceof ChunkRecoveryRequired)item.uncertain=true;
        throw error;
      }
      const project=object(result)?result.project:null;
      if(!object(project)||typeof project.id!=='string'||!/^p[0-9a-f]{32}$/.test(project.id)||project.session!==this.session
          ||project.revision!==1||!object(project.document)||project.document.kind!=='source-file'||project.document.title!==item.file.name){
        item.uncertain=true;
        throw new Error('The import receipt does not match this file and Project. Check its saved outcome before continuing.');
      }
      item.saved={id:project.id,session:this.session,revision:1,title:item.file.name};
      saved(item.saved);
    }
  }
}
