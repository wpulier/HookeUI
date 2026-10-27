/** Retained scientific comparisons. Opening a comparison never executes a tool. */
type Source = { id: string; day: string; version: number };
type Exact = { id: string; session: string; revision: number; digest: string };
type Target = { id: string; version: number; title: string };
type Open = (target: Target) => Promise<void>;
type Arm = { id: string; capability: string; generation: string; name: string; owner: string; scene: Exact | null };
type Output = { name: string; type: string; shape: number[] | null; meaning: unknown };
type Attempt = {
  arm: string; outcome: 'succeeded' | 'failed' | 'cancelled'; source: 'saved-result' | 'preparation' | 'researcher'; at: number;
  model: Record<string, unknown>; settings: { schema: string; version: number; values: Record<string, unknown> } | null;
  outputs: Output[]; result: Exact | null; timing: Record<string, unknown>; says: string;
};
type Chosen = { arm: string; result: Exact; at: number } | null;
type Comparison = { input: Exact; arms: Arm[]; retained: { arm: string; result: Exact }[]; attempts: Attempt[]; chosen: Chosen };
const FORMATS = ['scientific-comparison-v1', 'scientific-comparison-v2'];
const OUTCOMES: Record<string, string[]> = { succeeded: ['saved-result'], failed: ['preparation', 'researcher'], cancelled: ['preparation', 'researcher'] };
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => { const el = document.createElement(tag); el.textContent = text; return el; };
const exact = (v: unknown): v is Exact => object(v) && typeof v.id === 'string' && /^p[0-9a-f]{32}$/.test(v.id)
  && typeof v.session === 'string' && typeof v.revision === 'number' && Number.isSafeInteger(v.revision) && v.revision > 0
  && typeof v.digest === 'string' && /^[0-9a-f]{64}$/.test(v.digest);
const same = (a: Exact, b: Exact) => a.id === b.id && a.session === b.session && a.revision === b.revision && a.digest === b.digest;
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const words = (e: unknown) => e instanceof Error ? e.message : String(e);
const request = () => crypto.randomUUID().replaceAll('-', '');
const clock = (at: number) => new Date(at * 1000).toLocaleTimeString();
const letter = (arm: string) => arm.toUpperCase();
function parse(value: unknown, session: string): Comparison {
  if (!object(value) || !FORMATS.includes(String(value.format)) || !exact(value.input) || value.input.session !== session
      || !Array.isArray(value.arms) || value.arms.length < 2 || value.arms.length > 3 || !Array.isArray(value.retained) || value.retained.length > 12)
    throw new Error('This comparison format is unsupported. Keep its saved document.');
  const arms: Arm[] = value.arms.map((arm: unknown, index: number) => {
    if (!object(arm) || arm.id !== 'abc'[index] || typeof arm.capability !== 'string' || typeof arm.generation !== 'string'
        || !/^[a-f0-9]{16}$/.test(arm.generation) || typeof arm.name !== 'string' || typeof arm.owner !== 'string'
        || !(arm.scene === null || exact(arm.scene) && arm.scene.session === session)) throw new Error('A comparison alternative has an unsupported saved identity.');
    return { id: arm.id, capability: arm.capability, generation: arm.generation, name: arm.name, owner: arm.owner, scene: arm.scene };
  });
  const retained = value.retained.map((row: unknown) => {
    if (!object(row) || typeof row.arm !== 'string' || !arms.some(a => a.id === row.arm) || !exact(row.result) || row.result.session !== session)
      throw new Error('A comparison result has an unsupported saved identity.');
    return { arm: row.arm, result: row.result };
  });
  const v2 = value.format === FORMATS[1];
  if (v2 && (!Array.isArray(value.attempts) || value.attempts.length > 36)) throw new Error('This comparison’s attempts are unsupported. Keep its saved document.');
  const attempts: Attempt[] = (v2 ? value.attempts as unknown[] : []).map(row => {
    if (!object(row) || typeof row.arm !== 'string' || !arms.some(a => a.id === row.arm) || typeof row.outcome !== 'string'
        || !(OUTCOMES[row.outcome] ?? []).includes(String(row.source)) || !finite(row.at) || typeof row.says !== 'string'
        || !object(row.model) || !object(row.timing) || !Array.isArray(row.outputs)
        || !(row.settings === null || object(row.settings) && typeof row.settings.schema === 'string' && object(row.settings.values))
        || (row.outcome === 'succeeded' ? !(exact(row.result) && row.result.session === session) : row.result !== null))
      throw new Error('A comparison attempt has an unsupported saved form. Keep its saved document.');
    const outputs = row.outputs.filter(object).map(o => ({ name: String(o.name ?? ''), type: String(o.type ?? ''),
      shape: Array.isArray(o.shape) && o.shape.every(n => Number.isSafeInteger(n)) ? o.shape as number[] : null, meaning: o.meaning }));
    return { ...row, outputs } as Attempt;
  });
  let chosen: Chosen = null;
  if (v2 && value.chosen !== null) {
    const c = value.chosen;
    if (!object(c) || typeof c.arm !== 'string' || !exact(c.result) || !finite(c.at)
        || !attempts.some(a => a.outcome === 'succeeded' && a.arm === c.arm && a.result && same(a.result, c.result as Exact)))
      throw new Error('The chosen comparison result has an unsupported association. Keep its saved document.');
    chosen = { arm: c.arm, result: c.result, at: c.at };
  }
  return { input: value.input, arms, retained, attempts, chosen };
}
async function reply(response: Response): Promise<Record<string, unknown>> {
  const result: unknown = await response.json();
  if (!response.ok || !object(result) || result.ok !== true)
    throw new Error(object(result) ? `${String(result.says ?? 'Comparison could not finish.')} ${String(result.next ?? '')}` : 'The comparison response is incomplete.');
  return result;
}
async function change(source: Source, action: Record<string, unknown>): Promise<{ target: Target; comparison: Comparison }> {
  const value = await reply(await fetch(`/api/scientific-projects/${encodeURIComponent(source.id)}/comparison`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'scientific-comparison' },
    body: JSON.stringify({ session: source.day, revision: source.version, ...action }),
  }));
  const p = value.project, doc = object(p) ? p.document : null;
  if (!object(p) || typeof p.id !== 'string' || !/^p[a-f0-9]{32}$/.test(p.id) || p.session !== source.day
      || !Number.isSafeInteger(p.revision) || Number(p.revision) < 1 || !object(doc) || doc.kind !== 'scientific-comparison'
      || typeof doc.title !== 'string' || !object(doc.state)) throw new Error('The saved comparison receipt is incomplete. Check Project Data before retrying.');
  if (action.action !== 'create' && (p.id !== source.id || ![source.version, source.version + 1].includes(Number(p.revision))))
    throw new Error('The comparison receipt belongs to another revision. Reopen the saved comparison.');
  return { target: { id: p.id, version: Number(p.revision), title: doc.title }, comparison: parse(doc.state.comparison, source.day) };
}
/** One integration may fill several arms: each arm gets its own scene, so its settings are its own. */
export function comparisonChooser(root: HTMLElement, source: Source, digest: string, tools: unknown[], open: Open): void {
  const eligible = tools.filter((row): row is Record<string, unknown> => object(row) && typeof row.key === 'string' && typeof row.name === 'string'
    && typeof row.generation === 'string' && /^[a-f0-9]{16}$/.test(row.generation));
  if (!eligible.length) return;
  const section = node('details'), status = node('p'), create = node('button', 'Create comparison');
  section.className = 'scientific-comparison-chooser';
  section.append(node('summary', 'Compare alternatives on this input'), node('p', `Every alternative starts from saved revision ${source.version}. Choose two or three: different tools, or one tool more than once to compare its settings. Configure and run each separately; other results stay intact. Agreement is not scientific accuracy.`));
  const search=node('input');search.type='search';search.placeholder='Find alternatives by scientific task';search.setAttribute('aria-label','Find comparison alternatives by task');section.append(search);
  const selections = eligible.map(row => {
    const label = node('label'), input = node('input'), count = node('select'); input.type = 'checkbox';
    for (const n of [1, 2, 3]) { const option = node('option', n === 1 ? 'one arm' : `${n} arms, each with its own settings`); option.value = String(n); count.append(option); }
    count.value = '1'; count.setAttribute('aria-label', `Arms for ${String(row.name)}`); count.hidden = true;
    input.addEventListener('change', () => { count.hidden = !input.checked; });
    label.append(input, node('span', String(row.name)), count); section.append(label);
    return { input, label, count, key: String(row.key), text: [row.name,row.category,row.description,...(Array.isArray(row.searchAliases)?row.searchAliases:[])].filter(v=>typeof v==='string').join(' ').toLocaleLowerCase() };
  });
  search.addEventListener('input',()=>{const query=search.value.trim().toLocaleLowerCase();for(const row of selections)row.label.hidden=!!query&&!row.input.checked&&!row.text.includes(query);});
  status.setAttribute('role', 'status'); section.append(create, status); root.append(section);
  const identity = request(); let pending = false;
  create.addEventListener('click', () => { void (async () => {
    if (pending) return;
    const choices = selections.filter(row => row.input.checked).flatMap(row => Array<string>(Number(row.count.value) || 1).fill(row.key));
    if (choices.length < 2 || choices.length > 3) { status.textContent = 'Choose two or three alternatives in all.'; return; }
    pending = true; create.disabled = true; status.textContent = 'Saving the comparison and captured input…';
    try {
      const result = await change(source, { action: 'create', choices, request: identity });
      const input = result.comparison.input;
      if (input.id !== source.id || input.revision !== source.version || input.digest !== digest
          || result.comparison.arms.some((arm, i) => arm.capability !== choices[i])) throw new Error('The comparison differs from the selected input or alternatives. Check Project Data.');
      status.textContent = 'Comparison saved. No analysis has started.';
      const show = node('button', 'Open comparison'); section.append(show);
      show.addEventListener('click', () => { void open(result.target).catch(e => { status.textContent = words(e); }); });
      create.hidden = true;
    } catch (e) { status.textContent = words(e); create.disabled = false; }
    finally { pending = false; }
  })(); });
}
function seconds(value: unknown): string | null { return finite(value) ? `${value < 10 ? value.toFixed(1) : Math.round(value)} s` : null; }
/** Each time is said for what it measures; none is called compute time unless the tool reported it. */
export function timingWords(attempt: Attempt): string {
  const t = attempt.timing, parts: string[] = [];
  if (attempt.outcome === 'succeeded') {
    if (finite(t.savedAt)) parts.push(`Saved at ${clock(t.savedAt)}`);
    const wait = seconds(t.controlToSaveSeconds);
    if (wait) parts.push(`${wait} from the last plugin-panel action to Save tool (that action may be the Run click or a later change, so this is not the run time)`);
    else if (t.controls === null) parts.push('The producing session’s control history is not linked to this result');
    parts.push(finite(t.computeSeconds) ? `compute ${seconds(t.computeSeconds)}` : String(t.computeSays ?? 'The tool does not report its own compute time.'));
  } else {
    parts.push(`Recorded at ${clock(attempt.at)}`);
    const prep = seconds(t.preparationSeconds); if (prep) parts.push(`after ${prep} of preparation`);
  }
  return parts.join(' · ');
}
const DEVICES: Record<string, string> = { cpu: 'the CPU of this Mac', 'this-mac': 'this Mac', cloud: 'the provider’s cloud' };
function device(model: Record<string, unknown>): string {
  const d = object(model.device) ? model.device : {};
  const declared = typeof d.declared === 'string' ? d.declared : '';
  const where = DEVICES[declared] ?? (declared ? `declared as “${declared}”` : '');
  return where ? `Device: ${where}, as the integration declares it (the tool does not report the device it used)` : 'Device: not declared by the integration';
}
function outcomeWords(attempt: Attempt): string {
  if (attempt.outcome === 'succeeded') return 'Succeeded · saved result kept';
  const what = attempt.outcome === 'failed' ? 'Failed' : 'Cancelled';
  const where = attempt.source === 'preparation' ? ' while preparing its working input' : ', recorded by you';
  return `${what}${where}${attempt.says ? `: ${attempt.says}` : ''}`;
}
function settingsWords(attempt: Attempt): string {
  if (!attempt.settings) return attempt.outcome === 'succeeded' ? 'Settings: this integration did not declare reproducible settings for this result.' : '';
  const values = Object.entries(attempt.settings.values).map(([k, v]) => `${k} ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(', ');
  return `Panel settings when Save tool ran: ${values}. A change made after the run would also show here.`;
}
function outputsWords(attempt: Attempt): string {
  if (!attempt.outputs.length) return '';
  return `Outputs (${attempt.outputs.length}): ` + attempt.outputs.map(o => {
    const meaning = object(o.meaning) && typeof o.meaning.semantic === 'string' ? ` · ${o.meaning.semantic}` : '';
    return `${o.name} (${o.type}${o.shape ? ` ${o.shape.join('×')}` : ''}${meaning})`;
  }).join('; ');
}
export function showScientificComparison(root: HTMLElement, state: unknown, source: Source, open: Open): void {
  root.classList.add('scientific-comparison');
  const status = node('p'); status.setAttribute('role', 'status');
  let comparison: Comparison;
  try { comparison = parse(object(state) ? state.comparison : null, source.day); }
  catch (e) { root.append(node('p', words(e))); return; }
  const input = comparison.input, refresh = node('button', 'Check saved results'), results = node('div');
  const inputButton = node('button', `Inspect shared input · revision ${input.revision}`);
  inputButton.addEventListener('click', () => { void open({id:input.id,version:input.revision,title:'Comparison input'}).catch(e => {status.textContent=words(e);}); });
  root.append(node('h3', 'Compare alternatives'), node('p', 'One captured input. Independent controls and saved results. A failed or cancelled alternative is kept as that, beside the others. Keeping or choosing a result never changes your future defaults.'), inputButton);
  if (comparison.chosen) {
    const chosen = comparison.chosen, show = node('button', `Open preferred ${letter(chosen.arm)} result · revision ${chosen.result.revision}`);
    show.addEventListener('click', () => { void open({id:chosen.result.id,version:chosen.result.revision,title:`Preferred ${letter(chosen.arm)} result`}).catch(e => {status.textContent=words(e);}); });
    const clear = node('button', 'Clear preferred result');
    clear.addEventListener('click', () => { void update({action:'choose',arm:null,result:null,resultRevision:null}, clear, 'Preferred result cleared. Nothing else changed.'); });
    root.append(node('p', `Preferred: ${letter(chosen.arm)}, chosen by you at ${clock(chosen.at)}. This is recorded in this comparison only.`), show, clear);
  }
  let pending = false, next: HTMLButtonElement | null = null;
  // An action's outcome is said where the person acted, beside the button they pressed, never at the bottom of a long
  // comparison out of view (F-L8-7: "Working input saved…" appeared below every alternative).
  const sayBeside = (button: HTMLElement) => { next?.remove(); next = null; button.after(status); };
  async function update(action: Record<string,unknown>, button: HTMLButtonElement, done = 'Saved.'): Promise<boolean> {
    if (pending) return false; pending=true; button.disabled=true; sayBeside(button); status.textContent='Saving this comparison action…';
    try {
      const result = await change(source, action);
      if (!same(result.comparison.input, input)) throw new Error('The shared input changed unexpectedly. Keep the original comparison.');
      const before = comparison.attempts.length, added = result.comparison.attempts.slice(before);
      const failed = action.action === 'prepare' ? added.find(a => a.arm === action.arm && a.outcome !== 'succeeded') : undefined;
      status.textContent = failed ? `${letter(failed.arm)} ${failed.outcome === 'failed' ? 'failed' : 'was cancelled'} while preparing: ${failed.says} This is recorded beside the other alternatives; their results are unchanged. Open the updated comparison to retry or continue.`
        : `${done} Open the updated comparison to continue.`;
      const opener=node('button','Open updated comparison');status.after(opener);next=opener;
      opener.addEventListener('click',()=>{void open(result.target).catch(e=>{status.textContent=words(e);});});
      opener.focus?.();
      return true;
    } catch(e) {status.textContent=words(e);button.disabled=false; return false;}
    finally {pending=false;}
  }
  function chooseButton(attempt: Attempt, place: HTMLElement): void {
    if (attempt.outcome !== 'succeeded' || !attempt.result) return;
    const result = attempt.result;
    if (comparison.chosen && same(comparison.chosen.result, result)) { place.append(node('strong', 'Preferred result')); return; }
    const ask = node('button', 'Choose as preferred result'), confirm = node('button', `Confirm ${letter(attempt.arm)} as preferred`);
    const note = node('p', `Record ${letter(attempt.arm)}'s result as this comparison's preferred result? Nothing else changes: tools, their settings and future runs keep their own defaults.`);
    confirm.hidden = note.hidden = true;
    ask.addEventListener('click', () => { ask.hidden = true; confirm.hidden = note.hidden = false; });
    confirm.addEventListener('click', () => { void update({action:'choose',arm:attempt.arm,result:result.id,resultRevision:result.revision}, confirm, `${letter(attempt.arm)} recorded as preferred.`); });
    place.append(ask, note, confirm);
  }
  for (const arm of comparison.arms) {
    const row=node('article'); row.className='scientific-comparison-arm';
    const setup=node('a','Prepare or repair this integration'); setup.href=`#models/${encodeURIComponent(arm.owner)}`;
    const action=node('button',arm.scene?'Open controls for this alternative':'Create separate working input');
    const tries = comparison.attempts.filter(a => a.arm === arm.id), latest = tries.length > 0;
    const state = latest ? `${tries.length === 1 ? 'One attempt' : `${tries.length} attempts`} kept below, newest first.` : arm.scene ? 'Input retained. Use the tool’s controls to configure, run and save its results.' : 'This alternative has not been opened. Preparation can be completed without losing this comparison.';
    row.append(node('h4',`${letter(arm.id)} · ${arm.name}`),node('p',state),action,setup);
    action.addEventListener('click',()=>{if(arm.scene)void open({id:arm.scene.id,version:arm.scene.revision,title:`${letter(arm.id)} · ${arm.name}`}).catch(e=>{status.textContent=words(e);});else void update({action:'prepare',arm:arm.id},action,'Working input saved.');});
    for (const attempt of tries.slice().reverse()) {
      const card = node('section'); card.className = `scientific-comparison-attempt ${attempt.outcome}`;
      card.append(node('p', outcomeWords(attempt)));
      for (const line of [settingsWords(attempt), device(attempt.model), outputsWords(attempt), timingWords(attempt)]) if (line) card.append(node('p', line));
      if (attempt.result) {
        const result = attempt.result, inspect = node('button', `Inspect ${letter(attempt.arm)} result · revision ${result.revision}`);
        inspect.addEventListener('click', () => { void open({id:result.id,version:result.revision,title:`${letter(attempt.arm)} result`}).catch(e=>{status.textContent=words(e);}); });
        card.append(inspect); chooseButton(attempt, card);
      }
      row.append(card);
    }
    const record = node('details'), outcome = node('select'), say = node('textarea'), save = node('button', 'Record for this alternative');
    for (const [value, text] of [['failed', 'It failed'], ['cancelled', 'I cancelled it']]) { const option = node('option', text); option.value = value; outcome.append(option); }
    outcome.value = 'failed'; outcome.setAttribute('aria-label', `Outcome for ${letter(arm.id)}`); say.setAttribute('aria-label', `What happened in ${letter(arm.id)}`);
    say.placeholder = 'What happened, for example the message the tool showed'; say.maxLength = 1000;
    record.append(node('summary', 'Record a failure or cancellation'), node('p', 'Recorded as yours, beside this alternative’s other attempts. Saved results stay.'), outcome, say, save);
    const identity = request();
    save.addEventListener('click', () => { void update({action:'settle',arm:arm.id,outcome:outcome.value,says:say.value,request:identity}, save, `${letter(arm.id)} ${outcome.value === 'failed' ? 'failure' : 'cancellation'} recorded.`); });
    const details=node('details');details.append(node('summary','Pinned integration'),node('p',`${arm.capability} · ${arm.generation}`));row.append(record,details);root.append(row);
  }
  // A v1 comparison retained results before attempts existed: they stay openable as they were.
  for(const row of comparison.retained.filter(r=>!comparison.attempts.some(a=>a.result&&same(a.result,r.result)))){const show=node('button',`Open retained ${letter(row.arm)} result · revision ${row.result.revision}`);show.addEventListener('click',()=>{void open({id:row.result.id,version:row.result.revision,title:`Retained ${letter(row.arm)} result`}).catch(e=>{status.textContent=words(e);});});root.append(show);}
  root.append(node('p','Save tool and all layers in an alternative, then check its saved results here and keep them. Failed or cancelled work is not a low-quality result. Model agreement does not establish accuracy.'),refresh,status,results);
  refresh.addEventListener('click',()=>{void(async()=>{
    if(pending)return;pending=true;refresh.disabled=true;refresh.after(status);status.textContent='Checking accepted results…';
    try{
      const value=await reply(await fetch(`/api/scientific-projects/${encodeURIComponent(source.id)}/comparison-results?${new URLSearchParams({session:source.day,revision:String(source.version)})}`));
      if(!Array.isArray(value.results))throw new Error('Saved results are unavailable. Retry this check.');
      results.replaceChildren();
      for(const item of value.results){
        if(!object(item)||typeof item.arm!=='string'||!comparison.arms.some(a=>a.id===item.arm)||!exact(item.result)||item.result.session!==source.day||typeof item.title!=='string'||typeof item.retained!=='boolean')throw new Error('A result has an invalid association. Keep the original comparison.');
        const kept = comparison.attempts.some(a => a.result && same(a.result, item.result as Exact));
        const target={id:item.result.id,version:item.result.revision,title:item.title};
        const row=node('article'),inspect=node('button',`Inspect ${letter(item.arm)} · ${item.title}`),keep=node('button',kept?'Kept':'Keep with its settings and timing');keep.disabled=kept;
        inspect.addEventListener('click',()=>{void open(target).catch(e=>{status.textContent=words(e);});});
        keep.addEventListener('click',()=>{void update({action:'retain',arm:item.arm,result:target.id,resultRevision:target.version},keep,`${letter(String(item.arm))} result kept.`);});
        const settings = node('p', item.configuration === 'retained-tool-state' ? 'Tool settings retained. Keep it to record them beside this alternative.' : 'Producing settings were not retained. This result is inspectable, but its configuration cannot be reproduced from this comparison.');
        row.append(inspect,settings,keep);results.append(row);
      }
      status.textContent=value.results.length?'Saved results are available to inspect and keep.':'No saved results yet. Open an alternative, run it and save its tool and layers.';
    }catch(e){status.textContent=words(e);}finally{pending=false;refresh.disabled=false;}
  })();});
}
