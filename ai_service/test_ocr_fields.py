
import re
from datetime import datetime


DATE_FORMATS = (
    "%d %b %Y",
    "%d %B %Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
)


def _normalize_date(value: str):
    value = value.strip().replace(",", "")

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def _extract_labeled_date(text, labels):
    for label in labels:
        pattern = (
            rf"\b{label}\b\s*[:\-]?\s*"
            r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
            r"|\d{1,2}[/-]\d{1,2}[/-]\d{4}"
            r"|\d{4}-\d{2}-\d{2})"
        )

        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            result = _normalize_date(match.group(1))
            if result:
                return result

    return None


def _extract_labeled_amount(text, labels):
    for label in labels:
        pattern = (
            rf"\b{label}\b\s*[:\-]?\s*"
            r"(USD|KHR|EUR|THB)?\s*"
            r"([$]?\s*\d[\d,]*(?:\.\d{1,2})?)"
        )

        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            currency = match.group(1)
            amount = match.group(2)

            amount = amount.replace("$", "").replace(",", "").strip()

            try:
                amount = f"{float(amount):.2f}"
            except ValueError:
                continue

            return amount, currency.upper() if currency else None

    return None, None


def extract_fields(text: str, document_type: str) -> dict:
    if not isinstance(text, str):
        return {}

    document_type = (document_type or "").strip().lower()
    fields = {}

    if document_type == "passport":
        dob = _extract_labeled_date(
            text,
            ["Date of Birth", "Birth Date", "DOB"],
        )

        expiry = _extract_labeled_date(
            text,
            ["Date of Expiry", "Expiry Date", "Expiration Date"],
        )

        if dob:
            fields["passportDateOfBirth"] = dob

        if expiry:
            fields["passportExpiryDate"] = expiry

    elif document_type == "bank_statement":
        period = re.search(
            r"Statement\s+Period\s*[:\-]?\s*"
            r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s*"
            r"(?:-|to|–)\s*"
            r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
            text,
            re.IGNORECASE,
        )

        if period:
            start = _normalize_date(period.group(1))
            end = _normalize_date(period.group(2))

            if start:
                fields["statementStartDate"] = start

            if end:
                fields["statementEndDate"] = end

        amount, currency = _extract_labeled_amount(
            text,
            ["Closing Balance", "Ending Balance"],
        )

        if amount:
            fields["closingBalance"] = amount

        if currency:
            fields["currency"] = currency

    elif document_type == "fixed_deposit":
        amount, currency = _extract_labeled_amount(
            text,
            ["Principal Amount", "Deposit Amount", "Fixed Deposit Amount"],
        )

        maturity = _extract_labeled_date(
            text,
            ["Maturity Date", "Date of Maturity"],
        )

        if amount:
            fields["depositAmount"] = amount

        if currency:
            fields["currency"] = currency

        if maturity:
            fields["maturityDate"] = maturity

    return fields


def test_extracts_passport_dates_in_normalized_format():
    fields = extract_fields(
        "Date of Birth: 2 January 1990\nDate of Expiry: 31/12/2030",
        "passport",
    )

    assert fields == {
        "passportDateOfBirth": "1990-01-02",
        "passportExpiryDate": "2030-12-31",
    }


def test_extracts_bank_statement_period_and_closing_balance():
    fields = extract_fields(
        "Statement Period: 01/01/2026 - 31/01/2026\nClosing Balance: USD 1,250.50",
        "bank_statement",
    )

    assert fields == {
        "statementStartDate": "2026-01-01",
        "statementEndDate": "2026-01-31",
        "closingBalance": "1250.50",
        "currency": "USD",
    }


def test_extracts_monthly_opening_and_closing_balance_evidence_by_page():
    from app.main import extract_monthly_balances

    balances = extract_monthly_balances(
        "--- PAGE 1\n"
        "2026-01 Opening balance: USD 3,500.00\n"
        "2026-01 Closing balance: USD 4,100.00\n"
        "--- PAGE 2\n"
        "February 2026 Closing balance: $4,200.00\n"
        "03/2026 Ending balance: KHR 12,000.00"
    )

    assert balances == [
        {
            "month": "2026-01",
            "page": "1",
            "sourceText": "2026-01 Closing balance: USD 4,100.00",
            "openingBalance": "3500.00",
            "openingPage": "1",
            "openingSourceText": "2026-01 Opening balance: USD 3,500.00",
            "closingBalance": "4100.00",
            "closingPage": "1",
            "closingSourceText": "2026-01 Closing balance: USD 4,100.00",
            "currency": "USD",
        },
        {
            "month": "2026-02",
            "page": "2",
            "sourceText": "February 2026 Closing balance: $4,200.00",
            "closingBalance": "4200.00",
            "closingPage": "2",
            "closingSourceText": "February 2026 Closing balance: $4,200.00",
            "currency": "USD",
        },
        {
            "month": "2026-03",
            "page": "2",
            "sourceText": "03/2026 Ending balance: KHR 12,000.00",
            "closingBalance": "12000.00",
            "closingPage": "2",
            "closingSourceText": "03/2026 Ending balance: KHR 12,000.00",
            "currency": "KHR",
        },
    ]


def test_unknown_document_type_and_non_text_return_no_fields():
    assert extract_fields("irrelevant", "unknown") == {}
    assert extract_fields(None, "passport") == {}


def test_detects_and_extracts_supported_document_specific_fields():
    from app.main import detect_document_type, fallback_extract_fields

    samples = {
        "passport": (
            "PASSPORT\nPassport Number: P1234567\nFull Name: Ada Example\n"
            "Date of Birth: 2 January 1990\nDate of Expiry: 31/12/2030\n"
            "Nationality: Cambodian",
            {"passportNumber", "fullName", "dateOfBirth", "expiryDate", "nationality"},
        ),
        "bank_statement": (
            "Bank Statement\nBank Name: Example Bank\n"
            "Account Holder Name: Ada Example\nAccount Number: 123456789\n"
            "Statement Period: 01/01/2026 - 31/03/2026\n"
            "Opening Balance: USD 5,000.00\nClosing Balance: USD 5,500.00\n"
            "Total Deposits: USD 1,000.00\nTotal Withdrawals: USD 500.00\n"
            "Average Balance: USD 5,250.00\n"
            "2026-03-03 Salary Credit: 1,000.00 Balance: 5,500.00",
            {
                "bankName",
                "accountHolderName",
                "accountNumber",
                "statementStartDate",
                "statementEndDate",
                "openingBalance",
                "closingBalance",
                "deposits",
                "withdrawals",
                "averageBalance",
                "transactionHistory",
            },
        ),
        "employment_letter": (
            "Employment Certificate\nEmployee Name: Ada Example\n"
            "Employer Name: Example Ltd\nJob Title: Engineer\n"
            "Monthly Salary: USD 5,000.00\nDate of Issue: 1 March 2026",
            {"fullName", "employerName", "jobTitle", "monthlySalary", "documentDate"},
        ),
        "salary_slip": (
            "Salary Certificate\nEmployee Name: Ada Example\n"
            "Net Salary: USD 5,000.00\nPay Date: 1 March 2026",
            {"fullName", "monthlySalary", "documentDate"},
        ),
        "marriage_certificate": (
            "Marriage Certificate\nHusband Name: Ada Example\n"
            "Wife Name: Bea Example\nDate of Marriage: 1 March 2020",
            {"spouse1Name", "spouse2Name", "marriageDate"},
        ),
        "birth_certificate": (
            "Birth Certificate\nChild Name: Ada Example\n"
            "Date of Birth: 1 March 1990\nPlace of Birth: Phnom Penh",
            {"fullName", "dateOfBirth", "placeOfBirth"},
        ),
        "invitation_letter": (
            "Invitation Letter\nHost Name: Bea Example\n"
            "Visitor Name: Ada Example\nInvitation Date: 1 March 2026\n"
            "Stay Address: 123 Example Street",
            {"inviterName", "inviteeName", "invitationDate", "stayAddress"},
        ),
        "hotel_booking": (
            "Hotel Booking Confirmation\nGuest Name: Ada Example\n"
            "Hotel Name: Example Hotel\nCheck-in Date: 1 March 2026\n"
            "Check-out Date: 8 March 2026",
            {"guestName", "hotelName", "checkInDate", "checkOutDate"},
        ),
        "flight_booking": (
            "Flight Reservation\nPassenger Name: Ada Example\n"
            "Departure Date: 1 March 2026\nOrigin: PNH\nDestination: SYD",
            {"passengerName", "departureDate", "origin", "destination"},
        ),
    }

    for expected_type, (text, expected_fields) in samples.items():
        detected_type, _evidence = detect_document_type(text)
        assert detected_type == expected_type
        fields = fallback_extract_fields(text, detected_type)
        assert expected_fields.issubset(fields)
        if expected_type == "bank_statement":
            assert len(fields["transactionHistory"]) == 1
            assert fields["transactionHistory"][0]["date"] == "2026-03-03"


def test_health_reports_selected_provider_without_ollama(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app, ocr_service

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

    monkeypatch.setattr(ocr_service, "initialize", initialize_test_runtime)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "paddleocr"
    assert payload["ocr"]["model"] == "PP-OCRv6_tiny"
    assert payload["ollama"] == "disabled"
    assert payload["status"] == "ok"
