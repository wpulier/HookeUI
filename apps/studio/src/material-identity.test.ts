import {expect,it} from 'vitest';
import {dataRows,rowTitle,type DataEntry} from './data-roles';
import type {AssetMeta} from './protocol';
import {isRetainedMaterial,sourceReaderEntry} from './material-identity';
import {projectInputs} from './project-inputs';

const ref={origin:'a'.repeat(32),session:'2026-10-30',document:'p'+'b'.repeat(32),revision:1,digest:'c'.repeat(64)};
const source:AssetMeta={id:'a1',kind:'still',title:'Human cells · attribution',retained:ref,
  from:'added',capturedAt:null,captured:'',seconds:null,capturedOn:null,microscope:null,
  optics:{},specimen:null,specimenBy:null,notes:null,origin:null,file:'original.png'};
const entry:DataEntry={id:ref.document,day:ref.session,version:1,kind:'project',title:'original.png',summary:'',state:'saved',actions:[],source:{sha256:ref.digest},material:{kind:'image',origin:'imported',input:true,section:'files',category:'import',record:false,dependencies:[]},maker:{kind:'agent',id:'a',name:'Researcher'}};

it('joins one exact retained record to its source alias without losing authorship',()=>{
  const rows=dataRows([entry],[source]);
  expect(rows).toHaveLength(1);
  expect(rows[0]).toMatchObject({entry,asset:source,maker:entry.maker});
  expect(rowTitle(rows[0])).toBe('Human cells');
  expect(projectInputs([entry],'human cells',[source])).toEqual([entry]);
});

it('keeps different imports and historical revisions separate even with identical titles',()=>{
  for(const changed of [{...entry,id:'p'+'d'.repeat(32)}, {...entry,version:2},
    {...entry,day:'2026-10-29'}, {...entry,source:{sha256:'f'.repeat(64)}}]){
    expect(isRetainedMaterial(changed,source)).toBe(false);
    expect(dataRows([changed],[source])).toHaveLength(2);
  }
  expect(isRetainedMaterial({...entry,source:undefined},source)).toBe(false);
  expect(isRetainedMaterial(entry,{...source,retained:undefined})).toBe(false);
});

it('never borrows a legacy alias entry from a different retained source',()=>{
  const legacy:DataEntry={...entry,id:'a1',kind:'asset',material:undefined};
  const rows=dataRows([legacy],[source]);
  expect(rows).toHaveLength(2);
  expect(rows[0].entry).toBeNull();
  expect(rows[0].maker).not.toEqual(entry.maker);
  expect(rows[1].entry).toBe(legacy);
});

it('opens exact Project evidence when a source arrives before inventory',()=>{
  const reader=sourceReaderEntry(source,ref.session);
  expect(reader).toMatchObject({id:ref.document,day:ref.session,version:ref.revision,
    kind:'project',source:{sha256:ref.digest}});
  expect(reader.asset).toBeUndefined(); // No /api/library/<alias>/file reader branch.
  expect(sourceReaderEntry({...source,retained:undefined},ref.session)).toMatchObject({
    id:'a1',kind:'asset',asset:{id:'a1'}});
});
