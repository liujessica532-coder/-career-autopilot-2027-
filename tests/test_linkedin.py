"""Synthetic fixtures only. No real LinkedIn request or application is made."""
import json
from pathlib import Path
from datetime import timedelta
import pytest
from playwright.sync_api import sync_playwright
from sqlalchemy import select
from adapters import linkedin as li
from adapters.browser import NeedsHuman
from app.db import Application, Artifact, Candidate, EventLog, HumanAction, Job, Task, now
from scripts.linkedin import queue_application
from workers.main import run_task
from workers.queue import enqueue, claim, fail


@pytest.fixture
def records(db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path/'source.pdf'
    source.write_bytes(b'%PDF-1.4 SYNTHETIC ORIGINAL')
    root = tmp_path/'artifacts/real-validation'
    root.mkdir(parents=True)
    resume = root/'resume.pdf'
    resume.write_bytes(source.read_bytes())
    profile = {'name': 'SYNTHETIC', 'facts': ['Verified source fact.'], 'source_text': 'Verified source fact.', 'linkedin_answers': {'Email': 'synthetic@example.test'}}
    private = root/'profile.json'
    private.write_text(json.dumps(profile))
    monkeypatch.setattr(li, 'RESUME', resume)
    monkeypatch.setattr(li, 'PROFILE', private)
    c = Candidate(profile=profile, source_pdf=str(source), verified=True)
    j = Job(url='https://www.linkedin.com/jobs/view/123/', title='SYNTHETIC 2027 graduate', jd='Shanghai 2027 graduates', assessment={'eligible': True, 'checked_at': now().isoformat(), 'linkedin_resume_confirmation_selector': '#uploaded'})
    db.add_all([c,j]); db.commit()
    return c,j,profile,resume


def application(db, records):
    c,j,_,_=records
    app=Application(candidate_id=c.id,job_id=j.id)
    db.add(app);db.commit()
    return app


@pytest.mark.parametrize('value', ['http://www.linkedin.com/jobs/view/123/', 'https://www.linkedin.com.evil.test/jobs/view/123/', 'https://example.test/apply', 'https://user@www.linkedin.com/jobs/view/123/', 'https://www.linkedin.com:444/jobs/view/123/'])
def test_external_routes_rejected(value):
    with pytest.raises(ValueError):li.job_url(value)


def test_canonical_and_ranking():
    assert li.job_url('https://linkedin.com/jobs/view/123/?trackingId=x')=='https://www.linkedin.com/jobs/view/123/'
    assert li.job_url('https://www.linkedin.com/jobs/view/graduate-role-at-company-123/')=='https://www.linkedin.com/jobs/view/123/'
    explicit=li.rank_job('Shanghai class of 2027 graduates')
    other=li.rank_job('Beijing graduating between 2026 and 2028')
    assert explicit['priority']<other['priority']
    assert li.rank_job('澳门 2027届')['priority']<100
    assert not explicit['eligibility_verified']


def test_materials_exact_and_unchanged(db, records):
    c,_,_,resume=records
    original=resume.read_bytes()
    li.load_materials(c)
    assert resume.read_bytes()==original
    li.PROFILE.write_text('{}')
    with pytest.raises(NeedsHuman,match='does not match'):li.load_materials(c)


def test_profile_source_text_can_be_retained_only_in_verified_database(db, records):
    c,_,profile,_=records
    li.PROFILE.write_text(json.dumps({k:v for k,v in profile.items() if k!='source_text'}))
    loaded,_=li.load_materials(c)
    assert loaded['source_text']==c.profile['source_text']


def test_resume_tampering_pauses(db, records):
    c,_,_,resume=records
    resume.write_bytes(b'changed')
    with pytest.raises(NeedsHuman,match='differs'):li.load_materials(c)


def test_batch_gate_and_duplicate(db, records):
    c,j,_,_=records
    with pytest.raises(ValueError,match='First complete'):queue_application(db,c.id,j.id)
    appid=queue_application(db,c.id,j.id,validation=True)
    assert db.get(Application,appid).status=='draft'
    with pytest.raises(ValueError):queue_application(db,c.id,j.id,validation=True)
    assert not li.batch_unlocked(db,c.id)


def test_stale_job_and_ambiguous_cannot_replay(db, records):
    _,j,_,_=records
    app=application(db,records)
    j.assessment={**j.assessment,'checked_at':(now()-timedelta(days=2)).isoformat()};db.commit()
    with pytest.raises(NeedsHuman,match='current verification'):
        li.LinkedInExecutor(db).run(app.id,authorized=True,validation=True)
    assert app.status=='needs_human'
    app.status='submitting';db.commit()
    with pytest.raises(NeedsHuman):li.LinkedInExecutor(db).run(app.id,authorized=True,validation=True)
    assert app.status=='submitting'


@pytest.fixture
def page(monkeypatch):
    # Only tests relax host validation, for a synthetic inline DOM, never production.
    monkeypatch.setattr(li,'check_page',lambda page: None)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium')
        page=browser.new_page()
        page.set_default_timeout(1000)
        yield page
        browser.close()


def form_html(extra='', receipt=True):
    message='Application submitted' if receipt else 'Unknown result'
    return '''<div role="dialog"><h2>Contact</h2>
    <label>Email<input id="email" required></label>
    <label>Resume<input type="file" id="resume" onchange="document.querySelector('#uploaded').hidden=false"></label>
    <div id="uploaded" hidden>resume.pdf</div>'''+extra+'''
    <button onclick="document.querySelector('#review').hidden=false;this.remove()">Next</button>
    <div id="review" hidden><h3>Review</h3><button onclick="window.submissions=(window.submissions||0)+1;document.querySelector('[role=dialog]').innerHTML='<p>'''+message+'''</p>'">Submit application</button></div></div>'''


def test_multistep_original_upload_and_receipt_synthetic(db, records, page):
    _,job,profile,resume=records
    app=application(db,records)
    before=resume.read_bytes()
    page.set_content(form_html())
    li.LinkedInExecutor(db).form(page,app,job,profile,li.digest(resume),True)
    # This is a local synthetic DOM receipt, NOT real end-to-end evidence.
    assert page.evaluate('window.submissions')==1
    assert app.status=='submitted'
    record=db.scalar(select(Artifact).where(Artifact.kind=='linkedin_receipt'))
    payload=json.loads(Path(record.path).read_text())
    assert payload['url']==job.url and payload['resume_sha256']==li.digest(resume)
    assert resume.read_bytes()==before
    assert db.scalar(select(Artifact).where(Artifact.kind=='linkedin_receipt_screenshot'))
    # This unlocks only this isolated synthetic test DB, never the production DB.
    assert li.batch_unlocked(db,app.candidate_id)
    Path(record.path).write_text('tampered synthetic receipt')
    assert not li.batch_unlocked(db,app.candidate_id)


@pytest.mark.parametrize('extra', [
    '<label>Unknown qualification<input required></label>',
    '<label>I agree to terms<input type="checkbox"></label>',
    '<label>Visa sponsorship<input type="radio"></label>',
])
def test_unknown_declarations_pause_before_submit(db, records, page, extra):
    _,job,profile,resume=records
    app=application(db,records)
    page.set_content(form_html(extra))
    with pytest.raises(NeedsHuman):li.LinkedInExecutor(db).form(page,app,job,profile,li.digest(resume),True)
    assert page.evaluate('window.submissions||0')==0


def test_ambiguous_click_is_never_retried(db, records, page):
    _,job,profile,resume=records
    app=application(db,records)
    page.set_content(form_html(receipt=False))
    with pytest.raises(NeedsHuman):li.LinkedInExecutor(db).form(page,app,job,profile,li.digest(resume),True)
    assert app.status=='submission_uncertain'
    assert page.evaluate('window.submissions')==1
    with pytest.raises(NeedsHuman):li.LinkedInExecutor(db).run(app.id,authorized=True,validation=True)
    assert page.evaluate('window.submissions')==1


def test_worker_pause_isolated_and_retry_backoff(db,records,monkeypatch):
    app=application(db,records)
    enqueue(db,'linkedin-first','linkedin_apply',{'application_id':app.id,'authorized':True,'validation':True})
    enqueue(db,'report-test','report')
    def paused(self,*args,**kwargs):self.pause(app,'Unknown question')
    monkeypatch.setattr(li.LinkedInExecutor,'run',paused)
    first=claim(db);run_task(db,first)
    assert first.status=='human_required' and app.status=='needs_human'
    second=claim(db);run_task(db,second)
    assert second.status=='done'
    assert list(Path('artifacts/reports').glob('*.json'))
    enqueue(db,'retry-discover','linkedin_discover',{'search_urls':[]})
    task=claim(db);fail(db,task,ConnectionError('SENSITIVE'))
    assert task.status=='pending' and task.error=='ConnectionError'
    assert task.available_at.replace(tzinfo=now().tzinfo)>now()


def test_cover_letter_uses_only_source_facts():
    assert li.verified_facts({'facts':['Fact'], 'source_text':'Fact'})==['Fact']
    with pytest.raises(NeedsHuman):li.verified_facts({'facts':['Invented'], 'source_text':'Fact'})


@pytest.mark.parametrize('required', [False, True])
def test_cover_letter_generated_only_when_required(db, records, page, required):
    _,job,profile,resume=records
    app=application(db,records)
    page.set_content(form_html('<label>Cover letter<textarea '+('required' if required else '')+'></textarea></label>'))
    # Capture DOM before the synthetic final click to verify exact source facts.
    captured=[]
    executor=li.LinkedInExecutor(db)
    executor.submit=lambda *args: captured.append(page.locator('textarea').input_value())
    executor.form(page,app,job,profile,li.digest(resume),True)
    assert captured==['Verified source fact.' if required else '']
    assert not list(Path('artifacts/linkedin').rglob('*cover-letter.pdf'))


def test_duplicate_success_across_tracking_url_records(db, records):
    c,j,_,resume=records
    app=application(db,records)
    app.status='submitted';app.submitted_at=now();db.commit()
    other=Job(url=j.url+'?trackingId=other',title='same job',jd=j.jd,assessment=j.assessment)
    db.add(other);db.commit()
    assert li.already_submitted(db,other.url,li.digest(resume))
    with pytest.raises(ValueError,match='already submitted'):
        queue_application(db,c.id,other.id,validation=True)


def test_real_browser_multipart_upload_to_synthetic_server(db, records, page):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    uploads=[]
    submissions=[]
    class Site(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            body=form_html().replace("document.querySelector('#uploaded').hidden=false", "fetch('/upload',{method:'POST',body:(()=>{const f=new FormData();f.append('resume',this.files[0]);return f;})()}).then(r=>{if(r.ok)document.querySelector('#uploaded').hidden=false})")
            body=body.replace("window.submissions=(window.submissions||0)+1;document.querySelector('[role=dialog]').innerHTML='<p>Application submitted</p>'", "fetch('/submit',{method:'POST'}).then(r=>r.json()).then(r=>document.querySelector('[role=dialog]').innerHTML='<p>'+r.receipt+'</p>')")
            self.send_response(200);self.end_headers();self.wfile.write(body.encode())
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            if self.path=='/upload':uploads.append(body)
            elif self.path=='/submit':submissions.append(True)
            self.send_response(200);self.end_headers();self.wfile.write(b'{"receipt":"Application submitted: SYNTHETIC ONLY"}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Site)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        _,job,profile,resume=records
        app=application(db,records)
        page.goto(f'http://127.0.0.1:{server.server_port}')
        li.LinkedInExecutor(db).form(page,app,job,profile,li.digest(resume),False)
        assert len(uploads)==1 and resume.read_bytes() in uploads[0]
        assert len(submissions)==1
        assert not db.scalar(select(EventLog).where(EventLog.event=='linkedin_real_e2e_verified'))
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_login_and_external_checkpoint():
    class Page:
        url='https://www.linkedin.com/checkpoint/challenge'
    with pytest.raises(NeedsHuman):li.check_page(Page())
    Page.url='https://third-party.test/apply'
    with pytest.raises(NeedsHuman):li.check_page(Page())


def test_crashed_pre_submit_draft_is_not_replayed(db,records):
    app=application(db,records)
    app.checkpoint={'adapter':'linkedin','phase':'opening'};db.commit()
    with pytest.raises(NeedsHuman,match='Interrupted draft'):
        li.LinkedInExecutor(db).run(app.id,authorized=True,validation=True)
    assert app.status=='needs_human'


def test_discovery_filters_external_and_non_easy_apply(db,monkeypatch):
    from contextlib import contextmanager
    class Locator:
        def __init__(self,value):self.value=value
        def count(self):return self.value
        def inner_text(self):return self.value
        def evaluate_all(self,*args):return self.value
    class Page:
        url=''
        def goto(self,url,**kwargs):self.url=url
        def locator(self,selector):
            if selector.startswith('a['):return Locator(['https://www.linkedin.com/jobs/view/1/?trackingId=x','https://www.linkedin.com/jobs/view/2/','https://external.test/apply'])
            if selector=='h1':return Locator('SYNTHETIC vacancy')
            return Locator('Shanghai 2027 graduates')
        def get_by_role(self,*args,**kwargs):return Locator(0 if '/2/' in self.url else 1)
    @contextmanager
    def fake_browser():yield Page()
    monkeypatch.setattr(li,'browser',fake_browser)
    monkeypatch.setattr(li,'check_page',lambda page:None)
    li.discover(db,['https://www.linkedin.com/jobs/search/?f_AL=true'])
    jobs=list(db.scalars(select(Job)))
    assert len(jobs)==1 and jobs[0].url=='https://www.linkedin.com/jobs/view/1/'
    assert jobs[0].assessment['eligible'] is False
    assert not list(db.scalars(select(Task)))
