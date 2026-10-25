"""LinkedIn queue controls. Private data remains outside tracked files."""
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
from sqlalchemy import select
from adapters.linkedin import job_url, load_materials, batch_unlocked, rank_job, already_submitted, browser, check_page
from app.db import init_db, Session, Application, Candidate, Job, Task, HumanAction, now
from workers.queue import enqueue


def queue_application(db, candidate_id, job_id, *, validation=False):
    candidate = db.get(Candidate, candidate_id)
    _, resume_hash = load_materials(candidate)
    job = db.get(Job, job_id)
    if not job:
        raise ValueError('Job not found; import a reviewed job using scripts.manage add-job')
    canonical = job_url(job.url)
    if already_submitted(db, canonical, resume_hash):
        raise ValueError('Candidate already submitted to this job')
    if job.url != canonical:
        raise ValueError('Use the canonical LinkedIn job URL when importing the reviewed job')
    if not job.assessment.get('eligible'):
        raise ValueError('Review vacancy, 2027 eligibility and work rights before enqueueing')
    if not validation and not batch_unlocked(db, candidate_id):
        raise ValueError('First complete one real --validation application')
    if validation and db.scalar(select(Task).where(Task.kind == 'linkedin_apply', Task.status.in_(['pending', 'running', 'human_required']))):
        raise ValueError('An application is already outstanding; finish or reconcile it first')
    app = db.scalar(select(Application).where(Application.candidate_id == candidate_id, Application.job_id == job_id))
    if app:
        raise ValueError('Application already exists; use explicit reviewed resume for a paused task')
    app = Application(candidate_id=candidate_id, job_id=job_id)
    db.add(app)
    db.commit()
    enqueue(db, f'linkedin-apply:{candidate_id}:{job_id}', 'linkedin_apply', {'application_id': app.id, 'authorized': True, 'validation': validation})
    return app.id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('enqueue')
    p.add_argument('--candidate-id', type=int, required=True)
    p.add_argument('--job-id', type=int, required=True)
    p.add_argument('--validation', action='store_true')
    p.add_argument('--submit-authorized', action='store_true', required=True)
    p = sub.add_parser('discover')
    p.add_argument('--search-url', action='append', required=True)
    p = sub.add_parser('review-job')
    p.add_argument('--record', type=Path, required=True)
    p.add_argument('--confirmed', action='store_true', required=True)
    p = sub.add_parser('enqueue-ranked')
    p.add_argument('--candidate-id', type=int, required=True)
    p.add_argument('--submit-authorized', action='store_true', required=True)
    p = sub.add_parser('resume')
    p.add_argument('--application-id', type=int, required=True)
    p.add_argument('--confirmed-draft-reviewed', action='store_true', required=True)
    p = sub.add_parser('check-materials')
    p.add_argument('--candidate-id', type=int, required=True)
    sub.add_parser('login', help='Open a visible local Chromium; login manually, no credentials in CLI')
    p = sub.add_parser('probe', help='Read-only vacancy inspection; never submits')
    p.add_argument('--url', default='https://www.linkedin.com/jobs/view/4481983174/')
    args = parser.parse_args()
    if args.command == 'login':
        with browser(headless=False) as page:
            page.goto('https://www.linkedin.com/login', wait_until='domcontentloaded')
            input('Complete login/security checks in the local browser, then press Enter here: ')
            page.goto('https://www.linkedin.com/feed/', wait_until='domcontentloaded')
            check_page(page)
        print('Login page checks passed; validate the job separately')
        return
    if args.command == 'probe':
        import re
        from adapters.linkedin import digest
        with browser() as page:
            url = job_url(args.url)
            page.goto(url, wait_until='domcontentloaded')
            # Public job pages may contain a login modal but still expose a JD.
            # Read-only probing does not grant permission to fill or apply.
            from urllib.parse import urlsplit
            if urlsplit(page.url).hostname not in ('www.linkedin.com', 'linkedin.com'):
                raise ValueError('LinkedIn public job page redirected off site')
            text = page.locator('body').inner_text()
            descriptions = page.locator('.jobs-description, .show-more-less-html__markup')
            jd = '\n'.join(descriptions.all_inner_texts())
            evidence_dir = Path('artifacts/linkedin/probes')
            evidence_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            shot = evidence_dir / (url.split('/')[-2] + '.png')
            page.screenshot(path=str(shot), full_page=True)
            result = {'url': url, 'checked_at': now().isoformat(),
                      'inspection': 'read_only_public_signals_not_eligibility_verification',
                      'title': page.locator('h1').first.inner_text() if page.locator('h1').count() else None,
                      'description_available': bool(jd.strip()),
                      'login_controls_present': page.locator('input[type="password"]').count() > 0,
                      'easy_apply_button': page.get_by_role('button', name=re.compile(r'^(?:Easy Apply|简易申请|快速申请)(?:\s|$)', re.I)).count() == 1,
                      'closed_signal': bool(re.search(r'no longer accepting applications|不再接受申请|已停止接受申请', text, re.I)),
                      **rank_job(jd), 'screenshot_sha256': digest(shot)}
            (evidence_dir / (url.split('/')[-2] + '-description.txt')).write_text(jd)
            (evidence_dir / (url.split('/')[-2] + '.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False))
        return
    init_db()
    with Session() as db:
        if args.command == 'check-materials':
            load_materials(db.get(Candidate, args.candidate_id))
            print('Verified profile matches private JSON; original resume hash matches source PDF')
            return
        if args.command == 'enqueue':
            print('Queued application:', queue_application(db, args.candidate_id, args.job_id, validation=args.validation))
        elif args.command == 'discover':
            enqueue(db, 'linkedin-discover:manual:'+now().isoformat(), 'linkedin_discover', {'search_urls': args.search_url})
            print('Discovery queued for the existing Worker')
        elif args.command == 'review-job':
            record = json.loads(args.record.read_text())
            url = job_url(record['url'])
            evidence = record.get('evidence', {})
            checked_times = []
            for key in ('open', 'accepts_2027', 'location', 'work_rights'):
                item = evidence.get(key, {})
                if item.get('confirmed') is not True or not item.get('quote') or not item.get('checked_at') or job_url(item.get('url', '')) != url:
                    raise ValueError('Each eligibility decision needs confirmed, quote, checked_at and this LinkedIn URL')
                checked = datetime.fromisoformat(item['checked_at'])
                if checked.tzinfo is None or not timedelta(0) <= now()-checked <= timedelta(hours=24):
                    raise ValueError('Review evidence must be within the last 24 hours')
                checked_times.append(checked)
            if not record.get('resume_confirmation_selector'):
                raise ValueError('Review a backend resume-upload confirmation selector')
            job = db.scalar(select(Job).where(Job.url == url))
            if not job:
                job = Job(url=url, title=record['title'], jd=record['jd'])
                db.add(job)
            else:
                job.title = record['title']
                job.jd = record['jd']
            job.assessment = {**rank_job(record['title']+'\n'+record['jd']), 'eligible': True, 'evidence': evidence,
                              'checked_at': min(checked_times).isoformat(),
                              'adapter': 'linkedin', 'linkedin_resume_confirmation_selector': record['resume_confirmation_selector']}
            if record.get('cover_confirmation_selector'):
                job.assessment = {**job.assessment, 'linkedin_cover_confirmation_selector': record['cover_confirmation_selector']}
            db.commit()
            print('Reviewed job:', job.id)
        elif args.command == 'enqueue-ranked':
            if not batch_unlocked(db, args.candidate_id):
                raise ValueError('A verified real end-to-end application is required before batch enqueue')
            jobs = sorted(db.scalars(select(Job)), key=lambda job: job.assessment.get('priority', 999))
            count = 0
            for job in jobs:
                if job.assessment.get('adapter') != 'linkedin' or not job.assessment.get('eligible'):
                    continue
                if db.scalar(select(Application).where(Application.candidate_id == args.candidate_id, Application.job_id == job.id)):
                    continue
                queue_application(db, args.candidate_id, job.id)
                count += 1
                if count == 10:
                    break
            print('Queued reviewed applications:', count)
        elif args.command == 'resume':
            app = db.get(Application, args.application_id)
            task = db.scalar(select(Task).where(Task.kind == 'linkedin_apply', Task.key == f'linkedin-apply:{app.candidate_id}:{app.job_id}')) if app else None
            if not app or app.status != 'needs_human' or not task or task.status != 'human_required':
                raise ValueError('Only paused pre-submit LinkedIn tasks can resume; ambiguous submissions require receipt reconciliation')
            load_materials(db.get(Candidate, app.candidate_id))
            task.status = 'pending'
            task.attempts = 0
            task.available_at = now()
            task.lease_until = None
            app.checkpoint = {**app.checkpoint, 'restart_reviewed': True}
            for action in db.scalars(select(HumanAction).where(HumanAction.application_id == app.id, HumanAction.status == 'pending')):
                action.status = 'resolved_by_operator'
            db.commit()
            print('Queued reviewed draft; no final submission was replayed')


if __name__ == '__main__':
    main()
