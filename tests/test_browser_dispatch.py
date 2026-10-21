"""Broker retry acknowledges existing application state rather than reviving it."""
import pytest
from app.db import Candidate,Job,Application,Task
from workers.main import run_task

@pytest.mark.parametrize('application_state,expected',[('needs_human','human_required'),('submission_uncertain','human_required'),('submitted','done'),('draft','browser_running')])
def test_idempotent_dispatch_preserves_current_state(db,tmp_path,monkeypatch,application_state,expected):
    candidate=Candidate(profile={'name':'SYNTHETIC'},source_pdf='SYNTHETIC',verified=True)
    job=Job(url='https://example.test/SYNTHETIC',title='SYNTHETIC',jd='SYNTHETIC');db.add_all([candidate,job]);db.flush()
    app=Application(candidate_id=candidate.id,job_id=job.id,status=application_state);db.add(app);db.flush()
    task=Task(key='synthetic-task',kind='apply',status='running',payload={'application_id':app.id,'spec':{}});db.add(task);db.commit()
    token=tmp_path/'token';token.write_text('SYNTHETIC')
    monkeypatch.setenv('BROWSER_SERVICE_URL','http://browser:8765');monkeypatch.setenv('OPERATOR_TOKEN_FILE',str(token))
    class Client:
        def __init__(self,**kwargs):assert kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):return self
        def raise_for_status(self):pass
    monkeypatch.setattr('workers.main.httpx.Client',Client)
    run_task(db,task);db.refresh(task)
    assert task.status==expected and task.lease_until is None
    assert db.get(Application,app.id).status==application_state
