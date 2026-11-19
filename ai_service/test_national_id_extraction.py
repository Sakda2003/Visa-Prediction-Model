import base64
import os
import re
from pathlib import Path

import pymupdf
import pytest

from app.main import _build_ocr_response, fallback_extract_fields
from app.ocr_engine import OCRDocument, OCRPage
from mrz_parser import parse_mrz, parse_mrz_date


def test_cambodian_national_id_mrz_corrects_common_ocr_confusions():
    text = "\n".join(
        [
            "IDKHM01I2790846<<<<<<<<<<<<<<<<<<",
            "8605135F2611273KHM<<<<<<<<<<<<<<6",
            "K0NG<<RATHA<<<<<<<<<<<<<<<<<<K",
        ]
    )

    result = parse_mrz(text)

    assert result["format"] == "KH_NATIONAL_ID"
    assert result["data"]["national_id_number"] == "011279084"
    assert result["data"]["full_name"] == "KONG RATHA"
    assert result["data"]["date_of_birth"] == "1986-05-13"
    assert result["data"]["nationality"] == "KHM"
    assert result["data"]["sex"] == "F"
    assert result["data"]["expiry_date"] == "2026-11-27"
    assert result["status"] == "NEEDS_MANUAL_REVIEW"


def test_mrz_expiry_century_uses_cambodia_local_date():
    from datetime import date

    assert (
        parse_mrz_date("261127", "expiry", today=date(2026, 11, 18))
        == "2026-11-27"
    )


@pytest.mark.skipif(
    not (
        os.getenv("NATIONAL_ID_REGRESSION_PDF")
        and os.getenv("NATIONAL_ID_REGRESSION_OCR_TEXT_B64")
    ),
    reason="Requires the verified uploaded PDF and its saved OCR text.",
)
def test_actual_uploaded_two_page_national_id_pdf_regression():
    pdf_path = Path(os.environ["NATIONAL_ID_REGRESSION_PDF"])
    text = base64.b64decode(
        os.environ["NATIONAL_ID_REGRESSION_OCR_TEXT_B64"]
    ).decode("utf-8")
    page_texts = [
        section.strip()
        for section in re.split(r"(?m)^--- PAGE \d+ ---\s*$", text)
        if section.strip()
    ]

    with pymupdf.open(pdf_path) as document:
        assert len(document) == 2
    assert len(page_texts) == 2

    processed = OCRDocument(
        pages=[
            OCRPage(page=index, text=page_text, confidence=0.93)
            for index, page_text in enumerate(page_texts, start=1)
        ],
        metrics={
            "status": "OCR_COMPLETED",
            "page_count": 2,
            "pages_processed": 2,
        },
    )
    result = _build_ocr_response(pdf_path.name, "national_id", processed)

    assert result.document_type == "national_id"
    assert result.fields["nationalIdNumber"] == "011279084"
    assert result.fields["fullName"] == "KONG RATHA"
    assert result.fields["dateOfBirth"] == "1986-05-13"
    assert result.fields["nationality"] == "Cambodia"
    assert result.fields["gender"] == "Female"
    assert result.fields["expiryDate"] == "2026-11-27"
    assert result.missing_fields == []
    assert [page["page"] for page in result.pages] == [1, 2]
    assert result.metrics["pages_processed"] == 2
    assert result.field_evidence["nationalIdNumber"]["page"] == 1
    assert result.field_evidence["nationalIdNumber"]["evidenceText"]
    assert result.field_evidence["nationality"]["page"] == 2
    assert result.field_evidence["nationality"]["source"] == "mrz"
    assert result.field_evidence["nationality"]["evidenceText"]
    assert result.ai_analysis["mrz"]["data"]["national_id_number"] == "011279084"
    assert result.ai_analysis["authenticity_verified"] is False


def test_national_id_ocr_does_not_overwrite_values_with_empty_mrz_fields():
    fields = fallback_extract_fields(
        "Family and given names: KONG RATHA\n"
        "National ID number: 011279084\n"
        "Date of birth: May 13, 1986",
        "national_id",
    )

    assert fields["nationalIdNumber"] == "011279084"
    assert fields["fullName"] == "KONG RATHA"
    assert fields["dateOfBirth"] == "1986-05-13"
