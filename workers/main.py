import json
import os
import time
import signal
from adapters.browser import BrowserExecutor, NeedsHuman
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from app.db import init_db, Session, Application, Job, HumanAction, EventLog
from app.matching import assess
from adapters.jobs import parse_job_page
from workers.queue import enqueue, claim, fail
import httpx

def run_task(db, task):
    if task.kind == 'discover':
        # Only official sources explicitly configured by the operator. No invented vacancies.
        sources = json.loads(os.getenv('JOB_SOURCES_JSON', '[]'))
        if not sources:
            db.add(EventLog(event='discovery_no_sources', details={'configured': False}))
        for source in sources:
            response = httpx.get(source['url'], timeout=30, follow_redirects=True)
            response.raise_for_status()
            job = db.scalar(select(Job).where(Job.url == source['url']))
            parsed=parse_job_page(response.text)
            assessment=assess(parsed['jd'])
            assessment['source_metadata']={k:v for k,v in parsed.items() if k!='jd'}
            if not job:
                db.add(Job(url=source['url'], title=parsed.get('title') or source['title'], jd=parsed['jd'], assessment=assessment))
            else:
                job.jd=parsed['jd'];job.assessment=assessment
            # HTTP 200 alone NEVER verifies eligibility or open status.
    elif task.kind == 'apply':
        try:
            BrowserExecutor(db).run(task.payload['application_id'], task.payload['spec'], submit=task.payload.get('submit',False))
        except NeedsHuman:
            task.status = 'human_required'
            task.lease_until = None
            db.commit()
            return
    elif task.kind == 'report':
        report = {'at': datetime.now(timezone.utc).isoformat(), 'statuses': dict(db.execute(select(Application.status, func.count()).group_by(Application.status)).all()), 'pending_human_actions': db.scalar(select(func.count()).select_from(HumanAction).where(HumanAction.status == 'pending'))}
        Path('artifacts/reports').mkdir(parents=True, exist_ok=True)
        Path(f'artifacts/reports/{task.key.replace(":", "-")}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    else: raise ValueError('Unknown task type')
    task.status = 'done'
    task.lease_until = None
    db.commit()

def main():
    init_db()
    while True:
        with Session() as db:
            current = datetime.now(ZoneInfo(os.getenv('WORKFLOW_TIMEZONE', 'Asia/Shanghai')))
            enqueue(db, 'discover:'+current.strftime('%Y%m%d%H'), 'discover')
            enqueue(db, 'report:'+current.strftime('%Y%m%d'), 'report')
            task = claim(db, lease_seconds=300)
            if task:
                try:
                    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('Task deadline')))
                    signal.alarm(240)
                    run_task(db, task)
                except Exception as exc:
                    db.rollback()
                    fail(db, task, exc)
                finally:
                    signal.alarm(0)
        time.sleep(5)

if __name__ == '__main__': main()
