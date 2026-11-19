# Release validation - 18 November 2026

- Backend: 264 tests passed; 8 existing LightGBM feature-name warnings.
- Local OCR: 33 tests passed.
- Frontend production build passed; existing large-bundle warning remains.
- Real local MongoDB/OCR/API smoke test: both application document paths, immutable profile, reviewed bank 5.5 -> 15/30, total 85/100, no fabricated probability.
- Explicit 60/40 policy reassessment preserved 85 points beside a new 80-point ledger. Reviewed SPONSOR ownership survived reload.
- Current receipt version 3 generated successfully. Its rendered three-page PDF shows 80/100, bank 5.5/10, 20/40 contribution and policy/source/audit versions.
- Both historical PDFs download through authorization. The prior version's SHA-256 remained unchanged.
- JSON/CSV/HTML exports return no-store and the canonical assessment.
- Desktop 1440, mobile 390 and actual 1280 widths checked. Finish reviewer scored all six reported fixes resolved, disposition ship, scoped to those fixes.
- Windows launcher syntax and local readiness passed. Python 3.13 fresh binary dependency resolution passed; a complete separate clean-environment installation was not performed.
- Agency database remains empty. First-run administrator setup is ready at http://127.0.0.1:8003. QA records and credentials stay separate and are excluded from distribution.

No real labeled visa-outcome dataset or validated production model was supplied. Approval probability remains unavailable. See INTEGRATION.md for deployment, OCR, currency and unsupported-case limits.
