"""Synthetic typed reader observations; real issuer path with stand-in worker, no native proof."""
import copy
import hashlib
from types import SimpleNamespace

import numpy as np
import pytest

from env_worker.scientific_reader_probe import observe
from gpu_worker.reader_proof_contract import observation
from tests.scientific_threads import exclude_preexisting_test_harness_threads  # noqa:F401
from tests.test_env_runner import home  # noqa:F401
from tests.test_scientific_reader_proof import prepared_reader


def reader(kind='labels', dtype='>u2'):
    values=np.zeros((40,60),dtype=dtype);values[10:20,20:40]=1
    layer=SimpleNamespace(_type_string=kind,rgb=False,multiscale=False,data=values,
        ndim=2,axis_labels=('y','x'),units=('micrometer','micrometer'),
        data_to_world=lambda point:tuple(v*s for v,s in zip(point,(.5,.25))))
    return SimpleNamespace(viewer=SimpleNamespace(layers=[layer]),view_bindings=SimpleNamespace(key_for=lambda _: 'rectangle'))


def test_labels_observation_has_exact_explicit_semantics_and_calibration():
    host=reader();actual=observe(host);values=host.viewer.layers[0].data
    assert actual=={'format':'reader-data-contract-v2','layers':[{
        'key':'rectangle','type':'labels','axes':['y','x'],'units':['micrometer']*2,
        'basis':[[0.,0.],[.5,0.],[0.,.25]],'levels':[{'shape':[40,60],'dtype':'>u2',
        'sha256':hashlib.sha256(repr((values.dtype.str,values.shape)).encode()+values.tobytes()).hexdigest()}]}]}
    assert observation(actual)==actual
    legacy=observe(reader(kind='image'))
    assert legacy['format']=='reader-data-contract-v1' and 'type' not in legacy['layers'][0]
    assert legacy['layers'][0]['levels']==actual['layers'][0]['levels']
    assert actual!=legacy


@pytest.mark.parametrize('problem',['float','negative','multiscale'])
def test_incompatible_labels_cannot_issue_observation(problem):
    host=reader(dtype='<f4' if problem=='float' else '<i2');layer=host.viewer.layers[0]
    if problem=='negative':layer.data[0,0]=-1
    if problem=='multiscale':layer.multiscale=True;layer.data=[layer.data,layer.data[::2,::2]]
    with pytest.raises(ValueError,match='[Ll]abel'):observe(host)


@pytest.mark.parametrize('change',['missing-type','other-type','float','multilevel','legacy-label'])
def test_typed_wire_refuses_incompatible_or_ambiguous_semantics(change):
    actual={'format':'reader-data-contract-v2','layers':[{
        'key':'rectangle','type':'labels','axes':['y','x'],'units':['micrometer']*2,
        'basis':[[0.,0.],[.5,0.],[0.,.25]],'levels':[{'shape':[40,60],'dtype':'>u2','sha256':'a'*64}]}]}
    layer=actual['layers'][0]
    if change=='missing-type':layer.pop('type')
    if change=='other-type':layer['type']='surface'
    if change=='float':layer['levels'][0]['dtype']='<f4'
    if change=='multilevel':layer['levels']*=2
    if change=='legacy-label':actual['format']='reader-data-contract-v1'
    with pytest.raises(ValueError):observation(actual)


def test_parent_does_not_qualify_labels_against_unchanged_image_reference(home,monkeypatch):
    from orchestrator import env_runner,reader_proof
    owner,_,expected=prepared_reader(home,monkeypatch)
    changed=copy.deepcopy(expected);changed['format']='reader-data-contract-v2'
    changed['layers'][0]['type']='labels'
    monkeypatch.setattr(reader_proof,'_observe',lambda *args:changed)
    result=env_runner.prove(owner)
    assert not result['ok']
    assert 'reference' in str(result).lower() or 'differ' in str(result).lower()


def test_parent_seals_exact_typed_labels_proof_without_workspace_qualification(home,monkeypatch):
    from orchestrator import env_runner,reader_proof,bundle_runtime
    from orchestrator.maintenance import promote
    expected=observe(reader())
    owner,folder,_=prepared_reader(home,monkeypatch,expected=expected)
    monkeypatch.setattr(reader_proof,'_observe',lambda *args:copy.deepcopy(expected))
    proof=env_runner.prove(owner)
    assert proof['ok'] and proof['fullQualification'] is False
    installed=env_runner.generation(owner,folder.name)
    assert installed is not None and bundle_runtime.proof_complete(installed.m,folder,proof)
    assert promote.first_check(owner,folder.name)==''
    changed=copy.deepcopy(expected);changed['layers'][0]['type']='image'
    monkeypatch.setattr(reader_proof,'_observe',lambda *args:changed)
    assert 'differ' in promote.first_check(owner,folder.name)
