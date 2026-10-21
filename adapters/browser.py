"""Conservative browser execution. Real sites require a reviewed per-site mapping."""
import hashlib
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from sqlalchemy import update
from app.db import Application, Candidate, Job, HumanAction, EventLog, Artifact, now

class NeedsHuman(Exception): pass

class BrowserExecutor:
    def __init__(self, session, state_dir='state/browser'):
        self.db = session
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.state_dir, 0o700)

    @staticmethod
    def inspect(page):
        return page.locator('input,select,textarea,button').evaluate_all("els => els.map(e => ({tag:e.tagName,type:e.type,name:e.name,id:e.id,required:e.required,text:(e.labels?.[0]?.innerText || e.innerText || '').slice(0,200)}))")

    def pause(self, app, reason):
        app.status = 'needs_human'
        self.db.add(HumanAction(application_id=app.id, reason=reason))
        self.db.add(EventLog(application_id=app.id, event='human_required', details={'reason': reason}))
        self.db.commit()
        raise NeedsHuman(reason)

    def run(self, application_id, spec, *, submit=False, simulation=False):
        app = self.db.get(Application, application_id)
        if app.status in ('submitted', 'submitting', 'submission_uncertain'):
            raise ValueError('Submission already completed or ambiguous; reconcile before retry')
        candidate = self.db.get(Candidate, app.candidate_id)
        job = self.db.get(Job, app.job_id)
        if not candidate.verified: self.pause(app, 'Candidate profile is not verified')
        if simulation and urlparse(job.url).hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('Simulation is restricted to loopback fixtures')
        if not simulation:
            checked = datetime.fromisoformat(job.assessment.get('checked_at', '1970-01-01T00:00:00+00:00'))
            if checked.tzinfo is None or datetime.now(timezone.utc) - checked > timedelta(hours=24):
                self.pause(app, 'Vacancy verification is stale; recheck official page')
        if not simulation and not job.assessment.get('eligible'): self.pause(app, 'Job eligibility not verified')
        if not simulation and not spec.get('reviewed'): self.pause(app, 'Official site adapter requires review')
        if not simulation and urlparse(job.url).scheme != 'https': self.pause(app, 'Real application requires HTTPS')
        if submit and not spec.get('authorized'): self.pause(app, 'Missing explicit application authorization')
        evidence = Path('artifacts/applications') / str(app.id)
        evidence.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(self.state_dir), headless=True, executable_path=os.getenv('CHROMIUM_EXECUTABLE') or None)
            try:
                page = context.new_page()
                page.set_default_timeout(15000)
                page.goto(job.url, wait_until='domcontentloaded')
                for index, step in enumerate(spec['steps']):
                    text = page.locator('body').inner_text().lower()
                    if any(t in text for t in ['验证码', '短信验证', 'captcha', 'verification code']): self.pause(app, 'Security challenge requires user interaction')
                    mapped = set()
                    for field in step.get('fields', []):
                        selector = field['selector']
                        target = page.locator(selector)
                        mapped.add(selector)
                        if field.get('kind') == 'file':
                            artifact = self.db.get(Artifact, field['artifact_id'])
                            if artifact is None or artifact.application_id != app.id:
                                self.pause(app, 'Attachment must belong to this application')
                            if hashlib.sha256(Path(artifact.path).read_bytes()).hexdigest() != artifact.sha256:
                                self.pause(app, 'Attachment integrity check failed')
                            target.set_input_files(artifact.path)
                            if not simulation:
                                if not field.get('upload_confirm_selector'):
                                    self.pause(app, 'Real upload needs a site-specific backend acceptance indicator')
                                page.locator(field['upload_confirm_selector']).wait_for(state='visible')
                                self.db.add(EventLog(application_id=app.id, event='upload_confirmed', details={'artifact_id':artifact.id,'url':page.url}))
                                self.db.commit()
                        else:
                            key = field['profile_key']
                            value = candidate.profile.get(key)
                            if value is None: self.pause(app, f'Missing verified profile field: {key}')
                            if field.get('kind') == 'select': target.select_option(str(value))
                            else: target.fill(str(value))
                    # Any unmapped required input or legal declaration needs human review.
                    for control in page.locator('input[required],select[required],textarea[required],input[type=checkbox],input[type=radio]').all():
                        if not control.is_visible(): continue
                        if control.get_attribute('type') in ('checkbox', 'radio'): self.pause(app, 'Consent or legal declaration requires explicit review')
                        if not any(control.evaluate('(e,s)=>e.matches(s)', s) for s in mapped): self.pause(app, 'Unmapped required field')
                    app.checkpoint = {'step': index, 'url': page.url, 'simulation': simulation}
                    self.db.add(EventLog(application_id=app.id, event='form_filled', details=app.checkpoint))
                    self.db.commit()
                    page.screenshot(path=str(evidence / f'step-{index}.png'), full_page=True)
                    if step.get('next'): page.locator(step['next']).click()
                if not submit:
                    app.status = 'prepared'
                    self.db.commit()
                    return {'submitted': False, 'simulation': simulation}
                # Commit intent BEFORE clicking. An interrupted/ambiguous click is NEVER retried automatically.
                claimed = self.db.execute(update(Application).where(Application.id == app.id, Application.status.in_(['draft', 'prepared', 'needs_human'])).values(status='submitting'))
                if claimed.rowcount != 1:
                    self.db.rollback()
                    raise ValueError('Another executor owns this submission')
                self.db.add(EventLog(application_id=app.id, event='submission_intent', details={'url': page.url, 'simulation': simulation}))
                self.db.commit()
                try:
                    page.locator(spec['submit_selector']).click()
                    receipt = page.locator(spec['receipt_selector'])
                    receipt.wait_for(state='visible', timeout=spec.get('timeout_ms', 15000))
                    number = receipt.inner_text().strip()
                    if not number: raise ValueError('Empty confirmation')
                    shot = evidence / 'receipt.png'
                    page.screenshot(path=str(shot), full_page=True)
                    html = evidence / 'confirmation.html'
                    html.write_text(page.content(), encoding='utf-8')
                    app.status = 'simulated' if simulation else 'submitted'
                    app.receipt_number = number
                    app.submitted_at = now()
                    for file, kind in [(shot, 'receipt_screenshot'), (html, 'confirmation_html')]:
                        self.db.add(Artifact(application_id=app.id, kind=kind, path=str(file), sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
                    self.db.add(EventLog(application_id=app.id, event=app.status, details={'url': job.url, 'receipt': number, 'simulation': simulation}))
                    self.db.commit()
                    return {'submitted': not simulation, 'simulation': simulation, 'receipt': number}
                except Exception as exc:
                    app.status = 'submission_uncertain'
                    self.db.add(HumanAction(application_id=app.id, reason='Submission result ambiguous; reconcile portal before any retry'))
                    self.db.commit()
                    raise NeedsHuman('Submission result uncertain') from exc
            finally:
                context.close()
