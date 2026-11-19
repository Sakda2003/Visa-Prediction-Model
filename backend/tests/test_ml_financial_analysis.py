from pathlib import Path

import pandas as pd
import pytest

from backend.app.api.bank_statements import _case_context, _public_analysis
from backend.app.api.mongo_helpers import public_document
from backend.app.services.business_rules import (
    DEFAULT_RULE_POLICY,
    evaluate_business_rules,
    validate_rule_policy,
)
from backend.app.services.financial_analysis import calculate_financial_analysis
from backend.app.services.ml_training import (
    MODEL_FEATURES,
    load_training_dataset,
    predict_with_artifact,
    train_model,
    validate_training_dataset,
)


def _visa_applicant(**updates):
    return {
        "destination_country": "Japan",
        "nationality": "Cambodian",
        "has_overstay_history": False,
        "passport_expiry_date": "2035-01-01",
        "previous_visa_denials": 0,
        **updates,
    }


def test_financial_analysis_reconciles_statement_and_flags_large_deposit_without_fraud_label():
    result = calculate_financial_analysis(
        {
            "currency": "USD",
            "openingBalance": 1000,
            "closingBalance": 2600,
            "statementStartDate": "2025-01-01",
            "statementEndDate": "2025-02-01",
        },
        [
            {
                "date": "2025-01-15",
                "amount": 1700,
                "type": "credit",
                "description": "Cash deposit",
                "balance": 2700,
            },
            {
                "date": "2025-01-20",
                "amount": 100,
                "type": "debit",
                "description": "Payment",
                "balance": 2600,
            },
        ],
    )

    assert result["reconciliation"]["status"] == "reconciled"
    assert result["total_deposits"] == 1700
    assert result["total_withdrawals"] == 100
    assert result["large_deposits"][0]["review_required"] is True
    assert "not a fraud" in result["large_deposits"][0]["note"].lower()
    assert result["human_review_required"] is True


def test_foreign_applicant_may_provide_either_verified_employment_or_business_evidence():
    result = evaluate_business_rules(
        _visa_applicant(
            destination_country="Australia",
            nationality="Canadian",
            documents=[
                {"type": "employment_letter", "status": "PENDING"},
                {"type": "business_evidence", "status": "VERIFIED"},
            ],
        ),
        {"closing_balance": 5000, "currency": "USD"},
    )

    evidence_check = next(
        check
        for check in result["checks"]
        if check["code"] == "employment_or_business_evidence"
    )
    assert evidence_check["status"] == "pass"


def test_unknown_nationality_does_not_apply_foreigner_balance_threshold():
    result = evaluate_business_rules(
        _visa_applicant(nationality=""),
        {"closing_balance": 10000, "currency": "USD"},
    )

    assert any(check["code"] == "applicant_nationality" for check in result["checks"])
    assert not any(check["code"] == "destination_balance" for check in result["checks"])
    assert result["status"] == "manual_review"


def test_china_case_stays_manual_until_screening_fields_are_confirmed():
    result = evaluate_business_rules(
        _visa_applicant(destination_country="China"),
        {"closing_balance": 5000, "currency": "USD"},
    )

    checks = {check["code"]: check for check in result["checks"]}
    assert checks["first_time_female_18_30"]["status"] == "manual_review"
    assert checks["married_applicant"]["status"] == "manual_review"


def test_risk_adjustments_are_separate_from_financial_score_and_model_probability():
    result = evaluate_business_rules(
        _visa_applicant(
            travel_history_count=0,
            employment_letter=False,
            business_evidence=False,
            sponsor_support=True,
            fixed_deposit=True,
        ),
        {"closing_balance": 1000, "currency": "USD"},
    )

    risk = result["risk_assessment"]
    assert risk["score"] == 65
    assert risk["risk_category"] == "LOW"
    assert len(risk["adjustments"]) == 5
    assert "not combined" in risk["model_overlap_policy"].lower()


def test_missing_financial_and_applicant_data_does_not_produce_low_risk_scores():
    financial = calculate_financial_analysis({})
    rules = evaluate_business_rules({}, {})

    assert financial["financial_score"] is None
    assert financial["risk_category"] == "INSUFFICIENT_DATA"
    assert rules["risk_assessment"]["score"] is None
    assert rules["risk_assessment"]["risk_category"] == "INSUFFICIENT_DATA"


def test_public_document_masks_account_number_in_fields_and_ocr_text():
    response = public_document(
        {
            "_id": "document-1",
            "ocrText": "Account Number: 123456789012",
            "extractedFields": {
                "accountNumber": "123456789012",
                "rawText": "Account Number: 123456789012",
            },
        }
    )

    assert response["extractedFields"]["accountNumber"] == "********9012"
    assert "123456789012" not in response["extractedFields"]["rawText"]
    assert "ocrText" not in response


def test_bank_statement_public_response_masks_account_number_from_raw_ocr_text():
    response = _public_analysis(
        {
            "extracted": {
                "accountNumber": "123456789012",
                "rawText": "Account Number: 123456789012",
            }
        },
        {"123456789012"},
    )

    assert response["extracted"]["accountNumber"] == "********9012"
    assert "123456789012" not in response["extracted"]["rawText"]


def test_case_context_does_not_persist_unapproved_personal_fields():
    context = _case_context(
        {
            "nationality": "Cambodian",
            "travel_history_count": 2,
            "accountNumber": "123456789",
            "passport_number": "P12345",
            "documents": [
                {"type": "employment_letter", "status": "VERIFIED", "rawText": "private"}
            ],
        }
    )

    assert context["nationality"] == "Cambodian"
    assert context["travel_history_count"] == 2
    assert "accountNumber" not in context
    assert "passport_number" not in context
    assert context["documents"] == [
        {"type": "employment_letter", "status": "VERIFIED"}
    ]


def test_visa_training_rejects_workflow_status_labels():
    frame = pd.DataFrame(
        {
            "group_id": ["group-a"],
            "visa_outcome": ["PENDING"],
            "travel_history_count": [1],
        }
    )

    with pytest.raises(ValueError, match="Workflow statuses"):
        train_model(
            "visa",
            frame,
            b"test-only",
            minimum_rows=1,
            minimum_per_class=1,
            label_mapping={"PENDING": "APPROVED"},
        )


def test_training_cannot_lower_the_500_record_minimum():
    frame = pd.DataFrame(
        {
            "group_id": ["group-1"],
            "visa_outcome": ["APPROVED"],
            "travel_history_count": [1],
        }
    )

    with pytest.raises(ValueError, match="at least 500 are required"):
        train_model(
            "visa",
            frame,
            b"test-only",
            minimum_rows=1,
            minimum_per_class=1,
            label_mapping={"APPROVED": "APPROVED"},
        )


def test_visa_dataset_accepts_outcome_aliases_and_stable_application_ids():
    frame = pd.DataFrame(
        {
            "Application ID": ["app-1", "app-2", "app-3", "app-4"],
            "Visa Status": ["Visa Granted", "refused", " approved ", "DENIED"],
            "Travel History Count": [2, 0, 3, 1],
        }
    )
    mapping = {"APPROVED": "APPROVED", "REJECTED": "REJECTED"}

    normalized, report = validate_training_dataset(
        frame,
        "visa",
        minimum_rows=4,
        minimum_per_class=2,
        label_mapping=mapping,
    )
    repeated, _ = validate_training_dataset(
        frame,
        "visa",
        minimum_rows=4,
        minimum_per_class=2,
        label_mapping=mapping,
    )

    assert report["ready_to_train"] is True
    assert report["target_column_used"] == "visa_status"
    assert report["group_id_source_columns"] == ["application_id"]
    assert report["approved_count"] == 2
    assert report["rejected_count"] == 2
    assert normalized["visa_outcome"].tolist() == [
        "APPROVED",
        "REJECTED",
        "APPROVED",
        "REJECTED",
    ]
    assert normalized["group_id"].tolist() == repeated["group_id"].tolist()
    assert all(value.startswith("group_") for value in normalized["group_id"])
    assert "app-1" not in normalized["group_id"].tolist()


@pytest.mark.parametrize(
    "target_column",
    (
        "visa_outcome",
        "visa_status",
        "visa_decision",
        "decision_status",
        "decision",
        "outcome",
    ),
)
@pytest.mark.parametrize(
    "group_column",
    ("group_id", "applicant_id", "application_id", "source_record_id"),
)
def test_visa_dataset_canonicalizes_supported_target_and_group_aliases(
    target_column, group_column
):
    frame = pd.DataFrame(
        {
            target_column: [" approved ", "REJECTED"],
            group_column: ["person-1", "person-2"],
            "travel_history_count": [1, 2],
        }
    )

    normalized, report = validate_training_dataset(
        frame,
        "visa",
        minimum_rows=2,
        minimum_per_class=1,
        label_mapping={"APPROVED": "APPROVED", "REJECTED": "REJECTED"},
    )

    assert report["ready_to_train"] is True
    assert report["target_column_used"] == target_column
    assert report["group_id_source_columns"] == [group_column]
    assert normalized["visa_outcome"].tolist() == ["APPROVED", "REJECTED"]
    assert normalized["group_id"].nunique() == 2
    assert report["feature_columns"] == ["travel_history_count"]
    assert report["group_counts"] == {"APPROVED": 1, "REJECTED": 1}


def test_visa_dataset_rejects_missing_identifiers_unknown_labels_and_duplicates():
    frame = pd.DataFrame(
        {
            "Visa Outcome": ["APPROVED", "maybe", None, "REJECTED"],
            "Travel History Count": [1, 0, 2, 1],
        }
    )

    _, report = validate_training_dataset(
        frame,
        "visa",
        minimum_rows=1,
        minimum_per_class=1,
        label_mapping={"APPROVED": "APPROVED", "REJECTED": "REJECTED"},
    )

    assert report["validation_status"] == "invalid"
    assert report["ready_to_train"] is False
    assert report["missing_columns"] == ["group_id or a valid source ID column"]
    assert report["missing_labels"] == 1
    assert report["invalid_record_count"] == 4
    assert any("Unknown visa_outcome label 'MAYBE'" in issue for record in report["invalid_records"] for issue in record["issues"])

    duplicates = pd.DataFrame(
        {
            "application_id": ["app-1", "app-1", "app-2", "app-3"],
            "visa_outcome": ["APPROVED", "REJECTED", "APPROVED", "REJECTED"],
            "travel_history_count": [1, 2, 3, 4],
        }
    )
    _, duplicate_report = validate_training_dataset(
        duplicates,
        "visa",
        minimum_rows=4,
        minimum_per_class=1,
        label_mapping={"APPROVED": "APPROVED", "REJECTED": "REJECTED"},
    )
    assert duplicate_report["duplicate_records"]["count"] == 2
    assert duplicate_report["ready_to_train"] is False


def test_validation_counts_only_rows_without_errors_as_valid_and_reports_features():
    frame = pd.DataFrame(
        {
            "application_id": ["app-1", "app-1", "app-2", "app-3", "app-4"],
            "visa_outcome": ["APPROVED", "REJECTED", "APPROVED", "REJECTED", "unknown"],
            "travel_history_count": [1, 2, 3, 4, 5],
        }
    )

    _, report = validate_training_dataset(
        frame,
        "visa",
        minimum_rows=2,
        minimum_per_class=1,
        label_mapping={"APPROVED": "APPROVED", "REJECTED": "REJECTED"},
    )

    assert report["total_records"] == 5
    assert report["valid_records"] == 2
    assert report["invalid_record_count"] == 3
    assert report["approved_count"] == 1
    assert report["rejected_count"] == 1
    assert report["feature_columns"] == ["travel_history_count"]
    assert report["group_counts"] == {"APPROVED": 1, "REJECTED": 1}
    assert report["duplicate_records"]["count"] == 2
    assert any("Unknown visa_outcome label 'UNKNOWN'" in issue for record in report["invalid_records"] for issue in record["issues"])


def test_validator_requires_distinct_groups_per_class():
    frame = pd.DataFrame(
        {
            "applicant_id": ["person-a", "person-a", "person-b", "person-c"],
            "application_id": ["app-1", "app-2", "app-3", "app-4"],
            "visa_outcome": ["APPROVED", "APPROVED", "REJECTED", "REJECTED"],
            "travel_history_count": [1, 2, 3, 4],
        }
    )

    _, report = validate_training_dataset(
        frame,
        "visa",
        minimum_rows=4,
        minimum_per_class=2,
        label_mapping={"APPROVED": "APPROVED", "REJECTED": "REJECTED"},
    )

    assert report["valid_records"] == 4
    assert report["group_counts"] == {"APPROVED": 1, "REJECTED": 2}
    assert report["validation_status"] == "insufficient_data"
    assert report["ready_to_train"] is False


def test_visa_dataset_rejects_ambiguous_targets_labels_and_invalid_numeric_features():
    mapping = {"APPROVED": "APPROVED", "REJECTED": "REJECTED"}
    ambiguous_targets = pd.DataFrame(
        {
            "application_id": ["app-1", "app-2"],
            "visa_outcome": ["APPROVED", "REJECTED"],
            "visa_status": ["REJECTED", "APPROVED"],
            "travel_history_count": [1, 2],
        }
    )
    _, target_report = validate_training_dataset(
        ambiguous_targets,
        "visa",
        minimum_rows=2,
        minimum_per_class=1,
        label_mapping=mapping,
    )

    assert target_report["validation_status"] == "invalid"
    assert any("Ambiguous target columns" in message for message in target_report["messages"])
    assert target_report["ready_to_train"] is False

    invalid_rows = pd.DataFrame(
        {
            "application_id": ["app-1", "app-2"],
            "visa_outcome": ["granted", "rejected"],
            "travel_history_count": ["many", 2],
        }
    )
    _, label_report = validate_training_dataset(
        invalid_rows,
        "visa",
        minimum_rows=2,
        minimum_per_class=1,
        label_mapping={"GRANTED": "REJECTED", **mapping},
    )
    assert any(
        "Ambiguous outcome label 'GRANTED'" in issue
        for record in label_report["invalid_records"]
        for issue in record["issues"]
    )
    assert any(
        "Invalid numeric value for 'travel_history_count'" in issue
        for record in label_report["invalid_records"]
        for issue in record["issues"]
    )
    assert label_report["ready_to_train"] is False


def test_validator_flags_invalid_categorical_types_and_missing_feature_threshold():
    invalid_type = pd.DataFrame(
        {
            "applicant_id": ["person-1", "person-2"],
            "visa_outcome": ["APPROVED", "REJECTED"],
            "travel_history_count": [1, 2],
            "destination_country": ["Japan", 123],
        }
    )
    _, type_report = validate_training_dataset(
        invalid_type,
        "visa",
        minimum_rows=2,
        minimum_per_class=1,
        label_mapping={"APPROVED": "APPROVED", "REJECTED": "REJECTED"},
    )
    assert type_report["validation_status"] == "invalid"
    assert any(
        "Invalid categorical value for 'destination_country'" in issue
        for record in type_report["invalid_records"]
        for issue in record["issues"]
    )

    missing_feature = pd.DataFrame(
        {
            "applicant_id": ["person-1", "person-2"],
            "visa_outcome": ["APPROVED", "REJECTED"],
            "travel_history_count": [None, None],
        }
    )
    _, missing_report = validate_training_dataset(
        missing_feature,
        "visa",
        minimum_rows=1,
        minimum_per_class=1,
        label_mapping={"APPROVED": "APPROVED", "REJECTED": "REJECTED"},
    )
    assert missing_report["invalid_features"] == ["travel_history_count"]
    assert missing_report["valid_records"] == 0
    assert missing_report["invalid_record_count"] == 2


def test_dataset_import_normalizes_headers_for_csv_and_excel():
    import io

    frame = pd.DataFrame(
        {"Application ID": ["app-1"], "Visa Outcome": ["approved"]}
    )
    excel = io.BytesIO()
    frame.to_excel(excel, index=False)
    loaded_excel = load_training_dataset("records.xlsx", excel.getvalue())
    loaded_csv = load_training_dataset(
        "records.csv", b"Application ID,Visa Outcome\napp-1,approved\n"
    )

    assert list(loaded_excel.columns) == ["application_id", "visa_outcome"]
    assert list(loaded_csv.columns) == ["application_id", "visa_outcome"]


@pytest.mark.parametrize("algorithm", ("lightgbm", "random_forest"))
def test_visa_training_uses_grouped_holdout_calibration_and_returns_review_only_probability(
    algorithm,
):
    rows = []
    for index in range(500):
        rows.append(
            {
                "group_id": f"pseudonym-{index}",
                "visa_outcome": "APPROVED" if index % 2 == 0 else "REJECTED",
                "travel_history_count": 4 if index % 2 == 0 else 0,
                "destination_country": "Japan",
            }
        )
    frame = pd.DataFrame(rows)
    mapping = {"APPROVED": "APPROVED", "REJECTED": "REJECTED"}

    artifact, metadata = train_model(
        "visa",
        frame,
        b"test-only synthetic fixture",
        minimum_rows=1,
        minimum_per_class=1,
        label_mapping=mapping,
        algorithm=algorithm,
    )
    features = {
        name: frame.iloc[0].get(name)
        for name in MODEL_FEATURES["visa"]["numeric"]
        + MODEL_FEATURES["visa"]["categorical"]
        if name in artifact["features"]
    }
    prediction = predict_with_artifact(artifact, "visa", features)

    assert metadata["calibration_row_count"] > 0
    assert metadata["test_row_count"] > 0
    assert metadata["algorithm"] == (
        "LightGBM" if algorithm == "lightgbm" else "Random Forest"
    )
    assert {"precision", "recall", "f1", "roc_auc", "pr_auc", "brier_score"} <= set(
        metadata["metrics"]
    )
    assert prediction["prediction_status"] == "human_review_required"
    assert 0 <= prediction["visa_approval_probability"] <= 1
    assert prediction["explanation"]


@pytest.mark.parametrize("algorithm", ("lightgbm", "random_forest"))
def test_financial_model_trains_risk_and_score_only_with_verified_labels(algorithm):
    risk_levels = ("LOW", "MEDIUM", "HIGH")
    rows = []
    for index in range(500):
        risk = risk_levels[index % len(risk_levels)]
        score = {"LOW": 80, "MEDIUM": 55, "HIGH": 25}[risk]
        rows.append(
            {
                "group_id": f"financial-pseudonym-{index}",
                "financial_risk": risk,
                "financial_score": score,
                "average_daily_balance_usd": score * 50,
                "currency": "USD",
            }
        )
    frame = pd.DataFrame(rows)

    artifact, metadata = train_model(
        "financial",
        frame,
        b"test-only synthetic fixture",
        minimum_rows=1,
        minimum_per_class=1,
        label_mapping={risk: risk for risk in risk_levels},
        algorithm=algorithm,
    )
    features = {
        name: frame.iloc[0].get(name)
        for name in MODEL_FEATURES["financial"]["numeric"]
        + MODEL_FEATURES["financial"]["categorical"]
        if name in artifact["features"]
    }
    prediction = predict_with_artifact(artifact, "financial", features)

    assert metadata["test_row_count"] > 0
    assert metadata["algorithm"] == (
        "LightGBM" if algorithm == "lightgbm" else "Random Forest"
    )
    assert metadata["metrics"]["financial_score_mae"] >= 0
    assert metadata["metrics"]["financial_score_rmse"] >= 0
    assert prediction["prediction_status"] == "human_review_required"
    assert 0 <= prediction["financial_score"] <= 100
    assert prediction["financial_risk_category"] in risk_levels
    assert prediction["explanation"]


def test_model_inference_rejects_missing_live_features():
    with pytest.raises(ValueError, match="requires verified values"):
        predict_with_artifact(
            {"features": ["travel_history_count"]},
            "visa",
            {},
        )


def test_training_template_has_unique_headers_and_both_model_targets():
    template = (
        Path(__file__).resolve().parents[2]
        / "templates"
        / "ml_training_dataset_template.csv"
    )
    headers = template.read_text(encoding="utf-8").splitlines()[0].split(",")

    assert len(headers) == len(set(headers))
    assert {"group_id", "financial_risk", "financial_score", "visa_outcome"} <= set(
        headers
    )


def test_rule_policy_keeps_default_country_and_risk_configuration_valid():
    result = evaluate_business_rules(
        _visa_applicant(),
        {"closing_balance": 4000, "currency": "USD"},
        DEFAULT_RULE_POLICY,
    )
    assert result["risk_assessment"]["risk_category"] in {"LOW", "MEDIUM", "HIGH"}


@pytest.mark.parametrize(
    ("policy", "message"),
    [
        ({"universal_rules": {"minimum_passprt_validity_months": 12}}, "unsupported fields"),
        ({"version": True}, "positive integer"),
        (
            {"financial_scoring": {"targets": {"unknown_target": 1}}},
            "define every target",
        ),
    ],
)
def test_rule_policy_rejects_misspelled_or_invalid_configuration(policy, message):
    with pytest.raises(ValueError, match=message):
        validate_rule_policy(policy)
