# VisaDoc AI FastAPI Migration Plan

## Status

- Phase 1 (codebase audit and feature inventory): complete.
- Phase 2 (MongoDB backup and schema snapshot): complete; archive restore was verified in a separate temporary database.
- Phase 3 (FastAPI foundation): complete.
- Phase 4 (API compatibility): in progress; auth, role guard, country CRUD, customer CRUD, and legacy rate limits are implemented and fake-database tested. Demo admin/staff bootstrap and the remaining API domains are unresolved.
- Phases 5-10: not started.
- Express remains the active API. The frontend remains configured for `http://localhost:5000/api`.
- No existing API behavior was changed. The new FastAPI endpoints are additive, and readiness performs only a MongoDB ping.
- FastAPI liveness/readiness and `/docs` were smoke-tested locally; readiness successfully pinged MongoDB.

## Verified MongoDB backup

- Database: `visadoc_ai`, MongoDB server `8.3.4`.
- Archive: `C:\VisaDocBackups\visadoc_ai_20261110_185307.archive.gz` (9,149 bytes).
- SHA-256: `faf0cebf96ce5ba1e9e7acbdc7e4ca27e37bd6306d22cd74cbc8136e84727784`.
- MongoDB Database Tools `100.20.1` were installed locally from the official MongoDB download archive for backup/restore verification.
- Manifest with collection/index snapshot and restore checks: `C:\VisaDocBackups\visadoc_ai_20261110_185307.manifest.json`.
- Dump and restore logs: adjacent `.dump.log` and `.restore.log` files.
- The database contained 7 collections and 108 documents at the snapshot: `countries` (2), `customers` (4), `documents` (5), `requirementversions` (73), `visaapplications` (5), `visarequirements` (17), and `visatypes` (2).
- Restore check: all 7 collection names, document counts, and 26 index definitions matched the pre-dump snapshot. The original database's counts and indexes also remained unchanged during verification.
- Restore test data was removed from its uniquely named temporary database after successful comparison. The original `visadoc_ai` database was not modified.

## Current architecture

- Frontend: React/Vite application in `frontend/`; shared Axios client defaults to `http://localhost:5000/api`.
- Active API: Express in `backend/server.js`, port 5000 by default. Routes are protected by JWT middleware except public auth routes; document and user role checks are applied per route/controller.
- Operational store: controllers primarily use a process-global in-memory `__demoStore`. MongoDB persistence is opportunistic through Mongoose and `backend/src/db/persistence.js`.
- OCR service: separate FastAPI app in `ai_service/app/main.py`, port 8001. Express document upload calls its `/ocr/extract` endpoint using `AI_SERVICE_URL`.
- MongoDB: collections/models exist for countries, visa types, requirements, requirement versions, applications, customers, documents, and separate bank-analysis records. The persistence adapter does not include users, verifications, audit logs, or notifications.
- Existing automated tests: 16 Node workflow tests in `backend/test/document-workflow.test.js`. Python files named `test_ocr_fields.py`, `test_ocr.py`, and `test_ocr_backup.py` exist, but `test_ocr_fields.py` contains helper functions and no unittest/pytest test cases; a Python regression suite still needs to be established.
- Local MongoDB is listening on port 27017. `mongosh` and MongoDB Database Tools are installed locally. No database connection string or credentials were printed or copied.
- No Git repository metadata is available in this workspace, so Git-based migration rollback is unavailable here.

## Feature inventory and migration requirements

| Area | Existing implementation | Migration requirement / gap |
|---|---|---|
| Authentication | Express register/login/current-user endpoints; bcryptjs hashes; JWT; public registration creates CUSTOMER role | Preserve token claims, expiration, response shape, and role behavior. Existing user records are not persisted by the current Mongo adapter and no User model is present. Resolve user persistence before cutover. |
| Roles and access | `authenticateToken` and per-route `requireRole`; customer-specific filtering in some controllers | Port authorization consistently and add parity tests for ADMIN, STAFF, and CUSTOMER. |
| Dashboard | API aggregation from in-memory applications, customers, documents, verifications, and logs | Preserve response shape; define data ownership and durable source of verifications/logs before migration. |
| Customers | Express CRUD with duplicate checks and reference-protected deletion; FastAPI CRUD added | String IDs, duplicate checks, response envelopes, and application-reference protection covered by fake-database tests. No original-database writes performed. |
| Countries | Express CRUD, search/active filtering, deactivate-if-referenced behavior; FastAPI CRUD added | ObjectId IDs and collection names preserved; fake-database contract tests pass. No original-database writes performed. |
| Visa types | CRUD, filters, generated visa codes, duplicate/archive behavior | Preserve aliases and code generation. |
| Requirements | CRUD, list filters/order, archives, per-requirement upload settings, version history | Preserve requirement snapshots and existing historical records; maintain repeatable migrations. |
| Applications/checklist | Application list/create/delete; configured requirement snapshots; frontend calls requirements, documents, and verification APIs to load a checklist | Keep checklist loading independent of OCR. Preserve the existing React routes and API contract. |
| Documents | Private local upload; PDF/JPEG/PNG validation; authenticated streaming, removal, review; OCR candidate fields | Preserve private path checks, file limits/signatures, permissions, field formats, OCR fallback, and frontend behavior. |
| OCR / document intelligence | Python FastAPI `/ocr/extract`, Tesseract/PyMuPDF extraction, field fallback, optional Qwen via Ollama | Reuse or move this service into the new backend only after tests and bounded latency/resource behavior are verified. OCR output must remain a candidate, not an authenticity claim. |
| Verification/readiness | Rule-based passport, bank statement, and fixed-deposit checks; weighted document/form completeness; verification history | Port exact rules and response fields with regression tests. Current verification history is in memory and is not included in Mongo persistence. |
| Bank statement analysis | Separate CommonJS controller and Mongoose models; route file exists but is not mounted by `server.js`; it calls `/api/v1/analyze-statement`, which the current OCR FastAPI service does not expose | Treat as an inactive/legacy feature until its intended contract and database usage are reconciled. Do not silently claim it works. |
| Reports | `POST /api/reports/pdf` exists but returns HTTP 501 | Existing PDF report generation is not implemented; porting the placeholder does not satisfy report generation. |
| User/settings administration | Frontend pages/routes exist, but no user-management API is mounted in Express | Inventory and scope the actual UI actions before promising parity. Do not create a replacement that hides or removes the existing pages. |
| Audit logs/notifications | Referenced by dashboard/store shape but no persistent models/adapter entries found | Clarify event coverage and persistence semantics during implementation; do not imply durable audit history currently exists. |
| ML predictions/training | No current approval prediction endpoint, model, training/evaluation pipeline, or ML dependencies found | Not an existing feature to preserve. Only build after genuine, lawful labeled data is available; otherwise expose an explicit not-production-ready state and do not invent performance or probabilities. |

## Express-to-FastAPI endpoint mapping

All current paths below are under `/api`. Target paths retain the same URL and HTTP method unless a tested constraint requires otherwise. Existing duplicate aliases are kept for frontend compatibility.

| Existing Express endpoint | Proposed FastAPI endpoint | Status |
|---|---|---|
| `GET /api/health` | `GET /api/health` | Implemented and smoke-tested |
| `GET /api/ready` | `GET /api/ready` | Added for dependency readiness; read-only MongoDB ping |
| `POST /auth/register` | `POST /api/auth/register` | Implemented and fake-database tested |
| `POST /auth/login` | `POST /api/auth/login` | Implemented and fake-database tested; rate limiting remains |
| `GET /auth/me` | `GET /api/auth/me` | Implemented and fake-database tested |
| `GET /countries` | `GET /api/countries` | Implemented and fake-database tested |
| `POST /countries` | `POST /api/countries` | Implemented; ADMIN only |
| `PUT /countries/:id` | `PUT /api/countries/{id}` | Implemented; ADMIN only |
| `DELETE /countries/:id` | `DELETE /api/countries/{id}` | Implemented; ADMIN only |
| `GET /dashboard` | `GET /api/dashboard` | Planned |
| `GET /customers` | `GET /api/customers` | Implemented and fake-database tested |
| `POST /customers` | `POST /api/customers` | Implemented and fake-database tested |
| `PUT /customers/:id` | `PUT /api/customers/{id}` | Implemented and fake-database tested |
| `DELETE /customers/:id` | `DELETE /api/customers/{id}` | Implemented; reference-protection tested |
| `GET /visa-types`, `GET /visa-types/types` | same paths | Planned aliases |
| `POST /visa-types`, `POST /visa-types/types` | same paths | Planned aliases |
| `GET /visa-types/:id` | `GET /api/visa-types/{id}` | Planned |
| `PUT /visa-types/:id` | `PUT /api/visa-types/{id}` | Planned |
| `DELETE /visa-types/:id` | `DELETE /api/visa-types/{id}` | Planned |
| `POST /visa-types/:id/duplicate` | same path | Planned |
| `PATCH /visa-types/:id/archive` | same path | Planned |
| `GET /visa-types/requirements`, `GET /requirements` | same paths | Planned aliases |
| `POST /visa-types/requirements`, `POST /requirements` | same paths | Planned aliases |
| `GET /requirements/:id` | `GET /api/requirements/{id}` | Planned |
| `PUT /requirements/:id` | `PUT /api/requirements/{id}` | Planned |
| `DELETE /requirements/:id` | `DELETE /api/requirements/{id}` | Planned |
| `GET /requirements/:id/history` | same path | Planned |
| `GET /visa-types/applications` | same path | Planned |
| `POST /visa-types/applications` | same path | Planned |
| `DELETE /visa-types/applications/:id` | `DELETE /api/visa-types/applications/{id}` | Planned |
| `GET /documents` | `GET /api/documents` | Planned |
| `POST /documents/upload` | `POST /api/documents/upload` | Planned multipart upload |
| `GET /documents/:id/content` | `GET /api/documents/{id}/content` | Planned private streaming |
| `DELETE /documents/:id` | `DELETE /api/documents/{id}` | Planned |
| `PATCH /documents/:id/review` | `PATCH /api/documents/{id}/review` | Planned |
| `POST /verification/run` | `POST /api/verification/run` | Planned |
| `GET /verification` | `GET /api/verification` | Planned |
| `POST /reports/pdf` | `POST /api/reports/pdf` | Planned; current Express handler is 501 |
| Bank-statement router-local `POST /analyze` and `GET /:statementId/result` | Defer pending route prefix, contract, and data audit | Inactive: router is not mounted; controller expects an AI endpoint absent from current `ai_service` |

## Data preservation risks and safeguards

1. The current Mongo adapter's models omit `users`, `verifications`, `auditLogs`, and `notifications`; verify actual collections independently before determining whether data exists there.
2. IDs are mixed: several application entities use string `_id`s, while visa types/countries/requirements use ObjectIds. Existing relationship strings and BSON ObjectIds must be preserved exactly, not regenerated.
3. Current `loadMongoCollections` replaces a demo collection only when Mongo has rows; seed data is also persisted. The Python backend must not run the Express seed process or rewrite collections during cutover.
4. Existing backend migrations update requirements and Japan tourist requirements. They are not Python migration scripts and must not be rerun as part of the FastAPI startup.
5. Before any writes, take a verified MongoDB backup to a user-approved local, non-cloud-synced location, record collection counts/indexes, and test restore into a separate database.
6. No destructive migration, collection drop, automatic schema rewrite, frontend switch, or Express retirement until parity tests pass against a restored copy and cutover is explicitly approved.

## Staged implementation and acceptance gates

| Phase | Work | Exit criteria |
|---|---|---|
| 1. Audit | Feature inventory, endpoint map, schema/persistence risks | This document reviewed; unresolved inactive/placeholder features clearly marked |
| 2. Data safety | Backup, collection/index snapshot, restore rehearsal | Backup verified and separate-database restore succeeds; no production writes |
| 3. FastAPI foundation | Python backend package, configuration, logging, health, Mongo client/indexes, pytest setup | Complete: startup/shutdown and readiness tests pass; no seed writes or index changes |
| 4. API compatibility | Auth, roles, customers, countries, visa types, requirements, applications, dashboard, reports, user admin scope | Contract tests preserve frontend request/response behavior and authorization |
| 5. Document processing | Private upload/stream/delete/review and OCR adapter | PDF/image upload tests; OCR optional and checklist works while OCR is offline |
| 6. Verification/readiness | Port existing rule checks, scoring, snapshots, history | Existing Node workflow cases translated to pytest and match expected outputs |
| 7. Bank intelligence | Reconcile active feature contract; implement extraction/math checks only against verified requirements | Calculation/anomaly tests; no invented ML results |
| 8. Frontend integration | Keep React UI; point a test environment to FastAPI | Full route smoke test and browser workflow pass |
| 9. Regression/data validation | Run Python and frontend suites against restored copy | Endpoint parity, record/reference counts and upload access verified |
| 10. Cutover/retirement | Switch environment config; retain Express rollback path | User-approved cutover, monitored health, documented rollback; retire Express only later |

Phase 2 exit criteria are satisfied. Phase 3 is complete. Express remains active and the frontend still targets port 5000; FastAPI is an isolated parallel service on port 8000. FastAPI startup does not create/alter indexes or write operational data.

## Explicitly out of scope until prerequisites are met

- Visa approval probabilities, risk classification based on learned outcomes, or claims of model accuracy without verified, representative, lawful labeled data and independent evaluation.
- Automatic authenticity decisions from OCR, LLM output, or similarity scores.
- Deleting or transforming existing MongoDB records to fit a new schema.
- Claiming PDF reports, user management, notifications, or audit logs are implemented where the current API has only a placeholder or no route.
