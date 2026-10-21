"""Encrypted, record-scoped transfer. No cookies, browser profiles or secret files exported."""
import argparse
import hashlib
import io
import json
import os
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.db import Candidate,Job,Application,Artifact,EventLog,HumanAction,Session,init_db

MODELS=[Candidate,Job,Application,Artifact,EventLog,HumanAction]

def export_records(source, output, key_file):
    source=Path(source).resolve();root=Path.cwd().resolve()
    if Path(output).exists():raise ValueError('Refuse to overwrite an existing encrypted bundle')
    with tempfile.TemporaryDirectory() as temporary:
        snapshot=Path(temporary)/'records.db'
        with sqlite3.connect(source) as src,sqlite3.connect(snapshot) as dst:src.backup(dst)
        engine=create_engine('sqlite:///'+str(snapshot));files={}
        with sessionmaker(engine)() as db:
            candidates=list(db.scalars(select(Candidate)));artifacts=list(db.scalars(select(Artifact)))
            paths=[p for c in candidates for p in [c.source_pdf,c.photo] if p]+[a.path for a in artifacts]
            for raw in paths:
                path=Path(raw).resolve()
                if not path.is_relative_to(root/'artifacts'):raise ValueError('Only recorded artifact files within artifacts/ may be exported')
                if not path.is_file():raise ValueError('A recorded artifact is missing; preserve it before transfer')
                relative=str(path.relative_to(root));files[relative]=path
        engine.dispose()
        manifest={name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in files.items()}
        memory=io.BytesIO()
        with tarfile.open(fileobj=memory,mode='w') as archive:
            archive.add(snapshot,arcname='records.db')
            for name,path in files.items():archive.add(path,arcname=name,recursive=False)
            metadata=json.dumps({'source_root':str(root),'files':manifest}).encode();info=tarfile.TarInfo('manifest.json');info.size=len(metadata);archive.addfile(info,io.BytesIO(metadata))
        encrypted=Fernet(Path(key_file).read_bytes().strip()).encrypt(memory.getvalue())
        target=Path(output);target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(encrypted)
    return {'bundle':str(output),'recorded_files':len(files),'sha256':hashlib.sha256(encrypted).hexdigest()}

def import_records(bundle,key_file,sessions=Session):
    encrypted=Path(bundle).read_bytes();digest=hashlib.sha256(encrypted).hexdigest()
    with sessions() as db:
        for event in db.scalars(select(EventLog).where(EventLog.event=='records_bundle_imported')):
            if event.details.get('sha256')==digest:return {'already_imported':True,'mapping':event.details['mapping']}
        # Conservative: do not merge or overwrite a populated candidate/application dataset.
        if db.scalar(select(Candidate.id).limit(1)) or db.scalar(select(Application.id).limit(1)):
            raise ValueError('Target already contains candidate/application records; explicit reconciliation required')
    plain=Fernet(Path(key_file).read_bytes().strip()).decrypt(encrypted)
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(plain)) as archive:
            for member in archive.getmembers():
                if not member.isfile() or not (root/member.name).resolve().is_relative_to(root):raise ValueError('Unsafe bundle entry')
            archive.extractall(root,filter='data')
        metadata=json.loads((root/'manifest.json').read_text())
        for name,expected in metadata['files'].items():
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:raise ValueError('Artifact checksum mismatch')
            target=Path(name)
            if target.is_absolute() or '..' in target.parts or not name.startswith('artifacts/'):raise ValueError('Unsafe artifact path')
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=expected:raise ValueError('Refuse to overwrite different local artifact')
        source_engine=create_engine('sqlite:///'+str(root/'records.db'))
        mapping={model.__name__:{} for model in MODELS}
        def path(raw):
            if raw is None:return None
            p=Path(raw)
            return str(p.relative_to(metadata['source_root'])) if p.is_absolute() else raw
        with sessionmaker(source_engine)() as old,sessions() as target:
            for c in old.scalars(select(Candidate)):
                n=Candidate(profile=c.profile,source_pdf=path(c.source_pdf),photo=path(c.photo),verified=c.verified);target.add(n);target.flush();mapping['Candidate'][c.id]=n.id
            for j in old.scalars(select(Job)):
                n=target.scalar(select(Job).where(Job.url==j.url))
                if not n:n=Job(url=j.url,title=j.title,jd=j.jd,assessment=j.assessment,checked_at=j.checked_at);target.add(n);target.flush()
                mapping['Job'][j.id]=n.id
            for a in old.scalars(select(Application)):
                if a.status in ('submitted','simulated','submission_uncertain'):
                    state=a.status
                elif a.status in ('draft','not_started','blocked_navigation'):state='not_started'
                else:state='needs_human'
                n=Application(candidate_id=mapping['Candidate'][a.candidate_id],job_id=mapping['Job'][a.job_id],status=state,checkpoint=a.checkpoint,submitted_at=a.submitted_at,receipt_number=a.receipt_number);target.add(n);target.flush();mapping['Application'][a.id]=n.id
            for model in [Artifact,EventLog,HumanAction]:
                for old_row in old.scalars(select(model)):
                    fields={column.name:getattr(old_row,column.name) for column in model.__table__.columns if column.name!='id'}
                    fields['application_id']=mapping['Application'].get(old_row.application_id) if old_row.application_id else None
                    if model is Artifact:fields['path']=path(fields['path'])
                    if model is EventLog:fields['details']={**fields['details'],'legacy_event_id':old_row.id}
                    target.add(model(**fields))
            # Copy only verified files; browser state/tasks are deliberately not replayed.
            for name in metadata['files']:
                dest=Path(name);dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                if not dest.exists():
                    fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'wb') as out:out.write((root/name).read_bytes())
            target.add(EventLog(event='records_bundle_imported',details={'sha256':digest,'mapping':mapping,'tasks_replayed':False}));target.commit()
        source_engine.dispose()
    return {'already_imported':False,'mapping':mapping}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['export','import']);p.add_argument('--key-file',type=Path,required=True);p.add_argument('--source',type=Path);p.add_argument('--output',type=Path);p.add_argument('--input',type=Path);args=p.parse_args()
    if args.command=='export':
        if not args.source or not args.output:p.error('--source and --output required')
        result=export_records(args.source,args.output,args.key_file)
    else:
        if not args.input:p.error('--input required')
        init_db();result=import_records(args.input,args.key_file)
    print(json.dumps(result,ensure_ascii=False,indent=2))
