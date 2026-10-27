/** Explicit portable copy download; the original evidence URL is always available separately. */
export function portablePictureControls(url: string, original: string): HTMLElement {
  const host = document.createElement('span'); host.className = 'portable-picture';
  const native = document.createElement('a'); native.href = original; native.download = 'original-picture';
  native.textContent = 'Download original';
  const prepare = document.createElement('button'); prepare.type = 'button'; prepare.textContent = 'Download with capability';
  const link = document.createElement('a'); link.hidden = true;
  const status = document.createElement('span'); status.setAttribute('role', 'status');
  host.append(native, prepare, link, status);
  let objectUrl = '';
  prepare.addEventListener('click', async (event) => {
    event.stopPropagation(); prepare.disabled = true; link.hidden = true;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = ''; status.textContent = 'Preparing a portable copy; the original stays unchanged.';
    try {
      const response = await fetch(url);
      if (!response.ok) {
        // A reply that isn't the app's JSON (a proxy's page, a crash) still says the original is kept, never a parse error.
        const error = await response.json().catch(() => ({})) as { error?: string };
        throw new Error(error.error || 'Portable sharing could not finish. Your original is kept.');
      }
      const blob = await response.blob();
      if (host.isConnected === false) return; // the user changed session/asset while the copy was being prepared
      const suffix = blob.type.includes('png') ? 'png' : 'jpg';
      objectUrl = URL.createObjectURL(blob);
      link.href = objectUrl; link.download = `portable-result.${suffix}`; link.textContent = 'Save portable picture'; link.hidden = false;
      // Said from what the app embedded: an unpublished entry travels as bytes; a reviewed one is named for anyone to get.
      const carried = Number(response.headers.get('X-Carried-Entries') ?? '0');
      status.textContent = carried > 0
        ? `Ready: a separate copy carrying ${carried === 1 ? 'the capability that made it' : `the ${carried} capabilities that made it`}. Opening it elsewhere asks before running code.`
        : 'Ready: a separate copy naming what made it; those are in the reviewed catalog, so it carries no code.';
    } catch (error) {
      status.textContent = error instanceof Error ? error.message : String(error);
    } finally { prepare.disabled = false; }
  });
  return host;
}
