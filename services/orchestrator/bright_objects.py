"""Basic, deterministic bright-region analysis on original 8-bit picture pixels."""
import hashlib

import cv2
import numpy as np

from .brain.registry import ToolFailed
from .evidence import encode_labels

# Its declared settings' defaults (catalog/models/basic-bright-objects/model.toml, catalog format 2), when none are given.
DEFAULTS = {'sigma': 1.0, 'min_area': 20, 'connectivity': 8}


def settings_of(values: dict | None) -> dict:
    """What it ran with, as its producer records it: the run's settings, each one not given its default."""
    v = {**DEFAULTS, **(values or {})}
    return {'conversion': 'OpenCV BGR to grayscale (RGB source decoded as BGR)', 'gaussianKernel': [5, 5],
            'gaussianSigma': float(v['sigma']), 'border': 'BORDER_REFLECT_101', 'thresholdMethod': 'Otsu bright foreground',
            'connectivity': int(v['connectivity']), 'minimumSizePx2': int(v['min_area'])}


SETTINGS = settings_of(None)


def analyze(capture, crop, version, values: dict | None = None):
    """Synchronous CPU work; the operation adapter owns its off-loop lifetime and memory. `values`: its settings (sigma,
    min_area, connectivity), checked against its manifest by the caller; its defaults when none."""
    settings = settings_of(values)
    image = cv2.imdecode(np.frombuffer(capture.analysis_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.dtype != np.uint8 or not (image.ndim == 2 or image.ndim == 3 and image.shape[2] == 3):
        raise ToolFailed('Bright objects needs one 8-bit grayscale or RGB picture',
                         next='export an 8-bit grayscale or RGB PNG and open it here')
    source_pixels = hashlib.sha256(image.tobytes()).hexdigest()
    x0 = y0 = 0
    if crop is not None:
        if len(crop) != 4 or any(type(v) is not int for v in crop):
            raise ToolFailed('The analysis crop is invalid', next='select the whole picture and retry')
        x0, y0, width, height = crop
        if x0 < 0 or y0 < 0 or width <= 0 or height <= 0 or x0 + width > image.shape[1] or y0 + height > image.shape[0]:
            raise ToolFailed('The analysis crop lies outside the picture', next='select the whole picture and retry')
        image = image[y0:y0 + height, x0:x0 + width]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    blurred = cv2.GaussianBlur(gray, (5, 5), settings['gaussianSigma'], borderType=cv2.BORDER_REFLECT_101)
    threshold, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    n, raw, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=settings['connectivity'])
    mapping = np.zeros(n, np.int32)
    kept = np.flatnonzero(stats[1:, cv2.CC_STAT_AREA] >= settings['minimumSizePx2']) + 1
    mapping[kept] = np.arange(1, len(kept) + 1)
    labels = mapping[raw]
    producer = {'implementation': 'basic-bright-objects', 'version': version,
                'input': {'capture': capture.id, 'sha256': hashlib.sha256(capture.analysis_bytes).hexdigest(),
                          'pixelsSha256': source_pixels, 'width': capture.width, 'height': capture.height,
                          'sourceImage': capture.source_image},
                'settings': settings, 'threshold': float(threshold), 'runtime': {'opencv': cv2.__version__},
                'maskContract': 'native-bright-regions-v1',
                'limitations': 'Bright regions, not biological cell identities or an accuracy assessment.'}
    # Display polygons are derived by the ordinary population reader from these exact labels.
    return {'labels': {**encode_labels(labels), 'origin': [x0, y0]}, 'count': len(kept),
            'version': version, 'producer': producer}
