from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..core.errors import APIError
from ..services.document_metrics import summarize_documents
from ..services.evidence_scoring import SCORING_FIELDS, normalize_scoring_fields
from .dependencies import get_mongo_database, require_roles
from .mongo_helpers import id_query, public_document, reference_query

logger = logging.getLogger(__name__)
router = APIRouter()

REQUIREMENT_FIELDS = (
    "name",
    "code",
    "description",
    "required",
    "conditional",
    "weight",
    "displayOrder",
    "maxFileSize",
    "source",
    "sourceStatus",
    "effectiveFrom",
    "acceptedFormats",
    "photoRules",
    "allowMultiple",
    "status",
    *SCORING_FIELDS,
)


def _validate(payload: dict[str, Any], creating: bool = False) -> str | None:
    if creating and (not str(payload.get("name") or "").strip() or not payload.get("visaTypeId")):
        return "Requirement name and visa type are required"
    for field in ("required", "conditional", "allowMultiple", "requiredForFinalAssessment"):
        if field in payload and not isinstance(payload[field], bool):
            return f"{field} must be true or false"
    if "weight" in payload:
        if isinstance(payload["weight"], bool):
            return "Points must be between 0 and 100."
        try:
            weight = float(payload["weight"])
        except (TypeError, ValueError):
            return "Points must be between 0 and 100."
        if not math.isfinite(weight) or not 0 <= weight <= 100:
            return "Points must be between 0 and 100."
    for field in ("displayOrder", "maxFileSize"):
        if field in payload and payload[field] is not None:
            try:
                number = float(payload[field])
            except (TypeError, ValueError):
                return f"{field} must be numeric"
            if not math.isfinite(number):
                return f"{field} must be finite"
            if number < 0:
                return f"{field} cannot be negative"
            if not number.is_integer():
                return f"{field} must be an integer"
    if "acceptedFormats" in payload and (
        not isinstance(payload["acceptedFormats"], list)
        or any(not isinstance(item, str) for item in payload["acceptedFormats"])
    ):
        return "acceptedFormats must be a list of file extensions"
    if "photoRules" in payload:
        rules = payload["photoRules"]
        allowed_rules = {
            "minWidth",
            "minHeight",
            "minSharpness",
            "minBrightness",
            "maxBrightness",
            "faceCenterTolerance",
            "minFaceHeightRatio",
            "manualReviewRequired",
        }
        if not isinstance(rules, dict) or set(rules) - allowed_rules:
            return "photoRules must contain supported applicant photo settings"
        if "manualReviewRequired" in rules and not isinstance(
            rules["manualReviewRequired"], bool
        ):
            return "photoRules.manualReviewRequired must be true or false"
        bounds = {
            "minWidth": (1, 10000),
            "minHeight": (1, 10000),
            "minSharpness": (0, 10000),
            "minBrightness": (0, 255),
            "maxBrightness": (0, 255),
            "faceCenterTolerance": (0, 0.5),
            "minFaceHeightRatio": (0, 1),
        }
        for field, (minimum, maximum) in bounds.items():
            if field not in rules:
                continue
            value = rules[field]
            if isinstance(value, bool):
                return f"photoRules.{field} is out of range"
            try:
                number = float(value)
            except (TypeError, ValueError):
                return f"photoRules.{field} must be numeric"
            if not math.isfinite(number) or not minimum <= number <= maximum:
                return f"photoRules.{field} is out of range"
        min_brightness = float(rules.get("minBrightness", 40))
        max_brightness = float(rules.get("maxBrightness", 220))
        if min_brightness > max_brightness:
            return "photoRules.minBrightness cannot exceed photoRules.maxBrightness"
    code = str(payload.get("code") or "").casefold()
    name = str(payload.get("name") or "").casefold()
    if "photo" in code or "photo" in name or "photograph" in name:
        allowed_photo_formats = {"jpg", "jpeg", "png"}
        if "acceptedFormats" in payload and (
            not payload["acceptedFormats"]
            or {item.lower().lstrip(".") for item in payload["acceptedFormats"]}
            - allowed_photo_formats
        ):
            return "Applicant photos support JPG, JPEG, and PNG only"
    if any(field in payload for field in SCORING_FIELDS):
        try:
            # A partial update's range is validated against the stored record below.
            if creating or "maximumEarnedPoints" in payload or "weight" in payload:
                normalize_scoring_fields(payload)
        except ValueError as error:
            return str(error)
    return None


def _public_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
    serialized = public_document(requirement)
    rules = serialized.get("photoRules")
    if isinstance(rules, dict):
        serialized["photoRules"] = {
            key: value for key, value in rules.items() if key != "exactlyOneFace"
        }
    return serialized


async def _write_history(
    database: AsyncDatabase,
    requirement: dict[str, Any],
    action: str,
    admin_id: str,
    previous: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> None:
    now = datetime.now(timezone.utc)
    await database["requirementversions"].insert_one(
        {
            "_id": ObjectId(),
            "requirementId": requirement["_id"],
            "visaTypeId": requirement.get("visaTypeId"),
            "action": action,
            "adminId": admin_id,
            "previous": previous,
            "current": current,
            "createdAt": now,
            "updatedAt": now,
        }
    )


async def _refresh_application_scores(database: AsyncDatabase, visa_type_id: Any) -> None:
    """Refresh current scores after a policy mutation without touching documents."""
    try:
        active = await database["visarequirements"].find(
            {"visaTypeId": visa_type_id, "status": "ACTIVE"}
        ).to_list(length=None)
        applications = await database["visaapplications"].find(
            {"visaTypeId": visa_type_id}
        ).to_list(length=None)
        for application in applications:
            # Published snapshots are immutable. Editing live requirement drafts must not rewrite old evidence awards.
            if application.get("evidencePolicySnapshot"):
                continue
            documents = await database["documents"].find(
                {"applicationId": str(application.get("_id"))}
            ).to_list(length=None)
            summary = summarize_documents(
                [{**item, "requirementId": str(item["_id"])} for item in active],
                documents,
            )
            now = datetime.now(timezone.utc)
            snapshot = {
                "maximumPoints": summary["maximumPoints"],
                "earnedPoints": summary["earnedPoints"],
                "documentAssessmentScore": summary["documentAssessmentScore"],
                "requirementScores": summary["requirementScores"],
                "calculatedAt": now,
            }
            await database["visaapplications"].update_one(
                {"_id": application["_id"]},
                {"$set": {
                    "completenessScore": summary["documentAssessmentScore"],
                    "documentReadiness": summary["documentAssessmentScore"],
                    "earnedPoints": summary["earnedPoints"],
                    "maximumPoints": summary["maximumPoints"],
                    "verificationPercentage": summary["verificationPercentage"],
                    "submissionCompleteness": summary["submissionCompleteness"],
                    "verificationMetricsCurrent": True,
                    "lastCalculationAt": now,
                    "scoringVersion": "document-requirements-v2",
                    "policyVersion": max((item.get("updatedAt") or item.get("createdAt") for item in active), default=None),
                    "updatedAt": now,
                }, "$push": {"scoreHistory": snapshot}},
            )
    except PyMongoError:
        logger.exception("Could not refresh application document scores after policy change")


@router.get("/")
@router.get("")
async def list_requirements(
    database: AsyncDatabase = Depends(get_mongo_database),
    visa_type_id: str | None = Query(default=None, alias="visaTypeId"),
    country_id: str | None = Query(default=None, alias="countryId"),
    include_archived: bool = Query(default=False, alias="includeArchived"),
) -> JSONResponse:
    try:
        requirements = await database["visarequirements"].find({}).to_list(length=None)
        visa_types = {
            str(item["_id"]): item
            for item in await database["visatypes"].find({}).to_list(length=None)
        }
    except PyMongoError as error:
        logger.exception("Could not load requirements: %s", error.__class__.__name__)
        raise APIError(503, "Requirements are unavailable") from error

    result: list[dict[str, Any]] = []
    for item in requirements:
        if not include_archived and str(item.get("status", "ACTIVE")).upper() != "ACTIVE":
            continue
        if visa_type_id and str(item.get("visaTypeId")) != visa_type_id:
            continue
        if country_id:
            visa_type = visa_types.get(str(item.get("visaTypeId")), {})
            if str(item.get("countryId") or visa_type.get("countryId")) != country_id:
                continue
        serialized = _public_requirement(item)
        serialized["visaTypeId"] = str(item.get("visaTypeId") or "")
        serialized["visaType"] = serialized["visaTypeId"]
        visa_type = visa_types.get(str(item.get("visaTypeId")), {})
        serialized["visaTypeCode"] = visa_type.get("visaTypeCode")
        serialized["active"] = str(item.get("status", "ACTIVE")).upper() == "ACTIVE"
        result.append(serialized)
    result.sort(key=lambda item: (item.get("displayOrder", 0), item.get("name", "")))
    return JSONResponse(content={"requirements": result, "count": len(result)})


@router.get("/visa-type/{visa_type_id}")
async def list_requirements_for_visa_type(
    visa_type_id: str,
    include_archived: bool = Query(default=False, alias="includeArchived"),
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    return await list_requirements(
        database=database,
        visa_type_id=visa_type_id,
        country_id=None,
        include_archived=include_archived,
    )


@router.post("/", status_code=201)
@router.post("", status_code=201)
async def create_requirement(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    message = _validate(payload, creating=True)
    if message:
        return JSONResponse(status_code=400, content={"message": message})
    try:
        visa_type = await database["visatypes"].find_one(id_query(str(payload["visaTypeId"])))
        if not visa_type:
            return JSONResponse(status_code=404, content={"message": "Visa type not found"})
        name = str(payload["name"]).strip()
        duplicate = await database["visarequirements"].find_one(
            {
                **reference_query("visaTypeId", str(visa_type["_id"])),
                "name": {"$regex": f"^{name}$", "$options": "i"},
                "status": {"$ne": "ARCHIVED"},
            }
        )
        if duplicate:
            return JSONResponse(status_code=409, content={"message": "Requirement already exists for this visa type"})
        now = datetime.now(timezone.utc)
        requirement = {
            "_id": ObjectId(),
            "visaTypeId": visa_type["_id"],
            "countryId": visa_type.get("countryId"),
            "name": name,
            "code": str(payload.get("code") or name.lower().replace(" ", "_")),
            "description": payload.get("description"),
            "required": payload.get("required", True),
            "conditional": payload.get("conditional", False),
            "weight": float(payload.get("maximumEarnedPoints", payload.get("weight", 0))),
            "displayOrder": int(payload.get("displayOrder", 0)),
            "maxFileSize": int(payload.get("maxFileSize", 10 * 1024 * 1024)),
            "source": str(payload.get("source") or ""),
            "sourceStatus": str(payload.get("sourceStatus") or "UNVERIFIED"),
            "effectiveFrom": payload.get("effectiveFrom"),
            "acceptedFormats": payload.get("acceptedFormats", ["pdf", "jpg", "jpeg", "png"]),
            "allowMultiple": payload.get("allowMultiple", False),
            "status": str(payload.get("status") or "ACTIVE").upper(),
            "createdAt": now,
            "updatedAt": now,
        }
        if "photo" in str(requirement["code"]).casefold() or "photo" in name.casefold() or "photograph" in name.casefold():
            requirement["acceptedFormats"] = ["jpg", "jpeg", "png"]
            requirement["photoRules"] = payload.get(
                "photoRules",
                {
                    "minWidth": 600,
                    "minHeight": 600,
                    "minSharpness": 50,
                    "minBrightness": 40,
                    "maxBrightness": 220,
                    "faceCenterTolerance": 0.25,
                    "minFaceHeightRatio": 0.2,
                    "manualReviewRequired": False,
                },
            )
        if any(field in payload for field in SCORING_FIELDS):
            requirement.update({field: payload[field] for field in SCORING_FIELDS if field in payload})
            requirement = normalize_scoring_fields(requirement)
        await database["visarequirements"].insert_one(requirement)
        await _write_history(database, requirement, "CREATE", principal["id"], None, _public_requirement(requirement))
        await _refresh_application_scores(database, visa_type["_id"])
    except DuplicateKeyError:
        return JSONResponse(status_code=409, content={"message": "Requirement already exists"})
    except PyMongoError as error:
        logger.exception("Could not create requirement: %s", error.__class__.__name__)
        raise APIError(503, "Requirement could not be saved") from error
    return JSONResponse(status_code=201, content={"requirement": _public_requirement(requirement)})


@router.get("/{requirement_id}/history")
async def requirement_history(
    requirement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    try:
        requirement = await database["visarequirements"].find_one(id_query(requirement_id))
        if not requirement:
            return JSONResponse(status_code=404, content={"message": "Requirement not found"})
        history = await database["requirementversions"].find(
            reference_query("requirementId", str(requirement["_id"]))
        ).to_list(length=None)
    except PyMongoError as error:
        raise APIError(503, "Requirement history is unavailable") from error
    history.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
    serialized_history = []
    for item in history:
        serialized = public_document(item)
        for field in ("previous", "current"):
            if isinstance(item.get(field), dict):
                serialized[field] = _public_requirement(item[field])
        serialized_history.append(serialized)
    return JSONResponse(content={"history": serialized_history})


@router.get("/{requirement_id}")
async def get_requirement(
    requirement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    try:
        requirement = await database["visarequirements"].find_one(id_query(requirement_id))
    except PyMongoError as error:
        raise APIError(503, "Requirement is unavailable") from error
    if not requirement:
        return JSONResponse(status_code=404, content={"message": "Requirement not found"})
    return JSONResponse(content={"requirement": _public_requirement(requirement)})


@router.put("/{requirement_id}")
async def update_requirement(
    requirement_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    message = _validate(payload)
    if message:
        return JSONResponse(status_code=400, content={"message": message})
    try:
        collection = database["visarequirements"]
        current = await collection.find_one(id_query(requirement_id))
        if not current:
            return JSONResponse(status_code=404, content={"message": "Requirement not found"})
        updated = dict(current)
        for field in REQUIREMENT_FIELDS:
            if field in payload:
                updated[field] = payload[field]
        if any(field in payload or field in current for field in SCORING_FIELDS):
            # A legacy weight-only edit updates its canonical maximum alias too.
            if "weight" in payload and "maximumEarnedPoints" not in payload:
                updated["maximumEarnedPoints"] = payload["weight"]
            elif "maximumEarnedPoints" in payload and "weight" not in payload:
                updated["weight"] = payload["maximumEarnedPoints"]
            try:
                updated = normalize_scoring_fields(updated)
            except ValueError as error:
                return JSONResponse(status_code=400, content={"message": str(error)})
        updated["updatedAt"] = datetime.now(timezone.utc)
        await collection.replace_one({"_id": current["_id"]}, updated)
        await _write_history(
            database,
            updated,
            "UPDATE",
            principal["id"],
            _public_requirement(current),
            _public_requirement(updated),
        )
        await _refresh_application_scores(database, updated.get("visaTypeId"))
    except DuplicateKeyError:
        return JSONResponse(status_code=409, content={"message": "Requirement conflicts with an existing record"})
    except PyMongoError as error:
        logger.exception("Could not update requirement: %s", error.__class__.__name__)
        raise APIError(503, "Requirement changes could not be saved") from error
    return JSONResponse(content={"requirement": _public_requirement(updated)})


@router.delete("/{requirement_id}")
async def delete_requirement(
    requirement_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    try:
        collection = database["visarequirements"]
        current = await collection.find_one(id_query(requirement_id))
        if not current:
            return JSONResponse(status_code=404, content={"message": "Requirement not found"})
        referenced = await database["visaapplications"].find_one(
            {"requirementSnapshot.requirementId": str(current["_id"])}
        )
        if referenced:
            updated = {**current, "status": "ARCHIVED", "updatedAt": datetime.now(timezone.utc)}
            await collection.replace_one({"_id": current["_id"]}, updated)
            await _write_history(
                database,
                updated,
                "ARCHIVE",
                principal["id"],
                public_document(current),
                public_document(updated),
            )
            await _refresh_application_scores(database, updated.get("visaTypeId"))
            return JSONResponse(content={"message": "Requirement is used by applications and has been archived"})
        await collection.delete_one({"_id": current["_id"]})
        await _write_history(
            database,
            current,
            "DELETE",
            principal["id"],
            public_document(current),
            None,
        )
        await _refresh_application_scores(database, current.get("visaTypeId"))
    except PyMongoError as error:
        logger.exception("Could not delete requirement: %s", error.__class__.__name__)
        raise APIError(503, "Requirement could not be deleted") from error
    return JSONResponse(content={"message": "Requirement deleted"})
