/** A retained Study makes an empty stage a return point, rather than new-user onboarding. */
import {studyName, studyState, type Effort} from './research-efforts';
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const item = document.createElement(tag); item.textContent = text; return item;
};
export class ProjectResearchHome {
  readonly root = node('section');
  private session = '';
  private signature = '';
  private opening = 0;
  constructor(private readonly host: HTMLElement, private readonly actions: {
    current(): string; openStudy(id: string): void; data(): void; inputs(): void;
  }) {
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
    this.root.append(node('p', 'Return to your question, inspect the evidence, or choose the next experiment.'));
    if (error) { const notice = node('p', `Showing the last saved overview. ${error}`); notice.setAttribute('role', 'status'); this.root.append(notice); }
    const action = (label: string, run: () => void, primary = false) => {
      const button = node('button', label); button.type = 'button';
      button.className = primary ? 'mm-primary' : 'quiet';
      button.addEventListener('click', () => { if (this.opening === opening && this.session === session && this.actions.current() === session) run(); });
      return button;
    };
    for (const study of owned) {
      const card = node('article'); card.className = 'project-research-summary';
      card.append(node('h3', studyName(study)), node('p', study.objective));
      const experiments = Object.keys(study.experiments ?? {}).length + Object.keys(study.tasks ?? {}).length;
      const reports = (study.claims?.length ?? 0) + study.findings.length;
      const count = (n: number, singular: string, plural = `${singular}s`) => `${n} ${n === 1 ? singular : plural}`;
      const facts = node('p', `${studyState(study)} · ${count(study.inputs.length, 'input')} · ${count(experiments, 'recorded experiment')} · ${count(reports, 'finding or claim', 'findings and claims')}`);
      facts.className = 'project-research-facts';
      card.append(facts, action('Open Study', () => this.actions.openStudy(study.id), owned.length === 1));
      this.root.append(card);
    }
    const choices = node('div'); choices.className = 'project-research-actions';
    choices.append(action('Browse Project data', this.actions.data), action('Add material or choose a camera', this.actions.inputs));
    this.root.append(choices, node('p', 'Continue with the Assistant, a connected agent, or the same tools yourself. Opening a Study does not start work.'));
  }
}
