import { hostOf } from './notes';
import type { PresentationPlan, PresentationStyle, ResearchSession } from './protocol';
import { renderResearchChart } from './research-chart';
import { renderResearchCaptures } from './research-captures';

/** Trusted context stays outside bespoke HTML, whose styling cannot hide these caveats. */
export function renderArtifactContext(result: ResearchSession['result']): HTMLElement {
  const context = document.createElement('details');
  context.className = 'artifact-evidence-context';
  context.setAttribute('aria-label', 'Retained findings and limitations');
  const label = document.createElement('summary'); label.textContent = 'Retained findings and limitations'; context.append(label);
  const values = [...new Set([result?.summary, ...(result?.limitations ?? []), result?.report?.limitations].map(clean).filter(Boolean))];
  for (const value of values) {
    const paragraph = document.createElement('p');
    paragraph.textContent = clean(value);
    context.append(paragraph);
  }
  return context;
}

/** Researchers' text without the search tool's citation markers (private-use characters). */
export function clean(text: string | undefined | null): string {
  return String(text ?? '')
    .replace(/\uE200cite(?:\uE202[^\uE200-\uE2FF\s]*)*\uE201?/g, '')
    .replace(/[\uE000-\uF8FF]/g, '')
    .replace(/\s+([.,;:])/g, '$1')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

export function renderResearchReport(r: ResearchSession['result'], select?: (anchor: string) => void, openCapture?: (sequence:number,picture:number)=>void, capturePreview?: (sequence:number,picture:number)=>string): HTMLElement {
    if (r?.presentation) {
      const { presentation, ...finding } = r;
      return renderResearchPresentation(finding, presentation.plan, select, openCapture, capturePreview);
    }
    const wrap = document.createElement('div');
    wrap.className = 'viewer-summary';
    if (!r) return wrap;
    if (r.title) {
      const title = document.createElement('h2');
      title.textContent = clean(r.title);
      wrap.append(title);
    }
    if (r.findingRevision) {
      wrap.dataset.findingVersion = String(r.findingRevision.version);
      wrap.dataset.findingDigest = r.findingRevision.digest;
    }
    const p = document.createElement('p');
    p.dataset.anchor = 'summary';
    p.textContent = clean(r.summary);
    wrap.append(p);
    const revision = document.createElement('p');
    revision.className = 'sources';
    revision.textContent = r.report ? `Findings${r.findingRevision ? ` · revision ${r.findingRevision.version}` : ''}`
      : 'Summary only · a full findings record was not retained';
    wrap.append(revision);
    for (const [key, text] of Object.entries(r.report ?? {})) {
      const section = document.createElement('section');
      section.dataset.anchor = `report:${key}`;
      const heading = document.createElement('h3');
      heading.textContent = key[0].toUpperCase() + key.slice(1);
      const content = document.createElement('p');
      content.style.whiteSpace = 'pre-wrap';
      content.textContent = text;
      section.append(heading, content);
      if (select) section.append(selectionButton(`report:${key}`, select));
      wrap.append(section);
    }
    for (const [index, data] of (r.tables ?? []).entries()) {
      const scroll = document.createElement('div');
      scroll.className = 'research-table';
      scroll.dataset.anchor = `table:${index}`;
      scroll.tabIndex = 0;
      scroll.setAttribute('role', 'region');
      scroll.setAttribute('aria-label', data.title);
      const table = document.createElement('table');
      const title = document.createElement('caption');
      title.textContent = data.title;
      const head = document.createElement('thead');
      const headers = document.createElement('tr');
      for (const column of data.columns) {
        const th = document.createElement('th');
        th.scope = 'col';
        th.textContent = column;
        headers.append(th);
      }
      head.append(headers);
      const body = document.createElement('tbody');
      for (const row of data.rows) {
        const tr = document.createElement('tr');
        for (const value of row) {
          const td = document.createElement('td');
          td.textContent = value;
          tr.append(td);
        }
        body.append(tr);
      }
      table.append(title, head, body);
      scroll.append(table);
      if (select) scroll.append(selectionButton(`table:${index}`, select));
      wrap.append(scroll);
    }
    for (const [index, chart] of (r.charts ?? []).entries()) wrap.append(renderResearchChart(chart, index, select));
    wrap.append(...renderResearchCaptures(r.captures ?? [], select, openCapture, capturePreview));
    if (r.details?.length) {
      const ul = document.createElement('ul');
      ul.dataset.anchor = 'details';
      for (const d of r.details) {
        const li = document.createElement('li');
        li.textContent = clean(d);
        ul.append(li);
      }
      wrap.append(ul);
    }
    if (r.look_for) {
      const look = document.createElement('p');
      look.className = 'look-for';
      look.dataset.anchor = 'look_for';
      look.textContent = `Look for: ${clean(r.look_for).replace(/^look for:?\s*/i, '')}`;
      wrap.append(look);
    }
    for (const [title, items] of [["What it can't settle", r.limitations], ['Worth asking next', r.next_questions]] as const) {
      const lines = (items ?? []).map(clean).filter(Boolean);
      if (!lines.length) continue;
      const head = document.createElement('p');
      head.className = 'viewer-subhead';
      head.textContent = title;
      const ul = document.createElement('ul');
      for (const line of lines) {
        const li = document.createElement('li');
        li.textContent = line;
        ul.append(li);
      }
      const section = document.createElement('section');
      section.dataset.anchor = title === "What it can't settle" ? 'limitations' : 'next_questions';
      section.append(head, ul);
      wrap.append(section);
    }
    if (r.refs?.length) {
      const refs = document.createElement('div');
      refs.className = 'description-refs';
      for (const ref of r.refs) {
        const fig = document.createElement('figure');
        const a = link(ref.page, '', true);
        const img = document.createElement('img');
        img.src = ref.url;
        img.alt = ref.title || 'Reference picture';
        a.append(img);
        const cap = document.createElement('figcaption');
        cap.textContent = ref.credit ? `Reference, ${ref.credit}` : 'Reference picture';
        fig.append(a, cap);
        refs.append(fig);
      }
      wrap.append(refs);
    }
    if (r.sources?.length) {
      const src = document.createElement('p');
      src.className = 'sources';
      src.append('Sources: ');
      r.sources.forEach((url, i) => {
        if (i) src.append(', ');
        src.append(link(url, hostOf(url), true));
      });
      wrap.append(src);
    }
    const checks = [
      ...(r.claims ?? []).filter((claim) => claim.found !== true).map((claim) =>
        `${claim.found === false ? 'Not found in its source' : 'Source not checked'}: ${clean(claim.point)} (${hostOf(claim.source)})`),
      ...(r.session_claims ?? []).filter((claim) => claim.found !== true).map((claim) =>
        `${claim.found === false ? 'Evidence unavailable' : 'Evidence not checked'}: ${clean(claim.point)} (${claim.evidence.join(', ')})`),
    ];
    if (checks.length) {
      const section = document.createElement('section');
      section.dataset.anchor = 'evidence-checks';
      const heading = document.createElement('h3');
      heading.textContent = 'Evidence checks';
      const list = document.createElement('ul');
      for (const check of checks) {
        const item = document.createElement('li'); item.textContent = check; list.append(item);
      }
      section.append(heading, list); wrap.append(section);
    }
    return wrap;
}

/** A producer controls arrangement, while app code owns every displayed fact and source. */
export function renderResearchPresentation(result: ResearchSession['result'], plan: PresentationPlan,
  select?: (anchor: string) => void, openCapture?: (sequence:number,picture:number)=>void, capturePreview?: (sequence:number,picture:number)=>string): HTMLElement {
  const report = renderResearchReport(result, select, openCapture, capturePreview);
  const available = new Map([...report.children].filter((node): node is HTMLElement => !!(node as HTMLElement).dataset.anchor)
    .map((node) => [node.dataset.anchor!, node]));
  const refs = plan?.blocks?.map((block) => block.ref) ?? [];
  // The summary and the limitations, once (research_worker/presentation.py caveats): the list stands for the report's section.
  const limits = ['limitations', 'report:limitations'].find((ref) => available.has(ref));
  const mandatory = ['summary', limits].filter((ref): ref is string => !!ref && available.has(ref));
  if (!['report', 'comparison', 'briefing'].includes(plan?.layout) || !refs.length || refs.length > 24
    || new Set(refs).size !== refs.length || refs.some((ref) => !available.has(ref)) || mandatory.some((ref) => !refs.includes(ref))) {
    const warning = document.createElement('p'); warning.textContent = 'This presentation is invalid. Showing its retained findings.';
    report.prepend(warning); return report;
  }
  report.dataset.layout = plan.layout;
  const arranged = document.createElement('div'); arranged.className = 'research-presented-blocks';
  for (const ref of refs) arranged.append(available.get(ref)!);
  applyStyle(report, arranged, refs, available, plan.style);
  const rest = [...available.entries()].filter(([ref]) => !refs.includes(ref) && ref !== 'evidence-checks').map(([, node]) => node);
  // The title and version stay first; references and failed evidence checks stay visible after the blocks.
  const firstSource = [...report.children].find((node) => node.classList.contains('description-refs') || node.classList.contains('sources'));
  report.insertBefore(arranged, firstSource ?? report.firstElementChild?.nextSibling ?? null);
  if (rest.length) {
    const full = document.createElement('details');
    const heading = document.createElement('summary'); heading.textContent = 'All retained findings';
    full.append(heading, ...rest); arranged.after(full);
  }
  return report;
}

const STYLE_CHOICES = {
  theme: ['graphite', 'paper', 'contrast', 'accent'],
  density: ['compact', 'comfortable', 'spacious'],
  pictures: ['inline', 'lead', 'gallery'],
} as const;

/** The producer's look (checked again here: an unknown choice is left out, never guessed). The facts stay the app's. */
export function applyStyle(report: HTMLElement, arranged: HTMLElement, refs: string[], available: Map<string, HTMLElement>,
  style: PresentationStyle | undefined): void {
  if (!style) return;
  for (const key of ['theme', 'density', 'pictures'] as const) {
    const value = style[key];
    if (value && (STYLE_CHOICES[key] as readonly string[]).includes(value)) report.dataset[key] = value;
  }
  for (const ref of (style.emphasis ?? []).slice(0, 3)) {
    if (refs.includes(ref)) available.get(ref)?.classList.add('research-emphasis');
  }
  const pictures = refs.filter((ref) => ref.startsWith('capture:')).map((ref) => available.get(ref)!).filter(Boolean);
  if (!pictures.length) return;
  if (report.dataset.pictures === 'lead') {  // the pictures first, after the summary when it leads
    const others = [...arranged.children].filter((node) => !pictures.includes(node as HTMLElement)) as HTMLElement[];
    const at = others[0] === available.get('summary') ? 1 : 0;
    arranged.append(...others.slice(0, at), ...pictures, ...others.slice(at));
  } else if (report.dataset.pictures === 'gallery') {
    const gallery = document.createElement('div'); gallery.className = 'research-gallery';
    gallery.append(...pictures); arranged.append(gallery);
  }
}

function selectionButton(anchor: string, select: (anchor: string) => void): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button'; button.textContent = 'Ask about this';
  button.className = 'mm-control';
  button.addEventListener('click', () => select(anchor));
  return button;
}

function link(url: string, text: string, external: boolean): HTMLAnchorElement {
  const a = document.createElement('a');
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:' || parsed.protocol === 'http:') a.href = parsed.href;
  } catch { /* Keep unavailable source text readable, without an executable link. */ }
  if (external) {
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
  }
  a.textContent = text;
  return a;
}
