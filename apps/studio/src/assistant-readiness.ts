import type {CapabilitiesWire} from './protocol';

/** Same readiness for the main composer and Study handoff; connection grants are not model readiness. */
export function assistantBlockReason(wire: Pick<CapabilitiesWire, 'roles'> | null, connected: boolean, pending: boolean): string | null {
  if (!connected) return 'Reconnect to Hooke before asking. Your question stays here.';
  if (pending) return 'The Assistant is answering your previous question.';
  const role = wire?.roles.find(item => item.id === 'vision');
  const model = role?.options.find(item => item.id === role.current);
  if (!model) return 'Checking the Assistant’s model. Your question stays here.';
  if (!model.available) return `${model.label}: ${model.readiness?.word || model.why || 'unavailable in this workspace'}.`;
  return null;
}
