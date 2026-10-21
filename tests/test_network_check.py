from scripts.recaptcha_check import check
import pytest

def test_no_request_before_domain_publication(tmp_path):
    def forbidden(*args,**kwargs):raise AssertionError('Request must not run')
    with pytest.raises(ValueError):check('test-version',{},tmp_path/'check.json',forbidden)

def test_exactly_one_normal_request_per_saved_result(tmp_path):
    calls=[]
    def fetch(*args,**kwargs):
        calls.append(args[0])
        return type('Response',(),{'status_code':403})()
    policy={'http_network_policy':{'egress_rules':[{'host':'recaptcha.net'}]}}
    output=tmp_path/'result.json'
    assert check('test-version',policy,output,fetch)['status']==403
    assert check('test-version',policy,output,fetch)['status']==403
    assert len(calls)==1
