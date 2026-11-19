"""Synthetic PDF regression checks for the saved canonical evidence ledger."""
from copy import deepcopy
import re

import pymupdf
import pytest

from backend.app.services import verification_receipts as receipts


def _evidence_report():
    bank = {
        "documentId": "bank-selected", "fileName": "selected-bank.pdf",
        "documentReference": "SYNTHETIC-BANK", "ocrStatus": "OCR_COMPLETED",
        "status": "VERIFIED", "assignedPoints": 30, "awardedPoints": 0,
        "abaStatementAnalysis": {
            "schemaVersion": "aba-statement-v1", "analysisRevision": "analysis-revision-2",
            "statementStrengthScore": 5.5, "normalizedQuality": "0.5",
            "mappingVersion": "bank-linear-1-to-10-v1", "modelVersion": "1.0",
            "reviewStatus": "VERIFIED", "reconciliationStatus": "RECONCILED",
            "ownerRelationship": "SPONSOR", "ownerRelationshipConfirmed": True,
            "authenticityStatus": "NOT_VERIFIED",
            "statement": {"currency": "USD", "start": "2026-08-01", "end": "2026-10-31"},
            "metrics": {"average": "450000", "ending": 500000, "money_in": 100000, "money_out": 50000},
            "source": {"sha256": "synthetic-source-hash-001", "filename": "selected-bank.pdf", "units": "minor", "currency": "USD"},
            "audit": [{"type": "EXTRACTION_CORRECTION", "field": "ending", "row": "row-7", "original": 490000, "corrected": 500000, "reason": "Confirmed source row seven", "actorId": "synthetic-staff", "at": "2026-11-18T01:00:00Z", "sourceResolutionConfirmed": True}],
        },
        # These compatibility diagnostics must never replace the ABA evidence score.
        "bankStatementAnalysis": {"bankStatementScore": {"available": True, "points": 4.6, "maximum": 50}, "financialRequirementStatus": "INSUFFICIENT_DATA"},
    }
    return {
        "receiptNumber": "SYNTHETIC-RECEIPT-2", "receiptGeneratedAt": "2026-11-18T02:00:00Z",
        "receiptVersion": 2, "reportType": "EVIDENCE_ASSESSMENT", "finalStatus": "VERIFIED",
        "policyVersionId": "saved-policy-release-1",
        "evidencePolicySnapshot": {"id": "saved-policy-release-1", "version": "saved-policy-2026.1", "scoringVersion": "evidence-v3", "contentHash": "saved-policy-content-hash", "requirements": []},
        "applicant": {"fullName": "Synthetic Applicant"},
        "application": {"applicationNumber": "SYNTHETIC-CASE"},
        "summary": {
            "scoringVersion": "evidence-v3", "status": "READY_FOR_STAFF_REVIEW",
            "confirmedReadinessPoints": 85, "exactPoints": "85", "maximumPoints": 100,
            "totalAvailablePoints": 100, "verifiedPoints": 85,
            "totalDocuments": 2, "verifiedDocuments": 2,
            "policyVersion": "saved-policy-2026.1", "policyVersionId": "saved-policy-release-1",
            "requirementScores": [
                {"slotId": "identity", "requirementId": "identity", "name": "Identity evidence", "scoringMode": "FIXED", "configuredPoints": 70, "maximumEarnedPoints": "70", "earnedPoints": 70, "exactEarnedPoints": "70", "quality": "1", "status": "VERIFIED", "documentIds": ["identity-selected"], "selectedDocumentId": "identity-selected"},
                {"slotId": "bank", "requirementId": "bank", "name": "Bank evidence", "scoringMode": "DYNAMIC", "configuredPoints": 30, "minimumEarnedPoints": "0", "maximumEarnedPoints": "30", "earnedPoints": 15, "exactEarnedPoints": "15", "quality": "0.5", "status": "VERIFIED", "documentIds": ["bank-selected"], "selectedDocumentId": "bank-selected", "provenance": {"bankAnalysisRevision": "analysis-revision-2", "bankModelVersion": "1.0", "mappingVersion": "bank-linear-1-to-10-v1"}},
            ],
            "disclaimer": "Evidence strength is not visa approval probability or proof of document authenticity.",
        },
        "prediction": {"status": "UNAVAILABLE", "modelVersion": "synthetic-inactive-model"},
        "documents": [
            {"documentId": "identity-selected", "fileName": "identity.pdf", "documentReference": "SYNTHETIC-IDENTITY", "ocrStatus": "OCR_COMPLETED", "status": "VERIFIED", "assignedPoints": 70, "awardedPoints": 70},
            bank,
        ],
    }


@pytest.fixture
def pdf_text(monkeypatch, tmp_path):
    # Receipt tests do not open private storage or use external evidence previews.
    monkeypatch.setattr(receipts, "_document_thumbnail", lambda document: None)
    monkeypatch.setattr(receipts, "_LOGO_PATH", tmp_path / "no-logo.jpg")

    def render(report):
        content = receipts.create_receipt_pdf(report, [], "http://127.0.0.1/synthetic-receipt")
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            assert pdf.page_count > 0
            return re.sub(r"\s+", " ", " ".join(page.get_text() for page in pdf)).strip()
    return render


def test_evidence_pdf_uses_saved_canonical_ledger_and_aba_minor_units(pdf_text):
    report = _evidence_report()
    saved_report = deepcopy(report)
    text = pdf_text(report)

    assert report == saved_report
    assert "Evidence Assessment Ledger" in text
    assert re.search(r"85(?:\.0)?\s*/\s*100", text)
    assert "Bank evidence" in text and "VERIFIED" in text and "0.5" in text
    assert "Points: 15 / 30" in text
    assert "EVIDENCE_ASSESSMENT" in text and "Receipt version" in text
    assert "saved-policy-2026.1" in text and "saved-policy-release-1" in text
    assert "evidence-v3" in text
    assert "ABA Statement Evidence" in text
    for amount in ("4,500.00", "5,000.00", "1,000.00", "500.00"):
        assert amount in text
    assert "USD" in text
    assert re.search(r"5\.5\s*/\s*10", text)
    assert "bank-linear-1-to-10-v1" in text and "aba-statement-v1" in text
    assert "analysis-revision-2" in text and "synthetic-source-hash-001" in text
    assert "EXTRACTION_CORRECTION" in text and "Confirmed source row seven" in text
    assert "SPONSOR" in text
    assert "not visa approval probability" in text
    assert "Bank Statement Financial Checks" not in text
    assert "Three-month requirement" not in text
    assert "4.6/50" not in text.replace(" ", "")


def test_evidence_pdf_retains_exact_ledger_values_instead_of_rounded_compatibility_fields(pdf_text):
    report = _evidence_report()
    report["summary"]["exactPoints"] = "85.004"
    report["summary"]["requirementScores"][1]["exactEarnedPoints"] = "15.004"

    text = pdf_text(report)

    assert "85.004" in text and "15.004" in text
    assert "Points: 0 / 30" not in text


def test_evidence_pdf_does_not_award_duplicate_unselected_uploads(pdf_text):
    report = _evidence_report()
    duplicate = deepcopy(report["documents"][1])
    duplicate.update(documentId="bank-unselected", fileName="unselected-bank.pdf", awardedPoints=30)
    report["documents"].append(duplicate)
    report["summary"]["requirementScores"][1]["documentIds"].append("bank-unselected")

    text = pdf_text(report)
    selected_detail = text.split("Uploaded Documents", 1)[1].split("selected-bank.pdf", 1)[1].split("unselected-bank.pdf", 1)[0]
    duplicate_detail = text.split("Uploaded Documents", 1)[1].split("unselected-bank.pdf", 1)[1]

    assert "Points: 15 / 30" in selected_detail
    assert "Points: 30 / 30" not in duplicate_detail
    assert "Not selected" in duplicate_detail or "Points: 0 / 30" in duplicate_detail
    assert re.search(r"85(?:\.0)?\s*/\s*100", text)


def test_evidence_pdf_shows_unavailable_bank_quality_without_inventing_a_score(pdf_text):
    report = _evidence_report()
    analysis = report["documents"][1]["abaStatementAnalysis"]
    analysis.update(statementStrengthScore=None, normalizedQuality=None, reviewStatus="PENDING_REVIEW")
    slot = report["summary"]["requirementScores"][1]
    slot.update(quality=None, earnedPoints=0, exactEarnedPoints="0", status="NEEDS_REVIEW")
    report["summary"].update(confirmedReadinessPoints=70, exactPoints="70", verifiedPoints=70)

    text = pdf_text(report)
    aba_section = text.split("ABA Statement Evidence", 1)[1].split("Machine Learning Estimate", 1)[0]

    assert "Unavailable" in aba_section
    assert "PENDING_REVIEW" in aba_section
    assert "5.5" not in aba_section
    assert "Points: 0 / 30" in text


def test_legacy_pdf_keeps_compatibility_checks_and_document_points(pdf_text):
    report = _evidence_report()
    report.pop("evidencePolicySnapshot")
    report["summary"] = {"verifiedPoints": 0, "totalAvailablePoints": 30}
    report["documents"] = [report["documents"][1]]
    report["documents"][0].pop("abaStatementAnalysis")

    text = pdf_text(report)

    assert "Bank Statement Financial Checks" in text
    assert "Three-month requirement" in text
    assert "4.6/50" in text.replace(" ", "")
    assert "Points: 0 / 30" in text
    assert "Evidence Assessment Ledger" not in text
