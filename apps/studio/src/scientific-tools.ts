import { comparisonChooser } from './scientific-comparison';
/** Explicit next-tool choice retains values and earlier document revisions. */
type Source = { id: string; day: string; version: number };
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => { const el = document.createElement(tag); el.textContent = text; return el; };
async function reply(response: Response): Promise<Record<string, unknown>> {
  const result: unknown = await response.json();
  if (!object(result) || !response.ok || result.ok !== true)
    throw new Error(object(result) ? `${String(result.says ?? 'Tool choice failed.')} ${String(result.next ?? '')}` : 'The tool response is invalid. Reopen this saved revision.');
  return result;
}
export function scientificTools(root: HTMLElement, source: Source, open: (target: { id: string; version: number; title: string }) => Promise<void>, presentation?: { heading: string; inputName: string }): void {
  const section = node('details'), find = node('button', 'Find interactive tools'), status = node('p'), list = node('div');
  status.setAttribute('role', 'status');
  const explanation = node('details');
  explanation.append(node('summary', 'What happens next'), node('p', 'Choosing a tool saves your choice without changing scientific values. Earlier revisions and running sessions stay available. Opening the session is a separate action.'));
  section.append(node('summary', presentation?.heading ?? 'Continue with another tool'),
    node('p', `${presentation ? `${presentation.inputName} · ` : ''}Saved revision ${source.version}. Unsaved edits and other windows are not inputs.`), find, status, list, explanation);
  root.append(section);
  let loading = false;
  find.addEventListener('click', () => { void (async () => {
    if (loading) return; loading = true; find.disabled = true; status.textContent = 'Finding tools for this saved input…';
    try {
      const result = await reply(await fetch(`/api/scientific-projects/${encodeURIComponent(source.id)}/tools?${new URLSearchParams({ session: source.day, revision: String(source.version) })}`));
      if (!object(result.source) || result.source.id !== source.id || result.source.session !== source.day || result.source.revision !== source.version || !Array.isArray(result.tools))
        throw new Error('These tools belong to another input revision. Reopen the saved scene.');
      const digest = result.source.digest;
      if (typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest)) throw new Error('The saved input identity is incomplete. Reopen it.');
      list.replaceChildren();
      const search = node('input'); search.type = 'search'; search.placeholder = 'Find a task, such as segment or animate'; search.setAttribute('aria-label','Find tools by scientific task');
      const searchRows: {element:HTMLElement;text:string}[] = [];
      search.addEventListener('input',()=>{const query=search.value.trim().toLocaleLowerCase();for(const row of searchRows)row.element.hidden=!!query&&!row.text.includes(query);});
      list.append(search);
      status.textContent = result.tools.length ? 'Choose a tool for your task. Check its supported inputs before running; preparation and scientific suitability are separate.' : 'No interactive scene integration is available. Add one in Models, then check again.';
      for (const value of result.tools) {
        if (!object(value) || typeof value.key !== 'string' || typeof value.name !== 'string' || typeof value.owner !== 'string'
          || typeof value.canReview !== 'boolean' || typeof value.reason !== 'string' || typeof value.scope !== 'string'
          || !(typeof value.generation === 'string' && /^[a-f0-9]{16}$/.test(value.generation) || value.generation === null && !value.canReview))
          throw new Error('Tool declarations changed. Check tools again before choosing.');
        const key = value.key, generation = value.generation;
        const row = node('article'), choose = node('button', `${value.updateTrial === true ? 'Try update: ' : 'Continue with '}${value.name}`), message = node('p', value.reason || value.scope), setup = node('a', 'Open integration setup');
        setup.href = `#models/${encodeURIComponent(value.owner)}`;
        choose.disabled = !value.canReview; message.setAttribute('role', 'status');
        const identity = node('details'); identity.append(node('summary', 'Integration identity'), node('p', `${key} · ${generation ?? 'Preparation incomplete'}`));
        row.append(node('h4', value.name));
        if(typeof value.category==='string'&&value.category)row.append(node('p',value.category));
        if(typeof value.description==='string'&&value.description)row.append(node('p',value.description));
        row.append(message, choose, setup, identity); list.append(row);
        const aliases=Array.isArray(value.searchAliases)?value.searchAliases.filter(v=>typeof v==='string').join(' '):'';
        searchRows.push({element:row,text:[value.name,value.category,value.description,aliases].filter(v=>typeof v==='string').join(' ').toLocaleLowerCase()});
        let pending = false;
        choose.addEventListener('click', () => { void (async () => {
          if (pending) return; pending = true; choose.disabled = true; message.textContent = 'Saving your tool choice against this exact input revision…';
          try {
            const result = await reply(await fetch(`/api/scientific-projects/${encodeURIComponent(source.id)}/tool`, {
              method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'choose-scientific-tool' },
              body: JSON.stringify({ session: source.day, revision: source.version, capability: key, generation, ...(value.updateTrial === true ? {updateTrial:true} : {}) }),
            }));
            const project = result.project, doc = object(project) ? project.document : null;
            const state = object(doc) ? doc.state : null, choice = object(state) ? state.scientificToolChoice : null;
            if (!object(project) || project.id !== source.id || project.session !== source.day || project.revision !== source.version + 1
              || !object(doc) || typeof doc.title !== 'string' || !object(choice) || choice.format !== 1 || choice.capability !== key || choice.generation !== generation
              || !object(choice.source) || choice.source.id !== source.id || choice.source.session !== source.day || choice.source.revision !== source.version || choice.source.digest !== digest)
              throw new Error('The saved choice differs from your selected input or integration. Check Project Data before retrying.');
            const target = { id: source.id, version: Number(project.revision), title: doc.title }, show = node('button', `Open ${value.name}`);
            message.textContent = `${value.name} is selected. Open it to inspect settings; running is a separate action. This window remains on revision ${source.version}.`;
            const receipt = node('details'); receipt.append(node('summary', 'Saved choice'), node('p', `Saved revision ${project.revision}; input revision ${source.version}.`)); message.after(receipt);
            choose.hidden = true; message.after(show);
            show.addEventListener('click', () => { void open(target).catch(error => { message.textContent = String(error instanceof Error ? error.message : error); }); });
          } catch (error) { message.textContent = String(error instanceof Error ? error.message : error); choose.disabled = false; }
          finally { pending = false; }
        })(); });
      }
      if(object(result.comparison)&&result.comparison.available===true) comparisonChooser(list, source, digest, result.tools.filter(value => !object(value) || value.updateTrial !== true), open);
      else list.append(node('p',object(result.comparison)&&typeof result.comparison.reason==='string'&&result.comparison.reason
        ? result.comparison.reason : 'Comparison availability is unverified. Check tools again.'));
    } catch (error) { status.textContent = String(error instanceof Error ? error.message : error); }
    finally { loading = false; find.disabled = false; find.textContent = 'Check interactive tools again'; }
  })(); });
}

export function scientificToolChoice(root: HTMLElement, state: unknown, source: Source, open: (revision: number) => Promise<void>): void {
  const choice = selectedScientificTool(state, source);
  if (!choice) {
    if (object(state) && state.scientificToolChoice) root.append(node('p', 'This saved tool choice is incomplete or unsupported. Keep the original revision; no tool has been started.'));
    return;
  }
  const original = choice.source.revision, section = node('section'), status = node('p');
  const back = node('button', `Open input revision ${original}`);
  back.addEventListener('click', () => { void open(original).catch(error => { status.textContent = String(error instanceof Error ? error.message : error); }); });
  const details = node('details'); details.append(node('summary', 'Selected integration identity'), node('p', `${choice.capability} · ${choice.generation}`));
  section.append(node('p', `Selected tool: ${choice.name}. Inputs captured from saved revision ${original}. Choose Open session to use it.`), back, details, status);
  root.append(section);
}

/** Display only an explicitly recorded choice for this exact document owner. */
export function selectedScientificTool(state: unknown, source: Source): {name:string;capability:string;generation:string;source:{revision:number}} | null {
  const choice=object(state)?state.scientificToolChoice:null;
  if (!object(choice) || choice.format!==1 || typeof choice.name!=='string' || !choice.name.trim() || typeof choice.capability!=='string'
    || typeof choice.generation!=='string' || !/^[a-f0-9]{16}$/.test(choice.generation) || !object(choice.source)
    || choice.source.id!==source.id || choice.source.session!==source.day || typeof choice.source.revision!=='number'
    || !Number.isInteger(choice.source.revision) || choice.source.revision<1 || choice.source.revision>=source.version) return null;
  return {name:choice.name,capability:choice.capability,generation:choice.generation,source:{revision:choice.source.revision}};
}

/** The provenance service checked the retained selection; also bind its display to this view. */
export function savedScientificTool(document: Record<string, unknown>, value: unknown): string | null {
  const state=object(document.state)?document.state:null;
  const view=state&&object(state.scientificView)?state.scientificView:null;
  const output=state&&object(state.hookeOutput)?state.hookeOutput:null;
  if(!object(value)||typeof value.name!=='string'||!value.name.trim()||!object(value.runtime)||!object(value.source)
      ||!view||!object(view.runtime)||!output||!object(output.base)||!Array.isArray(document.runtimes))return null;
  const runtime=value.runtime, source=value.source, base=output.base;
  const matches=(other:unknown)=>object(other)&&['bundle','generation','operation'].every(key=>
    typeof runtime[key]==='string'&&runtime[key]!==''&&runtime[key]===other[key]);
  if(!matches(view.runtime)||!document.runtimes.some(matches)
      ||!['id','session','revision','digest'].every(key=>source[key]===base[key]))return null;
  return value.name;
}
