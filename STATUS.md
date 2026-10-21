# Phase 1 evidence — 2026-10-21

Implemented: app/{db,candidate,matching,materials,main}.py; adapters/{browser,jobs}.py; workers/{queue,main}.py; scripts/{manage,verify_restart}.py; deploy/{start.sh,cloud-build.py,cloud-compose.yaml}; Dockerfile, compose.yaml, .env.example, requirements.txt, README.md, .gitignore, .dockerignore, pytest.ini, tests/.

Actual validation in this cloud instance:

- Local suite: **16 passed, 2 skipped**. See artifacts/evidence/{pytest.log,tests.xml}.
- Same suite inside the built container: **16 passed, 2 skipped**. See artifacts/evidence/{container-tests.log,container-tests.xml}.
- Separate isolated PostgreSQL schema/locking integration: **1 passed**. See artifacts/evidence/{postgres.log,postgres.xml}. Test container removed afterwards.
- API and PostgreSQL container health checks passed; worker running. See artifacts/evidence/{container-ps.json,health.json,service.log}.
- API/DB/worker restart completed; DB audit event, file and persistent Chromium cookie survived. See artifacts/evidence/{restart.log,restart.json}.
- Hourly/daily tasks executed and persisted. See artifacts/evidence/{scheduled-tasks.json,daily-report.json}. Sources are empty; discovery correctly logs an unconfigured source list.
- Actual Chromium multipart upload to local synthetic server and simulated receipt verification passed. Screenshot: artifacts/applications/1/receipt.png. This is **not a real application**.
- Synthetic Chinese photo PDF and factual introduction generated: artifacts/materials/1/1/{resume.pdf,introduction.txt}. These contain synthetic test facts, not the user's resume.

Real job title/application URL: **not verified**. Attempted official Amazon jobs search (not an application URL): https://www.amazon.jobs/en/search.json?base_query=operations&loc_query=Shanghai&offset=0&result_limit=5. Current proxy returned 403 Forbidden; no vacancy or eligibility was inferred. No actual employer upload or submission occurred. See artifacts/evidence/real-status.json.

Remaining limitations: keyword matching with explicit human eligibility review; configured official-page discovery with JSON-LD extraction, no broad vacancy crawler; reviewed per-site form mapping required; no automatic CAPTCHA/SMS handling; pre-submit step replay rather than universal mid-form restoration; basic DB audit rather than tamper-proof logging; local report files rather than message delivery. There is no external SSH deployment or fresh-task snapshot-restoration claim.

Minimum next inputs: authentic resume PDF and photo; verified identity/contact details, precise graduation date and any employer-required declarations not present in the resume; access/login/interactive verification only when a selected official site requires them. No OpenAI API key is needed for the implemented deterministic material generator. External server access is only needed for deployment outside this cloud instance.

Saved configuration draft: install_script, start_skill, and custom network domains amazon.jobs/www.amazon.jobs. The user must review/save in environment settings and publish to activate/snapshot; persistence does not establish runtime application or publication.

Evidence files contain synthetic records only and are intentionally ignored by Git along with browser state and .env. Preserve the artifacts directory/cloud snapshot for review. GitHub source publication is a separate operation from cloud environment publication and external deployment. Consult the remote main branch for the saved source revision.

## Subsequent real-stage work and source preservation

Source-checked photo resume generation, a read-only real-site browser probe, and source-protection regression tests have been added. Authentic documents, generated application materials and detailed real-stage records remain private under ignored artifacts/real-validation/; they are intentionally absent from GitHub. No real employer submission or external server deployment is claimed. The original validation counts above describe the earlier phase. Amazon application testing is paused while source publication is prioritized.
