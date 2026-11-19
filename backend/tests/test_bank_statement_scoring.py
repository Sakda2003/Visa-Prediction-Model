from backend.app.api.bank_statements import _analysis_record, _build_result
from backend.app.services.bank_statement_scoring import analyze_bank_statement
from backend.app.services.business_rules import (
    DEFAULT_RULE_POLICY,
    validate_rule_policy,
)


def _statement(
    balances=(4000, 4200, 4400),
    currency="USD",
    closing_balance=5000,
    applicant_name="Sample Applicant",
    transactions=None,
):
    if transactions is None:
        transactions = [
            {
                "date": "2026-02-15",
                "description": "Monthly salary",
                "amount": 2000,
                "type": "credit",
            },
            {
                "date": "2026-03-10",
                "description": "Rent payment",
                "amount": 1000,
                "type": "debit",
            },
        ]
    return {
        "bankName": "Example Bank",
        "accountHolderName": applicant_name,
        "accountNumber": "123456789",
        "openingBalance": 4000,
        "closingBalance": closing_balance,
        "currency": currency,
        "statementStartDate": "2026-01-01",
        "statementEndDate": "2026-03-31",
        "deposits": 2000,
        "withdrawals": 1000,
        "averageBalance": 4200,
        "transactionHistory": transactions,
        "monthlyClosingBalances": [
            {
                "month": month,
                "closingBalance": balance,
                "currency": currency,
                "page": 1,
                "sourceText": f"{month} Closing balance: {balance}",
            }
            for month, balance in zip(
                ("2026-01", "2026-02", "2026-03"), balances
            )
        ],
    }


def test_three_month_threshold_is_inclusive_at_four_thousand_usd():
    result = analyze_bank_statement(_statement())

    assert [item["status"] for item in result["monthlyBalanceChecks"]] == [
        "PASS",
        "PASS",
        "PASS",
    ]
    assert result["financialRequirementStatus"] == "PASS"
    assert result["minimumMonthlyClosingBalanceUsd"] == 4000
    assert result["bankStatementScore"]["maximum"] == 50
    assert result["bankStatementScore"]["available"] is True
    assert result["bankStatementScore"]["percentage"] is not None
    assert 0 <= result["bankStatementScore"]["evidenceCoveragePercentage"] <= 100
    assert result["bankStatementScore"]["assessment"] in {
        "GOOD", "FAIR", "WEAK", "BELOW_REQUIREMENT", "INSUFFICIENT_DATA"
    }
    assert result["authenticityStatus"] == "AUTHENTICITY_NOT_CONFIRMED"


def test_any_month_below_threshold_fails_the_three_month_requirement():
    result = analyze_bank_statement(_statement(balances=(4000, 3999.99, 9000)))

    assert [item["status"] for item in result["monthlyBalanceChecks"]] == [
        "PASS",
        "FAIL",
        "PASS",
    ]
    assert result["financialRequirementStatus"] == "FAIL"
    assert any(
        flag["code"] == "MONTHLY_BALANCE_BELOW_REQUIRED"
        for flag in result["riskFlags"]
    )
    assert result["status"] == "NEEDS_MANUAL_REVIEW"


def test_missing_month_and_non_usd_balances_are_not_assumed_to_pass():
    missing_month = _statement()
    missing_month["monthlyClosingBalances"].pop()
    incomplete = analyze_bank_statement(missing_month)
    non_usd = analyze_bank_statement(_statement(currency="KHR"))

    assert incomplete["financialRequirementStatus"] == "INSUFFICIENT_DATA"
    assert incomplete["bankStatementScore"]["percentage"] is None
    assert incomplete["bankStatementScore"]["assessment"] == "INSUFFICIENT_DATA"
    assert incomplete["monthlyBalanceChecks"][-1]["status"] == "INSUFFICIENT_DATA"
    assert non_usd["financialRequirementStatus"] == "INSUFFICIENT_DATA"
    assert all(
        item["status"] == "INSUFFICIENT_DATA"
        for item in non_usd["monthlyBalanceChecks"]
    )
    assert any(flag["code"] == "CURRENCY_NOT_USD" for flag in non_usd["riskFlags"])


def test_bank_score_is_unavailable_without_any_scoring_evidence():
    result = analyze_bank_statement({})

    assert result["bankStatementScore"]["available"] is False
    assert result["bankStatementScore"]["points"] is None
    assert result["status"] == "NEEDS_MANUAL_REVIEW"


def test_incomplete_ocr_never_returns_a_final_bank_statement_score():
    result = analyze_bank_statement(_statement(), ocr_complete=False)

    assert result["bankStatementScore"]["available"] is False
    assert result["bankStatementScore"]["points"] is None
    assert result["bankStatementScore"]["percentage"] is None
    assert result["bankStatementScore"]["assessment"] == "INSUFFICIENT_DATA"
    assert all(
        component["points"] is None
        for component in result["bankStatementScore"]["components"].values()
    )
    assert result["financialAnalysis"]["financial_score"] is None
    assert result["financialRequirementStatus"] == "INSUFFICIENT_DATA"
    assert result["status"] == "NEEDS_MANUAL_REVIEW"


def test_partial_bank_statement_result_exposes_no_final_verification_scores():
    result = _build_result(
        {
            "status": "partial",
            "failed_pages": [{"page": 2, "error": "OCR timed out"}],
            "confidence": 0.9,
            "fields": _statement(),
            "pages": [
                {"page": 1, "text": "Transactions from page one"},
                {"page": 2, "text": ""},
            ],
            "metrics": {"status": "OCR_PARTIAL", "page_count": 2},
        }
    )
    record = _analysis_record(
        "partial-statement",
        "statement.pdf",
        {
            "applicant_name": "Sample Applicant",
            "visa_type": "Tourist",
            "requirements": {},
            "applicant_context": {},
            "storage_path": "private/statement.pdf",
            "result": result,
        },
    )

    assert result["extracted"]["pages"][1]["page"] == 2
    assert result["score"]["document_verification_score"] is None
    assert result["financial_analysis"]["financial_score"] is None
    assert (
        result["bank_statement_analysis"]["bankStatementScore"]["points"]
        is None
    )
    assert result["human_review_required"] is True
    assert record["extraction_status"] == "partial"
    assert record["financial_score"] is None
    assert record["document_verification_score"] is None


def test_duplicate_pages_and_transactions_are_manual_review_candidates():
    duplicate_transaction = {
        "date": "2026-02-15",
        "description": "Monthly salary",
        "amount": 2000,
        "type": "credit",
    }
    fields = _statement(
        transactions=[
            duplicate_transaction,
            duplicate_transaction.copy(),
        ]
    )
    raw_text = (
        "--- PAGE 1\n"
        + ("Example Bank statement transaction details " * 5)
        + "\n--- PAGE 2\n"
        + ("Example Bank statement transaction details " * 5)
    )

    result = analyze_bank_statement(fields, raw_text=raw_text)
    flag_codes = {item["code"] for item in result["riskFlags"]}

    assert result["duplicatePages"] == [{"page": 2, "duplicate_of_page": 1}]
    assert len(result["duplicateTransactions"]) == 1
    assert "DUPLICATE_PAGE_CANDIDATE" in flag_codes
    assert "DUPLICATE_TRANSACTION_CANDIDATE" in flag_codes
    assert result["authenticityVerified"] is False


def test_large_incoming_deposit_followed_by_transfer_is_a_review_flag():
    result = analyze_bank_statement(
        _statement(
            transactions=[
                {
                    "date": "2026-03-10",
                    "description": "Salary",
                    "amount": 5000,
                    "type": "credit",
                },
                {
                    "date": "2026-03-12",
                    "description": "Wire transfer out",
                    "amount": 4500,
                    "type": "debit",
                },
            ]
        )
    )

    flag = next(
        item
        for item in result["riskFlags"]
        if item["code"] == "RAPID_FUNDS_OUTFLOW"
    )
    assert flag["relatedDepositDate"] == "2026-03-10"
    assert "verify context" in flag["reason"]


def test_reconciliation_mismatch_and_account_name_mismatch_are_flagged():
    fields = _statement(closing_balance=4500, applicant_name="Other Person")
    result = analyze_bank_statement(fields, applicant_name="Sample Applicant")
    flag_codes = {item["code"] for item in result["riskFlags"]}

    assert result["reconciliation"]["status"] == "MISMATCHED"
    assert "RECONCILIATION_MISMATCH" in flag_codes
    assert "ACCOUNT_HOLDER_NAME_MISMATCH" in flag_codes
    assert result["accountHolderCheck"]["status"] == "NEEDS_MANUAL_REVIEW"


def test_transaction_report_preserves_source_ocr_line():
    fields = _statement(
        transactions=[
            {
                "date": "2026-02-15",
                "description": "Monthly salary",
                "amount": "2000.00",
                "type": "credit",
                "page": "4",
                "source_text": "2026-02-15 Monthly salary +2,000.00 6,800.50",
            }
        ]
    )

    result = analyze_bank_statement(fields)

    assert result["transactions"][0]["sourceText"] == (
        "2026-02-15 Monthly salary +2,000.00 6,800.50"
    )


def test_bank_score_weights_are_configurable_but_stay_out_of_fifty():
    policy = validate_rule_policy(DEFAULT_RULE_POLICY)
    changed = {
        **DEFAULT_RULE_POLICY,
        "bank_statement_scoring": {
            **DEFAULT_RULE_POLICY["bank_statement_scoring"],
            "weights": {
                "three_month_balance_stability": 25,
                "transaction_history_quality": 10,
                "source_of_funds_consistency": 10,
                "document_completeness_consistency": 5,
            },
        },
    }

    validated = validate_rule_policy(changed)

    assert policy["bank_statement_scoring"]["minimum_monthly_closing_balance_usd"] == 4000
    assert sum(validated["bank_statement_scoring"]["weights"].values()) == 50
    assert validated["bank_statement_scoring"]["weights"][
        "three_month_balance_stability"
    ] == 25
