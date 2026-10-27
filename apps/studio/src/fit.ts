/** Aspect/crop geometry only. Delivery provenance decides whether a camera disc applies. */
export type Disc = [number, number, number];
export interface FitInput {
  width: number; height: number; disc?: Disc | null;
  bounds: { min: number; usual: number; max: number; maxHeight?: number };
  chrome?: number;
}
export interface Fit {
  crop: { x: number; y: number; width: number; height: number };
  round: boolean; panel: { width: number; height: number }; scale: number;
}
export const MIN_DISC_SHARE = 0.25;
export function usableDisc(width: number, height: number, disc?: Disc | null): Disc | null {
  if (!disc || disc.length !== 3 || !disc.every(Number.isFinite) || width <= 0 || height <= 0) return null;
  const [x, y, radius] = disc;
  return x >= 0 && y >= 0 && x <= width && y <= height && radius >= Math.min(width, height) * MIN_DISC_SHARE / 2 ? disc : null;
}
export function fitPicture(input: FitInput): Fit {
  const { width, height } = input;
  if (!(Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0)) throw new RangeError('Invalid picture dimensions');
  const disc = usableDisc(width, height, input.disc);
  let crop = { x: 0, y: 0, width, height }, round = false;
  if (disc) {
    const [x, y, radius] = disc;
    const left = Math.max(0, Math.floor(x - radius)), top = Math.max(0, Math.floor(y - radius));
    crop = { x: left, y: top, width: Math.min(width, Math.ceil(x + radius)) - left, height: Math.min(height, Math.ceil(y + radius)) - top };
    round = crop.width >= 2 * radius - 2 && crop.height >= 2 * radius - 2;
  }
  const aspect = crop.width / crop.height, { min, usual, max, maxHeight } = input.bounds;
  let panelWidth = Math.min(max, Math.max(min, usual));
  if (maxHeight !== undefined) panelWidth = Math.max(min, Math.min(panelWidth, maxHeight * aspect));
  return { crop, round, panel: { width: Math.round(panelWidth), height: Math.round(panelWidth / aspect + (input.chrome ?? 30)) }, scale: panelWidth / crop.width };
}

/** Only fitting writes these offsets; digital zoom/pan compose independently via transform. */
export function fitPanel(root: HTMLElement, picture: HTMLElement, image: HTMLImageElement, disc?: Disc | null): void {
  if (disc === undefined) {
    delete root.dataset.fit; picture.style.aspectRatio = '';
    image.style.width = ''; image.style.left = ''; image.style.top = '';
    return;
  }
  if (!image.naturalWidth || !image.naturalHeight) return;
  const fit = fitPicture({ width: image.naturalWidth, height: image.naturalHeight, disc,
    bounds: { min: 240, usual: root.offsetWidth || 360, max: 640 } });
  const { crop } = fit;
  root.dataset.fit = fit.round ? 'round' : 'full';
  picture.style.aspectRatio = `${crop.width} / ${crop.height}`;
  image.style.width = `${image.naturalWidth / crop.width * 100}%`;
  image.style.left = `${-crop.x / crop.width * 100}%`;
  image.style.top = `${-crop.y / crop.height * 100}%`;
}
