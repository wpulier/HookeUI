import {Composer} from './composer';
import './launcher-assistant.css';

interface LauncherActions {
  activate: (id: string) => void | Promise<unknown>;
  recent: () => {id: string; name: string; when?: string}[];
  choices: () => Record<string, string>;
  context?: () => string;
  visible: () => boolean;
}

interface Transfer {
  session: string;
  visit: string;
  count: number;
  pending?: Promise<void>;
  error?: string;
}

interface Dictation {
  onresult: ((event: {results: ArrayLike<ArrayLike<{transcript:string}>>}) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: {error: string}) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const result = document.createElement(tag);
  result.textContent = text;
  return result;
};

async function postLauncherMessage(operation: string, value: object, signal?: AbortSignal) {
  const token = (window as Window & {hookeStudioToken?: string}).hookeStudioToken;
  const headers: Record<string, string> = {'Content-Type':'application/json', 'X-MicroscopeMind':'launcher-assistant'};
  if (token) headers['X-Hooke-Studio'] = token;
  const response = await fetch('/api/launcher-assistant/' + operation, {
    method:'POST', body:JSON.stringify(value), headers, signal, cache:'no-store',
  });
  const reply = await response.json();
  if (!response.ok || !reply.ok) throw new Error(reply.says || 'Hooke could not finish the request.');
  return reply;
}

/** A visit has no Project until the chosen starter or existing Project successfully opens. */
export class LauncherAssistant {
  private visit: string | null = null;
  private exchanges: {question:string; answer:string; action:string | null}[] = [];
  private pending: AbortController | null = null;
  private speech: Dictation | null = null;
  private readonly transfers = new Map<string, Transfer>();
  private recovery?: {host:HTMLElement; current:()=>string};
  private draft = '';
  private clearUI?: () => void;

  leave(): void {
    this.pending?.abort();
    this.pending = null;
    this.speech?.abort();
    this.speech = null;
  }

  reset(): void {
    this.leave();
    this.visit = null;
    this.exchanges = [];
    this.draft = '';
    this.clearUI?.();
  }

  async adopt(session: string): Promise<void> {
    this.leave();
    const visit = this.visit;
    const count = this.exchanges.length;
    this.reset();
    if (!visit) return;
    const transfer: Transfer = {session, visit, count};
    this.transfers.set(visit, transfer);
    await this.save(transfer);
  }

  private save(transfer: Transfer): Promise<void> {
    if (transfer.pending) return transfer.pending;
    transfer.error = undefined;
    transfer.pending = postLauncherMessage('attach', {session:transfer.session, visit:transfer.visit, count:transfer.count})
      .then(() => { this.transfers.delete(transfer.visit); })
      .catch((error: unknown) => {
        transfer.error = error instanceof Error ? error.message : 'The conversation could not be saved.';
        throw error;
      })
      .finally(() => { transfer.pending = undefined; this.refreshRecovery(); });
    this.refreshRecovery();
    return transfer.pending;
  }

  mountRecovery(host: HTMLElement, current: () => string): void {
    this.recovery = {host, current};
    this.refreshRecovery();
  }

  refreshRecovery(): void {
    if (!this.recovery) return;
    const {host, current} = this.recovery;
    const transfers = [...this.transfers.values()].filter(item => item.session === current());
    host.hidden = !transfers.length;
    host.replaceChildren(...transfers.map(item => {
      const row = node('div');
      row.setAttribute('role', 'status');
      if (item.pending) { row.textContent = 'Saving your launcher conversation…'; return row; }
      const retry = node('button', 'Retry saving conversation');
      retry.type = 'button';
      retry.addEventListener('click', () => {
        if (current() === item.session) void this.save(item).catch(() => {/* the reason stays in the panel */});
      });
      row.append(node('p', item.error), retry);
      return row;
    }));
  }

  mount(host: HTMLElement, actions: LauncherActions): void {
    this.leave();
    host.classList.add('launcher-assistant');
    host.setAttribute('aria-label', 'Hooke Assistant');
    const title = node('h2', 'Ask Hooke');
    const history = node('div');
    history.className = 'launcher-assistant-history';
    history.setAttribute('role', 'log');
    const status = node('p');
    status.className = 'launcher-assistant-status';
    status.setAttribute('role', 'status');
    const speak = node('button', 'Dictate');
    speak.type = 'button';
    speak.title = 'Dictate a message to Hooke';
    const composer = new Composer(null, {label:'Message to Hooke', placeholder:'Ask about a challenge, or tell Hooke where to start…', extra:[speak]});
    composer.set(this.draft);
    composer.field.maxLength = 4000;
    composer.field.addEventListener('input', () => { this.draft = composer.field.value; });

    function choices(): Record<string,string> {
      const recent = actions.recent().slice(0, 30).map(row => ['recent:' + row.id, (row.name + (row.when ? ' · ' + row.when : '')).slice(0, 350)]);
      const displayed = actions.choices();
      for (const [id, label] of recent) if (Object.hasOwn(displayed, id)) displayed[id] = label;
      return displayed;
    }
    const display = () => {
      const ids = choices();
      const lines = this.exchanges.flatMap(({question, answer, action}, i) => {
        const user = node('p', question);
        user.className = 'launcher-assistant-user';
        const message = node('p', answer);
        const suggestion = action && ids[action] && i === this.exchanges.length - 1 ? node('button', ids[action]) : null;
        if (!suggestion || !action) return [user, message];
        suggestion.type = 'button';
        suggestion.addEventListener('click', () => { if (actions.visible() && !this.pending) actions.activate(action); });
        return [user, message, suggestion];
      });
      history.replaceChildren(...lines);
      history.hidden = !lines.length;
      history.scrollTop = history.scrollHeight;
    };
    let channel: 'voice' | 'chat' = 'chat';
    speak.addEventListener('click', () => {
      if (this.speech) { this.speech.stop(); return; }
      const w = window as Window & {SpeechRecognition?: new () => Dictation; webkitSpeechRecognition?: new () => Dictation};
      const recognize = w.SpeechRecognition || w.webkitSpeechRecognition;
      composer.field.focus();
      if (!recognize) {
        status.textContent = 'Use your Mac’s Dictation shortcut in this field, then press Return to send.';
        return;
      }
      const speech = new recognize();
      this.speech = speech;
      speech.onresult = event => {
        if (this.speech !== speech || !actions.visible()) return;
        const words = Array.from(event.results, item => item[0]?.transcript || '').join(' ').trim();
        if (words) { this.draft = [composer.field.value, words].filter(Boolean).join(' '); composer.set(this.draft); channel = 'voice'; }
      };
      speech.onerror = event => {
        if (this.speech === speech && event.error !== 'aborted') status.textContent = 'Dictation is unavailable. Type here or use your Mac’s Dictation shortcut.';
      };
      speech.onend = () => {
        if (this.speech !== speech) return;
        this.speech = null;
        speak.textContent = 'Dictate';
        if (status.textContent === 'Listening…') status.textContent = 'Review your words, then send.';
      };
      try { speech.start(); speak.textContent = 'Stop dictating'; status.textContent = 'Listening…'; }
      catch { this.speech = null; status.textContent = 'Use your Mac’s Dictation shortcut in this field, then send.'; }
    });
    composer.root.addEventListener('submit', async event => {
      event.preventDefault();
      const question = composer.field.value.trim();
      if (!question || !actions.visible() || this.pending) return;
      this.speech?.abort();
      const controller = new AbortController();
      this.pending = controller;
      composer.send.disabled = true;
      composer.field.readOnly = true;
      speak.disabled = true;
      status.textContent = 'Hooke is thinking…';
      try {
        const reply = await postLauncherMessage('ask', {
          question, visit:this.visit, channel, choices:choices(), context:(actions.context?.() ?? '').slice(0, 6000),
        }, controller.signal);
        if (controller.signal.aborted || !actions.visible()) return;
        this.visit = reply.visit;
        const action = typeof reply.action === 'string' && Object.hasOwn(choices(), reply.action) ? reply.action : null;
        // OS file pickers require a user gesture. Mutating choices always get the same visible confirmation
        // as the button; navigation to a recent Project or a preview can happen at the user's direct request.
        const navigate = action && reply.requested && (action.startsWith('recent:')
          || ['light-my-cells-2', 'onion-root', 'rosette', 'remote-telescope', 'remote-microscope', 'own-telescope',
             'own-microscope', 'close-preview', 'research-link'].includes(action));
        this.exchanges.push({question, answer:reply.answer, action:navigate ? null : action});
        this.draft = '';
        composer.set('');
        channel = 'chat';
        display();
        status.textContent = '';
        if (navigate) await actions.activate(action);
      } catch (error) {
        if (!controller.signal.aborted) status.textContent = error instanceof Error ? error.message : 'Hooke could not answer. You can still choose any starting point.';
      } finally {
        if (this.pending === controller) {
          this.pending = null;
          composer.send.disabled = false; composer.field.readOnly = false; speak.disabled = false;
        }
      }
    });
    this.clearUI = () => {
      composer.set(''); status.textContent = ''; channel = 'chat'; speak.textContent = 'Dictate';
      composer.send.disabled = false; composer.field.readOnly = false; speak.disabled = false;
      display();
    };
    display();
    host.replaceChildren(title, history, composer.root, status);
  }
}
