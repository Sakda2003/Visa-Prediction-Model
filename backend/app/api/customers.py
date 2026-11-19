import logging
import math
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from io import BytesIO
from PIL import Image, ImageOps
from fastapi import APIRouter, Depends, Query, File, UploadFile
from fastapi.responses import JSONResponse, Response
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..core.errors import APIError
from ..database.serialization import serialize_mongo_document
from .dependencies import get_mongo_database
from .mongo_helpers import id_query

logger = logging.getLogger(__name__)
router = APIRouter()

_CUSTOMER_FIELDS = (
    "fullName",
    "email",
    "phone",
    "nationality",
    "address",
    "passportNumber",
    "dateOfBirth",
    "employmentStatus",
    "monthlyIncomeUsd",
    "monthlyExpensesUsd",
    "travelHistoryCount",
    "previousVisaDenials",
    "hasOverstayHistory",
    "accountHistoryMonths",
    "proofOfTiesToCambodia",
)
_CUSTOMER_COUNT_FIELDS = {
    "travelHistoryCount",
    "previousVisaDenials",
    "accountHistoryMonths",
}
_CUSTOMER_BOOLEAN_FIELDS = {"hasOverstayHistory", "proofOfTiesToCambodia"}


def _parse_boolean_field(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _validate_profile_fields(payload: dict[str, Any]) -> str | None:
    for field in ("monthlyIncomeUsd", "monthlyExpensesUsd", *_CUSTOMER_COUNT_FIELDS):
        if field not in payload or payload[field] in (None, ""):
            continue
        if isinstance(payload[field], bool):
            return f"{field} must be a non-negative number"
        try:
            value = float(payload[field])
        except (TypeError, ValueError):
            return f"{field} must be a non-negative number"
        if not math.isfinite(value) or value < 0:
            return f"{field} must be a non-negative number"
        if field in _CUSTOMER_COUNT_FIELDS and not value.is_integer():
            return f"{field} must be a non-negative integer"
    for field in _CUSTOMER_BOOLEAN_FIELDS:
        value = payload.get(field)
        if value not in (None, "") and _parse_boolean_field(value) is None:
            return f"{field} must be true or false"
    if "employmentStatus" in payload and payload["employmentStatus"] is not None:
        if not isinstance(payload["employmentStatus"], str) or len(payload["employmentStatus"]) > 80:
            return "employmentStatus must be a text value of 80 characters or fewer"
    return None


def _customer_profile_fields(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in _CUSTOMER_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if value in (None, ""):
            result[field] = None
        elif field in _CUSTOMER_BOOLEAN_FIELDS:
            result[field] = _parse_boolean_field(value)
        elif field in _CUSTOMER_COUNT_FIELDS:
            result[field] = int(value)
        elif field in {"monthlyIncomeUsd", "monthlyExpensesUsd"}:
            result[field] = float(value)
        elif field == "employmentStatus":
            result[field] = value.strip()
        else:
            result[field] = str(value).strip()
    return result


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _has_duplicate(
    customers: list[dict[str, Any]], data: dict[str, Any], excluding_id: str | None = None
) -> bool:
    email = _normalized(data.get("email"))
    passport_number = _normalized(data.get("passportNumber"))
    return any(
        customer.get("_id") != excluding_id
        and (
            (email and _normalized(customer.get("email")) == email)
            or (
                passport_number
                and _normalized(customer.get("passportNumber")) == passport_number
            )
        )
        for customer in customers
    )


@router.get("/")
@router.get("")
async def list_customers(
    database: AsyncDatabase = Depends(get_mongo_database),
    search: str = Query(default=""),
    nationality: str | None = Query(default=None),
) -> JSONResponse:
    try:
        customers = await database["customers"].find({}).to_list(length=None)
    except PyMongoError as error:
        logger.exception("Could not load customers: %s", error.__class__.__name__)
        raise APIError(503, "Customers are unavailable") from error
    normalized_search = search.strip().casefold()
    customers = [
        customer
        for customer in customers
        if (not nationality or str(customer.get("nationality") or "").casefold() == nationality.casefold())
        and (
            not normalized_search
            or normalized_search
            in " ".join(
                str(customer.get(field) or "")
                for field in ("fullName", "email", "phone", "passportNumber", "nationality")
            ).casefold()
        )
    ]
    return JSONResponse(
        content={
            "customers": [
                serialize_mongo_document(customer) for customer in customers
            ]
        }
    )


@router.get("/{customer_id}")
async def get_customer(
    customer_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    try:
        customer = await database["customers"].find_one(id_query(customer_id))
    except PyMongoError as error:
        logger.exception("Could not load customer: %s", error.__class__.__name__)
        raise APIError(503, "Customer is unavailable") from error
    if not customer:
        return JSONResponse(status_code=404, content={"message": "Customer not found"})
    return JSONResponse(content={"customer": serialize_mongo_document(customer)})


@router.post("/", status_code=201)
@router.post("", status_code=201)
async def create_customer(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    validation_error = _validate_profile_fields(payload)
    if validation_error:
        return JSONResponse(status_code=400, content={"message": validation_error})
    data = {
        "fullName": str(payload.get("fullName") or "").strip(),
        "email": str(payload.get("email") or "").strip().lower() or None,
        "phone": str(payload.get("phone") or "").strip(),
        "nationality": str(payload.get("nationality") or "").strip(),
        "address": str(payload.get("address") or "").strip(),
        "passportNumber": str(payload.get("passportNumber") or "").strip() or None,
        "dateOfBirth": (
            str(payload["dateOfBirth"]) if payload.get("dateOfBirth") else None
        ),
    }
    data.update(_customer_profile_fields(payload))
    if not payload.get("allowIncomplete") and (
        not data["fullName"] or not (data["passportNumber"] or data["email"])
    ):
        return JSONResponse(
            status_code=400,
            content={
                "message": "Full name and either passport number or email are required"
            },
        )
    if data["email"] and not re.fullmatch(
        r"[^\s@]+@[^\s@]+\.[^\s@]+", data["email"]
    ):
        return JSONResponse(
            status_code=400, content={"message": "Customer email address is invalid"}
        )

    try:
        customers = database["customers"]
        existing_customers = await customers.find({}).to_list(length=None)
        if _has_duplicate(existing_customers, data):
            return JSONResponse(
                status_code=409,
                content={
                    "message": "A customer with this passport number or email already exists"
                },
            )
        now = datetime.now(timezone.utc)
        customer = {
            "_id": str(uuid4()),
            **{key: value for key, value in data.items() if value is not None},
            "createdAt": now,
        }
        await customers.insert_one(customer)
    except DuplicateKeyError:
        return JSONResponse(
            status_code=409,
            content={
                "message": "A customer with this passport number or email already exists"
            },
        )
    except PyMongoError as error:
        logger.exception("Could not create customer: %s", error.__class__.__name__)
        raise APIError(503, "Customer could not be saved to the database") from error

    return JSONResponse(
        status_code=201,
        content={
            "message": "Customer created",
            "customer": serialize_mongo_document(customer),
        },
    )


@router.put("/{customer_id}")
async def update_customer(
    customer_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    validation_error = _validate_profile_fields(payload)
    if validation_error:
        return JSONResponse(status_code=400, content={"message": validation_error})
    try:
        customers = database["customers"]
        customer = await customers.find_one({"_id": customer_id})
        if not customer:
            return JSONResponse(
                status_code=404, content={"message": "Customer not found"}
            )

        updated = dict(customer)
        for field in _CUSTOMER_FIELDS:
            if field in payload:
                updated[field] = _customer_profile_fields({field: payload[field]})[field]
        if isinstance(updated.get("email"), str):
            updated["email"] = updated["email"].lower()
        for field in ("email", "passportNumber"):
            if isinstance(updated.get(field), str):
                updated[field] = updated[field].strip() or None
        if updated.get("email") and not re.fullmatch(
            r"[^\s@]+@[^\s@]+\.[^\s@]+", updated["email"]
        ):
            return JSONResponse(
                status_code=400,
                content={"message": "Customer email address is invalid"},
            )

        existing_customers = await customers.find({}).to_list(length=None)
        if _has_duplicate(existing_customers, updated, customer_id):
            return JSONResponse(
                status_code=409,
                content={
                    "message": "A customer with this passport number or email already exists"
                },
            )

        updated["updatedAt"] = datetime.now(timezone.utc)
        replacement = {
            key: value for key, value in updated.items() if value is not None
        }
        await customers.replace_one({"_id": customer_id}, replacement)
    except DuplicateKeyError:
        return JSONResponse(
            status_code=409,
            content={
                "message": "A customer with this passport number or email already exists"
            },
        )
    except PyMongoError as error:
        logger.exception("Could not update customer: %s", error.__class__.__name__)
        raise APIError(503, "Customer changes could not be saved") from error

    return JSONResponse(
        content={
            "message": "Customer updated",
            "customer": serialize_mongo_document(updated),
        }
    )


@router.delete("/{customer_id}")
async def delete_customer(
    customer_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    try:
        customers = database["customers"]
        customer = await customers.find_one({"_id": customer_id})
        if not customer:
            return JSONResponse(
                status_code=404, content={"message": "Customer not found"}
            )
        if await database["visaapplications"].find_one({"customer": customer_id}) or await database["visaapplications"].find_one({"customer": str(customer_id)}):
            return JSONResponse(
                status_code=409,
                content={
                    "message": "Customer is retained because historical applications reference it"
                },
            )
        await customers.delete_one({"_id": customer_id})
    except PyMongoError as error:
        logger.exception("Could not delete customer: %s", error.__class__.__name__)
        raise APIError(503, "Customer could not be removed from the database") from error

    return JSONResponse(
        content={
            "message": "Customer deleted",
            "customer": serialize_mongo_document(customer),
        }
    )


@router.post("/{customer_id}/photo")
async def upload_applicant_photo(customer_id: str, file: UploadFile = File(...), database: AsyncDatabase = Depends(get_mongo_database)):
    customer = await database["customers"].find_one(id_query(customer_id))
    if not customer or customer.get("status") == "DELETED":
        raise APIError(404, "Applicant not found")
    content = await file.read(5 * 1024 * 1024 + 1)
    if not content or len(content) > 5 * 1024 * 1024:
        raise APIError(400, "Choose a JPG, PNG or WebP photo up to 5 MB")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 20_000_000:
                raise ValueError("Unsupported image")
            clean = ImageOps.exif_transpose(image).convert("RGB")
            clean.thumbnail((1600, 2000))
            output = BytesIO()
            clean.save(output, format="JPEG", quality=90)
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        raise APIError(400, "The photo could not be read. Choose a valid JPG, PNG or WebP image.") from error
    now = datetime.now(timezone.utc)
    await database["customerphotos"].replace_one({"_id": customer["_id"]}, {"_id": customer["_id"], "content": output.getvalue(), "updatedAt": now}, upsert=True)
    await database["customers"].update_one({"_id": customer["_id"]}, {"$set": {"hasPhoto": True, "updatedAt": now}})
    return {"hasPhoto": True}


@router.get("/{customer_id}/photo")
async def get_applicant_photo(customer_id: str, database: AsyncDatabase = Depends(get_mongo_database)):
    customer = await database["customers"].find_one(id_query(customer_id))
    if not customer or customer.get("status") == "DELETED":
        raise APIError(404, "Applicant not found")
    photo = await database["customerphotos"].find_one({"_id": customer["_id"]})
    if not photo:
        raise APIError(404, "No applicant photo uploaded")
    return Response(content=photo["content"], media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
