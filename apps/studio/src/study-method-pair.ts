import {namedInputKeys, type Exact, type Workflow} from './workflow-model';

export type MethodPair = {baseline: Exact; challenger: Exact; input: Exact; bindings: Record<string, Exact>};
type DraftPair = Partial<MethodPair>;
type Choice = {reference: Exact; title: string};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, text = '') {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}
function choice(label: string, choices: Choice[], selected: Exact | undefined, change: (ref?: Exact) => void) {
  const field = element('label', label);
  const list = element('select');
  const empty = element('option', 'Choose…');
  empty.value = '';
  list.append(empty);
  for (const item of choices) {
    const option = element('option', item.title + ' · v' + item.reference.revision);
    option.value = item.reference.digest + ':' + item.reference.id;
    list.append(option);
  }
  list.value = selected ? selected.digest + ':' + selected.id : '';
  list.onchange = () => change(choices.find(item => list.value === item.reference.digest + ':' + item.reference.id)?.reference);
  field.append(list);
  return field;
}
function reference(w: Exact): Exact {
  return {id: w.id, session: w.session, revision: w.revision, digest: w.digest};
}

export function pairEditor(draft: HTMLInputElement, materials: Choice[], fetcher: typeof fetch, edited: () => void) {
  const section = element('section'), toggle = element('button'), controls = element('div');
  toggle.type = 'button';
  let methods: Workflow[] | undefined, error = '', loading = false;
  function render() {
    controls.replaceChildren();
    toggle.textContent = draft.value ? 'Use an ordinary plan' : 'Compare two methods';
    if (!draft.value) return;
    controls.append(element('p', 'Run a baseline, inspect its diagnostics and try one justified change. Score both on the same data and stop.'));
    let spec: DraftPair;
    try { spec = JSON.parse(draft.value); } catch { controls.append(element('p','Restore the saved comparison or choose the methods again.')); return; }
    function update(next: DraftPair) { draft.value = JSON.stringify(next); edited(); render(); }
    if (!methods) {
      if (error) {
        const retry = element('button','Retry workflow list'); retry.type='button';
        retry.onclick=()=>{error='';render();};
        controls.append(element('p',error),retry); return;
      }
      controls.append(element('p','Loading saved workflows…'));
      if (!loading) {
        loading=true;
        void fetcher('/api/workflows',{cache:'no-store'}).then(async r=>{
          if (!r.ok) throw new Error('Could not load saved workflows.');
          methods=(await r.json()).workflows;
          if (!Array.isArray(methods)) throw new Error('Workflow list unavailable.');
        }).catch(e=>{methods=undefined;error=String(e.message||e);}).finally(()=>{loading=false;render();});
      }
      return;
    }
    const choices=methods.filter(w=>!w.removed).map(w=>({reference:reference(w),title:w.name}));
    controls.append(choice('Baseline workflow',choices,spec.baseline,baseline=>update({...spec,baseline})),
      choice('Challenger workflow',choices,spec.challenger,challenger=>update({...spec,challenger})),
      choice('Shared input',materials,spec.input,input=>update({...spec,input})));
    const active=methods.filter(w=>[spec.baseline,spec.challenger].some(ref=>ref?.id===w.id && ref.digest===w.digest));
    const keys=new Set(active.flatMap(w=>namedInputKeys(w.graph)));
    for (const key of keys) controls.append(choice(key, materials,spec.bindings?.[key],ref=>{
      const bindings:Record<string,Exact>={};
      for(const k of keys) if(spec.bindings?.[k]) bindings[k]=spec.bindings[k];
      if(ref) bindings[key]=ref; else delete bindings[key];
      update({...spec,bindings});
    }));
  }
  toggle.onclick=()=>{draft.value=draft.value?'':JSON.stringify({bindings:{}});edited();render();};
  section.append(toggle,controls);
  return {section,render};
}

type ArmEvidence = {score: Exact; prediction: Exact};
export type MethodReport = {
  baseline: ArmEvidence; challenger: ArmEvidence; coverage: string;
  metrics: {name: string; direction: 'lower' | 'higher'; baseline: number; challenger: number}[];
};

export function comparisonResult(report: MethodReport, author: string, decision: string, limitations: string,
  inputs: Exact[], open: ((ref: Exact, name: string) => Promise<unknown>) | undefined, discuss?: () => void) {
  const section = element('section');
  section.className = 'study-comparison-result';
  section.setAttribute('aria-label', 'Method comparison');
  // Retain the evaluator's ordering even when its first measure was unchanged.
  const flat = report.metrics.filter(m => m.baseline === m.challenger);
  const lead = report.metrics[0];
  const intro=element('small','COMPARISON RESULTS');
  const metricTitle = lead?.name === 'Expression MAE' ? 'Average expression error' : lead?.name;
  const title = lead ? metricTitle + (lead.challenger === lead.baseline ? ' was unchanged' :
    lead.challenger < lead.baseline ? ' decreased' : ' increased') : 'Comparison results';
  const relative = lead && lead.baseline > 0 ? 100 * Math.abs(lead.challenger - lead.baseline) / lead.baseline : 0;
  section.append(intro,element('h3',title + (relative >= 0.1 && Number.isFinite(relative) ? ' by ' + Number(relative.toFixed(1)) + '%' : '')));
  if(lead) section.append(element('p',lead.baseline.toPrecision(4)+' → '+lead.challenger.toPrecision(4)+' · '+(lead.direction==='lower'?'Lower is better':'Higher is better')));
  if(flat.length) section.append(element('p','Unchanged: '+flat.map(m=>m.name+' ('+Number(m.baseline.toPrecision(4))+')').join(', ')+'.'));
  section.append(element('h4','Researcher’s recommendation'),element('p',decision),element('small',author));
  if (discuss) {
    const next = element('button', 'Discuss next experiment');
    next.className = 'study-quiet';
    next.type = 'button';
    next.onclick = discuss;
    section.append(next);
  }
  const status = element('p');
  status.setAttribute('role', 'status');
  const link = (ref: Exact, title: string, display = title) => {
    if (!open) return element('span', display);
    const button = element('button', display);
    button.type = 'button';
    button.title = title;
    button.setAttribute('aria-label', display + ' · ' + title);
    button.onclick = () => { void open(ref, title).catch(error => { status.textContent = String(error); }); };
    return button;
  };
  const table = element('table');
  const headGroup = element('thead'), head = element('tr');
  headGroup.append(head); table.append(headGroup);
  for (const title of ['Measure', 'Baseline', 'Challenger', 'Change']) head.append(element('th', title));
  const body = element('tbody'); table.append(body);
  const fmt = (value: number) => Number(value.toPrecision(6)).toString();
  for (const metric of report.metrics) {
    const row = element('tr'); body.append(row);
    row.append(element('th', metric.name + (metric.direction === 'lower' ? ' ↓' : ' ↑')));
    for (const role of ['baseline', 'challenger'] as const) {
      const cell = element('td');
      cell.append(link(report[role].score, 'Open ' + role + ' scoring report', fmt(metric[role])));
      row.append(cell);
    }
    const delta = metric.challenger - metric.baseline;
    row.append(element('td', (delta > 0 ? '+' : '') + fmt(delta)));
  }
  section.append(table, element('p', report.coverage), element('p', limitations));
  const evidence = element('details');
  evidence.append(element('summary', 'Predictions and shared inputs'), link(report.baseline.prediction, 'Baseline prediction'),
    link(report.challenger.prediction, 'Challenger prediction'));
  inputs.forEach((ref, index) => evidence.append(link(ref, 'Shared input ' + (index + 1) + ' · v' + ref.revision)));
  section.append(evidence, status);
  return section;
}

export type Candidate = {name:string;status:string;outputs:{step:string;reference:Exact}[]};
export function scoreFields(candidates: Record<'baseline'|'challenger',Candidate>, files: Choice[]) {
  const section=element('fieldset'), picked: Partial<Record<'baseline'|'challenger', Partial<ArmEvidence>>>={};
  section.append(element('legend','Compare frozen outputs'));
  section.append(element('p','Select the predictions and their retained scoring reports. Hooke checks that the scores used the same evaluation data.'));
  for(const role of ['baseline','challenger'] as const){
    const group=element('div'), selected:Partial<ArmEvidence>={};
    picked[role]=selected;
    group.append(element('h4',candidates[role].name),
      choice('Prediction',candidates[role].outputs.map(o=>({title:o.step,reference:o.reference})),undefined,r=>{selected.prediction=r;}),
      choice('Scoring report',files,undefined,r=>{selected.score=r;}));
    section.append(group);
  }
  const field=(label:string)=>{const wrap=element('label',label),input=element('textarea');wrap.append(input);section.append(wrap);return input;};
  const coverage=field('What did this evaluation cover?'),interpret=field('What should we try next?'),limits=field('What limits this result?');
  function read(){
    const {baseline,challenger}=picked;
    if(!baseline?.score||!baseline.prediction||!challenger?.score||!challenger.prediction||
      !coverage.value.trim()||!interpret.value.trim()||!limits.value.trim())throw new Error('Choose both predictions and reports, then record coverage, recommendation and limitations.');
    return {baseline:{score:baseline.score,prediction:baseline.prediction},challenger:{score:challenger.score,prediction:challenger.prediction},
      coverage:coverage.value.trim(),statement:interpret.value.trim(),limitations:limits.value.trim()};
  }
  return {section,read};
}
