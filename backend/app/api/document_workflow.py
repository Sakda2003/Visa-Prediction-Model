from __future__ import annotations

from typing import Any


WORKFLOW_STATES = {
    "UPLOADED",
    "PROCESSING",
    "OCR_PENDING",
    "OCR_PROCESSING",
    "OCR_COMPLETED",
    "OCR_PARTIAL",
    "OCR_FAILED",
    "PENDING_VERIFICATION",
    "VERIFIED",
    "REJECTED",
    "NEEDS_REVIEW",
    "FAILED",
    # Read older records without rewriting their persisted statuses.
    "SUSPICIOUS",
    "INSUFFICIENT_DATA",
}
LEGACY_COMPLETED_OCR_STATES = {"SUCCESS", "MANUAL_REVIEW"}


def has_completed_ocr(document: dict[str, Any]) -> bool:
    ocr_status = str(document.get("ocrStatus") or "").upper()
    if ocr_status in LEGACY_COMPLETED_OCR_STATES:
        return True
    if ocr_status != "OCR_COMPLETED":
        return False
    try:
        total_pages = int(document.get("ocrTotalPages") or 0)
        processed_pages = int(document.get("ocrPagesProcessed") or 0)
        progress = float(document.get("ocrProgressPercent") or 0)
        page_numbers = sorted(
            int(page_number)
            for page_number in document.get("ocrPageNumbers") or []
        )
    except (TypeError, ValueError):
        return False
    return (
        total_pages > 0
        and processed_pages == total_pages
        and progress >= 100
        and page_numbers == list(range(1, total_pages + 1))
    )


def document_workflow_status(document: dict[str, Any]) -> str:
    ocr_status = str(document.get("ocrStatus") or "").upper()
    status = str(document.get("status") or "").upper()
    if status in {
        "VERIFIED",
        "REJECTED",
        "NEEDS_REVIEW",
        "INSUFFICIENT_DATA",
        "FAILED",
        "SUSPICIOUS",
    }:
        return status
    if status == "PROCESSING":
        return status
    if not has_completed_ocr(document) and ocr_status in {
        "OCR_PENDING",
        "OCR_PROCESSING",
        "OCR_PARTIAL",
        "OCR_FAILED",
    }:
        return ocr_status
    workflow_status = str(document.get("workflowStatus") or "").upper()
    if workflow_status in WORKFLOW_STATES:
        return workflow_status
    if has_completed_ocr(document):
        return "PENDING_VERIFICATION"
    if ocr_status in {
        "OCR_PENDING",
        "OCR_PROCESSING",
        "OCR_PARTIAL",
        "OCR_FAILED",
    }:
        return ocr_status
    return "UPLOADED"


def public_workflow_document(document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    workflow_status = document_workflow_status(document)
    result["workflowStatus"] = workflow_status
    if has_completed_ocr(document):
        result["ocrStatus"] = "OCR_COMPLETED"
    elif str(document.get("ocrStatus") or "").upper() == "NOT_REQUIRED":
        result["ocrStatus"] = "NOT_REQUIRED"
    elif str(document.get("ocrStatus") or "").upper() in {
        "OCR_PENDING",
        "OCR_PROCESSING",
        "OCR_PARTIAL",
        "OCR_FAILED",
    }:
        result["ocrStatus"] = str(document.get("ocrStatus")).upper()
    else:
        result["ocrStatus"] = workflow_status

    total_pages = int(document.get("ocrTotalPages") or 0)
    processed_pages = int(document.get("ocrPagesProcessed") or 0)
    progress = document.get("ocrProgressPercent")
    if progress is None:
        progress = round(100 * processed_pages / total_pages) if total_pages else 0
    result["ocrTotalPages"] = total_pages
    result["ocrPagesProcessed"] = processed_pages
    result["ocrProgressPercent"] = min(100, max(0, int(progress)))
    return result
