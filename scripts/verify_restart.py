"""Local compose integration check: PostgreSQL, artifacts, browser profile survive restart."""
import subprocess
import json
import time
import uuid
from pathlib import Path
compose=['docker','compose','-f','compose.yaml','-f','deploy/cloud-compose.yaml']
marker=str(uuid.uuid4())
def execute(code):
    return subprocess.check_output(compose+['exec','-T','api','python','-c',code],text=True)
setup='''
import os
from pathlib import Path
from app.db import Session, EventLog
from playwright.sync_api import sync_playwright
marker=MARKER
with Session() as db:
 db.add(EventLog(event='restart_probe',details={'marker':marker}));db.commit()
Path('artifacts/evidence').mkdir(exist_ok=True)
Path('artifacts/evidence/restart-probe.txt').write_text(marker)
with sync_playwright() as p:
 c=p.chromium.launch_persistent_context('state/restart-probe',headless=True,executable_path=os.environ['CHROMIUM_EXECUTABLE'])
 c.add_cookies([{'name':'restart_probe','value':marker,'domain':'example.test','path':'/','expires':2000000000}]);c.close()
'''.replace('MARKER',repr(marker))
execute(setup)
subprocess.run(compose+['restart','db','api','worker'],check=True)
for attempt in range(30):
    try:
        execute("from app.main import health; assert health()['status']=='ok'")
        break
    except subprocess.CalledProcessError:
        time.sleep(1)
else: raise RuntimeError('API/DB did not recover')
check='''
import os,json
from pathlib import Path
from sqlalchemy import select
from app.db import Session,EventLog
from playwright.sync_api import sync_playwright
marker=MARKER
with Session() as db:
 assert any(e.details.get('marker')==marker for e in db.scalars(select(EventLog).where(EventLog.event=='restart_probe')))
assert Path('artifacts/evidence/restart-probe.txt').read_text()==marker
with sync_playwright() as p:
 c=p.chromium.launch_persistent_context('state/restart-probe',headless=True,executable_path=os.environ['CHROMIUM_EXECUTABLE'])
 assert any(x['name']=='restart_probe' and x['value']==marker for x in c.cookies());c.close()
print(json.dumps({'postgres_persisted':True,'artifact_persisted':True,'browser_session_persisted':True,'simulation_only':True}))
'''.replace('MARKER',repr(marker))
result=execute(check)
print(result)
Path('artifacts/evidence/restart.json').write_text(result)
