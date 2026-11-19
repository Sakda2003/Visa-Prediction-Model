# VISA Readiness Scoring

The report exposes `summary.readinessScore` from `backend/app/services/readiness_scoring.py`.
It is explicitly a **Readiness Score**, not an embassy approval probability.

The Bank Statement component contributes up to 45 points to this 100-point readiness score. The separate 50-point Bank Statement Quality Score is a diagnostic score for balance evidence, transaction history, source-of-funds patterns, and document completeness. Do not add the 50-point score to the 100-point readiness score, and do not interpret either score as visa approval probability or proof of authenticity.

## Weights

| Component | Maximum |
|---|---:|
| Passport | 15 |
| Photo | 5 |
| National ID | 10 |
| Bank statement / fixed deposit | 45 |
| Supporting documents | 15 |
| Cross-document identity consistency (name, date of birth, passport number, nationality where available) | 10 |
| **Total** | **100** |

Document quality uses existing workflow status: VERIFIED = 1.00, OCR-completed but not yet verified = 0.65, needs review/suspicious = 0.35, rejected/failed = 0.00. A required missing/rejected/failed document activates the gate. A passport expiring before the six-calendar-month threshold also activates the gate. Either gate caps the final score at 40 while preserving `rawScoreBeforeGate` for staff diagnosis.

## Bank statement features (45 points)

- Coverage: days with observed/carry-forward daily balance at least USD 4,000; full points at 90% coverage (12).
- Minimum and average daily balance: each capped against USD 4,000 (6 + 6).
- Recency: statement end date within 30 days (5).
- Period: at least 90 days (3).
- Pass-through: qualifying deposits of at least USD 1,000 followed by outflows of at least 90% within three days; lower ratio scores better (6).
- Source concentration: inverse of largest identified source share (3). This is a review signal, not proof of wrongdoing.
- Income regularity: number of months containing credits, capped at three (2).
- Savings rate: `(in - out) / in`, clamped to 0–1 (2).

USD-specific thresholds are not applied to other currencies without an independently verified exchange rate. Missing evidence is reported as unavailable and contributes no points. Fixed-deposit documents can contribute amount, issue-date and maturity evidence, but do not fabricate transaction history or daily-balance coverage.

## API and user interface

`GET /verification/results/{application_id}` includes the score under `summary.readinessScore`. The Verification Results page displays component points, gate reasons, bank feature breakdown and actionable recommendations. Existing bank diagnostics, ML prediction and workflow completeness metrics remain separate and unchanged.

## Important limitations

OCR confidence and balance reconciliation problems require human review. OCR, arithmetic consistency, duplicate-page checks and transaction patterns do not establish authenticity. Bank source concentration and pass-through flags are indicators for review, not fraud determinations. Encrypt bank data, avoid logging statement contents, restrict access and apply a retention/deletion policy before production use.

## Tests

Run from the repository root:

```bash
python -m pytest backend/tests/test_readiness_scoring.py -q
```

The broader backend test suite requires the dependencies in `backend/requirements.txt`.
