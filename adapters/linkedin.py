"""Fail-closed LinkedIn Easy Apply adapter. No private API or generated resume."""
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright
from sqlalchemy import select, update

from adapters.browser import NeedsHuman
from app.db import Application, Artifact, Candidate, EventLog, HumanAction, Job, now

RESUME = Path('artifacts/real-validation/resume.pdf')
PROFILE = Path('artifacts/real-validation/profile.json')
CITIES = (('上海', 'Shanghai'), ('北京', 'Beijing'), ('深圳', 'Shenzhen'),
          ('广州', 'Guangzhou'), ('香港', 'Hong Kong'), ('澳门', 'Macau', 'Macao'))
LEGAL = re.compile(r'consent|agree|certif|declar|privacy|terms|acknowledge|同意|声明|承诺|隐私', re.I)
RECEIPT = re.compile(r'application (?:was |has been )?(?:sent|submitted)|申请已(?:发送|提交)|已提交申请', re.I)


def job_url(value):
    parsed = urlsplit(value)
    match = re.fullmatch(r'/jobs/view/(?:[^/]+-)?(\d+)/?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname not in ('linkedin.com', 'www.linkedin.com') or parsed.port not in (None, 443) or parsed.username or not match:
        raise ValueError('Expected an HTTPS LinkedIn /jobs/view/NUMERIC_ID URL')
    return 'https://www.linkedin.com/jobs/view/' + match[1] + '/'


def rank_job(text):
    """Ranking only: a year mention is never an eligibility decision."""
    explicit = bool(re.search(r'2027\s*(?:届|graduates?|graduation)|(?:graduat\w*|class of)\D{0,20}2027', text, re.I))
    possible = explicit or bool(re.search(r'202[6-8].{0,20}202[7-9]|2027|在校|current students|final.year', text, re.I))
    city = next((i for i, names in enumerate(CITIES) if any(value.lower() in text.lower() for value in names)), len(CITIES))
    return {'priority': (0 if explicit else 1 if possible else 2) * 100 + city,
            'graduation_signal': 'explicit_2027' if explicit else 'review_2027' if possible else 'unknown',
            'eligibility_verified': False}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_materials(candidate):
    if not candidate or not candidate.verified:
        raise NeedsHuman('Candidate profile is not verified')
    if not RESUME.is_file() or not PROFILE.is_file():
        raise NeedsHuman('Original resume.pdf and verified profile.json are required')
    profile = json.loads(PROFILE.read_text())
    # Disk JSON is not self-authenticating: compare it with the verified DB record.
    if {k:v for k,v in profile.items() if k != 'source_text'} != {k:v for k,v in candidate.profile.items() if k != 'source_text'} or ('source_text' in profile and profile['source_text'] != candidate.profile.get('source_text')):
        raise NeedsHuman('profile.json does not match the verified candidate record')
    if not Path(candidate.source_pdf).is_file() or digest(candidate.source_pdf) != digest(RESUME):
        raise NeedsHuman('Original resume differs from the verified source PDF')
    return candidate.profile, digest(RESUME)


@contextmanager
def browser(*, headless=True):
    import fcntl
    root = Path('state/browser')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    # Serialize access to one persistent login; waiting for a human never holds it.
    with (root / '.worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with sync_playwright() as p:
            options = {'headless': headless, 'executable_path': os.getenv('CHROMIUM_EXECUTABLE') or None}
            if os.getenv('HTTPS_PROXY'):
                options['proxy'] = {'server': os.environ['HTTPS_PROXY'], 'bypass': 'localhost,127.0.0.1,[::1]'}
            context = p.chromium.launch_persistent_context(str(root), **options)
            try:
                page = context.new_page()
                page.set_default_timeout(10000)
                yield page
            finally:
                context.close()


def check_page(page):
    if urlsplit(page.url).hostname not in ('www.linkedin.com', 'linkedin.com'):
        raise NeedsHuman('Third-party application route rejected')
    if re.search(r'/checkpoint|/login|/authwall|/uas/', page.url):
        raise NeedsHuman('LinkedIn login or security checkpoint requires human action')
    if page.locator('iframe[src*="captcha"], input[type="password"], #captcha-internal').count():
        raise NeedsHuman('Login or CAPTCHA requires human action')


def batch_unlocked(db, candidate_id):
    for event in db.scalars(select(EventLog).where(EventLog.event == 'linkedin_real_e2e_verified')):
        app = db.get(Application, event.application_id)
        if app and app.candidate_id == candidate_id and app.status == 'submitted' and app.submitted_at:
            candidate = db.get(Candidate, candidate_id)
            if not Path(candidate.source_pdf).is_file() or event.details.get('resume_sha256') != digest(candidate.source_pdf):
                continue
            artifacts = list(db.scalars(select(Artifact).where(Artifact.application_id == app.id)))
            verified_kinds = {a.kind for a in artifacts if Path(a.path).is_file() and digest(a.path) == a.sha256}
            if {'linkedin_receipt', 'linkedin_receipt_screenshot'} <= verified_kinds:
                return True
    return False


def already_submitted(db, url, resume_hash):
    # Also cover duplicate job/candidate records imported under tracking URLs.
    for application in db.scalars(select(Application).where(Application.status == 'submitted')):
        job = db.get(Job, application.job_id)
        candidate = db.get(Candidate, application.candidate_id)
        try:
            same_job = job_url(job.url) == job_url(url)
        except ValueError:
            continue
        if same_job and Path(candidate.source_pdf).is_file() and digest(candidate.source_pdf) == resume_hash:
            return True
    return False


class LinkedInExecutor:
    def __init__(self, db):
        self.db = db

    def pause(self, application, reason):
        if application.status in ('submitted', 'simulated'):
            raise NeedsHuman(reason)
        if application.status not in ('submitting', 'submission_uncertain'):
            application.status = 'needs_human'
        else:
            application.status = 'submission_uncertain'
        self.db.add(HumanAction(application_id=application.id, reason=reason))
        self.db.add(EventLog(application_id=application.id, event='linkedin_human_required', details={'reason': reason}))
        self.db.commit()
        raise NeedsHuman(reason)

    def run(self, application_id, *, authorized=False, validation=False):
        app = self.db.get(Application, application_id)
        if not app:
            raise ValueError('Application not found')
        if app.status in ('submitted', 'submitting', 'submission_uncertain', 'simulated'):
            raise NeedsHuman('Completed or ambiguous application cannot be replayed')
        job = self.db.get(Job, app.job_id)
        candidate = self.db.get(Candidate, app.candidate_id)
        try:
            url = job_url(job.url)
            profile, resume_hash = load_materials(candidate)
            if already_submitted(self.db, url, resume_hash):
                raise NeedsHuman('This candidate already submitted to this LinkedIn job')
            if not authorized:
                raise NeedsHuman('Explicit submission authorization required')
            if app.checkpoint.get('adapter') == 'linkedin' and not app.checkpoint.get('restart_reviewed'):
                raise NeedsHuman('Interrupted draft requires explicit human review before restarting')
            if not validation and not batch_unlocked(self.db, candidate.id):
                raise NeedsHuman('Complete one real LinkedIn validation before batch applications')
            checked = datetime.fromisoformat(job.assessment.get('checked_at', '1970-01-01T00:00:00+00:00'))
            if not job.assessment.get('eligible') or checked.tzinfo is None or not timedelta(0) <= now()-checked <= timedelta(hours=24):
                raise NeedsHuman('Job eligibility and open status need current verification')
            with browser() as page:
                self.db.refresh(app)
                if app.status in ('submitted', 'submitting', 'submission_uncertain', 'simulated'):
                    raise NeedsHuman('Completed or ambiguous application cannot be replayed')
                if already_submitted(self.db, url, resume_hash):
                    raise NeedsHuman('This candidate already submitted to this LinkedIn job')
                app.checkpoint = {'adapter': 'linkedin', 'phase': 'opening', 'restart_reviewed': False, 'resume_sha256': resume_hash}
                self.db.commit()
                page.goto(url, wait_until='domcontentloaded')
                check_page(page)
                if page.get_by_text(re.compile(r'no longer accepting applications|不再接受申请|已停止接受申请', re.I)).count():
                    raise NeedsHuman('LinkedIn vacancy is closed')
                easy = page.get_by_role('button', name=re.compile(r'^(?:Easy Apply|简易申请|快速申请)(?:\s|$)', re.I))
                if easy.count() != 1:
                    raise NeedsHuman('No unambiguous LinkedIn Easy Apply button; external routes are unsupported')
                easy.click()
                self.form(page, app, job, profile, resume_hash, validation)
        except NeedsHuman as exc:
            self.pause(app, str(exc))
        except Exception:
            # A crash after ANY step can have changed server state. Never blindly replay.
            self.pause(app, 'Browser operation interrupted; inspect LinkedIn draft or receipt before resuming')

    def form(self, page, app, job, profile, resume_hash, validation):
        evidence = Path('artifacts/linkedin') / str(app.id)
        evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
        answers = profile.get('linkedin_answers', {})
        seen = set()
        resume_confirmed = False
        for step in range(20):
            check_page(page)
            dialog = page.get_by_role('dialog')
            if dialog.count() != 1:
                raise NeedsHuman('Unknown Easy Apply dialog')
            if dialog.get_by_text(LEGAL).count():
                raise NeedsHuman('Consent or legal declaration requires human review')
            signature = dialog.inner_text()
            if signature in seen:
                raise NeedsHuman('Form did not advance or contains validation errors')
            seen.add(signature)
            for field in dialog.locator('input, select, textarea').all():
                if not field.is_visible() and field.get_attribute('type') != 'file':
                    continue
                kind = field.get_attribute('type') or ''
                if kind in ('hidden', 'submit', 'button'):
                    continue
                label = field.evaluate("e => (e.labels?.[0]?.innerText || e.getAttribute('aria-label') || '').trim()")
                if kind in ('checkbox', 'radio') or LEGAL.search(label):
                    raise NeedsHuman('Choice or declaration requires explicit human review')
                required = field.get_attribute('required') is not None or field.get_attribute('aria-required') == 'true'
                if kind == 'file':
                    if re.search(r'resume|cv|简历', label, re.I):
                        if digest(RESUME) != resume_hash:
                            raise NeedsHuman('Resume changed during application')
                        if resume_confirmed:
                            continue
                        field.set_input_files(str(RESUME))
                        # File selection alone is not backend upload acceptance.
                        dialog.get_by_text(RESUME.name, exact=True).wait_for(state='visible')
                        confirmation = job.assessment.get('linkedin_resume_confirmation_selector')
                        if not confirmation:
                            raise NeedsHuman('Review a LinkedIn backend upload acceptance selector before submitting')
                        dialog.locator(confirmation).wait_for(state='visible')
                        resume_confirmed = True
                    elif required and re.search(r'cover letter|求职信', label, re.I):
                        facts = verified_facts(profile, ascii_only=True)
                        from reportlab.platypus import SimpleDocTemplate, Paragraph
                        from reportlab.lib.styles import getSampleStyleSheet
                        from xml.sax.saxutils import escape
                        cover = evidence / 'required-cover-letter.pdf'
                        SimpleDocTemplate(str(cover)).build([Paragraph(escape(f), getSampleStyleSheet()['BodyText']) for f in facts])
                        field.set_input_files(str(cover))
                        dialog.get_by_text(cover.name, exact=True).wait_for(state='visible')
                        confirmation = job.assessment.get('linkedin_cover_confirmation_selector')
                        if not confirmation:
                            raise NeedsHuman('Required cover letter needs a reviewed backend upload acceptance selector')
                        dialog.locator(confirmation).wait_for(state='visible')
                    elif required:
                        raise NeedsHuman('Unknown required attachment')
                    continue
                if re.search(r'cover letter|求职信', label, re.I):
                    if not required:
                        continue
                    value = '\n'.join(verified_facts(profile))
                elif label in answers and isinstance(answers[label], (str, int, float)) and not isinstance(answers[label], bool):
                    value = str(answers[label])
                else:
                    # Unknown prefilled answers are not implicitly trusted either.
                    raise NeedsHuman('Unknown application question; add a verified exact-label answer')
                if field.evaluate('e => e.tagName') == 'SELECT':
                    field.select_option(label=value)
                else:
                    field.fill(value)
            app.checkpoint = {'adapter': 'linkedin', 'step': step, 'url': job.url, 'resume_sha256': resume_hash}
            self.db.commit()
            page.screenshot(path=str(evidence / f'step-{step}.png'), full_page=True)
            submit = dialog.get_by_role('button', name=re.compile(r'^(?:Submit application|提交申请)$', re.I))
            if submit.count() == 1:
                if not resume_confirmed:
                    raise NeedsHuman('Original resume upload has not been confirmed in this run')
                self.submit(page, dialog, submit, app, job, evidence, resume_hash, validation)
                return
            advance = dialog.get_by_role('button', name=re.compile(r'^(?:Next|Review|Review application|下一步|查看申请|检查)$', re.I))
            if advance.count() != 1:
                raise NeedsHuman('Unknown form navigation')
            advance.click()
            # Wait for an actual DOM transition instead of assuming a click advanced.
            page.wait_for_function('(old) => {const e=document.querySelector("[role=dialog]"); return e && e.innerText !== old}', arg=signature)
        raise NeedsHuman('Easy Apply step limit exceeded')

    def submit(self, page, dialog, button, app, job, evidence, resume_hash, validation):
        if digest(RESUME) != resume_hash:
            raise NeedsHuman('Resume changed before submission')
        if already_submitted(self.db, job.url, resume_hash):
            raise NeedsHuman('This candidate already submitted to this LinkedIn job')
        intent = self.db.execute(update(Application).where(Application.id == app.id, Application.status.in_(['draft', 'prepared', 'needs_human'])).values(status='submitting'))
        if intent.rowcount != 1:
            self.db.rollback()
            raise NeedsHuman('Submission already owned or ambiguous')
        self.db.add(EventLog(application_id=app.id, event='linkedin_submission_intent', details={'url': job.url, 'resume_sha256': resume_hash}))
        self.db.commit()
        try:
            if page.get_by_role('dialog').get_by_text(RECEIPT).count():
                raise NeedsHuman('A receipt was already present before the submit click')
            button.click()
            check_page(page)
            receipt = page.get_by_role('dialog').get_by_text(RECEIPT)
            receipt.wait_for(state='visible')
            check_page(page)
            text = receipt.inner_text().strip()
            shot = evidence / 'receipt.png'
            page.screenshot(path=str(shot), full_page=True)
            record = evidence / 'receipt.json'
            submitted = now()
            record.write_text(json.dumps({'url': job_url(job.url), 'submitted_at': submitted.isoformat(), 'receipt': text, 'resume_sha256': resume_hash, 'simulation': False}, ensure_ascii=False, indent=2))
            for file, kind in ((record, 'linkedin_receipt'), (shot, 'linkedin_receipt_screenshot')):
                self.db.add(Artifact(application_id=app.id, kind=kind, path=str(file), sha256=digest(file)))
            app.status = 'submitted'
            app.submitted_at = submitted
            app.receipt_number = text
            self.db.add(EventLog(application_id=app.id, event='linkedin_submitted', details={'url': job.url, 'simulation': False}))
            if validation:
                self.db.add(EventLog(application_id=app.id, event='linkedin_real_e2e_verified', details={'url': job.url, 'resume_sha256': resume_hash}))
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.pause(app, 'Submission receipt uncertain; reconcile LinkedIn before any retry')


def verified_facts(profile, *, ascii_only=False):
    facts = profile.get('facts', [])
    if not facts or not all(isinstance(f, str) and f.strip() and f in profile.get('source_text', '') for f in facts):
        raise NeedsHuman('Required cover letter needs verified source facts')
    # The local PDF font does not support arbitrary scripts. Do not emit corrupted text.
    if ascii_only and any(not f.isascii() for f in facts):
        raise NeedsHuman('Non-ASCII cover-letter facts require a reviewed font/template')
    return facts


def discover(db, search_urls):
    """Bounded public-UI discovery; save candidates for review, never infer eligibility."""
    with browser() as page:
        urls = set()
        for url in search_urls[:6]:
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.hostname != 'www.linkedin.com' or parsed.path != '/jobs/search/' or parsed.port not in (None, 443) or parsed.username:
                raise ValueError('Only LinkedIn job search URLs supported')
            page.goto(url, wait_until='domcontentloaded')
            check_page(page)
            for href in page.locator('a[href*="/jobs/view/"]').evaluate_all('els => els.map(e => e.href)'):
                try:
                    urls.add(job_url(href))
                except ValueError:
                    continue
        for url in sorted(urls)[:20]:
            if db.scalar(select(Job).where(Job.url == url)):
                continue
            page.goto(url, wait_until='domcontentloaded')
            check_page(page)
            if page.get_by_role('button', name=re.compile(r'^Easy Apply(?:\s|$)', re.I)).count() != 1:
                continue
            title = page.locator('h1').inner_text()
            jd = page.locator('.jobs-description').inner_text()
            db.add(Job(url=url, title=title, jd=jd, assessment={**rank_job(title+'\n'+jd), 'adapter': 'linkedin', 'eligible': False, 'checked_at': now().isoformat(), 'blockers': ['Human review of 2027 eligibility, location, work rights and open status required']}))
            db.commit()
