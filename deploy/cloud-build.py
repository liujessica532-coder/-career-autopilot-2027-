"""Build in this cloud sandbox using its existing proxy and trusted public CA bundle.
No proxy credentials or values are written into source or printed.
"""
import os
import socket
import subprocess
from urllib.parse import urlparse
from pathlib import Path
os.chdir(Path(__file__).resolve().parent.parent)
args=['docker','build','--network','host','--secret','id=cloud_ca,src=/etc/ssl/certs/ca-certificates.crt']
proxy=urlparse(os.getenv('HTTPS_PROXY',''))
if proxy.hostname:
    args+=['--add-host',f'{proxy.hostname}:{socket.gethostbyname(proxy.hostname)}']
for key in ('HTTP_PROXY','HTTPS_PROXY','NO_PROXY','http_proxy','https_proxy','no_proxy'):
    if key in os.environ: args+=['--build-arg',key]
args+=['-t','career-autopilot:phase1','.']
raise SystemExit(subprocess.call(args,env={**os.environ,'DOCKER_CONFIG':os.environ.get('DOCKER_CONFIG','/workspace/career-docker')}))
