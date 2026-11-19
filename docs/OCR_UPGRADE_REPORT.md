# OCR Upgrade Report

## Summary

The OCR service now initializes PP-OCRv6 once during FastAPI startup and uses it
as the primary engine. It detects CPU/RAM and checks whether the installed
PaddlePaddle build can use an NVIDIA GPU; it does not install a GPU package.
Searchable PDF text is extracted directly, while scanned pages are rendered
with PyMuPDF at a configurable DPI. Tesseract remains available as a fallback
or can be selected as the primary engine for rollback.

The current CPU benchmark does **not** show a speed improvement over Tesseract.
No accuracy improvement is claimed: the sample has no ground-truth transcription
for a character- or field-level accuracy comparison.

## Windows Python 3.12 installation

From the project root in PowerShell:

```powershell
py -3.12 -m venv ai_service\.venv
.\ai_service\.venv\Scripts\python.exe -m pip install --upgrade pip
.\ai_service\.venv\Scripts\python.exe -m pip install -r ai_service\requirements.txt
.\ai_service\.venv\Scripts\python.exe -m pip install pytest
if (-not (Test-Path .\ai_service\.env)) {
    Copy-Item .\ai_service\.env.example .\ai_service\.env
}
```

Install the Windows Tesseract executable separately if fallback is enabled.
Set `TESSERACT_PATH` and `TESSERACT_LANG` in `ai_service\.env` for that
installation. Paddle's model files are downloaded into the user cache on first
startup; the first startup therefore needs internet access.

Start the OCR service:

```powershell
Set-Location .\ai_service
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

The default `paddlepaddle==3.3.1` dependency is CPU-only. This test machine had
an NVIDIA RTX 4050, but its installed PaddlePaddle reported no CUDA support, so
inference correctly used CPU. Install a GPU wheel only after independently
verifying that its Python, Windows, CUDA, and Paddle versions are compatible;
the application does not change packages automatically. OpenVINO was not
available or selected.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `OCR_PRIMARY_ENGINE` | `paddleocr` | `paddleocr` or `tesseract`; the latter is the rollback switch. |
| `OCR_DPI` | `180` | PDF render resolution. |
| `OCR_WORKERS` | `2` | Bounded page-render/Tesseract worker count. |
| `OCR_MODEL_SIZE` | `auto` | `tiny`, `small`, or automatic selection (`tiny` below 4 GB available RAM). |
| `OCR_DETECTION_LIMIT_SIDE_LEN` | `960` | Paddle text-detection resize limit; lower values trade detail for speed. |
| `OCR_MAX_IMAGE_SIDE` | `3200` | Maximum image side after rendering/decode. |
| `OCR_TESSERACT_FALLBACK` | `true` | Permit Tesseract when Paddle is unavailable or fails. |
| `OCR_PROCESS_TIMEOUT_SECONDS` | `300` | API processing timeout; cancellation is checked between page batches. |
| `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT` | `false` | Avoids the observed Paddle oneDNN/PIR inference failure on this CPU setup. |

Page rendering is bounded and concurrent, with a single Paddle model instance
used for inference. Paddle's CPU inference was not made faster by adding a
second model instance: the 13-page 896-side run took 119.250 s with two models
versus 117.328 s with one, while peak process RSS rose to about 747 MB. A running
Paddle inference call cannot be interrupted mid-call; cancellation and timeout
are cooperative at page-batch boundaries.

## Performance benchmark

Measured on the local `ai_service\bank_statement.pdf` sample:

- PDF: 7,808,752 bytes, 13 scanned pages, rendered at 180 DPI.
- CPU: AMD Ryzen 7 7435HS, 16 logical CPUs; system RAM: 16,203 MB.
- NVIDIA RTX 4050 was visible, but Paddle CUDA support was false.
- Paddle model: PP-OCRv6 tiny, PaddleOCR 3.7.0, CPU.
- Initial available RAM was about 1.7 GB, so automatic model selection chose
  tiny. The 960-side Paddle run reported about 509 MB process RSS.

| Run | Render time | OCR time | Total wall time | Mean confidence | Extracted characters |
| --- | ---: | ---: | ---: | ---: | ---: |
| Tesseract, 180 DPI, 2 workers | 4.659 s | 68.902 s | 73.561 s | 0.7412 | 80,708 |
| Paddle tiny, 180 DPI, 960 detector limit | 7.934 s | 245.192 s | 250.217 s | 0.9673 | 78,091 |
| Paddle tiny, 180 DPI, 896 detector limit (experiment) | 7.148 s | 117.328 s | 121.882 s | 0.9630 | 71,821 |

The 960-side Paddle run was approximately 3.4 times slower than Tesseract on
this CPU. The 896-side experiment was faster than 960 but still slower than
Tesseract and returned about 8% fewer characters than the 960-side Paddle run;
960 remains the default to avoid silently trading away that text detail.
Character counts and OCR confidence are not accuracy measures.

On page 1 at the same DPI and on the same rendered image, Tesseract took 6.990 s
(confidence 0.767, 5,050 characters); Paddle tiny took 15.366 s (confidence
0.972, 4,754 characters). These confidence values are engine-specific and not
directly comparable.

The small CPU model was evaluated on page 1 only: initialization took 129.802 s
including first-time model downloads, then inference took 30.096 s (confidence
0.988, 4,779 characters, about 428 MB process RSS). It was slower than tiny and
did not establish an accuracy improvement without ground truth. A full-PDF
small-model benchmark was not run.

Per-page Paddle times are the measured inference time of a two-page model call
divided evenly between its pages; they are not independent per-page timings.
The full-document run preserved page order and processed all 13 pages. No OCR
text, account numbers, or transaction values are included in this report.

## Validation and test coverage

Observed test results:

- OCR unit/API tests: **13 passed** (one Starlette/AnyIO deprecation warning).
- Backend tests: **21 passed** (same dependency warning).
- Frontend production build passed; Vite emitted its existing large-chunk
  advisory. Frontend lint exited successfully with warnings in untouched
  `DocumentPreview.jsx`, `AppPages.jsx`, and `RequirementsPage.jsx`.
- A real scanned 13-page PDF was processed by Paddle tiny and Tesseract at
  180 DPI; a searchable PDF and cancellation behavior are covered by synthetic
  tests.
- Transaction parsing and image upload/API response shape are tested with
  synthetic inputs. They do not constitute a real passport/visa/JPG/PNG or
  poor-quality-document OCR accuracy evaluation.

No ground-truth test fixtures were available for passports, visas, individual
JPG/PNG OCR quality, or poor scans. The 13-page file contains English/numeric
financial content, but its extracted values were not verified against a
trusted transcription. These remain manual-review workflows, not validated
financial recognition claims.

To run the focused tests:

```powershell
Set-Location .\ai_service
.\.venv\Scripts\python.exe -m pytest test_ocr_fields.py test_ocr_pipeline.py -q
```

## Response and data behavior

Existing OCR response fields remain available; page details, boxes, engine
metrics, and document-completeness information are additive. The document
verification score measures field completeness only. It is not visa approval
probability, fraud detection, or proof of authenticity. Raw OCR text is kept
for auditing, and incomplete or low-confidence information requires human
review. No MongoDB collections or records are deleted or reset by this upgrade.

Bank-statement transaction parsing is heuristic and layout-dependent. Missing
amounts are not fabricated, and transaction direction may remain unknown when
the statement does not make it explicit.

## Rollback

To use Tesseract as the primary engine without reverting code or changing
MongoDB data:

1. Install Tesseract and set a valid `TESSERACT_PATH` and `TESSERACT_LANG` in
   `ai_service\.env`.
2. Set `OCR_PRIMARY_ENGINE=tesseract`.
3. Restart the OCR service and verify `/health` reports `tesseract`.

To return to PaddleOCR, set `OCR_PRIMARY_ENGINE=paddleocr` and restart. For a
full source rollback, restore the pre-upgrade versions of the modified source,
dependency, and environment-example files from the project's source backup;
this repository directory is not a Git worktree, so no Git-based rollback is
available here. Back up source and MongoDB independently before deploying any
production change.
