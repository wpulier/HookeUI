import type { AssetMeta, ResultMessage } from './protocol';
export interface PreparedTry extends ResultMessage {
  token: string; session: string; action: 'ask' | 'click' | 'voice' | 'configure'; words: string; label: string; capability?: string;
  asset: AssetMeta | null;
}
interface Ports {
  session: () => string; empty: () => boolean; ready: () => boolean;
  prepare: (job: string, option: string, empty: boolean) => Promise<PreparedTry>;
  open: (asset: AssetMeta, session: string) => Promise<boolean>;
  now: () => number; wait: () => Promise<void>; show: () => void; guide: (words: string) => void;
  ask: (words: string, token: string) => boolean; voice: (token: string, option: string) => Promise<void>;
  configure: (capability: string, role: string, option: string) => void;
}
/** Guided first use shares the ordinary source, Send, gesture and Talk paths. It never edits defaults. */
export class CapabilityTry {
  private click?: { token: string; session: string; until: number };
  private generation = 0;
  private readonly ports: Ports;
  constructor(ports: Ports) { this.ports = ports; }
  async start(job: string, option: string, prepareExample = true): Promise<void> {
    const p = this.ports, generation = ++this.generation, session = p.session();
    this.click = undefined;
    const empty = p.empty();
    if (empty && !prepareExample) throw new Error('Choose a picture or video before trying this capability.');
    const trial = await p.prepare(job, option, empty);
    const current = () => {
      if (generation !== this.generation || p.session() !== session || trial.session !== session)
        throw new Error('The project changed. Press Try it in the conversation now open.');
    };
    current();
    if (trial.asset && !await p.open(trial.asset, session)) throw new Error('The example could not be opened. Try again.');
    current();
    // The source command is acknowledged before the first new frame is shown. Never ask about the previous picture.
    const deadline = p.now() + 8000;
    while (!p.ready()) {
      if (p.now() >= deadline) throw new Error('The example has not reached the view yet. Reconnect and try again.');
      await p.wait(); current();
    }
    p.show(); p.guide(`${trial.label}: ${trial.words}`);
    if (trial.action === 'configure') {
      if (!trial.capability) throw new Error('The operation form is missing. Reopen this capability after refreshing.');
      p.configure(trial.capability, job, option);
    }
    else if (trial.action === 'click') this.click = { token: trial.token, session, until: p.now() + 150000 };
    else if (trial.action === 'voice') await p.voice(trial.token, option);
    else if (!p.ask(trial.words, trial.token)) throw new Error('The request was not sent. Reconnect and try again.');
  }
  takeClick(): string | undefined {
    const pending = this.click; this.click = undefined;
    return pending && pending.session === this.ports.session() && pending.until > this.ports.now() ? pending.token : undefined;
  }
  hasClick(): boolean {
    return !!this.click && this.click.session === this.ports.session() && this.click.until > this.ports.now();
  }
}
