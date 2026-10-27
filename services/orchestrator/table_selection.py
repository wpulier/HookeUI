"""Shared, explicit row selection for table analyses and graphs; no scientific choices are inferred."""
from __future__ import annotations

import math

from .datatables import DataTableRefused, MAX_ROWS


def _label(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise DataTableRefused('group labels must be finite scalar values', next='choose a category column')


def selected_rows(snapshot: dict, spec: dict) -> dict:
    """Select finite numeric observations, retaining exact row IDs and exclusion reasons.

    `value` selects one numeric column; alternatively both `x` and `y` must be explicit.
    `group` optionally selects a grouping column, with explicit ordered `groups` when supplied.
    Unspecified groups follow first appearance. Existing exclusions always apply.
    """
    columns = {column['key']: column for column in snapshot['columns']}
    rows = snapshot['rows']
    if len(rows) > MAX_ROWS:
        raise DataTableRefused('too many rows for this analysis', next='choose a smaller table')
    value, x, y, group = (spec.get(key) for key in ('value', 'x', 'y', 'group'))
    if value and not x and not y:
        numeric = [value]
    elif x and y and not value:
        numeric = list(dict.fromkeys([x, y]))
    else:
        raise DataTableRefused('choose a value column, or both x and y', next='set the analysis or graph inputs')
    if any(key not in columns or columns[key].get('type') != 'number' for key in numeric):
        raise DataTableRefused('selected values must be numeric columns', next='choose numeric columns')
    if group is not None and group not in columns:
        raise DataTableRefused('the grouping column is unavailable', next='choose a table column')
    requested = spec.get('groups')
    if requested is not None and (not group or not isinstance(requested, list) or not requested or len(requested) > 12):
        raise DataTableRefused('choose 1 to 12 explicit groups from the grouping column', next='review the group selection')
    labels = [_label(label) for label in requested] if requested is not None else ([] if group else ['All'])
    # Typed equality prevents True and 1 from silently becoming one experimental group.
    def identity(label):
        return (type(label).__name__, label)
    if len({identity(label) for label in labels}) != len(labels):
        raise DataTableRefused('a group was selected twice', next='choose distinct groups')
    grouped = {identity(label): {'label': label, 'rows': [], 'n': 0, 'excluded': []} for label in labels}
    exclusions, identities = [], set()
    for row in rows:
        rid = row.get('row')
        if not isinstance(rid, str) or not rid or rid in identities:
            raise DataTableRefused('table rows require unique retained identities', next='restore the accepted table')
        identities.add(rid)
        label = _label(row.get(group)) if group else 'All'
        key = identity(label)
        if key not in grouped:
            if requested is not None:
                exclusions.append({'row': rid, 'reason': 'group not selected'})
                continue
            if len(grouped) == 12:
                raise DataTableRefused('choose at most 12 groups', next='select the groups to compare explicitly')
            labels.append(label)
            grouped[key] = {'label': label, 'rows': [], 'n': 0, 'excluded': []}
        entry = grouped[key]
        reason = row.get('excluded')
        if not reason:
            invalid = [key for key in numeric if isinstance(row.get(key), bool)
                       or not isinstance(row.get(key), (int, float)) or not math.isfinite(row[key])]
            reason = f'missing or non-finite numeric value: {", ".join(invalid)}' if invalid else None
        if reason:
            fact = {'row': rid, 'reason': str(reason)}
            exclusions.append(fact)
            entry['excluded'].append(fact)
        else:
            entry['rows'].append(row)
            entry['n'] += 1
    return {'columns': columns, 'groups': list(grouped.values()), 'excluded': exclusions,
            'selection': {'value': value, 'x': x, 'y': y, 'group': group, 'groups': labels}}
