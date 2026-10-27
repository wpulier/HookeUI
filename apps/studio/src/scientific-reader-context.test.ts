import {expect,test} from 'vitest';
import {recordedScientificReader,selectedScientificReader} from './scientific-reader-context';
const runtime={bundle:'mine/reader',generation:'a'.repeat(16),operation:'open'};
const document=()=>({runtimes:[{...runtime}],state:{sourceFile:{name:'Cells.zarr'},scientificView:{runtime:{...runtime}},
 sourceReader:{format:1,name:'OME-Zarr reader',sourceName:'Cells.zarr',capability:'mine/reader#open',generation:runtime.generation,
 source:{id:'p'+'b'.repeat(32),session:'project',revision:1,digest:'c'.repeat(64)}}}});
test('reader context uses exact recorded source and prepared name, not the resource title or package key',()=>{
 const saved={...document(),title:'My custom title'};
 expect(selectedScientificReader(saved,'project')).toEqual({name:'OME-Zarr reader',input:'Cells.zarr',runtime});
 expect(saved.title).toBe('My custom title');
});
test.each(['legacy','generation','runtime','project','source','sourceName','runtimes'])('refuses %s context without guessing a reader name',field=>{
 const saved=document();
 if(field==='legacy')Reflect.deleteProperty(saved.state.sourceReader,'name');
 if(field==='generation')saved.state.sourceReader.generation='b'.repeat(16);
 if(field==='runtime')saved.state.scientificView.runtime.operation='other';
 if(field==='project')saved.state.sourceReader.source.session='another-project';
 if(field==='source')saved.state.sourceReader.source.revision=0;
 if(field==='sourceName')saved.state.sourceFile.name='Another file';
 if(field==='runtimes')saved.runtimes=[];
 expect(selectedScientificReader(saved,'project')).toBeNull();
});
test('malformed or absent records never supply context',()=>{
 for(const state of [null,{},[],{sourceReader:{name:'Guessed'}}])expect(recordedScientificReader(state)).toBeNull();
});
