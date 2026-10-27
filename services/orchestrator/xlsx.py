"""Deterministic XLSX of one accepted DataTables snapshot; no reads, execution or dependencies.

Call off-loop after DataTables.read(..., owner=captured_owner), with the matching journal
record and captured Project. Recheck the owner before delivering the bytes. This writer
validates the record's immutable snapshot identity; it does not grant Project access.
Optional analyses use accepted TableDerivatives snapshots; no statistics are computed here.
"""
from __future__ import annotations

import hashlib
import io
from itertools import chain
import json
import math
import re
import zipfile
from xml.sax.saxutils import escape

from .datatables import ID as TABLE_ID

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
MAX_SNAPSHOT_BYTES = 32 << 20
MAX_XML_BYTES = 128 << 20
MAX_ROWS = 100_000
MAX_CELLS = 2_000_000
MAX_COLUMNS = 16_384
INVALID_XML = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


class ExportRefused(ValueError):
    """Export cannot preserve this accepted snapshot without changing its data."""


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _text(value):
    if INVALID_XML.search(value) or len(value.encode('utf-16-le')) // 2 > 32767:
        raise ExportRefused('A cell contains unsupported XML characters or exceeds Excel’s 32,767-character limit')
    # Escape literal OOXML escape sequences so Excel does not interpret them as characters.
    value = re.sub(r'_(?=x[0-9A-Fa-f]{4}_)', '_x005F_', value)
    return escape(value)


def _column(number):
    result = ''
    while number:
        number, rest = divmod(number - 1, 26)
        result = chr(65 + rest) + result
    return result


def _cell(reference, value):
    if value is None:
        return ''
    if type(value) is bool:
        return f'<c r="{reference}" t="b"><v>{int(value)}</v></c>'
    if type(value) in (int, float):
        if type(value) is float and not math.isfinite(value):
            raise ExportRefused('Non-finite numbers cannot be exported as measurements')
        # Excel truncates large integers to 15 digits. Preserve their exact decimal spelling.
        if type(value) is int and abs(value) > 999_999_999_999_999:
            return _cell(reference, str(value))
        return f'<c r="{reference}" t="n"><v>{repr(value)}</v></c>'
    if not isinstance(value, str):
        raise ExportRefused('Table cells must be text, finite numbers, booleans or empty')
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{_text(value)}</t></is></c>'


def _sheet(rows):
    output = io.StringIO()
    output.write(f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}"><sheetData>')
    size = 0
    for r, row in enumerate(rows, 1):
        if r > 1_048_576 or len(row) > MAX_COLUMNS:
            raise ExportRefused('The worksheet exceeds Excel’s row or column limit')
        cells = ''.join(_cell(f'{_column(c)}{r}', value) for c, value in enumerate(row, 1))
        text = f'<row r="{r}">{cells}</row>'
        size += len(text.encode())
        if size > MAX_XML_BYTES:
            raise ExportRefused('The worksheet exceeds the supported export byte budget')
        output.write(text)
    output.write('</sheetData></worksheet>')
    return output.getvalue().encode()


def _provenance(value, path=''):
    """Typed JSON-pointer leaves avoid oversized metadata cells and retain nested identities."""
    if isinstance(value, dict) and value:
        for key in sorted(value):
            token = key.replace('~', '~0').replace('/', '~1')
            yield from _provenance(value[key], path + '/' + token)
    elif isinstance(value, list) and value:
        for i, child in enumerate(value):
            yield from _provenance(child, path + '/' + str(i))
    else:
        yield [path, _json(value)]


def _analyses(analyses, source):
    rows = [['Analysis', 'Version', 'Field', 'Value type', 'Value']]
    records = []
    seen = set()
    if len(analyses) > 100:
        raise ExportRefused('Export at most 100 retained analyses at once')
    for record, snapshot in analyses:
        identity = (record.get('id'), record.get('version'))
        if (record.get('kind') != 'analysis' or snapshot.get('kind') != 'analysis'
                or type(snapshot.get('format')) is not int or snapshot['format'] != 1
                or _json(record.get('source')) != _json(source)
                or _json(snapshot.get('source')) != _json(source)
                or _json(record.get('spec')) != _json(snapshot.get('spec'))
                or not re.fullmatch(r'an[0-9]+', str(identity[0]))
                or type(identity[1]) is not int or identity[1] < 1 or identity in seen):
            raise ExportRefused('Choose distinct accepted analyses of this exact table version')
        try:
            body = _json(snapshot).encode()
        except (ValueError, TypeError) as exc:
            raise ExportRefused('The analysis is not finite JSON data') from exc
        if len(body) > MAX_SNAPSHOT_BYTES or hashlib.sha256(body).hexdigest() != record.get('snapshot', {}).get('sha256'):
            raise ExportRefused('The retained analysis snapshot changed')
        if not isinstance(snapshot.get('result'), dict):
            raise ExportRefused('The retained analysis has no computed result')
        seen.add(identity)
        records.append({key: record[key] for key in ('id', 'version', 'title', 'mode', 'source', 'spec', 'by', 'at') if key in record})
        records[-1]['snapshotSha256'] = record['snapshot']['sha256']
        for path, encoded in _provenance(snapshot['result']):
            value = json.loads(encoded)
            kind = ('null' if value is None else 'boolean' if type(value) is bool else
                    'integer' if type(value) is int else 'number' if type(value) is float else
                    'text' if isinstance(value, str) else 'json')
            rows.append([identity[0], identity[1], path, kind, encoded if kind == 'json' else value])
    return rows, records


def export_table(snapshot: dict, record: dict, *, session: str, analyses=()) -> bytes:
    """Export exact saved rows, including exclusions, with their pinned source record.

Data row 1 contains keys (stable, unambiguous); labels, units and declared types are in
Provenance. None is an empty cell, booleans stay booleans, text stays text even if it starts
with '='. Large integers are exact text, with their original type/coordinate documented.
Finite floats use Python's round-trip representation; Excel may round on later editing.
No formulas, external links, macros, source refresh or analysis computation are emitted.
"""
    from .table_reading import snapshot_path
    from .datatables import DataTableRefused
    try:
        snapshot_path(record, session, 'dataset-table')
    except DataTableRefused as error:
        raise ExportRefused(str(error)) from None
    if (snapshot.get('id') != record.get('id') or snapshot.get('version') != record.get('version')
            or not TABLE_ID.fullmatch(str(record.get('id', '')))
            or type(record.get('version')) is not int or record['version'] < 1):
        raise ExportRefused('Choose the exact saved table version from its owning Project')
    try:
        body = _json(snapshot).encode()
    except (ValueError, TypeError) as exc:
        raise ExportRefused('The saved table is not finite JSON data') from exc
    if len(body) > MAX_SNAPSHOT_BYTES:
        raise ExportRefused('The saved table exceeds the supported byte budget')
    expected = record.get('snapshot', {}).get('sha256')
    if hashlib.sha256(body).hexdigest() != expected:
        raise ExportRefused('The table snapshot changed; export its accepted saved version')
    columns, rows = snapshot.get('columns'), snapshot.get('rows')
    if not isinstance(columns, list) or not columns or not isinstance(rows, list):
        raise ExportRefused('The saved table needs declared columns and rows')
    if len(rows) > MAX_ROWS or len(columns) > MAX_COLUMNS or len(rows) * len(columns) > MAX_CELLS:
        raise ExportRefused('The saved table exceeds the supported row or cell budget')
    keys = [c.get('key') for c in columns if isinstance(c, dict)]
    if (len(keys) != len(columns) or any(not isinstance(k, str) or not k for k in keys)
            or len(set(keys)) != len(keys)):
        raise ExportRefused('Table column identities must be unique and nonempty')
    precise = []
    for r, row in enumerate(rows, 2):
        if not isinstance(row, dict) or set(row) - set(keys):
            raise ExportRefused('A row contains values outside its declared columns')
        for c, key in enumerate(keys, 1):
            value = row.get(key)
            if type(value) is int and abs(value) > 999_999_999_999_999:
                precise.append({'cell': f'{_column(c)}{r}', 'type': 'integer', 'storedAs': 'decimal text'})
    # Do not export private snapshot filesystem paths, thread IDs or transport metadata.
    metadata = {'format': 'hooke-datatable-xlsx-v1', 'session': session, 'id': record['id'],
                'version': record['version'], 'snapshotSha256': expected, 'columns': columns,
                'counts': snapshot.get('counts'), 'exactIntegerCells': precise,
                'analyses': 'No retained analyses exported',
                'numericStorage': 'Finite floats use round-trip decimal OOXML; Excel may round after editing.'}
    for key in ('title', 'mode', 'sources', 'added', 'computed', 'values', 'excluded', 'notes', 'change', 'by', 'at'):
        if key in record:
            metadata[key] = record[key]
    if 'comparison' in snapshot:
        metadata['comparison'] = snapshot['comparison']
    analysis_rows, analysis_records = _analyses(analyses, {'session': session, 'id': record['id'],
                                                         'version': record['version'], 'sha256': expected})
    if analysis_records:
        metadata['analyses'] = analysis_records
    names = ('Data', 'Analyses', 'Provenance')
    contents = [
        _sheet(chain([keys], ([row.get(key) for key in keys] for row in rows))),
        _sheet(analysis_rows if analysis_records else [['Status'], ['No retained analyses exported']]),
        _sheet(chain([['JSON pointer', 'JSON value']], _provenance(metadata))),
    ]
    manifest = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    manifest += '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    manifest += '<Default Extension="xml" ContentType="application/xml"/>'
    manifest += '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    for i in range(1, 4):
        manifest += f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    manifest += '</Types>'
    relationships = 'http://schemas.openxmlformats.org/package/2006/relationships'
    rootrels = f'<Relationships xmlns="{relationships}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    workbook = f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>'
    for i, name in enumerate(names, 1):
        workbook += f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
    workbook += '</sheets></workbook>'
    links = f'<Relationships xmlns="{relationships}">' + ''.join(
        f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, 4)) + '</Relationships>'
    files = {'[Content_Types].xml': manifest.encode(), '_rels/.rels': rootrels.encode(),
             'xl/workbook.xml': workbook.encode(), 'xl/_rels/workbook.xml.rels': links.encode()}
    files.update({f'xl/worksheets/sheet{i}.xml': content for i, content in enumerate(contents, 1)})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            info.create_system = 3
            archive.writestr(info, files[name])
    return output.getvalue()
