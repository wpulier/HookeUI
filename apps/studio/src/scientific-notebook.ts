/** A local Jupyter connection to this private session. Its token stays in Hooke's home; this panel never sees it. */
export type NotebookTransport = { connect(): Promise<unknown>; revoke(): Promise<void> };
type Grant = { name: string; display: string; kernel: string; install: string; expires: number; meaning: string };

const object = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === 'object' && !Array.isArray(x);

export function notebookGrant(raw: unknown): Grant {
  if (!object(raw) || typeof raw.name !== 'string' || !/^hooke-[0-9a-f]{12}$/.test(raw.name) || typeof raw.kernel !== 'string' ||
      raw.kernel.length > 4096 || typeof raw.display !== 'string' || raw.display.length > 128 || typeof raw.install !== 'string' || raw.install.length > 4200 ||
      !raw.install.startsWith('jupyter kernelspec install --user ') || typeof raw.meaning !== 'string' ||
      raw.meaning.length > 1024 || typeof raw.expires !== 'number' || !Number.isFinite(raw.expires) || 'token' in raw)
    throw new Error('Hooke returned an invalid notebook connection. Nothing was connected; try again.');
  return { name: raw.name, display: raw.display, kernel: raw.kernel, install: raw.install, expires: raw.expires, meaning: raw.meaning };
}

export class ScientificNotebook {
  readonly element = document.createElement('details');
  private readonly connectButton = document.createElement('button');
  private readonly revokeButton = document.createElement('button');
  private readonly status = document.createElement('p');
  private readonly command = document.createElement('pre');
  private busy = false;
  private connected = false;
  private available = true;
  private readonly transport: NotebookTransport;

  constructor(transport: NotebookTransport) {
    this.transport = transport;
    const summary = document.createElement('summary'), about = document.createElement('p');
    summary.textContent = 'Connect a notebook';
    about.textContent = 'Use this session from Jupyter on this Mac. Notebook cells run here, on this session’s saved input, with the same checks as Run code, and are kept in its Python history. Hooke runs nothing outside this session.';
    this.connectButton.type = this.revokeButton.type = 'button';
    this.connectButton.textContent = 'Connect a notebook';
    this.revokeButton.textContent = 'Revoke notebook connection';
    this.connectButton.addEventListener('click', () => { if (!this.connectButton.disabled) void this.connect(); });
    this.revokeButton.addEventListener('click', () => { if (!this.revokeButton.disabled) void this.revoke(); });
    this.status.setAttribute('role', 'status');
    this.command.setAttribute('aria-label', 'Command that adds this session to Jupyter');
    this.element.append(summary, about, this.connectButton, this.revokeButton, this.status, this.command);
    this.paint();
  }

  setAvailable(available: boolean): void { this.available = available; this.paint(); }

  /** The session ended or was replaced, so its connection ended with it. */
  reset(message = 'This session ended, so its notebook connection ended too. Connect again from the new session.'): void {
    if (!this.connected) return;
    this.connected = false; this.command.textContent = ''; this.status.textContent = message; this.paint();
  }

  private async connect(): Promise<void> {
    this.busy = true; this.paint(); this.status.textContent = 'Preparing the connection…';
    try {
      const grant = notebookGrant(await this.transport.connect());
      this.connected = true;
      this.command.textContent = grant.install;
      this.status.textContent = `${grant.meaning} In a terminal, run the command below once, then choose the kernel “${grant.display}” in Jupyter. Valid until ${new Date(grant.expires * 1000).toLocaleString()}.`;
    } catch (error) {
      this.status.textContent = `The notebook was not connected: ${error instanceof Error ? error.message : String(error)}`;
    } finally { this.busy = false; this.paint(); }
  }

  private async revoke(): Promise<void> {
    this.busy = true; this.paint();
    try {
      await this.transport.revoke();
      this.connected = false; this.command.textContent = '';
      this.status.textContent = 'Notebook connection revoked. A notebook still open can no longer run anything here.';
    } catch (error) {
      this.status.textContent = `The connection was not revoked: ${error instanceof Error ? error.message : String(error)}`;
    } finally { this.busy = false; this.paint(); }
  }

  private paint(): void {
    this.connectButton.disabled = this.busy || !this.available;
    this.connectButton.textContent = this.connected ? 'Connect again (ends the earlier connection)' : 'Connect a notebook';
    this.revokeButton.disabled = this.busy || !this.connected;
  }
}
