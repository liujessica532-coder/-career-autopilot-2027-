# Career Autopilot — phase 1 implementation

A conservative Python service for a 2027 translation master's graduate targeting Beijing, Shanghai, Guangzhou, Shenzhen and Hong Kong. Mainland work eligibility and Hong Kong sponsorship must be confirmed in the candidate/job records. Global travel preferences may be stored in the verified profile. Official graduate vacancies take priority; internships require separate review.

**This checkout has not submitted a real application.** Synthetic fixtures and their receipts are labelled SIMULATION. Real candidate resumes, photos and browser sessions are excluded from Git. Private local material preparation is recorded under ignored artifacts/real-validation/ when present. Real-site tests are opt-in and skipped without actual verified material. HTTP 200 does not establish an open vacancy or eligibility.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export CHROMIUM_EXECUTABLE=/usr/bin/chromium # supported installed Chromium
# Otherwise install the Playwright browser with its normal verified download.
.venv/bin/python -m scripts.manage init
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/bin/python -m workers.main
```

The default SQLite database supports local tests. Production uses PostgreSQL. Schema creation is idempotent for a new deployment; future schema evolution needs migrations before upgrades. No OpenAI API key is required: materials reorder verified source facts deterministically, without generated claims.

## Real candidate workflow

1. Import user-provided source documents: `python -m scripts.manage import --pdf /secure/resume.pdf --photo /secure/photo.jpg`. Textless scanned PDFs pause for transcription; there is no automatic OCR guessing.
2. Review extracted text locally. Prepare a private profile JSON with `name`, `facts` (verbatim source excerpts), contact fields and any confirmed answers. `python -m scripts.manage verify-profile --candidate-id ID --profile /secure/profile.json --confirmed`. Operator confirmation is required. Never use the test fixtures as candidate data.
3. Inspect an official vacancy and its actual application route. Save a private JSON with `url`, `title`, `jd`, `open_verified`, `graduation_verified`, `hk_sponsorship_verified` and `evidence`: each true verification flag needs a corresponding source quote plus URL and time. `python -m scripts.manage add-job --record /secure/job.json`. Matching provides conservative keyword signals, not a semantic eligibility decision. An explicit 2027 mention alone does not establish eligibility. Relevant experience, language, degree, deadline, visa and declarations must be checked by the operator.
4. Generate: `python -m scripts.manage prepare --candidate-id ID --job-id ID --language zh`. Outputs are under ignored `artifacts/materials/`. Facts are reused verbatim; photo is preserved. Generated introduction is factual, not a polished AI cover letter. English uses `--language en` and requires English source facts.
5. Inspect controls with `BrowserExecutor.inspect(page)` and write a private reviewed site specification. It contains `reviewed:true`, `steps:[{fields:[{selector,profile_key} or {selector,kind:"select",profile_key} or {selector,kind:"file",artifact_id,upload_confirm_selector}],next:"optional selector"}]`, `submit_selector` and a precise `receipt_selector`. Each attachment must belong to this application and its stored SHA256 must match. This phase implements reviewed adapters, not a universal autonomous website solver.
6. `python -m scripts.manage enqueue-application --application-id ID --spec /secure/site.json` queues upload-only execution. Add `--submit-authorized` only for an application covered by the user's authorization and all verified rules. Worker executes it and records evidence. Preflight pauses on unverified candidate, ineligible job, security challenge, unknown required controls and consent/legal declarations. Never add unchecked guessed values to a mapping.

The executor stores `submitting` before the final click. Ambiguous result creates HumanAction and blocks automated resubmission. An operator must reconcile the employer portal before changing that state. Browser profiles persist in `state/browser` (0700); do not commit, share or log cookies. Login/SMS/CAPTCHA need a future controlled interactive operator session or manual completion; no bypass exists. Step checkpoints and persistent sessions are retained, but form steps are replayed after failures before final submission; arbitrary site-specific mid-form restoration is not guaranteed. Do not mark already-submitted steps as `next`.

## Queue and schedules

PostgreSQL `SKIP LOCKED`, atomic claim, unique keys, leases, capped exponential backoff and five attempts. Worker deadline 240 seconds, lease 300 seconds. Application uniqueness is candidate/job. Compare-and-set submission intent protects concurrent workers. Lease-expired tasks can be reclaimed; ambiguous final submission is human-only. API and workers can restart without losing DB state. Scheduling enqueues hourly discovery and daily JSON reports (Asia/Shanghai; configurable with WORKFLOW_TIMEZONE). `JOB_SOURCES_JSON` is a list of `{ "url": "https://official-employer/job", "title": "..." }`; no sources means an audited no-op, not a claim of discovering jobs. Source fetching is basic and requires a reviewed site adapter to extract semantic JD/closed status. Reports are persisted, not emailed. No outbound messages are sent.

## Server deployment

```bash
cp .env.example .env
# Set POSTGRES_PASSWORD to a fresh URL-safe password; chmod 600 .env
./deploy/start.sh
# Restricted home directory: DOCKER_CONFIG=/workspace/career-docker ./deploy/start.sh
docker compose ps
docker compose logs --tail 100 api worker
docker compose restart api worker
curl --fail http://127.0.0.1:8000/health
```

PostgreSQL, artifacts and browser state use named volumes. API binds only loopback and exposes health only. Worker has no public port. Docker images use non-root runtime. Health checks validate DB connectivity. Configure encrypted disks/volume backups, retention and host access before storing real personal data. There is no external server SSH deployment claim. Back up DB and both volumes together. Do not delete volumes during upgrades. A fresh host must install Docker Engine + Compose and permit image/package downloads. Chromium comes from Debian packages without disabling signature/TLS verification.

## Tests and evidence

`python -m pytest --junitxml=artifacts/evidence/tests.xml`. Core tests exercise fail-closed matching, profile gate, PDF extraction, duplicate constraints and lease recovery. Browser tests use a real Chromium process against a local synthetic server and validate actual multipart upload + simulated receipt screenshot. They are not real applications. PostgreSQL test uses an isolated disposable DB via `TEST_POSTGRES_URL`. Real upload test: set `LIVE_UPLOAD_SPEC` and `LIVE_APPLICATION_ID`; this never clicks final submit. Skips are not passes. The generic test cannot prove employer backend receipt of a file; a reviewed site-specific upload confirmation is still required.

Audit records: EventLog, HumanAction, application URL through Job, submitted_at, receipt_number, artifact paths and SHA256; confirmation HTML/screenshot under ignored artifacts. This prototype does not yet provide tamper-proof or encrypted audit storage. Network restrictions may block official sites; see current evidence/status report. Real applications remain blocked until authentic candidate data, exact eligibility and working official route are verified.

### Cloud sandbox build

When the cloud proxy uses a private CA and its hostname is not resolvable inside Docker:

```bash
python3 deploy/cloud-build.py
DOCKER_CONFIG=/workspace/career-docker docker compose -f compose.yaml -f deploy/cloud-compose.yaml up -d --no-build --wait
```

The helper resolves only the existing proxy host and supplies the cloud machine's trusted public CA bundle through a Docker build secret. It does not turn off TLS/signature checks or save proxy credentials. Use the normal deployment script on a directly connected server.

After resolving a HumanAction or a pre-submit failed task, resume with `python -m scripts.manage resume-application --application-id ID --spec /secure/reviewed.json --confirmed`. This cannot resume a submitted or ambiguous final click and preserves the original authorization. Real upload mappings must name a site-specific backend acceptance indicator (`upload_confirm_selector`); setting a file input alone is insufficient.
