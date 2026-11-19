from __future__ import annotations

import importlib.util
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pymupdf
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.main import (
    _build_ocr_response,
    app,
    extract_transactions,
    fallback_extract_fields,
    ocr_service,
)
from app.ocr_engine import OCRCancelledError, OCRDocument, OCRPage, OCRService


def _pdf_with_pages(text_pages: list[str]) -> bytes:
    document = pymupdf.open()
    for text in text_pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def _ready_service() -> OCRService:
    service = OCRService()
    service._initialized = True
    service._runtime.update(
        engine="paddleocr",
        model="PP-OCRv6_tiny",
        model_version="3.7.0",
        device="cpu",
        initialized=True,
    )
    return service


def test_ocr_service_can_be_constructed():
    assert isinstance(OCRService(), OCRService)


def test_ocr_page_and_batch_limits_are_configurable(monkeypatch):
    monkeypatch.setenv("OCR_MAX_PAGES", "75")
    monkeypatch.setenv("OCR_BATCH_SIZE", "6")

    service = OCRService()

    assert service.max_pdf_pages == 75
    assert service.batch_size == 6


def test_searchable_pdf_uses_embedded_text_without_ocr(monkeypatch):
    service = _ready_service()
    monkeypatch.setattr(
        service,
        "_recognize_batch",
        lambda _images: pytest.fail("searchable PDF should not run OCR"),
    )

    result = service.process_document(
        _pdf_with_pages(["A long searchable statement page with embedded text."]),
        "pdf",
    )

    assert result.pages[0].method == "embedded_text"
    assert result.pages[0].text.startswith("A long searchable")
    assert result.pages[0].confidence is None
    assert result.metrics["ocr_engine"] == "paddleocr"


def test_scanned_pdf_batches_pages_but_preserves_original_order(monkeypatch):
    service = _ready_service()
    calls = []
    progress = []

    def fake_recognize(images):
        calls.append(len(images))
        time.sleep(0.01)
        return [
            (f"recognized page {index + 1}", 0.9, [])
            for index in range(len(images))
        ]

    monkeypatch.setattr(service, "_recognize_batch", fake_recognize)
    result = service.process_document(
        _pdf_with_pages(["", ""]),
        "pdf",
        progress_callback=lambda processed, total: progress.append(
            (processed, total)
        ),
    )

    assert calls == [2]
    assert [page.page for page in result.pages] == [1, 2]
    assert [page.text for page in result.pages] == [
        "recognized page 1",
        "recognized page 2",
    ]
    assert all(page.ocr_seconds > 0 for page in result.pages)
    assert progress == [(0, 2), (1, 2), (2, 2)]


def test_configured_batch_size_bounds_scanned_pdf_memory(monkeypatch):
    service = _ready_service()
    service.batch_size = 4
    calls = []

    def fake_recognize(images):
        calls.append(len(images))
        return [
            (f"recognized batch page {index + 1}", 0.9, [])
            for index in range(len(images))
        ]

    monkeypatch.setattr(service, "_recognize_batch", fake_recognize)
    result = service.process_document(_pdf_with_pages([""] * 10), "pdf")

    assert calls == [4, 4, 2]
    assert [page.page for page in result.pages] == list(range(1, 11))


@pytest.mark.parametrize("page_count", [10, 30, 31, 100])
def test_pdf_page_limits_allow_every_page_through_default_maximum(page_count):
    service = _ready_service()
    result = service.process_document(
        _pdf_with_pages(
            [
                f"Searchable statement page {page_number} with embedded text."
                for page_number in range(1, page_count + 1)
            ]
        ),
        "pdf",
    )

    assert len(result.pages) == page_count
    assert [page.page for page in result.pages] == list(
        range(1, page_count + 1)
    )
    assert result.failed_pages == []


def test_pdf_page_limit_error_reports_detected_and_allowed_counts():
    service = _ready_service()
    service.max_pdf_pages = 30

    with pytest.raises(ValueError, match="31 pages.*maximum allowed is 30"):
        service.process_document(
            _pdf_with_pages([""] * 31),
            "pdf",
        )


def test_pdf_failure_keeps_successful_pages_and_reports_failed_page(monkeypatch):
    service = _ready_service()
    service.batch_size = 3
    monkeypatch.setattr(
        service,
        "_render_pdf_page",
        lambda _data, page_index: (np.asarray([page_index]), 0.01),
    )
    monkeypatch.setattr(
        service,
        "_recognize_batch",
        lambda _images: (_ for _ in ()).throw(RuntimeError("batch failed")),
    )

    def recognize_page(image):
        if int(image[0]) == 1:
            raise RuntimeError("page recognition failed")
        return f"page {int(image[0]) + 1}", 0.9, []

    monkeypatch.setattr(service, "_recognize", recognize_page)
    result = service.process_document(_pdf_with_pages(["", "", ""]), "pdf")

    assert [page.page for page in result.pages] == [1, 2, 3]
    assert [page.status for page in result.pages] == [
        "OCR_COMPLETED",
        "OCR_FAILED",
        "OCR_COMPLETED",
    ]
    assert result.failed_pages == [
        {"page": 2, "error": "page recognition failed"}
    ]
    assert result.metrics["status"] == "OCR_PARTIAL"


def test_multi_page_bank_statement_transactions_keep_source_page_numbers():
    service = _ready_service()
    result = service.process_document(
        _pdf_with_pages(
            [
                "2026-03-05 Salary +2,000.00 2,000.00",
                "2026-03-06 Rent -250.00 1,750.00",
            ]
        ),
        "pdf",
    )

    transactions = extract_transactions(result.text)

    assert [transaction["page"] for transaction in transactions] == ["1", "2"]
    assert [transaction["amount"] for transaction in transactions] == [
        "2000.00",
        "250.00",
    ]


def test_legacy_ocr_api_keeps_partial_pages_and_enforces_configured_limit(
    monkeypatch,
):
    paddle = ModuleType("paddle")
    paddle.__version__ = "test"
    paddle.device = SimpleNamespace(get_device=lambda: "cpu")
    paddle.is_compiled_with_cuda = lambda: False
    paddleocr = ModuleType("paddleocr")
    paddleocr.PaddleOCR = object
    monkeypatch.setitem(sys.modules, "paddle", paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", paddleocr)
    monkeypatch.setenv("OCR_MAX_PAGES", "100")
    monkeypatch.setenv("OCR_BATCH_SIZE", "2")

    module_path = Path(__file__).resolve().parents[1] / "ocr_api.py"
    spec = importlib.util.spec_from_file_location("legacy_ocr_api_test", module_path)
    assert spec and spec.loader
    legacy_ocr_api = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, legacy_ocr_api)
    spec.loader.exec_module(legacy_ocr_api)
    legacy_ocr_api.ocr = object()

    def fake_extract_image(image_path):
        page_number = int(Path(image_path).stem.split("_")[1])
        if page_number == 2:
            raise RuntimeError("page two OCR failed")
        return [{"text": f"page {page_number}", "confidence": 0.9}]

    monkeypatch.setattr(legacy_ocr_api, "extract_image", fake_extract_image)
    progress = []
    result = legacy_ocr_api.process_document(
        "statement.pdf",
        _pdf_with_pages(["", "", ""]),
        ".pdf",
        lambda processed, total, failures: progress.append(
            (processed, total, failures)
        ),
    )

    assert result["status"] == "partial"
    assert [page["page"] for page in result["pages"]] == [1, 2, 3]
    assert result["failed_pages"] == [
        {"page": 2, "error": "page two OCR failed"}
    ]
    assert progress[-1][0:2] == (3, 3)

    legacy_ocr_api.OCR_MAX_PAGES = 2
    with pytest.raises(ValueError, match="3 pages.*maximum is 2"):
        legacy_ocr_api.process_document(
            "statement.pdf",
            _pdf_with_pages(["", "", ""]),
            ".pdf",
        )


def test_cancellation_is_reported_before_processing():
    service = _ready_service()
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(OCRCancelledError):
        service.process_document(_pdf_with_pages([""]), "pdf", cancelled)


def test_tesseract_can_be_selected_as_primary_rollback(monkeypatch):
    monkeypatch.setenv("OCR_PRIMARY_ENGINE", "tesseract")
    service = OCRService()
    monkeypatch.setattr(
        service,
        "_detect_hardware",
        lambda: {"logical_cpu_count": 2, "available_ram_mb": 2048},
    )
    def prepare_tesseract():
        service._tesseract_ready = True

    monkeypatch.setattr(service, "_prepare_tesseract", prepare_tesseract)

    service.initialize()

    assert service.runtime_info["engine"] == "tesseract"
    assert service.runtime_info["device"] == "cpu"
    assert service.runtime_info["initialized"] is True


def test_gpu_model_initialization_retries_with_cpu(monkeypatch):
    paddle = ModuleType("paddle")
    paddle.is_compiled_with_cuda = lambda: True
    paddle.device = SimpleNamespace(cuda=SimpleNamespace(device_count=lambda: 1))
    paddleocr = ModuleType("paddleocr")
    paddleocr.PaddleOCR = object
    monkeypatch.setitem(sys.modules, "paddle", paddle)
    monkeypatch.setitem(sys.modules, "paddleocr", paddleocr)

    service = OCRService()
    service._hardware = {
        "nvidia_gpu_available": True,
        "logical_cpu_count": 8,
        "available_ram_mb": 8192,
    }
    monkeypatch.setattr(service, "_detect_hardware", lambda: service._hardware)
    monkeypatch.setattr(service, "_prepare_tesseract", lambda: None)
    attempts = []

    def create_model(_paddle_ocr_type, _model_size, device):
        attempts.append(device)
        if device == "gpu:0":
            raise RuntimeError("GPU model setup failed")
        service._model = object()

    monkeypatch.setattr(service, "_create_paddle_model", create_model)

    service.initialize()

    assert attempts == ["gpu:0", "cpu"]
    assert service.runtime_info["device"] == "cpu"
    assert "retried with CPU" in service.runtime_info["fallback_reason"]


def test_paddle_result_preserves_confidence_and_text_boxes():
    text, confidence, boxes = OCRService._parse_paddle_result(
        {
            "rec_texts": ["USD 1,250.50", "Closing Balance"],
            "rec_scores": np.array([0.98, 0.8]),
            "rec_polys": np.array(
                [
                    [[10, 20], [120, 20], [120, 40], [10, 40]],
                    [[10, 50], [130, 50], [130, 70], [10, 70]],
                ]
            ),
        }
    )

    assert text == "USD 1,250.50\nClosing Balance"
    assert confidence == 0.89
    assert boxes[0]["points"][0] == [10.0, 20.0]
    assert boxes[1]["text"] == "Closing Balance"


def test_transactions_keep_unknown_direction_and_explicit_running_balance():
    transactions = extract_transactions(
        "--- PAGE 2 ---\n"
        "2026-03-05 Transfer 1,250.00 4,800.50\n"
        "2026-03-06 Salary +2,000.00 6,800.50"
    )

    assert transactions[0]["amount"] == "1250.00"
    assert transactions[0]["type"] == "unknown"
    assert transactions[0]["balance"] == "4800.50"
    assert transactions[0]["page"] == "2"
    assert transactions[1]["type"] == "credit"
    assert transactions[1]["amount"] == "2000.00"


def test_transaction_parser_uses_labeled_debit_credit_columns_and_keeps_source():
    transactions = extract_transactions(
        "--- PAGE 7 ---\n"
        "2026-07-01 | Transfer | Debit 30.00 | Credit 0.00 | Balance 4,026.00"
    )

    assert len(transactions) == 1
    assert transactions[0]["description"] == "Transfer"
    assert transactions[0]["amount"] == "30.00"
    assert transactions[0]["type"] == "debit"
    assert transactions[0]["balance"] == "4026.00"
    assert transactions[0]["page"] == "7"
    assert transactions[0]["source_text"].endswith("Balance 4,026.00")


def test_transaction_parser_does_not_treat_a_labeled_balance_as_transaction_amount():
    transactions = extract_transactions(
        "2026-07-01 | Closing balance | Balance 4,026.00"
    )

    assert transactions == []


def test_bank_statement_fields_do_not_invent_missing_financial_values():
    fields = fallback_extract_fields(
        "Bank Name: Example Bank\n"
        "Account Holder: Dara Sample\n"
        "Account Number: 12345678\n"
        "Currency: USD\n"
        "Opening Balance: USD 1,000.00",
        "bank_statement",
    )

    assert fields["bankName"] == "Example Bank"
    assert fields["accountHolderName"] == "Dara Sample"
    assert fields["accountNumber"] == "12345678"
    assert fields["currency"] == "USD"
    assert fields["openingBalance"] == "1000.00"
    assert "closingBalance" not in fields
    assert "withdrawals" not in fields


@pytest.mark.parametrize(
    ("filename", "file_type", "requirement_code"),
    [
        ("national-id.jpg", "jpg", "National ID"),
        ("national-id.png", "png", "Identity Card"),
        ("national-id.pdf", "pdf", "National ID Card"),
    ],
)
def test_national_id_fields_extract_from_supported_image_and_pdf_formats(
    filename,
    file_type,
    requirement_code,
    monkeypatch,
):
    text = (
        "NATIONAL ID CARD\n"
        "National ID Number: KH-123456789\n"
        "Full Name: Ada Example\n"
        "Date of Birth: 1990-04-03\n"
        "Nationality: Cambodian\n"
        "Gender: Female\n"
        "Address: 10 Example Street"
    )
    service = _ready_service()
    if file_type == "pdf":
        data = _pdf_with_pages(
            [
                "NATIONAL ID CARD\nNational ID Number: KH-123456789",
                "Full Name: Ada Example\n"
                "Date of Birth: 1990-04-03\n"
                "Nationality: Cambodian\n"
                "Gender: Female\n"
                "Address: 10 Example Street",
            ]
        )
    else:
        image_buffer = BytesIO()
        Image.new("RGB", (800, 500), "white").save(
            image_buffer,
            format="JPEG" if file_type == "jpg" else "PNG",
        )
        data = image_buffer.getvalue()
        monkeypatch.setattr(
            service,
            "_recognize",
            lambda _image: (text, 0.94, []),
        )

    processed = service.process_document(data, file_type)
    response = _build_ocr_response(filename, requirement_code, processed)

    assert response.document_type == "national_id"
    assert response.fields["nationalIdNumber"] == "KH123456789"
    assert response.fields["fullName"] == "Ada Example"
    assert response.fields["dateOfBirth"] == "1990-04-03"
    assert response.fields["nationality"] == "Cambodian"
    assert response.missing_fields == []
    assert response.ai_analysis["authenticity_verified"] is False
    assert response.metrics["page_count"] == (2 if file_type == "pdf" else 1)


def test_national_id_ocr_reports_required_fields_that_could_not_be_extracted():
    response = _build_ocr_response(
        "national-id.png",
        "national_id",
        OCRDocument(
            pages=[
                OCRPage(
                    page=1,
                    text="NATIONAL ID CARD\n"
                    "Full Name: Ada Example\n"
                    "Date of Birth: 1990-04-03",
                    confidence=0.93,
                )
            ],
            metrics={},
        ),
    )

    assert response.document_type == "national_id"
    assert response.missing_fields == ["nationalIdNumber"]
    assert response.status == "manual_review"


def test_ocr_endpoint_adds_page_metrics_without_removing_legacy_fields(monkeypatch):
    from app.ocr_engine import OCRDocument

    def initialize_test_runtime():
        ocr_service._runtime.update(
            engine="paddleocr",
            model="PP-OCRv6_tiny",
            model_version="3.7.0",
            device="cpu",
            fallback_reason=None,
            initialized=True,
        )
        ocr_service._hardware = {"paddle_cuda_supported": False}
        ocr_service._initialized = True

    def fake_process(_data, _file_type, _cancellation_event):
        return OCRDocument(
            pages=[
                OCRPage(
                    page=1,
                    text="Account Holder: Dara Sample\nClosing Balance: USD 1,250.50",
                    confidence=0.93,
                    boxes=[
                        {
                            "text": "Account Holder: Dara Sample",
                            "confidence": 0.93,
                            "points": [[1, 2], [3, 2], [3, 4], [1, 4]],
                        }
                    ],
                    ocr_seconds=0.2,
                )
            ],
            metrics={
                "pdf_render_seconds": 0.1,
                "ocr_seconds": 0.2,
                "total_processing_seconds": 0.3,
                "cpu_utilization_percent": 5.0,
                "ram_usage_mb": 256.0,
                "inference_device": "cpu",
                "ocr_engine": "paddleocr",
                "ocr_model": "PP-OCRv6_tiny",
                "ocr_model_version": "3.7.0",
                "hardware": {},
            },
        )

    monkeypatch.setattr(ocr_service, "initialize", initialize_test_runtime)
    monkeypatch.setattr(ocr_service, "process_document", fake_process)
    with TestClient(app) as client:
        response = client.post(
            "/ocr/extract",
            files={"file": ("statement.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
            data={"requirement_code": "bank_statement"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["filename"] == "statement.png"
    assert payload["extracted_text"].startswith("--- PAGE 1 ---")
    assert payload["confidence"] == 0.93
    assert payload["pages"][0]["boxes"][0]["text"] == "Account Holder: Dara Sample"
    assert payload["metrics"]["ocr_engine"] == "paddleocr"
    assert payload["document_verification_score"] is not None
    assert "visa approval probability" in payload["document_verification_score_basis"]


def test_ocr_endpoint_accepts_a_national_id_alias(monkeypatch):
    def initialize_test_runtime():
        ocr_service._runtime.update(
            engine="paddleocr",
            model="PP-OCRv6_tiny",
            model_version="3.7.0",
            device="cpu",
            initialized=True,
        )
        ocr_service._initialized = True

    def fake_process(_data, _file_type, _cancellation_event):
        return OCRDocument(
            pages=[
                OCRPage(
                    page=1,
                    text="NATIONAL ID CARD\n"
                    "National ID Number: KH123456789\n"
                    "Full Name: Ada Example\n"
                    "Date of Birth: 1990-04-03",
                    confidence=0.93,
                )
            ],
            metrics={},
        )

    image_buffer = BytesIO()
    Image.new("RGB", (800, 500), "white").save(image_buffer, format="PNG")
    monkeypatch.setattr(ocr_service, "initialize", initialize_test_runtime)
    monkeypatch.setattr(ocr_service, "process_document", fake_process)

    with TestClient(app) as client:
        response = client.post(
            "/ocr/extract",
            files={
                "file": (
                    "national-id.png",
                    image_buffer.getvalue(),
                    "image/png",
                )
            },
            data={"requirement_code": "National ID Card"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_type"] == "national_id"
    assert payload["fields"]["nationalIdNumber"] == "KH123456789"
    assert payload["missing_fields"] == []


def test_ocr_job_reports_page_progress_and_reuses_idempotency_key(monkeypatch):
    def fake_process(
        _data,
        _file_type,
        _cancellation_event,
        progress_callback,
    ):
        progress_callback(0, 1)
        time.sleep(0.02)
        progress_callback(1, 1)
        return OCRDocument(
            pages=[OCRPage(page=1, text="Readable document text", confidence=0.9)],
            metrics={"page_count": 1},
        )

    monkeypatch.setattr(ocr_service, "process_document", fake_process)
    with TestClient(app) as client:
        started = client.post(
            "/ocr/jobs",
            files={"file": ("document.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
            headers={"Idempotency-Key": "document-ocr-test"},
        )
        repeated = client.post(
            "/ocr/jobs",
            files={"file": ("document.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
            headers={"Idempotency-Key": "document-ocr-test"},
        )
        job_id = started.json()["job"]["id"]
        assert repeated.json()["job"]["id"] == job_id
        for _ in range(100):
            current = client.get(f"/ocr/jobs/{job_id}").json()["job"]
            if current["status"] in {"OCR_COMPLETED", "OCR_FAILED"}:
                break
            time.sleep(0.01)

    assert current["status"] == "OCR_COMPLETED"
    assert current["pagesProcessed"] == 1
    assert current["totalPages"] == 1
    assert current["progressPercent"] == 100
    assert current["message"] == "OCR completed. Ready for verification."
    assert len(current["result"]["pages"]) == 1


def test_ocr_job_polling_reports_live_batch_progress(monkeypatch):
    entered_batch = threading.Event()
    release_batch = threading.Event()

    def fake_process(
        _data,
        _file_type,
        _cancellation_event,
        progress_callback,
    ):
        progress_callback(0, 10)
        progress_callback(2, 10)
        entered_batch.set()
        release_batch.wait(timeout=5)
        progress_callback(10, 10)
        return OCRDocument(
            pages=[
                OCRPage(page=number, text=f"Page {number}", confidence=0.9)
                for number in range(1, 11)
            ],
            metrics={"page_count": 10, "pages_processed": 10},
        )

    monkeypatch.setattr(ocr_service, "process_document", fake_process)
    with TestClient(app) as client:
        started = client.post(
            "/ocr/jobs",
            files={"file": ("document.pdf", b"%PDF-1.7\ncontent", "application/pdf")},
            headers={"Idempotency-Key": "batch-polling-test"},
        )
        job_id = started.json()["job"]["id"]
        try:
            assert entered_batch.wait(timeout=2)
            current = client.get(f"/ocr/jobs/{job_id}").json()["job"]
        finally:
            release_batch.set()
        for _ in range(100):
            current = client.get(f"/ocr/jobs/{job_id}").json()["job"]
            if current["status"] in {
                "OCR_COMPLETED",
                "OCR_PARTIAL",
                "OCR_FAILED",
            }:
                break
            time.sleep(0.01)

    assert current["status"] == "OCR_COMPLETED"
    assert current["pagesProcessed"] == 10
    assert current["totalPages"] == 10
    assert current["progressPercent"] == 100


def test_ocr_job_preserves_successful_pages_when_one_page_fails(monkeypatch):
    def fake_process(
        _data,
        _file_type,
        _cancellation_event,
        progress_callback,
    ):
        progress_callback(0, 2)
        progress_callback(2, 2)
        failed_page = OCRPage(
            page=2,
            status="OCR_FAILED",
            error="Page could not be read.",
        )
        return OCRDocument(
            pages=[
                OCRPage(page=1, text="Readable page", confidence=0.9),
                failed_page,
            ],
            metrics={
                "page_count": 2,
                "pages_processed": 2,
                "status": "OCR_PARTIAL",
            },
            failed_pages=[
                {"page": 2, "error": "Page could not be read."}
            ],
        )

    monkeypatch.setattr(ocr_service, "process_document", fake_process)
    with TestClient(app) as client:
        started = client.post(
            "/ocr/jobs",
            files={"file": ("document.pdf", b"%PDF-1.7\ncontent", "application/pdf")},
            headers={"Idempotency-Key": "partial-page-test"},
        )
        job_id = started.json()["job"]["id"]
        for _ in range(100):
            current = client.get(f"/ocr/jobs/{job_id}").json()["job"]
            if current["status"] in {
                "OCR_COMPLETED",
                "OCR_PARTIAL",
                "OCR_FAILED",
            }:
                break
            time.sleep(0.01)

    assert current["status"] == "OCR_PARTIAL"
    assert current["pagesProcessed"] == 2
    assert current["progressPercent"] == 100
    assert current["failedPages"] == [
        {"page": 2, "error": "Page could not be read."}
    ]
    assert current["result"]["pages"][0]["text"] == "Readable page"
    assert current["error"]["code"] == "OCR_PARTIAL"


def test_ocr_job_reports_clear_page_limit_error(monkeypatch):
    from app.ocr_engine import PDFPageLimitError

    def raise_limit_error(
        _data,
        _file_type,
        _cancellation_event,
        progress_callback,
    ):
        progress_callback(0, 101)
        raise PDFPageLimitError(101, 100)

    monkeypatch.setattr(
        ocr_service,
        "process_document",
        raise_limit_error,
    )
    with TestClient(app) as client:
        started = client.post(
            "/ocr/jobs",
            files={"file": ("document.pdf", b"%PDF-1.7\ncontent", "application/pdf")},
            headers={"Idempotency-Key": "page-limit-job-test"},
        )
        job_id = started.json()["job"]["id"]
        for _ in range(100):
            current = client.get(f"/ocr/jobs/{job_id}").json()["job"]
            if current["status"] == "OCR_FAILED":
                break
            time.sleep(0.01)

    assert current["status"] == "OCR_FAILED"
    assert current["totalPages"] == 101
    assert current["error"]["code"] == "PDF_PAGE_LIMIT_EXCEEDED"
    assert current["error"]["totalPages"] == 101
    assert current["error"]["maximumAllowedPages"] == 100
    assert "101 pages" in current["error"]["message"]


def test_extract_endpoint_returns_total_and_maximum_for_page_limit(
    monkeypatch,
):
    def initialize_test_runtime():
        ocr_service._runtime.update(
            engine="paddleocr",
            model="PP-OCRv6_tiny",
            model_version="3.7.0",
            device="cpu",
            initialized=True,
        )
        ocr_service._initialized = True

    monkeypatch.setattr(ocr_service, "initialize", initialize_test_runtime)
    monkeypatch.setattr(ocr_service, "max_pdf_pages", 1)
    with TestClient(app) as client:
        response = client.post(
            "/ocr/extract",
            files={
                "file": (
                    "two-pages.pdf",
                    _pdf_with_pages(["", ""]),
                    "application/pdf",
                )
            },
        )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "2 pages" in detail
    assert "maximum allowed is 1" in detail
    assert "split it into smaller files" in detail


def test_ocr_job_fails_when_a_pdf_page_result_is_missing(monkeypatch):
    def fake_process(
        _data,
        _file_type,
        _cancellation_event,
        progress_callback,
    ):
        progress_callback(0, 2)
        progress_callback(2, 2)
        return OCRDocument(
            pages=[OCRPage(page=1, text="Only one page", confidence=0.9)],
            metrics={"page_count": 1},
        )

    monkeypatch.setattr(ocr_service, "process_document", fake_process)
    with TestClient(app) as client:
        started = client.post(
            "/ocr/jobs",
            files={"file": ("document.pdf", b"%PDF-1.7\ncontent", "application/pdf")},
            headers={"Idempotency-Key": "incomplete-pages-test"},
        )
        job_id = started.json()["job"]["id"]
        for _ in range(100):
            current = client.get(f"/ocr/jobs/{job_id}").json()["job"]
            if current["status"] in {"OCR_COMPLETED", "OCR_FAILED"}:
                break
            time.sleep(0.01)

    assert current["status"] == "OCR_FAILED"
    assert current["message"] == "Document reading failed. Please retry."
    assert current["error"]["code"] == "OCR_PROCESSING_ERROR"
    assert "every document page" in current["error"]["message"]


def test_health_preserves_legacy_status_and_reports_runtime_health():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded"}
    assert payload["health_status"] in {"healthy", "degraded"}
    assert payload["ocr_engine"] == payload["provider"]
    assert isinstance(payload["gpu_available"], bool)
    assert isinstance(payload["gpu_detected"], bool)
    assert isinstance(payload["gpu_initialized"], bool)
    assert isinstance(payload["fallback_available"], bool)
    assert payload["max_pdf_pages"] == ocr_service.max_pdf_pages
    assert payload["ocr_batch_size"] == ocr_service.batch_size
