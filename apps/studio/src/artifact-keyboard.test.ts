import {expect,test,vi} from 'vitest';
import {bindArtifactEscape} from './artifact-keyboard';

test('sandbox Escape accepts only this focused connected frame and its current nonce, outside another modal',()=>{
  let receive!: (event:MessageEvent)=>void; let loaded!:()=>void;
  const child={postMessage:vi.fn()};const close=vi.fn();
  const frame={src:'/research/day/r1/outputs/lesson.html',contentWindow:child,isConnected:true,
    addEventListener:(_name:string,fn:()=>void)=>{loaded=fn;}};
  const document={activeElement:frame,querySelectorAll:()=>[] as {contains:(target:unknown)=>boolean}[]};
  vi.stubGlobal('document',document);vi.stubGlobal('window',{addEventListener:(_name:string,fn:typeof receive)=>{receive=fn;}});
  bindArtifactEscape(frame as unknown as HTMLIFrameElement,close);loaded();
  const nonce=child.postMessage.mock.calls[0][0].nonce;
  const valid={origin:'null',source:child,data:{type:'micromind-artifact-escape',nonce}};
  for(const event of [{...valid,origin:'http://localhost'}, {...valid,source:{}}, {...valid,data:{...valid.data,nonce:'wrong'}}, {...valid,data:{...valid.data,type:'run-tool'}}]) receive(event as unknown as MessageEvent);
  expect(close).not.toHaveBeenCalled();
  frame.isConnected=false;receive(valid as unknown as MessageEvent);frame.isConnected=true;
  document.activeElement={} as typeof frame;receive(valid as unknown as MessageEvent);document.activeElement=frame;
  document.querySelectorAll=()=>[{contains:()=>false}];receive(valid as unknown as MessageEvent);document.querySelectorAll=()=>[];
  expect(close).not.toHaveBeenCalled();receive(valid as unknown as MessageEvent);expect(close).toHaveBeenCalledTimes(1);
  loaded();receive(valid as unknown as MessageEvent);expect(close).toHaveBeenCalledTimes(1);
  vi.unstubAllGlobals();
});
