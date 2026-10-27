"""Synthetic accepted table → real OOXML; independent openpyxl dev reader, no app/runtime.

Set HOOKE_XLSX_READER_PYTHON to an existing Python with openpyxl if the test venv has
no dev reader. It is not a production dependency and these tests install nothing.
"""
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from xml.etree import ElementTree as ET

import pytest

from orchestrator import xlsx


def accepted():
    columns = [{'key': key, 'label': label, 'unit': unit, 'type': kind, 'origin': 'source'}
               for key, label, unit, kind in [('row', 'Row', '', 'text'), ('value', 'Area', 'µm²', 'number'),
                                             ('note', 'Note', '', 'text'), ('excluded', 'Left out because', '', 'text'),
                                             ('flag', 'Flag', '', 'boolean'), ('id64', 'Exact ID', '', 'number')]]
    rows = [{'row': 'e1:c1', 'value': 1.2345678901234567, 'note': '=1+1 <&> "µ"\n next', 'excluded': None,
             'flag': True, 'id64': 18446744073709551615},
            {'row': 'e1:c2', 'value': -0.0, 'note': '@SUM(A1)  ', 'excluded': 'out of focus',
             'flag': False, 'id64': 123}]
    snapshot = {'id': 'tb1', 'version': 2, 'columns': columns, 'rows': rows,
                'counts': {'rows': 2, 'included': 1, 'excluded': 1}}
    record = {'id': 'tb1', 'version': 2, 'day': '2026-10-23', 'session': 'project-1', 'title': 'Cells', 'mode': 'frozen',
              'sources': [{'day': 'project-1', 'id': 'e1', 'revision': 3, 'digest': 'a' * 64,
                           'units': {'value': 'µm²'}, 'scale': 0.25}],
              'excluded': {'e1:c2': 'out of focus'}, 'computed': [], 'added': [],
              'snapshot': {'sha256': '', 'path': 'project-1/tb1/v2.json'}}
    seal(snapshot, record)
    return snapshot, record


def seal(snapshot, record):
    body = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    record['snapshot']['sha256'] = hashlib.sha256(body).hexdigest()


def test_independent_reader_retains_values_exclusions_types_identity_and_no_formulas(tmp_path):
    snapshot, record = accepted()
    original = copy.deepcopy((snapshot, record))
    body = xlsx.export_table(snapshot, record, session='project-1')
    assert body == xlsx.export_table(snapshot, record, session='project-1')
    assert (snapshot, record) == original
    path = tmp_path / 'table.xlsx'; path.write_bytes(body)
    reader = os.environ.get('HOOKE_XLSX_READER_PYTHON')
    if not reader:
        pytest.importorskip('openpyxl', reason='Independent XLSX dev reader not installed; use HOOKE_XLSX_READER_PYTHON')
        reader = sys.executable
    run = subprocess.run([reader, '-c', '''
import json, sys, openpyxl
book = openpyxl.load_workbook(sys.argv[1], data_only=False)
print(json.dumps({"sheets": book.sheetnames, "rows": list(book["Data"].values),
 "types": [[c.data_type for c in row] for row in book["Data"]],
 "analyses": list(book["Analyses"].values), "provenance": list(book["Provenance"].values)}))
''', str(path)], capture_output=True, text=True, check=True, timeout=30)
    data = json.loads(run.stdout)
    assert data['sheets'] == ['Data', 'Analyses', 'Provenance']
    assert data['rows'][0] == [c['key'] for c in snapshot['columns']]
    assert data['rows'][1] == ['e1:c1', 1.2345678901234567, '=1+1 <&> "µ"\n next', None, True, '18446744073709551615']
    assert data['rows'][2] == ['e1:c2', -0.0, '@SUM(A1)  ', 'out of focus', False, 123]
    assert data['types'][1] == ['s', 'n', 's', 'n', 'b', 's']
    assert not any(kind == 'f' for row in data['types'] for kind in row)
    provenance = {p: json.loads(v) for p, v in data['provenance'][1:]}
    assert provenance['/session'] == 'project-1'
    assert provenance['/snapshotSha256'] == record['snapshot']['sha256']
    assert provenance['/sources/0/revision'] == 3 and provenance['/sources/0/digest'] == 'a' * 64
    assert provenance['/columns/1/unit'] == 'µm²'
    assert provenance['/exactIntegerCells/0/cell'] == 'F2'
    assert provenance['/exactIntegerCells/0/type'] == 'integer'
    assert data['analyses'] == [['Status'], ['No retained analyses exported']]
    assert '/private/must-not-export' not in str(data)


def test_workbook_contains_no_active_content_and_all_xml_is_well_formed():
    snapshot, record = accepted()
    with zipfile.ZipFile(io.BytesIO(xlsx.export_table(snapshot, record, session='project-1'))) as archive:
        assert len(archive.namelist()) == 7
        for name in archive.namelist():
            tree = ET.fromstring(archive.read(name))
            assert all(node.tag.rsplit('}', 1)[-1] not in ('f', 'hyperlink', 'externalLink') for node in tree.iter())
            assert archive.getinfo(name).date_time == (1980, 1, 1, 0, 0, 0)


@pytest.mark.parametrize('damage', ['owner', 'version', 'bytes', 'unknown-column', 'control-character', 'oversized-cell'])
def test_refuses_changed_identity_or_unrepresentable_data_instead_of_losing_it(damage):
    snapshot, record = accepted()
    if damage == 'owner': record['session'] = 'other'
    if damage == 'version': snapshot['version'] = 3
    if damage == 'bytes': snapshot['rows'][0]['value'] = 999
    if damage == 'unknown-column': snapshot['rows'][0]['unlisted'] = 'must not disappear'
    if damage == 'control-character': snapshot['rows'][0]['note'] = 'bad\x01'
    if damage == 'oversized-cell': snapshot['rows'][0]['note'] = 'x' * 32768
    if damage in ('unknown-column', 'control-character', 'oversized-cell'): seal(snapshot, record)
    with pytest.raises(xlsx.ExportRefused): xlsx.export_table(snapshot, record, session='project-1')


def analysis_for(record):
    source = {'session': record['session'], 'id': record['id'], 'version': record['version'],
              'sha256': record['snapshot']['sha256']}
    spec = {'method': 'welch', 'column': 'value', 'groups': ['control', 'treated']}
    snapshot = {'format': 1, 'kind': 'analysis', 'source': source, 'spec': spec,
                'result': {'method': 'welch', 'statistic': 1.2345678901234567, 'p': 0.125,
                           'effect': {'value': None, 'reason': 'insufficient synthetic data'},
                           'groups': [{'label': '=literal', 'n': 2, 'excluded': 1}], 'pairs': []}}
    derivative = {'id': 'an1', 'version': 1, 'kind': 'analysis', 'source': source, 'spec': spec,
                  'mode': 'frozen', 'title': 'Synthetic retained analysis',
                  'snapshot': {'sha256': hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':'), allow_nan=False).encode()).hexdigest()}}
    return derivative, snapshot


def test_analysis_consumes_only_retained_numbers_and_keeps_unavailable_facts_explicit():
    snapshot, record = accepted()
    analysis = analysis_for(record)
    body = xlsx.export_table(snapshot, record, session='project-1', analyses=[analysis])
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        xml = archive.read('xl/worksheets/sheet2.xml')
        assert b'1.2345678901234567' in xml and b'0.125' in xml
        assert b'insufficient synthetic data' in xml and b'=literal' in xml
        assert b'<f>' not in xml and b'No retained analyses exported' not in xml


@pytest.mark.parametrize('damage', ['source', 'table-version', 'spec', 'result', 'duplicate'])
def test_analysis_from_another_revision_or_changed_result_refuses(damage):
    snapshot, record = accepted()
    analysis, result = analysis_for(record)
    if damage == 'source': analysis['source'] = {**analysis['source'], 'session': 'other'}
    if damage == 'table-version': analysis['source'] = {**analysis['source'], 'version': 1}
    if damage == 'spec': analysis['spec'] = {'method': 'different'}
    if damage == 'result': result['result']['p'] = 0.001
    chosen = [(analysis, result)] * (2 if damage == 'duplicate' else 1)
    with pytest.raises(xlsx.ExportRefused):
        xlsx.export_table(snapshot, record, session='project-1', analyses=chosen)


def test_comparison_provenance_uses_exact_accepted_snapshot_warning_and_revisions():
    snapshot, record = accepted()
    snapshot['comparison'] = {'tab': 'comparison', 'layoutRevision': 7, 'capture': 'retained-input',
                              'sourceRevisions': {'e1': 3, 'e2': 5},
                              'note': 'Same objects, not independent samples.'}
    record['comparison'] = {'sourceRevisions': {'e1': 1, 'e2': 2}}
    seal(snapshot, record)
    with zipfile.ZipFile(io.BytesIO(xlsx.export_table(snapshot, record, session='project-1'))) as archive:
        xml = ET.fromstring(archive.read('xl/worksheets/sheet3.xml'))
    rows = [[cell.find('.//{*}t').text for cell in row] for row in xml.findall('.//{*}row')]
    provenance = {key: json.loads(value) for key, value in rows[1:]}
    assert provenance['/comparison/note'] == snapshot['comparison']['note']
    assert provenance['/comparison/sourceRevisions/e1'] == 3
    assert provenance['/comparison/sourceRevisions/e2'] == 5
    assert provenance['/comparison/capture'] == 'retained-input'
