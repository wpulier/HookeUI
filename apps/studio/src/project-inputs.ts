/** Inputs is a view of the shared inventory, never a second membership store. */
import type { DataEntry, ProjectMaterial } from './data-roles';

export function projectInputs(entries: readonly DataEntry[], query = ''): DataEntry[] {
  const needle = query.trim().toLocaleLowerCase();
  const selected = new Map<string, DataEntry>();
  for (const entry of entries) {
    if (entry.material?.input) selected.set(`${entry.id}@${entry.version}`, entry);
    for (const use of entry.material?.uses ?? []) {
      const key = `${entry.id}@${use.revision}`;
      if (!selected.has(key)) selected.set(key, {...entry, title: use.title, version: use.revision,
        source: {...entry.source, sha256: use.digest},
        material: {...(use.material ?? entry.material!), input: true},
        summary: use.available ? entry.summary : 'This pinned revision is unavailable. Open the Study to inspect its reference.'});
    }
  }
  return [...selected.values()].filter(entry => !needle ||
    [entry.title, entry.summary, entry.material?.kind, entry.material?.format].filter(Boolean).join(' ').toLocaleLowerCase().includes(needle));
}

export function materialKind(kind: ProjectMaterial['kind']): string {
  return {assignment: 'Study assignment', folder: 'Folder', file: 'File', model: 'Model', analysis: 'Analysis', table: 'Table', labels: 'Outlines', workflow: 'Workflow record'}[kind] ?? 'File';
}

export function materialFacts(entry: DataEntry): string {
  const material = entry.material;
  if (!material) return '';
  const size = material.bytes === undefined ? '' : material.bytes < 1024 ? `${material.bytes} B` :
    material.bytes < 1024 ** 2 ? `${(material.bytes / 1024).toFixed(1)} KB` : `${(material.bytes / 1024 ** 2).toFixed(1)} MB`;
  return [materialKind(material.kind),
    material.format, material.shape?.join(' × '), size, `Revision ${entry.version ?? 1}`].filter(Boolean).join(' · ');
}

/** Returning to a retained Tab never substitutes the head for a pinned revision. */
export function projectEntry(entries: readonly DataEntry[], ref: {id: string; day: string; version: number | null}, session: string): DataEntry | null {
  const inventory = [...entries, ...projectInputs(entries)];
  return inventory.find(entry => entry.id === ref.id && (entry.day ?? session) === ref.day &&
    (ref.version === null || (entry.version ?? null) === ref.version)) ?? null;
}
