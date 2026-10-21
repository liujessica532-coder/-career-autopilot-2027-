import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import pytest
from app.db import Candidate, Job, Application, Artifact, HumanAction
from adapters.browser import BrowserExecutor, NeedsHuman

class Form(BaseHTTPRequestHandler):
    uploaded = False
    def log_message(self,*args): pass
    def do_GET(self):
        if self.path == '/challenge':
            self.send_response(200);self.end_headers();self.wfile.write(b'<p>CAPTCHA verification code</p>');return
        if self.path == '/multi':
            self.send_response(200);self.end_headers();self.wfile.write(b'<html><body><input id=first required><button id=next type=button onclick="location.href=String.fromCharCode(47)">Next</button></body></html>');return
        body=b'''<html><body><h1>SIMULATION ONLY</h1><form action="/apply" method="post" enctype="multipart/form-data"><label>Name<input id="name" name="name" required></label><select id="city" name="city" required><option value="Shanghai">Shanghai</option></select><input id="resume" name="resume" type="file" required><button id="submit">Apply</button></form></body></html>'''
        self.send_response(200);self.end_headers();self.wfile.write(body)
    def do_POST(self):
        data=self.rfile.read(int(self.headers['Content-Length']))
        Form.uploaded=b'%PDF' in data and b'TEST FIXTURE' in data
        self.send_response(200);self.end_headers()
        self.wfile.write(b'<div id="receipt">SIMULATED-RECEIPT-001</div>' if Form.uploaded else b'Upload failed')

def fixture_application(db,url):
    c=Candidate(profile={'name':'TEST FIXTURE','city':'Shanghai'},source_pdf='test',verified=True)
    j=Job(url=url,title='SIMULATION',jd='SIMULATION',assessment={})
    db.add_all([c,j]);db.commit()
    a=Application(candidate_id=c.id,job_id=j.id);db.add(a);db.commit()
    return a

def test_browser_upload_submit_receipt(db,tmp_path):
    server=ThreadingHTTPServer(('127.0.0.1',0),Form)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        a=fixture_application(db,f'http://127.0.0.1:{server.server_port}')
        file=tmp_path/'resume.pdf'
        from reportlab.pdfgen.canvas import Canvas
        canvas=Canvas(str(file),pageCompression=0);canvas.drawString(30,700,'TEST FIXTURE');canvas.save()
        artifact=Artifact(application_id=a.id,kind='test',path=str(file),sha256=hashlib.sha256(file.read_bytes()).hexdigest())
        db.add(artifact);db.commit()
        spec={'authorized':True,'steps':[{'fields':[{'selector':'#name','profile_key':'name'},{'selector':'#city','kind':'select','profile_key':'city'},{'selector':'#resume','kind':'file','artifact_id':artifact.id}]}],'submit_selector':'#submit','receipt_selector':'#receipt'}
        result=BrowserExecutor(db,str(tmp_path/'browser')).run(a.id,spec,submit=True,simulation=True)
        assert Form.uploaded and result['simulation'] and not result['submitted']
        assert a.status=='simulated' and a.receipt_number=='SIMULATED-RECEIPT-001'
        assert Path(f'artifacts/applications/{a.id}/receipt.png').exists()
    finally: server.shutdown()

def test_unverified_candidate_stops(db,tmp_path):
    a=fixture_application(db,'https://example.test/no-network')
    candidate=db.get(Candidate,a.candidate_id);candidate.verified=False;db.commit()
    with pytest.raises(NeedsHuman): BrowserExecutor(db,str(tmp_path/'state')).run(a.id,{},submit=True)
    assert a.status=='needs_human'

def test_ambiguous_submission_blocked(db,tmp_path):
    a=fixture_application(db,'https://example.test/no-network');a.status='submitting';db.commit()
    with pytest.raises(ValueError): BrowserExecutor(db,str(tmp_path/'state')).run(a.id,{})


def test_simulation_cannot_target_real_site(db,tmp_path):
    a=fixture_application(db,'https://real-employer.example/job')
    with pytest.raises(ValueError,match='loopback'):
        BrowserExecutor(db,str(tmp_path/'browser')).run(a.id,{},simulation=True)

def test_persistent_browser_session(tmp_path):
    from playwright.sync_api import sync_playwright
    import os
    directory=str(tmp_path/'persistent')
    with sync_playwright() as p:
        options=dict(headless=True,executable_path=os.getenv('CHROMIUM_EXECUTABLE') or None)
        first=p.chromium.launch_persistent_context(directory,**options)
        first.add_cookies([{'name':'test_session','value':'SYNTHETIC','domain':'example.test','path':'/','expires':2000000000}])
        first.close()
        second=p.chromium.launch_persistent_context(directory,**options)
        assert any(c['name']=='test_session' for c in second.cookies())
        second.close()


def test_security_challenge_creates_human_action(db,tmp_path):
    from sqlalchemy import select
    server=ThreadingHTTPServer(('127.0.0.1',0),Form)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        a=fixture_application(db,f'http://127.0.0.1:{server.server_port}/challenge')
        with pytest.raises(NeedsHuman):
            BrowserExecutor(db,str(tmp_path/'state')).run(a.id,{'steps':[{'fields':[]}]},simulation=True)
        assert db.scalar(select(HumanAction).where(HumanAction.application_id==a.id))
    finally: server.shutdown()

def test_multistep_upload_only(db,tmp_path):
    from reportlab.pdfgen.canvas import Canvas
    server=ThreadingHTTPServer(('127.0.0.1',0),Form)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        a=fixture_application(db,f'http://127.0.0.1:{server.server_port}/multi')
        path=tmp_path/'file.pdf';c=Canvas(str(path));c.drawString(30,700,'SYNTHETIC');c.save()
        artifact=Artifact(application_id=a.id,kind='test',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest());db.add(artifact);db.commit()
        spec={'steps':[{'fields':[{'selector':'#first','profile_key':'name'}],'next':'#next'}, {'fields':[{'selector':'#name','profile_key':'name'},{'selector':'#city','kind':'select','profile_key':'city'},{'selector':'#resume','kind':'file','artifact_id':artifact.id}]}]}
        result=BrowserExecutor(db,str(tmp_path/'state')).run(a.id,spec,simulation=True)
        assert not result['submitted'] and a.checkpoint['step']==1 and a.status=='prepared'
    finally: server.shutdown()
