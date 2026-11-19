from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def to_comparable(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip().lower()


def normalized_similarity(left: Any, right: Any) -> float:
    a = re.sub(r"[^a-z0-9]", "", to_comparable(left))
    b = re.sub(r"[^a-z0-9]", "", to_comparable(right))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    shorter = min(len(a), len(b))
    if shorter == 0:
        return 0.0
    matches = sum(1 for index in range(shorter) if a[index] == b[index])
    return matches / shorter


def build_hybrid_decision(
    form_score_points: float = 0,
    document_score_points: float = 0,
    results: Sequence[Dict[str, Any]] | None = None,
    overall_status: str | None = None,
) -> Dict[str, Any]:
    results = list(results or [])
    required_failures = [
        item for item in results if (item.get("required") or not item.get("conditional")) and item.get("status") in {"FAIL", "MISSING", "INVALID"}
    ]
    reviews = [item for item in results if item.get("status") in {"MANUAL_REVIEW", "WARNING", "REVIEW"}]
    overall_score = clamp(form_score_points + document_score_points, 0, 100)

    recommended_decision = "PASS"
    recommendation_confidence = 100
    risk_level = "LOW"
    risk_score = 0

    if required_failures:
        recommended_decision = "FAIL"
        risk_level = "HIGH"
        risk_score = 85
        recommendation_confidence = 90
    elif reviews or overall_score < 70:
        recommended_decision = "REVIEW"
        risk_level = "MEDIUM"
        risk_score = 55
        recommendation_confidence = 76
    elif overall_score >= 90:
        recommended_decision = "PASS"
        risk_level = "LOW"
        risk_score = 18
        recommendation_confidence = 92

    reasons: List[str] = []
    supporting_evidence: List[Dict[str, Any]] = []
    missing_information: List[str] = []
    critical_warnings: List[str] = []

    if required_failures:
        reasons.append("Critical required checks failed.")
        for item in required_failures:
            supporting_evidence.append({
                "field": item.get("requirementName") or item.get("requirementId"),
                "status": item.get("status"),
                "explanation": item.get("explanationEn"),
            })
            critical_warnings.append(item.get("explanationEn") or f"{item.get('requirementName') or item.get('requirementId')} requires attention.")

    if reviews:
        reasons.append("Human review is required for ambiguous or incomplete evidence.")
        for item in reviews:
            missing = item.get("missingInformation") or []
            if missing:
                missing_information.extend(missing)
            supporting_evidence.append({
                "field": item.get("requirementName") or item.get("requirementId"),
                "status": item.get("status"),
                "explanation": item.get("explanationEn"),
            })

    if 70 <= overall_score < 90:
        reasons.append("Overall evidence is acceptable but not strong enough for automatic approval.")
    if overall_score >= 90:
        reasons.append("Evidence strength is high and meets the configured verification thresholds.")

    match_fields: List[Dict[str, Any]] = []
    for item in results:
        detected_fields = item.get("detectedFields")
        if not isinstance(detected_fields, dict):
            continue
        for field_name, field_value in detected_fields.items():
            match_fields.append({
                "field_name": field_name,
                "form_value": field_value,
                "document_value": field_value,
                "match_status": "MATCH",
                "similarity_percentage": 100,
                "confidence": 100,
                "evidence": f"{item.get('requirementName') or item.get('requirementId')} provided {field_name}.",
                "explanation": item.get("explanationEn") or "Document and form information were accepted by backend validation.",
            })

    return {
        "recommended_decision": recommended_decision,
        "recommendation_confidence": recommendation_confidence,
        "risk_level": risk_level,
        "risk_score": risk_score,
        "reasons": reasons,
        "supporting_evidence": supporting_evidence,
        "missing_information": list(dict.fromkeys(missing_information)),
        "critical_warnings": list(dict.fromkeys(critical_warnings)),
        "model_version": "hybrid-decision-engine-v1",
        "overall_score": round(overall_score, 2),
        "match_results": match_fields,
        "overall_status": overall_status or recommended_decision,
    }


def compare_applicant_and_document(form_value: Any, document_value: Any) -> Dict[str, Any]:
    left = to_comparable(form_value)
    right = to_comparable(document_value)
    if not left and not right:
        return {"match_status": "NOT_FOUND", "similarity_percentage": 0, "confidence": 0}
    if not left or not right:
        return {"match_status": "NEEDS_REVIEW", "similarity_percentage": 0, "confidence": 0}
    similarity = normalized_similarity(left, right)
    if similarity >= 0.95:
        return {"match_status": "MATCH", "similarity_percentage": round(similarity * 100), "confidence": 95}
    if similarity >= 0.75:
        return {"match_status": "PARTIAL_MATCH", "similarity_percentage": round(similarity * 100), "confidence": 70}
    return {"match_status": "MISMATCH", "similarity_percentage": round(similarity * 100), "confidence": 80}

