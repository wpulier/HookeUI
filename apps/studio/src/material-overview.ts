/** Render only retained inspection facts; never infer scientific meaning from a filename. */
import type {MatrixInspectionFacts, MatrixPreview, ProjectMaterial} from './data-roles';
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='',cls='')=>{const n=document.createElement(tag);n.textContent=text;n.className=cls;return n;};
export function matrixPreview(preview:MatrixPreview):HTMLElement {
 const section=node('section','','material-preview');
 section.append(node('h4','Preview'),node('p',`First ${preview.observations.length} observations × ${preview.variables.length} variables. Positional slice, not representative sampling.`,'rd-note'));
 if(preview.integerEncoding)section.append(node('p','Integers beyond the browser’s exact numeric range are preserved as decimal text.','rd-note'));
 const scroll=node('div','','material-preview-scroll');scroll.tabIndex=0;scroll.setAttribute('aria-label','Matrix preview; scroll to see columns');
 const table=node('table'),head=node('tr');head.append(node('th','Observation'));
 for(const label of preview.variables){const th=node('th',label);th.scope='col';head.append(th);}const thead=node('thead');thead.append(head);table.append(thead);
 const body=node('tbody');preview.values.forEach((values,i)=>{const row=node('tr');const label=node('th',preview.observations[i]);label.scope='row';row.append(label);for(const value of values)row.append(node('td',String(value)));body.append(row);});table.append(body);scroll.append(table);section.append(scroll);
 for(const [title,annotations] of [['Observation annotations',preview.observationAnnotations],['Variable annotations',preview.variableAnnotations]] as const){
  if(!annotations.length)continue;const details=node('details');details.append(node('summary',`${title} · ${annotations.length} previewed`));
  for(const annotation of annotations)details.append(node('p',`${annotation.name}: ${annotation.values.map(v=>v===null?'—':String(v)).join(', ')}`));section.append(details);
 }
 return section;
}
export function inspectionFacts(facts:MatrixInspectionFacts):HTMLElement{
 const section=node('section','','material-inspection-facts');
 if(facts.shape?.length===2)section.append(node('p',`${facts.shape[0].toLocaleString()} observations × ${facts.shape[1].toLocaleString()} variables`));
 const labels=[facts.rawCounts===true?'Whole nonnegative counts verified':'',facts.sparse===true?'Sparse matrix':'',facts.dtype||''].filter(Boolean);
 if(labels.length)section.append(node('p',labels.join(' · '),'rd-note'));
 if(facts.observationColumns?.length)section.append(node('p',`Observation annotations: ${facts.observationColumns.join(', ')}`,'material-annotations'));
 if(facts.variableColumns?.length)section.append(node('p',`Variable annotations: ${facts.variableColumns.join(', ')}`,'material-annotations'));
 if(facts.preview)section.append(matrixPreview(facts.preview));
 else section.append(node('p','This inspection predates the preview. Inspect again to retain a new preview; earlier reports stay saved.','rd-note'));
 return section;
}
export function materialSummary(material:ProjectMaterial):HTMLElement{
 const section=node('section','','material-summary');section.append(node('h3','What’s inside'));
 if(material.inspection?.status==='conflicting')section.append(node('p','Inspection reports disagree. Review each report before using these dimensions.','material-conflict'));
 else if(material.shape?.length===2){const stats=node('div','','material-dimensions');for(const [i,label] of ['Observations','Variables'].entries()){const box=node('div');box.append(node('strong',material.shape[i].toLocaleString()),node('span',label));stats.append(box);}section.append(stats);}
 if(material.organization?.role)section.append(node('p',`Project role · ${material.organization.role}`,'material-role'));
 if(!material.inspection)section.append(node('p','Preview isn’t available yet. The saved file remains available to download.','rd-note'));
 return section;
}
