import {expect,test} from 'vitest';
import {Link,type ClientCommand,type ServerMessage} from './protocol';
class Wire extends Link {
 command?:ClientCommand & {requestId?:string};
 constructor(){super(()=>{});}
 send(command:ClientCommand & {requestId?:string}){this.command=command;}
 reply(message:unknown){this.receive(message as ServerMessage);}
}
test('the real connection retains structured conflict details for recovery',async()=>{
 const wire=new Wire();const pending=wire.request('session-note',{session:'p',action:'save',id:'a',base:1,title:'Note',text:'Draft'});
 const reply={type:'result',for:'session-note',requestId:wire.command!.requestId,ok:false,error:'Conflict',code:'conflict',current:{id:'a',title:'Note',text:'Saved',version:2}};
 wire.reply(reply);await expect(pending).rejects.toMatchObject({reply});
});
