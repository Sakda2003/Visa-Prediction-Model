from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ..core.errors import APIError
from .dependencies import get_mongo_database, require_roles
from .mongo_helpers import id_query, public_document

logger = logging.getLogger(__name__)
router = APIRouter()
VALID_ROLES = {"ADMIN", "STAFF", "CUSTOMER"}


@router.get("/")
@router.get("")
async def list_users(
    database: AsyncDatabase = Depends(get_mongo_database),
    _principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    try:
        users = await database["users"].find({}).to_list(length=None)
    except PyMongoError as error:
        logger.exception("Could not load users: %s", error.__class__.__name__)
        raise APIError(503, "Users are unavailable") from error
    return JSONResponse(content={"users": [public_document(user) for user in users]})


@router.put("/{user_id}")
async def update_user(
    user_id: str,
    payload: dict[str, Any],
    database: AsyncDatabase = Depends(get_mongo_database),
    principal: dict[str, str] = Depends(require_roles("ADMIN")),
) -> JSONResponse:
    changes: dict[str, Any] = {}
    if "role" in payload:
        role = str(payload["role"]).upper()
        if role not in VALID_ROLES:
            return JSONResponse(status_code=400, content={"message": "Invalid user role"})
        changes["role"] = role
    if "status" in payload:
        account_status = str(payload["status"]).upper()
        if account_status not in {"ACTIVE", "INACTIVE", "SUSPENDED"}:
            return JSONResponse(status_code=400, content={"message": "Invalid account status"})
        changes["status"] = account_status
    if not changes:
        return JSONResponse(status_code=400, content={"message": "No supported user fields were provided"})
    if user_id == principal["id"] and changes.get("role", principal["role"]) != principal["role"]:
        return JSONResponse(status_code=400, content={"message": "Administrators cannot change their own role"})
    changes["updatedAt"] = datetime.now(timezone.utc)
    try:
        collection = database["users"]
        user = await collection.find_one(id_query(user_id))
        if not user:
            return JSONResponse(status_code=404, content={"message": "User not found"})
        await collection.update_one({"_id": user["_id"]}, {"$set": changes})
        user.update(changes)
    except PyMongoError as error:
        logger.exception("Could not update user: %s", error.__class__.__name__)
        raise APIError(503, "User changes could not be saved") from error
    return JSONResponse(content={"user": public_document(user)})
