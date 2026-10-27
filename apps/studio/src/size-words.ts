// One way to say how big a download is, in SI units as the server says it (readiness.py `size_words`): "413 MB",
// "1.3 GB". The Models page, Tools and the run page use it, so one download never shows two sizes (the 0.3.0 "Download
// 413 MB" chip beside a "Download · 394 MB" button). Datasets keep their own 1024-based `sizeText` (datasets.ts).

export function sizeWords(n: number): string {
  return n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`;
}

/** "273 of 413 MB", "0.4 of 1.3 GB": a measured download's progress in the unit of its total (never a percent made up:
 * both numbers come from the server's download row). */
export function progressWords(got: number, total: number): string {
  if (total >= 1e9) return `${(got / 1e9).toFixed(1)} of ${(total / 1e9).toFixed(1)} GB`;
  return `${Math.round(got / 1e6)} of ${Math.max(1, Math.round(total / 1e6))} MB`;
}
