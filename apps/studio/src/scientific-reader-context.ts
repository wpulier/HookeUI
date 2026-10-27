/** Inert reader context captured by owned intake, never an inferred model choice. */
const object=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
export function recordedScientificReader(state:unknown):{name:string;input:string;runtime:Record<string,unknown>}|null {
 const reader=object(state)?state.sourceReader:null,view=object(state)?state.scientificView:null;
 const file=object(state)?state.sourceFile:null;
 if(!object(reader)||reader.format!==1||typeof reader.name!=='string'||!reader.name.trim()||reader.name.length>256
   ||typeof reader.sourceName!=='string'||!reader.sourceName.trim()||!object(file)||file.name!==reader.sourceName
   ||!object(reader.source)||typeof reader.source.id!=='string'||!/^p[a-f0-9]{32}$/.test(reader.source.id)
   ||typeof reader.source.session!=='string'||!reader.source.session||!Number.isSafeInteger(reader.source.revision)||Number(reader.source.revision)<1
   ||typeof reader.source.digest!=='string'||!/^([a-f0-9]{64})$/.test(reader.source.digest)
   ||typeof reader.capability!=='string'||typeof reader.generation!=='string'||!/^[a-f0-9]{16}$/.test(reader.generation)
   ||!object(view)||!object(view.runtime))return null;
 const runtime=view.runtime;
 if(reader.capability!==`${runtime.bundle}#${runtime.operation}`||reader.generation!==runtime.generation)return null;
 return {name:reader.name,input:reader.sourceName,runtime};
}
export function selectedScientificReader(document:Record<string,unknown>,project:string):ReturnType<typeof recordedScientificReader> {
 const reader=recordedScientificReader(document.state),state=object(document.state)?document.state:null;
 const source=state&&object(state.sourceReader)&&object(state.sourceReader.source)?state.sourceReader.source:null;
 if(!reader||source?.session!==project||!Array.isArray(document.runtimes)||!document.runtimes.some(value=>object(value)&&
   ['bundle','generation','operation'].every(key=>value[key]===reader.runtime[key])))return null;
 return reader;
}
