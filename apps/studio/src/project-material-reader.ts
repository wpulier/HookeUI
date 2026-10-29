/** Read metadata from the exact accepted revision; opening never executes a reader. */
export async function readMaterialRevision(ref: {id: string; day: string; version: number}, digest: string | undefined,
  signal: AbortSignal): Promise<Record<string, unknown>> {
  if (!ref.day || !Number.isSafeInteger(ref.version) || ref.version < 1) throw new Error('Choose an exact saved revision.');
  const response = await fetch(`/api/scientific-projects/${encodeURIComponent(ref.id)}?${new URLSearchParams({session: ref.day, revision: String(ref.version)})}`, {signal});
  const result = await response.json();
  if (!response.ok) throw new Error([typeof result.says === 'string' ? result.says : typeof result.error === 'string' ? result.error : 'The saved revision could not be read.', typeof result.next === 'string' ? result.next : ''].filter(Boolean).join(' '));
  const project = result.project;
  if (!project || project.id !== ref.id || project.session !== ref.day || project.revision !== ref.version ||
    (digest !== undefined && project.digest !== digest) || !project.document || typeof project.document !== 'object' || Array.isArray(project.document))
    throw new Error('This is not the requested saved revision. Reopen its retained reference.');
  return project.document;
}
