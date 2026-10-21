from pathlib import Path
from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas
from app.db import Candidate, Job, Application, Task, now
from app.matching import assess
from app.materials import generate
from app.candidate import import_candidate
from workers.queue import enqueue, claim, fail

def test_matching_requires_evidence():
    jd = '2027 北京 海外运营 应届毕业生'
    assert not assess(jd)['eligible']
    assert assess(jd, open_verified=True, graduation_verified=True)['eligible']
    assert not assess(jd+' 职位已关闭', open_verified=True, graduation_verified=True)['eligible']
    assert not assess('2027 Hong Kong operations', open_verified=True, graduation_verified=True)['eligible']

def test_queue_dedup_retry_restart(db):
    enqueue(db, 'one', 'report'); enqueue(db, 'one', 'report')
    task = claim(db, lease_seconds=-1)
    assert task.attempts == 1
    task = claim(db)
    assert task.attempts == 2
    fail(db, task, RuntimeError('sensitive'))
    assert task.status == 'pending' and task.error == 'RuntimeError'
    assert claim(db) is None
    task.available_at = now()-timedelta(seconds=1); db.commit()
    assert claim(db).attempts == 3
    assert len(db.scalars(select(Task)).all()) == 1

def test_application_dedup(db):
    c=Candidate(profile={},source_pdf='fixture');j=Job(url='https://example.test/job',title='test',jd='test')
    db.add_all([c,j]);db.commit()
    db.add(Application(candidate_id=c.id,job_id=j.id));db.commit()
    db.add(Application(candidate_id=c.id,job_id=j.id))
    with pytest.raises(IntegrityError): db.commit()

def test_import_requires_review(db,tmp_path):
    path=tmp_path/'source.pdf';c=Canvas(str(path));c.drawString(30,700,'SYNTHETIC TEST CANDIDATE');c.save()
    candidate=import_candidate(db,path)
    assert not candidate.verified
    assert 'SYNTHETIC' in candidate.profile['source_text']

def test_material_truth_gate_and_pdf(db):
    candidate=Candidate(profile={'name':'TEST FIXTURE - NOT REAL','facts':['TEST ONLY: English translation student']},source_pdf='test',verified=False)
    job=Job(url='https://example.test/material',title='Test operations',jd='translation')
    db.add_all([candidate,job]);db.commit()
    with pytest.raises(ValueError): generate(db,candidate,job)
    candidate.verified=True
    pdf,intro=generate(db,candidate,job)
    assert 'TEST ONLY' in '\n'.join(p.extract_text() for p in PdfReader(pdf).pages)
    assert 'TEST ONLY' in intro.read_text()


def test_chinese_pdf_preserves_photo_and_facts(db,tmp_path):
    from PIL import Image
    photo=tmp_path/'photo.png';Image.new('RGB',(80,100),'blue').save(photo)
    c=Candidate(profile={'name':'测试资料（非真实候选人）','facts':['测试：英语笔译学习经历']},source_pdf='fixture',photo=str(photo),verified=True)
    j=Job(url='https://example.test/chinese',title='测试岗位',jd='英语')
    db.add_all([c,j]);db.commit()
    pdf,_=generate(db,c,j)
    page=PdfReader(pdf).pages[0]
    assert '英语笔译' in page.extract_text()
    assert page['/Resources']['/XObject']
