"""Build in this cloud sandbox using its existing proxy and trusted public CA bundle.
No proxy credentials or values are written into source or printed.
"""
import os
import socket
import subprocess
from urllib.parse import urlparse
from pathlib import Path
os.chdir(Path(__file__).resolve().parent.parent)
args=['docker','--host=unix:///var/run/docker.sock','build','--secret','id=cloud_ca,src=/etc/ssl/certs/ca-certificates.crt']
proxy=urlparse(os.getenv('HTTPS_PROXY',''))
if proxy.hostname:
    args+=['--add-host',f'{proxy.hostname}:{socket.gethostbyname(proxy.hostname)}']
for key in ('HTTP_PROXY','HTTPS_PROXY','NO_PROXY','http_proxy','https_proxy','no_proxy'):
    if key in os.environ: args+=['--build-arg',key]
args+=['-t','career-autopilot:phase1','.']
build_env=os.environ.copy()
for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'): build_env.pop(key,None)
raise SystemExit(subprocess.call(args,env=build_env))
