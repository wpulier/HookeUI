import { expect, test } from 'vitest';
import { selectedFolder } from './scientific-folder';
const file=(path:string)=>{const result=new File(['abc'],path.split('/').at(-1)!);Object.defineProperty(result,'webkitRelativePath',{value:path});return result;};
test.each([['root/../outside'],['root//x'],['root/a','root/a'],['root/a','root/a/b'],['root/a/b','root/a'],['root/a','other/b'],['root/C:x']])('unsafe folder selection refuses before upload: %s',(...paths)=>{
  expect(()=>selectedFolder(paths.map(file))).toThrow();
});
test('oversized or empty folder is refused without reading file bytes',()=>{
  expect(()=>selectedFolder([])).toThrow();const large=file('root/a');Object.defineProperty(large,'size',{value:64*1024*1024+1});expect(()=>selectedFolder([large])).toThrow();
});
