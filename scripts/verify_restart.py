"""Restart existing local Compose services; never navigates to an employer website.
Run without concurrent applications. Preserves named volumes and actual records.
"""
import subprocess
import json
import time
import uuid
from pathlib import Path
compose=['docker','--host=unix:///var/run/docker.sock','compose','-f','compose.yaml']
marker=str(uuid.uuid4())
def execute(service,code):
    return subprocess.check_output(compose+['exec','-T',service,'python','-c',code],text=True,stderr=subprocess.PIPE)
execute('api', '''
from pathlib import Path
from app.db import Session, EventLog
with Session() as db:
 db.add(EventLog(event='restart_probe',details={'marker':MARKER}));db.commit()
Path('artifacts/evidence').mkdir(exist_ok=True)
Path('artifacts/evidence/restart-probe.txt').write_text(MARKER)
'''.replace('MARKER',repr(marker)))
execute('browser', '''
import os
from app.session_vault import SessionVault
vault=SessionVault(os.environ['BROWSER_STATE_DIR']+'/encrypted',os.environ['BROWSER_STATE_KEY_FILE'])
vault.save(MARKER,{'synthetic_restart_probe':MARKER})
assert MARKER.encode() not in vault.path(MARKER).read_bytes()
'''.replace('MARKER',repr(marker)))
subprocess.run(compose+['restart','db','api','browser','worker'],check=True)
checks={
'api':'''from sqlalchemy import select
from pathlib import Path
from app.db import Session,EventLog
with Session() as db:
 assert any(e.details.get('marker')==MARKER for e in db.scalars(select(EventLog).where(EventLog.event=='restart_probe')))
assert Path('artifacts/evidence/restart-probe.txt').read_text()==MARKER
''',
'browser':'''import os,urllib.request
from app.session_vault import SessionVault
urllib.request.urlopen('http://127.0.0.1:8765/health')
vault=SessionVault(os.environ['BROWSER_STATE_DIR']+'/encrypted',os.environ['BROWSER_STATE_KEY_FILE'])
assert vault.load(MARKER)=={'synthetic_restart_probe':MARKER}
''',
'worker':'''from workers.health import check
check()
'''}
# Total deadline bounded; each iteration yields after one-second backoff.
for service,code in checks.items():
    deadline=time.monotonic()+50
    while True:
        try:execute(service,code.replace('MARKER',repr(marker)));break
        except subprocess.CalledProcessError:
            if time.monotonic()>deadline:raise
            time.sleep(1)
result={'postgres_persisted':True,'artifact_persisted':True,'encrypted_state_persisted':True,'browser_healthy':True,'worker_healthy':True,'simulation_only':True,'employer_requests':0}
Path('artifacts/evidence').mkdir(exist_ok=True)
Path('artifacts/evidence/handover-restart.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
