"""Independent NumPy/Python reference, deliberately no application or OpenCV analysis calls.

OpenCV uint8 GaussianBlur uses fixed-point coefficients. For sigma=1 and five
samples its error-diffused kernel is [14,62,104,62,14]/256. Reference:
https://github.com/opencv/opencv/blob/4.13.0/modules/imgproc/src/smooth.dispatch.cpp
(getGaussianKernelFixedPoint_ED). Explicit convolution, histogram partition
scoring, and a Python flood fill provide an independent oracle for this recipe.
This tests algorithm reproducibility, not biological segmentation accuracy.
"""
from collections import deque

import numpy as np


def canonical(labels):
    """Renumber occupied regions by their first row-major pixel, ignoring implementation order."""
    flat = labels.ravel()
    values, first = np.unique(flat, return_index=True)
    ordered = values[np.argsort(first)]
    lut = np.zeros(int(values.max(initial=0)) + 1, dtype='<u2')
    foreground = ordered[ordered != 0]
    lut[foreground] = np.arange(1, len(foreground) + 1)
    return lut[labels]


def reference(gray):
    assert gray.ndim == 2 and gray.dtype == np.uint8
    padded = np.pad(gray.astype(np.int64), 2, mode='reflect')
    h, w = gray.shape
    accum = np.zeros((h, w), dtype=np.int64)
    kernel = [14, 62, 104, 62, 14]
    for y, ky in enumerate(kernel):
        for x, kx in enumerate(kernel):
            accum += ky * kx * padded[y:y+h, x:x+w]
    blurred = ((accum + 32768) // 65536).astype(np.uint8)
    hist = np.bincount(blurred.ravel(), minlength=256)
    total = int(hist.sum())
    weighted_total = sum(i * int(v) for i, v in enumerate(hist))
    weight = weighted = 0
    best_num, best_den, threshold = -1, 1, 0
    for value, count in enumerate(hist):
        weight += int(count)
        weighted += value * int(count)
        if weight == 0 or weight == total:
            continue
        num = (weighted_total * weight - weighted * total) ** 2
        den = weight * (total - weight)
        if num * best_den > best_num * den:
            best_num, best_den, threshold = num, den, value
    unseen = blurred > threshold
    labels = np.zeros(gray.shape, dtype='<u2')
    label = 0
    for y, x in np.argwhere(unseen):
        if not unseen[y, x]:
            continue
        pixels, todo = [], deque([(int(y), int(x))])
        unseen[y, x] = False
        while todo:
            cy, cx = todo.popleft()
            pixels.append((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and unseen[ny, nx]:
                        unseen[ny, nx] = False
                        todo.append((ny, nx))
        if len(pixels) >= 20:
            label += 1
            ys, xs = zip(*pixels)
            labels[ys, xs] = label
    measurements = []
    for n in range(1, label + 1):
        ys, xs = np.where(labels == n)
        measurements.append((len(xs), float(xs.mean() + .5), float(ys.mean() + .5), float(gray[ys, xs].mean())))
    return threshold, labels, measurements
