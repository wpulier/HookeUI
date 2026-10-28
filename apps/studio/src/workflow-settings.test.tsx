import {it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {WorkflowSettings} from './workflow-settings';
it('shows saved bright-region values in named controls without exposing generation editing',()=>{
 const html=renderToStaticMarkup(<WorkflowSettings kind="tool" settings={{capability:'basic-bright-objects',generation:'private-generation-digest',mode:'automatic',parameters:{sigma:2.5,min_area:45,connectivity:4}}} change={()=>{}}/>);
 expect(html).toContain('Smoothing (pixels)');expect(html).toContain('value="2.5"');
 expect(html).toContain('Minimum region size (pixels)');expect(html).toContain('value="45"');
 expect(html).toContain('value="4" selected');expect(html).not.toContain('private-generation-digest');
});
it('shows bounded research controls while preserving the no-tools boundary',()=>{
 const html=renderToStaticMarkup(<WorkflowSettings kind="researcher" settings={{question:'Compare the measurements',criteria:'Cite both inputs',maximumUsd:.25,seconds:120,provider:'gemini-google',tools:[],output:'report'}} change={()=>{}}/>);
 for(const value of ['Compare the measurements','Cite both inputs','value="0.25"','value="120"','no executable tools'])expect(html).toContain(value);
});
