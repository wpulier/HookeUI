/** Selected browser files stay blobs; only the bounded logical index is encoded. */
export type Folder = { name: string; size: number; count: number; body: Blob };
export function selectedFolder(files: File[]): Folder {
  if (!files.length || files.length > 4096) throw new Error('Choose a folder with 1–4,096 files.');
  const paths = new Set<string>();
  let name = '', size = 0;
  const entries = files.map(file => {
    const parts = file.webkitRelativePath.split('/'), root = parts.shift() ?? '', path = parts.join('/');
    if (!name) name = root;
    if (name !== root || !root || !path || path.length > 512 || /[\\:\u0000-\u001f\u007f]/.test(path)
        || parts.some(part => !part || part === '.' || part === '..') || paths.has(path))
      throw new Error('Choose one folder with distinct relative file names.');
    paths.add(path);
    const entry = { path, offset: size, size: file.size }; size += file.size;
    if (!Number.isSafeInteger(size) || size > 64 * 1024 * 1024) throw new Error('Folder import currently supports up to 64 MiB in total.');
    return entry;
  });
  for (const path of paths) for (let at = 0; at < path.length; at++) {
    if (path[at] === '/' && paths.has(path.slice(0, at))) throw new Error('A selected file cannot also be a folder.');
  }
  const index = new TextEncoder().encode(JSON.stringify({ format: 'source-directory-v1', entries }));
  if (index.byteLength > 1024 * 1024) throw new Error('This folder has too much entry metadata.');
  const length = new ArrayBuffer(4); new DataView(length).setUint32(0, index.byteLength);
  return { name, size, count: entries.length, body: new Blob([length, index, ...files]) };
}

/** Open files as a stack: flat names in natural order (z2 before z10), one new axis named by the researcher. */
export function selectedStack(files: File[]): Folder & { order: string[] } {
  if (files.length < 2 || files.length > 4096) throw new Error('Choose 2–4,096 files to stack.');
  const ordered = [...files].sort((a, b) => new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' }).compare(a.name, b.name));
  // The type after the last dot; a leading dot (.hidden) or a trailing dot is no type, as the server reads it.
  const kinds = new Set(ordered.map(file => file.name.replace(/^\.+/, '').includes('.') && !file.name.endsWith('.') ? file.name.split('.').pop()!.toLowerCase() : ''));
  if (kinds.has('') || kinds.size > 1) throw new Error('A stack’s files must share one file type, so one reader can open every plane.');
  if (new Set(ordered.map(file => file.name)).size !== ordered.length) throw new Error('Choose files with distinct names.');
  let size = 0;
  const entries = ordered.map(file => {
    if (!file.name || file.name.length > 512 || /[\\/:\u0000-\u001f\u007f]/.test(file.name)) throw new Error('A file name cannot be used in a stack.');
    const entry = { path: file.name, offset: size, size: file.size }; size += file.size;
    if (!Number.isSafeInteger(size) || size > 64 * 1024 * 1024) throw new Error('A stack import currently supports up to 64 MiB in total.');
    return entry;
  });
  const index = new TextEncoder().encode(JSON.stringify({ format: 'source-directory-v1', entries }));
  if (index.byteLength > 1024 * 1024) throw new Error('This stack has too much entry metadata.');
  const length = new ArrayBuffer(4); new DataView(length).setUint32(0, index.byteLength);
  const first = ordered[0].name, last = ordered[ordered.length - 1].name;
  return { name: `${first} – ${last} (${ordered.length} files)`.slice(0, 240), size, count: ordered.length,
    body: new Blob([length, index, ...ordered]), order: ordered.map(file => file.name) };
}
