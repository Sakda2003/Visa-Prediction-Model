from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..api.document_workflow import has_completed_ocr
from .photo_validation import is_photo_document

_INACTIVE_DOCUMENT_STATES = {"DELETED", "REPLACED"}
_SCORABLE_STATUSES = {"VERIFIED", "PARTIAL", "NEEDS_REVIEW", "INSUFFICIENT_DATA"}


def bank_requirement_points(
    analysis: dict[str, Any] | None,
    configured_points: float,
    document_status: str = "VERIFIED",
) -> float:
    """Map a diagnostic bank score only when coverage/evidence is reliable."""
    if document_status.upper() != "VERIFIED" or not isinstance(analysis, dict):
        return 0.0
    if analysis.get("financialRequirementStatus") != "PASS":
        return 0.0
    diagnostic = analysis.get("bankStatementScore") or {}
    if not diagnostic.get("available"):
        return 0.0
    try:
        points = float(diagnostic["points"])
        maximum = float(diagnostic["maximum"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if maximum <= 0:
        return 0.0
    return round(min(configured_points, max(0.0, configured_points * points / maximum)), 2)


def _points(requirement: dict[str, Any]) -> float:
    try:
        return max(0.0, float(requirement.get("weight", requirement.get("points", 0)) or 0))
    except (TypeError, ValueError):
        return 0.0


def _document_points(document: dict[str, Any], maximum: float) -> float:
    # awardedPoints is written only after evidence-based verification/review.
    try:
        return min(maximum, max(0.0, float(document.get("awardedPoints") or 0)))
    except (TypeError, ValueError):
        return 0.0


def _requirement_id(requirement: dict[str, Any]) -> str:
    return str(requirement.get("requirementId") or requirement.get("_id") or "")


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _status(documents: list[dict[str, Any]]) -> str:
    if not documents:
        return "NOT_UPLOADED"
    statuses = {str(item.get("status") or "PROCESSING").upper() for item in documents}
    if "VERIFIED" in statuses:
        return "VERIFIED"
    if "PARTIAL" in statuses:
        return "PARTIAL"
    if statuses <= {"REJECTED"}:
        return "REJECTED"
    if statuses & {"NEEDS_REVIEW", "SUSPICIOUS"}:
        return "NEEDS_REVIEW"
    if statuses & {"INSUFFICIENT_DATA", "FAILED"}:
        return "INSUFFICIENT_DATA"
    return "PROCESSING"


def summarize_documents(
    requirements: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    not_applicable_requirement_ids: set[str] | None = None,
    *,
    include_optional: bool = False,
    calculated_at: datetime | None = None,
) -> dict[str, Any]:
    """Calculate document points from configured requirements only.

    Optional requirements are excluded by the explicit default denominator policy;
    callers can pass include_optional=True when their policy includes them.
    """
    excluded = {str(item) for item in (not_applicable_requirement_ids or set())}
    current = [item for item in documents if str(item.get("status") or "").upper() not in _INACTIVE_DOCUMENT_STATES]
    by_requirement: dict[str, list[dict[str, Any]]] = {}
    for document in current:
        by_requirement.setdefault(str(document.get("requirementId") or document.get("requirement") or ""), []).append(document)

    requirement_scores: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    total_points = earned_points = 0.0
    completed_required = submitted_required = required_count = 0
    for requirement in requirements:
        requirement_id = _requirement_id(requirement)
        if not requirement_id or requirement_id in excluded:
            continue
        required = bool(requirement.get("required", True))
        maximum = _points(requirement)
        attached = by_requirement.get(requirement_id, [])
        if not requirement.get("allowMultiple", False) and attached:
            attached = sorted(attached, key=lambda item: item.get("updatedAt") or item.get("createdAt") or "", reverse=True)[:1]
        # Option B: required requirements always contribute. An optional
        # requirement contributes only after an active file exists; a rejected
        # active file therefore remains in the denominator until deleted.
        included = required or bool(attached) or include_optional
        status = _status(attached)
        awarded = min(maximum, sum(_document_points(item, maximum) for item in attached if str(item.get("status") or "").upper() in _SCORABLE_STATUSES))
        if included:
            total_points += maximum
            earned_points += awarded
        if required:
            required_count += 1
            if attached:
                submitted_required += 1
            if status == "VERIFIED" and any(
                str(item.get("status") or "").upper() == "VERIFIED"
                and (
                    is_photo_document(item, requirement)
                    or has_completed_ocr(item)
                )
                for item in attached
            ):
                completed_required += 1
            if not attached:
                missing.append({"requirementId": requirement_id, "name": requirement.get("name") or "Required document"})
        requirement_scores.append({
            "requirementId": requirement_id,
            "name": requirement.get("name") or "Requirement",
            "required": required,
            "optional": not required,
            "allowMultiple": bool(requirement.get("allowMultiple", False)),
            "configuredPoints": maximum,
            "earnedPoints": round(awarded, 2),
            "status": status,
            "documentIds": [str(item.get("_id")) for item in attached],
        })

    statuses = [str(item.get("status") or "").upper() for item in current]
    requirement_by_id = {
        _requirement_id(item): item
        for item in requirements
        if _requirement_id(item)
    }
    processing = sum(
        status == "PROCESSING"
        or (
            not is_photo_document(
                item,
                requirement_by_id.get(
                    str(item.get("requirementId") or item.get("requirement") or "")
                ),
            )
            and str(item.get("ocrStatus") or "").upper()
            in {"OCR_PENDING", "OCR_PROCESSING"}
        )
        for item, status in zip(current, statuses)
    )
    rejected = sum(status == "REJECTED" for status in statuses)
    needs_review = sum(status in {"NEEDS_REVIEW", "SUSPICIOUS"} for status in statuses)
    insufficient = sum(status == "INSUFFICIENT_DATA" for status in statuses)
    failed = sum(
        status == "FAILED"
        or (
            not is_photo_document(
                item,
                requirement_by_id.get(
                    str(item.get("requirementId") or item.get("requirement") or "")
                ),
            )
            and str(item.get("ocrStatus") or "").upper() == "OCR_FAILED"
        )
        for item, status in zip(current, statuses)
    )
    verified = sum(
        status == "VERIFIED"
        and (
            is_photo_document(
                item,
                requirement_by_id.get(
                    str(item.get("requirementId") or item.get("requirement") or "")
                ),
            )
            or has_completed_ocr(item)
        )
        for item, status in zip(current, statuses)
    )
    processing_status = "PROCESSING" if processing else "FAILED" if failed else "REJECTED" if rejected else "NEEDS_REVIEW" if needs_review else "INSUFFICIENT_DATA" if insufficient else "VERIFIED" if current and verified == len(current) else "UPLOADED" if not current else "PENDING_VERIFICATION"
    total_pages = sum(_non_negative_int(item.get("ocrTotalPages")) for item in current)
    pages_processed = sum(_non_negative_int(item.get("ocrPagesProcessed")) for item in current)
    calculated = calculated_at or datetime.now(timezone.utc)
    score = round(100 * earned_points / total_points, 2) if total_points else 0.0
    return {
        "totalDocuments": len(current),
        "ocrCompletedDocuments": sum(has_completed_ocr(item) for item in current),
        "verifiedDocuments": verified,
        "rejectedDocuments": rejected,
        "needsReviewDocuments": needs_review,
        "suspiciousDocuments": needs_review,
        "insufficientDataDocuments": insufficient,
        "failedDocuments": failed,
        "processingDocuments": processing,
        "totalRequiredDocuments": required_count,
        "submittedRequiredDocuments": submitted_required,
        "submissionCompleteness": round(100 * submitted_required / required_count, 2) if required_count else 0,
        "verifiedRequiredDocuments": completed_required,
        "verificationPercentage": round(100 * completed_required / required_count, 2) if required_count else 0,
        "totalAvailablePoints": round(total_points, 2),
        "maximumPoints": round(total_points, 2),
        "verifiedPoints": round(earned_points, 2),
        "earnedPoints": round(earned_points, 2),
        "documentReadiness": score,
        "documentAssessmentScore": score,
        "missingRequiredDocuments": missing,
        "requirementScores": requirement_scores,
        "documentProcessingStatus": processing_status,
        "lastCalculationAt": calculated,
        "ocrProgress": {"pagesProcessed": pages_processed, "totalPages": total_pages, "percentage": round(100 * pages_processed / total_pages, 2) if total_pages else 0},
    }
