# ApplyBuddy vs ApplyPilot — controlled evaluation (2026-10-25)

## Scope
Compare [ApplyBuddy](https://github.com/hangzhou-hz/ApplyBuddy) and [ApplyPilot](https://github.com/yvonnehe772/applypilot) as Codex workflows for real, verified job applications. This branch is experimental; do not merge into main without explicit approval.

## Candidate requirements
- 2027 master's graduate in English translation, TEM-8; use only verified resume facts.
- Prioritize overseas channel operations, international business operations, overseas marketing/e-commerce operations, supply-chain/logistics, and suitable graduate schemes.
- Open to long-term overseas assignments, subject to legal work authorization and visa sponsorship.
- China: Beijing, Shanghai, Guangzhou, Shenzhen; Hong Kong/Macau and overseas when eligible.
- Exclude outsourcing/staffing, pure sales with mandatory personal sales quotas, and clearly below-market compensation.
- Prioritize vacancies closing on or before 2026-10-31.
- Tailor each resume to JD language; include the user's approved headshot. Do not put resumes, photos, login sessions, tokens, personal contact data or application records in Git.

## Test design
Use three eligible, currently open postings for each workflow, ideally matched by application system and complexity. Avoid duplicate submissions to the same vacancy.
1. Discover and verify the official job page, application deadline, graduate eligibility, location, work authorization and current open status.
2. Classify job fit; reject hard-rule violations; record rationale.
3. Route a verified, role-specific resume PDF with approved photo; do not invent candidate facts.
4. Attempt form completion only in an authorized browser session; pause for login, CAPTCHA, 2FA and sensitive declarations.
5. Before any live submission, obtain authorization for the actual role; then capture success evidence (receipt, confirmation page or employer acknowledgment).
6. Record Submitted / Blocked / Needs user / Skipped / Pending. Count only confirmed submissions.

## Scorecard
| Metric | ApplyBuddy | ApplyPilot |
|---|---|---|
| Eligible open jobs discovered / 3 | Not tested | Not tested |
| Correct JD-based resume routing / 3 | Not tested | Not tested |
| Form fill completed / 3 | Not tested | Not tested |
| Confirmed submitted / 3 | Not tested | Not tested |
| Human interventions | Not tested | Not tested |
| Median minutes per confirmed submission | Not tested | Not tested |
| Failure modes and reproducible evidence | Not tested | Not tested |

## Initial static findings
- ApplyBuddy README: rule-driven eligibility, approved answers, resume routing, Codex-based filling and optional Simplify. Explicitly says real-world ATS/browser behavior has not been comprehensively tested.
- ApplyPilot SKILL.md: profile/dashboard, precision/volume routing, browser playbook, blocker triage, and explicit confirmation requirement. Trial mode defaults to discovery only.
- Existing Career Autopilot README: real application submission not yet proven; synthetic receipts are labeled simulation. Browser executor requires reviewed site adapters.

## Next gate
Before any live test, obtain a working authorized browser login and verified resume/photo artifacts. No current result should be described as a successful real application.
