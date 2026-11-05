import type {CapabilitiesWire, RoleOption} from './protocol';

type Wire = Pick<CapabilitiesWire, 'roles'> | null;

const chosen = (wire: Wire, id: string): RoleOption | undefined => {
  const role = wire?.roles.find(item => item.id === id);
  return role?.options.find(item => item.id === role.current);
};

/** Same readiness for the main composer and Study handoff; connection grants are not model readiness. */
export function assistantBlockReason(wire: Wire, connected: boolean, pending: boolean): string | null {
  if (!connected) return 'Reconnect to Hooke before asking. Your question stays here.';
  if (pending) return 'The Assistant is answering your previous question.';
  const model = chosen(wire, 'vision');
  if (!model) return 'Checking the Assistant’s model. Your question stays here.';
  if (!model.available) return `${model.label}: ${model.readiness?.word || model.why || 'unavailable in this workspace'}.`;
  return null;
}

/** The composer's model chip: the models that answer a typed question, in the order they answer (brain/loop.py). The
 * Quick decisions model answers first, from the workspace notes, when it is on and can run; otherwise the full look
 * answers in its place. The Seeing model answers whenever the question needs a look. The chip has room for one name, so
 * it says the first by its one name, "+1" for the other, and both in full in its tooltip; one model in both jobs is
 * named once. A Seeing model without its key is the one named: nothing is sent until it has one (assistantBlockReason).
 * The chip opens the Models defaults, where each job's model is chosen. */
export function assistantChip(wire: Wire): {words: string; more: string; title: string; needs: string} {
  const seeing = chosen(wire, 'vision');
  if (!seeing) return {words: '', more: '', title: 'Choose the models for each job', needs: ''};
  // A chosen model without its key says so here, before the first question finds out (readiness: its next step is the key).
  const key = !seeing.available && seeing.readiness?.next?.kind === 'key' ? seeing.readiness : null;
  if (key) return {words: seeing.label, more: '', needs: 'needs a key',
    title: `${seeing.label}: ${key.word.charAt(0).toLowerCase()}${key.word.slice(1)}. Add it in Models › Keys, or choose the models for each job`};
  const fast = chosen(wire, 'fast');
  const quick = fast && fast.id !== 'off' && fast.available && fast.label !== seeing.label ? fast : null;
  const said = (model: RoleOption) => model.said ?? model.label;
  if (!quick) return {words: seeing.label, more: '', needs: '', title: `The Assistant answers with ${said(seeing)}. Choose the models for each job`};
  return {words: quick.label, more: '+1', needs: '',
    title: `The Assistant answers with ${said(quick)} from the workspace notes, and with ${said(seeing)} when it needs to look. Choose the models for each job`};
}
