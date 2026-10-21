"""Real Chromium against a synthetic local site; NEVER evidence of real applications."""
import hashlib
import json
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.db import Candidate, Job, Application, BrowserSession, EventLog, Task, now
from app.browser_runtime import BrowserManager
from app.browser_service import create_app
from app.session_vault import SessionVault
from app.browser_failures import FailureKind, classify_exception, BrowserProblem

class Site(BaseHTTPRequestHandler):
    posts=0
    def log_message(self,*args):pass
    def do_GET(self):
        gate='' if 'operator=done' in self.headers.get('Cookie','') or self.path=='/plain' else '''<div id="challenge" style="position:absolute;left:20px;top:80px"><p>CAPTCHA: synthetic manual checkpoint</p><button id="operator" type="button" style="width:220px;height:50px" onclick="document.cookie='operator=done;path=/;max-age=3600';sessionStorage.setItem('operator-note','test-preserved');document.getElementById('challenge').remove()">Operator completed</button></div>'''
        if self.path=='/login':gate='<input id="password" type="password">'
        body=f'''<!doctype html><html><body>{gate}<form action="/apply" method="post"><input id="email" name="email" required><button id="submit">Apply</button></form></body></html>'''
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body.encode())
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length','0')));Site.posts+=1
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'<html><div id="receipt">SIMULATED application accepted <span id="number">SIMULATED-001</span></div></html>')

@pytest.fixture
def site():
    Site.posts=0
    server=ThreadingHTTPServer(('127.0.0.1',0),Site)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()

@pytest.fixture
def runtime(db,tmp_path,monkeypatch):
    monkeypatch.setenv('CHROMIUM_EXECUTABLE','/usr/bin/chromium')
    key=tmp_path/'key';key.write_bytes(Fernet.generate_key())
    sessions=sessionmaker(db.bind,expire_on_commit=False)
    manager=BrowserManager(sessions,tmp_path/'state',key,allow_simulation=True)
    yield manager,sessions,key
    manager.close()

def app_fixture(db,url,suffix=''):
    c=Candidate(profile={'name':'SYNTHETIC','email':'synthetic@example.test'},source_pdf='SYNTHETIC',verified=True)
    j=Job(url=url+suffix,title='SYNTHETIC TEST ONLY',jd='SYNTHETIC',assessment={})
    db.add_all([c,j]);db.commit();a=Application(candidate_id=c.id,job_id=j.id);db.add(a);db.commit();return a.id

def spec(url):
    return {'simulation':True,'reviewed':True,'authorized':True,'submit':True,'entry_url':url,'allowed_origins':[url.rsplit('/',1)[0] if url.count('/')>2 else url], 'steps':[{'fields':[{'selector':'#email','profile_key':'email'}]}],'submit_selector':'#submit','receipt_selector':'#receipt','receipt_text_pattern':'SIMULATED application accepted','receipt_number_selector':'#number','timeout_ms':1000}

def wait_status(sessions,sid,status,timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        with sessions() as db:
            row=db.get(BrowserSession,sid)
            if row and row.status==status:return row
        time.sleep(.1)
    with sessions() as db:row=db.get(BrowserSession,sid)
    raise AssertionError(f'Expected {status}, got {row.status if row else None}')

def test_paused_session_does_not_block_other_application(db,runtime,site):
    manager,sessions,_=runtime
    first=app_fixture(db,site,'/gate');second=app_fixture(db,site,'/plain')
    one=manager.start(first,spec(site+'/gate'));wait_status(sessions,one,'needs_human')
    two=manager.start(second,spec(site+'/plain'));wait_status(sessions,two,'simulated')
    with sessions() as check:
        assert check.get(Application,first).status=='needs_human'
        assert check.get(Application,second).submitted_at is None
    assert Site.posts==1

def test_authenticated_human_resume_after_restart(db,runtime,site,tmp_path):
    manager,sessions,key=runtime
    app_id=app_fixture(db,site,'/gate');sid=manager.start(app_id,spec(site+'/gate'));wait_status(sessions,sid,'needs_human')
    # Simulate a human normal mouse click on a synthetic control, not a real CAPTCHA.
    manager.command(sid,'control',action='click',x=70,y=145)
    manager.close()
    assert Site.posts==0
    ciphertext=manager.vault.path(sid).read_bytes()
    assert b'operator=done' not in ciphertext and b'synthetic@example.test' not in ciphertext
    saved=manager.vault.load(sid)
    assert any(c['name']=='operator' for c in saved['storage']['cookies'])
    assert saved['session_storage'][site]['operator-note']=='test-preserved'
    replacement=BrowserManager(sessions,tmp_path/'state',key,allow_simulation=True)
    # Fixture teardown closes the replacement too.
    runtime[0].close=lambda: replacement.close()
    replacement.restore();wait_status(sessions,sid,'needs_human')
    assert Site.posts==0
    token_file=tmp_path/'operator.token';token_file.write_text('synthetic-operator-token')
    service=create_app(replacement,sessions,token_file)
    with TestClient(service,base_url='http://127.0.0.1:8765') as client:
        assert client.get('/panel/'+sid+'/screen').status_code==403
        headers={'Authorization':'Bearer synthetic-operator-token'}
        assert client.post('/admin/sessions/'+sid+'/handover',headers=headers).status_code==200
        assert client.get('/panel/'+sid+'/screen').headers['content-type']=='image/png'
        assert client.post('/panel/'+sid+'/control',headers={'Origin':'https://evil.invalid'},json={'action':'click','x':1,'y':1}).status_code==403
        result=client.post('/panel/'+sid+'/resume',headers={'Origin':'http://127.0.0.1:8765'})
        assert result.status_code==200,result.text
        wait_status(sessions,sid,'simulated')
        assert client.get('/panel/'+sid+'/screen').status_code==403
    with sessions() as check:
        app=check.get(Application,app_id)
        assert app.status=='simulated' and app.receipt_number=='SIMULATED-001' and app.submitted_at is None
    assert Site.posts==1
    assert replacement.start(app_id,spec(site+'/gate'))==sid
    assert Site.posts==1

def test_resume_refuses_unsolved_challenge(db,runtime,site):
    manager,sessions,_=runtime
    sid=manager.start(app_fixture(db,site,'/gate'),spec(site+'/gate'));wait_status(sessions,sid,'needs_human')
    with pytest.raises(BrowserProblem):manager.command(sid,'resume')
    assert Site.posts==0

def test_handover_expiry_and_authentication(db,runtime,site,tmp_path):
    manager,sessions,_=runtime
    sid=manager.start(app_fixture(db,site,'/gate'),spec(site+'/gate'));wait_status(sessions,sid,'needs_human')
    token=tmp_path/'operator.token';token.write_text('synthetic-operator')
    with TestClient(create_app(manager,sessions,token),base_url='http://127.0.0.1:8765') as client:
        assert client.get('/admin/sessions').status_code==401
        assert client.post('/admin/sessions/'+sid+'/handover',headers={'Authorization':'Bearer wrong'}).status_code==401
        assert client.post('/admin/sessions/'+sid+'/handover',headers={'Authorization':'Bearer synthetic-operator'}).status_code==200
        with sessions() as session:
            session.get(BrowserSession,sid).capability_expires=now()-timedelta(seconds=1);session.commit()
        assert client.get('/panel/'+sid+'/screen').status_code==403

def test_vault_integrity_and_path_scope(tmp_path):
    key=tmp_path/'key';key.write_bytes(Fernet.generate_key())
    vault=SessionVault(tmp_path/'vault',key)
    import uuid
    sid=str(uuid.uuid4());vault.save(sid,{'cookie':'SYNTHETIC_PRIVATE'})
    assert b'SYNTHETIC_PRIVATE' not in vault.path(sid).read_bytes()
    data=bytearray(vault.path(sid).read_bytes());data[-3]^=1;vault.path(sid).write_bytes(data)
    with pytest.raises(InvalidToken):vault.load(sid)
    with pytest.raises(ValueError):vault.load('../../outside')

def test_failure_classification():
    assert classify_exception(RuntimeError('net::ERR_TUNNEL_CONNECTION_FAILED'))==FailureKind.NETWORK_BLOCKED
    assert classify_exception(TimeoutError())==FailureKind.PAGE_TIMEOUT
    assert classify_exception(BrowserProblem(FailureKind.LOGIN_EXPIRED,'expired'))==FailureKind.LOGIN_EXPIRED
    assert classify_exception(BrowserProblem(FailureKind.CAPTCHA,'challenge'))==FailureKind.CAPTCHA
    assert classify_exception(ValueError())==FailureKind.FORM_ERROR

def test_unknown_receipt_never_marks_success_or_retries(db,runtime,site):
    manager,sessions,_=runtime
    plan=spec(site+'/plain');plan['receipt_text_pattern']='THIS_RECEIPT_DOES_NOT_EXIST'
    app_id=app_fixture(db,site,'/plain')
    task=Task(key='SYNTHETIC-UNCERTAIN-TASK',kind='apply',status='running',payload={'application_id':app_id});db.add(task);db.commit()
    sid=manager.start(app_id,plan)
    wait_status(sessions,sid,'submission_uncertain')
    with sessions() as check:
        assert check.get(Application,app_id).submitted_at is None
        assert check.get(Application,app_id).receipt_number is None
        assert check.get(Task,task.id).status=='human_required'
    assert Site.posts==1
    with pytest.raises(ValueError):manager.command(sid,'resume')
    assert manager.start(app_id,plan)==sid and Site.posts==1
    token=manager.root/'test-token';token.write_text('SYNTHETIC-UNCERTAIN')
    with TestClient(create_app(manager,sessions,token),base_url='http://127.0.0.1:8765') as client:
        assert client.post('/admin/sessions/'+sid+'/handover',headers={'Authorization':'Bearer SYNTHETIC-UNCERTAIN'}).status_code==200
        assert client.get('/panel/'+sid+'/screen').status_code==200
    review={'confirmed_receipt_review':True,'receipt_selector':'#receipt','receipt_text_pattern':'SIMULATED application accepted','receipt_url_pattern':'/apply$'}
    assert manager.command(sid,'receipt_review',**review)['receipt_verified']
    assert Site.posts==1

def test_login_pause_and_operator_text_is_not_logged(db,runtime,site):
    manager,sessions,_=runtime
    sid=manager.start(app_fixture(db,site,'/login'),spec(site+'/login'))
    row=wait_status(sessions,sid,'needs_human')
    assert row.failure_kind=='login_expired'
    secret='SYNTHETIC-PRIVATE-OPERATOR-INPUT'
    manager.command(sid,'control',action='click',x=60,y=15)
    manager.command(sid,'control',action='text',text=secret)
    manager.close()
    saved=manager.vault.load(sid)
    assert secret not in json.dumps(saved['form_reference'])
    with sessions() as check:
        assert all(secret not in json.dumps(e.details) for e in check.scalars(select(EventLog)))

def test_operator_uses_real_handover_ui(db,runtime,site,tmp_path,monkeypatch):
    import socket
    import uvicorn
    from playwright.sync_api import sync_playwright
    manager,sessions,_=runtime
    sid=manager.start(app_fixture(db,site,'/gate'),spec(site+'/gate'));wait_status(sessions,sid,'needs_human')
    token=tmp_path/'token';token.write_text('SYNTHETIC-UI-OPERATOR')
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
    base=f'http://127.0.0.1:{sock.getsockname()[1]}'
    monkeypatch.setenv('HANDOVER_ORIGINS',base)
    server=uvicorn.Server(uvicorn.Config(create_app(manager,sessions,token),log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        end=time.monotonic()+5
        while not server.started and time.monotonic()<end:time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium')
            page=browser.new_page(viewport={'width':1440,'height':1100})
            page.goto(base)
            page.locator('#token').fill('SYNTHETIC-UI-OPERATOR');page.locator('#login').click()
            page.get_by_role('button',name='接管此申请（10分钟）').click()
            page.wait_for_url('**/panel/**')
            end=time.monotonic()+10
            while not page.locator("#screen").evaluate("e => e.complete && e.naturalWidth > 0"):
                assert time.monotonic()<end
                page.wait_for_timeout(100)
            Path('artifacts/evidence').mkdir(parents=True,exist_ok=True)
            page.screenshot(path='artifacts/evidence/operator-ui-test.png',full_page=True)
            box=page.locator('#screen').bounding_box()
            page.mouse.click(box['x']+70*box['width']/1280,box['y']+145*box['height']/900)
            page.wait_for_timeout(1500)
            page.locator('#resume').click()
            page.wait_for_timeout(2000)
            assert page.evaluate('Object.keys(localStorage).length')==0
            browser.close()
        wait_status(sessions,sid,'simulated')
        assert Site.posts==1
    finally:
        server.should_exit=True;thread.join(timeout=10);sock.close()

def test_manual_receipt_review_checks_actual_page_without_clicking_again(db,runtime,site):
    manager,sessions,_=runtime
    plan=spec(site+'/plain');plan['submit']=False;plan['start_paused']=True
    app_id=app_fixture(db,site,'/plain');sid=manager.start(app_id,plan);wait_status(sessions,sid,'needs_human')
    # Human completes ordinary form via keyboard. This is a synthetic fixture, not an employer.
    manager.command(sid,'control',action='click',x=70,y=15)
    manager.command(sid,'control',action='text',text='synthetic@example.test')
    manager.command(sid,'control',action='key',key='Tab')
    manager.command(sid,'control',action='key',key='Enter')
    end=time.monotonic()+5
    while Site.posts!=1 and time.monotonic()<end:time.sleep(.1)
    assert Site.posts==1
    end=time.monotonic()+5
    from playwright.sync_api import Error as BrowserError
    while True:
        try:
            inspected=manager.command(sid,'inspect')
            if inspected['url'].endswith('/apply') and any(e['id']=='receipt' for e in inspected['elements']):break
        except BrowserError:pass  # Navigation may still be committing after the POST.
        assert time.monotonic()<end
        time.sleep(.1)
    review={'confirmed_receipt_review':True,'receipt_selector':'#receipt','receipt_text_pattern':'NO SUCH RECEIPT','receipt_url_pattern':'/apply$'}
    assert manager.command(sid,'receipt_review',**review)=={'receipt_verified':False}
    with sessions() as check:assert check.get(Application,app_id).status=='needs_human'
    review['receipt_text_pattern']='SIMULATED application accepted'
    assert manager.command(sid,'receipt_review',**review)=={'receipt_verified':True}
    with sessions() as check:
        assert check.get(Application,app_id).status=='simulated'
        assert check.get(Application,app_id).submitted_at is None
        assert check.get(BrowserSession,sid).spec['submit'] is False
    assert Site.posts==1
