/** Only the address observed from this runtime; never another installation's port. */
export function bridgeAddress(value: string): string {
  if (!value) throw new Error('Camera connection unavailable. Wait for this app to connect.');
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Camera connection unavailable.');
  return value.replace(/\/$/, '');
}
