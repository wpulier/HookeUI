/** Explicit Add material completes before navigation. Restoration never calls this. */
export class SourceIntake {
  private selection = 0;
  private currentProject: () => string;
  private retain: (asset: string, session: string) => Promise<{ ok?: boolean; error?: string | null }>;
  private currentOpening: () => number;
  constructor(currentProject: () => string,
    retain: (asset: string, session: string) => Promise<{ ok?: boolean; error?: string | null }>,
    currentOpening: () => number = () => 0) {
    this.currentProject = currentProject;
    this.retain = retain;
    this.currentOpening = currentOpening;
  }

  async prepare(asset: string, session: string, alreadyRetained: boolean): Promise<boolean> {
    const selection = ++this.selection;
    const opening = this.currentOpening();
    if (this.currentProject() !== session) throw new Error('Return to this Project before adding material.');
    if (asset !== 'live' && !alreadyRetained) {
      let result: { ok?: boolean; error?: string | null };
      try { result = await this.retain(asset, session); }
      catch (error) { if (selection !== this.selection) return false; throw error; }
      if (selection !== this.selection) return false;
      if (result.ok === false) throw new Error(result.error || 'The material could not be added.');
    }
    if (selection !== this.selection) return false;
    if (this.currentProject() !== session || this.currentOpening() !== opening)
      throw new Error('The material was kept in its Project. Choose it again to open it.');
    return true;
  }
}
