from __future__ import annotations

import hashlib
import io
import math
import re
from typing import Any

import numpy as np


MODEL_FEATURES: dict[str, dict[str, tuple[str, ...]]] = {
    "financial": {
        "numeric": (
            "average_daily_balance_usd",
            "monthly_income_usd",
            "monthly_expenses_usd",
            "total_deposits_usd",
            "total_withdrawals_usd",
            "transaction_count",
            "large_deposit_count",
            "balance_stability_cv",
            "reconciliation_delta_usd",
            "statement_duration_months",
            "savings_growth_rate",
            "employment_income_evidence",
            "account_history_months",
        ),
        "categorical": ("currency",),
    },
    "visa": {
        "numeric": (
            "travel_history_count",
            "previous_visa_denials",
            "has_overstay_history",
            "employment_evidence",
            "business_evidence",
            "sponsor_support",
            "fixed_deposit",
            "financial_score",
            "average_daily_balance_usd",
            "monthly_income_usd",
            "monthly_expenses_usd",
            "transaction_count",
            "document_completeness_score",
            "account_history_months",
            "proof_of_ties_to_cambodia",
            "aba_statement_strength",
            "aba_statement_quality",
            "aba_average_daily_balance_usd",
            "aba_ending_balance_usd",
            "aba_money_in_usd",
            "aba_money_out_usd",
            "aba_statement_days",
            "evidence_strength_score",
        ),
        "categorical": (
            "destination_country",
            "visa_category",
            "nationality",
            "employment_status",
        ),
    },
}

MINIMUM_TRAINING_ROWS = 500
MINIMUM_ROWS_PER_CLASS = 50
_MIN_ROWS_DEFAULT = MINIMUM_TRAINING_ROWS
_MIN_PER_CLASS_DEFAULT = MINIMUM_ROWS_PER_CLASS
_MISSING_LIMIT = 0.5
_TARGET_COLUMN_ALIASES = {
    "visa": (
        "visa_outcome",
        "visa_status",
        "visa_decision",
        "decision_status",
        "decision",
        "outcome",
    ),
    "financial": (
        "financial_risk",
        "financial_risk_category",
        "risk_category",
        "risk_level",
    ),
}
_GROUP_ID_COLUMNS = (
    "group_id",
    "applicant_group_id",
    "applicant_id",
    "customer_id",
    "person_id",
    "application_id",
    "source_record_id",
    "record_id",
    "case_id",
)
_RECORD_ID_COLUMNS = (
    "application_id",
    "source_record_id",
    "record_id",
    "case_id",
)
_VISA_OUTCOME_ALIASES = {
    "APPROVED": "APPROVED",
    "APPROVE": "APPROVED",
    "GRANTED": "APPROVED",
    "GRANT": "APPROVED",
    "VISA GRANTED": "APPROVED",
    "VISA APPROVED": "APPROVED",
    "APPROVED VISA": "APPROVED",
    "ISSUED": "APPROVED",
    "VISA ISSUED": "APPROVED",
    "REJECTED": "REJECTED",
    "REJECT": "REJECTED",
    "REFUSED": "REJECTED",
    "REFUSE": "REJECTED",
    "DENIED": "REJECTED",
    "DECLINED": "REJECTED",
    "VISA DENIED": "REJECTED",
    "VISA REFUSED": "REJECTED",
    "NOT APPROVED": "REJECTED",
}
_FINANCIAL_LABEL_ALIASES = {
    "LOW": "LOW",
    "LOW RISK": "LOW",
    "MEDIUM": "MEDIUM",
    "MEDIUM RISK": "MEDIUM",
    "HIGH": "HIGH",
    "HIGH RISK": "HIGH",
}
_VISA_WORKFLOW_LABELS = {
    "PENDING",
    "IN REVIEW",
    "UNDER REVIEW",
    "UPLOADED",
    "VERIFIED",
    "REJECTED DOCUMENT",
    "COMPLETE",
    "COMPLETED",
    "CANCELLED",
    "WITHDRAWN",
}


def _normalize_column_name(value: Any) -> str:
    text = str(value).replace("\ufeff", "").strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").casefold()


def _normalize_columns(frame):
    normalized = [_normalize_column_name(column) for column in frame.columns]
    duplicates = sorted(
        {column for column in normalized if normalized.count(column) > 1}
    )
    if duplicates:
        raise ValueError(
            "Training dataset contains duplicate column names after normalization: "
            + ", ".join(duplicates)
            + "."
        )
    result = frame.copy()
    result.columns = normalized
    return result


def _normalize_label(value: Any) -> str:
    text = str(value).strip().upper()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", text)).strip()


def _identifier_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(float(value)):
            return None
        if float(value).is_integer():
            text = str(int(value))
        else:
            text = str(value)
    else:
        text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "null", "<na>", "n/a"}:
        return None
    return text


def _is_missing_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().casefold() in {
            "nan",
            "none",
            "null",
            "<na>",
            "n/a",
        }
    try:
        import pandas as pd

        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    return bool(missing) if isinstance(missing, (bool, np.bool_)) else False


def _stable_group_id(value: str) -> str:
    return "group_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _normalize_label_mapping(
    model_type: str, label_mapping: dict[str, str] | None
) -> dict[str, str]:
    allowed_labels = (
        {"APPROVED", "REJECTED"}
        if model_type == "visa"
        else {"LOW", "MEDIUM", "HIGH"}
    )
    aliases = (
        _VISA_OUTCOME_ALIASES
        if model_type == "visa"
        else _FINANCIAL_LABEL_ALIASES
    )
    mapping = label_mapping or {}
    if not isinstance(mapping, dict):
        raise ValueError("An explicit label mapping object is required.")
    normalized: dict[str, str] = {}
    for source, target in mapping.items():
        source_label = _normalize_label(source)
        target_label = _normalize_label(target)
        canonical_target = aliases.get(target_label, target_label)
        if not source_label or canonical_target not in allowed_labels:
            raise ValueError(
                "Label mapping contains an empty source or unsupported target label."
            )
        if model_type == "visa" and source_label in _VISA_WORKFLOW_LABELS:
            raise ValueError(
                "Workflow statuses cannot be used as verified visa outcome labels."
            )
        if source_label in normalized and normalized[source_label] != canonical_target:
            raise ValueError(f"Ambiguous label mapping for '{source_label}'.")
        normalized[source_label] = canonical_target
    return normalized


def validate_training_dataset(
    frame,
    model_type: str,
    minimum_rows: int = _MIN_ROWS_DEFAULT,
    minimum_per_class: int = _MIN_PER_CLASS_DEFAULT,
    label_mapping: dict[str, str] | None = None,
) -> tuple[Any, dict[str, Any]]:
    import pandas as pd

    if model_type not in MODEL_FEATURES:
        raise ValueError("model_type must be 'visa' or 'financial'.")
    target_column = "visa_outcome" if model_type == "visa" else "financial_risk"
    allowed_labels = (
        ("APPROVED", "REJECTED")
        if model_type == "visa"
        else ("LOW", "MEDIUM", "HIGH")
    )
    aliases = (
        _VISA_OUTCOME_ALIASES
        if model_type == "visa"
        else _FINANCIAL_LABEL_ALIASES
    )
    mapping = _normalize_label_mapping(model_type, label_mapping)
    data = _normalize_columns(frame)
    total_records = len(data)
    row_issues: dict[int, list[str]] = {}
    missing_columns: list[str] = []
    messages: list[str] = []
    missing_label_rows: list[int] = []

    target_candidates = [
        column
        for column in _TARGET_COLUMN_ALIASES[model_type]
        if column in data.columns
    ]
    if not target_candidates:
        missing_columns.append(target_column)
        missing_label_rows.extend(range(2, total_records + 2))
        messages.append(
            f"Missing required target column '{target_column}' (accepted headers: "
            + ", ".join(_TARGET_COLUMN_ALIASES[model_type])
            + ")."
        )
        for position in range(total_records):
            row_issues.setdefault(position, []).append(
                f"Missing required target column '{target_column}'."
            )
    elif len(target_candidates) > 1:
        messages.append(
            "Ambiguous target columns were provided: "
            + ", ".join(target_candidates)
            + ". Keep only one outcome column."
        )
    group_columns = [column for column in _GROUP_ID_COLUMNS if column in data.columns]
    if not group_columns:
        missing_columns.append("group_id or a valid source ID column")
        messages.append(
            "Missing applicant grouping ID. Provide group_id, applicant_id, "
            "application_id, or source_record_id."
        )

    features = MODEL_FEATURES[model_type]
    supported_features = list(features["numeric"] + features["categorical"])
    available_features = [
        column for column in supported_features if column in data.columns
    ]
    if not available_features:
        messages.append(
            "No supported feature columns were found. Add at least one recognized model feature."
        )
        for position in range(total_records):
            row_issues.setdefault(position, []).append(
                "No supported model feature columns are available."
            )
    if model_type == "financial" and "financial_score" not in data.columns:
        missing_columns.append("financial_score")
        messages.append("Financial training requires the 'financial_score' column.")
        for position in range(total_records):
            row_issues.setdefault(position, []).append(
                "Missing required column 'financial_score'."
            )

    canonical_outcomes: list[str | None] = [None] * total_records
    if len(target_candidates) == 1:
        source_column = target_candidates[0]
        for position, value in enumerate(data[source_column].tolist()):
            row_number = position + 2
            token = _normalize_label(value) if not _is_missing_value(value) else ""
            if not token:
                missing_label_rows.append(row_number)
                row_issues.setdefault(position, []).append(
                    f"Missing {target_column} label."
                )
                continue
            if model_type == "visa" and token in _VISA_WORKFLOW_LABELS:
                row_issues.setdefault(position, []).append(
                    f"Workflow status '{token}' is not a visa decision outcome."
                )
                continue
            alias_label = aliases.get(token)
            mapped_label = mapping.get(token)
            if alias_label and mapped_label and alias_label != mapped_label:
                row_issues.setdefault(position, []).append(
                    f"Ambiguous outcome label '{token}' conflicts with its supported alias."
                )
            elif mapped_label or alias_label:
                canonical_outcomes[position] = mapped_label or alias_label
            else:
                row_issues.setdefault(position, []).append(
                    f"Unknown {target_column} label '{token}'."
                )
    elif len(target_candidates) > 1:
        for position in range(total_records):
            row_issues.setdefault(position, []).append(
                "Multiple target columns make the outcome ambiguous."
            )

    groups: list[str | None] = [None] * total_records
    for position, (_, row) in enumerate(data.iterrows()):
        raw_group_id = next(
            (
                identifier
                for column in group_columns
                if (identifier := _identifier_value(row[column])) is not None
            ),
            None,
        )
        if raw_group_id is None:
            row_issues.setdefault(position, []).append(
                "Missing valid group_id or source record identifier."
            )
        else:
            groups[position] = _stable_group_id(raw_group_id)

    duplicate_positions: set[int] = set()
    record_id_columns = [
        column for column in _RECORD_ID_COLUMNS if column in data.columns
    ]
    for column in record_id_columns:
        identifiers = data[column].map(_identifier_value)
        duplicate_positions.update(
            index
            for index, is_duplicate in enumerate(
                identifiers.duplicated(keep=False).tolist()
            )
            if is_duplicate and identifiers.iloc[index] is not None
        )
    normalized = data.copy()
    if target_candidates:
        normalized[target_column] = canonical_outcomes
    normalized["group_id"] = groups

    invalid_features: list[str] = []
    bounded_features = {
        "aba_statement_strength": (1, 10), "aba_statement_quality": (0, 1),
        "evidence_strength_score": (0, 100), "document_completeness_score": (0, 100),
    }
    for column in available_features:
        if column in features["numeric"]:
            for position, value in enumerate(data[column].tolist()):
                if not _is_missing_value(value) and math.isnan(
                    _numeric_or_nan(value)
                ):
                    row_issues.setdefault(position, []).append(
                        f"Invalid numeric value for '{column}'."
                    )
            normalized[column] = normalized[column].map(_numeric_or_nan)
            if column in bounded_features:
                lower, upper = bounded_features[column]
                invalid_bounds = normalized[column].notna() & ~normalized[column].between(lower, upper)
                for position in np.flatnonzero(invalid_bounds.to_numpy()):
                    row_issues.setdefault(int(position), []).append(f"'{column}' must be between {lower} and {upper} in its declared feature units.")
        else:
            for position, value in enumerate(data[column].tolist()):
                if not _is_missing_value(value) and not isinstance(value, str):
                    row_issues.setdefault(position, []).append(
                        f"Invalid categorical value for '{column}'; expected text."
                    )
            normalized[column] = normalized[column].map(
                lambda value: _category_or_nan(value)
                if _is_missing_value(value) or isinstance(value, str)
                else np.nan
            )
        if normalized[column].isna().mean() > _MISSING_LIMIT:
            invalid_features.append(column)
            for position, value in enumerate(normalized[column].tolist()):
                if _is_missing_value(value):
                    row_issues.setdefault(position, []).append(
                        f"Feature '{column}' exceeds the 50% missing-value limit."
                    )
    if invalid_features:
        messages.append(
            "Too many missing or invalid values (>50%) in features: "
            + ", ".join(invalid_features)
            + "."
        )

    if model_type == "financial" and "financial_score" in normalized:
        scores = normalized["financial_score"].map(_numeric_or_nan)
        invalid_scores = scores.isna() | ~scores.between(0, 100)
        for position in np.flatnonzero(invalid_scores.to_numpy()):
            row_issues.setdefault(int(position), []).append(
                "financial_score must be a number from 0 to 100."
            )
        normalized["financial_score"] = scores

    exact_duplicate_mask = (
        normalized.astype("string").fillna("").duplicated(keep=False).tolist()
    )
    duplicate_positions.update(
        index for index, is_duplicate in enumerate(exact_duplicate_mask) if is_duplicate
    )
    for position in duplicate_positions:
        row_issues.setdefault(position, []).append("Duplicate source record.")

    valid_positions = [
        position for position in range(total_records) if position not in row_issues
    ]
    valid_records = len(valid_positions)
    class_counts = {
        label: sum(canonical_outcomes[index] == label for index in valid_positions)
        for label in allowed_labels
    }
    group_counts = {
        label: len(
            {
                groups[index]
                for index in valid_positions
                if canonical_outcomes[index] == label and groups[index] is not None
            }
        )
        for label in allowed_labels
    }
    if model_type == "visa" and any(class_counts[label] == 0 for label in allowed_labels):
        messages.append("Visa training requires both APPROVED and REJECTED outcomes.")
    if model_type == "financial" and any(
        class_counts[label] == 0 for label in allowed_labels
    ):
        messages.append("Financial training requires LOW, MEDIUM, and HIGH risk labels.")
    if valid_records < minimum_rows:
        messages.append(
            f"Insufficient data: {valid_records} valid records; at least {minimum_rows} are required."
        )
    insufficient_classes = [
        label
        for label in allowed_labels
        if class_counts[label] < minimum_per_class
        or group_counts[label] < minimum_per_class
    ]
    if insufficient_classes:
        messages.append(
            "Insufficient class balance: each class needs at least "
            f"{minimum_per_class} valid rows and distinct applicant groups "
            f"({', '.join(insufficient_classes)} are below the threshold)."
        )

    invalid_records = [
        {"row": position + 2, "issues": issues}
        for position, issues in sorted(row_issues.items())
    ]
    schema_errors = not available_features
    errors = bool(
        missing_columns
        or invalid_records
        or invalid_features
        or schema_errors
        or len(target_candidates) > 1
    )
    insufficient = valid_records < minimum_rows or bool(insufficient_classes)
    target_row_count = sum(value is not None for value in canonical_outcomes)
    report_status = (
        "invalid"
        if errors or any("Ambiguous target columns" in message for message in messages)
        else "insufficient_data"
        if insufficient or any("requires both" in message or "requires LOW" in message for message in messages)
        else "valid"
    )
    report = {
        "model_type": model_type,
        "validation_status": report_status,
        "ready_to_train": report_status == "valid",
        "total_records": int(total_records),
        "valid_records": int(valid_records),
        "labeled_records": int(target_row_count),
        "approved_count": int(class_counts.get("APPROVED", 0)),
        "rejected_count": int(class_counts.get("REJECTED", 0)),
        "class_counts": {key: int(value) for key, value in class_counts.items()},
        "group_counts": {key: int(value) for key, value in group_counts.items()},
        "missing_labels": len(missing_label_rows),
        "missing_label_rows": missing_label_rows[:100],
        "missing_columns": missing_columns,
        "duplicate_records": {
            "count": len(duplicate_positions),
            "rows": [position + 2 for position in sorted(duplicate_positions)[:100]],
        },
        "invalid_record_count": len(invalid_records),
        "invalid_records": invalid_records[:100],
        "feature_columns": available_features,
        "invalid_features": invalid_features,
        "target_column_used": target_candidates[0] if len(target_candidates) == 1 else None,
        "group_id_source_columns": group_columns,
        "minimum_records": int(minimum_rows),
        "minimum_rows_per_class": int(minimum_per_class),
        "messages": messages,
        "message": "Dataset is ready for training."
        if report_status == "valid"
        else " ".join(messages) or "Dataset validation failed.",
    }
    return normalized, report


def load_training_dataset(filename: str, content: bytes):
    import pandas as pd

    suffix = filename.rsplit(".", 1)[-1].casefold() if "." in filename else ""
    if suffix == "csv":
        frame = pd.read_csv(io.BytesIO(content))
    elif suffix in {"xlsx", "xls"}:
        frame = pd.read_excel(io.BytesIO(content))
    else:
        raise ValueError("Training dataset must be CSV or Excel (.xlsx/.xls).")
    if frame.empty:
        raise ValueError("Training dataset contains no records.")
    if len(frame.columns) > 100 or len(frame) > 100_000:
        raise ValueError("Training dataset exceeds the supported size.")
    return _normalize_columns(frame)


def train_model(
    model_type: str,
    frame,
    dataset_bytes: bytes,
    minimum_rows: int = _MIN_ROWS_DEFAULT,
    minimum_per_class: int = _MIN_PER_CLASS_DEFAULT,
    label_mapping: dict[str, str] | None = None,
    algorithm: str = "lightgbm",
) -> tuple[dict[str, Any], dict[str, Any]]:
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (
        accuracy_score,
        average_precision_score,
        brier_score_loss,
        log_loss,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )
    from sklearn.model_selection import GroupShuffleSplit, StratifiedGroupKFold
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    if algorithm not in {"lightgbm", "random_forest", "logistic_regression"}:
        raise ValueError("algorithm must be lightgbm, random_forest, or logistic_regression.")
    if algorithm == "logistic_regression" and model_type != "visa":
        raise ValueError("Logistic regression is a visa-outcome classifier, not a financial-score regressor.")
    minimum_rows = max(MINIMUM_TRAINING_ROWS, minimum_rows)
    minimum_per_class = max(MINIMUM_ROWS_PER_CLASS, minimum_per_class)
    target_column = "financial_risk" if model_type == "financial" else "visa_outcome"
    data, validation = validate_training_dataset(
        frame,
        model_type,
        minimum_rows,
        minimum_per_class,
        label_mapping,
    )
    if not validation["ready_to_train"]:
        raise ValueError(validation["message"])
    features = MODEL_FEATURES[model_type]
    feature_columns = list(features["numeric"] + features["categorical"])
    feature_schema_version = "visa-aba-evidence-v1" if model_type == "visa" and any(name in data.columns for name in ("aba_statement_strength", "evidence_strength_score")) else "visa-legacy-v1" if model_type == "visa" else "financial-legacy-v1"
    if feature_schema_version == "visa-aba-evidence-v1" and any(name not in data.columns for name in ("destination_country", "visa_category")):
        raise ValueError("ABA evidence models require destination_country and visa_category for supported-cohort validation.")
    available_features = [name for name in feature_columns if name in data.columns and not (feature_schema_version == "visa-aba-evidence-v1" and name == "nationality")]
    numeric_features = [name for name in features["numeric"] if name in available_features]
    categorical_features = [
        name for name in features["categorical"] if name in available_features
    ]

    counts = data[target_column].value_counts().to_dict()
    class_groups = data.groupby(target_column, observed=False)["group_id"].nunique().to_dict()

    feature_columns = available_features
    numeric_features = [name for name in features["numeric"] if name in feature_columns]
    categorical_features = [
        name for name in features["categorical"] if name in feature_columns
    ]
    selected = data[feature_columns].copy()
    missing_rates = selected.isna().mean()
    excessive_missing = [
        str(column)
        for column, rate in missing_rates.items()
        if rate > _MISSING_LIMIT
    ]
    if excessive_missing:
        raise ValueError(
            "Too many missing values (>50%) in features: "
            + ", ".join(excessive_missing)
            + "."
        )
    for column in numeric_features:
        selected[column] = selected[column].map(_numeric_or_nan)
    for column in categorical_features:
        selected[column] = selected[column].map(_category_or_nan)
    if not selected.columns.tolist():
        raise ValueError("No model features are available.")
    missing_rates = selected.isna().mean()
    excessive_missing = [
        str(column)
        for column, rate in missing_rates.items()
        if rate > _MISSING_LIMIT
    ]
    if excessive_missing:
        raise ValueError(
            "Too many missing or invalid values (>50%) in features: "
            + ", ".join(excessive_missing)
            + "."
        )

    labels = data[target_column].astype(str)
    groups = data["group_id"].astype(str)
    classes = sorted(labels.unique())
    split = None
    for train_index, test_index in StratifiedGroupKFold(
        n_splits=5, shuffle=True, random_state=42
    ).split(selected, labels, groups):
        if (
            set(labels.iloc[train_index]) == set(classes)
            and set(labels.iloc[test_index]) == set(classes)
        ):
            split = train_index, test_index
            break
    if split is None:
        raise ValueError(
            "Could not create a stratified, applicant-grouped holdout; provide more distinct cases per class."
        )
    train_index, test_index = split
    x_train = selected.iloc[train_index].reset_index(drop=True)
    x_test = selected.iloc[test_index].reset_index(drop=True)
    y_train = labels.iloc[train_index].reset_index(drop=True)
    y_test = labels.iloc[test_index].reset_index(drop=True)
    group_train = groups.iloc[train_index].reset_index(drop=True)
    group_test = set(groups.iloc[test_index])
    if group_test.intersection(set(group_train)):
        raise ValueError("Applicant group leakage detected in train/test split.")

    def create_preprocessor():
        transformers = []
        if numeric_features:
            transformers.append(
                (
                    "numeric",
                    Pipeline([("imputer", SimpleImputer(strategy="median", keep_empty_features=True)), ("scaler", StandardScaler())]) if algorithm == "logistic_regression" else SimpleImputer(strategy="median", keep_empty_features=True),
                    numeric_features,
                )
            )
        if categorical_features:
            transformers.append(
                (
                    "categorical",
                    Pipeline(
                        [
                            ("imputer", SimpleImputer(strategy="constant", fill_value="unknown")),
                            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                        ]
                    ),
                    categorical_features,
                )
            )
        return ColumnTransformer(transformers, remainder="drop", verbose_feature_names_out=False)

    def create_classifier():
        if algorithm == "logistic_regression":
            model = LogisticRegression(C=1.0, penalty="l2", solver="lbfgs", max_iter=2000, random_state=42)
        elif algorithm == "lightgbm":
            from lightgbm import LGBMClassifier

            model = LGBMClassifier(
                n_estimators=160,
                learning_rate=0.05,
                num_leaves=15,
                class_weight="balanced",
                random_state=42,
                verbosity=-1,
            )
        else:
            from sklearn.ensemble import RandomForestClassifier

            model = RandomForestClassifier(
                n_estimators=300,
                min_samples_leaf=2,
                class_weight="balanced_subsample",
                n_jobs=-1,
                random_state=42,
            )
        return Pipeline(
            [
                ("preprocessor", create_preprocessor()),
                ("model", model),
            ]
        )

    pipeline = create_classifier()
    classifier_pipeline = pipeline
    calibrator = None
    calibration_rows = 0
    if model_type == "visa":
        # Reserve disjoint applicant groups for calibration instead of calibrating
        # on training predictions or letting an applicant appear in both splits.
        calibration_split = None
        for fit_index, calibration_index in GroupShuffleSplit(
            n_splits=50, test_size=0.2, random_state=17
        ).split(x_train, y_train, group_train):
            if (
                set(y_train.iloc[fit_index]) == set(classes)
                and set(y_train.iloc[calibration_index]) == set(classes)
            ):
                calibration_split = fit_index, calibration_index
                break
        if calibration_split is None:
            raise ValueError("Insufficient distinct applicant groups for probability calibration.")
        fit_index, calibration_index = calibration_split
        calibration_groups = set(group_train.iloc[calibration_index])
        fit_groups = set(group_train.iloc[fit_index])
        if calibration_groups.intersection(fit_groups):
            raise ValueError("Applicant group leakage detected in the calibration split.")
        pipeline.fit(x_train.iloc[fit_index], y_train.iloc[fit_index])
        calibration_proba = pipeline.predict_proba(x_train.iloc[calibration_index])
        positive_class_index = list(pipeline.classes_).index("APPROVED")
        calibration_logits = _logit(calibration_proba[:, positive_class_index]).reshape(-1, 1)
        calibrator = LogisticRegression(solver="lbfgs", random_state=42)
        calibrator.fit(
            calibration_logits,
            (y_train.iloc[calibration_index] == "APPROVED").astype(int),
        )
        calibration_rows = len(calibration_index)
        # Explanations must use the same fitted base estimator as prediction,
        # not a second classifier trained on the calibration population.
        classifier_pipeline = pipeline
        positive_class_index = list(pipeline.classes_).index("APPROVED")
        raw_test_proba = pipeline.predict_proba(x_test)[:, positive_class_index]
        predicted_proba = calibrator.predict_proba(
            _logit(raw_test_proba).reshape(-1, 1)
        )[:, 1]
        predicted_labels = np.where(predicted_proba >= 0.5, "APPROVED", "REJECTED")
        actual_binary = (y_test == "APPROVED").astype(int)
        predictions_binary = (predicted_labels == "APPROVED").astype(int)
        metrics = {
            "precision": float(precision_score(actual_binary, predictions_binary, zero_division=0)),
            "recall": float(recall_score(actual_binary, predictions_binary, zero_division=0)),
            "f1": float(f1_score(actual_binary, predictions_binary, zero_division=0)),
            "roc_auc": float(roc_auc_score(actual_binary, predicted_proba)),
            "pr_auc": float(average_precision_score(actual_binary, predicted_proba)),
            "brier_score": float(brier_score_loss(actual_binary, predicted_proba)),
            "log_loss": float(log_loss(actual_binary, predicted_proba)),
            "calibration_bins": _calibration_bins(actual_binary.to_numpy(), predicted_proba),
            "baseline_brier_score": float(brier_score_loss(actual_binary, np.repeat(float((y_train == "APPROVED").mean()), len(y_test)))),
            "accuracy": float(accuracy_score(actual_binary, predictions_binary)),
            "confusion_matrix": confusion_matrix(
                actual_binary, predictions_binary, labels=[0, 1]
            ).tolist(),
            "confusion_matrix_labels": ["REJECTED", "APPROVED"],
            "probability_calibration": "Platt sigmoid; separate applicant groups",
            "decision_threshold": 0.5,
        }
        artifact = {
            "model_type": model_type,
            "pipeline": pipeline,
            "explanation_pipeline": classifier_pipeline,
            "calibrator": calibrator,
            "features": feature_columns,
            "numeric_features": numeric_features,
            "categorical_features": categorical_features,
            "classes": list(pipeline.classes_),
            "positive_class": "APPROVED",
        }
    else:
        pipeline.fit(x_train, y_train)
        predicted_labels = pipeline.predict(x_test)
        probabilities = pipeline.predict_proba(x_test)
        labels_in_order = list(pipeline.classes_)
        matrix = confusion_matrix(y_test, predicted_labels, labels=labels_in_order)
        try:
            roc_auc = roc_auc_score(
                y_test,
                probabilities,
                labels=labels_in_order,
                multi_class="ovr",
                average="macro",
            )
            binary_targets = (y_test.to_numpy()[:, None] == np.array(labels_in_order)).astype(int)
            pr_auc = average_precision_score(
                binary_targets, probabilities, average="macro"
            )
        except ValueError:
            roc_auc = None
            pr_auc = None
        metrics = {
            "precision_macro": float(
                precision_score(y_test, predicted_labels, average="macro", zero_division=0)
            ),
            "recall_macro": float(
                recall_score(y_test, predicted_labels, average="macro", zero_division=0)
            ),
            "f1_macro": float(
                f1_score(y_test, predicted_labels, average="macro", zero_division=0)
            ),
            "roc_auc_ovr_macro": float(roc_auc) if roc_auc is not None else None,
            "pr_auc_macro": float(pr_auc) if pr_auc is not None else None,
            "accuracy": float(accuracy_score(y_test, predicted_labels)),
            "confusion_matrix": matrix.tolist(),
            "confusion_matrix_labels": labels_in_order,
        }
        score_column = "financial_score"
        if score_column not in data:
            raise ValueError("Financial assessment training requires financial_score labels.")
        score_values = data[score_column].map(_numeric_or_nan)
        out_of_range_scores = score_values.notna() & ~score_values.between(0, 100)
        if out_of_range_scores.any():
            raise ValueError("financial_score values must be between 0 and 100.")
        valid_scores = score_values.notna()
        if int(valid_scores.sum()) < minimum_rows:
            raise ValueError(
                f"Insufficient financial_score labels: {int(valid_scores.sum())}; "
                f"at least {minimum_rows} are required."
            )
        score_data = selected.loc[valid_scores].reset_index(drop=True)
        score_y = score_values.loc[valid_scores].astype(float).reset_index(drop=True)
        score_groups = groups.loc[valid_scores].reset_index(drop=True)
        if algorithm == "lightgbm":
            from lightgbm import LGBMRegressor

            score_model = LGBMRegressor(
                n_estimators=160,
                learning_rate=0.05,
                num_leaves=15,
                random_state=42,
                verbosity=-1,
            )
        else:
            from sklearn.ensemble import RandomForestRegressor

            score_model = RandomForestRegressor(
                n_estimators=300,
                min_samples_leaf=2,
                n_jobs=-1,
                random_state=42,
            )
        regressor = Pipeline(
            [
                ("preprocessor", create_preprocessor()),
                ("model", score_model),
            ]
        )
        score_train_indices = [
            index for index in range(len(score_data))
            if score_groups.iloc[index] not in group_test
        ]
        score_test_indices = [
            index for index in range(len(score_data))
            if score_groups.iloc[index] in group_test
        ]
        if not score_train_indices or not score_test_indices:
            raise ValueError("Financial score target cannot use the grouped holdout.")
        regressor.fit(score_data.iloc[score_train_indices], score_y.iloc[score_train_indices])
        score_predictions = np.clip(
            regressor.predict(score_data.iloc[score_test_indices]), 0, 100
        )
        score_actual = score_y.iloc[score_test_indices].to_numpy()
        metrics["financial_score_mae"] = float(
            np.mean(np.abs(score_actual - score_predictions))
        )
        metrics["financial_score_rmse"] = float(
            np.sqrt(np.mean((score_actual - score_predictions) ** 2))
        )
        artifact = {
            "model_type": model_type,
            "pipeline": pipeline,
            "regressor": regressor,
            "features": feature_columns,
            "numeric_features": numeric_features,
            "categorical_features": categorical_features,
            "classes": list(pipeline.classes_),
        }

    supported_cohorts = []
    cohort_metrics = []
    if model_type == "visa" and "destination_country" in data.columns:
        cohort_columns = ["destination_country"] + (["visa_category"] if "visa_category" in data.columns else [])
        for key, part in data.groupby(cohort_columns, dropna=False):
            values = key if isinstance(key, tuple) else (key,)
            class_group_counts = part.groupby(target_column)["group_id"].nunique().to_dict()
            heldout = data.iloc[test_index]
            mask = np.ones(len(heldout), dtype=bool)
            for column, value in zip(cohort_columns, values):
                mask &= heldout[column].astype(str).to_numpy() == str(value)
            actual = (heldout.loc[mask, target_column] == "APPROVED").astype(int).to_numpy()
            probabilities = predicted_proba[mask]
            evaluation = {"destination_country": str(values[0]), "visa_category": str(values[1]) if len(values)>1 else None,
                          "test_rows": int(mask.sum()), "approved_test_rows": int(actual.sum()), "refused_test_rows": int(len(actual)-actual.sum()),
                          "brier_score": float(brier_score_loss(actual, probabilities)) if len(actual) else None,
                          "log_loss": float(log_loss(actual, probabilities, labels=[0, 1])) if len(actual) else None,
                          "roc_auc": float(roc_auc_score(actual, probabilities)) if len(set(actual))==2 else None}
            cohort_metrics.append(evaluation)
            if len(set(actual))==2 and all(int(class_group_counts.get(label, 0)) >= minimum_per_class for label in classes):
                supported_cohorts.append({"destination_country": str(values[0]), "visa_category": str(values[1]) if len(values)>1 else None,
                                          "rows":len(part), "groups_per_class":{str(k):int(v) for k,v in class_group_counts.items()}, "holdout_metrics":evaluation})
    artifact["feature_schema_version"] = feature_schema_version
    artifact["supported_cohorts"] = supported_cohorts
    artifact["feature_units"] = {name:("USD major units" if name.endswith("_usd") else "0-1" if name=="aba_statement_quality" else "1-10" if name=="aba_statement_strength" else "0-100" if name in {"financial_score","evidence_strength_score","document_completeness_score"} else "count/category") for name in feature_columns}
    metadata = {
        "model_type": model_type,
        "algorithm": {"lightgbm":"LightGBM", "random_forest":"Random Forest", "logistic_regression":"Logistic Regression"}[algorithm],
        "feature_schema_version": feature_schema_version,
        "feature_units": artifact["feature_units"],
        "supported_cohorts": supported_cohorts,
        "cohort_metrics": cohort_metrics,
        "evaluation_strategy": "Applicant-grouped holdout with separate calibration groups; temporal holdout is not evaluated without a reviewed dated cohort.",
        "feature_columns": feature_columns,
        "label_column": target_column,
        "class_counts": {str(key): int(value) for key, value in counts.items()},
        "group_counts": {str(key): int(value) for key, value in class_groups.items()},
        "row_count": int(len(data)),
        "train_row_count": int(len(train_index)),
        "test_row_count": int(len(test_index)),
        "calibration_row_count": calibration_rows,
        "missing_rates": {
            str(key): round(float(value), 4) for key, value in missing_rates.items()
        },
        "metrics": metrics,
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "label_mapping": _normalize_label_mapping(model_type, label_mapping),
        "model_status": "PENDING_REVIEW",
        "validation_passed": True,
        "human_review_required": True,
    }
    return artifact, metadata


def predict_with_artifact(
    artifact: dict[str, Any], model_type: str, payload: dict[str, Any]
) -> dict[str, Any]:
    import pandas as pd

    feature_columns = artifact["features"]
    requested_schema = payload.get("_feature_schema_version")
    trained_schema = artifact.get("feature_schema_version", "visa-legacy-v1" if model_type == "visa" else "financial-legacy-v1")
    if requested_schema and requested_schema != trained_schema:
        raise ValueError("The active model uses a different feature schema. Retraining and calibration are required.")
    if model_type == "visa" and (trained_schema == "visa-aba-evidence-v1" or "supported_cohorts" in artifact):
        supported = artifact.get("supported_cohorts") or []
        destination = str(payload.get("destination_country") or "").casefold()
        category = str(payload.get("visa_category") or "").casefold()
        if not any(str(c.get("destination_country") or "").casefold()==destination and (str(c.get("visa_category") or "").casefold()==category or (trained_schema == "visa-legacy-v1" and c.get("visa_category") is None)) for c in supported):
            raise ValueError("The active model has no validated destination/visa-category cohort for this case.")
    numeric_features = set(artifact.get("numeric_features") or ())
    missing_features = []
    row: dict[str, Any] = {}
    for name in feature_columns:
        if name not in payload:
            missing_features.append(name)
            continue
        value = payload[name]
        if name in numeric_features:
            normalized = _numeric_or_nan(value)
            if math.isnan(normalized):
                missing_features.append(name)
                continue
            row[name] = normalized
        elif isinstance(value, (str, int, float, bool)):
            normalized = _category_or_nan(value)
            if not isinstance(normalized, str):
                missing_features.append(name)
                continue
            row[name] = normalized
        else:
            missing_features.append(name)
    if missing_features:
        raise ValueError(
            "Prediction requires verified values for: "
            + ", ".join(missing_features)
            + "."
        )
    frame = pd.DataFrame([row], columns=feature_columns)
    if model_type == "visa":
        probability_columns = artifact["pipeline"].predict_proba(frame)
        positive_index = list(artifact["pipeline"].classes_).index("APPROVED")
        raw_probability = float(probability_columns[0, positive_index])
        calibrated = artifact["calibrator"].predict_proba(
            _logit(np.array([raw_probability])).reshape(-1, 1)
        )[0, 1]
        probability = float(np.clip(calibrated, 0, 1))
        status = "human_review_required"
        predicted_class = "APPROVED" if probability >= 0.5 else "REJECTED"
        explanation = _feature_contributions(
            artifact.get("explanation_pipeline", artifact["pipeline"]), frame, "APPROVED"
        )
        return {
            "prediction_status": status,
            "prediction": predicted_class,
            "visa_approval_probability": round(probability, 6),
            "probability_unit": "0-1",
            "model_type": model_type,
            "explanation": explanation,
            "disclaimer": (
                "Calibrated model estimate for human review only. It is not an "
                "embassy decision, guarantee, or substitute for professional advice."
            ),
        }

    risk = str(artifact["pipeline"].predict(frame)[0])
    predicted_score = float(np.clip(artifact["regressor"].predict(frame)[0], 0, 100))
    explanation = _feature_contributions(artifact["pipeline"], frame, risk)
    return {
        "prediction_status": "human_review_required",
        "financial_risk_category": risk,
        "financial_score": round(predicted_score, 2),
        "model_type": model_type,
        "explanation": explanation,
        "disclaimer": "Financial decision support only; not a visa approval probability or fraud finding.",
    }


def _feature_contributions(
    pipeline, frame, target_class: str
) -> list[dict[str, Any]]:
    from lightgbm import LGBMClassifier

    preprocessor = pipeline.named_steps["preprocessor"]
    model = pipeline.named_steps["model"]
    if target_class not in model.classes_:
        return []
    transformed = preprocessor.transform(frame)
    from sklearn.linear_model import LogisticRegression
    if isinstance(model, LogisticRegression):
        names = preprocessor.get_feature_names_out()
        values = np.asarray(model.coef_[0]) * np.asarray(transformed)[0]
        if list(model.classes_).index(target_class) == 0:
            values = -values
        order = np.argsort(np.abs(values))[::-1][:5]
        return [{"feature":str(names[index]),"contribution_log_odds":round(float(values[index]),5),
                 "direction":"supports predicted class" if values[index]>0 else "reduces support for predicted class",
                 "explanation_basis":"Same fitted logistic-regression base model; feature effects on uncalibrated log-odds, not probability points. The probability additionally uses the saved sigmoid calibration."}
                for index in order if abs(values[index])>1e-8]
    if not isinstance(model, LGBMClassifier):
        importances = getattr(model, "feature_importances_", None)
        if importances is None:
            return []
        names = preprocessor.get_feature_names_out()
        order = np.argsort(np.asarray(importances))[::-1][:5]
        return [
            {
                "feature": str(names[index]),
                "importance": round(float(importances[index]), 5),
                "explanation_basis": (
                    "Random Forest impurity-based feature importance; it has no "
                    "direction and is not a probability contribution."
                ),
            }
            for index in order
            if importances[index] > 1e-8
        ]
    contributions = np.asarray(
        model.booster_.predict(transformed, pred_contrib=True)
    )
    target_index = list(model.classes_).index(target_class)
    if contributions.ndim == 3:
        values = contributions[0, target_index, :-1]
    elif (
        contributions.ndim == 2
        and len(model.classes_) > 2
        and contributions.shape[1]
        == len(model.classes_) * (transformed.shape[1] + 1)
    ):
        values = contributions.reshape(
            len(frame), len(model.classes_), transformed.shape[1] + 1
        )[0, target_index, :-1]
    elif contributions.ndim == 2 and len(model.classes_) == 2:
        values = contributions[0, :-1]
        if target_index == 0:
            values = -values
    else:
        return []
    names = preprocessor.get_feature_names_out()
    order = np.argsort(np.abs(values))[::-1][:5]
    return [
        {
            "feature": str(names[index]),
            "contribution_log_odds": round(float(values[index]), 5),
            "direction": (
                "supports predicted class"
                if values[index] > 0
                else "reduces support for predicted class"
            ),
            "explanation_basis": "LightGBM TreeSHAP contribution on uncalibrated class log-odds; not probability points.",
        }
        for index in order
        if abs(values[index]) > 1e-8
    ]


def _logit(probabilities: np.ndarray) -> np.ndarray:
    epsilon = 1e-6
    clipped = np.clip(probabilities, epsilon, 1 - epsilon)
    return np.log(clipped / (1 - clipped))


def _numeric_or_nan(value: Any) -> float:
    if value is None:
        return np.nan
    try:
        number = float(value)
    except (TypeError, ValueError):
        return np.nan
    return number if math.isfinite(number) else np.nan


def _category_or_nan(value: Any) -> str | float:
    if value is None:
        return np.nan
    if isinstance(value, float) and math.isnan(value):
        return np.nan
    text = str(value).strip()
    return text if text and text.casefold() not in {"<na>", "nan", "none"} else np.nan


def _calibration_bins(actual, probabilities, count=10):
    rows=[]
    for lower in np.linspace(0, 1, count, endpoint=False):
        upper=lower+1/count
        selected=(probabilities>=lower) & ((probabilities<=upper) if upper>=1 else (probabilities<upper))
        if selected.any():
            rows.append({"lower":float(lower),"upper":float(min(1,upper)),"count":int(selected.sum()),
                         "predicted":float(np.mean(probabilities[selected])),"observed":float(np.mean(actual[selected]))})
    return rows
