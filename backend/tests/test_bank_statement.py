from backend.app.services.bank_statement_scoring import (
    REQUIRED_BANK_FIELDS,
    calculate_document_verification_score,
)


def test_document_verification_score_measures_extracted_field_completeness():
    score = calculate_document_verification_score(
        {
            "bankName": "Example Bank",
            "accountHolderName": "Dara Sample",
            "currency": "USD",
        }
    )

    assert score["document_verification_score"] == round(300 / len(REQUIRED_BANK_FIELDS))
    assert score["score_type"] == "document_field_completeness"
    assert score["missing_fields"] == [
        "accountNumber",
        "openingBalance",
        "closingBalance",
        "statementStartDate",
        "statementEndDate",
        "deposits",
        "withdrawals",
        "transactionHistory",
        "averageBalance",
    ]
    assert "not a visa approval probability" in score["basis"]


def test_empty_fields_do_not_receive_a_financial_or_visa_prediction_score():
    score = calculate_document_verification_score({})

    assert score["document_verification_score"] == 0
    assert len(score["missing_fields"]) == len(REQUIRED_BANK_FIELDS)
