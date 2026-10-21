"""One live browser thread per application; no CAPTCHA solver or verification bypass."""
import fcntl
import hashlib
import json
import os
import queue
import re
import threading
import uuid
from concurrent.futures import Future
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from sqlalchemy import select, update
from playwright.sync_api import sync_playwright
from app.db import Application, Candidate, Job, Artifact, EventLog, HumanAction, BrowserSession, Task, now
from app.browser_failures import BrowserProblem, FailureKind, challenge_kind, classify_exception
from app.session_vault import SessionVault

TERMINAL = {'submitted', 'simulated', 'submission_uncertain'}

def safe_url(url):
    u = urlsplit(url)
    return urlunsplit((u.scheme, u.netloc, u.path, '', ''))

def origin(url):
    u = urlsplit(url)
    return f'{u.scheme}://{u.netloc}'

class BrowserManager:
    def __init__(self, sessions, state_root, key_file, *, allow_simulation=False):
        self.sessions = sessions
        self.root = Path(state_root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_file = (self.root / 'broker.lock').open('a')
        try: fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise RuntimeError('Only one browser broker may own this state volume')
        self.vault = SessionVault(self.root / 'encrypted', key_file)
        self.allow_simulation = allow_simulation
        self.live = {}
        self.lock = threading.RLock()

    def validate(self, db, application, spec):
        if application is None: raise ValueError('Unknown application')
        if application.status in TERMINAL | {'submitting'}: raise ValueError('Completed or uncertain submission cannot be replayed')
        candidate = db.get(Candidate, application.candidate_id)
        job = db.get(Job, application.job_id)
        if not candidate.verified: raise ValueError('Unverified candidate')
        simulation = spec.get('simulation', False)
        entry = spec.get('entry_url', job.url)
        if simulation:
            if not self.allow_simulation or urlsplit(entry).hostname not in ('127.0.0.1', 'localhost', '::1'):
                raise ValueError('Simulation is disabled or not loopback')
        else:
            if not spec.get('reviewed') or not job.assessment.get('eligible'): raise ValueError('Reviewed adapter and eligibility required')
            checked = datetime.fromisoformat(job.assessment.get('checked_at', '1970-01-01T00:00:00+00:00'))
            if checked.tzinfo is None or now() - checked > timedelta(hours=24): raise ValueError('Eligibility must be rechecked within 24 hours')
            if urlsplit(entry).scheme != 'https': raise ValueError('Real application requires HTTPS')
        allowed = spec.get('allowed_origins', [origin(entry)])
        if origin(entry) not in allowed: raise ValueError('Entry origin not reviewed')
        for item in allowed:
            if origin(item) != item or (not simulation and urlsplit(item).scheme != 'https'):
                raise ValueError('Only exact reviewed HTTPS origins allowed')
        if spec.get('submit'):
            if not spec.get('authorized'): raise ValueError('Explicit submission authorization required')
            for field in ('submit_selector', 'receipt_selector', 'receipt_text_pattern'):
                if not spec.get(field): raise ValueError('Exact receipt contract and submit selector required')
            re.compile(spec['receipt_text_pattern'])
        return candidate, job, entry

    def start(self, application_id, spec):
        with self.lock, self.sessions() as db:
            application = db.get(Application, application_id)
            record = db.scalar(select(BrowserSession).where(BrowserSession.application_id == application_id))
            if record:
                # Idempotency: never replace a running plan or replay a completed plan.
                if record.spec != spec: raise ValueError('Existing session has a different reviewed plan')
                return record.id
            self.validate(db, application, spec)
            record = BrowserSession(id=str(uuid.uuid4()), application_id=application_id, spec=spec, status='active')
            db.add(record); db.commit()
            self._spawn(record.id, False)
            return record.id

    def restore(self):
        with self.sessions() as db:
            ids = list(db.scalars(select(BrowserSession.id).where(BrowserSession.status.not_in({'submitted','simulated'}))))
        for sid in ids: self._spawn(sid, True)

    def _spawn(self, sid, restored):
        if sid in self.live: return
        if restored:
            with self.sessions() as db:
                record=db.get(BrowserSession,sid);record.status='restoring';record.capability_hash=None;record.capability_expires=None;db.commit()
        runtime = LiveBrowser(self, sid, restored)
        self.live[sid] = runtime
        runtime.start()

    def command(self, sid, command_name, **payload):
        with self.lock:
            runtime = self.live.get(sid)
        if runtime is None or not runtime.is_alive(): raise ValueError('Browser is unavailable; inspect persisted checkpoint')
        future = Future()
        runtime.commands.put((command_name, payload, future))
        return future.result(timeout=45)

    def close(self):
        for runtime in list(self.live.values()): runtime.stopping.set()
        for runtime in list(self.live.values()): runtime.join(timeout=45)
        if any(t.is_alive() for t in self.live.values()):
            raise RuntimeError('Browser did not stop; keep the broker lock until process exit')
        self.lock_file.close()

class LiveBrowser(threading.Thread):
    def __init__(self, manager, sid, restored):
        super().__init__(daemon=True)
        self.manager, self.sid, self.restored = manager, sid, restored
        self.commands = queue.Queue()
        self.stopping = threading.Event()
        self.page = self.context = None
        self.ready_to_advance = not restored
        self.dirty = False

    def record(self, db): return db.get(BrowserSession, self.sid)

    def emit(self, event, details):
        with self.manager.sessions() as db:
            record = self.record(db)
            db.add(EventLog(application_id=record.application_id, event=event, details=details)); db.commit()

    def pause(self, kind, reason):
        with self.manager.sessions() as db:
            record = self.record(db)
            application = db.get(Application, record.application_id)
            if application.status in TERMINAL: return
            record.status = 'needs_human'; record.failure_kind = str(kind); record.updated_at = now()
            record.capability_hash = None; record.capability_expires = None
            application.status = 'needs_human'
            application.checkpoint = {**application.checkpoint, 'browser_session':self.sid, 'cursor':record.cursor, 'failure_kind':str(kind)}
            db.add(HumanAction(application_id=application.id, reason=f'{kind}: {reason}'))
            db.add(EventLog(application_id=application.id, event='browser_needs_human', details={'kind':str(kind),'reason':reason,'session_id':self.sid}))
            for task in db.scalars(select(Task).where(Task.kind == 'apply', Task.status.in_(['running','browser_running']))):
                if task.payload.get('application_id') == application.id: task.status='human_required'; task.lease_until=None
            db.commit()
        self.ready_to_advance = False
        if self.page: self.snapshot()

    def snapshot(self):
        if not self.page or self.page.is_closed(): return
        try:
            storage = self.context.storage_state()
            session_storage = self.page.evaluate('Object.fromEntries(Object.entries(sessionStorage))')
            # Never serialize password fields, file inputs, or hidden challenge/CSRF values.
            fields = self.page.locator('input:not([type=password]):not([type=file]):not([type=hidden]),textarea:not([name*=captcha]),select').evaluate_all("els => els.filter(e=>e.id).map(e=>({id:e.id,value:e.value,type:e.type}))")
            value = {'storage':storage,'session_storage':{origin(self.page.url):session_storage},'last_url':self.page.url,'form_reference':fields}
            self.manager.vault.save(self.sid, value)
            with self.manager.sessions() as db:
                record = self.record(db)
                record.updated_at=now()
                record.checkpoint={**record.checkpoint,'last_url':safe_url(self.page.url),'state_saved':True}
                db.commit()
        except Exception:
            # A failed durable checkpoint must stop advancement, not silently lose state.
            raise BrowserProblem(FailureKind.FORM_ERROR, 'Encrypted browser checkpoint failed')

    def guard(self):
        with self.manager.sessions() as db:
            record=self.record(db); allowed=record.spec.get('allowed_origins')
            if allowed is None:
                job=db.get(Job,db.get(Application,record.application_id).job_id)
                allowed=[origin(record.spec.get('entry_url',job.url))]
        if origin(self.page.url) not in allowed:
            raise BrowserProblem(FailureKind.LOGIN_EXPIRED, 'Navigation reached an unreviewed origin; operator must review authentication route')
        failure=challenge_kind(self.page)
        if failure: raise BrowserProblem(failure, 'Complete website verification/login manually in this session')

    def actions(self, spec):
        result=[]
        for step in spec.get('steps',[]):
            result.extend({'op':'field',**field} for field in step.get('fields',[]))
            if step.get('next'): result.append({'op':'next','selector':step['next']})
        return result

    def attachment(self, db, record, artifact_id, selector):
        artifact=db.get(Artifact,artifact_id)
        if not artifact or artifact.application_id!=record.application_id:
            raise BrowserProblem(FailureKind.FORM_ERROR,'Attachment does not belong to this application')
        path=Path(artifact.path)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=artifact.sha256:
            raise BrowserProblem(FailureKind.FORM_ERROR,'Attachment integrity mismatch')
        self.page.locator(selector).set_input_files(str(path.resolve()))

    def advance(self):
        with self.manager.sessions() as db:
            record=self.record(db); app=db.get(Application,record.application_id); spec=record.spec
            self.manager.validate(db,app,spec)
            actions=self.actions(spec)
        while not self.stopping.is_set():
            self.guard()
            with self.manager.sessions() as db:
                record=self.record(db); app=db.get(Application,record.application_id)
                if record.cursor>=len(actions): break
                action=actions[record.cursor]
                if action['op']=='field':
                    if action.get('kind')=='file':
                        self.attachment(db,record,action['artifact_id'],action['selector'])
                        if not spec.get('simulation'):
                            if not action.get('upload_confirm_selector'): raise BrowserProblem(FailureKind.FORM_ERROR,'Reviewed upload acceptance indicator required')
                            self.page.locator(action['upload_confirm_selector']).wait_for(state='visible')
                    else:
                        candidate=db.get(Candidate,app.candidate_id); key=action['profile_key']
                        if key not in candidate.profile: raise BrowserProblem(FailureKind.FORM_ERROR,'Unknown required candidate field')
                        target=self.page.locator(action['selector'])
                        if action.get('kind')=='select': target.select_option(str(candidate.profile[key]))
                        else: target.fill(str(candidate.profile[key]))
                else:
                    self.validate_form(record)
                    record.checkpoint={**record.checkpoint,'action_in_progress':record.cursor}; db.commit()
                    self.page.locator(action['selector']).click()
                    self.page.wait_for_load_state('domcontentloaded')
                record.cursor+=1
                record.checkpoint={**record.checkpoint,'action_in_progress':None,'human_review_cursor':None}
                app.checkpoint={**app.checkpoint,'browser_session':self.sid,'cursor':record.cursor}
                db.commit()
            self.snapshot()
        if self.stopping.is_set(): return
        self.guard()
        with self.manager.sessions() as db:
            record=self.record(db); self.validate_form(record)
            if not spec.get('submit'):
                record.status='prepared'; db.get(Application,record.application_id).status='prepared'
                for task in db.scalars(select(Task).where(Task.kind=='apply',Task.status.in_(['running','browser_running']))):
                    if task.payload.get('application_id')==record.application_id:task.status='done';task.lease_until=None
                db.commit();self.ready_to_advance=False;return
            app=db.get(Application,record.application_id)
            claimed=db.execute(update(Application).where(Application.id==app.id,Application.status.in_(['draft','not_started','prepared','needs_human','in_progress'])).values(status='submitting'))
            if claimed.rowcount!=1: raise BrowserProblem(FailureKind.FORM_ERROR,'Submission is already owned or ambiguous')
            record.checkpoint={**record.checkpoint,'final_intent':True}
            db.add(EventLog(application_id=app.id,event='browser_submission_intent',details={'session_id':self.sid,'simulation':spec.get('simulation',False)}));db.commit()
        try:
            self.page.locator(spec['submit_selector']).click()
            self.page.locator(spec['receipt_selector']).wait_for(state='visible',timeout=spec.get('timeout_ms',15000))
            if not self.verify_receipt(): raise ValueError('Receipt contract not satisfied')
        except Exception:
            with self.manager.sessions() as db:
                record=self.record(db);app=db.get(Application,record.application_id)
                app.status='submission_uncertain';record.status='submission_uncertain'
                db.add(HumanAction(application_id=app.id,reason='Final click result uncertain. Reconcile employer portal; no automated retry.'))
                for task in db.scalars(select(Task).where(Task.kind=='apply',Task.status.in_(['running','browser_running']))):
                    if task.payload.get('application_id')==app.id:task.status='human_required';task.lease_until=None
                db.commit()
            self.ready_to_advance=False
            self.snapshot()

    def validate_form(self, record):
        reviewed=record.checkpoint.get('human_review_cursor')==record.cursor
        mapped=[a['selector'] for a in self.actions(record.spec) if a['op']=='field']
        for control in self.page.locator('input[required],select[required],textarea[required],input[type=checkbox],input[type=radio]').all():
            if not control.is_visible(): continue
            kind=control.get_attribute('type')
            if kind in ('checkbox','radio'):
                if not reviewed: raise BrowserProblem(FailureKind.FORM_ERROR,'Human review of declarations required')
            elif not control.input_value(): raise BrowserProblem(FailureKind.FORM_ERROR,'Required field is empty')
            elif not reviewed and not any(control.evaluate('(e,s)=>e.matches(s)',s) for s in mapped):
                raise BrowserProblem(FailureKind.FORM_ERROR,'Unmapped required field needs human review')

    def verify_receipt(self):
        with self.manager.sessions() as db:
            record=self.record(db); spec=record.spec
            if not (spec.get('submit') or spec.get('verify_manual_receipt')) or not spec.get('authorized'): return False
            if origin(self.page.url) not in spec.get('allowed_origins',[origin(spec.get('entry_url',db.get(Job,db.get(Application,record.application_id).job_id).url))]): return False
            if spec.get('receipt_url_pattern') and not re.search(spec['receipt_url_pattern'],self.page.url): return False
            target=self.page.locator(spec['receipt_selector'])
            if not target.count() or not target.is_visible(): return False
            text=target.inner_text().strip()
            if not re.search(spec['receipt_text_pattern'],text): return False
            app=db.get(Application,record.application_id)
            if app.status in ('submitted','simulated'): return True
            folder=Path('artifacts/browser-receipts')/self.sid;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            shot=folder/'receipt.png';html=folder/'receipt.html'
            self.page.screenshot(path=str(shot),full_page=True);html.write_text(self.page.content())
            for path,kind in [(shot,'receipt_screenshot'),(html,'receipt_html')]:
                db.add(Artifact(application_id=app.id,kind=kind,path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            number=None
            if spec.get('receipt_number_selector'):
                number=self.page.locator(spec['receipt_number_selector']).inner_text().strip() or None
            app.status='simulated' if spec.get('simulation') else 'submitted'
            # Synthetic confirmations never acquire a real submission timestamp.
            app.submitted_at=None if spec.get('simulation') else now()
            app.receipt_number=number;record.status=app.status;record.capability_hash=None;record.capability_expires=None
            db.add(EventLog(application_id=app.id,event='browser_receipt_verified',details={'simulation':spec.get('simulation',False),'url':safe_url(self.page.url),'receipt_number':number,'session_id':self.sid}))
            for action in db.scalars(select(HumanAction).where(HumanAction.application_id==app.id,HumanAction.status=='pending')): action.status='resolved_after_verified_receipt'
            for task in db.scalars(select(Task).where(Task.kind=='apply',Task.status.in_(['browser_running','human_required','running']))):
                if task.payload.get('application_id')==app.id:task.status='done';task.lease_until=None
            db.commit();self.ready_to_advance=False
            return True

    def run(self):
        try:
            with self.manager.sessions() as db:
                record=self.record(db);spec=record.spec;app=db.get(Application,record.application_id);job=db.get(Job,app.job_id)
                entry=spec.get('entry_url',job.url)
                ambiguous=app.status in ('submitting','submission_uncertain') or record.checkpoint.get('final_intent') and app.status!='needs_human'
            saved=self.manager.vault.load(self.sid) if self.restored else None
            with sync_playwright() as p:
                options={'headless':os.getenv('BROWSER_HEADLESS','true').lower()=='true','executable_path':os.getenv('CHROMIUM_EXECUTABLE') or None}
                if os.getenv('HTTPS_PROXY') and not spec.get('simulation'): options['proxy']={'server':os.environ['HTTPS_PROXY'],'bypass':'127.0.0.1,localhost,[::1]'}
                browser=p.chromium.launch(**options)
                self.context=browser.new_context(storage_state=saved['storage'] if saved else None,viewport={'width':1280,'height':900},accept_downloads=False)
                if saved:
                    self.context.add_init_script('const states='+json.dumps(saved.get('session_storage',{}))+'; const s=states[location.origin]; if(s) for(const [k,v] of Object.entries(s)) sessionStorage.setItem(k,v);')
                self.page=self.context.new_page();self.page.set_default_timeout(15000)
                # Only safe GET navigation is restored. Never replay a POST or final click.
                target=saved['last_url'] if saved else entry
                with self.manager.sessions() as db:
                    record=self.record(db)
                    if origin(target) not in spec.get('allowed_origins',[origin(entry)]):target=entry
                    if ambiguous:
                        record.status='submission_uncertain';db.get(Application,record.application_id).status='submission_uncertain';db.commit()
                try:
                    response=self.page.goto(target,wait_until='domcontentloaded',timeout=30000)
                    if response and response.status==403:raise BrowserProblem(FailureKind.NETWORK_BLOCKED,'HTTP 403 from browser destination')
                    if response and response.status in (401,):raise BrowserProblem(FailureKind.LOGIN_EXPIRED,'Login expired')
                    self.snapshot()
                    if ambiguous:
                        self.ready_to_advance=False
                        self.emit('browser_restored_ambiguous_submission',{'session_id':self.sid})
                    elif self.restored:
                        self.pause(FailureKind.FORM_ERROR,'Browser restarted: cookies/storage restored; operator must review cursor and page before continuing. No POST replayed.')
                    elif spec.get('start_paused'):
                        self.pause(FailureKind.FORM_ERROR,'Operator requested a manual checkpoint before automation')
                    else:self.advance()
                except Exception as exc:self.pause(classify_exception(exc),'Browser operation failed; inspect authenticated session')
                while not self.stopping.is_set():
                    try:
                        name,payload,future=self.commands.get(timeout=0.15)
                    except queue.Empty:
                        try:
                            self.page.wait_for_timeout(50)
                            if self.dirty:
                                self.snapshot();self.dirty=False
                                self.verify_receipt()
                        except Exception: pass
                        continue
                    try:future.set_result(self.handle(name,payload))
                    except Exception as exc:future.set_exception(exc)
                self.snapshot();self.context.close();browser.close()
        except Exception as exc:
            try:self.pause(classify_exception(exc),'Browser startup or checkpoint failure; inspect service logs')
            except Exception:pass
            while True:
                try:_,_,future=self.commands.get_nowait();future.set_exception(ValueError('Browser startup failed'))
                except queue.Empty:break

    def handle(self, name, payload):
        if name=='screenshot': return self.page.screenshot()
        if name=='inspect':
            return {'url':safe_url(self.page.url),'title':self.page.title(),'elements':self.page.locator('[id]').evaluate_all("els => els.slice(0,200).map(e=>({tag:e.tagName,id:e.id,text:(e.tagName==='INPUT'?'':e.textContent||'').slice(0,180)}))")}
        if name=='receipt_review':
            if not payload.get('confirmed_receipt_review'): raise ValueError('Operator must review actual employer receipt')
            for key in ('receipt_selector','receipt_text_pattern','receipt_url_pattern'):
                if not payload.get(key): raise ValueError('Exact receipt selector, text and URL pattern required')
            re.compile(payload['receipt_text_pattern']);re.compile(payload['receipt_url_pattern'])
            with self.manager.sessions() as db:
                record=self.record(db)
                if not record.spec.get('authorized'): raise ValueError('No application authorization')
                record.spec={**record.spec,'verify_manual_receipt':True,**{k:payload[k] for k in ('receipt_selector','receipt_text_pattern','receipt_url_pattern','receipt_number_selector') if payload.get(k)}}
                db.add(EventLog(application_id=record.application_id,event='operator_receipt_contract_reviewed',details={'session_id':self.sid}));db.commit()
            return {'receipt_verified':self.verify_receipt()}
        if name=='resume':
            if self.verify_receipt(): return {'status':'receipt_verified'}
            with self.manager.sessions() as db:
                record=self.record(db);app=db.get(Application,record.application_id)
                if app.status in TERMINAL|{'submitting'}:raise ValueError('Completed or uncertain final submission cannot be resumed')
                if record.checkpoint.get('action_in_progress') is not None:
                    raise ValueError('Interrupted form action requires explicit reconcile-cursor command')
                record.checkpoint={**record.checkpoint,'human_review_cursor':record.cursor};db.commit()
            failure=challenge_kind(self.page)
            if failure:raise BrowserProblem(failure,'Website still requires human verification; not resumed')
            with self.manager.sessions() as db:
                record=self.record(db);record.status='active';record.failure_kind=None;record.capability_hash=None;record.capability_expires=None;db.get(Application,record.application_id).status='in_progress';db.commit()
            self.emit('human_resume_requested',{'session_id':self.sid})
            try:self.advance()
            except Exception as exc:self.pause(classify_exception(exc),'Verification or form condition still unresolved')
            return {'status':'checked'}
        if name=='reconcile_cursor':
            with self.manager.sessions() as db:
                record=self.record(db);app=db.get(Application,record.application_id)
                if app.status in TERMINAL|{'submitting'}:raise ValueError('Cannot reconcile an uncertain final submission through cursor control')
                cursor=payload['cursor']
                if not 0<=cursor<=len(self.actions(record.spec)):raise ValueError('Invalid reviewed cursor')
                record.cursor=cursor;record.checkpoint={**record.checkpoint,'action_in_progress':None};db.add(EventLog(application_id=app.id,event='operator_cursor_reconciled',details={'cursor':cursor}));db.commit()
            return {'cursor':cursor}
        if name=='control':
            with self.manager.sessions() as db:
                record=self.record(db)
                if record.status not in ('needs_human','submission_uncertain'): raise ValueError('Human control only allowed for a paused application')
            # Only human-issued normal mouse/keyboard actions. No JS execution, network forging or CAPTCHA token API.
            action=payload['action']
            if action=='move':self.page.mouse.move(max(0,min(1279,payload['x'])),max(0,min(899,payload['y'])))
            elif action=='down':
                self.page.mouse.move(max(0,min(1279,payload['x'])),max(0,min(899,payload['y'])));self.page.mouse.down()
            elif action=='up':self.page.mouse.up()
            elif action=='click':self.page.mouse.click(max(0,min(1279,payload['x'])),max(0,min(899,payload['y'])))
            elif action=='scroll':self.page.mouse.wheel(0,max(-1500,min(1500,payload['delta'])))
            elif action=='text':self.page.keyboard.insert_text(payload['text'][:2000])
            elif action=='key':
                allowed={'Tab','Shift+Tab','Enter','Backspace','Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Control+A','Escape'}
                if payload['key'] not in allowed:raise ValueError('Key not allowed')
                self.page.keyboard.press(payload['key'])
            elif action=='attach':
                with self.manager.sessions() as db:
                    record=self.record(db)
                    field=next((a for a in self.actions(record.spec) if a.get('artifact_id')==payload['artifact_id'] and a.get('kind')=='file'),None)
                    if field is None:raise ValueError('No reviewed mapping for this attachment')
                    self.attachment(db,record,payload['artifact_id'],field['selector'])
            else:raise ValueError('Unsupported human operation')
            self.emit('human_browser_control',{'session_id':self.sid,'action':action})
            self.dirty=True
            return {'ok':True}
        raise ValueError('Unknown browser command')
