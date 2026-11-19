"""Explainable visa document readiness scoring (not an approval probability)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
import re

TOTAL_WEIGHTS = {
    "passport": 15.0,
    "photo": 5.0,
    "national_id": 10.0,
    "bank_statement": 45.0,
    "supporting_documents": 15.0,
    "consistency": 10.0,
}


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(value[:20], fmt).date()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    import calendar
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _kind(requirement: dict[str, Any]) -> str:
    code = re.sub(r"[^a-z0-9]+", "_", str(requirement.get("code") or requirement.get("requirementCode") or "").casefold()).strip("_")
    name = str(requirement.get("name") or "").casefold()
    value = f"{code} {name}"
    if "photo" in value or "photograph" in value:
        return "photo"
    if "passport" in value:
        return "passport"
    if any(term in value for term in ("national_id", "identity_card", "national identity", "id card")):
        return "national_id"
    if "bank" in value and "statement" in value or "fixed_deposit" in value:
        return "bank_statement"
    if any(term in value for term in ("employment", "business", "marriage", "salary", "support", "sponsor", "tax", "family")):
        return "supporting_documents"
    return "other"


def _doc_quality(document: dict[str, Any] | None) -> float:
    if not document:
        return 0.0
    status = str(document.get("status") or "").upper()
    if status == "VERIFIED":
        return 1.0
    if status in {"NEEDS_REVIEW", "SUSPICIOUS"}:
        return 0.35
    if status in {"REJECTED", "FAILED", "DELETED", "REPLACED"}:
        return 0.0
    if str(document.get("ocrStatus") or "").upper() == "OCR_COMPLETED":
        return 0.65
    return 0.25


def _bank_features(fields: dict[str, Any], as_of: date) -> dict[str, Any]:
    currency = str(fields.get("currency") or fields.get("currency_code") or "").upper()
    start = _date(fields.get("statementStartDate") or fields.get("statement_start_date"))
    end = _date(fields.get("statementEndDate") or fields.get("statement_end_date"))
    transactions = fields.get("transactionHistory") or fields.get("transactions") or []
    transactions = [x for x in transactions if isinstance(x, dict)] if isinstance(transactions, list) else []
    txs = []
    for tx in transactions:
        dt = _date(tx.get("date") or tx.get("transactionDate") or tx.get("transaction_date"))
        amount = _num(tx.get("amount"))
        direction = str(tx.get("direction") or tx.get("type") or "").casefold()
        if amount is not None and (direction in {"credit", "deposit", "income", "salary", "cr"} or amount < 0):
            direction = "credit" if amount >= 0 else "debit"
        elif direction in {"debit", "withdrawal", "expense", "payment", "dr"}:
            direction = "debit"
        else:
            direction = "unknown"
        if dt and amount is not None and direction in {"credit", "debit"}:
            tx_balance = tx.get("balance")
            if tx_balance is None:
                tx_balance = tx.get("runningBalance")
            if tx_balance is None:
                tx_balance = tx.get("running_balance")
            explicit_source = tx.get("counterparty") or tx.get("sender") or tx.get("source") or tx.get("from")
            txs.append({"date": dt, "amount": abs(amount), "direction": direction, "balance": _num(tx_balance), "source": str(explicit_source or tx.get("description") or "Unknown"), "explicitSource": str(explicit_source).strip().casefold() if explicit_source else None})
    txs.sort(key=lambda x: x["date"])
    usd = currency in {"USD", "$", "US DOLLAR", "US DOLLARS"}
    balances_by_day: dict[date, float] = {}
    opening_value = fields.get("openingBalance")
    if opening_value is None:
        opening_value = fields.get("opening_balance")
    running = _num(opening_value)
    if start and running is not None:
        balances_by_day[start] = running
    for tx in txs:
        if tx["balance"] is not None:
            running = tx["balance"]
        elif running is not None:
            running += tx["amount"] if tx["direction"] == "credit" else -tx["amount"]
        if running is not None:
            balances_by_day[tx["date"]] = running
    daily: list[float] = []
    if start and end and end >= start and balances_by_day:
        current = None
        cursor = start
        while cursor <= end:
            if cursor in balances_by_day:
                current = balances_by_day[cursor]
            else:
                earlier = [d for d in balances_by_day if d <= cursor]
                if earlier:
                    current = balances_by_day[max(earlier)]
            if current is not None:
                daily.append(current)
            cursor += timedelta(days=1)
    total_period_days = (end - start).days + 1 if start and end and end >= start else 0
    coverage = (sum(v >= 4000 for v in daily) / total_period_days) if daily and total_period_days and usd else None
    daily_complete = bool(total_period_days and len(daily) == total_period_days)
    minimum = min(daily) if daily and usd and daily_complete else None
    average = sum(daily) / len(daily) if daily and usd and daily_complete else None
    days_old = (as_of - end).days if end else None
    period_days = (end - start).days + 1 if start and end and end >= start else 0
    credits = [x for x in txs if x["direction"] == "credit"]
    debits = [x for x in txs if x["direction"] == "debit"]
    total_in = sum(x["amount"] for x in credits)
    total_out = sum(x["amount"] for x in debits)
    passthrough_amount = 0.0
    passthrough_count = 0
    for credit in credits:
        if credit["amount"] < 1000:
            continue
        outflow = sum(x["amount"] for x in debits if credit["date"] <= x["date"] <= credit["date"] + timedelta(days=3))
        if outflow >= 0.9 * credit["amount"]:
            passthrough_amount += credit["amount"]
            passthrough_count += 1
    passthrough_ratio = min(1.0, passthrough_amount / total_in) if total_in else None
    source_totals: dict[str, float] = defaultdict(float)
    for tx in credits:
        if tx.get("explicitSource"):
            source_totals[tx["explicitSource"]] += tx["amount"]
    concentration = max(source_totals.values()) / total_in if total_in and source_totals else None
    income_months = len({x["date"].strftime("%Y-%m") for x in credits})
    savings_rate = (total_in - total_out) / total_in if total_in else None
    return {
        "coverageRatio": coverage,
        "minimumBalance": minimum,
        "averageDailyBalance": average,
        "statementAgeDays": days_old,
        "periodDays": period_days,
        "passThroughRatio": passthrough_ratio,
        "passThroughCount": passthrough_count,
        "sourceConcentration": concentration,
        "incomeMonths": income_months if credits else None,
        "savingsRate": savings_rate,
        "currency": currency or None,
        "dailyBalanceSamples": len(daily),
        "dailyBalanceCompletenessRatio": round(len(daily) / total_period_days, 4) if total_period_days else None,
        "transactionCount": len(txs),
    }


def calculate_readiness_score(
    requirements: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    applicant: dict[str, Any] | None = None,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Calculate the user's 100-point weighted readiness rubric with a hard 40% gate."""
    today = as_of or date.today()
    applicant = applicant or {}
    docs_by_requirement = {str(d.get("requirementId") or ""): d for d in documents if isinstance(d, dict)}
    grouped: dict[str, list[tuple[dict[str, Any], dict[str, Any] | None]]] = defaultdict(list)
    missing_required: list[str] = []
    passport_expired = False
    passport_expiry: date | None = None
    for req in requirements:
        if not isinstance(req, dict) or req.get("required", True) is False:
            continue
        rid = str(req.get("requirementId") or req.get("_id") or "")
        doc = docs_by_requirement.get(rid)
        kind = _kind(req)
        if doc is None or str(doc.get("status") or "").upper() in {"DELETED", "REPLACED", "REJECTED", "FAILED"}:
            missing_required.append(str(req.get("name") or kind))
        # Unclassified requirements do not receive weighted points, but they
        # still count as required evidence for the hard gate.
        if kind == "other":
            continue
        grouped[kind].append((req, doc))
        if kind == "passport" and doc:
            fields = doc.get("extractedFields") if isinstance(doc.get("extractedFields"), dict) else {}
            passport_expiry = _date(fields.get("expiryDate") or fields.get("expiry_date") or applicant.get("passportExpiryDate"))
            if passport_expiry and passport_expiry < _add_months(today, 6):
                passport_expired = True
    components: dict[str, dict[str, Any]] = {}
    for kind, weight in (("passport",15), ("photo",5), ("national_id",10), ("supporting_documents",15)):
        rows = grouped.get(kind, [])
        ratio = sum(_doc_quality(doc) for _, doc in rows) / len(rows) if rows else None
        points = weight * ratio if ratio is not None else 0.0
        components[kind] = {"points": round(points, 2), "maximum": weight, "ratio": round(ratio, 4) if ratio is not None else None, "documents": [{"name": req.get("name"), "status": (doc or {}).get("status", "MISSING"), "quality": _doc_quality(doc)} for req, doc in rows]}
    bank_rows = grouped.get("bank_statement", [])
    bank_doc = next((doc for _, doc in bank_rows if doc), None)
    bank_req = next((req for req, doc in bank_rows if doc is bank_doc), {})
    bank_fields = bank_doc.get("extractedFields") if bank_doc and isinstance(bank_doc.get("extractedFields"), dict) else {}
    bank_kind_code = str(bank_doc.get("requirementCode") or bank_req.get("code") or "").casefold().replace("-", "_") if bank_doc else ""
    is_fixed_deposit = "fixed_deposit" in bank_kind_code or "fixed deposit" in str(bank_req.get("name") or "").casefold()
    features = _bank_features(bank_fields, today) if bank_doc else _bank_features({}, today)
    if bank_doc and is_fixed_deposit and not (bank_fields.get("transactionHistory") or bank_fields.get("transactions")):
        deposit_amount = _num(bank_fields.get("depositAmount") or bank_fields.get("deposit_amount"))
        deposit_currency = str(bank_fields.get("currency") or "").upper()
        issued = _date(bank_fields.get("documentDate") or bank_fields.get("issueDate"))
        maturity = _date(bank_fields.get("maturityDate") or bank_fields.get("maturity_date"))
        usable = deposit_amount if deposit_currency in {"USD", "$", "US DOLLAR", "US DOLLARS"} else None
        features.update({
            "coverageRatio": None,
            "minimumBalance": usable,
            "averageDailyBalance": usable,
            "statementAgeDays": (today - issued).days if issued else None,
            "periodDays": (maturity - issued).days if maturity and issued and maturity >= issued else 0,
            "passThroughRatio": None, "passThroughCount": 0,
            "sourceConcentration": None, "incomeMonths": None,
            "savingsRate": None, "currency": deposit_currency or None,
            "dailyBalanceSamples": 0, "transactionCount": 0,
            "fixedDepositAmount": deposit_amount, "maturityDate": maturity.isoformat() if maturity else None,
        })
    bank_feature_defs = [
        ("coverage",12, min(features["coverageRatio"] / 0.9, 1) if features["coverageRatio"] is not None else None),
        ("minimum_balance",6, min(max(features["minimumBalance"] or 0, 0) / 4000, 1) if features["minimumBalance"] is not None else None),
        ("average_balance",6, min(max(features["averageDailyBalance"] or 0, 0) / 4000, 1) if features["averageDailyBalance"] is not None else None),
        ("recency",5, 1.0 if features["statementAgeDays"] is not None and 0 <= features["statementAgeDays"] <= 30 else 0.0 if features["statementAgeDays"] is not None else None),
        ("period",3, 1.0 if features["periodDays"] >= 90 else 0.0 if features["periodDays"] else None),
        ("pass_through",6, 1 - min(features["passThroughRatio"] / 0.5, 1) if features["passThroughRatio"] is not None else None),
        ("source_concentration",3, 1 - min(features["sourceConcentration"], 1) if features["sourceConcentration"] is not None else None),
        ("income_regularity",2, min((features["incomeMonths"] or 0) / 3, 1) if features["incomeMonths"] is not None else None),
        ("savings_rate",2, min(max(features["savingsRate"] or 0, 0), 1) if features["savingsRate"] is not None else None),
    ]
    bank_points = sum(weight * max(0, min(1, ratio)) for _, weight, ratio in bank_feature_defs if ratio is not None)
    raw_field_evidence = bank_doc.get("ocrFieldEvidence") or bank_doc.get("fieldEvidence") or {} if bank_doc else {}
    field_confidence = {
        str(field): {"confidence": _num(evidence.get("confidence")), "source": evidence.get("source")}
        for field, evidence in raw_field_evidence.items()
        if isinstance(evidence, dict)
    } if isinstance(raw_field_evidence, dict) else {}
    reconciliation_status = str(((bank_doc.get("bankStatementAnalysis") or {}).get("reconciliation") or {}).get("status") or "NOT_AVAILABLE").upper() if bank_doc else "NOT_AVAILABLE"
    components["bank_statement"] = {
        "points": round(bank_points, 2), "maximum": 45,
        "features": [{"name": name, "maximum": weight, "ratio": round(ratio, 4) if ratio is not None else None, "points": round(weight * max(0, min(1, ratio)), 2) if ratio is not None else None} for name, weight, ratio in bank_feature_defs],
        "fieldConfidence": field_confidence,
        "balanceReconciliationStatus": reconciliation_status,
        "evidence": features,
        "ocrConfidence": (bank_doc.get("ocrConfidence") if bank_doc else None),
        "manualReviewRequired": bool(bank_doc and (
            ((not is_fixed_deposit) and (features["dailyBalanceCompletenessRatio"] != 1 or features["transactionCount"] == 0)) or not features["currency"]
            or (bank_doc.get("ocrConfidence") is not None and _num(bank_doc.get("ocrConfidence")) is not None and _num(bank_doc.get("ocrConfidence")) < 0.75)
            or str((bank_doc.get("bankStatementAnalysis") or {}).get("reconciliation", {}).get("status") or "").upper() in {"MISMATCH", "MISMATCHED"}
        )),
        "manualReviewReason": "OCR confidence is low, balance evidence is incomplete, or reconciliation needs review." if bank_doc and (
            ((not is_fixed_deposit) and (features["dailyBalanceCompletenessRatio"] != 1 or features["transactionCount"] == 0)) or not features["currency"]
            or (bank_doc.get("ocrConfidence") is not None and _num(bank_doc.get("ocrConfidence")) is not None and _num(bank_doc.get("ocrConfidence")) < 0.75)
            or str((bank_doc.get("bankStatementAnalysis") or {}).get("reconciliation", {}).get("status") or "").upper() in {"MISMATCH", "MISMATCHED"}
        ) else None,
    }
    # Cross-check names, dates of birth, passport numbers and nationality when both sides exist.
    applicant_values = {
        "fullName": applicant.get("fullName"),
        "dateOfBirth": applicant.get("dateOfBirth"),
        "passportNumber": applicant.get("passportNumber"),
        "nationality": applicant.get("nationality"),
    }
    compared, matches = 0, 0
    consistency_checks: list[dict[str, Any]] = []
    identity_fields = {"passport", "national_id", "supporting_documents", "bank_statement"}
    for kind in identity_fields:
        for _, doc in grouped.get(kind, []):
            fields = doc.get("extractedFields") if doc and isinstance(doc.get("extractedFields"), dict) else {}
            candidates = {
                "fullName": fields.get("fullName") or fields.get("accountHolderName") or fields.get("account_holder_name"),
                "dateOfBirth": fields.get("dateOfBirth") or fields.get("date_of_birth"),
                "passportNumber": fields.get("passportNumber") or fields.get("passport_number"),
                "nationality": fields.get("nationality"),
            }
            for field, candidate in candidates.items():
                expected = applicant_values.get(field)
                if expected in (None, "") or candidate in (None, ""):
                    continue
                if field == "dateOfBirth":
                    expected_value = _date(expected)
                    candidate_value = _date(candidate)
                    is_match = bool(expected_value and candidate_value and expected_value == candidate_value)
                else:
                    is_match = _norm(expected) == _norm(candidate)
                compared += 1
                matches += int(is_match)
                consistency_checks.append({"field": field, "documentType": kind, "status": "MATCH" if is_match else "MISMATCH"})
    consistency_ratio = matches / compared if compared else None
    components["consistency"] = {"points": round(10 * consistency_ratio, 2) if consistency_ratio is not None else 0.0, "maximum": 10, "ratio": round(consistency_ratio, 4) if consistency_ratio is not None else None, "comparisons": compared, "matches": matches, "checks": consistency_checks}
    raw_score = round(sum(item["points"] for item in components.values()), 2)
    gate_reasons = []
    if missing_required:
        gate_reasons.append("MISSING_REQUIRED_DOCUMENTS")
    if passport_expired:
        gate_reasons.append("PASSPORT_EXPIRES_WITHIN_SIX_MONTHS")
    final_score = min(raw_score, 40.0) if gate_reasons else raw_score
    return {
        "scoreType": "READINESS_SCORE",
        "label": "ពិន្ទុត្រៀមខ្លួន (Readiness Score)",
        "score": round(final_score, 2),
        "rawScoreBeforeGate": raw_score,
        "maximum": 100,
        "riskLevel": "HIGH" if final_score < 50 else "MEDIUM" if final_score < 75 else "LOW",
        "gateApplied": bool(gate_reasons),
        "gateReasons": gate_reasons,
        "missingRequiredDocuments": missing_required,
        "passportExpiryDate": passport_expiry.isoformat() if passport_expiry else None,
        "manualReviewRequired": bool(
            any(kind == "passport" for kind in grouped) and passport_expiry is None
            or components["bank_statement"].get("manualReviewRequired")
        ),
        "reviewFlags": (["PASSPORT_EXPIRY_DATE_UNAVAILABLE"] if any(kind == "passport" for kind in grouped) and passport_expiry is None else []) + (["BANK_STATEMENT_EVIDENCE_REQUIRES_REVIEW"] if components["bank_statement"].get("manualReviewRequired") else []),
        "components": components,
        "recommendations": _recommendations(components, missing_required, gate_reasons),
        "disclaimer": "This is a document readiness score only, not a visa approval probability or guarantee. Embassy decisions depend on current rules and human review.",
    }


def _recommendations(components: dict[str, Any], missing: list[str], gates: list[str]) -> list[str]:
    tips = [f"Submit required document: {name}." for name in missing]
    if "PASSPORT_EXPIRES_WITHIN_SIX_MONTHS" in gates:
        tips.append("Renew the passport or confirm the destination's current passport-validity rule before applying.")
    bank = components.get("bank_statement", {})
    if bank.get("points", 0) < 30:
        tips.append("Review the bank statement period, daily balance evidence, income sources, and large deposits with supporting proof.")
    if bank.get("manualReviewRequired"):
        tips.append("Have a staff member inspect OCR confidence and balance reconciliation before relying on the bank score.")
    if components.get("consistency", {}).get("ratio") is None:
        tips.append("Provide readable identity details so names can be cross-checked.")
    elif components["consistency"]["ratio"] < 1:
        tips.append("Resolve differences in names or identity details across documents.")
    return tips
