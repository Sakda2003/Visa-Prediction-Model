from datetime import date

from app.services.readiness_scoring import calculate_readiness_score


def test_missing_unclassified_required_document_applies_gate():
    result = calculate_readiness_score(
        requirements=[
            {"requirementId": "r1", "code": "travel_itinerary", "name": "Travel itinerary", "required": True}
        ],
        documents=[],
        as_of=date(2026, 11, 16),
    )
    assert result["gateApplied"] is True
    assert result["score"] <= 40
    assert result["missingRequiredDocuments"] == ["Travel itinerary"]


def test_optional_missing_document_does_not_apply_gate():
    result = calculate_readiness_score(
        requirements=[
            {"requirementId": "r1", "code": "travel_itinerary", "name": "Travel itinerary", "required": False}
        ],
        documents=[],
        as_of=date(2026, 11, 16),
    )
    assert result["gateApplied"] is False
    assert result["missingRequiredDocuments"] == []


def test_score_is_readiness_not_approval_probability():
    result = calculate_readiness_score([], [], as_of=date(2026, 11, 16))
    assert result["scoreType"] == "READINESS_SCORE"
    assert "not a visa approval probability" in result["disclaimer"]
