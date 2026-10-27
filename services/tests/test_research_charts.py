"""Synthetic structured evidence; no specimen assumptions or provider calls."""
import copy

import pytest

from research_worker.charts import resolve


def fixture():
    document = {'entry': {'day':'2026-10-10', 'id':'motion-a', 'version':2},
                'detail': {'values': {'speed':3.5, 'unit':'µm/s', 'count':7, 'excluded':['edge-cut track']}}}
    point = {'label':'First capture', 'id':'motion-a', 'revision':'retained-digest',
             'valuePath':'/detail/values/speed', 'unitPath':'/detail/values/unit',
             'denominatorPath':'/detail/values/count', 'exclusionsPath':'/detail/values/excluded'}
    return document, [{'title':'Retained speed', 'points':[point]}]


def test_chart_copies_exact_retained_values_and_inspectable_basis():
    document, request = fixture()
    charts = resolve(request, {('motion-a','retained-digest'):document})
    row = charts[0]['rows'][0]
    assert (row['value'], row['unit'], row['denominator']) == (3.5,'µm/s',7)
    assert row['source']['version'] == 2 and row['source']['valuePath'] == '/detail/values/speed'
    assert charts[0]['transformation'] == 'identity'
    document['detail']['values']['excluded'].clear()
    assert row['exclusions'] == ['edge-cut track']


@pytest.mark.parametrize('bad', [None, float('nan'), '3.5 µm/s', True])
def test_chart_refuses_missing_nonfinite_or_model_prose_values(bad):
    document, request = fixture()
    document['detail']['values']['speed'] = bad
    with pytest.raises(ValueError, match='finite retained numbers'):
        resolve(request, {('motion-a','retained-digest'):document})


def test_chart_refuses_changed_revision_or_mixed_units_and_keeps_unknowns():
    document, request = fixture()
    with pytest.raises(ValueError, match='not kept whole'):
        resolve(request, {('motion-a','newer-digest'):document})
    other = copy.deepcopy(document); other['detail']['values']['unit'] = 'pixels/s'
    point = {**request[0]['points'][0], 'id':'motion-b'}
    request[0]['points'].append(point)
    with pytest.raises(ValueError, match='mix units'):
        resolve(request, {('motion-a','retained-digest'):document, ('motion-b','retained-digest'):other})
    request[0]['points'] = [{key:value for key,value in request[0]['points'][0].items()
                             if key not in ('unitPath','denominatorPath','exclusionsPath')}]
    row = resolve(request, {('motion-a','retained-digest'):document})[0]['rows'][0]
    assert row['unit'] is row['denominator'] is row['exclusions'] is None
