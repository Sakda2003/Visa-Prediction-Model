# Integrated evidence assessment and ABA statement reader

The integrated application lives in `visa-platform/`. The original archive and sibling standalone reader were preserved. The source extraction excluded bundled private passports, bank PDFs, cached OCR/results, environment files, and private storage. `EXTRACTION.json` records the archive hash and exclusions.

## Run locally on Windows

Prerequisites: Python 3.12 or 3.13, Node.js compatible with the bundled frontend, and an internet connection for the first dependency/runtime installation. The tested environment uses Python 3.13 and Node.js 24.

```powershell
cd 'C:\Users\huths\OneDrive\Documents\ChatGPT\Bank Statement Reader\visa-platform'
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Open **http://127.0.0.1:8003**. The launcher prepares a virtual environment, dependencies, the production frontend, a verified portable MongoDB 8.0 runtime on loopback port 27018, the local OCR service on 8002, and the authenticated business/UI server on 8003. No Docker service or system MongoDB installer is required. First installation downloads software from its registries/vendor; documents are never included in those requests.

On an empty local user database, the first-run page lets **you** choose the administrator email and password. There are no default credentials. Setup requires `LOCAL_SETUP_ENABLED=true`, a loopback host/client, an allowed origin, and no existing users; an atomic claim prevents duplicate initial administrators. After setup, use the normal login and existing roles. Disable local setup in `.env` when deploying beyond the local workstation.

For an already prepared environment, `start.ps1 -SkipInstall` skips installation/build. For a separately managed MongoDB, set `.env` correctly and use `start.ps1 -ExternalMongo`. Existing `.env` values are preserved. Keep the launcher terminal open; Ctrl+C stops only processes it launched. If another process already owns a port, the launcher reuses it: stop outdated development servers before testing a new build.

## Agency workflow

1. Create a country/visa type and requirement definitions using Requirements Management. Select fixed versus dynamic mode. The points field is the maximum allocation; bank requirements use `ABA_STATEMENT_V1`, and other dynamic requirements need a versioned reviewed rubric.
2. Select that visa type in Evidence scoring profiles. Create a draft, set a unique version, adjust its complete 100-point allocation, and publish with a review reason. Publication validates the entire snapshot at once; it does not edit historical applications. No destination's official rules or assumed approval rate is seeded.
3. Create an application. Its effective published policy is pinned. Missing documents may remain a draft. An employment/business alternative group shares one allocation. Optional files do not alter the denominator.
4. Upload documents through the application checklist. ABA PDFs take the local reader path; other documents use the existing local OCR/verification service. Password-protected ABA PDFs support an optional password and password retry without reuploading.
5. Review evidence on the application results screen. Bank review requires exact reconciliation and an explicit account relationship. Select the reviewed analysis as primary bank evidence. Corrections/reviews change its revision, so a previously selected revision cannot continue awarding points silently.
6. Review non-bank dynamic evidence with the published rubric. Quality review records strength; document verification remains separate. Fixed evidence earns its allocation only after verification.
7. Refresh assessment and verification before finalization. An evidence-only report can be finalized with no active model if required evidence and staff review pass. A prediction report additionally requires an appropriate active model and current verified inputs.

## Score meanings

- Completion describes submitted or verified required evidence.
- Evidence-v3 strength is an exact, stable 100-point ledger. Fixed slots award their allocation after passing checks. Dynamic slots award `minimum + (maximum - minimum) × quality`. Missing/unknown/unverified evidence has zero **confirmed** points and unknown quality, with its reason and potential completion range shown.
- ABA statement strength remains the independently versioned 1–10 policy. Its country-independent historical score preserves average 35%, ending 20%, floor 15%, cash flow 15%, established 15%. Posted balances are not guaranteed spendable balances.
- The bank mapping uses the **final capped/rounded** reader score: `quality = (score - 1) / 9`. A 0–30 slot earns 15 points for score 5.5 and 20 for score 7.0. A null score never receives the configured minimum. The ledger sums exact contributions before rounding its displayed total.
- Visa approval probability is a separately calibrated outcome model. Evidence points are never relabeled as a chance of approval. It remains unavailable without sufficient verified inputs and a reviewed, approved, active, applicable model.

Legacy 0–100 financial and 50-point bank diagnostics retain their field names and units for historical/ML compatibility. They do not receive a second award in the new ledger. New ABA values use additive versioned fields, and new models require the matching feature schema.

## Migration and architecture

| Component | Authoritative implementation |
| --- | --- |
| Business API, auth, MongoDB and frontend serving | `backend/app/main.py` and existing routers |
| Immutable profiles and applicability | `services/policy_profiles.py`, `api/policy_profiles.py` |
| Canonical 100-point ledger | `services/evidence_scoring.py` |
| Legacy scoring | `document_metrics.py`, `readiness_scoring.py`; selected only for legacy application versions |
| Native/scanned ABA parsing and exact calculations | `services/aba_reader/` and `services/aba_integration.py` |
| Both document upload paths and audited bank review | `api/documents.py`, `api/bank_statements.py` |
| Verified prediction inputs and fingerprints | `services/assessment_context.py`, `api/verification_reports.py` |
| Training/calibration/registry lifecycle | Existing `ml_training.py`, `api/ml.py`, now including logistic regression |
| Local OCR including RapidOCR option | Existing `ai_service/app/ocr_engine.py` |

Use the dry-run draft-only migration in [EVIDENCE_PROFILE_MIGRATION.md](EVIDENCE_PROFILE_MIGRATION.md). No existing cases were migrated automatically. Rollback refuses modified or published drafts. Explicit reassessment preserves an audit/history entry rather than rewriting the original assessment.

Document replacement, correction, deletion, review changes, selected evidence, policy changes, and model/schema changes invalidate dependent predictions. Revision compare-and-swap and input fingerprints prevent stale workers/reports from replacing newer results. Finalization checks the latest verification and rechecks evidence after PDF generation. Changed evidence makes the current receipt historical; staff must refresh verification and provide a revision reason before issuing a new immutable receipt. Previous PDFs remain available through authorized downloads. Explicit policy reassessment preserves the old ledger and pinned snapshot alongside the new result; reconstructed comparisons are labeled as current-evidence reconstructions rather than historical facts.

## Added API contracts

- `/api/policy-profiles`: draft list/create/edit and immutable publication; `revision` is required for edits/publication. `/effective/visa-type/{id}` returns the effective published snapshot or null.
- `/api/policy-profiles/applications/{id}/reassess`: explicit policy selection and reason.
- `/api/policy-profiles/applications/{id}/review-requirement`: recorded rubric criterion scores or a level ID, source reason, selected document.
- `/api/bank-statements/documents/{id}/aba`: protected analysis in minor currency units. `/page/{n}?row={rowId}` returns a protected source image. `/corrections`, `/review`, `/select-primary` require current `analysisRevision` and a source-evidence reason. Mutation responses return the current revision.
- `/api/documents/upload`: optional `pdfPassword` is consumed locally and not persisted. OCR retry accepts optional JSON `pdfPassword`. Protected render copies of encrypted sources are deleted with their document.
- `/api/assessments/{id}/exports/json|csv|html`: case-scoped exports reuse the exact canonical report. CSV escapes spreadsheet formulas; HTML escapes source values. Private responses use no-store.
- `/api/verification/{id}/finalize`: `reportType` is `EVIDENCE_ASSESSMENT` or `PREDICTION`.
- Existing report prediction `approvalProbability` remains a **percentage** compatibility field. `approvalProbabilityNormalized` is explicitly 0–1. Unavailable/stale normalized values are null.

All source, review and bank mutations remain authorized staff operations. Customers can access their own case reports/exports through existing case-scoped authorization, not staff source-review endpoints.

## ML status and limits

No real outcome dataset or production model was supplied or invented. The training template contains headers only. Logistic regression joins the existing Random Forest/LightGBM options and uses regularization, group-separated holdout/calibration, measured probability metrics and cohort checks. Dataset floors, artifact integrity, review/approval/activation and rollback are retained. New ABA-schema models require documented Admin release criteria; a row count alone does not validate a model.

Country/category effects must be supported by evaluated data; unsupported cohorts return unavailable. Public application prediction uses server-built verified records, not arbitrary browser feature vectors. Declared income is separated from reviewed source-backed income. Explanations are tied to the model and do not pretend to be additive approval percentage points.

RapidOCR is a local CPU option using packaged models. Its Khmer transcription is limited; use the original PaddleOCR or configured Tesseract+Khmer option when validated for your documents. Poor scans, unsupported layouts and unsupported document types remain reviewable. ABA scoring is USD-only without an independently configured FX policy. Multi-account aggregation, automatic authenticity verification, and automatic official-country-rule updates are not implemented.

This is a local single-agency deployment, not a hardened public multi-tenant service. The prepared MongoDB is loopback-only without database authentication; `.env` has a random JWT secret. Network deployment requires a managed authenticated database, TLS, appropriate secrets, and agency backup/retention configuration. Case records/private uploads persist until authorized deletion; the standalone reader's 30-minute expiry is deliberately not applied. Private source data and credentials are excluded from packaged source.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m pytest ai_service\test_local_rapidocr.py ai_service\test_ocr_pipeline.py -q
npm --prefix frontend run build
```

`scripts/qa_integration.py` exercises real MongoDB, both local document paths, staff verification, primary-bank selection and protected exports. It refuses to seed any database whose name does not end in `_qa`. Its generated account/session stays under ignored `.runtime`; the real agency database is not seeded with QA people, profiles, or decisions. Browser QA uses this isolated synthetic workspace.


## Source distribution

Run `.\.venv\Scripts\python.exe scripts\package_source.py` to create `../output/Visa_Platform_Integrated.zip`. The bundle contains source, the built frontend, docs and synthetic test generators. It excludes `.env`, local databases, credentials, private uploads, OCR output, real PDFs, and model artifacts. `SOURCE_MANIFEST.json` records SHA-256 for every included file. It starts as a clean agency installation; local databases are not transferred.
