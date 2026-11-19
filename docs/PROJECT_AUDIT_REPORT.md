# VisaDoc AI Project Audit

> **Scope note:** This is a point-in-time audit. Its OCR and bank-statement
> findings describe the pre-upgrade state and are superseded by the
> [OCR upgrade report](OCR_UPGRADE_REPORT.md). The remaining findings have not
> been re-audited as part of that upgrade.

## Executive summary

The project is running as a React/Vite frontend, a Python FastAPI business
backend, and a second Python FastAPI OCR/AI service. It is not currently running
as the documented Express + FastAPI split. The active frontend defaults point to
the FastAPI services correctly, but the root README and root environment example
still describe conflicting ports and an Express API. That documentation drift
can send developers to the wrong service and cause avoidable network/404 errors.

The active OCR service still calls Ollama/Qwen for document analysis. At the time
of this audit its health endpoint reported both Ollama and Tesseract connected;
this is a live dependency, not an optional or removed integration. OCR currently
has deterministic extraction helpers, but it does not yet provide the requested
no-Ollama, fully structured, high-confidence document extraction workflow.

There is no configured or validated visa-outcome ML model, no model registry or
training pipeline, and no trustworthy approved/rejected training labels in the
observed application records. The existing prediction API correctly returns
HTTP 503 instead of inventing a probability. Training or returning visa
approval probabilities is not supportable from the current data.

No application code, database records, collections, indexes, or stored files
were modified during this audit. Database inspection and health checks were
read-only; tests used fake MongoDB collections.

## Scope and evidence

Inspected the active FastAPI application, API modules, frontend API clients,
configuration examples, package/dependency manifests, Docker configuration,
legacy backend modules, OCR code and tests, project documentation, MongoDB
collection/index metadata, and storage inventory. No passport/document contents,
credentials, password hashes, or tokens were read into this report.

Validation performed:

- MongoDB connection and collection/index metadata reads succeeded against the
  configured database.
- The live OCR service `/health` responded HTTP 200 and reported
  `ollama=connected`, `tesseract=connected`, and `ocr_language=eng+khm`.
- `python -m pytest backend\tests ai_service\test_ocr_fields.py -q`:
  **20 passed**, with one upstream Starlette deprecation warning.
- `npm --prefix frontend run build`: **passed**. Vite reported a generated
  JavaScript chunk larger than 500 kB; this is a build warning, not a failure.
- Tests use fake/in-memory MongoDB objects. They do not prove successful login
  against a real user record or exercise every live protected route.

The database was observed to have different counts in separate reads during
this shared-environment session. The counts below are a point-in-time snapshot,
not a stable baseline; take a fresh backup and recount before any database
mutation.

## Verified architecture

| Component | Active implementation | Notes |
|---|---|---|
| Frontend | React + Vite, served on port 5174 by the project scripts | Central API clients are in [`frontend/src/pages/pageUtils.js`](../frontend/src/pages/pageUtils.js). |
| Business API | FastAPI `backend.app.main:app`, normally port 8001 | Uses the existing MongoDB database and registers the business routers listed below. |
| OCR/AI API | FastAPI `ai_service.app.main:app`, normally port 8002 | Runs Tesseract/PyMuPDF/Pillow and currently calls Ollama/Qwen. |
| Database | MongoDB database `visadoc_ai` | Existing database and collections were preserved. |
| Legacy/conflicting backend | `backend/server.py`, `backend/src/`, and `backend/package.json` | `server.py` is a Python FastAPI proxy configured around port 5000; `backend/src/` contains older Python modules. They are not imported by the active `backend/app/main.py`. Express dependencies in a package manifest do not establish that an Express server is running. |

The active frontend API defaults are `http://127.0.0.1:8001` for the business
API and `http://127.0.0.1:8002` for the OCR/AI API. This agrees with the active
backend configuration. However, the root README and root `.env.example` describe
the business API as Express on port 5000 and/or place the AI API on port 8001.
The deployment examples and active source should be reconciled before changing
ports or service ownership.

The active backend creates an async MongoDB client during FastAPI startup,
selects the configured database, and pings MongoDB. Health reports degraded
status when the database ping fails; readiness returns HTTP 503. These behaviors
are covered by the focused backend tests.

## Active API surface

The active FastAPI application registers these route groups. Protected routes
use the existing JWT dependency; individual write routes also apply role
checks. This is a source inventory, not a claim that every route was live-tested.

| Prefix | Existing operations |
|---|---|
| `/api/auth` | Register, login, current principal |
| `/api/health` | Health and readiness |
| `/api/countries` | List, create, update, delete/deactivate |
| `/api/customers` | List, read, create, update, delete |
| `/api/dashboard` | Dashboard summary, statistics, recent applications |
| `/api/visa-types` | List/create types, active types, update/archive/duplicate/delete; list/create/delete applications |
| `/api/applications` | Application CRUD |
| `/api/requirements` | List, create, read, update, delete, history, visa-type requirements |
| `/api/documents` | List, upload, application documents, content, delete, review |
| `/api/verification` | List, run checks, update document verification |
| `/api/bank-statements` | Removed; bank statement uploads remain available through `/api/documents` |
| `/api/ocr` | Extract, passport, and visa OCR proxy operations |
| `/api/ai` | Prediction request and prediction lookup |
| `/api/reports` | Statistics, CSV, Excel |
| `/api/users` | User list and update |

## MongoDB snapshot and preservation notes

The read-only snapshot contained **9 collections and 122 documents**:

| Collection | Documents | Observed indexes (in addition to `_id`) |
|---|---:|---|
| `applications` | 0 | `created_at`, `application_id` |
| `countries` | 2 | `isoCode`, `active` |
| `customers` | 4 | `email`, `passportNumber` |
| `documents` | 13 | `applicationId`, `visaTypeId`, `requirementId`, `customerId` |
| `requirementversions` | 73 | `requirementId` |
| `users` | 2 | none |
| `visaapplications` | 9 | `applicationNumber`, `customer`, `visaTypeId`, `status` |
| `visarequirements` | 17 | `countryId`, `status`, compound `visaTypeId + name` |
| `visatypes` | 2 | `visaTypeCode`, `countryId`, `active` |

Observed `visaapplications.status` values were `DRAFT` (2),
`UNDER_VERIFICATION` (5), `FINAL_REVIEW` (1), and `FINAL_FAIL` (1).
`FINAL_FAIL` has not been verified as an authoritative visa-denial outcome.
There were no explicit `Approved`/`Rejected` labels in the observed records.
Consequently, these status values must not be used as ground-truth labels for
visa decisions without domain-owner confirmation.

The observed business-field indexes are non-unique. Before proposing any unique
indexes, check for duplicates and confirm the intended key semantics; do not
silently convert existing data or indexes.

The private-file inventory contained 34 PDFs under root `storage/private` and
13 PDFs under `backend/storage/private`. File names and contents were not read.
The database has 13 document records; the audit did not establish that every
root-level file is referenced by a current document record. Preserve both
directories until an explicit, verified retention/mapping plan exists.

A historical backup archive documented in the migration notes exists, but it
predates this snapshot and covers fewer collections/documents. It is not an
adequate rollback point for future migrations. No restore was attempted.

## OCR and AI readiness

### OCR

- The active service uses PyMuPDF for PDFs, Pillow preprocessing, and
  `pytesseract`; the live health endpoint found English and Khmer OCR data.
- The service enforces a configured file-size limit and a 30-page PDF limit.
- Deterministic field extraction primarily covers passport dates and selected
  bank-statement/fixed-deposit values. Much of the broader extraction is
  currently delegated to Qwen.
- `ai_service/mrz_parser.py` contains a separate MRZ parser, but the active OCR
  endpoint does not integrate it.
- The endpoint does not persist page-level OCR results itself. The main backend
  stores uploaded documents and coordinates OCR.
- An OCR error during PDF processing can discard partial extracted text. The
  API needs explicit partial-page/error reporting before being relied on for
  document-review workflows.
- The focused `ai_service/test_ocr_fields.py` tests helper functions duplicated
  in that test file; they do not exercise the active HTTP endpoint or prove
  full-document extraction accuracy.
- `opencv-python-headless` is declared but was not found in the active OCR
  implementation. Standalone/backup OCR scripts are not the active service.

### Ollama and prediction

- `ai_service/app/main.py` configures `OLLAMA_URL`, `QWEN_MODEL` (default
  `qwen3.5:4b`), and a long request timeout. `/health` checks Ollama, and
  `/ocr/extract` invokes Qwen for document analysis.
- The service's top-level health status can remain `"ok"` while its Ollama
  sub-status is offline or the model is missing. Health should distinguish OCR
  readiness from optional/external analysis dependencies.
- No trained visa model, model artifact, dataset management API/UI, registry,
  or training/evaluation pipeline was found in the active application. The
  active API requirements do not include a scikit-learn/LightGBM/XGBoost/SHAP
  prediction stack.
- `POST /api/ai/predict` deliberately returns **503** when no validated model
  exists. `GET /api/ai/predictions/{application_id}` returns **404** when no
  saved prediction exists. This is safer than a fabricated or misleading
  probability and should remain the behavior until validation gates pass.

## Findings and priority

1. **High - no authoritative training labels or adequate training dataset.**
   The observed nine visa applications have workflow statuses, not confirmed
   adjudication outcomes. Do not train or claim approval probabilities from
   these records.
2. **High - Ollama/Qwen is still a runtime dependency.** The current OCR
   analysis path does not meet the no-Ollama requirement, even though the
   service was healthy at audit time.
3. **High - conflicting architecture instructions.** Active source runs two
   FastAPI services, while README/root examples still imply an Express service
   on port 5000. The active UI client is consistent with the FastAPI ports.
4. **Medium - OCR coverage and failure reporting are incomplete.** MRZ parsing
   is disconnected, extraction is not comprehensively structured, and partial
   page results can be lost on errors.
5. **Medium - storage has two separate private PDF trees.** Their relationship
   to current MongoDB document records has not been verified; neither should be
   cleaned up as part of this work without an explicit mapping/retention review.
6. **Medium - indexes do not enforce uniqueness.** Any unique-index change
   requires a duplicate scan and a compatibility decision first.
7. **Low - frontend build size warning.** The current production build succeeds,
   but its main JavaScript chunk exceeds Vite's 500 kB advisory threshold.

## Proposed implementation plan (not yet started)

### Gate 0 - agree on data and decision safeguards

- Identify the lawful, authoritative source for final visa outcomes, the exact
  approved/denied/withdrawn/reversed label mapping, jurisdiction, and time
  period.
- Confirm consent, retention, access controls, permitted features, and any
  restrictions on sensitive attributes. Exclude workflow statuses such as
  `FINAL_FAIL` unless an authorized domain owner confirms their meaning.
- Require human review and make clear that any model output is decision support,
  not a guarantee or substitute for an immigration authority's decision.
- If an adequate, representative labeled dataset is unavailable, do not train
  or show approval probabilities. Keep the current truthful 503 response.

### Gate 1 - establish a safe baseline

- Take a fresh, encrypted backup of the current database and private-file
  stores; verify archive integrity and perform a restore rehearsal in an
  isolated environment.
- Record collection counts, indexes, schema samples without PII, and storage
  references. Do not alter production data in this stage.
- Reconcile the active FastAPI architecture, service ports, environment
  examples, startup commands, and documentation without introducing another
  backend or changing the database name.

### Gate 2 - remove the OCR LLM dependency

- Replace Qwen-dependent extraction with deterministic OCR parsing, integrated
  MRZ validation, field-level confidence/provenance, page-level results, and
  explicit manual-review/error states.
- Preserve the existing upload/storage and response contracts where possible;
  add compatibility tests before changing response shapes.
- Remove Ollama-only configuration and health coupling after the replacement
  passes OCR endpoint tests with Ollama unavailable.
- Evaluate OCR against consented, redacted fixtures; never use private live
  documents as test output or commit them to the repository.

### Gate 3 - only if the data gate passes, build and evaluate ML

- Create a versioned, access-controlled dataset and label audit. De-identify
  where permitted and document missingness, class balance, time/jurisdiction
  coverage, and leakage risks.
- Establish a reproducible baseline and time-aware, applicant-grouped
  evaluation. Report calibration, subgroup performance, uncertainty, and
  limitations; do not invent metrics or rely on accuracy alone.
- Add a versioned model artifact/registry and persist prediction inputs,
  explanation, model version, and review status only after schema and privacy
  review. Do not let predictions automatically approve or reject applications.
- Keep the API unavailable until the Admin review, approval, activation, and
  documented human-review controls are approved.

### Gate 4 - integration and release

- Add focused tests for OCR, data/model versioning, auth/roles, API contracts,
  failure behavior, and preservation of existing records.
- Validate the React clients against the documented business and AI service
  URLs; verify CORS and protected routes.
- Run a read-only/shadow evaluation first. Release only after review of
  evidence, rollback procedure, monitoring, and user-facing disclaimers.

## Approval needed before implementation

The technical audit is complete, but the outcome-label source and permissions
are unknown. Before any training/prediction behavior is implemented, provide
the authoritative outcome source and approved label mapping, and confirm the
human-review constraint. Until then, only the non-predictive OCR and
documentation/configuration work can be safely scoped; no model training or
approval probability should be generated.
