import type { ClientCommand, ResearchSession } from './protocol';

const requests = new Map<string, string>();
interface ResponseState {pending?:Promise<void>; accepted?:boolean; error?:string; listeners:Set<()=>void>}
const responses = new Map<string,ResponseState>();
/** Explicit answers use a durable request identity; only the original server acknowledgement confirms them. */
export function evidenceActions(session: ResearchSession, question: NonNullable<ResearchSession['requests']>[number],
  send: (command: ClientCommand) => unknown): HTMLElement {
  const actions = document.createElement('div'); actions.className = 'research-evidence-actions';
  const day = session.day ?? session.snapshot.split('/')[2];
  const identity = `research-evidence:${day}/${session.rid}/${question.run ?? 0}/${question.id}`;
  const state = responses.get(identity) ?? {listeners:new Set<()=>void>()}; responses.set(identity,state);
  const note = document.createElement('p'); note.setAttribute('role','status');
  const buttons:HTMLButtonElement[]=[];
  const refresh = () => {
    for (const button of buttons) button.disabled = !!state.pending || !!state.accepted;
    note.textContent = state.pending ? 'Sending response…' : state.accepted ? 'Response sent' : state.error ?? '';
    note.hidden = !note.textContent;
  };
  if (state.pending) state.listeners.add(refresh);
  // A keep-going question is answered yes with a note (the server's evidence answers are Hold and Not now only);
  // footage is held only when seconds were asked for.
  const choices = (question.keepGoing ? ['keep', 'decline'] : question.seconds ? ['hold', 'decline'] : ['decline']) as ('keep' | 'hold' | 'decline')[];
  for (const choice of choices) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = choice === 'decline' ? 'mm-control' : 'mm-primary';
    button.textContent = choice === 'keep' ? 'Keep going' : choice === 'hold' ? `Hold ${question.seconds} s` : question.keepGoing ? 'Not now' : 'Not now';
    button.title = choice === 'keep' ? 'Tell it to keep going; its allowance still applies.' : choice === 'hold' ? 'Hold the requested live frames for this researcher. Record remains yours.' : question.keepGoing ? 'Tell it not to go on now' : 'Continue without this evidence';
    button.addEventListener('click', () => {
      if (state.pending || state.accepted) return;
      const key = `${identity}/${choice}`;
      let requestId = requests.get(key);
      try { requestId ??= sessionStorage.getItem(key) ?? undefined; } catch { /* optional transport recovery */ }
      requestId ??= crypto.randomUUID(); requests.set(key, requestId);
      try { sessionStorage.setItem(key, requestId); } catch { /* the in-memory identity stays stable */ }
      state.error = undefined;
      state.listeners.add(refresh);
      state.pending = (async () => {
        try {
          const reply = await send(choice === 'keep'
            ? {type:'research-note', rid:session.rid, text:'Yes, keep going.', requestId, selection:{day, rid:session.rid}}
            : {type:'research-evidence', rid:session.rid, day, questionId:question.id, run:question.run ?? 0, choice, requestId});
          if (!reply || typeof reply !== 'object' || !('ok' in reply) || reply.ok !== true) {
            throw new Error(reply && typeof reply === 'object' && 'error' in reply ? String(reply.error) : 'No acknowledgement received. Try again to check the same response.');
          }
          if (!('requestId' in reply) || reply.requestId !== requestId) throw new Error('The response acknowledgement did not match. Try again to check the same response.');
          state.accepted = true;
        } catch (error) {
          state.error = error instanceof Error ? error.message : 'Could not send. Try again to check the same response.';
        }
      })().finally(() => {
        state.pending = undefined;
        for (const listener of state.listeners) listener();
        state.listeners.clear();
      });
      refresh();
    });
    buttons.push(button); actions.append(button);
  }
  actions.append(note); refresh(); return actions;
}
