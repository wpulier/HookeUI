// Select first, then create the Project. Browsing and cancelling leave the current Project alone.
import { libraryUrl, readLibrary } from './asset-library';
import type { AssetMeta } from './protocol';

export function chooseProjectAsset(): Promise<AssetMeta | null> {
  const dialog = document.createElement('dialog');
  dialog.className = 'workspace-recovery-dialog project-library-picker';
  dialog.setAttribute('aria-label', 'Choose from Library');
  const heading = document.createElement('h2');
  heading.textContent = 'Choose from Library';
  const search = document.createElement('input');
  search.type = 'search';
  search.setAttribute('aria-label', 'Search your pictures and recordings');
  search.placeholder = 'Search your pictures and recordings';
  const rows = document.createElement('div');
  rows.className = 'project-library-choices';
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  const more = document.createElement('button');
  const cancel = document.createElement('button');
  cancel.textContent = 'Cancel';
  const load = async (offset: number) => {
    const read = ++generation;
    if (!offset) rows.replaceChildren();
    status.textContent = 'Reading Library…';
    more.hidden = true;
    try {
      const response = await fetch(libraryUrl({scope:'mine', q:search.value, limit:48, offset, fresh:offset===0}), {cache:'no-store'});
      if (!response.ok) throw new Error('Could not read the Library.');
      const page = readLibrary(await response.json());
      if (!dialog.open || generation !== read) return;
      for (const asset of page.assets) {
        const button = document.createElement('button');
        if (asset.thumb) {
          const image = document.createElement('img');
          image.src = asset.thumb; image.alt = ''; image.loading = 'lazy';
          button.append(image);
        }
        button.append(asset.title);
        button.onclick = () => { selected = asset; dialog.close(); };
        rows.append(button);
      }
      const total = offset + page.assets.length;
      status.textContent = total ? `${total} of ${page.count} inputs` : 'No matching pictures or recordings.';
      more.hidden = total > 0 && (!page.assets.length || total >= page.count);
      more.textContent = total ? 'Show more' : 'Refresh Library';
      more.onclick = () => void load(total);
    } catch {
      if (!dialog.open || generation !== read) return;
      status.textContent = 'The Library could not be read.';
      more.textContent = 'Retry'; more.hidden = false;
      more.onclick = () => void load(offset);
    }
  };
  let selected: AssetMeta | null = null;
  let generation = 0;
  search.oninput = () => void load(0);
  cancel.onclick = () => dialog.close();
  dialog.append(heading, search, status, rows, more, cancel);
  document.body.append(dialog);
  dialog.showModal();
  const refreshEmpty = () => { if (dialog.open && !rows.childElementCount) void load(0); };
  window.addEventListener('hooke-project-library-changed', refreshEmpty);
  void load(0);
  return new Promise(resolve => dialog.addEventListener('close', () => {
    window.removeEventListener('hooke-project-library-changed', refreshEmpty);
    dialog.remove(); resolve(selected);
  }, {once:true}));
}
