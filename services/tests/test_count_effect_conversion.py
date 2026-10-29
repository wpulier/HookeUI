"""Analytical effect-to-proportion oracles, independent of count sampling."""
import numpy as np
import pytest
from scientific_compute import count_emission as emission


def meaning(genes=('a','b','c')):
    return {'scale':'log2-normalized-count-ratio','normalization':{'genes':list(genes),
        'total':100.,'pseudocount':1.,'method':'aggregate-count-proportions'}}


def test_inverse_recovers_known_composition_including_zero_control_gene():
    before=np.array([.8,.2,0.]);after=np.array([.5,.4,.1])
    effects=np.log2((after*100+1)/(before*100+1))
    actual, projected=emission.proportions(before,effects,['a','b','c'],meaning(),'pseudocount-inverse-v1')
    np.testing.assert_allclose(actual,after,rtol=1e-12,atol=1e-12)
    assert projected==0


def test_normalization_subset_uses_its_declared_denominator():
    before=np.array([.4,.1,.5]);after=np.array([.2,.3,.5])
    effects=np.array([np.log2(41/81),np.log2(61/21),0.])
    actual,_=emission.proportions(before,effects,['a','b','outside'],meaning(('a','b')),'pseudocount-inverse-v1')
    np.testing.assert_allclose(actual,after)


def test_null_keeps_exact_proportions_and_legacy_remains_explicit():
    p=np.array([.7,.3,0.])
    actual,count=emission.proportions(p,np.zeros(3),['a','b','c'],meaning(),'pseudocount-inverse-v1')
    np.testing.assert_array_equal(actual,p);assert count==0
    actual,_=emission.proportions(p,np.array([1.,0.,2.]),['a','b','c'],{},'proportional-fold-v1')
    np.testing.assert_allclose(actual,[1.4/1.7,.3/1.7,0.])


def test_negative_inverse_is_explicitly_projected_onto_nonnegative_counts():
    actual,projected=emission.proportions(np.array([0.,1.]),np.array([-1.,0.]),['a','b'],meaning(('a','b')),'pseudocount-inverse-v1')
    np.testing.assert_array_equal(actual,[0.,1.]);assert projected==1


@pytest.mark.parametrize('change',['missing','unknown','method','scale','total','pseudocount'])
def test_unknown_or_incompatible_meaning_refuses(change):
    m=meaning()
    if change=='unknown':m={}
    elif change=='scale':m['scale']='unknown'
    elif change=='missing':m['normalization']['genes']=['absent']
    elif change=='method':m['normalization']['method']='unknown'
    else:m['normalization'][change]=float('nan')
    with pytest.raises(ValueError):emission.proportions(np.array([.5,.5,0.]),np.ones(3),['a','b','c'],m,'pseudocount-inverse-v1')
