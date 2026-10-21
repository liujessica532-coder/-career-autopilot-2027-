"""Use the session's existing proxy address and trust; no policy bypass or secret output."""
import os
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse
os.chdir(Path(__file__).resolve().parent.parent)
proxy=urlparse(os.environ['HTTPS_PROXY'])
subprocess.run(['python3','deploy/init_secrets.py'],check=True)
child=os.environ.copy()
child['CLOUD_PROXY_HOST']=proxy.hostname
child['CLOUD_PROXY_IP']=socket.gethostbyname(proxy.hostname)
for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):child.pop(key,None)
args=['docker','--host=unix:///var/run/docker.sock','compose','-f','compose.yaml','-f','deploy/cloud-compose.yaml','up','-d','--no-build','--wait']
raise SystemExit(subprocess.call(args,env=child))
