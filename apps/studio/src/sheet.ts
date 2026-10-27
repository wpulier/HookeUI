/** Shared responsive host. Mount stateful content before rendering to preserve iframe browsing contexts. */
export class StudioSheet {
  private static active: StudioSheet | null = null;
  readonly dialog = document.createElement('dialog');
  private readonly body = document.createElement('div');
  private readonly title = document.createElement('h2');
  private readonly closeButton = document.createElement('button');
  private placeholder: Comment | null = null;
  private content: HTMLElement | null = null;
  private opener: HTMLElement | null = null;
  private mounted = false;
  private modal = false;
  onDismiss: () => void = () => {};

  constructor(label: string) {
    this.dialog.className = 'studio-sheet';
    this.title.textContent = label;
    this.title.id = `sheet-${crypto.randomUUID()}`;
    this.dialog.setAttribute('aria-labelledby', this.title.id);
    this.closeButton.type = 'button'; this.closeButton.textContent = '×';
    this.closeButton.setAttribute('aria-label', `Close ${label}`);
    const header = document.createElement('header'); header.className = 'studio-sheet-header';
    header.append(this.title, this.closeButton);
    this.body.className = 'studio-sheet-body';
    this.dialog.append(header, this.body);
    this.closeButton.addEventListener('click', () => this.close());
    this.dialog.addEventListener('cancel', event => { event.preventDefault(); event.stopPropagation(); this.close(); });
    this.dialog.addEventListener('keydown', event => {
      // Escape belongs to the topmost sheet, not the panel or route underneath it.
      if (this.modal && event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); this.close(); }
    });
    this.dialog.addEventListener('click', event => {
      if (event.target !== this.dialog) return;
      const box = this.dialog.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) this.close();
    });
  }

  /** Call once while the content host is empty, before creating any iframe/media inside it.
   * The dialog stays at this position; showModal promotes it without reparenting. */
  mount(content: HTMLElement): void {
    if (this.content === content && this.mounted) return;
    if (this.content) throw new Error('Sheet already owns content');
    content.before(this.dialog); this.body.append(content);
    this.content = content; this.mounted = true;
    this.dialog.dataset.inline = ''; this.dialog.show();
  }

  open(content: HTMLElement, opener?: HTMLElement | null): void {
    if (this.content === content && this.modal) return;
    StudioSheet.active?.close();
    this.opener = opener ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    if (this.mounted) {
      if (this.content !== content) throw new Error('Mounted sheet cannot change its content host');
      this.dialog.close(); delete this.dialog.dataset.inline;
    } else {
      this.placeholder = document.createComment('studio-sheet return position');
      content.before(this.placeholder); this.content = content;
      this.body.append(content); document.body.append(this.dialog);
    }
    this.modal = true; StudioSheet.active = this; this.dialog.showModal(); this.closeButton.focus();
  }

  /** Returns the exact node to its original container; optional silent close handles breakpoint changes. */
  close(notify = true): void {
    if (!this.content || !this.modal) return;
    this.modal = false; this.dialog.close();
    if (this.mounted) { this.dialog.dataset.inline = ''; this.dialog.show(); }
    else {
      this.placeholder?.replaceWith(this.content);
      this.content = null; this.placeholder = null; this.dialog.remove();
    }
    if (StudioSheet.active === this) StudioSheet.active = null;
    if (notify) this.onDismiss();
    if (this.opener?.isConnected) this.opener.focus({ preventScroll: true });
    this.opener = null;
  }
  isOpen(): boolean { return this.modal; }
}
