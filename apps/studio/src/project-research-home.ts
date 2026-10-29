/** A retained Study makes an empty stage a return point, rather than new-user onboarding. */
import {studyName, studyState, type Effort} from './research-efforts';
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '', className = '') => {
  const item = document.createElement(tag); item.textContent = text; item.className = className; return item;
};
type Actions = {current(): string; openStudy(id: string): void; data(): void; inputs(): void};
export class ProjectResearchHome {
  readonly root = node('section');
  private session = '';
  private signature = '';
  private opening = 0;
  private readonly host: HTMLElement;
  private readonly actions: Actions;
  constructor(host: HTMLElement, actions: Actions) {
    this.host = host; this.actions = actions;
    this.root.className = 'project-research-home';
    this.root.setAttribute('aria-label', 'Research in this Project');
    this.root.hidden = true; host.append(this.root);
  }
  update(session: string, studies: readonly Effort[], error = ''): void {
    const owned = studies.filter(study => study.session === session);
    const signature = JSON.stringify([session, owned, error]);
    if (signature === this.signature) return;
    this.signature = signature;
    if (this.session !== session) this.opening++;
    this.session = session;
    const opening = this.opening;
    this.root.hidden = !owned.length;
    this.host.dataset.projectResearch = String(owned.length > 0);
    this.root.replaceChildren();
    if (!owned.length) return;
    if (error) { const notice = node('p', `Showing the last saved overview. ${error}`, 'project-research-notice'); notice.setAttribute('role', 'status'); this.root.append(notice); }
    const action = (label: string, run: () => void, className = 'quiet') => {
      const button = node('button', label, className); button.type = 'button';
      button.addEventListener('click', () => { if (this.opening === opening && this.session === session && this.actions.current() === session) run(); });
      return button;
    };
    for (const study of owned) {
      const open = () => this.actions.openStudy(study.id);
      const card = node('article', '', 'project-research-summary');
      const header = node('div', '', 'project-research-heading');
      header.append(node('span', 'Research study', 'project-research-eyebrow'));
      const state = node('span', studyState(study), 'project-research-state');
      state.title = 'Study status; individual experiments may be running, finished or awaiting review.';
      header.append(state);
      card.append(header, node('h3', studyName(study)));
      const question = node('details', '', 'project-research-question');
      question.append(node('summary', 'Question & success criteria'), node('p', study.objective));
      if (study.criteria) question.append(node('p', study.criteria, 'project-research-criteria'));
      card.append(question);
      const stats = node('div', '', 'project-research-stats');
      const experiments = Object.keys(study.experiments ?? {}).length + Object.keys(study.tasks ?? {}).length;
      for (const [label, value, run] of [
        ['Inputs', study.inputs.length, this.actions.data], ['Experiments', experiments, open],
        ['Reports', study.claims?.length ?? 0, open], ['Findings', study.findings.length, open],
      ] as const) {
        const button = action('', run, 'project-research-stat');
        button.setAttribute('aria-label', `${value} ${label.toLowerCase()}: ${label === 'Inputs' ? 'browse Project data' : 'open Study'}`);
        button.append(node('strong', String(value)), node('span', label)); stats.append(button);
      }
      card.append(stats);
      const latest = study.claims?.at(-1);
      if (latest) {
        const report = node('details', '', 'project-research-report');
        const summary = node('summary');
        const meta = node('span', '', 'project-research-report-meta');
        meta.append(node('span', 'Latest report'), node('span', latest.byLabel || 'Author not recorded'));
        summary.append(meta, node('span', latest.statement, 'project-research-report-preview'), node('span', 'Read report', 'project-research-report-link'));
        report.append(summary, node('p', latest.statement, 'project-research-report-full'));
        if (latest.limitations) report.append(node('p', latest.limitations, 'project-research-criteria'));
        report.append(node('p', 'Researcher report · not an accepted finding', 'project-research-caption'));
        card.append(report);
      } else {
        card.append(node('p', 'Reports will appear here as the work develops.', 'project-research-empty'));
      }
      const footer = node('div', '', 'project-research-study-footer');
      const authors = [...new Set([...(study.claims ?? []).map(claim => claim.byLabel), ...Object.values(study.experiments ?? {}).map(experiment => experiment.byLabel)].filter(Boolean))];
      const contributors = node('p', authors.length ? `Contributions by ${authors.slice(0, 3).join(' · ')}${authors.length > 3 ? ` +${authors.length - 3}` : ''}` : 'Your research stays in this Project.', 'project-research-contributors');
      contributors.title = authors.join(' · ');
      const enter = action('Open Study', open, 'mm-primary');
      enter.title = 'Inspect experiments, evidence and reports in a retained Study tab. Opening does not start work.';
      footer.append(contributors, enter); card.append(footer); this.root.append(card);
    }
    const choices = node('div', '', 'project-research-actions');
    const data = action('Project data', this.actions.data);
    const inputs = action('Add material', this.actions.inputs);
    inputs.title = 'Open Inputs to import material or choose a camera.';
    choices.append(data, inputs); this.root.append(choices);
  }
}
