"""Isolated synthetic records only; no real candidate files used."""
import hashlib
from pathlib import Path
import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from app.db import Base, Candidate, Job, Application, Artifact, Task
from scripts.records_bundle import export_records, import_records


def test_encrypted_records_roundtrip_preserves_pauses_without_replaying_tasks(tmp_path, monkeypatch):
    source=tmp_path/'source';source.mkdir();monkeypatch.chdir(source)
    files=source/'artifacts';files.mkdir()
    resume=files/'synthetic.pdf';resume.write_bytes(b'SYNTHETIC RESUME ONLY')
    key=tmp_path/'key';key.write_bytes(Fernet.generate_key())
    engine=create_engine('sqlite:///'+str(source/'records.db'));Base.metadata.create_all(engine)
    with sessionmaker(engine)() as db:
        candidate=Candidate(profile={'name':'SYNTHETIC'},source_pdf=str(resume),verified=True);db.add(candidate);db.flush()
        for index,state in enumerate(['needs_human','needs_human','not_started']):
            job=Job(url=f'https://example.test/{index}',title='SYNTHETIC',jd='TEST');db.add(job);db.flush()
            application=Application(candidate_id=candidate.id,job_id=job.id,status=state);db.add(application);db.flush()
            db.add(Artifact(application_id=application.id,kind='resume',path=str(resume),sha256=hashlib.sha256(resume.read_bytes()).hexdigest()))
        db.add(Task(key='DO NOT REPLAY',kind='apply'));db.commit()
    bundle=tmp_path/'records.enc';result=export_records(source/'records.db',bundle,key)
    assert result['recorded_files']==1 and b'SYNTHETIC' not in bundle.read_bytes()
    destination=tmp_path/'target';destination.mkdir();monkeypatch.chdir(destination)
    target=create_engine('sqlite:///'+str(destination/'records.db'));Base.metadata.create_all(target);sessions=sessionmaker(target)
    wrong=tmp_path/'wrong';wrong.write_bytes(Fernet.generate_key())
    with pytest.raises(InvalidToken):import_records(bundle,wrong,sessions)
    imported=import_records(bundle,key,sessions)
    assert not imported['already_imported'] and import_records(bundle,key,sessions)['already_imported']
    with sessions() as db:
        applications=list(db.scalars(select(Application).order_by(Application.id)))
        assert [a.status for a in applications]==['needs_human','needs_human','not_started']
        assert all(a.submitted_at is None and a.receipt_number is None for a in applications)
        assert db.scalar(select(func.count()).select_from(Task))==0
        assert db.scalar(select(Candidate)).source_pdf=='artifacts/synthetic.pdf'
    assert Path('artifacts/synthetic.pdf').read_bytes()==b'SYNTHETIC RESUME ONLY'
    other=tmp_path/'other.enc';monkeypatch.chdir(source);export_records(source/'records.db',other,key);monkeypatch.chdir(destination)
    with pytest.raises(ValueError,match='already contains'):import_records(other,key,sessions)
    engine.dispose();target.dispose()
