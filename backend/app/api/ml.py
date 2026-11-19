from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import joblib
from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, UploadFile, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..core.config import PROJECT_ROOT
from ..core.errors import APIError
from ..services.business_rules import (
    DEFAULT_RULE_POLICY,
    evaluate_business_rules,
    validate_rule_policy,
)
from ..services.ml_training import (
    MINIMUM_ROWS_PER_CLASS,
    MINIMUM_TRAINING_ROWS,
    load_training_dataset,
    predict_with_artifact,
    train_model,
    validate_training_dataset,
)
from .dependencies import get_current_principal, get_mongo_database, require_roles
from .mongo_helpers import id_query

logger = logging.getLogger(__name__)
router = APIRouter()

_RULES_ID = "visa_financial_rules"
_MODEL_DIR = PROJECT_ROOT / "backend" / "storage" / "models"
_ALLOWED_DATASET_EXTENSIONS = {".csv", ".xlsx", ".xls"}
_ATTESTATION = (
    "I attest that these are authorized, real, verified historical labels, "
    "not synthetic or inferred from workflow statuses."
)


def _training_thresholds() -> tuple[int, int]:
    try:
        rows = int(os.getenv("ML_MINIMUM_TRAINING_ROWS", str(MINIMUM_TRAINING_ROWS)))
        per_class = int(os.getenv("ML_MINIMUM_PER_CLASS", str(MINIMUM_ROWS_PER_CLASS)))
    except ValueError as error:
        logger.exception("ML minimum dataset thresholds are invalid")
        raise APIError(500, "ML minimum dataset thresholds are invalid") from error
    return max(MINIMUM_TRAINING_ROWS, rows), max(MINIMUM_ROWS_PER_CLASS, per_class)


async def _load_policy(database: AsyncDatabase) -> dict[str, Any]:
    try:
        record = await database["business_rules"].find_one({"_id": _RULES_ID})
    except PyMongoError as error:
        logger.exception("Could not load internal business rules")
        raise APIError(503, "Business rules are unavailable") from error
    if not record:
        return validate_rule_policy(DEFAULT_RULE_POLICY)
    return validate_rule_policy(record.get("policy"))


async def _audit(
    database: AsyncDatabase,
    actor_id: str,
    action: str,
    details: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc)
    record = {
        "_id": str(uuid4()),
        "actor_id": actor_id,
        "action": action,
        "details": details,
        "created_at": now,
        "timestamp": now,
    }
    for field in (
        "model_version",
        "trained_by",
        "reviewed_by",
        "approved_by",
        "activated_by",
    ):
        if field in details:
            record[field] = details[field]
    comment = (
        details.get("comments")
        or details.get("review_comment")
        or details.get("approval_comment")
        or details.get("activation_comment")
    )
    if comment:
        record["comments"] = comment
    try:
        await database["ml_business_audit"].insert_one(record)
    except PyMongoError as error:
        logger.exception("Audit event could not be recorded for action=%s", action)
        raise APIError(503, "Required audit record could not be saved") from error


def _public_model(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: record.get(key)
            for key in (
                "model_version",
                "model_type",
                "algorithm",
                "feature_columns",
                "feature_schema_version",
                "feature_units",
                "supported_cohorts",
                "cohort_metrics",
                "evaluation_strategy",
                "label_column",
                "class_counts",
                "group_counts",
                "row_count",
                "train_row_count",
                "test_row_count",
                "calibration_row_count",
                "missing_rates",
                "metrics",
                "trained_at",
                "approved_at",
                "activated_at",
                "deactivated_at",
                "rolled_back_at",
                "rejected_at",
                "validation_report",
                "validation_passed",
                "artifact_sha256",
                "trained_by",
                "reviewed_by",
                "approved_by",
                "activated_by",
                "reviewed_at",
                "review_type",
                "review_comment",
                "releaseCriteria",
                "approval_comment",
                "activation_comment",
                "rejection_comment",
                "rollback_comment",
                "rolled_back_by",
                "rejected_by",
                "activation_mode",
                "self_review",
            )
            if key in record
        },
        **(
            {"model_status": _model_lifecycle_status(record)}
            if "model_status" in record
            else {}
        ),
    }


def _model_lifecycle_status(record: dict[str, Any]) -> str:
    status = str(record.get("model_status") or "").strip().upper()
    return {
        "TRAINED_PENDING_INDEPENDENT_REVIEW": "PENDING_REVIEW",
        "PENDING_REVIEW": "PENDING_REVIEW",
        "UNDER_REVIEW": "UNDER_REVIEW",
        "APPROVED": "APPROVED",
        "ACTIVE": "ACTIVE",
        "INACTIVE": "ROLLED_BACK",
        "ROLLED_BACK": "ROLLED_BACK",
        "REJECTED": "REJECTED",
    }.get(status, status)


def _has_training_validation(record: dict[str, Any]) -> bool:
    if record.get("validation_passed") is not None:
        return record.get("validation_passed") is True
    legacy_status = str(record.get("model_status") or "").strip().casefold()
    row_count = record.get("row_count")
    return (
        legacy_status
        in {
            "trained_pending_independent_review",
            "active",
            "inactive",
            "rolled_back",
        }
        and isinstance(record.get("metrics"), dict)
        and isinstance(row_count, int)
        and row_count >= MINIMUM_TRAINING_ROWS
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact_file:
        for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_verified_artifact(
    record: dict[str, Any],
    model_version: str,
    require_training_validation: bool = True,
) -> tuple[dict[str, Any], str]:
    if str(record.get("model_version") or "") != model_version:
        raise APIError(409, "Model registry version does not match the requested version")
    if require_training_validation and not _has_training_validation(record):
        raise APIError(409, "A model that failed or lacks training validation cannot be activated")
    try:
        path = _model_path(record)
    except APIError as error:
        raise APIError(409, "Model artifact is missing or outside the model storage directory") from error
    if path.name != f"{model_version}.joblib":
        raise APIError(409, "Model artifact filename does not match its registry version")
    try:
        checksum = _sha256_file(path)
    except OSError as error:
        raise APIError(409, "Model artifact could not be read") from error
    expected_checksum = record.get("artifact_sha256")
    if expected_checksum and not hmac.compare_digest(
        str(expected_checksum), checksum
    ):
        raise APIError(409, "Model artifact checksum does not match the registry")
    try:
        artifact = joblib.load(path)
    except (
        AttributeError,
        EOFError,
        ImportError,
        IndexError,
        KeyError,
        OSError,
        pickle.UnpicklingError,
        TypeError,
        ValueError,
    ) as error:
        logger.exception("Could not load model artifact version=%s", model_version)
        raise APIError(409, "Model artifact failed to load and cannot be activated") from error
    if (
        not isinstance(artifact, dict)
        or artifact.get("model_type") != record.get("model_type")
        or not artifact.get("pipeline")
        or (
            artifact.get("model_version") is not None
            and artifact.get("model_version") != model_version
        )
    ):
        raise APIError(409, "Model artifact metadata does not match its registry entry")
    return artifact, checksum


def _model_path(record: dict[str, Any]) -> Path:
    path = Path(str(record.get("artifact_path") or "")).resolve()
    try:
        path.relative_to(_MODEL_DIR.resolve())
    except ValueError as error:
        raise APIError(503, "Model artifact path is invalid") from error
    if not path.is_file():
        raise APIError(503, "Model artifact is missing")
    return path


@router.get("/rules")
async def get_rules(
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    return JSONResponse(content={"rules": await _load_policy(database)})


@router.put("/rules")
async def update_rules(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    try:
        policy = validate_rule_policy(payload.get("rules"))
    except (TypeError, ValueError) as error:
        return JSONResponse(status_code=400, content={"message": str(error)})
    now = datetime.now(timezone.utc)
    policy["version"] = int(policy.get("version", 1)) + 1
    try:
        await database["business_rules"].update_one(
            {"_id": _RULES_ID},
            {
                "$set": {
                    "policy": policy,
                    "updated_at": now,
                    "updated_by": principal["id"],
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    except PyMongoError as error:
        logger.exception("Could not save internal business rules")
        raise APIError(503, "Business rules could not be saved") from error
    await _audit(
        database,
        principal["id"],
        "business_rules_updated",
        {"version": policy["version"]},
    )
    return JSONResponse(content={"rules": policy})


@router.post("/rules/evaluate")
async def evaluate_rules(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    applicant = payload.get("applicant", {})
    financial = payload.get("financial_analysis", {})
    if not isinstance(applicant, dict) or not isinstance(financial, dict):
        return JSONResponse(
            status_code=400,
            content={"message": "applicant and financial_analysis must be objects"},
        )
    result = evaluate_business_rules(applicant, financial, await _load_policy(database))
    await _audit(
        database,
        principal["id"],
        "business_rules_evaluated",
        {"status": result["status"], "destination": str(applicant.get("destination_country") or "")[:60]},
    )
    return JSONResponse(content={"assessment": result})


@router.get("/ml/model-status")
async def model_status(
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    try:
        versions = await database["ml_model_versions"].find({}).to_list(length=200)
        active = await database["ml_active_models"].find({}).to_list(length=20)
    except PyMongoError as error:
        logger.exception("Could not load model registry")
        raise APIError(503, "Model registry is unavailable") from error
    active_by_type = {str(item.get("_id")): item.get("model_version") for item in active}
    latest_by_type: dict[str, dict[str, Any]] = {}
    for version in versions:
        model_type = str(version.get("model_type") or "")
        current = latest_by_type.get(model_type)
        if current is None or str(version.get("trained_at", "")) > str(current.get("trained_at", "")):
            latest_by_type[model_type] = version
    statuses = {}
    for model_type in ("financial", "visa"):
        latest = latest_by_type.get(model_type)
        active_version = active_by_type.get(model_type)
        statuses[model_type] = {
            "status": (
                "ACTIVE"
                if active_version
                else _model_lifecycle_status(latest)
                if latest
                else "INSUFFICIENT_DATA"
            ),
            "active_model_version": active_version,
            "latest": _public_model(latest) if latest else None,
        }
    return JSONResponse(
        content=jsonable_encoder(
            {
                "models": statuses,
                "prediction_available": bool(active_by_type.get("visa")),
                "message": (
                    "No approval probability is available until an Admin-reviewed model is activated."
                    if not active_by_type.get("visa")
                    else "An Admin-reviewed decision-support model is active; human review is still required."
                ),
            },
            custom_encoder={ObjectId: str},
        )
    )


@router.get("/ml/metrics")
async def model_metrics(
    database: AsyncDatabase = Depends(get_mongo_database),
    model_type: str | None = None,
    _principal: dict[str, str] = Depends(get_current_principal),
) -> JSONResponse:
    query = {"model_type": model_type} if model_type in {"visa", "financial"} else {}
    try:
        records = await database["ml_model_versions"].find(query).to_list(length=200)
    except PyMongoError as error:
        logger.exception("Could not load model metrics")
        raise APIError(503, "Model metrics are unavailable") from error
    records.sort(key=lambda item: str(item.get("trained_at") or ""), reverse=True)
    return JSONResponse(
        content=jsonable_encoder(
            {"models": [_public_model(item) for item in records]},
            custom_encoder={ObjectId: str},
        )
    )


@router.get("/ml/dataset-template")
async def dataset_template(
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> Response:
    template_path = PROJECT_ROOT / "templates" / "ml_training_dataset_template.csv"
    try:
        content = template_path.read_bytes()
    except OSError as error:
        logger.exception("Could not load ML dataset template")
        raise APIError(503, "Dataset template is unavailable") from error
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="ml_training_dataset_template.csv"'
        },
    )


@router.post("/ml/validate-dataset")
async def validate_dataset(
    file: UploadFile = File(...),
    kind: str = Form(..., alias="model_type"),
    label_mapping: str = Form(default="{}"),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    filename = Path(file.filename or "").name
    if Path(filename).suffix.casefold() not in _ALLOWED_DATASET_EXTENSIONS:
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "Upload a CSV or Excel (.xlsx/.xls) dataset."},
        )
    if kind not in {"visa", "financial"}:
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "model_type must be visa or financial."},
        )
    try:
        mapping = json.loads(label_mapping)
    except json.JSONDecodeError:
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "label_mapping must be valid JSON."},
        )
    if not isinstance(mapping, dict):
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "label_mapping must be a JSON object."},
        )
    content = await file.read(settings_file_limit())
    await file.close()
    if not content:
        return JSONResponse(status_code=400, content={"message": "Dataset file is empty."})
    if len(content) >= settings_file_limit():
        return JSONResponse(
            status_code=413,
            content={"message": "Dataset exceeds the configured upload limit."},
        )
    try:
        frame = await asyncio.to_thread(load_training_dataset, filename, content)
        minimum_rows, minimum_per_class = _training_thresholds()
        _, report = await asyncio.to_thread(
            validate_training_dataset,
            frame,
            kind,
            minimum_rows,
            minimum_per_class,
            mapping,
        )
    except (ValueError, ImportError) as error:
        return JSONResponse(status_code=400, content={"message": str(error)})
    return JSONResponse(content=report)


@router.post("/ml/train", status_code=201)
async def train(
    file: UploadFile = File(...),
    kind: str = Form(..., alias="model_type"),
    algorithm: str = Form(default="lightgbm"),
    verified_outcomes: bool = Form(...),
    privacy_reviewed: bool = Form(...),
    source_description: str = Form(...),
    label_mapping: str = Form(...),
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    filename = Path(file.filename or "").name
    if Path(filename).suffix.casefold() not in _ALLOWED_DATASET_EXTENSIONS:
        await file.close()
        return JSONResponse(status_code=400, content={"message": "Upload a CSV or Excel dataset."})
    if kind not in {"visa", "financial"}:
        await file.close()
        return JSONResponse(status_code=400, content={"message": "model_type must be visa or financial."})
    if algorithm not in {"lightgbm", "random_forest", "logistic_regression"}:
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "algorithm must be lightgbm, random_forest, or logistic_regression (visa only)."},
        )
    if not verified_outcomes or not privacy_reviewed:
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "Verified-outcome and privacy-review attestations are required."},
        )
    source_description = source_description.strip()
    if len(source_description) < 12 or len(source_description) > 500:
        await file.close()
        return JSONResponse(
            status_code=400,
            content={"message": "Describe the authorized outcome source (12-500 characters)."},
        )
    try:
        mapping = json.loads(label_mapping)
    except json.JSONDecodeError:
        await file.close()
        return JSONResponse(status_code=400, content={"message": "label_mapping must be valid JSON."})
    if not isinstance(mapping, dict) or not mapping:
        await file.close()
        return JSONResponse(status_code=400, content={"message": "An explicit non-empty label_mapping is required."})
    content = await file.read(settings_file_limit())
    await file.close()
    if not content:
        return JSONResponse(status_code=400, content={"message": "Dataset file is empty."})
    if len(content) >= settings_file_limit():
        return JSONResponse(status_code=413, content={"message": "Dataset exceeds the configured upload limit."})

    try:
        frame = await asyncio.to_thread(load_training_dataset, filename, content)
        minimum_rows, minimum_per_class = _training_thresholds()
        _, validation = await asyncio.to_thread(
            validate_training_dataset,
            frame,
            kind,
            minimum_rows,
            minimum_per_class,
            mapping,
        )
        if not validation["ready_to_train"]:
            return JSONResponse(
                status_code=409,
                content={
                    "model_status": "insufficient_data",
                    "prediction_available": False,
                    "message": validation["message"],
                    "validation_report": validation,
                },
            )
        artifact, metadata = await asyncio.to_thread(
            train_model,
            kind,
            frame,
            content,
            minimum_rows,
            minimum_per_class,
            mapping,
            algorithm,
        )
    except ValueError as error:
        logger.warning("Model training was not started: %s", str(error)[:300])
        return JSONResponse(
            status_code=409,
            content={
                "model_status": "insufficient_data",
                "prediction_available": False,
                "message": str(error),
            },
        )
    except ImportError as error:
        logger.exception("A required model-training dependency is unavailable")
        raise APIError(
            503, "Model training dependency is unavailable; check backend installation."
        ) from error
    except Exception as error:
        logger.exception("Model training failed")
        raise APIError(500, "Model training failed; no model was activated") from error

    model_version = f"{kind}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"
    model_dir = _MODEL_DIR / kind
    artifact_path = model_dir / f"{model_version}.joblib"
    temporary_path = model_dir / f".{model_version}.tmp"
    try:
        model_dir.mkdir(parents=True, exist_ok=True)
        artifact["model_version"] = model_version
        await asyncio.to_thread(joblib.dump, artifact, temporary_path)
        os.replace(temporary_path, artifact_path)
        artifact_checksum = await asyncio.to_thread(_sha256_file, artifact_path)
    except OSError as error:
        for partial_path in (temporary_path, artifact_path):
            try:
                partial_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove partial model artifact")
        logger.exception("Could not persist trained model artifact")
        raise APIError(500, "Model artifact could not be saved") from error

    now = datetime.now(timezone.utc)
    registry_record = {
        "_id": model_version,
        "model_version": model_version,
        **metadata,
        "source_description": source_description,
        "label_mapping": metadata["label_mapping"],
        "training_attestation": _ATTESTATION,
        "privacy_reviewed": True,
        "trained_by": principal["id"],
        "trained_at": now,
        "artifact_path": str(artifact_path.resolve()),
        "model_status": "PENDING_REVIEW",
        "validation_passed": True,
        "artifact_sha256": artifact_checksum,
    }
    try:
        await database["ml_model_versions"].insert_one(registry_record)
    except PyMongoError as error:
        try:
            artifact_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not remove unregistered model artifact")
        logger.exception("Could not register trained model")
        raise APIError(503, "Model was trained but could not be registered") from error
    await _audit(
        database,
        principal["id"],
        "model_trained_pending_review",
        {
            "model_version": model_version,
            "model_type": kind,
            "row_count": metadata["row_count"],
            "dataset_sha256": metadata["dataset_sha256"],
            "trained_by": principal["id"],
        },
    )
    return JSONResponse(
        status_code=201,
        content=jsonable_encoder(
            {
                "model": _public_model(registry_record),
                "prediction_available": False,
                "message": "Model is pending Admin review; predictions remain unavailable until an Admin approves and activates it.",
            },
            custom_encoder={ObjectId: str},
        ),
    )


async def _get_model_record(
    database: AsyncDatabase, model_version: str
) -> dict[str, Any]:
    try:
        record = await database["ml_model_versions"].find_one(
            id_query(model_version)
        )
    except PyMongoError as error:
        logger.exception("Could not load model version=%s", model_version)
        raise APIError(503, "Model registry is unavailable") from error
    if not record:
        raise APIError(404, "Model version not found")
    if str(record.get("model_version") or "") != model_version:
        raise APIError(409, "Model registry version does not match the requested version")
    return record


async def _update_model_record(
    database: AsyncDatabase,
    record: dict[str, Any],
    values: dict[str, Any],
) -> None:
    try:
        result = await database["ml_model_versions"].update_one(
            {
                "_id": record["_id"],
                "model_status": record.get("model_status"),
            },
            {"$set": values},
        )
    except PyMongoError as error:
        logger.exception("Could not update model version=%s", record.get("model_version"))
        raise APIError(503, "Model registry could not be updated") from error
    if result.matched_count != 1:
        raise APIError(409, "Model status changed; refresh the model history and retry")


@router.post("/ml/models/{model_version}/review")
async def review_model(
    model_version: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    report = str(payload.get("validation_report") or "").strip()
    comment = str(payload.get("review_comment") or "").strip()
    if len(report) < 20 or len(report) > 2000:
        raise APIError(400, "Provide a validation report between 20 and 2000 characters")
    if len(comment) < 12 or len(comment) > 1000:
        raise APIError(400, "Provide a review comment between 12 and 1000 characters")
    record = await _get_model_record(database, model_version)
    if _model_lifecycle_status(record) != "PENDING_REVIEW":
        raise APIError(409, "Only a pending model can enter review")
    if not _has_training_validation(record):
        raise APIError(409, "A model that failed training validation cannot enter review")
    self_review = record.get("trained_by") == principal["id"]
    review_type = "Admin Self-Review" if self_review else "Admin Review"
    release_criteria = str(payload.get("releaseCriteria") or "").strip()
    if release_criteria and not 20 <= len(release_criteria) <= 4000:
        raise APIError(400, "Release criteria must contain 20 to 4000 characters")
    now = datetime.now(timezone.utc)
    await _update_model_record(
        database,
        record,
        {
            "model_status": "UNDER_REVIEW",
            "validation_passed": True,
            "validation_report": report,
            "review_comment": comment,
            **({"releaseCriteria": release_criteria} if release_criteria else {}),
            "reviewed_by": principal["id"],
            "reviewed_at": now,
            "review_type": review_type,
            "self_review": self_review,
        },
    )
    await _audit(
        database,
        principal["id"],
        "model_review_started",
        {
            "model_version": model_version,
            "model_type": record["model_type"],
            "trained_by": record.get("trained_by"),
            "reviewed_by": principal["id"],
            "review_type": review_type,
            "comments": comment,
            **({"releaseCriteria": release_criteria} if release_criteria else {}),
        },
    )
    return JSONResponse(
        content={
            "model_version": model_version,
            "model_status": "UNDER_REVIEW",
            "review_type": review_type,
            "reviewed_by": principal["id"],
        }
    )


@router.post("/ml/models/{model_version}/approve")
async def approve_model(
    model_version: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    comment = str(payload.get("approval_comment") or "").strip()
    if len(comment) < 12 or len(comment) > 1000:
        raise APIError(400, "Provide an approval comment between 12 and 1000 characters")
    record = await _get_model_record(database, model_version)
    if _model_lifecycle_status(record) != "UNDER_REVIEW":
        raise APIError(409, "Only a model under review can be approved")
    if not _has_training_validation(record) or len(
        str(record.get("validation_report") or "").strip()
    ) < 20:
        raise APIError(409, "Model training validation and review report must pass before approval")
    release_criteria = str(payload.get("releaseCriteria") or record.get("releaseCriteria") or "").strip()
    if record.get("model_type") == "visa" and record.get("feature_schema_version") == "visa-aba-evidence-v1" and not 20 <= len(release_criteria) <= 4000:
        raise APIError(400, "Document releaseCriteria (20 to 4000 characters) for this ABA evidence model before approval")
    now = datetime.now(timezone.utc)
    await _update_model_record(
        database,
        record,
        {
            "model_status": "APPROVED",
            "approved_by": principal["id"],
            "approved_at": now,
            "approval_comment": comment,
            **({"releaseCriteria": release_criteria} if release_criteria else {}),
        },
    )
    await _audit(
        database,
        principal["id"],
        "model_approved",
        {
            "model_version": model_version,
            "model_type": record["model_type"],
            "trained_by": record.get("trained_by"),
            "reviewed_by": record.get("reviewed_by"),
            "approved_by": principal["id"],
            "comments": comment,
            **({"releaseCriteria": release_criteria} if release_criteria else {}),
        },
    )
    return JSONResponse(
        content={
            "model_version": model_version,
            "model_status": "APPROVED",
            "approved_by": principal["id"],
        }
    )


@router.post("/ml/models/{model_version}/reject")
async def reject_model(
    model_version: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    comment = str(payload.get("rejection_comment") or "").strip()
    if len(comment) < 12 or len(comment) > 1000:
        raise APIError(400, "Provide a rejection comment between 12 and 1000 characters")
    record = await _get_model_record(database, model_version)
    if _model_lifecycle_status(record) not in {"PENDING_REVIEW", "UNDER_REVIEW"}:
        raise APIError(409, "Only a pending or reviewing model can be rejected")
    now = datetime.now(timezone.utc)
    self_review = record.get("trained_by") == principal["id"]
    await _update_model_record(
        database,
        record,
        {
            "model_status": "REJECTED",
            "reviewed_by": principal["id"],
            "reviewed_at": now,
            "review_type": "Admin Self-Review" if self_review else "Admin Review",
            "self_review": self_review,
            "rejected_by": principal["id"],
            "rejected_at": now,
            "rejection_comment": comment,
        },
    )
    await _audit(
        database,
        principal["id"],
        "model_rejected",
        {
            "model_version": model_version,
            "model_type": record["model_type"],
            "trained_by": record.get("trained_by"),
            "reviewed_by": principal["id"],
            "comments": comment,
        },
    )
    return JSONResponse(
        content={"model_version": model_version, "model_status": "REJECTED"}
    )


async def _restore_record_fields(
    database: AsyncDatabase,
    record: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    values = {field: record[field] for field in fields if field in record}
    unset = {field: "" for field in fields if field not in record}
    update: dict[str, Any] = {}
    if values:
        update["$set"] = values
    if unset:
        update["$unset"] = unset
    if update:
        await database["ml_model_versions"].update_one(
            {"_id": record["_id"]}, update
        )


async def _activate_registered_model(
    database: AsyncDatabase,
    record: dict[str, Any],
    principal: dict[str, str],
    comment: str,
    *,
    rollback: bool,
) -> dict[str, Any]:
    version = str(record["model_version"])
    model_type = str(record.get("model_type") or "")
    status = _model_lifecycle_status(record)
    if rollback:
        if status not in {"ROLLED_BACK", "ACTIVE"} or not record.get("approved_at"):
            raise APIError(409, "Rollback is allowed only to a previously approved model version")
    elif status != "APPROVED":
        raise APIError(409, "Only an approved model can be activated")
    if model_type == "visa" and record.get("feature_schema_version") == "visa-aba-evidence-v1" and len(str(record.get("releaseCriteria") or "").strip()) < 20:
        raise APIError(409, "This ABA model requires documented release criteria before activation")
    if not _has_training_validation(record):
        raise APIError(409, "A model that failed training validation cannot be activated")
    if not rollback and len(
        str(record.get("validation_report") or "").strip()
    ) < 20:
        raise APIError(409, "The model has no completed Admin review report")
    if model_type not in {"visa", "financial"}:
        raise APIError(409, "Model registry type is invalid")

    _, checksum = await asyncio.to_thread(_load_verified_artifact, record, version)
    try:
        active_record = await database["ml_active_models"].find_one(
            {"_id": model_type}
        )
        previous_version = (
            str(active_record.get("model_version") or "")
            if active_record
            else ""
        )
        if rollback and not previous_version:
            raise APIError(409, "Rollback requires an existing active model")
        if previous_version == version:
            raise APIError(409, "This model version is already active")
        previous_record = (
            await database["ml_model_versions"].find_one(
                id_query(previous_version)
            )
            if previous_version
            else None
        )
        if previous_version and not previous_record:
            raise APIError(409, "The current active model registry entry is missing")
    except PyMongoError as error:
        logger.exception("Could not load active model during activation")
        raise APIError(503, "Model registry is unavailable") from error

    now = datetime.now(timezone.utc)
    candidate_fields = (
        "model_status",
        "activated_by",
        "activated_at",
        "activation_comment",
        "activation_mode",
        "artifact_sha256",
    )
    previous_fields = (
        "model_status",
        "deactivated_at",
        "deactivated_to_version",
        "rolled_back_by",
        "rolled_back_at",
        "rollback_comment",
    )
    candidate_updated = False
    previous_updated = False
    pointer_attempted = False
    pointer_updated = False
    try:
        await _update_model_record(
            database,
            record,
            {
                "model_status": "ACTIVE",
                "activated_by": principal["id"],
                "activated_at": now,
                "activation_comment": comment,
                "activation_mode": "ROLLBACK" if rollback else "ACTIVATION",
                "artifact_sha256": checksum,
            },
        )
        candidate_updated = True
        if previous_record:
            await _update_model_record(
                database,
                previous_record,
                {
                    "model_status": "ROLLED_BACK",
                    "deactivated_at": now,
                    "deactivated_to_version": version,
                    "rolled_back_by": principal["id"],
                    "rolled_back_at": now,
                    "rollback_comment": comment if rollback else None,
                },
            )
            previous_updated = True

        pointer = {
            "model_version": version,
            "activated_at": now,
            "activated_by": principal["id"],
            "activation_comment": comment,
        }
        if active_record:
            pointer_attempted = True
            result = await database["ml_active_models"].update_one(
                {"_id": model_type, "model_version": previous_version},
                {"$set": pointer},
            )
            if result.matched_count != 1:
                raise APIError(409, "Active model changed; refresh and retry")
            pointer_updated = True
        else:
            pointer_attempted = True
            await database["ml_active_models"].insert_one(
                {"_id": model_type, **pointer}
            )
            pointer_updated = True

        action = "model_rolled_back" if rollback else "model_activated"
        await _audit(
            database,
            principal["id"],
            action,
            {
                "model_version": version,
                "model_type": model_type,
                "previous_version": previous_version or None,
                "trained_by": record.get("trained_by"),
                "reviewed_by": record.get("reviewed_by"),
                "approved_by": record.get("approved_by"),
                "activated_by": principal["id"],
                "comments": comment,
            },
        )
    except (APIError, PyMongoError) as error:
        try:
            should_restore_pointer = pointer_updated
            if pointer_attempted and not pointer_updated:
                uncertain_pointer = await database["ml_active_models"].find_one(
                    {
                        "_id": model_type,
                        "model_version": version,
                        "activated_by": principal["id"],
                        "activated_at": now,
                    }
                )
                should_restore_pointer = uncertain_pointer is not None
            if should_restore_pointer and active_record:
                pointer_fields = (
                    "model_version",
                    "activated_at",
                    "activated_by",
                    "activation_comment",
                )
                old_pointer = {
                    field: active_record[field]
                    for field in pointer_fields
                    if field in active_record
                }
                unset_pointer = {
                    field: ""
                    for field in pointer_fields
                    if field not in active_record
                }
                restore_pointer: dict[str, Any] = {}
                if old_pointer:
                    restore_pointer["$set"] = old_pointer
                if unset_pointer:
                    restore_pointer["$unset"] = unset_pointer
                await database["ml_active_models"].update_one(
                    {"_id": model_type, "model_version": version},
                    restore_pointer,
                )
            elif should_restore_pointer:
                await database["ml_active_models"].delete_one(
                    {"_id": model_type, "model_version": version}
                )
            if candidate_updated:
                await _restore_record_fields(
                    database, record, candidate_fields
                )
            if previous_updated and previous_record:
                await _restore_record_fields(
                    database, previous_record, previous_fields
                )
        except PyMongoError:
            logger.exception(
                "Could not restore model state after failed activation version=%s",
                version,
            )
        if isinstance(error, APIError):
            raise
        if isinstance(error, DuplicateKeyError):
            raise APIError(409, "Another model activation is already in progress") from error
        logger.exception("Could not activate model version=%s", version)
        raise APIError(503, "Model version could not be activated; the prior active model was preserved") from error

    return {
        "model_version": version,
        "model_type": model_type,
        "model_status": "ACTIVE",
        "previous_version": previous_version or None,
    }


@router.post("/ml/models/{model_version}/activate")
async def activate_model(
    model_version: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    if payload.get("activation_confirmation") is not True:
        raise APIError(400, "Explicit activation confirmation is required")
    comment = str(payload.get("activation_comment") or "").strip()
    if len(comment) < 12 or len(comment) > 1000:
        raise APIError(400, "Provide an activation comment between 12 and 1000 characters")
    record = await _get_model_record(database, model_version)
    result = await _activate_registered_model(
        database, record, principal, comment, rollback=False
    )
    return JSONResponse(content=result)


@router.post("/ml/models/{model_version}/rollback")
async def rollback_model(
    model_version: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    if payload.get("rollback_confirmation") is not True:
        raise APIError(400, "Explicit rollback confirmation is required")
    comment = str(payload.get("rollback_comment") or "").strip()
    if len(comment) < 12 or len(comment) > 1000:
        raise APIError(400, "Provide a rollback comment between 12 and 1000 characters")
    record = await _get_model_record(database, model_version)
    result = await _activate_registered_model(
        database, record, principal, comment, rollback=True
    )
    return JSONResponse(content=result)


async def predict_from_verified_features(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    model_type = payload.get("model_type", "visa")
    features = payload.get("features")
    if model_type not in {"visa", "financial"} or not isinstance(features, dict):
        return JSONResponse(
            status_code=400,
            content={"message": "model_type and a features object are required."},
        )
    try:
        active = await database["ml_active_models"].find_one({"_id": model_type})
        if not active:
            return JSONResponse(
                status_code=503,
                content={
                    "prediction_available": False,
                    "model_status": "insufficient_data",
                    "message": "No Admin-reviewed active model is available; no prediction was generated.",
                },
            )
        record = await database["ml_model_versions"].find_one(
            id_query(str(active["model_version"]))
        )
    except PyMongoError as error:
        logger.exception("Could not load active model")
        raise APIError(503, "Model registry is unavailable") from error
    if not record or _model_lifecycle_status(record) != "ACTIVE":
        raise APIError(503, "Active model registry entry is invalid")
    try:
        artifact, _ = await asyncio.to_thread(
            _load_verified_artifact,
            record,
            str(record["model_version"]),
            False,
        )
        result = await asyncio.to_thread(
            predict_with_artifact, artifact, model_type, features
        )
    except APIError:
        raise
    except ValueError as error:
        return JSONResponse(status_code=422, content={"message": str(error)})
    except Exception as error:
        logger.exception("Model inference failed")
        raise APIError(503, "Model prediction is unavailable") from error

    prediction_id = str(uuid4())
    prediction_record = {
        "_id": prediction_id,
        "prediction_id": prediction_id,
        "model_type": model_type,
        "model_version": record["model_version"],
        "result": result,
        "human_review_required": True,
        "input_fingerprint":payload.get("input_fingerprint"),
        "feature_schema_version":payload.get("feature_schema_version"),
        "policy_version_id":payload.get("policy_version_id"),
        "created_by": principal["id"],
        "created_at": datetime.now(timezone.utc),
    }
    if isinstance(payload.get("application_id"), str):
        prediction_record["application_id"] = payload["application_id"][:100]
    try:
        await database["predictions"].insert_one(prediction_record)
    except PyMongoError as error:
        logger.exception("Could not persist model prediction")
        raise APIError(503, "Prediction could not be saved") from error
    await _audit(
        database,
        principal["id"],
        "model_prediction_created",
        {"prediction_id": prediction_id, "model_version": record["model_version"]},
    )
    return JSONResponse(
        content={
            "prediction_id": prediction_id,
            "model_version": record["model_version"],
            "result": result,
            "human_review_required": True,
        }
    )


@router.post("/ml/predict")
async def predict(
    payload: dict[str, Any],
    request: Request,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    """Visa probabilities can only use a server-built, authorized case record."""
    if payload.get("model_type", "visa") == "visa":
        if "features" in payload:
            raise APIError(400, "Visa probabilities require an application_id; browser-supplied features are not accepted.")
        application_id=str(payload.get("application_id") or payload.get("applicationId") or "")
        if not application_id:
            raise APIError(400, "An application_id is required for visa prediction.")
        from .verification_reports import predict_application
        return await predict_application(application_id, request, database, principal)
    return await predict_from_verified_features(payload, database, principal)


@router.get("/predictions/{prediction_id}")
async def get_prediction(
    prediction_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN", "STAFF")),
) -> JSONResponse:
    try:
        record = await database["predictions"].find_one(id_query(prediction_id))
    except PyMongoError as error:
        logger.exception("Could not load prediction")
        raise APIError(503, "Prediction is unavailable") from error
    if not record:
        raise APIError(404, "Prediction not found")
    if principal["role"] != "ADMIN" and record.get("created_by") != principal["id"]:
        raise APIError(404, "Prediction not found")
    return JSONResponse(
        content={
            "prediction_id": record.get("prediction_id"),
            "model_type": record.get("model_type"),
            "model_version": record.get("model_version"),
            "result": record.get("result"),
            "human_review_required": True,
            "created_at": record.get("created_at").isoformat()
            if isinstance(record.get("created_at"), datetime)
            else record.get("created_at"),
        }
    )


def settings_file_limit() -> int:
    from ..core.config import settings

    return settings.max_file_size + 1
