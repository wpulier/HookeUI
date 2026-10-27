"""Spot measure: every connected spot darker than the threshold, larger than the smallest area, one row each.

run(picture, params) is called with the view (a uint8 RGB array) and its parameters' values, checked, defaults filled,
plus pixel_size_um when the app knows the scale. No network, nothing installed at run time."""
import numpy as np
from scipy import ndimage


def run(picture, params):
    if min(picture.shape[:2]) < 16:
        return {"unsupported": "pictures smaller than 16 px a side"}
    grey = picture.mean(axis=2)
    labels, n = ndimage.label(grey < params["threshold"])
    index = np.arange(1, n + 1)
    areas = ndimage.sum_labels(np.ones_like(grey), labels, index) if n else np.array([])
    keep = index[areas >= params["min_area"]] if n else index
    um = params.get("pixel_size_um")
    columns = [{"name": "Spot"}, {"name": "Area", "unit": "px", "definition": "pixels in the spot"},
               {"name": "Centre x", "unit": "px"}, {"name": "Centre y", "unit": "px"},
               {"name": "Mean grey", "unit": "0-255", "definition": "mean of the R, G and B means over the spot"}]
    if um:
        columns.insert(2, {"name": "Area (µm²)", "unit": "µm²", "definition": "pixels times the app's scale squared"})
    rows = []
    for i, label in enumerate(keep, 1):
        area = float(areas[label - 1])
        cy, cx = ndimage.center_of_mass(labels == label)
        mean = float(grey[labels == label].mean())
        row = [i, area, round(float(cx), 2), round(float(cy), 2), round(mean, 2)]
        if um:
            row.insert(2, round(area * um * um, 4))
        rows.append(row)
    return {"columns": columns, "rows": rows}
