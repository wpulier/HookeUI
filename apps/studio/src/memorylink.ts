// An in-memory Connection for tests: it keeps what the studio sent and plays recorded server
// messages back through the same path a socket would (replies settle their requests first).
import { Link, type ClientCommand, type ServerMessage } from './protocol';

export class MemoryLink extends Link {
  readonly sent: (ClientCommand & { requestId?: string })[] = [];

  send(command: ClientCommand & { requestId?: string }): void {
    this.sent.push(command);
  }

  /** Server messages, in the order they were recorded. */
  replay(messages: Iterable<ServerMessage>): void {
    for (const message of messages) this.receive(message);
  }

  /** The server's reply to the last command sent with a requestId. */
  reply(fields: { ok: boolean; error?: string; [key: string]: unknown }): void {
    const last = [...this.sent].reverse().find((c) => c.requestId);
    if (!last) throw new Error('nothing is waiting for a reply');
    this.receive({ type: 'result', for: last.type, requestId: last.requestId, ...fields });
  }
}
