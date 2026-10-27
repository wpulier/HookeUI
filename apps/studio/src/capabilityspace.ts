/** The runtime copies Space can free. The server owns paths and refuses a changed inventory or active work; each
 * row's `says` (capability_space.py) is what removing it does to storage, in the server's words. */
export interface StorageItem { id: string; name: string; kind: string; models: string[]; bytes: number; revision: string; generations: number; pendingRemoval?: number; retention?: { reason: string; next: string; checked?: boolean }; says?: string }
/** `says` splits the total into what Remove can free and what is kept because saved work uses it. */
export interface StorageInventory { items: StorageItem[]; bytes: number; preserved: string; says?: string; reclaimableBytes?: number; keptBytes?: number }
export interface SpaceCommands { list: () => Promise<StorageInventory>; remove: (id: string, revision: string) => Promise<unknown> }

export function storageWords(bytes: number): string {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : bytes >= 1024 ** 2
    ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${bytes.toLocaleString()} bytes`;
}

export function spaceSection(commands: SpaceCommands): HTMLElement {
  const section = document.createElement('section'); section.id = 'capability-space'; section.dataset.modelEditor = 'capability-space';
  section.className = 'models-group';
  const title = document.createElement('h3'); title.textContent = 'Space';
  const note = document.createElement('p'); note.textContent = 'Managed downloads and environments. Your pictures, data, results and original proof inputs stay.';
  const refresh = document.createElement('button'); refresh.type = 'button'; refresh.className = 'mm-control'; refresh.textContent = 'Show storage';
  const said = document.createElement('p'); said.setAttribute('role', 'status');
  // What the last removal did stays said above the refreshed list (the list's own summary replaces `said`).
  const outcome = document.createElement('p'); outcome.setAttribute('role', 'status'); outcome.className = 'capability-space-outcome';
  const list = document.createElement('div'); list.className = 'capability-space-list';
  const read = async (): Promise<void> => {
    refresh.disabled = true; said.textContent = 'Reading the managed storage…';
    try {
      const inventory = await commands.list(); list.replaceChildren();
      said.textContent = `${inventory.says ?? `${storageWords(inventory.bytes)} in managed copies.`} ${inventory.preserved}`;
      for (const row of inventory.items) {
        // A row of its own (workspace-feel.css), not a Models card: a name and size, what removing does, its one action.
        const item = document.createElement('article'); item.className = 'capability-space-item';
        const heading = document.createElement('h4'); heading.textContent = `${row.name} · ${storageWords(row.bytes)}`;
        const affects = document.createElement('p');
        affects.textContent = row.says ?? `Shared by: ${row.models.join(', ')}. Removing these ${row.generations} runtime copies means those capabilities need setup again.`;
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'mm-control'; remove.textContent = 'Remove runtime copies';
        // Removing a working copy is confirmed first, with what it frees and what needs setup again; finishing a
        // withdrawn copy's cleanup is not, since nothing that runs is taken away.
        const confirm = document.createElement('div'); confirm.className = 'capability-space-confirm'; confirm.hidden = true;
        const question = document.createElement('p');
        question.textContent = `Remove ${row.name} (up to ${storageWords(row.bytes)})? Shared by: ${row.models.join(', ')}. Your Projects and saved results are not removed.`;
        const yes = document.createElement('button'); yes.type = 'button'; yes.className = 'mm-control'; yes.textContent = `Remove and free up to ${storageWords(row.bytes)}`;
        const no = document.createElement('button'); no.type = 'button'; no.className = 'mm-control'; no.textContent = 'Cancel';
        confirm.append(question, yes, no);
        if (row.pendingRemoval) {
          if (!row.says) affects.textContent = `${row.pendingRemoval} withdrawn copies still need cleanup. Finishing removes only those copies; currently installed versions and saved work stay.`;
          remove.textContent = 'Finish removal';
        }
        if (row.retention) {
          affects.textContent = `${row.says ?? row.retention.reason} ${row.retention.next}`;
          // checked false: the saved-work check could not run, which is not the same as saved work using it.
          remove.disabled = true; remove.textContent = row.retention.checked === false ? 'Saved work not checked' : 'Kept for saved work';
        }
        const removeNow = (): void => {
          remove.disabled = true; yes.disabled = true; confirm.hidden = true;
          void commands.remove(row.id, row.revision).then((reply: unknown) => {
            const words = reply && typeof reply === 'object' && 'says' in reply && typeof reply.says === 'string' ? reply.says : '';
            outcome.textContent = words || `Removed ${row.name}. Your Projects and saved results are kept.`;
            return read();
          }).catch((error: unknown) => {
            let message = error instanceof Error ? error.message : String(error);
            const reply = error && typeof error === 'object' && 'reply' in error ? error.reply : null;
            const next = reply && typeof reply === 'object' && 'next' in reply ? reply.next : null;
            if (typeof next === 'string' && next.trim() && !message.includes(next)) message += ` ${next}`;
            outcome.textContent = message; remove.disabled = false; yes.disabled = false;
          });
        };
        remove.addEventListener('click', () => {
          if (row.pendingRemoval) { removeNow(); return; }
          confirm.hidden = false; remove.disabled = true;
        });
        yes.addEventListener('click', removeNow);
        no.addEventListener('click', () => { confirm.hidden = true; remove.disabled = false; });
        item.append(heading, affects, remove, confirm); list.append(item);
      }
      refresh.textContent = 'Refresh storage';
    } catch (error) { said.textContent = error instanceof Error ? error.message : String(error); }
    finally { refresh.disabled = false; }
  };
  refresh.addEventListener('click', () => { void read(); });
  section.append(title, note, refresh, outcome, said, list);
  return section;
}
