"""Operator CLI. No HTTP endpoint exposes candidate or browser state."""
import argparse
import json
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import select
from app.db import init_db, Session, Candidate, Job, Application, HumanAction, EventLog, Task, now
from app.candidate import import_candidate
from app.matching import assess
from app.materials import generate
from workers.queue import enqueue

parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest='command', required=True)
sub.add_parser('init')
p=sub.add_parser('import');p.add_argument('--pdf',required=True,type=Path);p.add_argument('--photo',type=Path)
p=sub.add_parser('verify-profile');p.add_argument('--candidate-id',required=True,type=int);p.add_argument('--profile',required=True,type=Path);p.add_argument('--confirmed',action='store_true',required=True)
p=sub.add_parser('add-job');p.add_argument('--record',required=True,type=Path)
p=sub.add_parser('prepare');p.add_argument('--candidate-id',required=True,type=int);p.add_argument('--job-id',required=True,type=int);p.add_argument('--language',choices=['zh','en'],default='zh')
p=sub.add_parser('enqueue-application');p.add_argument('--application-id',required=True,type=int);p.add_argument('--spec',required=True,type=Path);p.add_argument('--submit-authorized',action='store_true')
p=sub.add_parser('resume-application');p.add_argument('--application-id',required=True,type=int);p.add_argument('--spec',required=True,type=Path);p.add_argument('--confirmed',action='store_true',required=True)
args=parser.parse_args()
init_db()
with Session() as db:
    if args.command=='import':
        c=import_candidate(db,args.pdf,args.photo);print(json.dumps({'candidate_id':c.id,'verified':False}))
    elif args.command=='verify-profile':
        c=db.get(Candidate,args.candidate_id)
        if not c: raise ValueError('Candidate not found')
        profile=json.loads(args.profile.read_text())
        if not profile.get('name') or not profile.get('facts'): raise ValueError('name and facts required')
        if any(not isinstance(f,str) or f not in c.profile['source_text'] for f in profile['facts']):
            raise ValueError('Facts must be verbatim excerpts of imported resume; update source resume for corrections')
        c.profile={**profile,'source_text':c.profile['source_text']};c.verified=True
        db.add(EventLog(event='profile_human_verified',details={'candidate_id':c.id}))
        db.commit();print('Profile verified by operator')
    elif args.command=='add-job':
        record=json.loads(args.record.read_text())
        # Human verification is explicit and auditable; never inferred from HTTP success.
        evidence=record.get('evidence',{})
        if any(record.get(k) and not evidence.get(k) for k in ('open_verified','graduation_verified','hk_sponsorship_verified')):
            raise ValueError('Each verification requires an exact quoted source and checked official URL')
        assessment=assess(record['jd'],open_verified=record.get('open_verified',False),graduation_verified=record.get('graduation_verified',False),hk_sponsorship_verified=record.get('hk_sponsorship_verified',False))
        assessment['evidence']=evidence
        j=Job(url=record['url'],title=record['title'],jd=record['jd'],assessment=assessment)
        db.add(j);db.commit();print(json.dumps({'job_id':j.id,'assessment':assessment},ensure_ascii=False))
    elif args.command=='prepare':
        c=db.get(Candidate,args.candidate_id);j=db.get(Job,args.job_id)
        if not c or not j: raise ValueError('Candidate or job missing')
        a=db.scalar(select(Application).where(Application.candidate_id==c.id,Application.job_id==j.id))
        if not a:
            a=Application(candidate_id=c.id,job_id=j.id);db.add(a);db.commit()
        if a.status not in ('draft','prepared','needs_human'): raise ValueError('Application already in progress/completed')
        paths=generate(db,c,j,a.id,args.language)
        print(json.dumps({'application_id':a.id,'files':[str(p) for p in paths]}))
    elif args.command=='enqueue-application':
        spec=json.loads(args.spec.read_text())
        if not spec.get('reviewed'): raise ValueError('Mapping must be reviewed for the official site')
        # Capture the reviewed mapping by value; later edits cannot change queued application.
        spec['authorized']=args.submit_authorized
        enqueue(db,f'application:{args.application_id}','apply',{'application_id':args.application_id,'spec':spec,'submit':args.submit_authorized})
        print('Application task queued; omitted authorization means upload-only')

    elif args.command=='resume-application':
        app=db.get(Application,args.application_id)
        if not app or app.status in ('submitted','submitting','submission_uncertain'):
            raise ValueError('Cannot resume submitted or ambiguous application; reconcile externally')
        task=db.scalar(select(Task).where(Task.key==f'application:{app.id}'))
        if not task or task.status not in ('human_required','failed'):
            raise ValueError('No paused/failed task eligible for resumption')
        spec=json.loads(args.spec.read_text())
        if not spec.get('reviewed'): raise ValueError('Reviewed mapping required')
        # Retain original final-submission authorization; resumption never grants new authority.
        spec['authorized']=task.payload.get('submit',False)
        task.payload={**task.payload,'spec':spec}
        task.status='pending';task.attempts=0;task.available_at=now();task.lease_until=None
        for action in db.scalars(select(HumanAction).where(HumanAction.application_id==app.id,HumanAction.status=='pending')):
            action.status='resolved_by_operator'
        db.add(EventLog(application_id=app.id,event='operator_resumed',details={}))
        db.commit();print('Application resumed after operator review')
