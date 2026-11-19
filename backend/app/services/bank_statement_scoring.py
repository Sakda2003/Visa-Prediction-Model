from __future__ import annotations

import calendar
import heapq
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .financial_analysis import calculate_financial_analysis
from .identity_normalization import name_tokens
from .transaction_normalization import normalize_transactions, parse_amount


REQUIRED_BANK_FIELDS = (
    "bankName",
    "accountHolderName",
    "accountNumber",
    "openingBalance",
    "closingBalance",
    "currency",
    "statementStartDate",
    "statementEndDate",
    "deposits",
    "withdrawals",
    "transactionHistory",
    "averageBalance",
)

DEFAULT_BANK_STATEMENT_SCORING: dict[str, Any] = {
    "minimum_monthly_closing_balance_usd": 4000,
    "full_balance_score_usd": 8000,
    "recent_large_deposit_days": 30,
    "rapid_funds_outflow_days": 7,
    "rapid_funds_outflow_ratio": 0.8,
    "weights": {
        "three_month_balance_stability": 20,
        "transaction_history_quality": 15,
        "source_of_funds_consistency": 10,
        "document_completeness_consistency": 5,
    },
}

_BANK_FIELD_ALIASES = {
    "bankName": ("bank_name", "bank"),
    "accountHolderName": ("account_holder_name", "accountName", "holderName"),
    "accountNumber": ("account_number", "accountNo", "account_no"),
    "openingBalance": ("opening_balance", "beginningBalance", "beginning_balance"),
    "closingBalance": ("closing_balance", "endingBalance", "ending_balance"),
    "currency": ("currency_code", "currencyCode"),
    "statementStartDate": ("statement_start_date", "periodStartDate", "period_start_date"),
    "statementEndDate": ("statement_end_date", "periodEndDate", "period_end_date"),
    "deposits": ("totalCredits", "total_credits", "totalDeposits", "total_deposits"),
    "withdrawals": ("totalDebits", "total_debits", "totalWithdrawals", "total_withdrawals"),
    "transactionHistory": ("transaction_history", "transactions"),
    "averageBalance": ("average_balance",),
    "monthlyClosingBalances": ("monthly_closing_balances",),
    "monthlyBalanceHistory": ("monthly_balance_history", "bankMonthlySummaries"),
}


def normalize_bank_fields(fields: Any) -> dict[str, Any]:
    """Return a non-mutating field map with canonical names for legacy aliases."""
    normalized = dict(fields) if isinstance(fields, dict) else {}
    for canonical, aliases in _BANK_FIELD_ALIASES.items():
        if normalized.get(canonical) not in (None, "", []):
            continue
        for alias in aliases:
            if normalized.get(alias) not in (None, "", []):
                normalized[canonical] = normalized[alias]
                break
    period = normalized.get("statementPeriod") or normalized.get("statement_period")
    if isinstance(period, dict):
        normalized.setdefault(
            "statementStartDate", period.get("startDate") or period.get("start_date")
        )
        normalized.setdefault(
            "statementEndDate", period.get("endDate") or period.get("end_date")
        )
    return normalized


def calculate_document_verification_score(
    fields: dict[str, Any],
    required_fields: tuple[str, ...] = REQUIRED_BANK_FIELDS,
) -> dict[str, Any]:
    fields = normalize_bank_fields(fields)
    present = [
        name
        for name in required_fields
        if fields.get(name) not in (None, "", [])
    ]
    missing = [name for name in required_fields if name not in present]
    score = round(100 * len(present) / len(required_fields)) if required_fields else 0
    return {
        "document_verification_score": score,
        "score_type": "document_field_completeness",
        "required_fields": len(required_fields),
        "extracted_fields": len(present),
        "missing_fields": missing,
        "basis": (
            "Percentage of required bank-statement fields extracted. "
            "This is not a visa approval probability or authenticity result."
        ),
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    number, _, _ = parse_amount(value)
    return number


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    value = value.strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value[:10], pattern).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _month_key(value: Any) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}", value.strip()):
        return value.strip()
    parsed = _date(value)
    return parsed.strftime("%Y-%m") if parsed else None


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + offset
    return index // 12, index % 12 + 1


def _currency(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized in {"$", "US DOLLAR", "US DOLLARS"}:
        return "USD"
    return normalized


def _normalized_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _direction(transaction: dict[str, Any]) -> str | None:
    value = str(transaction.get("direction") or transaction.get("type") or "").strip().casefold()
    if value in {"credit", "deposit", "income", "salary", "cr"}:
        return "credit"
    if value in {"debit", "withdrawal", "expense", "payment", "dr"}:
        return "debit"
    amount = _number(transaction.get("amount"))
    if amount is not None and amount < 0:
        return "debit"
    return None


def _page_duplicates(raw_text: str) -> list[dict[str, Any]]:
    pages = re.split(r"(?m)^--- PAGE (\d+)\s*$", raw_text or "")
    seen: dict[str, int] = {}
    duplicates = []
    for index in range(1, len(pages), 2):
        page_number = int(pages[index])
        content = re.sub(r"[^a-z0-9]+", "", pages[index + 1].casefold())
        if len(content) < 40:
            continue
        previous = seen.get(content)
        if previous is not None:
            duplicates.append(
                {"page": page_number, "duplicate_of_page": previous}
            )
        else:
            seen[content] = page_number
    return duplicates


def analyze_bank_statement(
    fields: dict[str, Any],
    *,
    raw_text: str = "",
    applicant_name: str = "",
    config: dict[str, Any] | None = None,
    financial_config: dict[str, Any] | None = None,
    ocr_complete: bool = True,
) -> dict[str, Any]:
    """Analyze extracted evidence without asserting that the statement is authentic."""
    policy = config or DEFAULT_BANK_STATEMENT_SCORING
    weights = policy.get("weights", DEFAULT_BANK_STATEMENT_SCORING["weights"])
    threshold = float(
        policy.get("minimum_monthly_closing_balance_usd", 4000)
    )
    full_balance_score = float(policy.get("full_balance_score_usd", 8000))
    recent_deposit_days = int(policy.get("recent_large_deposit_days", 30))
    rapid_days = int(policy.get("rapid_funds_outflow_days", 7))
    rapid_ratio = float(policy.get("rapid_funds_outflow_ratio", 0.8))
    fields = normalize_bank_fields(fields)
    source_transactions = (
        fields.get("transactionHistory")
        or fields.get("transaction_history")
        or fields.get("transactions")
        or []
    )
    normalized_result = normalize_transactions(
        source_transactions,
        default_currency=fields.get("currency") or fields.get("currency_code"),
    )
    transactions = normalized_result["transactions"]
    financial = calculate_financial_analysis(
        fields,
        transactions,
        financial_config,
    )
    period = fields.get("statementPeriod") or fields.get("statement_period") or {}
    period_start = _date(
        fields.get("statementStartDate")
        or fields.get("statement_start_date")
        or (period.get("startDate") or period.get("start_date") if isinstance(period, dict) else None)
    )
    period_end = _date(
        fields.get("statementEndDate")
        or fields.get("statement_end_date")
        or (period.get("endDate") or period.get("end_date") if isinstance(period, dict) else None)
    )
    statement_currency = _currency(fields.get("currency") or fields.get("currency_code"))

    month_keys: list[str] = []
    period_complete = False
    if period_start and period_end and period_end >= period_start:
        first_target_year, first_target_month = _shift_month(
            period_end.year, period_end.month, -2
        )
        first_target = date(first_target_year, first_target_month, 1)
        last_day = calendar.monthrange(period_end.year, period_end.month)[1]
        month_keys = [
            f"{_shift_month(period_end.year, period_end.month, offset)[0]:04d}-"
            f"{_shift_month(period_end.year, period_end.month, offset)[1]:02d}"
            for offset in (-2, -1, 0)
        ]
        period_complete = (
            period_start <= first_target
            and period_end.day == last_day
        )

    evidence_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    monthly_balances = fields.get("monthlyClosingBalances") or fields.get("monthly_closing_balances")
    if isinstance(monthly_balances, list):
        for item in monthly_balances:
            if not isinstance(item, dict):
                continue
            key = _month_key(item.get("month") or item.get("date") or item.get("monthEndDate"))
            amount = _number(
                item.get("closingBalance")
                if item.get("closingBalance") is not None
                else item.get("balance")
            )
            if key and amount is not None:
                evidence_by_month[key].append(
                    {
                        "amount": amount,
                        "currency": _currency(item.get("currency") or statement_currency),
                        "date": item.get("date") or item.get("monthEndDate"),
                        "page": item.get("closingPage") or item.get("page"),
                        "sourceText": str(
                            item.get("closingSourceText")
                            or item.get("sourceText")
                            or ""
                        )[:500],
                        "source": "extracted_monthly_closing_balance",
                    }
                )

    if not statement_currency:
        explicit_currencies = {
            item["currency"]
            for entries in evidence_by_month.values()
            for item in entries
            if item["currency"]
        }
        if len(explicit_currencies) == 1:
            statement_currency = explicit_currencies.pop()

    monthly_balance_history = fields.get("monthlyBalanceHistory") or fields.get("monthly_balance_history")
    if not isinstance(monthly_balance_history, list):
        monthly_balance_history = (
            monthly_balances if isinstance(monthly_balances, list) else []
        )
    serialized_monthly_balance_history = []
    for item in monthly_balance_history:
        if not isinstance(item, dict):
            continue
        key = _month_key(item.get("month") or item.get("date"))
        if not key:
            continue
        serialized_monthly_balance_history.append(
            {
                "month": key,
                "openingBalance": _number(item.get("openingBalance")),
                "closingBalance": _number(item.get("closingBalance")),
                "currency": _currency(item.get("currency") or statement_currency)
                or None,
                "openingPage": item.get("openingPage"),
                "closingPage": item.get("closingPage") or item.get("page"),
                "openingSourceText": str(item.get("openingSourceText") or "")[:500],
                "closingSourceText": str(
                    item.get("closingSourceText") or item.get("sourceText") or ""
                )[:500],
            }
        )

    for transaction in transactions:
        if not isinstance(transaction, dict):
            continue
        transaction_date = _date(
            transaction.get("date")
            or transaction.get("transactionDate")
            or transaction.get("transaction_date")
        )
        balance = _number(
            transaction.get("balance")
            if transaction.get("balance") is not None
            else transaction.get("running_balance")
            if transaction.get("running_balance") is not None
            else transaction.get("runningBalance")
        )
        if not transaction_date or balance is None:
            continue
        month_last_day = calendar.monthrange(
            transaction_date.year, transaction_date.month
        )[1]
        if transaction_date.day == month_last_day:
            evidence_by_month.setdefault(
                transaction_date.strftime("%Y-%m"), []
            ).append(
                {
                    "amount": balance,
                    "currency": statement_currency,
                    "date": transaction_date.isoformat(),
                    "page": transaction.get("page"),
                    "sourceText": str(transaction.get("evidence") or transaction.get("source_text") or "")[:500],
                    "source": "month_end_transaction_balance",
                }
            )

    monthly_checks = []
    if month_keys:
        for key in month_keys:
            candidates = evidence_by_month.get(key, [])
            unique_amounts = {
                round(item["amount"], 2)
                for item in candidates
            }
            item = candidates[-1] if candidates else None
            closing_balance = item["amount"] if item else None
            currency = item["currency"] if item else statement_currency
            ambiguous = len(unique_amounts) > 1
            if not period_complete or closing_balance is None or currency != "USD" or ambiguous:
                status = "INSUFFICIENT_DATA"
            elif closing_balance < threshold:
                status = "FAIL"
            else:
                status = "PASS"
            monthly_checks.append(
                {
                    "month": key,
                    "closingBalance": round(closing_balance, 2)
                    if closing_balance is not None
                    else None,
                    "currency": currency or None,
                    "minimumRequired": threshold if currency == "USD" else None,
                    "differenceFromMinimum": round(closing_balance - threshold, 2)
                    if closing_balance is not None and currency == "USD"
                    else None,
                    "status": status,
                    "source": item["source"] if item else None,
                    "sourcePage": item["page"] if item else None,
                    "sourceText": item["sourceText"] if item else "",
                    "ambiguous": ambiguous,
                }
            )

    month_statuses = [item["status"] for item in monthly_checks]
    if any(status == "FAIL" for status in month_statuses):
        financial_requirement_status = "FAIL"
    elif len(monthly_checks) != 3 or any(
        status != "PASS" for status in month_statuses
    ):
        financial_requirement_status = "INSUFFICIENT_DATA"
    else:
        financial_requirement_status = "PASS"
    available_closings = [
        item["closingBalance"]
        for item in monthly_checks
        if item["closingBalance"] is not None
        and item["currency"] == "USD"
    ]

    flags: list[dict[str, Any]] = []

    def add_flag(
        code: str,
        reason: str,
        severity: str = "medium",
        transaction: dict[str, Any] | None = None,
        **details: Any,
    ) -> None:
        flag: dict[str, Any] = {
            "code": code,
            "reason": reason,
            "severity": severity,
            "manualReviewRequired": True,
        }
        if transaction:
            flag.update(
                {
                    "date": transaction.get("date")
                    or transaction.get("transactionDate")
                    or transaction.get("transaction_date"),
                    "amount": _number(transaction.get("amount")),
                    "currency": statement_currency or None,
                    "description": str(transaction.get("description") or "")[:160],
                    "page": transaction.get("page"),
                }
            )
        flag.update(details)
        flags.append(flag)

    if not period_complete:
        add_flag(
            "INCOMPLETE_STATEMENT_PERIOD",
            "The statement does not establish three complete calendar months.",
        )
    if statement_currency != "USD":
        add_flag(
            "CURRENCY_NOT_USD",
            "USD balance thresholds cannot be applied without a verified exchange rate.",
        )
    for item in monthly_checks:
        if item["status"] == "FAIL":
            add_flag(
                "MONTHLY_BALANCE_BELOW_REQUIRED",
                f"{item['month']} closing balance is below the configured USD minimum.",
                month=item["month"],
                closingBalance=item["closingBalance"],
                minimumRequired=item["minimumRequired"],
            )
        elif item["ambiguous"]:
            add_flag(
                "CONFLICTING_MONTHLY_BALANCES",
                f"More than one closing balance was extracted for {item['month']}.",
                month=item["month"],
            )

    holder_name = str(fields.get("accountHolderName") or fields.get("account_holder_name") or "")
    holder_tokens = name_tokens(holder_name)
    applicant_tokens = name_tokens(applicant_name)
    name_reason = ""
    if holder_tokens and applicant_tokens:
        if holder_tokens == applicant_tokens:
            name_status = "MATCHED"
            name_reason = "Name tokens match after case, spacing, punctuation, and ordering normalization."
        else:
            overlap = holder_tokens & applicant_tokens
            shorter = min(len(holder_tokens), len(applicant_tokens))
            if len(overlap) >= 2 and len(overlap) == shorter:
                name_status = "POSSIBLE_MATCH"
                name_reason = "All tokens in the shorter name are present; middle-name or ordering differences require review."
            elif len(overlap) >= 2 and len(overlap) / max(len(holder_tokens), len(applicant_tokens)) >= 0.5:
                name_status = "POSSIBLE_MATCH"
                name_reason = "Several name tokens overlap, but the names are not identical; manual confirmation is required."
            else:
                name_status = "NEEDS_MANUAL_REVIEW"
                name_reason = "The extracted and applicant names differ; this is not a fraud determination."
        if name_status != "MATCHED":
            add_flag(
                "ACCOUNT_HOLDER_NAME_MISMATCH",
                name_reason,
            )
    else:
        name_status = "INSUFFICIENT_DATA"
        name_reason = "Account-holder or applicant name evidence is unavailable for comparison."
        add_flag(
            "ACCOUNT_HOLDER_NAME_UNAVAILABLE",
            name_reason,
            "low",
        )

    duplicate_pages = _page_duplicates(raw_text)
    for duplicate in duplicate_pages:
        add_flag(
            "DUPLICATE_PAGE_CANDIDATE",
            "OCR text for this page duplicates another page; inspect the original file.",
            page=duplicate["page"],
            duplicateOfPage=duplicate["duplicate_of_page"],
        )

    start_date = period_start
    end_date = period_end
    transaction_records = [
        item for item in transactions if isinstance(item, dict)
    ]
    if not transaction_records:
        add_flag(
            "TRANSACTION_HISTORY_UNAVAILABLE",
            "No transaction rows were extracted for source and reconciliation checks.",
        )
    duplicate_keys: dict[tuple[Any, ...], int] = {}
    duplicate_transactions = []
    monthly_summaries: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "deposits": 0.0,
            "withdrawals": 0.0,
            "incomingCount": 0,
            "outgoingCount": 0,
        }
    )
    complete_transaction_count = 0
    source_terms = (
        "salary", "payroll", "wage", "pension", "business income", "invoice",
        "monthly income", "income", "dividend", "rental income",
    )
    source_patterns: dict[str, dict[str, Any]] = {}
    own_account_transfers: list[dict[str, Any]] = []
    parsed_transactions: list[tuple[date | None, float | None, str | None, dict[str, Any]]] = []
    for index, transaction in enumerate(transaction_records):
        tx_date = _date(transaction.get("date") or transaction.get("transactionDate"))
        amount = _number(transaction.get("amount"))
        direction = _direction(transaction)
        description = str(transaction.get("description") or "")
        month = tx_date.strftime("%Y-%m") if tx_date else None
        if tx_date and start_date and end_date and not start_date <= tx_date <= end_date:
            add_flag(
                "TRANSACTION_OUTSIDE_STATEMENT_PERIOD",
                "Transaction date falls outside the stated period.",
                "low",
                transaction,
            )
        if tx_date and amount is not None and direction:
            complete_transaction_count += 1
            summary = monthly_summaries[month]
            key = "deposits" if direction == "credit" else "withdrawals"
            count_key = "incomingCount" if direction == "credit" else "outgoingCount"
            summary[key] += abs(amount)
            summary[count_key] += 1
            if direction == "credit":
                description_folded = description.casefold()
                own_transfer = any(
                    term in description_folded
                    for term in ("own account", "self transfer", "internal transfer", "between my accounts")
                )
                if own_transfer:
                    own_account_transfers.append(
                        {
                            "transactionIndex": index,
                            "date": tx_date.isoformat() if tx_date else None,
                            "amount": round(abs(amount), 2),
                            "description": description,
                            "evidence": transaction.get("evidence") or "",
                            "note": "Description suggests an own-account transfer; confirm account ownership.",
                        }
                    )
                else:
                    labeled_income = any(
                        term in description_folded for term in source_terms
                    )
                    transfer_candidate = any(
                        term in description_folded
                        for term in ("transfer", "wire", "remittance", "standing order")
                    )
                    if labeled_income or transfer_candidate:
                        category = (
                            "salary_or_payroll"
                            if any(term in description_folded for term in ("salary", "payroll", "wage", "pension"))
                            else "business_income"
                            if any(term in description_folded for term in ("business income", "invoice"))
                            else "recurring_transfer_candidate"
                            if transfer_candidate and not labeled_income
                            else "other_labeled_income"
                        )
                        pattern = re.sub(r"\b\d+\b", " ", description_folded)
                        pattern = re.sub(r"[^\w]+", " ", pattern, flags=re.UNICODE)
                        pattern = " ".join(pattern.split()) or category
                        key = f"{category}:{pattern}"
                        source = source_patterns.setdefault(
                            key,
                            {
                                "category": category,
                                "descriptionPattern": pattern,
                                "months": set(),
                                "transactionIndexes": [],
                                "totalAmount": 0.0,
                                "evidence": [],
                            },
                        )
                        if month:
                            source["months"].add(month)
                        source["transactionIndexes"].append(index)
                        source["totalAmount"] += abs(amount)
                        if transaction.get("evidence"):
                            source["evidence"].append(str(transaction["evidence"])[:300])

        transaction_balance = transaction.get("balance")
        if transaction_balance is None:
            transaction_balance = transaction.get("running_balance")
        if transaction_balance is None:
            transaction_balance = transaction.get("runningBalance")
        fingerprint = (
            tx_date.isoformat() if tx_date else None,
            round(abs(amount), 2) if amount is not None else None,
            direction,
            re.sub(r"\s+", " ", description.casefold()).strip(),
            _number(transaction_balance),
        )
        previous_index = duplicate_keys.get(fingerprint)
        if previous_index is not None and fingerprint[3]:
            duplicate_transactions.append(
                {"transactionIndex": index, "duplicateOfIndex": previous_index}
            )
            add_flag(
                "DUPLICATE_TRANSACTION_CANDIDATE",
                "This transaction duplicates another extracted row; inspect the statement.",
                "low",
                transaction,
                duplicateOfIndex=previous_index,
            )
        else:
            duplicate_keys[fingerprint] = index
        parsed_transactions.append((tx_date, abs(amount) if amount is not None else None, direction, transaction))

    large_deposit_threshold = float(
        (financial_config or {}).get("targets", {}).get(
            "large_deposit_threshold_usd", 1000
        )
    )
    transfer_terms = ("transfer", "wire", "remittance", "cash out", "cash withdrawal")
    for tx_date, amount, direction, transaction in parsed_transactions:
        description = str(transaction.get("description") or "").casefold()
        if (
            amount is not None
            and direction == "credit"
            and statement_currency == "USD"
            and amount >= large_deposit_threshold
        ):
            explained = any(term in description for term in source_terms)
            if not explained:
                add_flag(
                    "LARGE_DEPOSIT_SOURCE_REVIEW",
                    "Large incoming deposit; confirm the source and supporting evidence. This is not a fraud finding.",
                    "medium",
                    transaction,
                )
            if tx_date and end_date and 0 <= (end_date - tx_date).days <= recent_deposit_days:
                add_flag(
                    "RECENT_LARGE_DEPOSIT",
                    "Large deposit occurred shortly before statement end; confirm its source.",
                    "medium",
                    transaction,
                )
        if (
            amount is not None
            and amount >= large_deposit_threshold
            and any(term in description for term in transfer_terms)
        ):
            add_flag(
                "TRANSFER_REVIEW",
                "Transfer transaction requires context; this is not a suspicious-activity finding.",
                "low",
                transaction,
            )

    transfer_debits = sorted(
        [
            (tx_date, amount, transaction)
            for tx_date, amount, direction, transaction in parsed_transactions
            if tx_date
            and amount is not None
            and direction == "debit"
            and any(
                term in str(transaction.get("description") or "").casefold()
                for term in transfer_terms
            )
        ],
        key=lambda item: item[0],
    )
    credits = sorted(
        [
            (item[0], item[1])
            for item in parsed_transactions
            if item[0] and item[1] is not None and item[2] == "credit"
        ],
        key=lambda item: item[0],
    )
    transfer_index = 0
    available_transfers: list[tuple[float, int, date, dict[str, Any]]] = []
    for credit_date, credit_amount in credits:
        while (
            transfer_index < len(transfer_debits)
            and transfer_debits[transfer_index][0]
            <= credit_date + timedelta(days=rapid_days)
        ):
            debit_date, debit_amount, debit = transfer_debits[transfer_index]
            heapq.heappush(
                available_transfers,
                (-debit_amount, transfer_index, debit_date, debit),
            )
            transfer_index += 1
        while available_transfers and available_transfers[0][2] < credit_date:
            heapq.heappop(available_transfers)
        if (
            available_transfers
            and -available_transfers[0][0] >= credit_amount * rapid_ratio
        ):
            related_debit = available_transfers[0][3]
            add_flag(
                "RAPID_FUNDS_OUTFLOW",
                "A substantial transfer out followed an incoming deposit within the configured review window; verify context.",
                "medium",
                related_debit,
                relatedDepositDate=credit_date.isoformat(),
            )

    reconciliation_internal = financial["reconciliation"]
    reconciliation = {
        **reconciliation_internal,
        "status": reconciliation_internal.get("statusLabel", "NOT_AVAILABLE"),
        "legacyStatus": reconciliation_internal.get("status"),
    }
    if reconciliation_internal["status"] == "mismatch":
        add_flag(
            "RECONCILIATION_MISMATCH",
            "Opening balance plus credits minus debits differs from the extracted closing balance.",
            expectedClosingBalance=(
                round(
                    (_number(fields.get("openingBalance")) or 0)
                    + (financial["total_deposits"] or 0)
                    - (financial["total_withdrawals"] or 0),
                    2,
                )
                if _number(fields.get("openingBalance")) is not None
                and financial["total_deposits"] is not None
                and financial["total_withdrawals"] is not None
                else None
            ),
            extractedClosingBalance=financial.get("closing_balance"),
            difference=reconciliation.get("delta"),
        )
    elif reconciliation_internal["status"] != "reconciled":
        add_flag(
            "RECONCILIATION_INSUFFICIENT_DATA",
            "Opening balance, closing balance, or transaction totals are incomplete.",
            "low",
        )

    score_components: dict[str, dict[str, Any]] = {}

    def add_component(name: str, ratio: float | None, explanation: str) -> None:
        maximum = float(weights.get(name, 0))
        points = max(0.0, min(maximum, maximum * ratio)) if ratio is not None else None
        score_components[name] = {
            "points": round(points, 2) if points is not None else None,
            "maximum": maximum,
            "available": ratio is not None,
            "explanation": explanation,
            "evidence_ratio": round(ratio, 4) if ratio is not None else None,
        }

    balance_ratio = (
        min(1.0, max(0.0, min(available_closings) / full_balance_score))
        if len(monthly_checks) == 3
        and len(available_closings) == 3
        and period_complete
        and statement_currency == "USD"
        and full_balance_score > 0
        else None
    )
    add_component(
        "three_month_balance_stability",
        balance_ratio,
        "Based on the lowest of the three separately checked monthly closing balances; a high month cannot offset a below-minimum month.",
    )
    transaction_ratio = (
        complete_transaction_count / len(transaction_records)
        if transaction_records and complete_transaction_count
        else None
    )
    if transaction_ratio is not None:
        if reconciliation_internal["status"] == "mismatch":
            transaction_ratio *= 0.5
        if duplicate_transactions:
            transaction_ratio *= max(
                0.0,
                1.0 - len(duplicate_transactions) / len(transaction_records),
            )
    add_component(
        "transaction_history_quality",
        transaction_ratio,
        "Based on dated transactions with known amounts and directions, adjusted for duplicates and reconciliation.",
    )
    recurring_sources = [
        item for item in source_patterns.values() if len(item["months"]) >= 2
    ]
    source_ratio = 1.0 if recurring_sources else None
    add_component(
        "source_of_funds_consistency",
        source_ratio,
        "Uses explicit recurring income descriptions only; unclassified deposits require human context.",
    )
    completeness = calculate_document_verification_score(fields)
    completeness_ratio = (
        completeness["document_verification_score"] / 100
        if completeness["extracted_fields"]
        else None
    )
    if duplicate_pages:
        completeness_ratio *= 0.75
    if name_status == "NEEDS_MANUAL_REVIEW":
        completeness_ratio *= 0.75
    add_component(
        "document_completeness_consistency",
        completeness_ratio,
        "Based on extracted required fields; OCR completeness does not prove authenticity.",
    )
    score_components["three_month_balance_stability"]["evidence"] = {
        "monthly_closing_balances": [
            {"month": item["month"], "closing_balance": item["closingBalance"], "source_page": item["sourcePage"], "source": item["source"]}
            for item in monthly_checks
        ],
        "complete_three_month_period": period_complete,
    }
    score_components["transaction_history_quality"]["evidence"] = {
        "valid_transaction_count": complete_transaction_count,
        "total_transaction_count": len(transaction_records),
        "invalid_transaction_count": len(normalized_result["invalid_transactions"]),
        "duplicate_transaction_count": len(duplicate_transactions),
        "reconciliation_status": reconciliation["status"],
    }
    score_components["source_of_funds_consistency"]["evidence"] = {
        "recurring_source_count": len(recurring_sources),
        "recurring_months": [
            sorted(item["months"]) for item in recurring_sources
        ],
    }
    score_components["document_completeness_consistency"]["evidence"] = {
        "extracted_fields": completeness["extracted_fields"],
        "required_fields": completeness["required_fields"],
        "missing_fields": completeness["missing_fields"],
        "duplicate_page_count": len(duplicate_pages),
        "account_holder_status": name_status,
    }
    bank_score = (
        round(
            sum(component["points"] or 0 for component in score_components.values()), 2
        )
        if ocr_complete
        else None
    )
    bank_score_maximum = sum(
        float(value) for value in weights.values()
    )
    bank_score_available = ocr_complete and any(
        component["available"] for component in score_components.values()
    )
    # Keep the raw 50-point score and its evidence coverage separate. A
    # percentage alone can look deceptively strong when OCR missed components.
    available_points_maximum = sum(
        component["maximum"]
        for component in score_components.values()
        if component["available"]
    )
    evidence_coverage_percentage = (
        round((available_points_maximum / bank_score_maximum) * 100, 2)
        if bank_score_available and bank_score_maximum > 0
        else 0.0
    )
    bank_score_percentage = (
        round((bank_score / bank_score_maximum) * 100, 2)
        if bank_score is not None
        and bank_score_maximum > 0
        and ocr_complete
        and financial_requirement_status != "INSUFFICIENT_DATA"
        and evidence_coverage_percentage >= 80
        else None
    )
    if not ocr_complete:
        for component in score_components.values():
            component["points"] = None
            component["available"] = False
            component["evidence_ratio"] = None
        financial = {
            **financial,
            "financial_score": None,
            "status": "manual_review",
            "human_review_required": True,
        }
        financial_requirement_status = "INSUFFICIENT_DATA"

    if not ocr_complete:
        overall_status = "NEEDS_MANUAL_REVIEW"
    elif financial_requirement_status == "FAIL":
        overall_status = "NEEDS_MANUAL_REVIEW"
    elif financial_requirement_status != "PASS" or flags:
        overall_status = "NEEDS_MANUAL_REVIEW"
    else:
        overall_status = "CONSISTENCY_CHECK_PASSED"
    monthly_transaction_summary = []
    for month_row in financial.get("monthly_summary", []):
        month = month_row["month"]
        check = next((item for item in monthly_checks if item["month"] == month), {})
        values = monthly_summaries.get(month, {})
        monthly_transaction_summary.append(
            {
                "month": month,
                "totalDeposits": month_row.get("credits") if month_row.get("credits") is not None else 0,
                "totalWithdrawals": month_row.get("debits") if month_row.get("debits") is not None else 0,
                "incomingCount": values.get("incomingCount", 0),
                "outgoingCount": values.get("outgoingCount", 0),
                "transactionCount": month_row.get("transaction_count", 0),
                "closingBalance": (
                    check.get("closingBalance")
                    if check.get("closingBalance") is not None
                    else month_row.get("closing_balance")
                ),
                "minimumObservedBalance": month_row.get("minimum_observed_balance"),
                "currency": statement_currency or financial.get("currency"),
                "closingBalanceEvidenceAvailable": bool(
                    check.get("closingBalance") is not None
                    or month_row.get("closing_balance_evidence_available")
                ),
            }
        )

    missing_evidence = []
    if period_start is None:
        missing_evidence.append("statement_start_date")
    if period_end is None:
        missing_evidence.append("statement_end_date")
    if _number(fields.get("openingBalance", fields.get("opening_balance"))) is None:
        missing_evidence.append("opening_balance")
    if _number(fields.get("closingBalance", fields.get("closing_balance"))) is None:
        missing_evidence.append("closing_balance")
    if not transaction_records:
        missing_evidence.append("transaction_history")
    if len(monthly_checks) != 3 or any(item["closingBalance"] is None for item in monthly_checks):
        missing_evidence.append("three_month_closing_balance_evidence")
    if not applicant_name:
        missing_evidence.append("applicant_name_for_account_holder_comparison")
    if not statement_currency:
        missing_evidence.append("statement_currency")

    recurring_source_output = [
        {
            "category": item["category"],
            "descriptionPattern": item["descriptionPattern"],
            "months": sorted(item["months"]),
            "transactionCount": len(item["transactionIndexes"]),
            "totalAmount": round(item["totalAmount"], 2),
            "currency": statement_currency or None,
            "transactionIndexes": item["transactionIndexes"],
            "evidence": item["evidence"],
            "status": "POSSIBLE_MATCH",
            "note": "Recurring description pattern only; verify source documents before treating as income.",
        }
        for item in recurring_sources
    ]
    source_of_funds = {
        "status": "POSSIBLE_MATCH" if recurring_source_output else "INSUFFICIENT_DATA",
        "recurringIncomeCandidates": recurring_source_output,
        "possibleOwnAccountTransfers": own_account_transfers,
        "unexplainedLargeDeposits": [
            item
            for item in financial.get("large_deposits", [])
            if item.get("review_required")
        ],
        "note": "Credits are not automatically classified as salary; transfer sources and income require human confirmation.",
    }

    return {
        "extractionStatus": "EXTRACTION_COMPLETED"
        if fields or raw_text
        else "NEEDS_MANUAL_REVIEW",
        "status": overall_status,
        "financialRequirementStatus": financial_requirement_status,
        "authenticityStatus": "AUTHENTICITY_NOT_CONFIRMED",
        "authenticityVerified": False,
        "accountInformation": {
            "bankName": fields.get("bankName") or fields.get("bank_name"),
            "accountHolderName": holder_name or None,
            "accountNumber": fields.get("accountNumber") or fields.get("account_number") or fields.get("accountNo"),
            "currency": statement_currency or None,
            "openingBalance": financial.get("opening_balance"),
            "closingBalance": financial.get("closing_balance"),
        },
        "statementPeriod": {
            "startDate": period_start.isoformat() if period_start else None,
            "endDate": period_end.isoformat() if period_end else None,
            "completeThreeMonths": period_complete,
            "monthsEvaluated": month_keys,
            "status": "VERIFIED" if period_complete else "INSUFFICIENT_DATA",
            "evidenceSource": "explicit extracted statement-period fields",
        },
        "monthlyBalanceChecks": monthly_checks,
        "monthlyBalanceHistory": serialized_monthly_balance_history,
        "averageMonthlyClosingBalance": round(sum(available_closings) / len(available_closings), 2)
        if available_closings
        else None,
        "lowestMonthlyClosingBalance": min(available_closings)
        if available_closings
        else None,
        "highestMonthlyClosingBalance": max(available_closings)
        if available_closings
        else None,
        "minimumMonthlyClosingBalanceUsd": threshold,
        "transactionSummary": {
            "totalDeposits": financial.get("total_deposits"),
            "totalWithdrawals": financial.get("total_withdrawals"),
            "incomingCount": sum(
                item["incomingCount"] for item in monthly_transaction_summary
            ),
            "outgoingCount": sum(
                item["outgoingCount"] for item in monthly_transaction_summary
            ),
            "monthly": monthly_transaction_summary,
        },
        "transactions": transactions,
        "transactionCount": len(transactions),
        "invalidTransactions": normalized_result["invalid_transactions"],
        "invalidTransactionCount": len(normalized_result["invalid_transactions"]),
        "normalizationWarnings": normalized_result["warnings"],
        "reconciliation": reconciliation,
        "accountHolderCheck": {
            "status": name_status,
            "accountHolderName": holder_name or None,
            "applicantName": applicant_name or None,
            "reason": name_reason,
            "originalExtractedName": holder_name or None,
        },
        "duplicatePages": duplicate_pages,
        "duplicateTransactions": duplicate_transactions,
        "riskFlags": flags,
        "reviewFlags": flags,
        "sourceOfFundsAnalysis": source_of_funds,
        "missingEvidence": list(dict.fromkeys(missing_evidence)),
        "extractionWarnings": list(normalized_result["warnings"]),
        "financialAnalysis": financial,
        "documentVerificationScore": completeness,
        "bankStatementScore": {
            "points": bank_score if bank_score_available else None,
            "maximum": bank_score_maximum,
            "percentage": bank_score_percentage if bank_score_available else None,
            "evidenceCoveragePercentage": evidence_coverage_percentage,
            "available": bank_score_available,
            "assessment": (
                "INSUFFICIENT_DATA"
                if not ocr_complete
                or financial_requirement_status == "INSUFFICIENT_DATA"
                or evidence_coverage_percentage < 80
                else "BELOW_REQUIREMENT"
                if financial_requirement_status == "FAIL"
                else "GOOD"
                if bank_score_percentage is not None and bank_score_percentage >= 80
                else "FAIR"
                if bank_score_percentage is not None and bank_score_percentage >= 65
                else "WEAK"
            ),
            "assessmentNote": (
                "The score is provisional because required statement evidence is missing or less than 80% of the scoring weight is supported."
                if not ocr_complete
                or financial_requirement_status == "INSUFFICIENT_DATA"
                or evidence_coverage_percentage < 80
                else "At least one required monthly closing balance is below the configured minimum."
                if financial_requirement_status == "FAIL"
                else "Score reflects extracted evidence only; authenticity and visa approval are not confirmed."
            ),
            "availablePointsMaximum": available_points_maximum,
            "components": score_components,
            "formula": (
                "20 points monthly balance stability + 15 transaction history "
                "quality + 10 source-of-funds consistency + 5 document "
                "completeness/consistency; each component uses the configured "
                "weight and its evidence ratio."
            ),
        },
        "reviewReasons": list(dict.fromkeys(flag["reason"] for flag in flags)),
        "disclaimer": (
            "Automated OCR and consistency checks only. This report does not "
            "verify authenticity, establish fraud, or predict visa approval."
        ),
    }
