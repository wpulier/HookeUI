"""Chart values come from an exact completed resource read, never model-supplied numbers."""
from __future__ import annotations

import copy
import math


def pointer(document, path):
    if not isinstance(path, str) or not path.startswith('/detail/') or len(path) > 300:
        raise ValueError('chart paths must address retained detail values')
    value = document
    for part in path[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, TypeError, IndexError, ValueError):
            raise ValueError(f'chart source path is unavailable: {path}') from None
    return value


def resolve(requests, documents):
    if requests is None:
        return []
    if not isinstance(requests, list) or len(requests) > 3:
        raise ValueError('at most three retained-data charts are supported')
    charts = []
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError('a chart must name retained source points')
        title, points = request.get('title'), request.get('points')
        if not isinstance(title, str) or not 1 <= len(title) <= 160 or not isinstance(points, list) or not 1 <= len(points) <= 50:
            raise ValueError('a chart needs a title and 1–50 retained points')
        rows = []
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get('id'), str) or not isinstance(point.get('revision'), str):
                raise ValueError('a chart point must identify its exact source revision')
            key = (point.get('id'), point.get('revision'))
            document = documents.get(key)
            if document is None:
                raise ValueError('that revision was not kept whole: read it from offset 0 to its end (an entry over '
                                 '240,000 characters, or past the 6th, is read but never kept for charts)')
            value = pointer(document, point.get('valuePath'))
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('chart values must be finite retained numbers; missing values are never imputed')
            unit = pointer(document, point['unitPath']) if point.get('unitPath') else None
            denominator = pointer(document, point['denominatorPath']) if point.get('denominatorPath') else None
            excluded = pointer(document, point['exclusionsPath']) if point.get('exclusionsPath') else None
            if unit is not None and (not isinstance(unit, str) or len(unit) > 80):
                raise ValueError('the retained unit must be text')
            if denominator is not None and (type(denominator) is not int or denominator < 0):
                raise ValueError('the retained denominator must be a nonnegative count')
            if excluded is not None and (not isinstance(excluded, (str, list, dict)) or len(str(excluded)) > 4000):
                raise ValueError('retained exclusions exceed the chart bound')
            label = point.get('label')
            if not isinstance(label, str) or not 1 <= len(label) <= 120:
                raise ValueError('each chart point needs a readable label')
            entry = document['entry']
            rows.append({'label': label, 'value': value, 'unit': unit, 'denominator': denominator,
                         'exclusions': copy.deepcopy(excluded), 'source': {
                             'day': entry['day'], 'id': entry['id'], 'version': entry.get('version'),
                             'contentRevision': key[1], **{name: point[name] for name in
                                 ('valuePath', 'unitPath', 'denominatorPath', 'exclusionsPath') if point.get(name)}}})
        if len({row['unit'] for row in rows}) > 1:
            raise ValueError('one chart cannot mix units; keep unlike measurements in separate charts')
        charts.append({'title': title, 'rows': rows, 'transformation': 'identity',
                       'selectionRule': 'Highlight only; values, denominator and exclusions remain unchanged'})
    return charts
