import json
from adapters.jobs import parse_job_page

def test_official_structured_jd_expired():
    data={'@context':'https://schema.org','@type':'JobPosting','title':'Graduate Operations','description':'<p>2027 上海 海外运营</p>','validThrough':'2020-01-01T00:00:00Z','jobLocation':{'address':{'addressLocality':'Shanghai'}}}
    html='<script type="application/ld+json">'+json.dumps(data)+'</script>'
    result=parse_job_page(html)
    assert result['structured'] and result['expired'] and '2027' in result['jd']
    assert result['title']=='Graduate Operations'

def test_unstructured_does_not_claim_open():
    result=parse_job_page('<html><p>Apply now</p></html>')
    assert result['expired'] is None and not result['structured']
