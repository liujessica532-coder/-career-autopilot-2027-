"""One normal TLS request per verified published configuration. Never solves CAPTCHA."""
import argparse
import json
from pathlib import Path
from datetime import datetime,timezone
import httpx

def check(published_version, policy, output, fetch=httpx.get):
    output=Path(output)
    if output.exists():return json.loads(output.read_text())
    hosts=[r['host'] for r in policy.get('http_network_policy',{}).get('egress_rules',[])]
    if 'recaptcha.net' not in hosts:
        raise ValueError('Published runtime policy does not include recaptcha.net; no network request performed')
    result={'published_version':published_version,'at':datetime.now(timezone.utc).isoformat(),'url':'https://recaptcha.net/recaptcha/api.js'}
    try:
        response=fetch(result['url'],timeout=20,follow_redirects=False)
        result['status']=response.status_code
    except Exception as exc:
        result['error']=type(exc).__name__
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--published-version',required=True,help='Version independently verified in environment status after publication')
    parser.add_argument('--output',type=Path,required=True,help='Keep this result; do not retry the same published version')
    args=parser.parse_args()
    policy=json.loads(Path('/etc/codex/network-policy.json').read_text())
    print(json.dumps(check(args.published_version,policy,args.output),indent=2))
