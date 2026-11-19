import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ..core.config import parse_jwt_lifetime, settings
from ..core.errors import APIError
from .dependencies import get_current_principal, get_mongo_database
from .mongo_helpers import id_query, public_document

logger = logging.getLogger(__name__)
router = APIRouter()


class RegisterRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    password: str | None = None


class LoginRequest(BaseModel):
    email: str | None = None
    password: str | None = None


def _password_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:72]


def _sign_token(user: dict[str, Any]) -> str:
    issued_at = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "id": str(user["_id"]),
            "email": user["email"],
            "role": str(user.get("role", "CUSTOMER")).strip().upper(),
            "iat": issued_at,
            "exp": issued_at + parse_jwt_lifetime(settings.jwt_expires_in),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    safe_user = public_document(user)
    if isinstance(safe_user.get("role"), str):
        safe_user["role"] = safe_user["role"].strip().upper()
    return safe_user


async def _find_user_by_email(users: Any, email: str) -> dict[str, Any] | None:
    user = await users.find_one({"email": email})
    if user:
        return user
    return await users.find_one(
        {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}}
    )


def _user_id(email: str) -> str:
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
    return f"user-{digest}"


def _token_response(user: dict[str, Any]) -> dict[str, Any]:
    token = _sign_token(user)
    return {
        "token": token,
        "access_token": token,
        "user": _public_user(user),
    }


@router.post("/register")
async def register_user(
    payload: RegisterRequest,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    name = payload.name.strip() if isinstance(payload.name, str) else ""
    email = payload.email.strip().lower() if isinstance(payload.email, str) else ""
    password = payload.password
    if not name or not email or not password:
        return JSONResponse(
            status_code=400,
            content={"message": "Name, email, and password are required"},
        )
    if len(password) < 8:
        return JSONResponse(
            status_code=400,
            content={"message": "Password must be at least 8 characters"},
        )

    now = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    user = {
        "_id": _user_id(email),
        "name": name,
        "email": email,
        "password": bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt(10)).decode(
            "ascii"
        ),
        "role": "CUSTOMER",
        "status": "ACTIVE",
        "createdAt": now,
        "updatedAt": now,
    }

    try:
        users = database["users"]
        if await _find_user_by_email(users, email):
            return JSONResponse(
                status_code=409, content={"message": "User already exists"}
            )
        await users.insert_one(user)
    except DuplicateKeyError:
        return JSONResponse(
            status_code=409, content={"message": "User already exists"}
        )
    except PyMongoError as error:
        logger.exception("Could not register user: %s", error.__class__.__name__)
        raise APIError(503, "User storage is unavailable") from error

    auth_payload = _token_response(user)
    logger.info("Registration completed")
    return JSONResponse(
        status_code=201,
        content={
            "message": "User registered successfully",
            **auth_payload,
        },
    )


@router.post("/login")
async def login_user(
    payload: LoginRequest,
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    logger.info("Login request received")
    email = payload.email.strip().lower() if isinstance(payload.email, str) else ""
    password = payload.password
    if not email or not password:
        logger.warning("Login rejected: missing credentials")
        return JSONResponse(
            status_code=400,
            content={"message": "Email and password are required"},
        )

    try:
        users = database["users"]
        user = await _find_user_by_email(users, email)
    except PyMongoError as error:
        logger.exception("Could not load user for login: %s", error.__class__.__name__)
        raise APIError(503, "User storage is unavailable") from error

    if not user:
        logger.warning("Login rejected: user lookup failed")
        return JSONResponse(
            status_code=401, content={"message": "Invalid credentials"}
        )

    account_status = user.get("status")
    account_is_active = not (
        account_status is not None
        and str(account_status).strip().upper() != "ACTIVE"
    ) and user.get("isActive") is not False
    logger.info("Login account lookup succeeded; active=%s", account_is_active)
    if not account_is_active:
        logger.warning("Login rejected: account inactive")
        return JSONResponse(
            status_code=403, content={"message": "Account is not active"}
        )

    stored_hash = (
        user.get("password")
        or user.get("passwordHash")
        or user.get("password_hash")
    )
    if isinstance(stored_hash, str):
        try:
            password_matches = bcrypt.checkpw(
                _password_bytes(password), stored_hash.encode("ascii")
            )
        except (ValueError, TypeError):
            password_matches = False
    else:
        password_matches = False

    if not password_matches:
        logger.warning("Login rejected: password verification failed")
        return JSONResponse(
            status_code=401, content={"message": "Invalid credentials"}
        )
    logger.info("Login password verification succeeded")

    try:
        token = _sign_token(user)
    except (KeyError, TypeError, ValueError):
        logger.exception("JWT generation failed")
        return JSONResponse(
            status_code=500,
            content={"message": "Authentication token could not be generated"},
        )
    logger.info("Login JWT generation succeeded")

    response_payload = {
        "message": "Login successful",
        "token": token,
        "access_token": token,
        "user": _public_user(user),
    }
    return JSONResponse(status_code=200, content=response_payload)


@router.get("/me")
async def get_current_user(
    principal: dict[str, str] = Depends(get_current_principal),
    database: AsyncDatabase = Depends(get_mongo_database),
) -> JSONResponse:
    try:
        users = database["users"]
        user = await users.find_one(id_query(principal["id"]))
        if not user:
            user = await _find_user_by_email(users, principal["email"])
    except PyMongoError as error:
        logger.exception("Could not load current user: %s", error.__class__.__name__)
        raise APIError(503, "User storage is unavailable") from error

    if not user:
        return JSONResponse(status_code=404, content={"message": "User not found"})
    return JSONResponse(content={"user": _public_user(user)})
