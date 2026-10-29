"""Synthetic numerical oracles for explicit whole-context effect diagnostics."""
import json
import numpy as np
import pytest
from scientific_compute import engine, aggregate_profiles
from scientific_compute.catalog import parameters


def prepared(tmp_path, name, values, *, context='held-out', targets=('x','y'), genes=('a','b'), total=10000):
    folder=tmp_path/name; folder.mkdir()
    meta={'format':'response-effects-v1','shape':[len(targets),len(genes)],'contexts':[context],
        'scale':'log2-normalized-count-ratio','normalization':{'genes':list(genes),'total':total,
        'pseudocount':1.,'method':'aggregate-count-proportions'},'provenance':{'fixture':True}}
    return aggregate_profiles._save(folder,np.array(values,dtype=float),list(genes),[context]*len(targets),
        list(targets),[10]*len(targets),meta)[0]


def model(tmp_path, values=((2.,-2.),(4.,0.))):
    source=prepared(tmp_path,'train',values,context='training')
    folder=tmp_path/'fit';folder.mkdir()
    return engine.fit({'effects':source},parameters('response-fit',{'regularization':1}),folder,lambda:None)[0]


def evaluate(tmp_path, fitted, reference):
    folder=tmp_path/'evaluate';folder.mkdir()
    return engine.OPERATIONS['response-effect-evaluate']({'model':fitted,'reference':reference},
        parameters('response-effect-evaluate',{}),folder,lambda:None)


def test_transfer_matches_hand_calculated_error_and_zero_effect_baseline(tmp_path):
    fitted=model(tmp_path)
    reference=prepared(tmp_path,'reference',[[2.,-1.],[1.,0.]])
    path,result=evaluate(tmp_path,fitted,reference)
    assert result['competitionScore'] is False and result['sealedHoldout'] is False
    assert result['overall']['mse']==.5
    assert result['overall']['zeroEffectMse']==1.5
    assert result['overall']['mae']==.5
    assert result['overall']['zeroEffectMae']==1.
    assert result['overall']['mseSkill']==pytest.approx(2/3)
    assert json.loads(path.read_text())==result
    assert result['trainingContexts']==['training']
    assert result['evaluatedContexts']==['held-out']


def test_same_context_is_refused_instead_of_reporting_generalization(tmp_path):
    fitted=model(tmp_path)
    reference=prepared(tmp_path,'reference',[[1.,1.],[2.,2.]],context='training')
    with pytest.raises(engine.Refused,match='overlap'):
        evaluate(tmp_path,fitted,reference)


@pytest.mark.parametrize('change',['normalization','genes'])
def test_incompatible_meaning_is_refused(tmp_path,change):
    fitted=model(tmp_path)
    reference=prepared(tmp_path,'reference',[[1.,1.],[2.,2.]],
        total=100 if change=='normalization' else 10000,
        genes=('b','a') if change=='genes' else ('a','b'))
    with pytest.raises(engine.Refused,match='normalization|ordered gene'):
        evaluate(tmp_path,fitted,reference)


def test_unseen_targets_are_counted_as_zero_effect_without_dropping_rows(tmp_path):
    fitted=model(tmp_path)
    reference=prepared(tmp_path,'reference',[[1.,-1.],[3.,4.]],targets=('x','unseen'))
    _,result=evaluate(tmp_path,fitted,reference)
    assert result['coverage']['unseenTargets']==['unseen']
    assert len(result['rows'])==2 and result['overall']['mse']==6.25


def test_zero_effect_reference_has_no_division_or_claimed_skill(tmp_path):
    fitted=model(tmp_path,values=((0.,0.),(0.,0.)))
    reference=prepared(tmp_path,'reference',[[0.,0.],[0.,0.]])
    _,result=evaluate(tmp_path,fitted,reference)
    assert result['overall']['mse']==0 and result['overall']['mseSkill'] is None


def test_legacy_model_without_preparation_meaning_is_explicitly_refused(tmp_path):
    fitted=model(tmp_path)
    data=engine._model(fitted); meta=json.loads(data['metadata'].item());meta.pop('normalization',None)
    np.savez_compressed(fitted,**{**data,'metadata':np.array(json.dumps(meta))})
    reference=prepared(tmp_path,'reference',[[1.,1.],[2.,2.]])
    with pytest.raises(engine.Refused,match='refit'):
        evaluate(tmp_path,fitted,reference)
