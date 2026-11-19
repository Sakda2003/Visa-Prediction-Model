# ML and Bank Statement Upgrade Migration Plan

## Inspection findings

- The active application is React/Vite plus the existing FastAPI business API,
  OCR API, and MongoDB database. Keep those services and collections intact.
- Existing uploads validate file size, extension, and file signature; OCR is
  called through the configured AI service and document records are stored in
  MongoDB. Reuse those paths rather than creating a second application.
- `/api/bank-statements/analyze` exists and persists a result, but it currently
  returns document completeness, not a financial assessment. Existing
  `/api/ai/predict` correctly returns HTTP 503 when no validated visa model is
  available.
- The frontend already has a bank-statement review panel, authenticated API
  client, admin settings route, and role-based API dependencies.
- At initial inspection, no LightGBM, XGBoost, scikit-learn, or SHAP runtime
  dependency was configured. The local bank-analysis JSON cache has no
  visa-outcome label fields; these are OCR/result artifacts, not verified
  historical outcomes. Do not infer labels from application workflow statuses
  or train on these files.
- The prior project audit also found no verified adjudication labels. Production
  MongoDB was not queried or modified during this inspection.

## Incremental plan

1. **Add explainable non-ML analysis first.** Derive transaction aggregates,
   balance reconciliation, large/unexplained deposit alerts, and a configurable
   0–100 financial score. Unknown or inconsistent fields remain unknown and
   trigger review rather than fabricated values.
2. **Add a versioned internal rules configuration.** Expose authenticated read
   and Admin-only update APIs with change audit records. Keep visa requirements
   explicitly labeled as internal policy; an unconfigured China condition
   yields manual review.
3. **Add a guarded ML training/registry surface.** Accept CSV/XLSX in memory,
   require an Admin attestation of verified labels and source/mapping, reject
   inadequate/imbalanced or applicant-leaking data, evaluate grouped holdouts,
   calibrate the binary outcome model, persist model artifacts/metadata, and
   support activation/rollback. Do not train during this change because no
   authorized dataset was supplied.
4. **Preserve no-model behavior.** Prediction endpoints report
   `insufficient_data` or `unavailable` without returning a probability until a
   validated active model exists. Never treat a workflow status as an outcome.
5. **Integrate the existing UI and APIs.** Preserve document upload and
   application verification, mask account identifiers in public responses, and
   keep authentication and collections. The standalone statement-analysis
   panel is not part of application creation.
6. **Document dataset schema, APIs, thresholds, training and rollback.** Add a
   header-only dataset template; it contains no fabricated examples.
7. **Validate in layers.** Test financial calculations and rules, ML data
   validation/model lifecycle, API authorization/schema, existing backend
   routes, and frontend production build. Do not claim real-data ML metrics
   without a verified training/evaluation run.

## Safety and compatibility boundaries

- A model score is decision support only, not an embassy decision, legal
  recommendation, or guarantee. Human review remains mandatory.
- OCR confidence is not a visa probability; financial completeness/health is
  reported separately from the visa outcome model.
- Country thresholds supplied by the requester are internal defaults, not
  statements of official embassy policy. Currency conversion is not assumed.
- China-specific eligibility is not inferred where the requested special
  conditions are unspecified; Admin configuration and manual review are
  required.
- Dataset uploads are not persisted. Training artifacts and metadata must not
  contain source rows or direct personal identifiers. Do not mutate existing
  MongoDB collections or records as a migration shortcut.

## Implementation and verification status

- Implemented the financial analysis, reconciliation, large-deposit review,
  internal configurable rules and risk adjustments, model training/evaluation
  and registry APIs, account-number response masking, document ownership
  checks, and Admin UI integration. Application creation uses the existing
  document verification workflow without a separate statement-analysis panel.
  See
  `ML_BANK_STATEMENT_UPGRADE.md` for the supported API and operating controls.
- Added LightGBM, scikit-learn, pandas, NumPy, joblib, and Excel readers to the
  backend requirements. The model candidate training and inference code paths
  were exercised with test-only fixtures; those fixtures are not persisted or
  used as real outcomes.
- Backend suite: 41 passed. Frontend production build passed. Frontend lint
  exited successfully with existing warnings in `DocumentPreview.jsx`,
  `AppPages.jsx`, and `RequirementsPage.jsx`.
- No authorized historical visa outcome dataset was supplied. No production
  model was trained, activated, or used to produce an approval probability;
  no production ML metrics are claimed. MongoDB data was not queried, reset,
  or modified during implementation.
