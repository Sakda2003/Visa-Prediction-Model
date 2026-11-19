from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional


def as_date(value: Any) -> Optional[datetime]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "")):
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError:
        return None


def add_months(value: Optional[datetime], months: int) -> Optional[datetime]:
    if value is None:
        return None
    year = value.year + (value.month - 1 + months) // 12
    month = (value.month - 1 + months) % 12 + 1
    day = min(value.day, 28)
    return datetime(year, month, day)


def as_amount(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalized_name_tokens(value: Any) -> List[str]:
    text = str(value or "")
    text = re.sub(r"[\u0300-\u036f]", "", text)
    tokens = re.split(r"[^A-Z0-9]+", text.upper())
    return sorted(token for token in tokens if token)


def names_match(left: Any, right: Any) -> bool:
    left_tokens = normalized_name_tokens(left)
    right_tokens = normalized_name_tokens(right)
    return bool(left_tokens) and len(left_tokens) == len(right_tokens) and all(a == b for a, b in zip(left_tokens, right_tokens))


def analyze_japan_document(
    requirement_code: Optional[str] = None,
    extracted_fields: Optional[Dict[str, Any]] = None,
    applicant: Optional[Dict[str, Any]] = None,
    application: Optional[Dict[str, Any]] = None,
    document_status: Optional[str] = None,
) -> Dict[str, Any]:
    extracted_fields = extracted_fields or {}
    applicant = applicant or {}
    application = application or {}
    requirement_code = requirement_code or "passport"

    if requirement_code == "passport":
        birth_date = as_date(extracted_fields.get("passportDateOfBirth"))
        expiry_date = as_date(extracted_fields.get("passportExpiryDate"))
        if not birth_date or not expiry_date or not as_date(applicant.get("dateOfBirth")) or not as_date(application.get("returnDate")):
            return {"status": "MANUAL_REVIEW", "message": "Enter the passport date of birth and expiry date from the document; OCR extraction is not configured."}
        if birth_date.date() != as_date(applicant.get("dateOfBirth")).date():
            return {"status": "FAIL", "message": "Passport date of birth does not match applicant information."}
        passport_full_name = str(extracted_fields.get("passportFullName") or "").strip()
        if passport_full_name and not names_match(passport_full_name, applicant.get("fullName")):
            return {"status": "FAIL", "message": "Passport full name does not match the applicant name on the form."}
        minimum_expiry = add_months(as_date(application.get("returnDate")), 6)
        if expiry_date < minimum_expiry:
            return {"status": "FAIL", "message": "Passport must remain valid for at least 6 months after the return date."}
        if document_status in {"VERIFIED", "OCR_CHECKED"}:
            return {"status": "PASS", "message": "Passport dates match the applicant and validity rules." if document_status == "VERIFIED" else "OCR-extracted passport dates match the applicant and validity rules; staff must still confirm document authenticity."}
        return {"status": "MANUAL_REVIEW", "message": "Date checks passed; staff must verify the passport document."}

    if requirement_code == "bank_statement":
        start_date = as_date(extracted_fields.get("statementStartDate"))
        end_date = as_date(extracted_fields.get("statementEndDate"))
        monthly_summaries = extracted_fields.get("bankMonthlySummaries") or []
        balance = as_amount(extracted_fields.get("closingBalance"))
        currency = str(extracted_fields.get("currency") or "").strip().upper()
        if not start_date or not end_date or balance is None or not currency:
            return {"status": "MANUAL_REVIEW", "message": "Enter the statement period, closing balance, and currency; OCR extraction is not configured."}
        if start_date > end_date:
            return {"status": "FAIL", "message": "Bank statement start date is after its end date."}
        if start_date > add_months(end_date, -3):
            return {"status": "FAIL", "message": "Bank statement must cover at least 3 calendar months."}
        if currency != "USD":
            return {"status": "MANUAL_REVIEW", "message": "Balance is not in USD; a staff member must confirm the USD equivalent."}
        if balance < 4000:
            return {"status": "FAIL", "message": "Bank statement closing balance is below USD 4,000."}
        if not isinstance(monthly_summaries, list) or len(monthly_summaries) != 3:
            return {"status": "MANUAL_REVIEW", "message": "Enter the opening balance, total deposits, total withdrawals, and closing balance for all 3 statement months."}
        parsed_months = [{
            "openingBalance": as_amount(month.get("openingBalance")),
            "totalCredits": as_amount(month.get("totalCredits")),
            "totalDebits": as_amount(month.get("totalDebits")),
            "closingBalance": as_amount(month.get("closingBalance")),
        } for month in monthly_summaries]
        if any(any(value is None or value < 0 for value in month.values()) for month in parsed_months):
            return {"status": "MANUAL_REVIEW", "message": "Complete all non-negative monthly opening balances, deposits, withdrawals, and closing balances."}
        monthly_arithmetic_mismatch = any(abs(month["openingBalance"] + month["totalCredits"] - month["totalDebits"] - month["closingBalance"]) > 1 for month in parsed_months)
        month_boundary_mismatch = any(abs(parsed_months[index + 1]["openingBalance"] - parsed_months[index]["closingBalance"]) > 1 for index in range(len(parsed_months) - 1))
        if monthly_arithmetic_mismatch or month_boundary_mismatch or abs(parsed_months[2]["closingBalance"] - balance) > 1:
            return {"status": "MANUAL_REVIEW", "message": "Monthly transaction totals do not reconcile with the statement balances; staff must review the figures."}
        if parsed_months[2]["closingBalance"] < parsed_months[0]["closingBalance"]:
            return {"status": "MANUAL_REVIEW", "message": "The statement shows a declining balance over the 3-month period; staff must review the transaction history."}
        if document_status in {"VERIFIED", "OCR_CHECKED"}:
            return {"status": "PASS", "message": "Statement period and USD 4,000 balance checks passed." if document_status == "VERIFIED" else "OCR-extracted statement period and USD 4,000 balance checks passed; staff must still confirm document authenticity."}
        return {"status": "MANUAL_REVIEW", "message": "The 3-month balance and transaction checks passed; staff must verify the statement."}

    return {"status": "MANUAL_REVIEW", "message": "Document validation is not configured for the selected requirement."}


def get_japan_document_verification_error(
    extracted_fields: Optional[Dict[str, Any]] = None,
    application: Optional[Dict[str, Any]] = None,
    requirement_code: Optional[str] = None,
) -> Optional[str]:
    analysis = analyze_japan_document(requirement_code=requirement_code, extracted_fields=extracted_fields, application=application)
    if analysis["status"] == "PASS":
        return None
    return analysis.get("message") or "Japan document review required."


def normalize_review_fields(fields: Optional[Dict[str, Any]], requirement_code: Optional[str] = None) -> Dict[str, Any]:
    allowed_fields = {
        "passport": ["fullName", "passportNumber", "issueDate", "expiryDate", "countryOfIssue"],
        "bankStatement": ["accountHolderName", "bankName", "currency", "closingBalance"],
    }
    normalized: Dict[str, Any] = {}
    for field, value in (fields or {}).items():
        allowed = allowed_fields.get(requirement_code or "", [])
        if allowed and field not in allowed:
            continue
        target_field = "passportFullName" if requirement_code == "passport" and field == "fullName" else field
        normalized[target_field] = value
    return normalized


