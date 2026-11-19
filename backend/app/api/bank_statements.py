from __future__ import annotations

import json
import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ..core.config import PROJECT_ROOT, settings
from ..core.errors import APIError
from ..services.business_rules import (
    DEFAULT_RULE_POLICY,
    evaluate_business_rules,
    validate_rule_policy,
)
from ..services.financial_analysis import calculate_financial_analysis
from ..services.aba_integration import (analyze_aba_pdf, aba_to_legacy_ocr, apply_correction, reviewed_analysis, decrypted_render_bytes)
from ..services.bank_statement_scoring import (
    analyze_bank_statement as score_bank_statement,
    calculate_document_verification_score,
    normalize_bank_fields,
)
from .dependencies import get_mongo_database, require_roles
from .mongo_helpers import id_query

logger = logging.getLogger(__name__)
router = APIRouter()

_PRIVATE_DIR = PROJECT_ROOT / "backend" / "storage" / "private"
_SIGNATURES = {
    ".pdf": b"%PDF-",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".png": b"\x89PNG\r\n\x1a\n",
}
_LOW_CONFIDENCE_THRESHOLD = 0.65
_DISCLAIMER = (
    "OCR and field-completeness results are for document review only. "
    "They do not predict visa approval or verify document authenticity."
)


def _financial_features(fields: dict[str, Any], transactions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "currency": fields.get("currency"),
        "opening_balance": fields.get("openingBalance"),
        "closing_balance": fields.get("closingBalance"),
        "deposits": fields.get("deposits"),
        "withdrawals": fields.get("withdrawals"),
        "average_balance": fields.get("averageBalance"),
        "transaction_count": len(transactions),
    }


def _case_context(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "destination_country",
        "nationality",
        "citizenship",
        "has_overstay_history",
        "passport_expiry_date",
        "passportExpiryDate",
        "previous_visa_denials",
        "employment_letter",
        "business_evidence",
        "ties_to_cambodia",
        "fixed_deposit",
        "sponsor_support",
        "travel_history_count",
        "age",
        "gender",
        "first_time_traveller",
        "marital_status",
    }
    context = {key: value[key] for key in allowed if key in value}
    documents = value.get("documents")
    if isinstance(documents, list):
        context["documents"] = [
            {
                key: item[key]
                for key in ("type", "code", "status")
                if key in item and isinstance(item[key], (str, int, bool))
            }
            for item in documents[:100]
            if isinstance(item, dict)
        ]
    return context


def _mask_account_number(value: Any) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]", "", str(value or ""))
    if len(normalized) <= 4:
        return "****"
    return f"{'*' * min(8, len(normalized) - 4)}{normalized[-4:]}"


def _account_number_from(record: dict[str, Any]) -> Any:
    extracted = record.get("result", {}).get("extracted", {})
    if not isinstance(extracted, dict):
        return None
    return extracted.get("accountNumber") or extracted.get("accountNo")


def _public_analysis(value: Any, account_numbers: set[str] | None = None) -> Any:
    known_numbers = account_numbers or set()
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized_key = re.sub(r"[^a-z]", "", str(key).casefold())
            if normalized_key in {"accountnumber", "accountno"} and item:
                result[key] = _mask_account_number(item)
            else:
                result[key] = _public_analysis(item, known_numbers)
        return result
    if isinstance(value, list):
        return [_public_analysis(item, known_numbers) for item in value]
    if isinstance(value, str):
        sanitized = value
        for account_number in known_numbers:
            compact = re.sub(r"[^A-Za-z0-9]", "", account_number)
            if len(compact) < 5:
                continue
            variants = {account_number, compact}
            variants.add(" ".join(compact))
            for variant in variants:
                if variant:
                    sanitized = re.sub(
                        re.escape(variant),
                        _mask_account_number(compact),
                        sanitized,
                        flags=re.IGNORECASE,
                    )
        return re.sub(
            r"(?im)(account\s*(?:number|no\.?|#)\s*[:#-]?\s*)([A-Z0-9][A-Z0-9 -]{3,})",
            lambda match: match.group(1) + _mask_account_number(match.group(2)),
            sanitized,
        )
    return value


def _build_result(
    ocr: dict[str, Any],
    applicant_context: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    applicant_name: str = "",
) -> dict[str, Any]:
    fields = normalize_bank_fields(ocr.get("fields"))
    transactions = fields.get("transactionHistory")
    if not isinstance(transactions, list):
        transactions = []
    metrics = ocr.get("metrics") if isinstance(ocr.get("metrics"), dict) else {}
    failed_pages = ocr.get("failed_pages") or []
    ocr_incomplete = (
        str(ocr.get("status") or "").lower() == "partial"
        or str(ocr.get("status") or "").lower() == "failed"
        or bool(failed_pages)
        or metrics.get("status") == "OCR_PARTIAL"
    )
    if ocr_incomplete:
        required_fields = calculate_document_verification_score({})["missing_fields"]
        present_fields = [
            name for name in required_fields if fields.get(name) not in (None, "", [])
        ]
        missing_fields = [
            name for name in required_fields if name not in present_fields
        ]
        verification = {
            "document_verification_score": None,
            "score_type": "document_field_completeness",
            "required_fields": len(required_fields),
            "extracted_fields": len(present_fields),
            "missing_fields": missing_fields,
            "basis": (
                "Unavailable because OCR did not successfully process every page."
            ),
            "score_available": False,
        }
    else:
        verification = calculate_document_verification_score(fields)
    confidence = ocr.get("confidence")
    try:
        numeric_confidence = float(confidence)
    except (TypeError, ValueError):
        numeric_confidence = 0.0
    reported_confidence = None if metrics.get("source") == "ABA_NATIVE_LOCAL" else numeric_confidence
    warnings = list(ocr.get("warnings") or [])
    if ocr_incomplete:
        failed_page_numbers = [
            str(item.get("page"))
            for item in failed_pages
            if isinstance(item, dict) and item.get("page") is not None
        ]
        warnings.append(
            "OCR is incomplete; no final bank-statement verification score was generated."
            + (
                f" Failed page(s): {', '.join(failed_page_numbers)}."
                if failed_page_numbers
                else ""
            )
        )
    if metrics.get("source") == "ABA_NATIVE_LOCAL":
        warnings.append("No numeric OCR confidence was assigned to native/local ABA extraction; inspect its source and reconciliation checks.")
    elif numeric_confidence < _LOW_CONFIDENCE_THRESHOLD:
        warnings.append("OCR confidence is low; verify extracted values against the original statement.")

    financial_assessment = {
        "status": (
            "manual_review"
            if ocr_incomplete
            or verification["missing_fields"]
            or numeric_confidence < _LOW_CONFIDENCE_THRESHOLD
            else "extracted"
        ),
        "missing_fields": verification["missing_fields"],
        "warnings": warnings,
        "authenticity_verified": False,
    }
    ml_assessment = {
        "status": "unavailable",
        "reason": "No validated visa outcome model is configured.",
    }
    extracted = {
        **fields,
        "transactionHistory": transactions,
        "rawText": str(ocr.get("extracted_text") or ""),
        "confidence": reported_confidence,
        "pages": ocr.get("pages") or [],
        "metrics": metrics,
        "failed_pages": failed_pages,
    }
    score = {
        **verification,
        "ocr_confidence": reported_confidence,
    }
    if ocr_incomplete:
        score["status"] = "OCR_PARTIAL"
    active_policy = validate_rule_policy(policy or DEFAULT_RULE_POLICY)
    financial_analysis = calculate_financial_analysis(
        fields,
        transactions,
        active_policy["financial_scoring"],
    )
    if ocr_incomplete:
        financial_analysis = {
            **financial_analysis,
            "financial_score": None,
            "status": "manual_review",
            "human_review_required": True,
        }
    business_rules = evaluate_business_rules(
        applicant_context or {},
        financial_analysis,
        active_policy,
    )
    bank_statement_analysis = score_bank_statement(
        fields,
        raw_text=str(ocr.get("extracted_text") or ""),
        applicant_name=applicant_name or str(
            (applicant_context or {}).get("full_name")
            or (applicant_context or {}).get("fullName")
            or ""
        ),
        config=active_policy["bank_statement_scoring"],
        financial_config=active_policy["financial_scoring"],
        ocr_complete=not ocr_incomplete,
    )
    normalized_transactions = bank_statement_analysis.get("transactions", [])
    ocr_page_count = metrics.get("page_count") or len(ocr.get("pages") or [])
    bank_statement_analysis.update(
        {
            "documentInformation": {
                "fileName": ocr.get("filename"),
                "ocrPageCount": ocr_page_count,
                "transactionPageCount": None,
            },
            "ocrProcessingSummary": {
                "status": "OCR_PARTIAL" if ocr_incomplete else "OCR_COMPLETED",
                "pagesProcessed": metrics.get("pages_processed") or len(ocr.get("pages") or []),
                "totalPages": ocr_page_count,
                "failedPages": failed_pages,
                "confidence": reported_confidence,
                "metrics": metrics,
            },
            "accountInformation": {
                "bankName": fields.get("bankName") or fields.get("bank_name"),
                "accountHolderName": fields.get("accountHolderName") or fields.get("account_holder_name"),
                "accountNumber": fields.get("accountNumber") or fields.get("account_number") or fields.get("accountNo"),
                "currency": fields.get("currency") or fields.get("currency_code"),
            },
            "documentVerificationScore": verification,
        }
    )
    extracted["normalizedTransactions"] = normalized_transactions
    extracted["invalidTransactions"] = bank_statement_analysis.get("invalidTransactions", [])
    extracted["transactionNormalizationWarnings"] = bank_statement_analysis.get(
        "normalizationWarnings", []
    )
    if ocr_incomplete:
        business_rules = {
            **business_rules,
            "status": "manual_review",
            "reason": "OCR did not successfully process every page.",
        }
    return {
        "ocr_status": "OCR_PARTIAL" if ocr_incomplete else "OCR_COMPLETED",
        "extracted": extracted,
        "score": score,
        "financial_features": _financial_features(fields, transactions),
        "financial_analysis": financial_analysis,
        "business_rules": business_rules,
        "bank_statement_analysis": bank_statement_analysis,
        "document_information": bank_statement_analysis["documentInformation"],
        "ocr_processing_summary": bank_statement_analysis["ocrProcessingSummary"],
        "account_information": bank_statement_analysis["accountInformation"],
        "statement_period": bank_statement_analysis["statementPeriod"],
        "monthly_financial_summary": bank_statement_analysis["transactionSummary"]["monthly"],
        "balance_reconciliation": bank_statement_analysis["reconciliation"],
        "account_holder_verification": bank_statement_analysis["accountHolderCheck"],
        "source_of_funds_analysis": bank_statement_analysis["sourceOfFundsAnalysis"],
        "financial_scoring": bank_statement_analysis["bankStatementScore"],
        "review_flags": bank_statement_analysis["reviewFlags"],
        "missing_evidence": bank_statement_analysis["missingEvidence"],
        "extraction_warnings": list(dict.fromkeys(warnings + bank_statement_analysis["extractionWarnings"])),
        "financial_assessment": {
            **financial_analysis,
            "missing_fields": verification["missing_fields"],
            "ocr_confidence": reported_confidence,
        },
        "ml_assessment": ml_assessment,
        "warnings": warnings,
        "human_review_required": (
            ocr_incomplete
            or financial_assessment["status"] == "manual_review"
            or financial_analysis["human_review_required"]
            or business_rules["status"] == "manual_review"
            or bank_statement_analysis["status"] != "CONSISTENCY_CHECK_PASSED"
        ),
        "disclaimer": _DISCLAIMER,
    }


def _analysis_record(statement_id: str, filename: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = {**payload["result"], "statement_id": statement_id}
    score = result["score"]
    now = datetime.now(timezone.utc).isoformat()
    status = "manual_review" if result["human_review_required"] else "complete"
    return {
        "_id": statement_id,
        "statementId": statement_id,
        "fileName": filename,
        "applicantName": payload["applicant_name"],
        "visaType": payload["visa_type"],
        "requirements": payload["requirements"],
        "status": status,
        "verification_status": status,
        "extraction_status": (
            "partial" if result.get("ocr_status") == "OCR_PARTIAL" else "completed"
        ),
        "document_verification_score": score["document_verification_score"],
        "score": score,
        "financial_score": result["financial_analysis"]["financial_score"],
        "financial_features": result["financial_features"],
        "financial_analysis": result["financial_analysis"],
        "business_rules": result["business_rules"],
        "bank_statement_analysis": result["bank_statement_analysis"],
        "bank_statement_score": result["bank_statement_analysis"][
            "bankStatementScore"
        ],
        "financial_assessment": result["financial_assessment"],
        "ml_assessment": result["ml_assessment"],
        "case_context": payload["applicant_context"],
        "warnings": result["warnings"],
        "result": result,
        "storagePath": payload["storage_path"],
        "createdAt": now,
        "updatedAt": now,
    }


async def _audit_statement_analysis(
    database: AsyncDatabase, actor_id: str, statement_id: str
) -> None:
    try:
        await database["ml_business_audit"].insert_one(
            {
                "_id": str(uuid4()),
                "actor_id": actor_id,
                "action": "bank_statement_analyzed",
                "details": {"statement_id": statement_id},
                "created_at": datetime.now(timezone.utc),
            }
        )
    except PyMongoError as error:
        logger.exception("Could not audit bank statement analysis")
        raise APIError(503, "Required audit record could not be saved") from error


async def _load_statement(
    statement_id: str,
    database: AsyncDatabase,
) -> dict[str, Any]:
    try:
        statement = await database["bank_statements"].find_one(id_query(statement_id))
    except PyMongoError as error:
        logger.exception("Could not load bank statement analysis")
        raise APIError(503, "Bank statement analysis is unavailable") from error
    if statement is None:
        raise APIError(404, "Bank statement analysis not found")
    return statement


@router.post("/analyze", status_code=201)
async def analyze_bank_statement(
    file: UploadFile = File(...),
    applicant_name: str = Form(default="", alias="applicant_name"),
    applicant_name_camel: str = Form(default="", alias="applicantName"),
    visa_type: str = Form(default="", alias="visa_type"),
    visa_type_camel: str = Form(default="", alias="visaType"),
    requirements_json: str = Form(default="{}", alias="requirements"),
    applicant_context_json: str = Form(default="{}", alias="applicant_context"),
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
    pdf_password: str = Form(default="", alias="pdfPassword"),
    force_ocr: bool = Form(default=False, alias="forceOcr"),
) -> JSONResponse:
    filename = Path(file.filename or "bank-statement").name[:255]
    if len(pdf_password) > 1024:
        await file.close()
        raise APIError(400, "PDF password must contain at most 1024 characters")
    extension = Path(filename).suffix.lower()
    signature = _SIGNATURES.get(extension)
    if signature is None:
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "Only PDF, JPG, JPEG, and PNG files are supported"},
        )

    content = await file.read(settings.max_file_size + 1)
    await file.close()
    if not content:
        return JSONResponse(status_code=400, content={"message": "The uploaded file is empty"})
    if len(content) > settings.max_file_size:
        return JSONResponse(status_code=413, content={"message": "Uploaded file exceeds the configured maximum size"})
    if not content.startswith(signature):
        return JSONResponse(status_code=400, content={"message": "File content does not match its extension"})

    try:
        requirements = json.loads(requirements_json or "{}")
    except json.JSONDecodeError as error:
        raise APIError(400, "Requirements must be valid JSON") from error
    if not isinstance(requirements, (dict, list)):
        raise APIError(400, "Requirements must be a JSON object or array")
    try:
        applicant_context = json.loads(applicant_context_json or "{}")
    except json.JSONDecodeError as error:
        raise APIError(400, "Applicant context must be valid JSON") from error
    if not isinstance(applicant_context, dict):
        raise APIError(400, "Applicant context must be a JSON object")
    applicant_context = _case_context(applicant_context)

    statement_id = str(uuid4())
    aba_analysis = await asyncio.to_thread(analyze_aba_pdf, content, filename, password=pdf_password, document_id=statement_id, force_ocr=force_ocr) if extension == ".pdf" else None
    if aba_analysis is not None:
        ocr = aba_to_legacy_ocr(aba_analysis)
    else:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.ai_service_timeout_seconds, connect=5.0)
            ) as client:
                response = await client.post(
                    f"{settings.ai_service_url}/ocr/extract",
                    files={"file": (filename, content)},
                    data={"requirement_code": "bank_statement"},
                )
            response.raise_for_status()
            ocr = response.json()
        except httpx.TimeoutException as error:
            logger.warning("OCR service timed out during bank-statement analysis")
            raise APIError(504, "OCR service timed out") from error
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("OCR service failed during bank-statement analysis: %s", error.__class__.__name__)
            raise APIError(503, "OCR service is unavailable") from error
        if not isinstance(ocr, dict):
            raise APIError(503, "OCR service returned an invalid response")

    storage_path = _PRIVATE_DIR / f"{statement_id}{extension}"
    try:
        _PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(content)
    except OSError as error:
        logger.exception("Could not retain uploaded bank statement")
        raise APIError(500, "Could not retain uploaded bank statement") from error

    try:
        policy_record = await database["business_rules"].find_one(
            {"_id": "visa_financial_rules"}
        )
        active_policy = validate_rule_policy(
            policy_record.get("policy") if policy_record else DEFAULT_RULE_POLICY
        )
    except PyMongoError as error:
        logger.exception("Could not load rules for bank statement analysis")
        raise APIError(503, "Business rules are unavailable") from error
    except ValueError as error:
        logger.exception("Stored business rules are invalid")
        raise APIError(503, "Business rules require administrator review") from error

    result = _build_result(
        ocr,
        applicant_context,
        active_policy,
        applicant_name or applicant_name_camel,
    )
    if aba_analysis is not None:
        result["abaStatementAnalysis"] = aba_analysis
        result["human_review_required"] = True
        # Legacy score/tolerance remains diagnostic; exact reader gates are authoritative.
        result["warnings"].append("ABA evidence requires account-holder and staff review before application points are confirmed.")
    payload = {
        "applicant_name": applicant_name or applicant_name_camel,
        "visa_type": visa_type or visa_type_camel,
        "requirements": requirements,
        "applicant_context": applicant_context,
        "storage_path": str(storage_path),
        "result": result,
    }
    record = _analysis_record(statement_id, filename, payload)
    record["createdBy"] = _principal["id"]
    if aba_analysis is not None:
        record["abaStatementAnalysis"] = aba_analysis
        if pdf_password:
            render_bytes = decrypted_render_bytes(content,pdf_password)
            if render_bytes:
                (_PRIVATE_DIR / f"{statement_id}.aba-render.pdf").write_bytes(render_bytes)
    pdf_password = ""
    try:
        await database["bank_statements"].insert_one(record)
    except PyMongoError as error:
        try:
            storage_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not clean up an unpersisted bank statement upload")
        logger.exception("Could not save bank statement analysis")
        raise APIError(503, "Bank statement analysis could not be saved") from error
    await _audit_statement_analysis(database, _principal["id"], statement_id)

    account_number = _account_number_from(record)
    public_result = _public_analysis(
        record["result"],
        {str(account_number)} if account_number else set(),
    )
    return JSONResponse(
        status_code=201,
        content={
            "statementId": statement_id,
            "abaStatementAnalysis": record.get("abaStatementAnalysis"),
            "status": record["status"],
            "extraction_status": record["extraction_status"],
            "financial_score": record["financial_score"],
            "financial_features": record["financial_features"],
            "financial_analysis": public_result["financial_analysis"],
            "business_rules": public_result["business_rules"],
            "bank_statement_analysis": public_result["bank_statement_analysis"],
            "financial_assessment": public_result["financial_assessment"],
            "ml_assessment": record["ml_assessment"],
            "human_review_required": record["result"]["human_review_required"],
            "document_verification_score": record["document_verification_score"],
            "bank_statement_score": public_result["bank_statement_analysis"][
                "bankStatementScore"
            ],
            "disclaimer": _DISCLAIMER,
            "result": public_result,
        },
    )


@router.get("/{statement_id}/result")
async def get_bank_statement_result(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    account_number = _account_number_from(statement)
    return JSONResponse(
        content={
            "result": _public_analysis(
                statement.get("result"),
                {str(account_number)} if account_number else set(),
            )
        }
    )


@router.get("/{statement_id}")
async def get_bank_statement(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    account_number = _account_number_from(statement)
    public_statement = _public_analysis(
        statement,
        {str(account_number)} if account_number else set(),
    )
    public_statement.pop("storagePath", None)
    return JSONResponse(content={"statement": public_statement})


@router.get("/{statement_id}/score")
async def get_bank_statement_score(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    return JSONResponse(
        content={
            "score": statement.get("score"),
            "abaStatementAnalysis": statement.get("abaStatementAnalysis"),
            "bank_statement_score": statement.get("bank_statement_analysis", {}).get(
                "bankStatementScore"
            ),
            "verification_status": statement.get("verification_status"),
            "document_verification_score": statement.get("document_verification_score"),
        }
    )


@router.post("/{statement_id}/recalculate")
async def recalculate_bank_statement_score(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    extracted = statement.get("result", {}).get("extracted", {})
    if (
        statement.get("extraction_status") == "partial"
        or statement.get("result", {}).get("ocr_status") == "OCR_PARTIAL"
        or extracted.get("failed_pages")
        or (extracted.get("metrics") or {}).get("status") == "OCR_PARTIAL"
    ):
        raise APIError(
            409,
            "OCR did not successfully process every page. Retry OCR before recalculating the bank-statement score.",
        )
    fields = normalize_bank_fields(extracted)
    score = {
        **calculate_document_verification_score(fields),
        "ocr_confidence": extracted.get("confidence", 0.0),
    }
    try:
        numeric_confidence = float(score["ocr_confidence"])
    except (TypeError, ValueError):
        numeric_confidence = 0.0
    needs_review = (
        bool(score["missing_fields"])
        or numeric_confidence < _LOW_CONFIDENCE_THRESHOLD
    )
    update = {
        "score": score,
        "document_verification_score": score["document_verification_score"],
        "result.score": score,
        "result.human_review_required": needs_review,
        "result.financial_assessment.status": (
            "manual_review" if needs_review else "extracted"
        ),
        "verification_status": "manual_review" if needs_review else "complete",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        policy_record = await database["business_rules"].find_one(
            {"_id": "visa_financial_rules"}
        )
        policy = validate_rule_policy(
            policy_record.get("policy") if policy_record else DEFAULT_RULE_POLICY
        )
    except PyMongoError as error:
        logger.exception("Could not load rules for statement recalculation")
        raise APIError(503, "Business rules are unavailable") from error
    financial_analysis = calculate_financial_analysis(
        extracted,
        extracted.get("transactionHistory") or [],
        policy["financial_scoring"],
    )
    bank_statement_analysis = score_bank_statement(
        extracted,
        raw_text=str(extracted.get("rawText") or ""),
        applicant_name=str(statement.get("applicantName") or ""),
        config=policy["bank_statement_scoring"],
        financial_config=policy["financial_scoring"],
    )
    business_rules = evaluate_business_rules(
        statement.get("case_context") or {},
        financial_analysis,
        policy,
    )
    update.update(
        {
            "financial_score": financial_analysis["financial_score"],
            "financial_analysis": financial_analysis,
            "business_rules": business_rules,
            "result.financial_analysis": financial_analysis,
            "result.business_rules": business_rules,
            "bank_statement_analysis": bank_statement_analysis,
            "result.bank_statement_analysis": bank_statement_analysis,
            "result.financial_assessment": {
                **financial_analysis,
                "missing_fields": score["missing_fields"],
                "ocr_confidence": score["ocr_confidence"],
            },
            "result.human_review_required": (
                bool(score["missing_fields"])
                or numeric_confidence < _LOW_CONFIDENCE_THRESHOLD
                or financial_analysis["human_review_required"]
                or business_rules["status"] == "manual_review"
                or bank_statement_analysis["status"]
                != "CONSISTENCY_CHECK_PASSED"
            ),
        }
    )
    needs_review = bool(update["result.human_review_required"]) or bool(statement.get("abaStatementAnalysis") and (statement["abaStatementAnalysis"].get("reviewStatus") != "VERIFIED" or statement["abaStatementAnalysis"].get("reconciliationStatus") != "RECONCILED"))
    update["result.human_review_required"] = needs_review
    update["verification_status"] = "manual_review" if needs_review else "complete"
    update["status"] = update["verification_status"]
    try:
        await database["bank_statements"].update_one(id_query(statement_id), {"$set": update})
    except PyMongoError as error:
        logger.exception("Could not recalculate bank statement score")
        raise APIError(503, "Bank statement score could not be recalculated") from error
    return JSONResponse(
        content={
            "score": score,
            "verification_status": update["verification_status"],
            "document_verification_score": score["document_verification_score"],
            "bank_statement_score": bank_statement_analysis["bankStatementScore"],
            "bank_statement_analysis": bank_statement_analysis,
        }
    )


@router.get("/{statement_id}/transactions")
async def get_bank_statement_transactions(
    statement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    statement = await _load_statement(statement_id, database)
    result = statement.get("result", {})
    analysis = result.get("bank_statement_analysis", {})
    transactions = analysis.get("transactions")
    if not isinstance(transactions, list):
        transactions = result.get("extracted", {}).get("normalizedTransactions", [])
    invalid_transactions = analysis.get("invalidTransactions", [])
    account_number = _account_number_from(statement)
    return JSONResponse(
        content={
            "transactions": _public_analysis(
                transactions,
                {str(account_number)} if account_number else set(),
            ),
            "invalidTransactions": _public_analysis(
                invalid_transactions,
                {str(account_number)} if account_number else set(),
            ),
            "transactionCount": len(transactions),
            "invalidTransactionCount": len(invalid_transactions),
            "pagination": {
                "dataset": "normalized_transactions",
                "pageNumbersReferTo": "source_document_pages",
                "ocrPageCount": (result.get("extracted", {}).get("metrics") or {}).get("page_count"),
            },
        }
    )


async def _aba_case_document(document_id: str, database: AsyncDatabase) -> tuple[dict[str, Any], dict[str, Any]]:
    document = await database["documents"].find_one(id_query(document_id))
    if not document or document.get("status") in {"DELETED", "REPLACED"}:
        raise APIError(404, "Current bank document not found")
    application = await database["visaapplications"].find_one(id_query(str(document.get("applicationId") or "")))
    if not application or application.get("deletedAt"):
        raise APIError(404, "Application not found")
    if not isinstance(document.get("abaStatementAnalysis"), dict):
        raise APIError(409, "Local ABA extraction is not available for this document")
    return document, application


def _aba_response(document: dict[str, Any], application: dict[str, Any]) -> dict[str, Any]:
    return {"analysis": document["abaStatementAnalysis"], "documentId": str(document["_id"]),
            "status": document.get("status"), "selectedPrimary":
                str(application.get("selectedBankDocumentId") or "") == str(document["_id"])
                and application.get("selectedBankAnalysisRevision") == document["abaStatementAnalysis"].get("analysisRevision")}


async def _invalidate_aba_case(database: AsyncDatabase, application: dict[str, Any]) -> None:
    await database["visaapplications"].update_one(
        {"_id": application["_id"]}, {"$set": {"verificationMetricsCurrent": False, "predictionInputsCurrent": False,
            "approvalProbability": None, "approvalProbabilityNormalized": None, "rejectionProbabilityNormalized": None,
            "predictionStatus": "STALE" if application.get("predictionStatus") == "COMPLETED" else application.get("predictionStatus", "UNAVAILABLE"),
            "updatedAt": datetime.now(timezone.utc)}})
    from .documents import _refresh_application_score
    await _refresh_application_score(database, application)


async def _save_aba_document(database: AsyncDatabase, document: dict[str, Any], application: dict[str, Any],
                             analysis: dict[str, Any], status: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    fields = aba_to_legacy_ocr(analysis)["fields"]
    # Compatibility fields must follow the corrected source as well. Their old
    # diagnostics are explicitly stale until recomputed; they never release ABA.
    updated = await database["documents"].update_one(
        {"_id": document["_id"], "abaStatementAnalysis.analysisRevision": document["abaStatementAnalysis"]["analysisRevision"],
         "status": {"$nin": ["DELETED", "REPLACED"]}},
        {"$set": {"abaStatementAnalysis": analysis, "status": status, "workflowStatus": status,
                  "extractedFields": fields, "legacyBankDiagnosticsStale": True,
                  "awardedPoints": 0, "updatedAt": now}})
    if not updated.matched_count:
        raise APIError(409, "Document or analysis changed; reload before reviewing")
    await _invalidate_aba_case(database, application)
    from .documents import _refresh_cross_document_consistency

    await _refresh_cross_document_consistency(database, str(application["_id"]))
    return {**document, "abaStatementAnalysis": analysis, "extractedFields": fields,
            "legacyBankDiagnosticsStale": True, "status": status, "workflowStatus": status, "updatedAt": now}


@router.get("/documents/{document_id}/aba")
async def get_document_aba(document_id: str, database: AsyncDatabase = Depends(get_mongo_database),
                           _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> JSONResponse:
    document, application = await _aba_case_document(document_id, database)
    return JSONResponse(content=_aba_response(document, application), headers={"Cache-Control": "no-store"})


@router.get("/documents/{document_id}/aba/page/{page}")
async def get_document_aba_page(document_id: str, page: int, row: int | None = None,
                                database: AsyncDatabase = Depends(get_mongo_database),
                                _principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> Response:
    document, _ = await _aba_case_document(document_id, database)
    return _render_aba_page(document, page, row)


def _render_aba_page(document: dict[str, Any], page: int, row: int | None = None) -> Response:
    import fitz
    original = Path(str(document.get("storagePath") or "")).resolve()
    render = (_PRIVATE_DIR / f'{document["_id"]}.aba-render.pdf').resolve()
    path = render if render.is_file() else original
    if not path.is_relative_to(_PRIVATE_DIR.resolve()) or not path.is_file():
        raise APIError(404, "Private source file is unavailable")
    with fitz.open(path) as pdf:
        if pdf.needs_pass:
            raise APIError(409, "Retry extraction with the PDF password to enable source images")
        if not 1 <= page <= len(pdf):
            raise APIError(404, "Source page not found")
        source = pdf[page-1]
        if row is not None:
            transaction = next((t for t in document["abaStatementAnalysis"].get("transactions", []) if t["id"] == row and t["page"] == page), None)
            if transaction and transaction.get("bbox"):
                source.draw_rect(fitz.Rect(transaction["bbox"]), color=(.08,.45,.39), width=1.5, fill=(.2,.7,.5), fill_opacity=.12)
        return Response(source.get_pixmap(matrix=fitz.Matrix(1.5,1.5), alpha=False).tobytes("png"), media_type="image/png", headers={"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff"})


@router.post("/documents/{document_id}/aba/corrections")
async def correct_document_aba(document_id: str, payload: dict[str, Any],
                                database: AsyncDatabase = Depends(get_mongo_database),
                                principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> JSONResponse:
    document, application = await _aba_case_document(document_id, database)
    try:
        analysis = apply_correction(document["abaStatementAnalysis"], payload, principal["id"])
    except (ValueError, ArithmeticError) as error:
        raise APIError(409, str(error)) from error
    updated = await _save_aba_document(database, document, application, analysis, "NEEDS_REVIEW")
    return JSONResponse(content=_aba_response(updated, application), headers={"Cache-Control":"no-store"})


@router.post("/documents/{document_id}/aba/review")
async def review_document_aba(document_id: str, payload: dict[str, Any],
                               database: AsyncDatabase = Depends(get_mongo_database),
                               principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> JSONResponse:
    document, application = await _aba_case_document(document_id, database)
    try:
        analysis = reviewed_analysis(document["abaStatementAnalysis"], payload, principal["id"])
    except (ValueError, ArithmeticError) as error:
        raise APIError(409, str(error)) from error
    updated = await _save_aba_document(database, document, application, analysis, analysis["reviewStatus"])
    return JSONResponse(content=_aba_response(updated, application), headers={"Cache-Control":"no-store"})


@router.post("/documents/{document_id}/aba/select-primary")
async def select_primary_document_aba(document_id: str, payload: dict[str, Any],
                                       database: AsyncDatabase = Depends(get_mongo_database),
                                       principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF"))) -> JSONResponse:
    document, application = await _aba_case_document(document_id, database)
    analysis = document["abaStatementAnalysis"]
    if payload.get("analysisRevision") != analysis["analysisRevision"]:
        raise APIError(409, "Analysis changed; reload before selecting")
    reason = str(payload.get("reason") or "").strip()
    if len(reason) < 8:
        raise APIError(400, "A selection reason of at least 8 characters is required")
    if document.get("status") != "VERIFIED" or analysis.get("reviewStatus") != "VERIFIED" or not analysis.get("ownerRelationshipConfirmed") or analysis.get("reconciliationStatus") != "RECONCILED":
        raise APIError(409, "Verify the exact bank evidence and account relationship before selecting it")
    event = {"documentId": document_id, "analysisRevision": analysis["analysisRevision"], "reason": reason,
             "actorId": principal["id"], "at": datetime.now(timezone.utc).isoformat()}
    await database["visaapplications"].update_one({"_id": application["_id"]},
        {"$set": {"selectedBankDocumentId": document_id, "selectedBankAnalysisRevision": analysis["analysisRevision"]},
         "$push": {"bankSelectionHistory": event}})
    await _invalidate_aba_case(database, {**application,"selectedBankDocumentId":document_id})
    fresh = await database["documents"].find_one(id_query(document_id))
    if not fresh or fresh.get("status") != "VERIFIED" or (fresh.get("abaStatementAnalysis") or {}).get("analysisRevision") != analysis["analysisRevision"]:
        raise APIError(409, "Bank evidence changed during selection; reload and review the current revision")
    return JSONResponse(content=_aba_response(document, {**application,"selectedBankDocumentId":document_id,"selectedBankAnalysisRevision":analysis["analysisRevision"]}), headers={"Cache-Control":"no-store"})
