/** Foreground order among workspace content roots, not menus or dialogs.
 * Global source/control coverage deliberately remains independent and incomplete.
 * Only direct siblings with measured CSS stacking contexts can prove this order.
 */
export interface WorkspaceForeground {
  scope: 'workspace-windows'; hostId: string; complete: boolean;
  roots: { rootId: string; z: number }[];
}
export function workspaceForeground(host: HTMLElement, registered: readonly HTMLElement[]): WorkspaceForeground {
  const result: WorkspaceForeground = { scope: 'workspace-windows', hostId: host.id, complete: !!host.id, roots: [] };
  const known = new Set<HTMLElement>();
  for (let root of registered) {
    while (root.parentElement && root.parentElement !== host) root = root.parentElement;
    if (root.parentElement === host) known.add(root);
  }
  const hostStyle = getComputedStyle(host), hostBox = host.getBoundingClientRect();
  if (!host.parentElement || host.hidden || document.visibilityState === 'hidden'
    || hostStyle.display === 'none' || hostStyle.visibility === 'hidden' || hostStyle.opacity === '0'
    || hostBox.width <= 0 || hostBox.height <= 0) result.complete = false;
  for (const child of Array.from(host.children) as HTMLElement[]) {
    const style = getComputedStyle(child);
    // Zero-size/hidden-visibility wrappers may still paint visible descendants.
    // Only subtree-wide paint suppression excludes a root from this inventory.
    if (style.display === 'none' || style.opacity === '0') continue;
    // Restore buttons and camera status are workspace chrome, not content windows.
    if (child.classList.contains('workspace-hidden-shelf') || child.id === 'notice' && child.classList.contains('notice')) continue;
    if (!known.has(child) || !child.id) { result.complete = false; continue; }
    const z = Number(style.zIndex);
    // Canvas has no descendants that could escape an auto stacking level.
    if (child.tagName === 'CANVAS' && style.zIndex === 'auto') result.roots.push({ rootId: child.id, z: 0 });
    else if (!['absolute', 'relative', 'fixed', 'sticky'].includes(style.position) || style.zIndex === 'auto' || !Number.isFinite(z)) result.complete = false;
    else result.roots.push({ rootId: child.id, z });
  }
  if (result.roots.length > 64) { result.complete = false; result.roots.length = 64; }
  return result;
}
