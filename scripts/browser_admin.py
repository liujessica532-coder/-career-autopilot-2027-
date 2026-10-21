"""Operator CLI; reads the token file but never prints its value."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import httpx
parser=argparse.ArgumentParser()
parser.add_argument('command',choices=['list','start','inspect','receipt-review','revoke','cursor'])
parser.add_argument('--url',default='http://127.0.0.1:8765')
parser.add_argument('--token-file',type=Path,default=Path('private/operator.token'))
parser.add_argument('--session-id')
parser.add_argument('--application-id',type=int)
parser.add_argument('--spec',type=Path)
parser.add_argument('--review',type=Path)
parser.add_argument('--cursor',type=int)
args=parser.parse_args()
parsed=urlsplit(args.url)
if parsed.scheme!='https' and parsed.hostname not in ('127.0.0.1','localhost','::1'):parser.error('Use HTTPS or SSH-forwarded loopback')
headers={'Authorization':'Bearer '+args.token_file.read_text().strip()}
with httpx.Client(base_url=args.url,headers=headers,trust_env=False,timeout=45) as client:
    if args.command=='list':response=client.get('/admin/sessions')
    elif args.command=='start':
        if args.application_id is None or args.spec is None:parser.error('--application-id and --spec required')
        response=client.post('/admin/sessions',json={'application_id':args.application_id,'spec':json.loads(args.spec.read_text())})
    else:
        if not args.session_id:parser.error('--session-id required')
        path='/admin/sessions/'+args.session_id
        if args.command=='inspect':response=client.get(path+'/inspect')
        elif args.command=='receipt-review':
            if args.review is None:parser.error('--review required')
            response=client.post(path+'/receipt-review',json=json.loads(args.review.read_text()))
        elif args.command=='cursor':response=client.post(path+'/cursor',json={'cursor':args.cursor,'confirmed_after_portal_review':True})
        else:response=client.post(path+'/revoke')
    response.raise_for_status()
    print(json.dumps(response.json(),ensure_ascii=False,indent=2))
