import logging
import re
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..core.errors import APIError
from ..database.serialization import serialize_mongo_document
from .dependencies import get_mongo_database, require_roles

logger = logging.getLogger(__name__)
router = APIRouter()


def _country_response(country: dict[str, Any]) -> dict[str, Any]:
    return {"country": serialize_mongo_document(country)}


@router.get("/")
@router.get("")
async def list_countries(
    database: AsyncDatabase = Depends(get_mongo_database),
    search: str = Query(default=""),
    active: str | None = Query(default=None),
) -> JSONResponse:
    try:
        countries = await database["countries"].find({}).to_list(length=None)
    except PyMongoError as error:
        logger.exception("Could not load countries: %s", error.__class__.__name__)
        raise APIError(503, "Countries are unavailable") from error

    normalized_search = search.strip().lower()
    result = [
        country
        for country in countries
        if (not active or str(country.get("active", True)).lower() == active)
        and (
            not normalized_search
            or normalized_search
            in " ".join(
                str(country.get(field) or "")
                for field in ("name", "isoCode", "nationality")
            ).lower()
        )
    ]
    return JSONResponse(
        content={
            "countries": [
                serialize_mongo_document(country) for country in result
            ]
        }
    )


@router.post("/", status_code=201)
@router.post("", status_code=201)
async def create_country(
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    name = payload.get("name")
    iso_code = str(payload.get("isoCode") or "").upper()
    if not isinstance(name, str) or not name.strip() or not re.fullmatch(
        r"[A-Z]{2}", iso_code
    ):
        return JSONResponse(
            status_code=400,
            content={
                "message": "Name and a valid two-letter ISO country code are required"
            },
        )

    now = datetime.now(timezone.utc)
    nationality = payload.get("nationality")
    country = {
        "_id": ObjectId(),
        "name": name.strip(),
        "isoCode": iso_code,
        "flag": str(payload.get("flag") or ""),
        "nationality": (
            nationality.strip().upper() if isinstance(nationality, str) else ""
        ),
        "active": True,
        "createdAt": now,
        "updatedAt": now,
    }

    try:
        countries = database["countries"]
        if await countries.find_one({"isoCode": iso_code}):
            return JSONResponse(
                status_code=409, content={"message": "Country code already exists"}
            )
        await countries.insert_one(country)
    except DuplicateKeyError:
        return JSONResponse(
            status_code=409, content={"message": "Country code already exists"}
        )
    except PyMongoError as error:
        logger.exception("Could not create country: %s", error.__class__.__name__)
        raise APIError(503, "Country could not be saved to the database") from error

    return JSONResponse(status_code=201, content=_country_response(country))


@router.put("/{country_id}")
async def update_country(
    country_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    try:
        object_id = ObjectId(country_id)
    except InvalidId:
        return JSONResponse(status_code=404, content={"message": "Country not found"})

    try:
        countries = database["countries"]
        country = await countries.find_one({"_id": object_id})
        if not country:
            return JSONResponse(
                status_code=404, content={"message": "Country not found"}
            )

        name = payload.get("name", country.get("name"))
        if "name" in payload and not str(name).strip():
            return JSONResponse(
                status_code=400,
                content={"message": "Country name cannot be empty"},
            )
        iso_code = str(payload.get("isoCode", country["isoCode"])).upper()
        if "isoCode" in payload and not re.fullmatch(r"[A-Z]{2}", iso_code):
            return JSONResponse(
                status_code=400,
                content={"message": "ISO code must contain two letters"},
            )
        if await countries.find_one(
            {"isoCode": iso_code, "_id": {"$ne": object_id}}
        ):
            return JSONResponse(
                status_code=409, content={"message": "Country code already exists"}
            )
        active = payload.get("active", country.get("active", True))
        if active is not None and not isinstance(active, bool):
            return JSONResponse(
                status_code=400, content={"message": "Country active status is invalid"}
            )

        updated = {
            **country,
            "name": str(name).strip(),
            "isoCode": iso_code,
            "flag": payload.get("flag", country.get("flag", "")),
            "nationality": payload.get(
                "nationality", country.get("nationality", "")
            ),
            "active": active if active is not None else country.get("active", True),
            "updatedAt": datetime.now(timezone.utc),
        }
        updated["flag"] = str(updated["flag"] or "")
        if updated["nationality"] is not None:
            updated["nationality"] = str(updated["nationality"])
        if isinstance(updated["nationality"], str):
            updated["nationality"] = updated["nationality"].strip().upper()
        result = await countries.replace_one({"_id": object_id}, updated)
    except DuplicateKeyError:
        return JSONResponse(
            status_code=409, content={"message": "Country code already exists"}
        )
    except PyMongoError as error:
        logger.exception("Could not update country: %s", error.__class__.__name__)
        raise APIError(503, "Country changes could not be saved to the database") from error

    if result.matched_count == 0:
        return JSONResponse(status_code=404, content={"message": "Country not found"})
    return JSONResponse(content=_country_response(updated))


@router.delete("/{country_id}")
async def delete_country(
    country_id: str,
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    try:
        object_id = ObjectId(country_id)
    except InvalidId:
        return JSONResponse(status_code=404, content={"message": "Country not found"})

    try:
        countries = database["countries"]
        country = await countries.find_one({"_id": object_id})
        if not country:
            return JSONResponse(
                status_code=404, content={"message": "Country not found"}
            )
        country_id_value = str(object_id)
        in_use = bool(
            await database["visatypes"].find_one({"countryId": object_id})
            or await database["visatypes"].find_one({"countryId": country_id_value})
            or await database["visaapplications"].find_one({"countryId": object_id})
            or await database["visaapplications"].find_one(
                {"countryId": country_id_value}
            )
        )
        if in_use:
            updated = {
                **country,
                "active": False,
                "updatedAt": datetime.now(timezone.utc),
            }
            await countries.replace_one({"_id": object_id}, updated)
            return JSONResponse(
                content={
                    "message": "Country is in use and has been deactivated",
                    **_country_response(updated),
                }
            )
        result = await countries.delete_one({"_id": object_id})
    except PyMongoError as error:
        logger.exception("Could not delete country: %s", error.__class__.__name__)
        raise APIError(503, "Country could not be removed from the database") from error

    if result.deleted_count == 0:
        return JSONResponse(status_code=404, content={"message": "Country not found"})
    return JSONResponse(content={"message": "Country deleted"})
